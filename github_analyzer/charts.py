"""Optional PNG chart export.

Charts are a bonus: ``matplotlib`` is **not** a required dependency. If it is
not installed, :func:`export_charts` returns an empty list and the CLI simply
tells the user how to enable the feature. Terminal charts always work.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .models import RepositoryReport, UserReport
from .reports import safe_slug

logger = logging.getLogger(__name__)

Report = RepositoryReport | UserReport


def matplotlib_available() -> bool:
    """Return True when PNG export is possible in this environment."""
    try:  # pragma: no cover - depends on the environment
        import matplotlib  # noqa: F401

        return True
    except ImportError:
        return False


def export_charts(report: Report, output_dir: Path) -> list[Path]:
    """Write PNG charts for ``report`` and return the created paths."""
    if not matplotlib_available():
        return []

    import matplotlib

    matplotlib.use("Agg")  # headless backend: no GUI window is ever opened
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_slug(
        report.full_name if isinstance(report, RepositoryReport) else report.login
    )
    created: list[Path] = []

    def save(fig, suffix: str) -> None:
        path = output_dir / f"{stem}-{suffix}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=130)
        plt.close(fig)
        created.append(path)

    try:
        if report.languages:
            langs = report.languages[:8]
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.barh([l.name for l in langs][::-1], [l.percent for l in langs][::-1],
                    color="#2f81f7")
            ax.set_xlabel("Share (%)")
            ax.set_title("Language distribution")
            save(fig, "languages")

        if isinstance(report, RepositoryReport):
            if report.commits.monthly:
                months = [m for m, _ in report.commits.monthly]
                counts = [c for _, c in report.commits.monthly]
                fig, ax = plt.subplots(figsize=(8, 4))
                ax.plot(months, counts, marker="o", color="#2f81f7")
                ax.set_ylabel("Commits")
                ax.set_title("Commits per month (sampled)")
                ax.tick_params(axis="x", rotation=45)
                ax.grid(alpha=0.3)
                save(fig, "commits")

            if report.contributors:
                top = report.contributors[:10][::-1]
                fig, ax = plt.subplots(figsize=(7, 4.5))
                ax.barh([c.login for c in top], [c.contributions for c in top],
                        color="#3fb950")
                ax.set_xlabel("Commits")
                ax.set_title("Top contributors")
                save(fig, "contributors")
        elif report.top_repos:
            top = report.top_repos[:10][::-1]
            fig, ax = plt.subplots(figsize=(7, 4.5))
            ax.barh([r["name"] for r in top], [r["stars"] for r in top], color="#3fb950")
            ax.set_xlabel("Stars")
            ax.set_title("Top repositories by stars")
            save(fig, "top-repos")
    except Exception as exc:  # noqa: BLE001 - charts must never break a run
        logger.debug("Chart export failed: %s", exc)

    return created
