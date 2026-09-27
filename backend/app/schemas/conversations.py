from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None
    message_count: int
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class MessageOut(BaseModel):
    role: Literal["human", "ai"]
    content: str
