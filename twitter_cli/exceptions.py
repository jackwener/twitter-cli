"""Custom exceptions for twitter-cli.

Provides a structured exception hierarchy for categorized error handling:
- Authentication failures
- API errors (rate-limit, not-found, forbidden)
- Network errors
- Query ID resolution failures

Each exception carries an `error_code` attribute for structured output.
"""

from __future__ import annotations


class TwitterError(RuntimeError):
    """Base exception for twitter-cli errors."""

    error_code: str = "api_error"


class AuthenticationError(TwitterError):
    """Raised when cookies are missing, expired, or invalid."""

    error_code = "not_authenticated"


class RateLimitError(TwitterError):
    """Raised when Twitter rate limits the request (HTTP 429)."""

    error_code = "rate_limited"


class NotFoundError(TwitterError):
    """Raised when a user or tweet is not found."""

    error_code = "not_found"


class NetworkError(TwitterError):
    """Raised when upstream network requests fail."""

    error_code = "network_error"


class QueryIdError(TwitterError):
    """Raised when a GraphQL queryId cannot be resolved."""

    error_code = "query_id_error"


class MediaUploadError(TwitterError):
    """Raised when media upload fails (file not found, too large, unsupported format, API error)."""

    error_code = "media_upload_error"


class InvalidInputError(TwitterError):
    """Raised when user input is invalid (bad tweet ID, invalid options, etc.)."""

    error_code = "invalid_input"


class UnsupportedFeatureError(TwitterError):
    """Raised when the selected auth backend does not support a command yet."""

    error_code = "unsupported_operation"


class TwitterAPIError(TwitterError):
    """Raised on non-OK Twitter API responses with HTTP status + message."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        # Derive error_code from HTTP status
        if status_code in (401, 403):
            self.error_code = "not_authenticated"
        elif status_code == 429:
            self.error_code = "rate_limited"
        elif status_code == 404:
            self.error_code = "not_found"
        else:
            self.error_code = "api_error"
        super().__init__("Twitter API error (HTTP %d): %s" % (status_code, message))
