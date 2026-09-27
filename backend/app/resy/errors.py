"""Typed Resy errors, tagged on Langfuse traces. Messages never include tokens, headers,
or response bodies."""


class ResyError(Exception):
    """Base class. Also raised directly for unexpected 4xx statuses."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ResyAuthError(ResyError):
    """401, or credentials missing from the environment."""


class ResyBlockedError(ResyError):
    """403 or a bot-challenge page."""


class ResyRateLimitedError(ResyError):
    """429."""


class ResyUpstreamError(ResyError):
    """5xx, timeouts, and connection failures. The only error reads retry on.

    `request_sent` is False only when the request never left (connect error/timeout). A write
    that fails with request_sent=True may have gone through: its outcome is unknown."""

    def __init__(
        self, message: str, *, status_code: int | None = None, request_sent: bool = True
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.request_sent = request_sent


class ResyNotFoundError(ResyError):
    """404."""


class ResySchemaError(ResyError):
    """Response didn't match the expected shape. Carries field paths, never values."""

    def __init__(self, message: str, *, field_paths: list[str] | None = None) -> None:
        super().__init__(message)
        self.field_paths = field_paths or []


class ResyWritesDisabledError(ResyError):
    """A write was attempted without RESY_WRITES_ENABLED=true and allow_write=True."""
