import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Conversation
from app.db.repository import SqlConversationRepository
from app.errors import ApiError

pytestmark = pytest.mark.integration


async def test_session_isolation_get_owned_and_list() -> None:
    engine = create_async_engine(settings.database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session_a = f"test-session-a-{uuid.uuid4()}"
    session_b = f"test-session-b-{uuid.uuid4()}"

    try:
        async with session_maker() as db:
            repo = SqlConversationRepository(db)
            conversation = await repo.create(session_a)

            visible_to_a = await repo.list_for_session(session_a)
            assert [c.id for c in visible_to_a] == [conversation.id]

            visible_to_b = await repo.list_for_session(session_b)
            assert conversation.id not in [c.id for c in visible_to_b]

            owned = await repo.get_owned(conversation.id, session_a)
            assert owned.id == conversation.id

            with pytest.raises(ApiError) as exc_info:
                await repo.get_owned(conversation.id, session_b)
            assert exc_info.value.status_code == 404
    finally:
        async with session_maker() as db:
            await db.execute(
                delete(Conversation).where(Conversation.session_id.in_([session_a, session_b]))
            )
            await db.commit()
        await engine.dispose()


async def test_finalize_turn_sets_title_and_message_count() -> None:
    engine = create_async_engine(settings.database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session_id = f"test-session-{uuid.uuid4()}"

    try:
        async with session_maker() as db:
            repo = SqlConversationRepository(db)
            conversation = await repo.create(session_id)
            assert conversation.title is None
            assert conversation.message_count == 0

            await repo.finalize_turn(conversation, "a" * 100)

            assert conversation.message_count == 1
            assert conversation.title == "a" * 60
            assert conversation.last_message_at is not None

            await repo.finalize_turn(conversation, "should not overwrite title")
            assert conversation.message_count == 2
            assert conversation.title == "a" * 60
    finally:
        async with session_maker() as db:
            await db.execute(delete(Conversation).where(Conversation.session_id == session_id))
            await db.commit()
        await engine.dispose()
