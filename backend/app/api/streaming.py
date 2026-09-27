"""One graph run streamed as SSE events (CLAUDE.md "Streaming protocol"). Shared by /api/chat
and the booking confirm/decline endpoints, which resume a paused graph."""

import json
import logging
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp
from langgraph.graph.state import CompiledStateGraph
from openai import APIConnectionError, APITimeoutError, OpenAIError, RateLimitError

from app.api.message_text import content_to_text
from app.config import settings
from app.guards.spend import SpendGuard
from app.observability.pricing import compute_cost

logger = logging.getLogger(__name__)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
PING_INTERVAL_SECONDS = 15


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def tool_outcome(output: Any) -> tuple[bool, str | None]:
    """(ok, error code) from a tool's output: tools return JSON with an "error" key on
    failure (e.g. "location_required")."""
    content = getattr(output, "content", output)
    try:
        parsed = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return True, None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    return (error is None, str(error) if error else None)


async def pending_confirmations(
    graph: CompiledStateGraph, config: RunnableConfig
) -> list[dict[str, Any]]:
    """Interrupt payloads from the book tool that are waiting on the user."""
    state = await graph.aget_state(config)
    return [
        intr.value
        for intr in state.interrupts
        if isinstance(intr.value, dict) and "pending_booking_id" in intr.value
    ]


class GraphRun:
    """Streams one run. Yields everything up to (not including) `done`; `failed` is set when
    an error event was sent, so callers can skip their own bookkeeping."""

    def __init__(
        self,
        graph: CompiledStateGraph,
        graph_input: Any,
        config: RunnableConfig,
        spend: SpendGuard,
    ) -> None:
        self._graph = graph
        self._input = graph_input
        self._config = config
        self._spend = spend
        self.failed = False

    async def events(self) -> AsyncIterator[str]:
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
            async for event in self._graph.astream_events(
                self._input, config=self._config, version="v2"
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
                            yield sse_event("token", {"text": text})
                elif kind == "on_tool_start":
                    call_id = str(event["run_id"])
                    tool_started[call_id] = now
                    yield sse_event("tool_start", {"name": event["name"], "call_id": call_id})
                elif kind in ("on_tool_end", "on_tool_error"):
                    call_id = str(event["run_id"])
                    if kind == "on_tool_end":
                        ok, error_code = tool_outcome(event["data"].get("output"))
                    else:
                        # book's interrupt() surfaces as a tool error; it's a pause, not a failure.
                        ok = isinstance(event["data"].get("error"), GraphBubbleUp)
                        error_code = None
                    if error_code == "location_required":
                        yield sse_event("location_required", {})
                    started = tool_started.pop(call_id, now)
                    yield sse_event(
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
                        call_cost = compute_cost(model_name, call_input, call_cached, call_output)
                        total_cost += call_cost
                        await self._spend.record(call_cost)
            confirmations = await pending_confirmations(self._graph, self._config)
        except RateLimitError:
            logger.exception("chat stream failed: openai rate limited")
            yield self._error(
                "llm_rate_limited", "The AI service is busy right now. Please try again shortly."
            )
            return
        except (APITimeoutError, APIConnectionError):
            logger.exception("chat stream failed: openai timeout/connection")
            yield self._error("llm_unavailable", "The AI service timed out. Please try again.")
            return
        except OpenAIError:
            logger.exception("chat stream failed: openai error")
            yield self._error("llm_error", "The AI service returned an error. Please try again.")
            return
        except Exception:
            logger.exception("chat stream failed")
            yield self._error("internal_error", "Something went wrong. Please try again.")
            return

        end = time.monotonic()
        yield sse_event(
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
        for confirmation in confirmations:
            yield sse_event("confirmation_required", confirmation)

    def _error(self, code: str, message: str) -> str:
        self.failed = True
        return sse_event("error", {"code": code, "message": message})
