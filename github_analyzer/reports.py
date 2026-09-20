"""Report exporters.

Supported formats: ``json``, ``csv``, ``md`` and ``html``. The HTML file is a
self-contained static export — the analyzer itself stays a CLI tool.

Reports never contain the GitHub token or any other secret: they are built
solely from the public data held in the report models.
"""

from __future__ import annotations

import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __app_name__, __version__
from .errors import AnalyzerError
from .models import RepositoryReport, UserReport

SUPPORTED_FORMATS = ("json", "csv", "md", "html")

Report = RepositoryReport | UserReport

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_slug(value: str) -> str:
    """Turn ``owner/repo`` into a filesystem-safe stem.

    Path separators and traversal sequences are stripped, so a hostile
    repository name can never escape the output directory.
    """
    slug = _SAFE_NAME.sub("-", value.replace("/", "-"))
    slug = re.sub(r"\.{2,}", ".", slug).strip("-.")
    return slug or "report"


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten a nested report dict into ``key -> string`` pairs for CSV."""
    flat: dict[str, str] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        elif isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (dict, list, tuple)):
                flat[name] = json.dumps(value, ensure_ascii=False, default=str)
            else:
                flat[name] = ", ".join(str(item) for item in value)
        else:
            flat[name] = "" if value is None else str(value)
    return flat


def _target_name(report: Report) -> str:
    """Human-readable subject of the report."""
    return report.full_name if isinstance(report, RepositoryReport) else report.login


def write_report(report: Report, fmt: str, output_dir: Path) -> Path:
    """Write ``report`` in ``fmt`` into ``output_dir`` and return the path."""
    fmt = fmt.lower().strip()
    if fmt not in SUPPORTED_FORMATS:
        raise AnalyzerError(
            f"Unsupported report format '{fmt}'.",
            hints=(f"Supported formats: {', '.join(SUPPORTED_FORMATS)}",),
        )

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AnalyzerError(
            f"Could not create the output directory '{output_dir}'.",
            hints=("Check the path and your write permissions",),
        ) from exc

    kind = "repository" if isinstance(report, RepositoryReport) else "user"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"{kind}-{safe_slug(_target_name(report))}-{stamp}.{fmt}"

    try:
        writer = {
            "json": _write_json,
            "csv": _write_csv,
            "md": _write_markdown,
            "html": _write_html,
        }[fmt]
        writer(report, path)
    except OSError as exc:
        raise AnalyzerError(
            f"Could not write the report to '{path}'.",
            hints=("Check disk space and write permissions",),
        ) from exc

    return path


# --------------------------------------------------------------------------- #
# Format writers
# --------------------------------------------------------------------------- #


def _write_json(report: Report, path: Path) -> None:
    path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _write_csv(report: Report, path: Path) -> None:
    flat = _flatten(report.to_dict())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["field", "value"])
        for key, value in flat.items():
            writer.writerow([key, value])


def _markdown_lines(report: Report) -> list[str]:
    """Build the Markdown body shared by the md and html exporters."""
    lines = [f"# {__app_name__} report", ""]
    lines.append(f"**Subject:** {_target_name(report)}  ")
    lines.append(f"**Generated:** {report.generated_at}  ")
    lines.append(f"**Analyzer version:** {report.analyzer_version}")
    lines.append("")

    if isinstance(report, RepositoryReport):
        lines += [
            "## Overview", "",
            f"- URL: {report.url}",
            f"- Description: {report.description or 'N/A'}",
            f"- Visibility: {'Private' if report.private else 'Public'}",
            f"- Default branch: `{report.default_branch}`",
            f"- License: {report.license_name}",
            f"- Topics: {', '.join(report.topics) or 'None'}",
            f"- Created: {report.created_at} | Last push: {report.pushed_at}",
            "",
            "## Key statistics", "",
            "| Metric | Value |", "| --- | --- |",
            f"| Stars | {report.stars:,} |",
            f"| Forks | {report.forks:,} |",
            f"| Watchers | {report.watchers:,} |",
            f"| Open issues + PRs | {report.open_issues_and_pulls:,} |",
            f"| Size (KB) | {report.size_kb:,} |",
            f"| Contributors | {report.contributor_count:,} |",
            f"| Releases | {report.release_count:,} |",
            "",
        ]
        if report.languages:
            lines += ["## Languages", "", "| Language | Share | Bytes |", "| --- | --- | --- |"]
            lines += [f"| {l.name} | {l.percent}% | {l.bytes:,} |" for l in report.languages[:12]]
            lines.append("")
        if report.contributors:
            lines += ["## Top contributors", "", "| Contributor | Commits | Share |",
                      "| --- | --- | --- |"]
            lines += [
                f"| {c.login} | {c.contributions:,} | {c.percent}% |"
                for c in report.contributors[:10]
            ]
            lines.append("")
        if report.commits.monthly:
            lines += ["## Commits per month", "", "| Month | Commits |", "| --- | --- |"]
            lines += [f"| {m} | {v} |" for m, v in report.commits.monthly]
            lines.append("")

        i = report.issues
        lines += [
            "## Issues and pull requests", "",
            f"- Issues sampled: {i.sampled_issues} (open {i.open_issues}, closed {i.closed_issues})",
            f"- Pull requests sampled: {i.sampled_pulls} "
            f"(open {i.open_pulls}, merged {i.merged_pulls})",
            f"- Merge rate: {i.merge_rate}% *(Analyzer Metric)*",
            "",
            "## Project signals", "",
            f"- README: {report.signals.has_readme}",
            f"- License file: {report.signals.has_license}",
            f"- Tests detected: {report.signals.has_tests}",
            f"- CI configuration: {report.signals.has_ci}",
            f"- Dockerfile: {report.signals.has_dockerfile}",
            f"- Package managers: {', '.join(report.signals.package_managers) or 'None detected'}",
            f"- Frameworks: {', '.join(report.signals.frameworks) or 'None detected'}",
            "",
            "## Health estimate", "",
            "> This is an **Analyzer Metric** computed by this tool from public signals.",
            "> It is not an official GitHub score.",
            "",
            f"**{report.health.total}/100 — {report.health.grade}**", "",
            "| Dimension | Score |", "| --- | --- |",
        ]
        lines += [f"| {label} | {score}/{maximum} |" for label, score, maximum in report.health.breakdown]
        lines.append("")
        if report.health.notes:
            lines += ["### Observations", ""] + [f"- {n}" for n in report.health.notes] + [""]
    else:
        lines += [
            "## Profile", "",
            f"- Name: {report.name}",
            f"- Type: {report.account_type}",
            f"- Profile: {report.profile_url}",
            f"- Location: {report.location or 'N/A'}",
            f"- Joined: {report.created_at}",
            "",
            "## Key statistics", "",
            "| Metric | Value |", "| --- | --- |",
            f"| Followers | {report.followers:,} |",
            f"| Following | {report.following:,} |",
            f"| Public repositories | {report.public_repos:,} |",
            f"| Repositories analysed | {report.analyzed_repos:,} |",
            f"| Total stars received | {report.total_stars:,} |",
            f"| Total forks received | {report.total_forks:,} |",
            f"| Top language | {report.top_language} |",
            "",
        ]
        if report.languages:
            lines += ["## Languages", "", "| Language | Share of repositories |", "| --- | --- |"]
            lines += [f"| {l.name} | {l.percent}% |" for l in report.languages[:12]]
            lines.append("")
        if report.top_repos:
            lines += ["## Top repositories", "", "| Repository | Stars | Forks | Language | Updated |",
                      "| --- | --- | --- | --- | --- |"]
            lines += [
                f"| {r['name']} | {r['stars']:,} | {r['forks']:,} | {r['language']} | {r['updated']} |"
                for r in report.top_repos
            ]
            lines.append("")

    if report.warnings:
        lines += ["## Notes", ""] + [f"- {w}" for w in report.warnings] + [""]

    lines.append(f"*Generated by {__app_name__} v{__version__}.*")
    return lines


def _write_markdown(report: Report, path: Path) -> None:
    path.write_text("\n".join(_markdown_lines(report)) + "\n", encoding="utf-8")


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --fg:#1b1f24; --bg:#ffffff; --muted:#5a6672;
  --accent:#0969da; --line:#d8dee4; --chip:#f2f5f8; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --fg:#e6edf3; --bg:#0d1117; --muted:#9198a1; --accent:#58a6ff;
    --line:#30363d; --chip:#161b22; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:2rem 1rem; background:var(--bg); color:var(--fg);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
main {{ max-width:940px; margin:0 auto; }}
h1 {{ font-size:1.8rem; border-bottom:2px solid var(--accent); padding-bottom:.5rem; }}
h2 {{ margin-top:2.2rem; font-size:1.25rem; border-bottom:1px solid var(--line);
  padding-bottom:.35rem; }}
h3 {{ font-size:1.05rem; color:var(--muted); }}
table {{ border-collapse:collapse; width:100%; margin:1rem 0; font-size:.95rem;
  display:block; overflow-x:auto; }}
th,td {{ border:1px solid var(--line); padding:.5rem .75rem; text-align:left; }}
th {{ background:var(--chip); }}
tr:nth-child(even) td {{ background:color-mix(in srgb, var(--chip) 45%, transparent); }}
blockquote {{ margin:1rem 0; padding:.6rem 1rem; border-left:4px solid var(--accent);
  background:var(--chip); color:var(--muted); }}
code {{ background:var(--chip); padding:.1rem .35rem; border-radius:4px; }}
footer {{ margin-top:3rem; color:var(--muted); font-size:.85rem;
  border-top:1px solid var(--line); padding-top:1rem; }}
ul {{ padding-left:1.2rem; }}
</style>
</head>
<body><main>
{body}
<footer>Static export generated by {app} v{version}. Analyzer Metrics are produced by this
tool and are not official GitHub measurements.</footer>
</main></body>
</html>
"""


