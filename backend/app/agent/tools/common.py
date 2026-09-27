"""Output helpers shared by the agent tools: compact JSON and friendly Resy error results."""

import json
import logging
from typing import Any

from app.resy.errors import ResyError, ResyNotFoundError

logger = logging.getLogger(__name__)


def dump(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


def resy_error_result(exc: ResyError, tool: str) -> dict[str, Any]:
    """A result the model can relay instead of a failed turn. Never includes Resy's message
    or response body."""
    logger.warning("resy_error_in_tool", extra={"endpoint": tool, "status": exc.status_code})
    if isinstance(exc, ResyNotFoundError):
        return {"error": "venue_not_found", "message": "Resy doesn't have that venue."}
    return {
        "error": "resy_unavailable",
        "error_type": type(exc).__name__,
        "message": "Resy is temporarily unavailable. Try again in a minute.",
    }


def truncate(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
