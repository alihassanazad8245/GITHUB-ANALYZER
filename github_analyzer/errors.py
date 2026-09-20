"""Exception types used across the analyzer.

Every error raised by the client/analyzer layers derives from ``AnalyzerError``
so the CLI can present a friendly message instead of a raw traceback.
"""

from __future__ import annotations


class AnalyzerError(Exception):
    """Base class for all expected, user-facing errors."""

    #: Short bullet hints shown under the error message.
    hints: tuple[str, ...] = ()

    def __init__(self, message: str, hints: tuple[str, ...] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hints is not None:
            self.hints = hints


class ConfigError(AnalyzerError):
    """Raised when configuration or input is invalid."""


class InvalidInputError(AnalyzerError):
    """Raised when a username / repository reference cannot be parsed."""


class NetworkError(AnalyzerError):
    """Raised when GitHub cannot be reached at all."""

    hints = (
        "Check that your machine is online",
        "Check proxy / firewall settings",
        "Try again in a few seconds",
    )


class NotFoundError(AnalyzerError):
    """Raised when a user or repository does not exist (HTTP 404)."""

    hints = (
        "Verify the spelling of the owner and repository name",
        "The resource may be private, renamed or deleted",
        "Private resources need a GITHUB_TOKEN with 'repo' scope",
    )


class AuthError(AnalyzerError):
    """Raised for HTTP 401 / 403 that are not rate-limit related."""

    hints = (
        "Your GITHUB_TOKEN may be invalid, expired or revoked",
        "Regenerate the token at https://github.com/settings/tokens",
        "Confirm the token has the scopes the request needs",
    )


class RateLimitError(AnalyzerError):
    """Raised when the GitHub API rate limit is exhausted."""

    hints = (
        "Unauthenticated requests are limited to 60 per hour",
        "Set a GITHUB_TOKEN to raise the limit to 5,000 per hour",
        "Or simply wait until the limit resets",
    )


class APIError(AnalyzerError):
    """Raised for any other non-success HTTP response."""
