import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.api.deps import (
    get_conversation_repository,
    get_graph,
    get_pending_booking_repository,
    get_region_directory,
    get_session_id,
    get_spend_guard,
)
from app.api.streaming import SSE_HEADERS, GraphRun, pending_confirmations, sse_event
from app.db.models import Conversation
from app.db.pending_bookings import PendingBookingRepository
from app.db.repository import ConversationRepository
from app.guards.rate_limit import enforce_rate_limits
from app.guards.spend import SpendGuard
from app.guards.turns import check_turn_limit
from app.observability import langfuse as langfuse_module
from app.regions import RegionDirectory, region_config, resolve_region
from app.schemas.chat import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])

SUPERSEDED = json.dumps(
    {"status": "declined", "message": "Not booked: the user sent a new message instead."}
)


async def _get_or_create_conversation(
    repo: ConversationRepository, conversation_id: uuid.UUID | None, session_id: str
) -> Conversation:
    if conversation_id is None:
        return await repo.create(session_id)
    return await repo.get_owned(conversation_id, session_id)


async def close_open_confirmations(
    graph: CompiledStateGraph,
    bookings: PendingBookingRepository,
    thread_config: RunnableConfig,
    session_id: str,
) -> None:
    """A new message while a confirmation card is open declines it. The paused `book` call gets
    a tool result without resuming (no LLM call), so the history stays valid for the model."""
    confirmations = await pending_confirmations(graph, thread_config)
    if not confirmations:
        return
    for confirmation in confirmations:
        await bookings.transition(
            uuid.UUID(confirmation["pending_booking_id"]),
            session_id=session_id,
            from_status="pending",
            to_status="declined",
            now=datetime.now(UTC),
        )
    state = await graph.aget_state(thread_config)
    messages = state.values.get("messages", [])
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    unanswered = [c for c in (last_ai.tool_calls if last_ai else []) if c["id"] not in answered]
    await graph.aupdate_state(
        thread_config,
        {"messages": [ToolMessage(content=SUPERSEDED, tool_call_id=c["id"]) for c in unanswered]},
        as_node="tools",
    )


@router.post("", dependencies=[Depends(enforce_rate_limits)])
async def chat(
    body: ChatRequest,
    session_id: str = Depends(get_session_id),
    repo: ConversationRepository = Depends(get_conversation_repository),
    graph: CompiledStateGraph = Depends(get_graph),
    spend: SpendGuard = Depends(get_spend_guard),
    bookings: PendingBookingRepository = Depends(get_pending_booking_repository),
    regions: RegionDirectory = Depends(get_region_directory),
) -> StreamingResponse:
    await spend.check_cap()
    city = await resolve_region(regions, body.region)

    conversation = await _get_or_create_conversation(repo, body.conversation_id, session_id)
    check_turn_limit(conversation)

    await close_open_confirmations(
        graph, bookings, {"configurable": {"thread_id": str(conversation.id)}}, session_id
    )

    async def event_stream() -> AsyncIterator[str]:
        async with langfuse_module.trace_turn(
            conversation_id=str(conversation.id),
            session_id=session_id,
            region=city.slug,
        ) as (handler, trace_id):
            yield sse_event("meta", {"conversation_id": str(conversation.id), "trace_id": trace_id})

            run_config: RunnableConfig = {
                "configurable": {
                    "thread_id": str(conversation.id),
                    "session_id": session_id,
                    **region_config(city),
                },
                "callbacks": [handler],
                "recursion_limit": 12,
            }
            run = GraphRun(graph, {"messages": [("user", body.message)]}, run_config, spend)
            async for chunk in run.events():
                yield chunk

        if not run.failed:
            await repo.finalize_turn(conversation, body.message)
        yield sse_event("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
