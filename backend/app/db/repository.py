"""Conversation persistence, kept behind a small typed interface so chat/session
tests can swap in an in-memory fake instead of hitting Postgres."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation
from app.errors import ApiError

TITLE_MAX_CHARS = 60


class ConversationRepository(Protocol):
    async def create(self, session_id: str) -> Conversation: ...

    async def list_for_session(self, session_id: str) -> Sequence[Conversation]: ...

    async def get_owned(self, conversation_id: uuid.UUID, session_id: str) -> Conversation: ...

    async def finalize_turn(self, conversation: Conversation, first_message: str) -> None: ...


class SqlConversationRepository:
    """Default implementation, backed by the app's Postgres database."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, session_id: str) -> Conversation:
        conversation = Conversation(session_id=session_id)
        self._db.add(conversation)
        await self._db.commit()
        await self._db.refresh(conversation)
        return conversation

    async def list_for_session(self, session_id: str) -> Sequence[Conversation]:
        result = await self._db.execute(
            select(Conversation)
            .where(Conversation.session_id == session_id)
            .order_by(Conversation.created_at.desc())
        )
        return result.scalars().all()

    async def get_owned(self, conversation_id: uuid.UUID, session_id: str) -> Conversation:
        result = await self._db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.session_id == session_id
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ApiError(404, "not_found", "Conversation not found.")
        return conversation

    async def finalize_turn(self, conversation: Conversation, first_message: str) -> None:
        conversation.message_count += 1
        conversation.last_message_at = datetime.now(UTC)
        if not conversation.title:
            conversation.title = first_message[:TITLE_MAX_CHARS]
        await self._db.commit()
