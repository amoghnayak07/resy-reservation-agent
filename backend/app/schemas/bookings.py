from pydantic import BaseModel, Field

from app.schemas.chat import RegionSlug


class BookingActionRequest(BaseModel):
    """Decline body. `region` (as in chat) sets the resumed run's timezone and names."""

    region: RegionSlug


class ConfirmBookingRequest(BookingActionRequest):
    # Never logged, traced, or passed to the graph.
    passcode: str = Field(min_length=1, max_length=200)
