import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Conversation, PendingBooking
from app.db.pending_bookings import SqlPendingBookingRepository

pytestmark = pytest.mark.integration


async def test_owned_lookup_and_expiry_persist() -> None:
    engine = create_async_engine(settings.database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session_id = f"test-session-{uuid.uuid4()}"
    conversation_id = uuid.uuid4()
    now = datetime.now(UTC)

    try:
        async with session_maker() as db:
            db.add(Conversation(id=conversation_id, session_id=session_id))
            await db.commit()

        repo = SqlPendingBookingRepository(session_maker)
        booking = await repo.create(
            PendingBooking(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                session_id=session_id,
                venue_id=1,
                venue_name="Test",
                slot_start=now + timedelta(days=2),
                party_size=2,
                book_token="test-token",
                status="pending",
                expires_at=now + timedelta(minutes=10),
            )
        )
        owner = {"session_id": session_id, "conversation_id": conversation_id}

        assert await repo.get_owned(booking.id, now=now, **{**owner, "session_id": "x"}) is None
        assert (
            await repo.get_owned(booking.id, now=now, **{**owner, "conversation_id": uuid.uuid4()})
            is None
        )

        fresh = await repo.get_owned(booking.id, now=now, **owner)
        assert fresh is not None and fresh.status == "pending"

        await repo.get_owned(booking.id, now=now + timedelta(minutes=11), **owner)
        reread = await repo.get_owned(booking.id, now=now, **owner)
        assert reread is not None and reread.status == "expired"  # persisted
    finally:
        async with session_maker() as db:
            await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await db.commit()
        await engine.dispose()
