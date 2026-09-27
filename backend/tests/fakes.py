"""In-memory test doubles: a fake conversation repository and a config-recording
wrapper around a real (fake-LLM-backed) graph, so chat/session tests never touch
Postgres or a real LLM."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.db.models import Conversation
from app.db.repository import TITLE_MAX_CHARS
from app.errors import ApiError


class FakeConversationRepository:
    def __init__(self) -> None:
        self._rows: dict[uuid.UUID, Conversation] = {}

    async def create(self, session_id: str) -> Conversation:
        now = datetime.now(UTC)
        conversation = Conversation(
            id=uuid.uuid4(),
            session_id=session_id,
            title=None,
            message_count=0,
            created_at=now,
            updated_at=now,
            last_message_at=None,
        )
        self._rows[conversation.id] = conversation
        return conversation

    async def list_for_session(self, session_id: str) -> Sequence[Conversation]:
        rows = [c for c in self._rows.values() if c.session_id == session_id]
        return sorted(rows, key=lambda c: c.created_at, reverse=True)

    async def get_owned(self, conversation_id: uuid.UUID, session_id: str) -> Conversation:
        conversation = self._rows.get(conversation_id)
        if conversation is None or conversation.session_id != session_id:
            raise ApiError(404, "not_found", "Conversation not found.")
        return conversation

    async def finalize_turn(self, conversation: Conversation, first_message: str) -> None:
        conversation.message_count += 1
        conversation.last_message_at = datetime.now(UTC)
        if not conversation.title:
            conversation.title = first_message[:TITLE_MAX_CHARS]


class FakeSpendGuard:
    """In-memory stand-in for SqlSpendGuard: no Postgres, and lets tests force
    the over-cap path without touching daily_spend."""

    def __init__(self, over_cap: bool = False) -> None:
        self.over_cap = over_cap
        self.recorded: list[Decimal] = []

    async def check_cap(self) -> None:
        if self.over_cap:
            raise ApiError(
                429, "daily_budget_reached", "Daily demo budget reached, try again tomorrow."
            )

    async def record(self, cost_usd: Decimal) -> None:
        self.recorded.append(cost_usd)


class RecordingGraph:
    """Wraps a real CompiledStateGraph and records the config passed to astream_events,
    so tests can assert what reaches the graph without a custom fake event stream."""

    def __init__(self, graph: CompiledStateGraph) -> None:
        self._graph = graph
        self.last_config: RunnableConfig | None = None

    def astream_events(self, *args: Any, **kwargs: Any) -> Any:
        self.last_config = kwargs.get("config")
        return self._graph.astream_events(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)
