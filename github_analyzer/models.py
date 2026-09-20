"""Typed result containers produced by the analysis engine.

Every model exposes :meth:`to_dict` so reports can be serialised without the
report layer knowing anything about the analysis internals.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import __version__


def _now() -> str:
    """Current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class LanguageStat:
    """One language and its share of the codebase."""

    name: str
    bytes: int
    percent: float


@dataclass(slots=True)
class ContributorStat:
    """One contributor and their share of recorded commits."""

    login: str
    contributions: int
    percent: float
    profile_url: str = ""


@dataclass(slots=True)
class CommitActivity:
    """Commit-history summary derived from the most recent commits."""

    analyzed_commits: int = 0
    monthly: list[tuple[str, int]] = field(default_factory=list)
    weekday: list[tuple[str, int]] = field(default_factory=list)
    top_authors: list[tuple[str, int]] = field(default_factory=list)
    first_commit: str = "N/A"
    latest_commit: str = "N/A"
    latest_message: str = "N/A"
    commits_per_month: float = 0.0
    truncated: bool = False


@dataclass(slots=True)
class CodeStructure:
    """File-tree statistics gathered from a single git-tree API call."""

    total_files: int = 0
    directories: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    extensions: list[tuple[str, int]] = field(default_factory=list)
    top_level: list[str] = field(default_factory=list)
    largest_files: list[tuple[str, int]] = field(default_factory=list)
    generated_files: int = 0
    tree_truncated: bool = False
    available: bool = False


@dataclass(slots=True)
class ProjectSignals:
    """Detected project conventions (tests, CI, packaging, containers)."""

    has_readme: bool = False
    has_license: bool = False
    has_contributing: bool = False
    has_tests: bool = False
    has_ci: bool = False
    has_dockerfile: bool = False
    package_managers: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IssueStats:
    """Issue and pull-request counters over the sampled window."""

    open_issues: int = 0
    closed_issues: int = 0
    sampled_issues: int = 0
    top_labels: list[tuple[str, int]] = field(default_factory=list)
    open_pulls: int = 0
    closed_pulls: int = 0
    merged_pulls: int = 0
    sampled_pulls: int = 0
    merge_rate: float = 0.0
    newest_issue: str = "N/A"
    oldest_sampled_issue: str = "N/A"


@dataclass(slots=True)
class HealthScore:
    """Custom, unofficial health estimate.

    These are *Analyzer Metrics* computed by this tool. They are not provided
    or endorsed by GitHub.
    """

    total: int = 0
    grade: str = "N/A"
    breakdown: list[tuple[str, int, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RepositoryReport:
    """Complete analysis of a single repository."""

    full_name: str
    owner: str
    name: str
    description: str = ""
    url: str = ""
    homepage: str = ""
    private: bool = False
    fork: bool = False
    archived: bool = False
    disabled: bool = False
    created_at: str = "N/A"
    updated_at: str = "N/A"
    pushed_at: str = "N/A"
    days_since_push: int | None = None
    default_branch: str = "N/A"
    license_name: str = "None"
    topics: list[str] = field(default_factory=list)
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues_and_pulls: int = 0
    size_kb: int = 0
    languages: list[LanguageStat] = field(default_factory=list)
    contributors: list[ContributorStat] = field(default_factory=list)
    contributor_count: int = 0
    contributor_concentration: float = 0.0
    commits: CommitActivity = field(default_factory=CommitActivity)
    structure: CodeStructure = field(default_factory=CodeStructure)
    signals: ProjectSignals = field(default_factory=ProjectSignals)
    issues: IssueStats = field(default_factory=IssueStats)
    latest_release: str = "None"
    release_count: int = 0
    health: HealthScore = field(default_factory=HealthScore)
    warnings: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=_now)
    analyzer_version: str = __version__
    api_requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise the report to plain JSON-compatible types."""
        data = asdict(self)
        data["report_type"] = "repository"
        return data


@dataclass(slots=True)
class UserReport:
    """Complete analysis of a GitHub user or organisation."""

    login: str
    name: str = "N/A"
    account_type: str = "User"
    bio: str = ""
    company: str = ""
    location: str = ""
    blog: str = ""
    profile_url: str = ""
    created_at: str = "N/A"
    account_age_days: int | None = None
    followers: int = 0
    following: int = 0
    public_repos: int = 0
    public_gists: int = 0
    analyzed_repos: int = 0
    total_stars: int = 0
    total_forks: int = 0
    total_watchers: int = 0
    original_repos: int = 0
    forked_repos: int = 0
    archived_repos: int = 0
    repos_with_license: int = 0
    repos_with_description: int = 0
    languages: list[LanguageStat] = field(default_factory=list)
    top_language: str = "N/A"
    top_repos: list[dict[str, Any]] = field(default_factory=list)
    recent_repos: list[dict[str, Any]] = field(default_factory=list)
    yearly_activity: list[tuple[str, int]] = field(default_factory=list)
    days_since_last_push: int | None = None
    warnings: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=_now)
    analyzer_version: str = __version__
    api_requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise the report to plain JSON-compatible types."""
        data = asdict(self)
        data["report_type"] = "user"
        return data
