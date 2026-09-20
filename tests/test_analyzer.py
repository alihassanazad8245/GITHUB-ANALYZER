"""Test suite for GitHub Analyzer.

Everything here is offline: the HTTP layer is exercised through a fake
``requests.Response`` so the tests never touch the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from github_analyzer.analyzer import Analyzer, compare_users
from github_analyzer.config import Settings, resolve_token
from github_analyzer.errors import (
    AuthError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
)
from github_analyzer.github_client import GitHubClient
from github_analyzer.models import RepositoryReport, UserReport
from github_analyzer.reports import SUPPORTED_FORMATS, safe_slug, write_report
from github_analyzer.utils import (
    classify_path,
    human_size,
    is_generated,
    parse_repo_reference,
    percentage,
    validate_username,
)

# --------------------------------------------------------------------------- #
# URL / username parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reference",
    [
        "python/cpython",
        "https://github.com/python/cpython",
        "http://www.github.com/python/cpython/",
        "https://github.com/python/cpython.git",
        "git@github.com:python/cpython.git",
        "github.com/python/cpython/tree/main/Lib",
        "  python/cpython  ",
    ],
)
def test_parse_repo_reference_accepts_common_forms(reference: str) -> None:
    assert parse_repo_reference(reference) == ("python", "cpython")


@pytest.mark.parametrize("reference", ["", "   ", "cpython", "https://github.com/", "a b/c d"])
def test_parse_repo_reference_rejects_bad_input(reference: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_repo_reference(reference)


def test_validate_username_strips_at_sign() -> None:
    assert validate_username("@alihassanazad8245") == "alihassanazad8245"


@pytest.mark.parametrize("name", ["", "   ", "has space", "-leading", "trailing-", "a" * 40])
def test_validate_username_rejects_invalid(name: str) -> None:
    with pytest.raises(InvalidInputError):
        validate_username(name)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def test_percentage_handles_zero_division() -> None:
    assert percentage(5, 0) == 0.0
    assert percentage(1, 4) == 25.0


def test_human_size_scales_units() -> None:
    assert human_size(0) == "0 KB"
    assert human_size(512).endswith("KB")
    assert human_size(2048).endswith("MB")


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/app.py", "code"),
        ("tests/test_app.py", "test"),
        ("pyproject.toml", "config"),
        ("README.md", "docs"),
        ("logo.png", "image"),
        ("LICENSE", "other"),
    ],
)
def test_classify_path(path: str, expected: str) -> None:
    assert classify_path(path) == expected


def test_is_generated_detects_vendored_paths() -> None:
    assert is_generated("node_modules/lodash/index.js")
    assert not is_generated("src/index.js")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_resolve_token_prefers_explicit_argument(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    assert resolve_token("explicit") == "explicit"


def test_resolve_token_falls_back_to_environment(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    assert resolve_token(None) == "from-env"


def test_settings_reports_authentication_state() -> None:
    assert Settings(token="x").authenticated is True
    assert Settings(token=None).authenticated is False


# --------------------------------------------------------------------------- #
# HTTP error mapping
# --------------------------------------------------------------------------- #


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status_code: int, payload=None, headers=None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def _client_returning(response: FakeResponse) -> GitHubClient:
    client = GitHubClient(token=None)
    client.session.get = lambda *a, **k: response  # type: ignore[assignment]
    return client


def test_404_maps_to_not_found_error() -> None:
    client = _client_returning(FakeResponse(404))
    with pytest.raises(NotFoundError):
        client.get_repo("nope", "nope")


def test_exhausted_rate_limit_maps_to_rate_limit_error() -> None:
    client = _client_returning(
        FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"})
    )
    with pytest.raises(RateLimitError):
        client.get_user("someone")


def test_401_maps_to_auth_error() -> None:
    client = _client_returning(FakeResponse(401))
    with pytest.raises(AuthError):
        client.get_user("someone")


def test_optional_endpoints_degrade_to_empty_list() -> None:
    client = _client_returning(FakeResponse(409))  # 409 = empty repository
    assert client.get_commits("owner", "repo") == []


# --------------------------------------------------------------------------- #
# Analysis logic
# --------------------------------------------------------------------------- #


def test_language_stats_sorted_and_normalised() -> None:
    stats = Analyzer._language_stats({"Python": 750, "HTML": 250})
    assert [s.name for s in stats] == ["Python", "HTML"]
    assert stats[0].percent == 75.0
    assert Analyzer._language_stats({}) == []


def test_contributor_stats_computes_concentration() -> None:
    raw = [
        {"login": "alice", "contributions": 80, "html_url": ""},
        {"login": "bob", "contributions": 20, "html_url": ""},
    ]
    stats, count, concentration = Analyzer._contributor_stats(raw)
    assert count == 2
    assert concentration == 80.0
    assert stats[0].percent == 80.0


def test_commit_activity_summarises_history() -> None:
    commits = [
        {
            "commit": {"author": {"date": "2026-03-02T10:00:00Z", "name": "Ali"},
                       "message": "Add feature\n\ndetails"},
            "author": {"login": "ali"},
        },
        {
            "commit": {"author": {"date": "2026-01-05T10:00:00Z", "name": "Ali"},
                       "message": "Initial commit"},
            "author": {"login": "ali"},
        },
    ]
    activity = Analyzer._commit_activity(commits)
    assert activity.analyzed_commits == 2
    assert activity.first_commit == "2026-01-05"
    assert activity.latest_commit == "2026-03-02"
    assert activity.top_authors[0][0] == "ali"
    assert activity.latest_message == "Add feature"


def test_commit_activity_handles_empty_repository() -> None:
    assert Analyzer._commit_activity([]).analyzed_commits == 0


def test_issue_stats_separates_pull_requests() -> None:
    issues = [
        {"state": "open", "created_at": "2026-05-01T00:00:00Z",
         "labels": [{"name": "bug"}]},
        {"state": "closed", "created_at": "2026-04-01T00:00:00Z", "labels": []},
        {"state": "open", "created_at": "2026-04-02T00:00:00Z", "labels": [],
         "pull_request": {"url": "x"}},
    ]
    pulls = [
        {"state": "closed", "merged_at": "2026-04-03T00:00:00Z"},
        {"state": "closed", "merged_at": None},
        {"state": "open", "merged_at": None},
    ]
    stats = Analyzer._issue_stats(issues, pulls)
    assert stats.sampled_issues == 2
    assert stats.open_issues == 1 and stats.closed_issues == 1
    assert stats.merged_pulls == 1 and stats.merge_rate == 50.0
    assert stats.top_labels == [("bug", 1)]


def test_code_structure_counts_files_and_directories() -> None:
    entries = [
        {"path": "src", "type": "tree"},
        {"path": "src/app.py", "type": "blob", "size": 4096},
        {"path": "tests/test_app.py", "type": "blob", "size": 1024},
        {"path": "README.md", "type": "blob", "size": 512},
        {"path": "node_modules/x/index.js", "type": "blob", "size": 99},
    ]
    structure = Analyzer._code_structure(entries, truncated=False)
    assert structure.available and structure.total_files == 4
    assert structure.directories == 1
    assert structure.categories["test"] == 1
    assert structure.generated_files == 1
    assert structure.largest_files[0][0] == "src/app.py"


def test_project_signals_detects_conventions() -> None:
    entries = [
        {"path": "README.md"}, {"path": "requirements.txt"},
        {"path": "tests/test_app.py"}, {"path": ".github/workflows/ci.yml"},
        {"path": "Dockerfile"}, {"path": "manage.py"},
    ]
    report = RepositoryReport(full_name="a/b", owner="a", name="b")
    signals = Analyzer._project_signals(entries, report)
    assert signals.has_readme and signals.has_tests and signals.has_ci
    assert signals.has_dockerfile
    assert "pip" in signals.package_managers
    assert "Django" in signals.frameworks


def test_health_score_is_bounded_and_graded() -> None:
    report = RepositoryReport(full_name="a/b", owner="a", name="b", days_since_push=5,
                              stars=100, forks=20, contributor_count=25)
    report.signals.has_readme = True
    report.signals.has_tests = True
    report.signals.has_ci = True
    report.signals.has_license = True
    score = Analyzer._score_repository(report)
    assert 0 <= score.total <= 100
    assert score.grade != "N/A"
    assert sum(maximum for _, _, maximum in score.breakdown) == 100


def test_compare_users_picks_a_leader() -> None:
    one = UserReport(login="alice", followers=100, total_stars=50)
    two = UserReport(login="bob", followers=10, total_stars=500)
    rows = compare_users([one, two])
    followers = next(r for r in rows if r["metric"] == "Followers")
    assert followers["winner"] == "alice"
    assert compare_users([one]) == []


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "hostile", ["../../etc/passwd", "..\\..\\windows", "a/b/../../c", "$(whoami)", "..."]
)
def test_safe_slug_blocks_path_traversal(hostile: str) -> None:
    slug = safe_slug(hostile)
    assert "/" not in slug and "\\" not in slug
    assert ".." not in slug
    assert slug and not slug.startswith((".", "-"))


@pytest.mark.parametrize("fmt", SUPPORTED_FORMATS)
def test_write_report_creates_readable_files(tmp_path: Path, fmt: str) -> None:
    report = RepositoryReport(full_name="python/cpython", owner="python", name="cpython",
                              stars=1234, description="The Python language")
    path = write_report(report, fmt, tmp_path)
    assert path.exists() and path.stat().st_size > 0
    content = path.read_text(encoding="utf-8")
    if fmt == "json":
        assert json.loads(content)["full_name"] == "python/cpython"
    else:
        assert "cpython" in content


def test_write_report_rejects_unknown_format(tmp_path: Path) -> None:
    from github_analyzer.errors import AnalyzerError

    report = UserReport(login="someone")
    with pytest.raises(AnalyzerError):
        write_report(report, "pdf", tmp_path)


def test_html_report_escapes_injected_markup(tmp_path: Path) -> None:
    report = UserReport(login="someone", bio="<script>alert(1)</script>")
    content = write_report(report, "html", tmp_path).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in content


def test_reports_never_contain_the_token(tmp_path: Path) -> None:
    report = UserReport(login="someone")
    content = write_report(report, "json", tmp_path).read_text(encoding="utf-8")
    assert "token" not in content.lower()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_parser_accepts_documented_flags() -> None:
    from github_analyzer.cli import build_parser

    args = build_parser().parse_args(
        ["--repo", "python/cpython", "--format", "json", "md", "--no-color", "--quick"]
    )
    assert args.repo == "python/cpython"
    assert args.format == ["json", "md"]
    assert args.no_color and args.quick


def test_cli_rejects_owner_without_name(capsys) -> None:
    from github_analyzer.cli import main

    assert main(["--owner", "python"]) == 1
