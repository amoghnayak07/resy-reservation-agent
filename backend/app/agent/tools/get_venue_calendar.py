"""`get_venue_calendar` tool (stage 7): which dates have openings at one venue, from
`GET /4/venue/calendar`. Day-level only; times come from search_availability with the
venue_id. Dates after `last_calendar_day` are not released yet, which is never "fully booked"
(CLAUDE.md rule 12)."""

import datetime as dt
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.common import dump, resy_error_result
from app.resy.client import ResyClient
from app.resy.errors import ResyError
from app.resy.models import Availability, VenueCalendar

TOOL_NAME = "get_venue_calendar"
WINDOW_DAYS = 90  # Resy caps the response at last_calendar_day anyway
MAX_LISTED_DATES = 14

DESCRIPTION = """Which dates have open reservations at one restaurant, for a party size. \
Day-level only: to get times on a chosen date, call search_availability with the venue_id. \
`reservations_open_through` is the last released date; later dates are not released yet \
(never say they're fully booked). `sold_out_dates` are released but fully booked."""


class GetVenueCalendarArgs(BaseModel):
    venue_id: int = Field(description="Resy venue ID from earlier results.")
    party_size: int = Field(ge=1, le=20, description="Number of people.")
    start_date: dt.date | None = Field(
        default=None, description="First date to check, YYYY-MM-DD (default today)."
    )


def make_get_venue_calendar_tool(
    resy: ResyClient, *, now_fn: Callable[[ZoneInfo], datetime] | None = None
) -> BaseTool:
    clock = now_fn or (lambda tz: datetime.now(tz))

    async def get_venue_calendar(
        venue_id: int, party_size: int, config: RunnableConfig, start_date: dt.date | None = None
    ) -> str:
        tz = ZoneInfo(config.get("configurable", {})["timezone"])
        today = clock(tz).date()
        start = max(start_date or today, today)  # never look back
        try:
            raw = await resy.get_calendar(
                venue_id, party_size, start, start + timedelta(days=WINDOW_DAYS)
            )
        except ResyError as exc:
            return dump(resy_error_result(exc, TOOL_NAME))
        calendar = VenueCalendar.from_response(raw, venue_id)
        return dump(summarize(calendar, party_size, start))

    return StructuredTool.from_function(
        coroutine=get_venue_calendar,
        name=TOOL_NAME,
        description=DESCRIPTION,
        args_schema=GetVenueCalendarArgs,
    )


def summarize(calendar: VenueCalendar, party_size: int, start: dt.date) -> dict[str, Any]:
    open_through = calendar.last_calendar_day
    result: dict[str, Any] = {
        "venue_id": calendar.venue_id,
        "party_size": party_size,
        "from": start.isoformat(),
        "reservations_open_through": open_through.isoformat(),
    }
    if start > open_through:
        return result | {
            "not_released_yet": True,
            "note": f"Reservations are released only through {open_through.isoformat()}; "
            "later dates are not released yet (not fully booked).",
        }

    days = [d for d in calendar.days if start <= d.date <= open_through]
    available = [d.date.isoformat() for d in days if d.reservation is Availability.AVAILABLE]
    sold_out = [d.date.isoformat() for d in days if d.reservation is Availability.SOLD_OUT]
    walk_in_days = sum(1 for d in days if d.walk_in is Availability.AVAILABLE)
    return result | {
        "available_dates": available[:MAX_LISTED_DATES],
        "available_count": len(available),
        "sold_out_dates": sold_out[:MAX_LISTED_DATES],
        "sold_out_count": len(sold_out),
        "unavailable_count": len(days) - len(available) - len(sold_out),
        "walk_ins": f"walk-ins available on {walk_in_days} of {len(days)} days",
        "note": f"Dates after {open_through.isoformat()} are not released yet (not fully booked).",
    }
