import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.graph import open_checkpointer
from app.config import settings
from app.db.models import Conversation

pytestmark = pytest.mark.integration


async def test_conversation_insert_and_read_roundtrip() -> None:
    engine = create_async_engine(settings.database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    conversation_id = uuid.uuid4()

    try:
        async with session_maker() as session:
            session.add(Conversation(id=conversation_id, session_id="test-session"))
            await session.commit()

        async with session_maker() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            row = result.scalar_one()
            assert row.session_id == "test-session"
            assert row.message_count == 0
    finally:
        async with session_maker() as session:
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await session.commit()
        await engine.dispose()


async def test_checkpointer_setup_runs_twice_without_error() -> None:
    async with open_checkpointer():
        pass
    async with open_checkpointer():
        pass
