"""Command-line interface: argument parsing, interactive menu, orchestration.

Two ways to run:

* ``python main.py``                      → interactive menu
* ``python main.py --repo owner/name``    → direct, scriptable mode
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from . import __app_name__, __version__
from .analyzer import Analyzer, compare_users
from .charts import export_charts, matplotlib_available
from .config import DEFAULT_REPORT_DIR, Settings, resolve_token
from .errors import AnalyzerError, InvalidInputError
from .github_client import GitHubClient
from .models import RepositoryReport, UserReport
from .reports import SUPPORTED_FORMATS, write_report
from .ui import UI
from .utils import parse_repo_reference, validate_username

MIN_PYTHON = (3, 9)
logger = logging.getLogger("github_analyzer")


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="github-analyzer",
        description=f"{__app_name__} — analyse GitHub repositories and developers "
                    "from your terminal.",
        epilog=(
            "Examples:\n"
            "  python main.py\n"
            "  python main.py --repo python/cpython\n"
            "  python main.py --repo https://github.com/pallets/flask --format md html\n"
            "  python main.py --user torvalds --charts\n"
            "  python main.py --compare torvalds gvanrossum\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    target = parser.add_argument_group("analysis targets")
    target.add_argument("--repo", "-r", metavar="OWNER/NAME",
                        help="repository to analyse (owner/name or a GitHub URL)")
    target.add_argument("--owner", metavar="OWNER",
                        help="repository owner (use together with --name)")
    target.add_argument("--name", metavar="NAME",
                        help="repository name (use together with --owner)")
    target.add_argument("--user", "-u", metavar="USERNAME",
                        help="GitHub user or organisation to analyse")
    target.add_argument("--compare", "-c", nargs=2, metavar=("USER1", "USER2"),
                        help="compare two GitHub users")

    output = parser.add_argument_group("output")
    output.add_argument("--format", "-f", nargs="+", default=[], choices=SUPPORTED_FORMATS,
                        help="export report formats (default: none)")
    output.add_argument("--output", "-o", metavar="DIR", default=str(DEFAULT_REPORT_DIR),
                        help="directory for generated reports (default: ./reports)")
    output.add_argument("--charts", action="store_true",
                        help="also export PNG charts (requires matplotlib)")
    output.add_argument("--no-color", action="store_true", help="disable coloured output")
    output.add_argument("--quick", action="store_true",
                        help="repository metadata only; skip commits, contributors and files")

    misc = parser.add_argument_group("misc")
    misc.add_argument("--token", "-t", metavar="TOKEN",
                      help="GitHub token (prefer the GITHUB_TOKEN environment variable)")
    misc.add_argument("--verbose", "-v", action="store_true",
                      help="verbose logging and full tracebacks")
    misc.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    return parser


def _configure_logging(verbose: bool) -> None:
    """Set up logging; quiet by default, detailed with ``--verbose``."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


