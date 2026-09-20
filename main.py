#!/usr/bin/env python3
"""GitHub Analyzer - entry point.

    python main.py                    interactive menu
    python main.py --repo owner/name  direct analysis
    python main.py --help             all options
"""

from __future__ import annotations

import sys


def run() -> int:
    """Start the CLI, reporting missing dependencies in plain language."""
    try:
        from github_analyzer.cli import main
    except ImportError as exc:
        package = getattr(exc, "name", None) or "a required package"
        sys.stderr.write(
            f"\n[ERROR] GitHub Analyzer could not start: '{package}' is not installed.\n\n"
            "Install the dependencies first:\n\n"
            "    pip install -r requirements.txt\n\n"
        )
        return 2
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