def _markdown_to_html(lines: list[str]) -> str:
    """Convert the limited Markdown produced above into HTML.

    Only the subset this module emits is supported (headings, tables, lists,
    blockquotes, bold, inline code). All text is HTML-escaped first.
    """
    out: list[str] = []
    in_table = False
    in_list = False
    header_done = False

    def inline(text: str) -> str:
        text = html.escape(text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
        return text

    def close_blocks() -> None:
        nonlocal in_table, in_list, header_done
        if in_table:
            out.append("</tbody></table>")
            in_table = False
            header_done = False
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            close_blocks()
            continue

        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                out.append("<table><thead><tr>")
                out += [f"<th>{inline(c)}</th>" for c in cells]
                out.append("</tr></thead><tbody>")
                in_table, header_done = True, True
                continue
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue

        close_blocks()

        if line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
        else:
            out.append(f"<p>{inline(line.rstrip('  '))}</p>")

    close_blocks()
    return "\n".join(out)


def _write_html(report: Report, path: Path) -> None:
    body = _markdown_to_html(_markdown_lines(report))
    path.write_text(
        _HTML_TEMPLATE.format(
            title=html.escape(f"{__app_name__} — {_target_name(report)}"),
            body=body,
            app=html.escape(__app_name__),
            version=html.escape(__version__),
        ),
        encoding="utf-8",
    )
