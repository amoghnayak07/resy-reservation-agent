import uuid
from datetime import date, datetime
from typing import Literal, get_args

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(index=True)
    title: Mapped[str | None] = mapped_column(default=None)
    message_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    last_message_at: Mapped[datetime | None] = mapped_column(default=None)


class DailySpend(Base):
    __tablename__ = "daily_spend"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    spend_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    llm_calls: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


BookingStatus = Literal[
    "pending", "confirming", "confirmed", "declined", "expired", "failed", "unknown"
]
BOOKING_STATUSES: tuple[str, ...] = get_args(BookingStatus)
_TZ = DateTime(timezone=True)


class PendingBooking(Base):
    """A booking the user hasn't confirmed yet (stage 9), and its outcome (stage 10).
    `book_token` and `resy_token` are server-side only: never logged, traced, or returned."""

    __tablename__ = "pending_bookings"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in BOOKING_STATUSES) + ")",
            name="ck_pending_bookings_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    session_id: Mapped[str] = mapped_column(index=True)
    venue_id: Mapped[int]
    venue_name: Mapped[str]
    slot_start: Mapped[datetime] = mapped_column(_TZ)
    party_size: Mapped[int]
    seating_type: Mapped[str | None]
    book_token: Mapped[str]
    book_token_expires: Mapped[datetime | None] = mapped_column(_TZ)
    cancellation_policy: Mapped[str | None]
    refund_cutoff: Mapped[datetime | None] = mapped_column(_TZ)
    change_cutoff: Mapped[datetime | None] = mapped_column(_TZ)
    payment_type: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="pending")
    expires_at: Mapped[datetime] = mapped_column(_TZ)
    reservation_id: Mapped[int | None] = mapped_column(BigInteger)
    resy_token: Mapped[str | None]
    error_code: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(_TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), onupdate=func.now()
    )
