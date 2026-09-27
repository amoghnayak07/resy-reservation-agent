"""Booking confirm/decline (stage 10). The only way a paused `book` call resumes. Order on
confirm: rate limit → session ownership → passcode → atomic `pending → confirming` → resume.
The passcode never reaches the graph, logs, or traces."""

import hmac
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.api.deps import get_graph, get_pending_booking_repository, get_session_id, get_spend_guard
from app.api.streaming import SSE_HEADERS, GraphRun, pending_confirmations, sse_event
from app.config import settings
from app.db.models import PendingBooking
from app.db.pending_bookings import PendingBookingRepository
from app.errors import ApiError
from app.guards.rate_limit import enforce_confirm_rate_limits
from app.guards.spend import SpendGuard
from app.observability import langfuse as langfuse_module
from app.schemas.bookings import BookingActionRequest, ConfirmBookingRequest

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


def _passcode_ok(passcode: str) -> bool:
    expected = settings.demo_booking_passcode.get_secret_value()
    if not expected:
        return False
    return hmac.compare_digest(passcode.encode(), expected.encode())


async def _owned(
    bookings: PendingBookingRepository, booking_id: uuid.UUID, session_id: str
) -> PendingBooking:
    row = await bookings.get_for_session(booking_id, session_id=session_id, now=datetime.now(UTC))
    if row is None:
        raise ApiError(404, "not_found", "Booking not found.")
    return row


def _thread_config(row: PendingBooking) -> RunnableConfig:
    return {"configurable": {"thread_id": str(row.conversation_id)}}


async def _require_paused(graph: CompiledStateGraph, row: PendingBooking) -> None:
    """The graph must be waiting on this exact booking; otherwise there's nothing to resume."""
    waiting = await pending_confirmations(graph, _thread_config(row))
    if not any(c.get("pending_booking_id") == str(row.id) for c in waiting):
        raise ApiError(409, "already_handled", "This booking is no longer awaiting confirmation.")


async def _transition(
    bookings: PendingBookingRepository, row: PendingBooking, session_id: str, to_status: str
) -> None:
    now = datetime.now(UTC)
    moved = await bookings.transition(
        row.id, session_id=session_id, from_status="pending", to_status=to_status, now=now
    )
    if moved is not None:
        return
    current = await bookings.get_for_session(row.id, session_id=session_id, now=now)
    if current is None or current.status == "expired":
        raise ApiError(410, "expired", "This booking hold expired. Search again.")
    raise ApiError(409, "already_handled", "This booking was already handled.")


def _resume_stream(
    graph: CompiledStateGraph,
    bookings: PendingBookingRepository,
    spend: SpendGuard,
    row: PendingBooking,
    session_id: str,
    timezone: str,
    *,
    approved: bool,
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        async with langfuse_module.trace_turn(
            conversation_id=str(row.conversation_id),
            session_id=session_id,
            location_used=False,
            tags=["booking_attempt" if approved else "booking_declined"],
        ) as (handler, trace_id):
            yield sse_event(
                "meta", {"conversation_id": str(row.conversation_id), "trace_id": trace_id}
            )
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": str(row.conversation_id),
                    "session_id": session_id,
                    "timezone": timezone,
                    "location_available": False,
                    "location": None,
                },
                "callbacks": [handler],
                "recursion_limit": 12,
            }
            run = GraphRun(graph, Command(resume={"approved": approved}), config, spend)
            async for chunk in run.events():
                yield chunk
            final = await bookings.get_for_session(
                row.id, session_id=session_id, now=datetime.now(UTC)
            )
            langfuse_module.record_booking_outcome(trace_id, final.status if final else "missing")
        yield sse_event("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/{booking_id}/confirm", dependencies=[Depends(enforce_confirm_rate_limits)])
async def confirm_booking(
    booking_id: uuid.UUID,
    body: ConfirmBookingRequest,
    session_id: str = Depends(get_session_id),
    bookings: PendingBookingRepository = Depends(get_pending_booking_repository),
    graph: CompiledStateGraph = Depends(get_graph),
    spend: SpendGuard = Depends(get_spend_guard),
) -> StreamingResponse:
    row = await _owned(bookings, booking_id, session_id)
    if not _passcode_ok(body.passcode):
        raise ApiError(403, "invalid_passcode", "That passcode isn't right.")
    await spend.check_cap()
    await _require_paused(graph, row)
    await _transition(bookings, row, session_id, "confirming")
    return _resume_stream(graph, bookings, spend, row, session_id, body.timezone, approved=True)


@router.post("/{booking_id}/decline")
async def decline_booking(
    booking_id: uuid.UUID,
    body: BookingActionRequest,
    session_id: str = Depends(get_session_id),
    bookings: PendingBookingRepository = Depends(get_pending_booking_repository),
    graph: CompiledStateGraph = Depends(get_graph),
    spend: SpendGuard = Depends(get_spend_guard),
) -> StreamingResponse:
    row = await _owned(bookings, booking_id, session_id)
    await spend.check_cap()
    await _require_paused(graph, row)
    await _transition(bookings, row, session_id, "declined")
    return _resume_stream(graph, bookings, spend, row, session_id, body.timezone, approved=False)
