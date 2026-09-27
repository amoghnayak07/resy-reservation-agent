"""Pending-booking persistence behind a small typed interface, so tool and endpoint tests can use
an in-memory fake. Tools run outside a request, so the SQL implementation opens its own session
per call. Every lookup filters by session (CLAUDE.md hard rule 9); tool lookups also filter by
conversation."""

import uuid
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PendingBooking


class PendingBookingRepository(Protocol):
    async def create(self, booking: PendingBooking) -> PendingBooking: ...

    async def get_owned(
        self, booking_id: uuid.UUID, *, session_id: str, conversation_id: uuid.UUID, now: datetime
    ) -> PendingBooking | None: ...

    async def get_for_session(
        self, booking_id: uuid.UUID, *, session_id: str, now: datetime
    ) -> PendingBooking | None: ...

    async def transition(
        self,
        booking_id: uuid.UUID,
        *,
        session_id: str,
        from_status: str,
        to_status: str,
        now: datetime,
    ) -> PendingBooking | None:
        """Atomic `from_status → to_status`, only while the row is unexpired. None if no row
        matched (already handled, expired, or not owned)."""
        ...

    async def update_fields(self, booking_id: uuid.UUID, **values: Any) -> None: ...


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
        return await self._get(
            booking_id,
            now,
            PendingBooking.session_id == session_id,
            PendingBooking.conversation_id == conversation_id,
        )

    async def get_for_session(
        self, booking_id: uuid.UUID, *, session_id: str, now: datetime
    ) -> PendingBooking | None:
        return await self._get(booking_id, now, PendingBooking.session_id == session_id)

    async def transition(
        self,
        booking_id: uuid.UUID,
        *,
        session_id: str,
        from_status: str,
        to_status: str,
        now: datetime,
    ) -> PendingBooking | None:
        async with self._session_maker() as db:
            result = await db.execute(
                update(PendingBooking)
                .where(
                    PendingBooking.id == booking_id,
                    PendingBooking.session_id == session_id,
                    PendingBooking.status == from_status,
                    PendingBooking.expires_at > now,
                )
                .values(status=to_status)
                .returning(PendingBooking)
            )
            booking = result.scalar_one_or_none()
            await db.commit()
            return booking

    async def update_fields(self, booking_id: uuid.UUID, **values: Any) -> None:
        async with self._session_maker() as db:
            await db.execute(
                update(PendingBooking).where(PendingBooking.id == booking_id).values(**values)
            )
            await db.commit()

    async def _get(
        self, booking_id: uuid.UUID, now: datetime, *owner: Any
    ) -> PendingBooking | None:
        async with self._session_maker() as db:
            result = await db.execute(
                select(PendingBooking).where(PendingBooking.id == booking_id, *owner)
            )
            booking = result.scalar_one_or_none()
            if booking is not None and expire_if_due(booking, now):
                await db.commit()
            return booking
