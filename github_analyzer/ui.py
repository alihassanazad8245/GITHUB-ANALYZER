"""Terminal presentation layer built on ``rich``.

Nothing here performs network calls or analysis; it only renders report
objects. Keeping rendering separate means the same reports can be exported to
files without duplicating logic.
"""

from __future__ import annotations

from typing import Any, Sequence

from rich.align import Align
from rich.box import HEAVY, ROUNDED, SIMPLE
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import __app_name__, __tagline__, __version__
from .errors import AnalyzerError
from .models import RepositoryReport, UserReport
from .utils import bar, human_number, human_size, sparkline, truncate

BANNER = r"""
  ____ _ _   _   _       _        _                _
 / ___(_) |_| | | |_   _| |__    /_\  _ __   __ _| |_   _ _______ _ __
| |  _| | __| |_| | | | | '_ \  //_\\| '_ \ / _` | | | | |_  / _ \ '__|
| |_| | | |_|  _  | |_| | |_) |/  _  \ | | | (_| | | |_| |/ /  __/ |
 \____|_|\__|_| |_|\__,_|_.__/ \_/ \_/_| |_|\__,_|_|\__, /___\___|_|
                                                    |___/
"""


class UI:
    """All terminal output flows through this class."""

    def __init__(self, no_color: bool = False) -> None:
        self.console = Console(no_color=no_color, highlight=False, soft_wrap=False)
        self.no_color = no_color

    # ------------------------------------------------------------------ #
    # Generic messages
    # ------------------------------------------------------------------ #

    def banner(self) -> None:
        """Print the application banner."""
        header = Text(BANNER.strip("\n"), style="bold cyan" if not self.no_color else "")
        subtitle = Text.assemble(
            (f"{__app_name__} ", "bold white"),
            (f"v{__version__}", "dim"),
            ("  •  ", "dim"),
            (__tagline__, "italic cyan"),
        )
        self.console.print(
            Panel(
                Group(Align.center(header), Align.center(subtitle)),
                box=HEAVY,
                border_style="cyan",
                padding=(0, 2),
            )
        )

    def rule(self, title: str) -> None:
        """Print a section separator."""
        self.console.print()
        self.console.print(Rule(f"[bold cyan]{title}[/bold cyan]", style="cyan"))

    def success(self, message: str) -> None:
        """Print a success line."""
        self.console.print(f"[bold green]\\[OK][/bold green] {message}")

    def info(self, message: str) -> None:
        """Print an informational line."""
        self.console.print(f"[bold blue]\\[i][/bold blue] {message}")

    def warn(self, message: str) -> None:
        """Print a warning line."""
        self.console.print(f"[bold yellow]\\[!][/bold yellow] {message}")

    def error(self, error: AnalyzerError | str, hints: Sequence[str] = ()) -> None:
        """Print a friendly error panel with optional hints."""
        if isinstance(error, AnalyzerError):
            message, hint_list = error.message, list(error.hints)
        else:
            message, hint_list = str(error), list(hints)

        body = Text(message, style="bold red")
        if hint_list:
            body.append("\n\nPlease check:\n", style="white")
            for hint in hint_list:
                body.append(f"  • {hint}\n", style="dim white")

        self.console.print(
            Panel(body, title="[bold red]ERROR[/bold red]", border_style="red", box=ROUNDED)
        )

    def status(self, message: str):
        """Context-manager spinner used while network calls are in flight."""
        return self.console.status(f"[cyan]{message}…[/cyan]", spinner="dots")

    # ------------------------------------------------------------------ #
    # Shared building blocks
    # ------------------------------------------------------------------ #

    #: Minimum console width needed before two panels are placed side by side.
    SIDE_BY_SIDE_WIDTH = 104

    def _kv_table(self, rows: Sequence[tuple[str, Any]], title: str | None = None,
                  key_width: int = 24, value_width: int | None = None) -> Table:
        """Two-column key/value table."""
        table = Table(box=SIMPLE, show_header=False, title=title, title_style="bold cyan",
                      pad_edge=False, expand=False)
        table.add_column("Field", style="cyan", no_wrap=True, width=key_width)
        table.add_column("Value", style="white", overflow="fold",
                         min_width=value_width or 28)
        for key, value in rows:
            table.add_row(key, str(value))
        return table

    def _side_by_side(self, left: Table, right: Table) -> None:
        """Print two tables next to each other, stacking them on narrow terminals.

        Rich shrinks columns to fit, which mangles long values in an 80-column
        window, so below :attr:`SIDE_BY_SIDE_WIDTH` the panels are stacked.
        """
        if self.console.width < self.SIDE_BY_SIDE_WIDTH:
            self.console.print(left)
            self.console.print(right)
            return

        grid = Table.grid(padding=(0, 4))
        grid.add_column()
        grid.add_column()
        grid.add_row(left, right)
        self.console.print(grid)

    #: Bar colours, applied by rank so the biggest values read first.
    _RANK_COLOURS = ("bright_cyan", "cyan", "blue", "bright_blue")

    def _rank_colour(self, index: int, total: int) -> str:
        """Pick a bar colour based on a row's position in the chart."""
        if total <= 1:
            return self._RANK_COLOURS[0]
        step = index * len(self._RANK_COLOURS) // total
        return self._RANK_COLOURS[min(step, len(self._RANK_COLOURS) - 1)]

    def bar_chart(
        self,
        title: str,
        data: Sequence[tuple[str, float]],
        suffix: str = "",
        width: int = 30,
        label_width: int = 16,
        trend: bool = False,
    ) -> None:
        """Render a horizontal bar chart, optionally with a trend sparkline."""
        if not data:
            self.console.print(f"[dim]{title}: no data available[/dim]")
            return

        # Keep the chart inside the terminal even in a narrow window.
        width = max(10, min(width, self.console.width - label_width - 18))
        maximum = max(value for _, value in data) or 1
        values = [
            f"{v:,.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}"
            for _, v in data
        ]
        value_width = max(len(v) for v in values) + len(suffix)

        body = Text()
        for index, ((label, value), shown) in enumerate(zip(data, values)):
            colour = self._rank_colour(index, len(data))
            body.append(f"{truncate(label, label_width):<{label_width}} ", style="white")
            body.append(f"{bar(value, maximum, width):<{width}} ", style=colour)
            body.append(f"{shown + suffix:>{value_width}}\n", style="bold white")

        if trend and len(data) > 2:
            body.append("\nTrend  ", style="dim white")
            body.append(sparkline([v for _, v in data]), style="bright_cyan")

        self.console.print(
            Panel(body, title=f"[bold]{title}[/bold]", border_style="blue", box=ROUNDED)
        )

    def _gauge(self, value: int, maximum: int, width: int = 34) -> Text:
        """Render a coloured progress gauge for a score out of ``maximum``."""
        colour = "green" if value >= maximum * 0.7 else "yellow" if value >= maximum * 0.45 else "red"
        filled = bar(value, maximum, width)
        gauge = Text()
        gauge.append(filled, style=f"bold {colour}")
        gauge.append("░" * max(0, width - len(filled)), style="dim")
        return gauge

    # ------------------------------------------------------------------ #
    # Repository rendering
    # ------------------------------------------------------------------ #

    def render_repository(self, report: RepositoryReport) -> None:
        """Print the full repository dashboard."""
        self._repo_overview(report)
        self._repo_languages(report)
        self._repo_contributors(report)
        self._repo_commits(report)
        self._repo_issues(report)
        self._repo_structure(report)
        self._repo_health(report)
        self._footer(report.warnings, report.api_requests, report.generated_at)

    def _repo_overview(self, r: RepositoryReport) -> None:
        self.rule("REPOSITORY OVERVIEW")
        flags = []
        if r.private:
            flags.append("private")
        if r.fork:
            flags.append("fork")
        if r.archived:
            flags.append("archived")
        if r.disabled:
            flags.append("disabled")

        left = self._kv_table([
            ("Repository", r.full_name),
            ("Owner", r.owner),
            ("Description", r.description or "N/A"),
            ("URL", r.url),
            ("Homepage", r.homepage or "N/A"),
            ("Visibility", "Private" if r.private else "Public"),
            ("Status", ", ".join(flags) if flags else "active"),
            ("Default branch", r.default_branch),
            ("License", r.license_name),
            ("Topics", ", ".join(r.topics) if r.topics else "None"),
        ])

        pushed = f"{r.pushed_at}" + (
            f"  ({r.days_since_push} days ago)" if r.days_since_push is not None else ""
        )
        right = self._kv_table([
            ("Stars", human_number(r.stars)),
            ("Forks", human_number(r.forks)),
            ("Watchers", human_number(r.watchers)),
            ("Open issues + PRs", human_number(r.open_issues_and_pulls)),
            ("Repository size", human_size(r.size_kb)),
            ("Created", r.created_at),
            ("Last updated", r.updated_at),
            ("Last push", pushed),
            ("Releases", f"{r.release_count} (latest: {r.latest_release})"),
        ])

        self._side_by_side(left, right)

    def _repo_languages(self, r: RepositoryReport) -> None:
        self.rule("LANGUAGE DISTRIBUTION")
        self.bar_chart(
            "Languages (by bytes of code)",
            [(lang.name, lang.percent) for lang in r.languages[:10]],
            suffix="%",
        )

    def _repo_contributors(self, r: RepositoryReport) -> None:
        self.rule("CONTRIBUTORS")
        if not r.contributors:
            self.console.print("[dim]No contributor data available.[/dim]")
            return

        table = Table(box=ROUNDED, border_style="blue", header_style="bold cyan")
        table.add_column("#", width=3, justify="right")
        table.add_column("Contributor", style="white")
        table.add_column("Commits", justify="right")
        table.add_column("Share", justify="right")
        table.add_column("Distribution")

        top = max(c.contributions for c in r.contributors)
        for index, contributor in enumerate(r.contributors[:10], start=1):
            table.add_row(
                str(index),
                contributor.login,
                human_number(contributor.contributions),
                f"{contributor.percent}%",
                f"[{self._rank_colour(index - 1, 10)}]{bar(contributor.contributions, top, 20)}[/]",
            )
        self.console.print(table)
        self.console.print(
            f"[dim]Total contributors: {r.contributor_count} • "
            f"Top contributor share: {r.contributor_concentration}% "
            f"(Analyzer Metric)[/dim]"
        )

    def _repo_commits(self, r: RepositoryReport) -> None:
        self.rule("COMMIT ACTIVITY")
        c = r.commits
        if not c.analyzed_commits:
            self.console.print("[dim]No commit history available (the repository may be empty).[/dim]")
            return

        self.console.print(self._kv_table([
            ("Commits analysed", human_number(c.analyzed_commits)),
            ("Sampled range", f"{c.first_commit} → {c.latest_commit}"),
            ("Average per month", f"{c.commits_per_month} (Analyzer Metric)"),
            ("Latest commit", c.latest_message),
        ]))
        self.bar_chart("Commits per month", [(m, v) for m, v in c.monthly],
                       width=26, label_width=10, trend=True)
        self.bar_chart("Commits per weekday", [(d, v) for d, v in c.weekday], width=26, label_width=10)

    def _repo_issues(self, r: RepositoryReport) -> None:
        self.rule("ISSUES & PULL REQUESTS")
        i = r.issues
        self._side_by_side(
            self._kv_table([
                ("Issues sampled", human_number(i.sampled_issues)),
                ("Open issues", human_number(i.open_issues)),
                ("Closed issues", human_number(i.closed_issues)),
                ("Newest issue", i.newest_issue),
                ("Oldest sampled", i.oldest_sampled_issue),
            ], title="Issues"),
            self._kv_table([
                ("PRs sampled", human_number(i.sampled_pulls)),
                ("Open PRs", human_number(i.open_pulls)),
                ("Closed PRs", human_number(i.closed_pulls)),
                ("Merged PRs", human_number(i.merged_pulls)),
                ("Merge rate", f"{i.merge_rate}% (Analyzer Metric)"),
            ], title="Pull requests"),
        )
        if i.top_labels:
            self.bar_chart("Most used issue labels", list(i.top_labels), width=22)

    def _repo_structure(self, r: RepositoryReport) -> None:
        self.rule("CODE STRUCTURE")
        s = r.structure
        if not s.available:
            self.console.print("[dim]File tree unavailable (empty repository or restricted access).[/dim]")
            return

        self.console.print(self._kv_table([
            ("Total files", human_number(s.total_files)),
            ("Directories", human_number(s.directories)),
            ("Code files", human_number(s.categories.get("code", 0))),
            ("Test files", human_number(s.categories.get("test", 0))),
            ("Config files", human_number(s.categories.get("config", 0))),
            ("Documentation files", human_number(s.categories.get("docs", 0))),
            ("Generated / vendored", human_number(s.generated_files)),
            ("Top-level entries", ", ".join(s.top_level[:12]) or "N/A"),
        ]))

        if s.extensions:
            self.bar_chart("File types", [(ext, n) for ext, n in s.extensions], width=22, label_width=12)

        if s.largest_files:
            table = Table(box=SIMPLE, header_style="bold cyan", title="Largest files",
                          title_style="bold cyan")
            table.add_column("Path", style="white", overflow="fold")
            table.add_column("Size", justify="right")
            for path, size in s.largest_files:
                table.add_row(truncate(path, 60), human_size(size // 1024 or 1))
            self.console.print(table)

        sig = r.signals
        def mark(value: bool) -> str:
            return "[green]yes[/green]" if value else "[red]no[/red]"

        self.console.print(self._kv_table([
            ("README", mark(sig.has_readme)),
            ("License file", mark(sig.has_license)),
            ("Contributing guide", mark(sig.has_contributing)),
            ("Tests detected", mark(sig.has_tests)),
            ("CI configuration", mark(sig.has_ci)),
            ("Dockerfile", mark(sig.has_dockerfile)),
            ("Package managers", ", ".join(sig.package_managers) or "None detected"),
            ("Frameworks", ", ".join(sig.frameworks) or "None detected"),
        ], title="Project signals"))

    def _repo_health(self, r: RepositoryReport) -> None:
        self.rule("HEALTH SUMMARY  (Analyzer Metric — not an official GitHub score)")
        h = r.health
        colour = "green" if h.total >= 70 else "yellow" if h.total >= 45 else "red"

        table = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("Dimension", style="white", width=24)
        table.add_column("Score", justify="right", width=8)
        table.add_column("Rating")
        for label, score, maximum in h.breakdown:
            table.add_row(
                label, f"{score}/{maximum}", f"[cyan]{bar(score, maximum, 20)}[/cyan]"
            )

        headline = Text()
        headline.append("Overall estimate  ", style="white")
        headline.append_text(self._gauge(h.total, 100))
        headline.append(f"  {h.total}/100", style=f"bold {colour}")
        headline.append(f"\nGrade             {h.grade}\n", style=f"bold {colour}")

        content: list[Any] = [headline, table]
        if h.notes:
            notes = Text("\nObservations:\n", style="bold white")
            for note in h.notes:
                notes.append(f"  • {note}\n", style="yellow")
            content.append(notes)

        self.console.print(
            Panel(Group(*content), border_style=colour, box=ROUNDED,
                  title="[bold]REPOSITORY HEALTH ESTIMATE[/bold]")
        )

    # ------------------------------------------------------------------ #
    # User rendering
    # ------------------------------------------------------------------ #

    def render_user(self, report: UserReport) -> None:
        """Print the full user/organisation dashboard."""
        self.rule("PROFILE OVERVIEW")
        self._side_by_side(
            self._kv_table([
                ("Username", report.login),
                ("Name", report.name),
                ("Account type", report.account_type),
                ("Bio", report.bio or "N/A"),
                ("Company", report.company or "N/A"),
                ("Location", report.location or "N/A"),
                ("Website", report.blog or "N/A"),
                ("Profile URL", report.profile_url),
            ]),
            self._kv_table([
                ("Followers", human_number(report.followers)),
                ("Following", human_number(report.following)),
                ("Public repositories", human_number(report.public_repos)),
                ("Public gists", human_number(report.public_gists)),
                ("Joined GitHub", report.created_at),
                ("Account age", f"{report.account_age_days} days"
                    if report.account_age_days is not None else "N/A"),
                ("Top language", report.top_language),
                ("Last public push", f"{report.days_since_last_push} days ago"
                    if report.days_since_last_push is not None else "N/A"),
            ]),
        )

        self.rule("REPOSITORY PORTFOLIO")
        self.console.print(self._kv_table([
            ("Repositories analysed", human_number(report.analyzed_repos)),
            ("Original / forked", f"{report.original_repos} / {report.forked_repos}"),
            ("Archived", human_number(report.archived_repos)),
            ("Total stars received", human_number(report.total_stars)),
            ("Total forks received", human_number(report.total_forks)),
            ("With a license", f"{report.repos_with_license}/{report.analyzed_repos}"),
            ("With a description", f"{report.repos_with_description}/{report.analyzed_repos}"),
        ]))

        if report.languages:
            self.bar_chart(
                "Languages across repositories (share of repos)",
                [(lang.name, lang.percent) for lang in report.languages[:10]],
                suffix="%",
            )

        if report.yearly_activity:
            self.bar_chart(
                "Repositories created per year",
                [(year, count) for year, count in report.yearly_activity],
                width=26,
                label_width=8,
            )

        if report.top_repos:
            self.rule("TOP REPOSITORIES BY STARS")
            table = Table(box=ROUNDED, border_style="blue", header_style="bold cyan")
            table.add_column("#", width=3, justify="right")
            table.add_column("Repository", style="white")
            table.add_column("Stars", justify="right")
            table.add_column("Forks", justify="right")
            table.add_column("Language")
            table.add_column("Updated")
            for index, item in enumerate(report.top_repos, start=1):
                table.add_row(
                    str(index), item["name"], human_number(item["stars"]),
                    human_number(item["forks"]), item["language"], item["updated"],
                )
            self.console.print(table)

        self._footer(report.warnings, report.api_requests, report.generated_at)

    def render_comparison(self, users: Sequence[UserReport], rows: Sequence[dict[str, Any]]) -> None:
        """Print a side-by-side comparison of two analysed users."""
        self.rule("USER COMPARISON")
        table = Table(box=ROUNDED, border_style="blue", header_style="bold cyan")
        table.add_column("Metric", style="white", width=28)
        for user in users:
            table.add_column(user.login, justify="right")
        table.add_column("Leader", style="bold green")

        for row in rows:
            values = [
                human_number(v) if isinstance(v, int) else str(v) for v in row["values"]
            ]
            table.add_row(row["metric"], *values, str(row["winner"]))
        self.console.print(table)

        wins = [row["winner"] for row in rows if row["winner"] not in ("Tie", "-")]
        if wins:
            leader = max(set(wins), key=wins.count)
            self.console.print(
                Panel(
                    Text(f"{leader} leads on {wins.count(leader)} of {len(wins)} compared metrics.",
                         style="bold green"),
                    border_style="green", box=ROUNDED, title="[bold]SUMMARY[/bold]",
                )
            )

    # ------------------------------------------------------------------ #
    # Footer
    # ------------------------------------------------------------------ #

    def _footer(self, warnings: Sequence[str], api_requests: int, generated_at: str) -> None:
        """Print warnings plus the scan metadata line."""
        if warnings:
            text = Text()
            for warning in warnings:
                text.append(f"• {warning}\n", style="yellow")
            self.console.print(
                Panel(text, title="[bold yellow]NOTES[/bold yellow]",
                      border_style="yellow", box=ROUNDED)
            )
        self.console.print(
            f"[dim]Scan completed {generated_at} • {api_requests} API requests • "
            f"analyzer v{__version__}[/dim]"
        )
