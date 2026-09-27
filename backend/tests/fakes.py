"""In-memory test doubles: a fake conversation repository and a config-recording
wrapper around a real (fake-LLM-backed) graph, so chat/session tests never touch
Postgres or a real LLM."""

import json
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.db.models import Conversation, PendingBooking
from app.db.pending_bookings import expire_if_due
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


class FakePendingBookingRepository:
    """In-memory stand-in for SqlPendingBookingRepository."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, PendingBooking] = {}

    async def create(self, booking: PendingBooking) -> PendingBooking:
        self.rows[booking.id] = booking
        return booking

    async def get_owned(
        self, booking_id: uuid.UUID, *, session_id: str, conversation_id: uuid.UUID, now: datetime
    ) -> PendingBooking | None:
        booking = self.rows.get(booking_id)
        if booking is None or booking.conversation_id != conversation_id:
            return None
        return await self.get_for_session(booking_id, session_id=session_id, now=now)

    async def get_for_session(
        self, booking_id: uuid.UUID, *, session_id: str, now: datetime
    ) -> PendingBooking | None:
        booking = self.rows.get(booking_id)
        if booking is None or booking.session_id != session_id:
            return None
        expire_if_due(booking, now)
        return booking

    async def transition(
        self,
        booking_id: uuid.UUID,
        *,
        session_id: str,
        from_status: str,
        to_status: str,
        now: datetime,
    ) -> PendingBooking | None:
        booking = self.rows.get(booking_id)
        if (
            booking is None
            or booking.session_id != session_id
            or booking.status != from_status
            or booking.expires_at <= now
        ):
            return None
        booking.status = to_status
        return booking

    async def update_fields(self, booking_id: uuid.UUID, **values: Any) -> None:
        booking = self.rows[booking_id]
        for key, value in values.items():
            setattr(booking, key, value)


class ToolCallingFakeModel(GenericFakeChatModel):
    """GenericFakeChatModel that accepts bind_tools (a no-op), so graphs with tools can run
    on scripted AIMessages, including ones with tool_calls. Streams each scripted message as
    one chunk (the base class yields nothing for tool-call-only messages when streaming)."""

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, Any]:
        return self

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        scripted = message.generations[0].message
        tool_calls = scripted.tool_calls if isinstance(scripted, AIMessage) else []
        chunk = AIMessageChunk(
            content=scripted.content,
            tool_call_chunks=[
                tool_call_chunk(
                    name=call["name"], args=json.dumps(call["args"]), id=call["id"], index=i
                )
                for i, call in enumerate(tool_calls)
            ],
        )
        if run_manager and isinstance(chunk.content, str) and chunk.content:
            run_manager.on_llm_new_token(chunk.content, chunk=ChatGenerationChunk(message=chunk))
        yield ChatGenerationChunk(message=chunk)


def scripted_model(*messages: AIMessage) -> ToolCallingFakeModel:
    return ToolCallingFakeModel(messages=iter(messages))


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