class Application:
    """Wires the UI, client and analyzer together for one session."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ui = UI(no_color=settings.no_color)
        self.client = GitHubClient(token=settings.token)
        self.analyzer = Analyzer(self.client)

    # -- helpers ------------------------------------------------------- #

    def show_auth_status(self) -> None:
        """Tell the user whether a token was found and the remaining quota."""
        if self.settings.authenticated:
            limit = self.client.get_rate_limit()
            self.ui.success(
                f"Authenticated with GITHUB_TOKEN "
                f"({limit['remaining']}/{limit['limit']} API requests remaining)"
            )
        else:
            self.ui.warn(
                "No GITHUB_TOKEN found — running unauthenticated (60 requests/hour). "
                "See README.md to raise the limit to 5,000/hour."
            )

    def _run_analysis(self, label: str, func, *args, **kwargs):
        """Run an analysis with a spinner and per-step progress messages."""
        steps: list[str] = []
        self.analyzer.progress = steps.append
        try:
            with self.ui.status(label):
                result = func(*args, **kwargs)
        finally:
            self.analyzer.progress = lambda _m: None
        for step in steps:
            self.ui.success(step)
        return result

    def _export(self, report: RepositoryReport | UserReport, formats: Sequence[str],
                charts: bool) -> None:
        """Write requested report files and charts."""
        if not formats and not charts:
            return

        self.ui.rule("EXPORTS")
        for fmt in formats:
            try:
                path = write_report(report, fmt, self.settings.report_dir)
                self.ui.success(f"{fmt.upper()} report → {path}")
            except AnalyzerError as exc:
                self.ui.error(exc)

        if charts:
            if not matplotlib_available():
                self.ui.warn(
                    "PNG charts need matplotlib. Install it with: pip install matplotlib"
                )
                return
            paths = export_charts(report, self.settings.report_dir)
            if paths:
                for path in paths:
                    self.ui.success(f"Chart → {path}")
            else:
                self.ui.warn("No charts could be generated for this report.")

    # -- commands ------------------------------------------------------ #

    def analyze_repository(self, reference: str, formats: Sequence[str] = (),
                           charts: bool = False, quick: bool = False) -> None:
        """Analyse and render one repository."""
        owner, name = parse_repo_reference(reference)
        report = self._run_analysis(
            f"Analyzing {owner}/{name}",
            self.analyzer.analyze_repository, owner, name, deep=not quick,
        )
        self.ui.render_repository(report)
        self._export(report, formats, charts)

    def analyze_user(self, username: str, formats: Sequence[str] = (),
                     charts: bool = False) -> None:
        """Analyse and render one user or organisation."""
        login = validate_username(username)
        report = self._run_analysis(
            f"Analyzing user {login}", self.analyzer.analyze_user, login
        )
        self.ui.render_user(report)
        self._export(report, formats, charts)

    def compare(self, first: str, second: str, formats: Sequence[str] = ()) -> None:
        """Analyse two users and render a side-by-side comparison."""
        one = validate_username(first)
        two = validate_username(second)
        if one.lower() == two.lower():
            raise InvalidInputError("Please provide two different usernames to compare.")

        reports = [
            self._run_analysis(f"Analyzing {login}", self.analyzer.analyze_user, login)
            for login in (one, two)
        ]
        self.ui.render_comparison(reports, compare_users(reports))
        for report in reports:
            self._export(report, formats, charts=False)

    def close(self) -> None:
        """Release network resources."""
        self.client.close()

    # -- interactive menu ---------------------------------------------- #

    def _ask(self, prompt: str) -> str:
        """Read one line of input, treating Ctrl+C / Ctrl+D as an exit."""
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            self.ui.console.print("\n[green]Goodbye.[/green]")
            raise SystemExit(0)

    def _ask_formats(self) -> list[str]:
        """Ask which report formats to export."""
        answer = self._ask(
            "Export reports? (json/csv/md/html, space separated, blank for none): "
        ).lower()
        chosen = [fmt for fmt in answer.split() if fmt in SUPPORTED_FORMATS]
        if answer and not chosen:
            self.ui.warn(f"Ignored unknown formats. Supported: {', '.join(SUPPORTED_FORMATS)}")
        return chosen

    def interactive(self) -> int:
        """Run the menu-driven interactive session."""
        self.ui.banner()
        self.show_auth_status()

        menu = (
            "\n[bold cyan]MAIN MENU[/bold cyan]\n"
            "  [bold]1[/bold]  Analyze a repository\n"
            "  [bold]2[/bold]  Analyze a user or organisation\n"
            "  [bold]3[/bold]  Compare two users\n"
            "  [bold]4[/bold]  Show API rate limit status\n"
            "  [bold]5[/bold]  Exit\n"
        )

        while True:
            self.ui.console.print(menu)
            choice = self._ask("Select an option [1-5]: ")

            try:
                if choice == "1":
                    reference = self._ask("Repository (owner/name or URL): ")
                    formats = self._ask_formats()
                    self.analyze_repository(reference, formats)
                elif choice == "2":
                    username = self._ask("GitHub username: ")
                    formats = self._ask_formats()
                    self.analyze_user(username, formats)
                elif choice == "3":
                    first = self._ask("First username: ")
                    second = self._ask("Second username: ")
                    self.compare(first, second)
                elif choice == "4":
                    limit = self.client.get_rate_limit()
                    self.ui.info(
                        f"API requests remaining: {limit['remaining']}/{limit['limit']}"
                    )
                elif choice in ("5", "q", "quit", "exit"):
                    self.ui.console.print("[green]Goodbye.[/green]")
                    return 0
                else:
                    self.ui.warn("Please choose a number between 1 and 5.")
            except AnalyzerError as exc:
                self.ui.error(exc)
                if self.settings.verbose:
                    self.ui.console.print_exception()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _check_python_version() -> None:
    """Abort early with a clear message on unsupported interpreters."""
    if sys.version_info < MIN_PYTHON:
        required = ".".join(str(part) for part in MIN_PYTHON)
        current = ".".join(str(part) for part in sys.version_info[:3])
        sys.stderr.write(
            f"[ERROR] {__app_name__} requires Python {required} or newer "
            f"(found {current}).\n"
        )
        raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> int:
    """Program entry point. Returns a process exit code."""
    _check_python_version()

    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    settings = Settings(
        token=resolve_token(args.token),
        verbose=args.verbose,
        no_color=args.no_color,
        report_dir=Path(args.output).expanduser().resolve(),
    )

    app = Application(settings)
    ui = app.ui

    try:
        repo_reference = args.repo
        if not repo_reference and args.owner and args.name:
            repo_reference = f"{args.owner}/{args.name}"
        elif bool(args.owner) != bool(args.name):
            raise InvalidInputError("--owner and --name must be used together.")

        direct = bool(repo_reference or args.user or args.compare)
        if direct:
            ui.banner()
            app.show_auth_status()

        if repo_reference:
            app.analyze_repository(repo_reference, args.format, args.charts, args.quick)
        if args.user:
            app.analyze_user(args.user, args.format, args.charts)
        if args.compare:
            app.compare(args.compare[0], args.compare[1], args.format)

        if not direct:
            return app.interactive()
        return 0

    except AnalyzerError as exc:
        ui.error(exc)
        if args.verbose:
            ui.console.print_exception()
        return 1
    except KeyboardInterrupt:
        ui.console.print("\n[yellow]Cancelled by user.[/yellow]")
        return 130
    except Exception as exc:  # noqa: BLE001 - last line of defence
        ui.error(
            "An unexpected internal error occurred.",
            hints=(
                f"Details: {type(exc).__name__}: {exc}",
                "Re-run with --verbose to see the full traceback",
            ),
        )
        if args.verbose:
            ui.console.print_exception()
        return 1
    finally:
        app.close()
