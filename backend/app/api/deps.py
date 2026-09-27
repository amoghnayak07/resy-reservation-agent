import uuid

from fastapi import Depends, Header, Request
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import async_session_maker, get_db
from app.db.pending_bookings import PendingBookingRepository, SqlPendingBookingRepository
from app.db.repository import ConversationRepository, SqlConversationRepository
from app.errors import ApiError
from app.guards.spend import SpendGuard, SqlSpendGuard
from app.regions import RegionDirectory


async def get_session_id(
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> str:
    if x_session_id is None:
        raise ApiError(400, "invalid_session", "X-Session-Id header is required.")
    try:
        uuid.UUID(x_session_id)
    except ValueError as exc:
        raise ApiError(400, "invalid_session", "X-Session-Id must be a valid UUID.") from exc
    return x_session_id


def get_graph(request: Request) -> CompiledStateGraph:
    return request.app.state.graph


def get_region_directory(request: Request) -> RegionDirectory:
    return request.app.state.regions


async def get_conversation_repository(
    db: AsyncSession = Depends(get_db),
) -> ConversationRepository:
    return SqlConversationRepository(db)


async def get_spend_guard(db: AsyncSession = Depends(get_db)) -> SpendGuard:
    return SqlSpendGuard(db)


def get_pending_booking_repository() -> PendingBookingRepository:
    return SqlPendingBookingRepository(async_session_maker)
