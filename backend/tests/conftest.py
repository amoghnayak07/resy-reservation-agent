"""Shared test fixtures. Langfuse is mocked for every test (unit and integration):
no test should reach the real Langfuse Cloud project configured in .env."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from langchain_core.callbacks import BaseCallbackHandler

from app.observability import langfuse as langfuse_module


class FakeCallbackHandler(BaseCallbackHandler):
    """Stands in for langfuse.langchain.CallbackHandler: a real no-op LangChain
    callback handler, so the callback manager is happy but nothing reaches Langfuse."""


@pytest.fixture(autouse=True)
def mock_langfuse(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    @asynccontextmanager
    async def fake_trace_turn(
        conversation_id: str,
        session_id: str,
        location_used: bool,
        tags: list[str] | None = None,
    ) -> AsyncIterator[tuple[FakeCallbackHandler, str]]:
        calls.append(
            {
                "conversation_id": conversation_id,
                "session_id": session_id,
                "location_used": location_used,
                "tags": tags,
            }
        )
        yield FakeCallbackHandler(), "fake-trace-id"

    def fake_record_booking_outcome(trace_id: str, outcome: str) -> None:
        calls.append({"booking_outcome": outcome})

    monkeypatch.setattr(langfuse_module, "trace_turn", fake_trace_turn)
    monkeypatch.setattr(langfuse_module, "record_booking_outcome", fake_record_booking_outcome)
    return calls
