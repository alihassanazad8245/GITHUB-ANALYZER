"""The analysis engine.

Raw GitHub JSON goes in, :mod:`.models` report objects come out. This module
performs no I/O of its own and no rendering, which keeps it easy to test.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Callable, Iterable, Sequence

from .github_client import GitHubClient
from .models import (
    CodeStructure,
    CommitActivity,
    ContributorStat,
    HealthScore,
    IssueStats,
    LanguageStat,
    ProjectSignals,
    RepositoryReport,
    UserReport,
)
from .utils import (
    classify_path,
    days_since,
    format_datetime,
    is_generated,
    parse_iso_datetime,
    percentage,
    truncate,
)

logger = logging.getLogger(__name__)

ProgressHook = Callable[[str], None]

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_CI_MARKERS = (".github/workflows/", ".gitlab-ci.yml", ".travis.yml",
               "azure-pipelines.yml", "jenkinsfile", ".circleci/")

_PACKAGE_MANAGER_FILES = {
    "requirements.txt": "pip",
    "pyproject.toml": "pip / PEP 621",
    "setup.py": "setuptools",
    "pipfile": "pipenv",
    "package.json": "npm / yarn",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "cargo.toml": "cargo",
    "go.mod": "go modules",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "composer.json": "composer",
    "gemfile": "bundler",
}

_FRAMEWORK_MARKERS = {
    "manage.py": "Django",
    "next.config.js": "Next.js",
    "nuxt.config.js": "Nuxt",
    "angular.json": "Angular",
    "vite.config.js": "Vite",
    "vite.config.ts": "Vite",
    "svelte.config.js": "Svelte",
    "pubspec.yaml": "Flutter",
    "streamlit_app.py": "Streamlit",
}


def _noop(_message: str) -> None:
    """Default progress hook that discards messages."""


class Analyzer:
    """Coordinates API calls and turns the responses into report objects."""

    def __init__(self, client: GitHubClient, progress: ProgressHook | None = None) -> None:
        self.client = client
        self.progress = progress or _noop

    # ------------------------------------------------------------------ #
    # Repository analysis
    # ------------------------------------------------------------------ #

    def analyze_repository(self, owner: str, repo: str, deep: bool = True) -> RepositoryReport:
        """Analyse a single repository.

        ``deep=False`` skips the optional endpoints (commits, contributors,
        issues, file tree) and performs only two API calls.
        """
        self.progress("Fetching repository metadata")
        raw = self.client.get_repo(owner, repo)

        report = RepositoryReport(
            full_name=raw.get("full_name", f"{owner}/{repo}"),
            owner=(raw.get("owner") or {}).get("login", owner),
            name=raw.get("name", repo),
            description=truncate(raw.get("description"), 200),
            url=raw.get("html_url", ""),
            homepage=raw.get("homepage") or "",
            private=bool(raw.get("private")),
            fork=bool(raw.get("fork")),
            archived=bool(raw.get("archived")),
            disabled=bool(raw.get("disabled")),
            created_at=format_datetime(raw.get("created_at")),
            updated_at=format_datetime(raw.get("updated_at")),
            pushed_at=format_datetime(raw.get("pushed_at")),
            days_since_push=days_since(raw.get("pushed_at")),
            default_branch=raw.get("default_branch") or "main",
            license_name=(raw.get("license") or {}).get("name") or "None",
            topics=list(raw.get("topics") or []),
            stars=raw.get("stargazers_count", 0),
            forks=raw.get("forks_count", 0),
            watchers=raw.get("subscribers_count", raw.get("watchers_count", 0)),
            open_issues_and_pulls=raw.get("open_issues_count", 0),
            size_kb=raw.get("size", 0),
        )

        if report.archived:
            report.warnings.append("This repository is archived (read-only).")
        if report.disabled:
            report.warnings.append("This repository has been disabled by GitHub.")
        if report.fork:
            report.warnings.append("This is a fork; statistics reflect the fork, not upstream.")

        self.progress("Analyzing languages")
        report.languages = self._language_stats(self.client.get_languages(owner, repo))

        if not deep:
            report.warnings.append("Quick mode: commit, contributor and file analysis skipped.")
            report.health = self._score_repository(report)
            report.api_requests = self.client.requests_made
            return report

        self.progress("Analyzing contributors")
        contributors = self.client.get_contributors(owner, repo)
        report.contributors, report.contributor_count, report.contributor_concentration = (
            self._contributor_stats(contributors)
        )

        self.progress("Analyzing commit history")
        report.commits = self._commit_activity(self.client.get_commits(owner, repo))

        self.progress("Analyzing issues and pull requests")
        report.issues = self._issue_stats(
            self.client.get_issues_and_pulls(owner, repo),
            self.client.get_pulls(owner, repo),
        )

        self.progress("Analyzing releases")
        releases = self.client.get_releases(owner, repo)
        report.release_count = len(releases)
        if releases:
            newest = releases[0]
            tag = newest.get("tag_name") or newest.get("name") or "untagged"
            report.latest_release = f"{tag} ({format_datetime(newest.get('published_at'))})"

        self.progress("Analyzing code structure")
        entries, truncated = self.client.get_tree(owner, repo, report.default_branch)
        report.structure = self._code_structure(entries, truncated)
        report.signals = self._project_signals(entries, report)
        if report.structure.tree_truncated:
            report.warnings.append(
                "The file tree was truncated by GitHub; structure numbers are a lower bound."
            )
        if report.commits.truncated:
            report.warnings.append(
                f"Commit analysis is based on the {report.commits.analyzed_commits} "
                "most recent commits."
            )

        report.health = self._score_repository(report)
        report.api_requests = self.client.requests_made
        return report

    # ------------------------------------------------------------------ #
    # User analysis
    # ------------------------------------------------------------------ #

    def analyze_user(self, username: str) -> UserReport:
        """Analyse a GitHub user or organisation and their public repositories."""
        self.progress("Fetching user profile")
        raw = self.client.get_user(username)

        report = UserReport(
            login=raw.get("login", username),
            name=raw.get("name") or "N/A",
            account_type=raw.get("type", "User"),
            bio=truncate(raw.get("bio"), 160),
            company=raw.get("company") or "",
            location=raw.get("location") or "",
            blog=raw.get("blog") or "",
            profile_url=raw.get("html_url", ""),
            created_at=format_datetime(raw.get("created_at")),
            account_age_days=days_since(raw.get("created_at")),
            followers=raw.get("followers", 0),
            following=raw.get("following", 0),
            public_repos=raw.get("public_repos", 0),
            public_gists=raw.get("public_gists", 0),
        )

        self.progress("Fetching repositories")
        repos = self.client.get_user_repos(username)
        report.analyzed_repos = len(repos)

        if report.public_repos > len(repos):
            report.warnings.append(
                f"Analysed the {len(repos)} most recently updated of "
                f"{report.public_repos} public repositories."
            )
        if not repos:
            report.warnings.append("This account has no public repositories to analyse.")
            return report

        self.progress("Aggregating repository statistics")
        language_counter: Counter[str] = Counter()
        year_counter: Counter[str] = Counter()
        newest_push: int | None = None

        for item in repos:
            report.total_stars += item.get("stargazers_count", 0)
            report.total_forks += item.get("forks_count", 0)
            report.total_watchers += item.get("watchers_count", 0)

            if item.get("fork"):
                report.forked_repos += 1
            else:
                report.original_repos += 1
            if item.get("archived"):
                report.archived_repos += 1
            if item.get("license"):
                report.repos_with_license += 1
            if item.get("description"):
                report.repos_with_description += 1

            language = item.get("language")
            if language:
                language_counter[language] += 1

            created = parse_iso_datetime(item.get("created_at"))
            if created:
                year_counter[str(created.year)] += 1

            pushed = days_since(item.get("pushed_at"))
            if pushed is not None and (newest_push is None or pushed < newest_push):
                newest_push = pushed

        report.days_since_last_push = newest_push
        total_langs = sum(language_counter.values())
        report.languages = [
            LanguageStat(name, count, round(percentage(count, total_langs), 1))
            for name, count in language_counter.most_common(12)
        ]
        report.top_language = report.languages[0].name if report.languages else "N/A"
        report.yearly_activity = sorted(year_counter.items())

        report.top_repos = [
            self._repo_summary(item)
            for item in sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:10]
        ]
        report.recent_repos = [self._repo_summary(item) for item in repos[:5]]
        report.api_requests = self.client.requests_made
        return report

    # ------------------------------------------------------------------ #
    # Component analyses (pure functions over raw payloads)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _repo_summary(item: dict[str, Any]) -> dict[str, Any]:
        """Compact per-repository row used in user reports."""
        return {
            "name": item.get("name", "?"),
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "language": item.get("language") or "N/A",
            "updated": format_datetime(item.get("pushed_at") or item.get("updated_at")),
            "description": truncate(item.get("description"), 60),
            "url": item.get("html_url", ""),
        }

    @staticmethod
    def _language_stats(languages: dict[str, int]) -> list[LanguageStat]:
        """Convert raw byte counts into sorted percentage shares."""
        total = sum(languages.values())
        if not total:
            return []
        ordered = sorted(languages.items(), key=lambda kv: kv[1], reverse=True)
        return [
            LanguageStat(name, size, round(percentage(size, total), 1))
            for name, size in ordered
        ]

    @staticmethod
    def _contributor_stats(
        contributors: Sequence[dict[str, Any]],
    ) -> tuple[list[ContributorStat], int, float]:
        """Build the contributor leaderboard and concentration metric."""
        if not contributors:
            return [], 0, 0.0

        total = sum(c.get("contributions", 0) for c in contributors) or 1
        stats = [
            ContributorStat(
                login=c.get("login") or "unknown",
                contributions=c.get("contributions", 0),
                percent=round(percentage(c.get("contributions", 0), total), 1),
                profile_url=c.get("html_url", ""),
            )
            for c in contributors[:15]
        ]
        concentration = round(percentage(contributors[0].get("contributions", 0), total), 1)
        return stats, len(contributors), concentration

    @staticmethod
    def _commit_activity(commits: Sequence[dict[str, Any]]) -> CommitActivity:
        """Summarise the sampled commit history."""
        activity = CommitActivity(analyzed_commits=len(commits))
        if not commits:
            return activity

        months: Counter[str] = Counter()
        weekdays: Counter[str] = Counter()
        authors: Counter[str] = Counter()
        dates = []

        for commit in commits:
            detail = commit.get("commit") or {}
            author_meta = (detail.get("author") or {})
            when = parse_iso_datetime(author_meta.get("date"))
            if when:
                dates.append(when)
                months[when.strftime("%Y-%m")] += 1
                weekdays[WEEKDAYS[when.weekday()]] += 1

            login = (commit.get("author") or {}).get("login")
            name = login or author_meta.get("name") or "unknown"
            authors[name] += 1

        activity.monthly = sorted(months.items())[-12:]
        activity.weekday = [(day, weekdays.get(day, 0)) for day in WEEKDAYS]
        activity.top_authors = authors.most_common(10)

        if dates:
            dates.sort()
            activity.first_commit = dates[0].strftime("%Y-%m-%d")
            activity.latest_commit = dates[-1].strftime("%Y-%m-%d")
            span_months = max(1.0, (dates[-1] - dates[0]).days / 30.44)
            activity.commits_per_month = round(len(dates) / span_months, 1)

        latest = (commits[0].get("commit") or {}).get("message", "")
        activity.latest_message = truncate(latest.split("\n")[0], 70)
        activity.truncated = len(commits) >= 300
        return activity

    @staticmethod
    def _issue_stats(
        issues: Sequence[dict[str, Any]], pulls: Sequence[dict[str, Any]]
    ) -> IssueStats:
        """Split the issues endpoint from pull requests and count both."""
        stats = IssueStats()
        labels: Counter[str] = Counter()
        pure_issues = [item for item in issues if "pull_request" not in item]

        for item in pure_issues:
            if item.get("state") == "open":
                stats.open_issues += 1
            else:
                stats.closed_issues += 1
            for label in item.get("labels") or []:
                name = label.get("name") if isinstance(label, dict) else str(label)
                if name:
                    labels[name] += 1

        stats.sampled_issues = len(pure_issues)
        stats.top_labels = labels.most_common(8)
        if pure_issues:
            stats.newest_issue = format_datetime(pure_issues[0].get("created_at"))
            stats.oldest_sampled_issue = format_datetime(pure_issues[-1].get("created_at"))

        for pull in pulls:
            if pull.get("state") == "open":
                stats.open_pulls += 1
            else:
                stats.closed_pulls += 1
                if pull.get("merged_at"):
                    stats.merged_pulls += 1

        stats.sampled_pulls = len(pulls)
        if stats.closed_pulls:
            stats.merge_rate = round(percentage(stats.merged_pulls, stats.closed_pulls), 1)
        return stats

    @staticmethod
    def _code_structure(entries: Sequence[dict[str, Any]], truncated: bool) -> CodeStructure:
        """Derive file-type statistics from a git tree listing."""
        structure = CodeStructure(tree_truncated=truncated)
        if not entries:
            return structure

        structure.available = True
        categories: Counter[str] = Counter()
        extensions: Counter[str] = Counter()
        top_level: set[str] = set()
        sizes: list[tuple[str, int]] = []

        for entry in entries:
            path = entry.get("path", "")
            if not path:
                continue
            if entry.get("type") == "tree":
                structure.directories += 1
                if "/" not in path:
                    top_level.add(path + "/")
                continue

            structure.total_files += 1
            categories[classify_path(path)] += 1
            if is_generated(path):
                structure.generated_files += 1
            if "/" not in path:
                top_level.add(path)

            name = path.rsplit("/", 1)[-1]
            if "." in name:
                extensions["." + name.rsplit(".", 1)[-1].lower()] += 1

            size = entry.get("size")
            if isinstance(size, int):
                sizes.append((path, size))

        structure.categories = dict(categories)
        structure.extensions = extensions.most_common(10)
        structure.top_level = sorted(top_level)[:20]
        structure.largest_files = sorted(sizes, key=lambda kv: kv[1], reverse=True)[:5]
        return structure

    @staticmethod
    def _project_signals(
        entries: Sequence[dict[str, Any]], report: RepositoryReport
    ) -> ProjectSignals:
        """Detect documentation, tests, CI, containers and package managers."""
        signals = ProjectSignals(has_license=report.license_name != "None")
        paths = [entry.get("path", "").lower() for entry in entries]
        managers: set[str] = set()
        frameworks: set[str] = set()

        for path in paths:
            name = path.rsplit("/", 1)[-1]

            if "/" not in path:
                if name.startswith("readme"):
                    signals.has_readme = True
                if name.startswith("license") or name.startswith("copying"):
                    signals.has_license = True
                if name.startswith("contributing"):
                    signals.has_contributing = True

            if name.startswith("dockerfile") or name == "docker-compose.yml":
                signals.has_dockerfile = True
            if any(marker in path for marker in _CI_MARKERS):
                signals.has_ci = True
            if classify_path(path) == "test":
                signals.has_tests = True
            if name in _PACKAGE_MANAGER_FILES:
                managers.add(_PACKAGE_MANAGER_FILES[name])
            if name in _FRAMEWORK_MARKERS:
                frameworks.add(_FRAMEWORK_MARKERS[name])

        signals.package_managers = sorted(managers)
        signals.frameworks = sorted(frameworks)
        return signals

    # ------------------------------------------------------------------ #
    # Analyzer Metric: repository health estimate (unofficial)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _score_repository(report: RepositoryReport) -> HealthScore:
        """Compute the unofficial repository health estimate.

        This is an *Analyzer Metric* produced by this tool from public signals.
        GitHub publishes no such score; treat it as a rough heuristic only.
        """
        breakdown: list[tuple[str, int, int]] = []
        notes: list[str] = []

        # Recent activity (25)
        days = report.days_since_push
        if days is None:
            activity = 5
        elif days <= 30:
            activity = 25
        elif days <= 90:
            activity = 20
        elif days <= 365:
            activity = 12
        else:
            activity = 4
            notes.append("No pushes in over a year.")
        if report.archived:
            activity = min(activity, 5)
        breakdown.append(("Recent activity", activity, 25))

        # Documentation (20)
        docs = 0
        docs += 8 if report.signals.has_readme else 0
        docs += 5 if report.description and report.description != "N/A" else 0
        docs += 4 if report.signals.has_contributing else 0
        docs += 3 if report.topics else 0
        if not report.signals.has_readme:
            notes.append("No README detected at the repository root.")
        breakdown.append(("Documentation", docs, 20))

        # Engineering practice (20)
        practice = 0
        practice += 8 if report.signals.has_tests else 0
        practice += 7 if report.signals.has_ci else 0
        practice += 5 if report.signals.has_license else 0
        if not report.signals.has_tests:
            notes.append("No test files detected.")
        if not report.signals.has_ci:
            notes.append("No CI configuration detected.")
        breakdown.append(("Engineering practice", practice, 20))

        # Community (20)
        community = 0
        count = report.contributor_count
        if count >= 20:
            community += 10
        elif count >= 5:
            community += 7
        elif count >= 2:
            community += 4
        elif count == 1:
            community += 2
        if report.contributor_concentration and report.contributor_concentration > 90 and count > 1:
            notes.append("Contributions are highly concentrated in one contributor.")
        elif count > 1:
            community += 3
        community += 4 if report.stars >= 50 else (2 if report.stars >= 5 else 0)
        community += 3 if report.forks >= 10 else (1 if report.forks >= 1 else 0)
        breakdown.append(("Community", min(community, 20), 20))

        # Maintenance responsiveness (15)
        maintenance = 0
        issues = report.issues
        if issues.sampled_issues:
            closed_ratio = percentage(issues.closed_issues, issues.sampled_issues)
            maintenance += 8 if closed_ratio >= 60 else (5 if closed_ratio >= 30 else 2)
        if issues.merge_rate >= 60:
            maintenance += 5
        elif issues.merge_rate >= 30:
            maintenance += 3
        if report.release_count:
            maintenance += 2
        breakdown.append(("Maintenance", min(maintenance, 15), 15))

        total = sum(score for _, score, _ in breakdown)
        if total >= 85:
            grade = "A - Excellent"
        elif total >= 70:
            grade = "B - Healthy"
        elif total >= 55:
            grade = "C - Moderate"
        elif total >= 35:
            grade = "D - Needs attention"
        else:
            grade = "E - At risk"

        return HealthScore(total=total, grade=grade, breakdown=breakdown, notes=notes[:5])


def compare_users(reports: Iterable[UserReport]) -> list[dict[str, Any]]:
    """Build comparison rows for two or more analysed users.

    Each row is ``{"metric": ..., "values": [...], "winner": login|"Tie"}``.
    """
    users = list(reports)
    if len(users) < 2:
        return []

    metrics: list[tuple[str, Callable[[UserReport], int]]] = [
        ("Followers", lambda u: u.followers),
        ("Public repositories", lambda u: u.public_repos),
        ("Total stars", lambda u: u.total_stars),
        ("Total forks", lambda u: u.total_forks),
        ("Original repositories", lambda u: u.original_repos),
        ("Repositories with a license", lambda u: u.repos_with_license),
    ]

    rows: list[dict[str, Any]] = []
    for label, getter in metrics:
        values = [getter(user) for user in users]
        best = max(values)
        winner = "Tie" if values.count(best) > 1 else users[values.index(best)].login
        rows.append({"metric": label, "values": values, "winner": winner})

    langs = [user.top_language for user in users]
    rows.append({"metric": "Top language", "values": langs, "winner": "-"})
    return rows
