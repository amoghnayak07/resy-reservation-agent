import uuid

from fastapi import Depends, Header, Request
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_db
from app.db.repository import ConversationRepository, SqlConversationRepository
from app.errors import ApiError


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


async def get_conversation_repository(
    db: AsyncSession = Depends(get_db),
) -> ConversationRepository:
    return SqlConversationRepository(db)
