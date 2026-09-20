"""Centralised configuration.

The token is read from (in priority order):

1. an explicit ``--token`` command-line argument;
2. the ``GITHUB_TOKEN`` environment variable;
3. a ``GITHUB_TOKEN`` entry in a local ``.env`` file.

Tokens are never written to disk, logs or generated reports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

#: Root of the project (the folder that holds ``main.py``).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Default directory for generated reports and charts.
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"

API_BASE_URL = "https://api.github.com"
USER_AGENT = f"github-analyzer/{__version__}"

#: Network timeout (connect, read) in seconds.
REQUEST_TIMEOUT: tuple[float, float] = (5.0, 20.0)

#: Hard ceilings so a huge repository can never trigger hundreds of calls.
MAX_REPOS = 300
MAX_COMMITS = 300
MAX_CONTRIBUTORS = 100
MAX_ISSUES = 200
MAX_PULLS = 200
PER_PAGE = 100

TOKEN_ENV_VAR = "GITHUB_TOKEN"


def _load_dotenv_token(env_path: Path) -> str | None:
    """Return ``GITHUB_TOKEN`` from a ``.env`` file, if present.

    ``python-dotenv`` is used when available; otherwise a tiny fallback parser
    handles the simple ``KEY=value`` form so the tool never hard-depends on it.
    """
    if not env_path.is_file():
        return None

    try:  # pragma: no cover - trivial import branch
        from dotenv import dotenv_values

        return dotenv_values(env_path).get(TOKEN_ENV_VAR) or None
    except ImportError:
        pass

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == TOKEN_ENV_VAR:
                return value.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def resolve_token(explicit: str | None = None) -> str | None:
    """Resolve the GitHub token from CLI argument, environment or ``.env``."""
    if explicit and explicit.strip():
        return explicit.strip()

    env_token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if env_token:
        return env_token

    return _load_dotenv_token(PROJECT_ROOT / ".env")


@dataclass(slots=True)
class Settings:
    """Runtime settings for a single analyzer session."""

    token: str | None = None
    verbose: bool = False
    no_color: bool = False
    report_dir: Path = field(default_factory=lambda: DEFAULT_REPORT_DIR)

    @property
    def authenticated(self) -> bool:
        """True when a token was supplied (raises the API rate limit)."""
        return bool(self.token)
