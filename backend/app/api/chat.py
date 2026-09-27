import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.api.deps import get_conversation_repository, get_graph, get_session_id
from app.api.message_text import content_to_text
from app.config import settings
from app.db.models import Conversation
from app.db.repository import ConversationRepository
from app.observability import langfuse as langfuse_module
from app.observability.pricing import compute_cost
from app.schemas.chat import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 15


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _get_or_create_conversation(
    repo: ConversationRepository, conversation_id: uuid.UUID | None, session_id: str
) -> Conversation:
    if conversation_id is None:
        return await repo.create(session_id)
    return await repo.get_owned(conversation_id, session_id)


@router.post("")
async def chat(
    body: ChatRequest,
    session_id: str = Depends(get_session_id),
    repo: ConversationRepository = Depends(get_conversation_repository),
    graph: CompiledStateGraph = Depends(get_graph),
) -> StreamingResponse:
    conversation = await _get_or_create_conversation(repo, body.conversation_id, session_id)

    location_used = body.user_location is not None
    location: dict[str, float] | None = None
    if body.user_location is not None:
        location = {
            "lat": round(body.user_location.lat, 3),
            "lng": round(body.user_location.lng, 3),
        }

    async def event_stream() -> AsyncIterator[str]:
        async with langfuse_module.trace_turn(
            conversation_id=str(conversation.id),
            session_id=session_id,
            location_used=location_used,
        ) as (handler, trace_id):
            yield _sse_event(
                "meta", {"conversation_id": str(conversation.id), "trace_id": trace_id}
            )

            run_config: RunnableConfig = {
                "configurable": {
                    "thread_id": str(conversation.id),
                    "timezone": body.timezone,
                    "location_available": location_used,
                    "location": location,
                },
                "callbacks": [handler],
            }

            start = time.monotonic()
            first_token_at: float | None = None
            last_ping = start
            input_tokens = 0
            cached_tokens = 0
            output_tokens = 0
            model_name = settings.openai_model

            try:
                async for event in graph.astream_events(
                    {"messages": [("user", body.message)]},
                    config=run_config,
                    version="v2",
                ):
                    now = time.monotonic()
                    if now - last_ping > PING_INTERVAL_SECONDS:
                        yield ": ping\n\n"
                        last_ping = now

                    kind = event["event"]
                    if kind == "on_chat_model_stream":
                        chunk = event["data"].get("chunk")
                        if isinstance(chunk, AIMessageChunk):
                            text = content_to_text(chunk.content)
                            if text:
                                if first_token_at is None:
                                    first_token_at = now
                                yield _sse_event("token", {"text": text})
                    elif kind == "on_chat_model_end":
                        output = event["data"].get("output")
                        usage_metadata = getattr(output, "usage_metadata", None)
                        if usage_metadata:
                            input_tokens += usage_metadata.get("input_tokens", 0)
                            output_tokens += usage_metadata.get("output_tokens", 0)
                            cached_tokens += (
                                usage_metadata.get("input_token_details", {}) or {}
                            ).get("cache_read", 0)
                        response_metadata = getattr(output, "response_metadata", None) or {}
                        model_name = response_metadata.get("model_name", model_name)
            except Exception:
                logger.exception("chat stream failed")
                yield _sse_event(
                    "error",
                    {
                        "code": "internal_error",
                        "message": "Something went wrong. Please try again.",
                    },
                )
                yield _sse_event("done", {})
                return

            end = time.monotonic()
            cost = compute_cost(model_name, input_tokens, cached_tokens, output_tokens)
            yield _sse_event(
                "usage",
                {
                    "model": model_name,
                    "input_tokens": input_tokens,
                    "cached_tokens": cached_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": float(cost),
                    "latency_ms": int((end - start) * 1000),
                    "ttft_ms": int((first_token_at - start) * 1000) if first_token_at else None,
                },
            )

        await repo.finalize_turn(conversation, body.message)
        yield _sse_event("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
