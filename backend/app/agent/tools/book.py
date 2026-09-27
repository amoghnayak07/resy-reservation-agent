"""`book` tool (stage 10): books a pending reservation, but only after the graph pauses with
`interrupt()`, the user confirms in the UI with the passcode, and the confirm endpoint moved the
row to `confirming` (CLAUDE.md hard rule 3). Approval comes from the resume value and DB state,
never from the LLM or tool arguments.

On resume LangGraph re-runs this tool from the top, so everything before `interrupt()` is
read-only (expire_if_due is idempotent). `/3/book` is never retried: a failure after the request
may have been sent is recorded as `unknown`."""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.agent.tools.booking_summary import booking_summary
from app.agent.tools.common import dump
from app.db.models import PendingBooking
from app.db.pending_bookings import PendingBookingRepository
from app.resy.client import ResyClient
from app.resy.errors import ResyError, ResySchemaError, ResyUpstreamError
from app.resy.models import BookingDetails, BookingResult

logger = logging.getLogger(__name__)

TOOL_NAME = "book"
TOKEN_REFRESH_MARGIN = timedelta(seconds=30)

DESCRIPTION = """Book a pending reservation. Call it right after a successful prepare_booking, \
in the same turn, with its `pending_booking_id`. It shows the user a confirmation card and waits: \
nothing is booked unless the user confirms there. Don't ask "shall I book?" first; the card is \
the confirmation step."""

DECLINED = {"status": "declined", "message": "Okay, not booked."}
NOT_FOUND = {"error": "not_found", "message": "That booking wasn't found. Please search again."}
EXPIRED = {
    "error": "expired",
    "message": "That hold expired before it was confirmed. Nothing was booked; search again.",
}
NO_LONGER_AVAILABLE = "That time is no longer available, so nothing was booked."
FAILED = "Resy didn't accept the booking, so nothing was booked."
UNKNOWN = (
    "Resy didn't respond in time, so it's unclear whether the booking went through. "
    "Check the Resy app before trying again."
)


class BookArgs(BaseModel):
    pending_booking_id: str = Field(description="pending_booking_id from prepare_booking.")


def make_book_tool(
    resy: ResyClient,
    bookings: PendingBookingRepository,
    *,
    now_fn: Callable[[ZoneInfo], datetime] | None = None,
) -> BaseTool:
    clock = now_fn or (lambda tz: datetime.now(tz))

    async def book(pending_booking_id: str, config: RunnableConfig) -> str:
        configurable = config.get("configurable", {})
        tz = ZoneInfo(configurable["timezone"])
        try:
            booking_id = uuid.UUID(pending_booking_id)
        except ValueError:
            return dump(NOT_FOUND)
        owner = {
            "session_id": str(configurable["session_id"]),
            "conversation_id": uuid.UUID(str(configurable["thread_id"])),
        }

        # --- before interrupt(): re-runs on resume, read-only ---------------------------------
        row = await bookings.get_owned(booking_id, now=clock(tz), **owner)
        if row is None:
            return dump(NOT_FOUND)
        if row.status == "expired":
            return dump(EXPIRED)
        if row.status == "declined":
            return dump(DECLINED)
        if row.status not in ("pending", "confirming"):
            return dump({"error": "already_handled", "message": f"That booking is {row.status}."})

        decision = interrupt({"pending_booking_id": str(row.id), "summary": card_summary(row, tz)})

        # --- after resume ------------------------------------------------------------------
        approved = isinstance(decision, dict) and decision.get("approved") is True
        row = await bookings.get_owned(booking_id, now=clock(tz), **owner)
        if row is None:
            return dump(NOT_FOUND)
        if not approved or row.status == "declined":
            return dump(DECLINED)
        if row.status != "confirming":  # approval without the confirm endpoint's DB transition
            logger.warning("book_resume_without_confirming", extra={"status": row.status})
            return dump(
                {"error": "not_confirmed", "message": "It wasn't confirmed, so nothing was booked."}
            )
        return dump(await _book(resy, bookings, row, tz, clock(tz)))

    return StructuredTool.from_function(
        coroutine=book, name=TOOL_NAME, description=DESCRIPTION, args_schema=BookArgs
    )


def card_summary(row: PendingBooking, tz: ZoneInfo) -> dict[str, Any]:
    """What the confirmation card shows; `expires_at` (ISO) drives its countdown."""
    return booking_summary(row, tz) | {"expires_at": row.expires_at.isoformat()}


async def _book(
    resy: ResyClient,
    bookings: PendingBookingRepository,
    row: PendingBooking,
    tz: ZoneInfo,
    now: datetime,
) -> dict[str, Any]:
    token = row.book_token
    if row.book_token_expires is not None and row.book_token_expires <= now + TOKEN_REFRESH_MARGIN:
        refreshed = await _refresh_token(resy, bookings, row, tz)
        if refreshed is None:
            return await _finish(bookings, row, "failed", "slot_unavailable", NO_LONGER_AVAILABLE)
        token = refreshed

    try:
        result = BookingResult.from_response(await resy.book(token, allow_write=True))
    except ResyUpstreamError as exc:
        if exc.request_sent:
            return await _finish(bookings, row, "unknown", "no_response", UNKNOWN)
        return await _finish(bookings, row, "failed", type(exc).__name__, FAILED)
    except ResySchemaError:  # Resy answered 2xx but the body didn't parse: it may have booked
        return await _finish(bookings, row, "unknown", "unreadable_response", UNKNOWN)
    except ResyError as exc:
        return await _finish(bookings, row, "failed", type(exc).__name__, FAILED)

    await bookings.update_fields(
        row.id,
        status="confirmed",
        reservation_id=result.reservation_id,
        resy_token=result.resy_token,
    )
    logger.info("booking_outcome", extra={"status": "confirmed"})
    summary = booking_summary(row, tz)
    summary.pop("hold_expires", None)
    return {"status": "confirmed", "message": "Booked on Resy."} | summary


async def _refresh_token(
    resy: ResyClient, bookings: PendingBookingRepository, row: PendingBooking, tz: ZoneInfo
) -> str | None:
    """Re-issues an expired book token (commit=1), re-checking that it's still free."""
    if not row.config_token:
        return None
    try:
        raw = await resy.get_details(
            row.config_token, row.slot_start.astimezone(tz).date(), row.party_size, commit=True
        )
    except ResyError:
        return None
    details = BookingDetails.from_response(raw)
    if not details.is_free or not details.book_token:
        return None
    await bookings.update_fields(
        row.id, book_token=details.book_token, book_token_expires=details.book_token_expires
    )
    return details.book_token


async def _finish(
    bookings: PendingBookingRepository,
    row: PendingBooking,
    status: str,
    error_code: str,
    message: str,
) -> dict[str, Any]:
    await bookings.update_fields(row.id, status=status, error_code=error_code)
    logger.warning("booking_outcome", extra={"status": status, "error_code": error_code})
    return {"status": status, "error": f"booking_{status}", "message": message}
