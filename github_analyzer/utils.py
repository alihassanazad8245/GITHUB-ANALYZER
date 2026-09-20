"""Small, dependency-free helpers shared across the package."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Sequence

from .errors import InvalidInputError

# GitHub usernames: alphanumeric or single hyphens, 1-39 chars, no leading or
# trailing hyphen. Repository names allow a wider set of characters.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def is_valid_username(username: str) -> bool:
    """Return True if ``username`` is a syntactically valid GitHub login."""
    return bool(_USERNAME_RE.match(username.strip()))


def validate_username(username: str) -> str:
    """Return the cleaned username or raise :class:`InvalidInputError`."""
    cleaned = username.strip().lstrip("@")
    if not cleaned:
        raise InvalidInputError("No username was entered.")
    if not is_valid_username(cleaned):
        raise InvalidInputError(
            f"'{cleaned}' is not a valid GitHub username.",
            hints=(
                "Usernames may contain letters, digits and single hyphens",
                "They cannot contain spaces and are at most 39 characters",
            ),
        )
    return cleaned


def parse_repo_reference(reference: str) -> tuple[str, str]:
    """Parse a repository reference into an ``(owner, repo)`` pair.

    Accepts ``owner/repo``, a full or partial GitHub URL, an ``https://`` or
    ``git@`` clone URL, and tolerates a trailing ``.git`` or slash.
    """
    text = (reference or "").strip().strip("<>").rstrip("/")
    if not text:
        raise InvalidInputError("No repository was entered.")

    # git@github.com:owner/repo.git
    if text.startswith("git@"):
        text = text.split(":", 1)[-1]

    text = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", text, flags=re.I)
    text = re.sub(r"^(?:https?://)?(?:www\.)?api\.github\.com/repos/", "", text, flags=re.I)

    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        raise InvalidInputError(
            f"'{reference}' is not a valid repository reference.",
            hints=(
                "Use the owner/repository form, e.g. python/cpython",
                "A full URL such as https://github.com/python/cpython also works",
            ),
        )

    owner, repo = parts[0], parts[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]

    if not is_valid_username(owner) or not _REPO_NAME_RE.match(repo):
        raise InvalidInputError(
            f"'{reference}' contains an invalid owner or repository name."
        )
    return owner, repo


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp into an aware ``datetime``."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_datetime(value: str | datetime | None) -> str:
    """Render a timestamp as ``YYYY-MM-DD`` or ``N/A``."""
    parsed = parse_iso_datetime(value) if isinstance(value, (str, type(None))) else value
    return parsed.strftime("%Y-%m-%d") if parsed else "N/A"


def days_since(value: str | datetime | None) -> int | None:
    """Whole days elapsed since ``value``, or None if it cannot be parsed."""
    parsed = parse_iso_datetime(value) if isinstance(value, (str, type(None))) else value
    if not parsed:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def human_number(value: int | float | None) -> str:
    """Format a number with thousands separators."""
    if value is None:
        return "N/A"
    return f"{value:,}"


def human_size(kilobytes: int | None) -> str:
    """Render a size given in KB as a human-readable string."""
    if not kilobytes:
        return "0 KB"
    size = float(kilobytes)
    for unit in ("KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024
    return f"{size:.1f} GB"


def percentage(part: float, whole: float) -> float:
    """Safe percentage calculation that never divides by zero."""
    return (part / whole * 100.0) if whole else 0.0


def truncate(text: str | None, limit: int = 80) -> str:
    """Shorten ``text`` to ``limit`` characters with an ellipsis."""
    if not text:
        return "N/A"
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


#: Partial block characters, used to render fractional bar segments smoothly.
_BLOCKS = " ▏▎▍▌▋▊▉█"

#: Sparkline levels, low to high.
_SPARKS = "▁▂▃▄▅▆▇█"


def bar(value: float, maximum: float, width: int = 24, char: str = "█") -> str:
    """Build a bar of ``width`` cells scaled against ``maximum``.

    Uses one-eighth block characters for the final cell so bars of similar
    size stay visually distinguishable instead of rounding to the same length.
    """
    if maximum <= 0 or value <= 0:
        return ""
    if char != "█":  # caller asked for a specific glyph: keep it simple
        return char * max(1, min(width, int(round(value / maximum * width))))

    exact = max(0.0, min(float(width), value / maximum * width))
    full = int(exact)
    remainder = exact - full
    partial = _BLOCKS[int(remainder * 8)] if full < width else ""
    rendered = ("█" * full) + partial
    # A tiny but non-zero value must still show something.
    return rendered if rendered.strip() else "▏"


def sparkline(values: Sequence[float]) -> str:
    """Render a compact single-line trend for a series of values."""
    numbers = [float(v) for v in values]
    if not numbers:
        return ""
    low, high = min(numbers), max(numbers)
    if high == low:
        return _SPARKS[3] * len(numbers)
    span = high - low
    return "".join(_SPARKS[int((v - low) / span * (len(_SPARKS) - 1))] for v in numbers)


# --------------------------------------------------------------------------- #
# File classification used by the repository code-structure analysis.
# --------------------------------------------------------------------------- #

_CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cc", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".kts", ".m",
    ".scala", ".sh", ".bash", ".ps1", ".pl", ".r", ".lua", ".dart", ".sql",
    ".vue", ".svelte", ".html", ".css", ".scss", ".sass",
}
_CONFIG_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".properties", ".lock", ".editorconfig",
}
_DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc", ".pdf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp"}

_TEST_MARKERS = ("test", "spec", "__tests__")

_GENERATED_MARKERS = (
    "node_modules/", "dist/", "build/", "vendor/", ".min.js", ".min.css",
    "package-lock.json", "yarn.lock", "poetry.lock", "pnpm-lock.yaml",
)


def classify_path(path: str) -> str:
    """Classify a repository path into a coarse category.

    Returns one of ``code``, ``test``, ``config``, ``docs``, ``image`` or
    ``other``. Tests take priority over the underlying file type.
    """
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""

    if ext in _CODE_EXTENSIONS and any(
        marker in lower for marker in _TEST_MARKERS
    ):
        return "test"
    if ext in _CODE_EXTENSIONS:
        return "code"
    if ext in _CONFIG_EXTENSIONS or name in {"dockerfile", "makefile", "procfile"}:
        return "config"
    if ext in _DOC_EXTENSIONS:
        return "docs"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    return "other"


def is_generated(path: str) -> bool:
    """Heuristically detect vendored or machine-generated files."""
    lower = path.lower()
    return any(marker in lower for marker in _GENERATED_MARKERS)
