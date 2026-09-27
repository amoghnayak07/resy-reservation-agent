"""`prepare_booking` tool (stage 9): fetches Resy's booking details for a slot the user chose
and stores a server-side pending booking. Makes no reservation.

`/3/details` with commit=1 issues the book token and may hold the table, so this tool and the
confirm flow are its only callers (CLAUDE.md hard rule 1). Only free reservations go ahead.
Conversation and session come from the run config, never from tool arguments."""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.booking_summary import booking_summary
from app.agent.tools.common import dump
from app.agent.tools.slot_ids import SlotIdMap, SlotRef, slot_ids
from app.db.models import PendingBooking
from app.db.pending_bookings import PendingBookingRepository
from app.resy.client import ResyClient
from app.resy.errors import ResyError
from app.resy.models import BookingDetails

logger = logging.getLogger(__name__)

TOOL_NAME = "prepare_booking"
DECISION_WINDOW = timedelta(minutes=10)

DESCRIPTION = """Prepare a booking for one slot the user chose (or the single exact match of \
a fully specified request): gets Resy's cancellation policy and fees and creates a pending \
booking. It does NOT make a reservation. Free reservations only; others are refused with a \
Resy link. Pass the `slot_id` from search_availability."""

SLOT_EXPIRED = "That time is no longer held, please search again."
NOT_HELD = "Resy couldn't hold that time; it may have just been taken. Please search again."


class PrepareBookingArgs(BaseModel):
    slot_id: str = Field(description="slot_id from search_availability, e.g. 's3'.")


def make_prepare_booking_tool(
    resy: ResyClient,
    bookings: PendingBookingRepository,
    *,
    slot_map: SlotIdMap = slot_ids,
    now_fn: Callable[[ZoneInfo], datetime] | None = None,
) -> BaseTool:
    clock = now_fn or (lambda tz: datetime.now(tz))

    async def prepare_booking(slot_id: str, config: RunnableConfig) -> str:
        configurable = config.get("configurable", {})
        conversation_id = str(configurable["thread_id"])
        session_id = str(configurable["session_id"])
        tz = ZoneInfo(configurable["timezone"])

        ref = slot_map.get(conversation_id, slot_id)
        if ref is None:
            return dump({"error": "slot_expired", "message": SLOT_EXPIRED})
        if not ref.bookable:
            return dump(_refusal("not_bookable", "This time can't be booked here.", ref))
        if ref.requires_payment:
            return dump(_refusal("requires_payment", "This reservation requires payment.", ref))

        try:
            raw = await resy.get_details(
                ref.config_token, ref.start.date(), ref.party_size, commit=True
            )
        except ResyError as exc:
            logger.warning("resy_error_in_tool", extra={"endpoint": TOOL_NAME})
            return dump(
                {"error": "not_held", "error_type": type(exc).__name__, "message": NOT_HELD}
            )

        details = BookingDetails.from_response(raw)
        if not details.is_free:
            return dump(_refusal("requires_payment", _payment_reason(details), ref))
        if not details.book_token:
            logger.warning("resy_details_without_book_token", extra={"endpoint": TOOL_NAME})
            return dump({"error": "not_held", "message": NOT_HELD})

        now = clock(tz)
        booking = await bookings.create(
            PendingBooking(
                id=uuid.uuid4(),
                conversation_id=uuid.UUID(conversation_id),
                session_id=session_id,
                venue_id=ref.venue_id,
                venue_name=ref.venue_name,
                address=details.address,
                slot_start=ref.start,
                party_size=ref.party_size,
                seating_type=ref.seating_type,
                book_token=details.book_token,
                config_token=ref.config_token,
                book_token_expires=details.book_token_expires,
                cancellation_policy=details.policy_text,
                refund_cutoff=details.refund_cutoff,
                change_cutoff=details.change_cutoff,
                payment_type=details.payment_type,
                status="pending",
                expires_at=now + DECISION_WINDOW,
            )
        )
        return dump(summarize(booking, tz))

    return StructuredTool.from_function(
        coroutine=prepare_booking,
        name=TOOL_NAME,
        description=DESCRIPTION,
        args_schema=PrepareBookingArgs,
    )


def summarize(booking: PendingBooking, tz: ZoneInfo) -> dict[str, Any]:
    return {
        "pending_booking_id": str(booking.id),
        "status": "pending (not booked yet)",
    } | booking_summary(booking, tz)


def _refusal(code: str, reason: str, ref: SlotRef) -> dict[str, Any]:
    out: dict[str, Any] = {
        "error": code,
        "message": f"{reason} This agent only books free reservations; "
        "the user can book it on Resy instead.",
        "restaurant": ref.venue_name,
    }
    if ref.venue_url:
        out["url"] = ref.venue_url
    return out


def _payment_reason(details: BookingDetails) -> str:
    if details.total:
        return f"This reservation requires a payment of ${details.total:.2f}."
    if details.cancellation_fee is not None:
        return "This reservation has a cancellation fee and needs a card on file."
    return "This reservation requires a card or payment."
