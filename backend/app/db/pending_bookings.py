"""Pending-booking persistence behind a small typed interface, so tool tests can use an
in-memory fake. Tools run outside a request, so the SQL implementation opens its own session
per call. Every lookup filters by both session and conversation (CLAUDE.md hard rule 9)."""

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PendingBooking


class PendingBookingRepository(Protocol):
    async def create(self, booking: PendingBooking) -> PendingBooking: ...

    async def get_owned(
        self, booking_id: uuid.UUID, *, session_id: str, conversation_id: uuid.UUID, now: datetime
    ) -> PendingBooking | None: ...


def expire_if_due(booking: PendingBooking, now: datetime) -> bool:
    """Marks a pending row past its decision window as expired (no background job).
    Returns True if the status changed."""
    if booking.status == "pending" and booking.expires_at <= now:
        booking.status = "expired"
        return True
    return False


class SqlPendingBookingRepository:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    async def create(self, booking: PendingBooking) -> PendingBooking:
        async with self._session_maker() as db:
            db.add(booking)
            await db.commit()
            await db.refresh(booking)
            return booking

    async def get_owned(
        self, booking_id: uuid.UUID, *, session_id: str, conversation_id: uuid.UUID, now: datetime
    ) -> PendingBooking | None:
        async with self._session_maker() as db:
            result = await db.execute(
                select(PendingBooking).where(
                    PendingBooking.id == booking_id,
                    PendingBooking.session_id == session_id,
                    PendingBooking.conversation_id == conversation_id,
                )
            )
            booking = result.scalar_one_or_none()
            if booking is not None and expire_if_due(booking, now):
                await db.commit()
            return booking
