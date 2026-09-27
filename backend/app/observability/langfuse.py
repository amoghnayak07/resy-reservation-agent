"""Langfuse v4 wiring: one client for the process, one trace per chat turn.

v4 replaced v3's `update_current_trace()` and the LangChain handler's `update_trace`
argument with `propagate_attributes()`; don't follow v3 examples.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langfuse import Langfuse, propagate_attributes
from langfuse.langchain import CallbackHandler

from app.config import settings

_client: Langfuse | None = None


def init_langfuse() -> Langfuse:
    """Create the process-wide Langfuse client. Call once, from the FastAPI lifespan."""
    global _client
    _client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        environment=settings.app_env,
    )
    return _client


def shutdown_langfuse() -> None:
    """Flush any pending spans/events. Call once, from the FastAPI lifespan shutdown."""
    if _client is not None:
        _client.shutdown()


@asynccontextmanager
async def trace_turn(
    conversation_id: str,
    session_id: str,
    location_used: bool,
    tags: list[str] | None = None,
) -> AsyncIterator[tuple[CallbackHandler, str]]:
    """One trace per chat turn (or booking confirm/decline): session_id = conversation,
    user_id = guest session.

    Never pass coordinates here -- only the location_used flag, per CLAUDE.md.
    """
    trace_id = Langfuse.create_trace_id()
    handler = CallbackHandler(trace_context={"trace_id": trace_id})
    with propagate_attributes(
        session_id=conversation_id,
        user_id=session_id,
        metadata={"location_used": location_used},
        tags=tags,
    ):
        yield handler, trace_id


def record_booking_outcome(trace_id: str, outcome: str) -> None:
    """Adds the booking status (confirmed/failed/unknown/declined/...) to a trace. Status
    only: never tokens, the passcode, or reservation details."""
    if _client is not None:
        _client.create_event(
            trace_context={"trace_id": trace_id},
            name="booking_outcome",
            metadata={"outcome": outcome},
        )
