import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from openai import APIConnectionError, APITimeoutError, OpenAIError, RateLimitError

from app.api.deps import (
    get_conversation_repository,
    get_graph,
    get_session_id,
    get_spend_guard,
)
from app.api.message_text import content_to_text
from app.config import settings
from app.db.models import Conversation
from app.db.repository import ConversationRepository
from app.guards.rate_limit import enforce_rate_limits
from app.guards.spend import SpendGuard
from app.guards.turns import check_turn_limit
from app.observability import langfuse as langfuse_module
from app.observability.pricing import compute_cost
from app.schemas.chat import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 15


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _tool_outcome(output: Any) -> tuple[bool, str | None]:
    """(ok, error code) from a tool's output: tools return JSON with an "error" key on
    failure (e.g. "location_required")."""
    content = getattr(output, "content", output)
    try:
        parsed = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return True, None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    return (error is None, str(error) if error else None)


async def _stream_error(code: str, message: str) -> AsyncIterator[str]:
    yield _sse_event("error", {"code": code, "message": message})
    yield _sse_event("done", {})


async def _get_or_create_conversation(
    repo: ConversationRepository, conversation_id: uuid.UUID | None, session_id: str
) -> Conversation:
    if conversation_id is None:
        return await repo.create(session_id)
    return await repo.get_owned(conversation_id, session_id)


@router.post("", dependencies=[Depends(enforce_rate_limits)])
async def chat(
    body: ChatRequest,
    session_id: str = Depends(get_session_id),
    repo: ConversationRepository = Depends(get_conversation_repository),
    graph: CompiledStateGraph = Depends(get_graph),
    spend: SpendGuard = Depends(get_spend_guard),
) -> StreamingResponse:
    await spend.check_cap()

    conversation = await _get_or_create_conversation(repo, body.conversation_id, session_id)
    check_turn_limit(conversation)

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
                "recursion_limit": 12,
            }

            start = time.monotonic()
            first_token_at: float | None = None
            last_ping = start
            input_tokens = 0
            cached_tokens = 0
            output_tokens = 0
            total_cost = Decimal("0")
            model_name = settings.openai_model
            tool_started: dict[str, float] = {}

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
                    elif kind == "on_tool_start":
                        call_id = str(event["run_id"])
                        tool_started[call_id] = now
                        yield _sse_event("tool_start", {"name": event["name"], "call_id": call_id})
                    elif kind in ("on_tool_end", "on_tool_error"):
                        call_id = str(event["run_id"])
                        if kind == "on_tool_end":
                            ok, error_code = _tool_outcome(event["data"].get("output"))
                        else:
                            ok, error_code = False, None
                        if error_code == "location_required":
                            yield _sse_event("location_required", {})
                        started = tool_started.pop(call_id, now)
                        yield _sse_event(
                            "tool_end",
                            {
                                "name": event["name"],
                                "call_id": call_id,
                                "ok": ok,
                                "duration_ms": int((now - started) * 1000),
                            },
                        )
                    elif kind == "on_chat_model_end":
                        output = event["data"].get("output")
                        usage_metadata = getattr(output, "usage_metadata", None)
                        if usage_metadata:
                            call_input = usage_metadata.get("input_tokens", 0)
                            call_output = usage_metadata.get("output_tokens", 0)
                            call_cached = (usage_metadata.get("input_token_details", {}) or {}).get(
                                "cache_read", 0
                            )
                            input_tokens += call_input
                            output_tokens += call_output
                            cached_tokens += call_cached
                            response_metadata = getattr(output, "response_metadata", None) or {}
                            model_name = response_metadata.get("model_name", model_name)
                            call_cost = compute_cost(
                                model_name, call_input, call_cached, call_output
                            )
                            total_cost += call_cost
                            await spend.record(call_cost)
            except RateLimitError:
                logger.exception("chat stream failed: openai rate limited")
                async for chunk in _stream_error(
                    "llm_rate_limited",
                    "The AI service is busy right now. Please try again shortly.",
                ):
                    yield chunk
                return
            except (APITimeoutError, APIConnectionError):
                logger.exception("chat stream failed: openai timeout/connection")
                async for chunk in _stream_error(
                    "llm_unavailable", "The AI service timed out. Please try again."
                ):
                    yield chunk
                return
            except OpenAIError:
                logger.exception("chat stream failed: openai error")
                async for chunk in _stream_error(
                    "llm_error", "The AI service returned an error. Please try again."
                ):
                    yield chunk
                return
            except Exception:
                logger.exception("chat stream failed")
                async for chunk in _stream_error(
                    "internal_error", "Something went wrong. Please try again."
                ):
                    yield chunk
                return

            end = time.monotonic()
            yield _sse_event(
                "usage",
                {
                    "model": model_name,
                    "input_tokens": input_tokens,
                    "cached_tokens": cached_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": float(total_cost),
                    "latency_ms": int((end - start) * 1000),
                    "ttft_ms": int((first_token_at - start) * 1000) if first_token_at else None,
                },
            )

        await repo.finalize_turn(conversation, body.message)
        yield _sse_event("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
