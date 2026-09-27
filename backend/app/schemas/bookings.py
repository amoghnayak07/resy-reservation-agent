from pydantic import BaseModel, Field, field_validator

from app.schemas.chat import validate_timezone_name


class BookingActionRequest(BaseModel):
    """Decline body. `timezone` (the user's, as in chat) goes into the resumed run's config."""

    timezone: str

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return validate_timezone_name(value)


class ConfirmBookingRequest(BookingActionRequest):
    # Never logged, traced, or passed to the graph.
    passcode: str = Field(min_length=1, max_length=200)
