"""The booking summary shared by prepare_booking's output and the book tool's confirmation card.
Built only from the pending-booking row; never includes tokens."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db.models import PendingBooking


def booking_summary(booking: PendingBooking, tz: ZoneInfo) -> dict[str, Any]:
    start = booking.slot_start.astimezone(tz)
    out: dict[str, Any] = {
        "restaurant": booking.venue_name,
        "address": booking.address,
        "date": start.date().isoformat(),
        "weekday": start.strftime("%A"),
        "time": start.strftime("%H:%M"),
        "party_size": booking.party_size,
        "seating": booking.seating_type,
        "cost": "free",
        "cancellation_policy": booking.cancellation_policy,
        "free_cancellation_until": local_label(booking.refund_cutoff, tz),
        "changes_allowed_until": local_label(booking.change_cutoff, tz),
        "hold_expires": local_label(booking.expires_at, tz),
    }
    return {k: v for k, v in out.items() if v is not None}


def local_label(value: datetime | None, tz: ZoneInfo) -> str | None:
    """e.g. "12:00 PM Oct 22" in the user's timezone (portable: no %-I on Windows)."""
    if value is None:
        return None
    local = value.astimezone(tz)
    return f"{local.strftime('%I:%M %p').lstrip('0')} {local.strftime('%b')} {local.day}"
