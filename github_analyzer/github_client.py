"""Thin, defensive wrapper around the GitHub REST API v3.

Design goals:

* one :class:`requests.Session` (connection reuse, one place for headers);
* every failure mode mapped onto a typed :mod:`.errors` exception;
* pagination with hard ceilings so no repository can trigger runaway calls;
* an in-memory response cache so repeated analyses stay cheap.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from .config import (
    API_BASE_URL,
    MAX_COMMITS,
    MAX_CONTRIBUTORS,
    MAX_ISSUES,
    MAX_PULLS,
    MAX_REPOS,
    PER_PAGE,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .errors import (
    APIError,
    AuthError,
    NetworkError,
    NotFoundError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

JSONDict = dict[str, Any]


class GitHubClient:
    """Client for the public GitHub REST API.

    A token is optional: without one GitHub allows 60 requests per hour, with
    one it allows 5,000. The client works either way.
    """

    def __init__(self, token: str | None = None, timeout: tuple[float, float] | None = None) -> None:
        self.token = token or None
        self.timeout = timeout or REQUEST_TIMEOUT
        self.requests_made = 0
        self._cache: dict[str, Any] = {}

        self.session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers.update(headers)

    # ------------------------------------------------------------------ #
    # Low-level request handling
    # ------------------------------------------------------------------ #

    def _request(self, path: str, params: JSONDict | None = None) -> requests.Response:
        """Perform one GET request and translate errors into exceptions."""
        url = path if path.startswith("http") else f"{API_BASE_URL}{path}"
        logger.debug("GET %s params=%s", url, params)

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.Timeout as exc:
            raise NetworkError(
                "GitHub did not respond in time.",
                hints=("The connection may be slow", "Try again in a few moments"),
            ) from exc
        except requests.exceptions.SSLError as exc:
            raise NetworkError(
                "The secure connection to GitHub failed.",
                hints=("A proxy or antivirus may be intercepting HTTPS traffic",),
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise NetworkError("Could not reach api.github.com.") from exc
        except requests.exceptions.RequestException as exc:  # pragma: no cover
            raise NetworkError(f"Network request failed: {exc}") from exc

        self.requests_made += 1
        self._raise_for_status(response)
        return response

    def _raise_for_status(self, response: requests.Response) -> None:
        """Map a non-success HTTP status onto a typed exception."""
        status = response.status_code
        if status < 400:
            return

        if status == 404:
            raise NotFoundError("The requested GitHub resource does not exist.")

        if status in (401, 403, 429):
            remaining = response.headers.get("X-RateLimit-Remaining")
            if status == 429 or remaining == "0":
                raise RateLimitError(
                    f"GitHub API rate limit reached. {self.rate_limit_reset_text(response)}"
                )
            if status == 401:
                raise AuthError("GitHub rejected the supplied token (401 Unauthorized).")
            raise AuthError(
                "GitHub refused the request (403 Forbidden).",
                hints=(
                    "The resource may be private or require extra token scopes",
                    "Abuse-detection throttling can also cause this; retry shortly",
                ),
            )

        if status >= 500:
            raise APIError(
                f"GitHub returned a server error ({status}).",
                hints=("This is a problem on GitHub's side", "Check https://www.githubstatus.com"),
            )

        raise APIError(f"Unexpected GitHub API response ({status}).")

    @staticmethod
    def rate_limit_reset_text(response: requests.Response) -> str:
        """Human-readable description of when the rate limit resets."""
        reset = response.headers.get("X-RateLimit-Reset")
        if not reset or not reset.isdigit():
            return "Try again later."
        reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc).astimezone()
        minutes = max(0, int((reset_at - datetime.now().astimezone()).total_seconds() // 60))
        return f"The limit resets at {reset_at:%H:%M} (about {minutes} min)."

    # ------------------------------------------------------------------ #
    # JSON helpers
    # ------------------------------------------------------------------ #

    def get_json(self, path: str, params: JSONDict | None = None, cache: bool = True) -> Any:
        """GET a single JSON document, optionally served from cache."""
        key = f"{path}?{sorted((params or {}).items())}"
        if cache and key in self._cache:
            return self._cache[key]

        data = self._request(path, params).json()
        if cache:
            self._cache[key] = data
        return data

    def get_paginated(
        self,
        path: str,
        limit: int,
        params: JSONDict | None = None,
    ) -> list[JSONDict]:
        """Collect up to ``limit`` items across paginated endpoints."""
        key = f"paged:{path}?{sorted((params or {}).items())}:{limit}"
        if key in self._cache:
            return self._cache[key]

        items: list[JSONDict] = []
        page = 1
        while len(items) < limit:
            page_params = dict(params or {})
            page_params.update({"per_page": min(PER_PAGE, limit - len(items)), "page": page})
            batch = self._request(path, page_params).json()
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < page_params["per_page"]:
                break
            page += 1
            if page > 50:  # absolute safety valve
                break

        result = items[:limit]
        self._cache[key] = result
        return result

    # ------------------------------------------------------------------ #
    # Users
    # ------------------------------------------------------------------ #

    def get_user(self, username: str) -> JSONDict:
        """Fetch a user (or organisation) profile."""
        try:
            return self.get_json(f"/users/{username}")
        except NotFoundError as exc:
            raise NotFoundError(
                f"GitHub user '{username}' was not found.",
                hints=(
                    "Check the spelling of the username",
                    "The account may have been renamed or deleted",
                ),
            ) from exc

    def get_user_repos(self, username: str, limit: int = MAX_REPOS) -> list[JSONDict]:
        """Fetch a user's public repositories, newest activity first."""
        return self.get_paginated(
            f"/users/{username}/repos",
            limit=limit,
            params={"sort": "updated", "type": "owner"},
        )

    # ------------------------------------------------------------------ #
    # Repositories
    # ------------------------------------------------------------------ #

    def get_repo(self, owner: str, repo: str) -> JSONDict:
        """Fetch core repository metadata."""
        try:
            return self.get_json(f"/repos/{owner}/{repo}")
        except NotFoundError as exc:
            raise NotFoundError(
                f"Repository '{owner}/{repo}' was not found.",
                hints=(
                    "Check the owner and repository spelling",
                    "Private repositories require a GITHUB_TOKEN with 'repo' scope",
                    "The repository may have been renamed or deleted",
                ),
            ) from exc

    def get_languages(self, owner: str, repo: str) -> dict[str, int]:
        """Byte counts per language."""
        data = self.get_json(f"/repos/{owner}/{repo}/languages")
        return data if isinstance(data, dict) else {}

    def get_contributors(self, owner: str, repo: str) -> list[JSONDict]:
        """Contributors ordered by number of commits."""
        return self._safe_list(f"/repos/{owner}/{repo}/contributors", MAX_CONTRIBUTORS)

    def get_commits(self, owner: str, repo: str) -> list[JSONDict]:
        """Most recent commits on the default branch."""
        return self._safe_list(f"/repos/{owner}/{repo}/commits", MAX_COMMITS)

    def get_issues_and_pulls(self, owner: str, repo: str) -> list[JSONDict]:
        """Recent issues *and* pull requests (GitHub returns both here)."""
        return self._safe_list(
            f"/repos/{owner}/{repo}/issues",
            MAX_ISSUES,
            params={"state": "all", "sort": "created", "direction": "desc"},
        )

    def get_pulls(self, owner: str, repo: str) -> list[JSONDict]:
        """Recent pull requests in any state."""
        return self._safe_list(
            f"/repos/{owner}/{repo}/pulls",
            MAX_PULLS,
            params={"state": "all", "sort": "created", "direction": "desc"},
        )

    def get_releases(self, owner: str, repo: str) -> list[JSONDict]:
        """Published releases, newest first."""
        return self._safe_list(f"/repos/{owner}/{repo}/releases", 30)

    def get_tree(self, owner: str, repo: str, branch: str) -> tuple[list[JSONDict], bool]:
        """Fetch the recursive file tree for ``branch``.

        Returns ``(entries, truncated)``. This is a single API call and avoids
        cloning the repository, but GitHub truncates very large trees.
        """
        try:
            data = self.get_json(
                f"/repos/{owner}/{repo}/git/trees/{branch}", params={"recursive": "1"}
            )
        except (NotFoundError, APIError):
            return [], False
        entries = data.get("tree", []) if isinstance(data, dict) else []
        return entries, bool(isinstance(data, dict) and data.get("truncated"))

    # ------------------------------------------------------------------ #
    # Rate limit
    # ------------------------------------------------------------------ #

    def get_rate_limit(self) -> JSONDict:
        """Return ``{limit, remaining, reset}`` for the core API."""
        try:
            data = self.get_json("/rate_limit", cache=False)
            core = data.get("resources", {}).get("core", {})
            return {
                "limit": core.get("limit", 0),
                "remaining": core.get("remaining", 0),
                "reset": core.get("reset", 0),
            }
        except Exception:  # noqa: BLE001 - never block analysis on this
            return {"limit": 0, "remaining": 0, "reset": 0}

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _safe_list(
        self, path: str, limit: int, params: JSONDict | None = None
    ) -> list[JSONDict]:
        """Paginated GET that degrades to an empty list for optional data.

        Empty repositories return 409, and some endpoints 404 for forks or
        disabled features. Those are normal conditions, not failures. Rate
        limit and auth problems are still propagated.
        """
        try:
            return self.get_paginated(path, limit=limit, params=params)
        except (RateLimitError, AuthError):
            raise
        except (NotFoundError, APIError, NetworkError) as exc:
            logger.debug("Optional endpoint %s unavailable: %s", path, exc)
            return []

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_exc: Iterable[Any]) -> None:
        self.close()
