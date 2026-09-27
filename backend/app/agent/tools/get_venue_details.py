"""`get_venue_details` tool (stage 7): describes one venue from `GET /3/venue?id=`."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.common import dump, resy_error_result, truncate
from app.resy.client import ResyClient
from app.resy.errors import ResyError
from app.resy.models import VenueDetails

TOOL_NAME = "get_venue_details"
DESCRIPTION_CHARS = 500
NEED_TO_KNOW_CHARS = 300

DESCRIPTION = """Describe one restaurant: neighborhood, address, cuisine, price range, rating, \
a short description, need-to-know notes, and its Resy link. Use the `venue_id` from an \
exact match or a venue the user confirmed; never for an unconfirmed "did you mean" candidate."""


class GetVenueDetailsArgs(BaseModel):
    venue_id: int = Field(description="Resy venue ID from earlier results.")


def make_get_venue_details_tool(
    resy: ResyClient, *, now_fn: Callable[[ZoneInfo], datetime] | None = None
) -> BaseTool:
    clock = now_fn or (lambda tz: datetime.now(tz))

    async def get_venue_details(venue_id: int, config: RunnableConfig) -> str:
        tz = ZoneInfo(config.get("configurable", {})["timezone"])
        try:
            details = VenueDetails.from_response(await resy.get_venue(venue_id))
        except ResyError as exc:
            return dump(resy_error_result(exc, TOOL_NAME))
        return dump(_compact(details, clock(tz).date()))

    return StructuredTool.from_function(
        coroutine=get_venue_details,
        name=TOOL_NAME,
        description=DESCRIPTION,
        args_schema=GetVenueDetailsArgs,
    )


def _compact(details: VenueDetails, today: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "venue_id": details.id,
        "name": details.name,
        "neighborhood": details.neighborhood,
        "address": details.address,
        "cuisine": details.cuisine,
        "price_range": details.price_range,
        "rating": round(details.rating, 1) if details.rating is not None else None,
        "rating_count": details.rating_count,
        "description": truncate(details.description or details.about, DESCRIPTION_CHARS),
        "need_to_know": truncate(details.need_to_know, NEED_TO_KNOW_CHARS),
        "url": details.url,
    }
    if details.reopen_date and details.reopen_date > today:
        out["closed_until"] = details.reopen_date.isoformat()
    return {k: v for k, v in out.items() if v is not None}
