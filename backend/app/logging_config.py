import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

_EXTRA_FIELDS = ("request_id", "session_id_hash", "path", "status", "duration_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


request_logger = logging.getLogger("app.request")


def _hash_session_id(session_id: str | None) -> str | None:
    """Never log the raw guest session ID -- only a short, one-way hash for
    correlating requests (CLAUDE.md: logs never include message content)."""
    if session_id is None:
        return None
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Request logging middleware: request ID, hashed session ID, path, status,
    latency. No message content (CLAUDE.md -> Backend steps, step 4)."""
    request_id = str(uuid.uuid4())
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - start) * 1000)
    request_logger.info(
        "request",
        extra={
            "request_id": request_id,
            "session_id_hash": _hash_session_id(request.headers.get("X-Session-Id")),
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    response.headers["X-Request-Id"] = request_id
    return response
