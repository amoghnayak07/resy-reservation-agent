from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

from app.config import settings

# A Resy city `url_slug` (e.g. "new-york-ny"); resolved server-side against the city list.
RegionSlug = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")]


class ChatRequest(BaseModel):
    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=settings.max_message_chars)
    region: RegionSlug
