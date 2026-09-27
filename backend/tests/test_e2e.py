"""End-to-end through the API (stage 11): search → venue details → calendar → prepare_booking →
confirmation_required → confirm → confirmed. Scripted fake model, fake repositories, and Resy
fixtures behind a mocked transport: no network, no real reservation."""

import asyncio
import copy
import json
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from app.agent.graph import build_graph
from app.agent.tools.book import make_book_tool
from app.agent.tools.get_venue_calendar import make_get_venue_calendar_tool
from app.agent.tools.get_venue_details import make_get_venue_details_tool
from app.agent.tools.prepare_booking import make_prepare_booking_tool
from app.agent.tools.search_availability import make_search_availability_tool
from app.agent.tools.slot_ids import SlotIdMap
from app.api.deps import (
    get_conversation_repository,
    get_graph,
    get_pending_booking_repository,
    get_spend_guard,
)
from app.config import settings
from app.guards import rate_limit
from app.main import create_app
from app.resy.client import ResyClient
from tests.fakes import (
    FakeConversationRepository,
    FakePendingBookingRepository,
    FakeSpendGuard,
    ToolCallingFakeModel,
)
from tests.test_chat_api import _parse_sse
from tests.test_search_tool import LOWER_MANHATTAN, NY, fixture

SESSION_ID = str(uuid.uuid4())
TZ = "America/New_York"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=NY)  # search/calendar fixtures are for 2026-10-22
SEARCH_ARGS = {
    "venue_id": 87134,
    "date": "2026-10-22",
    "party_size": 2,
    "time_precision": "exact",
    "requested_time": "12:00",
}


def _call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": f"c-{uuid.uuid4().hex[:6]}"}]
    )


class FlowModel(ToolCallingFakeModel):
    """Walks the full flow, taking slot and booking IDs from earlier tool results."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        last = messages[-1]
        results = {
            m.name: json.loads(str(m.content)) for m in messages if isinstance(m, ToolMessage)
        }
        if isinstance(last, HumanMessage):
            reply = _call("search_availability", SEARCH_ARGS)
        elif isinstance(last, ToolMessage) and last.name == "search_availability":
            reply = _call("get_venue_details", {"venue_id": 87134})
        elif isinstance(last, ToolMessage) and last.name == "get_venue_details":
            reply = _call("get_venue_calendar", {"venue_id": 87134, "party_size": 2})
        elif isinstance(last, ToolMessage) and last.name == "get_venue_calendar":
            slot_id = results["search_availability"]["exact_time_match"][0]
            reply = _call("prepare_booking", {"slot_id": slot_id})
        elif isinstance(last, ToolMessage) and last.name == "prepare_booking":
            reply = _call(
                "book", {"pending_booking_id": results["prepare_booking"]["pending_booking_id"]}
            )
        else:
            reply = AIMessage(content=f"Booking status: {results['book']['status']}.")
        return ChatResult(generations=[ChatGeneration(message=reply)])


class ResyFixtures:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.book_forms: list[dict[str, list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path == "/3/details":
            details = copy.deepcopy(fixture("details-commit1.json"))
            details["book_token"] = {
                "value": "BOOK_TOKEN_E2E",
                "date_expires": "2099-01-01T00:00:00Z",
            }
            return httpx.Response(200, json=details)
        if path == "/3/book":
            self.book_forms.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json=fixture("book.json"))
        files = {
            "/3/venue": "venue-by-id.json",
            "/4/find": "venue-find.json",
            "/4/venue/calendar": "venue-calendar.json",
        }
        return httpx.Response(200, json=fixture(files[path]))


@pytest.fixture(autouse=True)
def passcode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "demo_booking_passcode", SecretStr("e2e-passcode"))
    monkeypatch.setattr(rate_limit, "_limiter", rate_limit.SlidingWindowLimiter())


def test_search_to_confirmed_booking() -> None:
    resy_fake = ResyFixtures()
    resy = ResyClient(
        api_key="k",
        auth_token="t",
        writes_enabled=True,  # mocked transport only
        transport=httpx.MockTransport(resy_fake),
        retry_backoff_s=0,
    )
    clock = lambda tz: NOW.astimezone(tz)  # noqa: E731
    slot_map = SlotIdMap()
    bookings = FakePendingBookingRepository()
    conversations = FakeConversationRepository()
    conversation_id = str(asyncio.run(conversations.create(SESSION_ID)).id)
    tools = [
        make_search_availability_tool(resy, slot_map=slot_map, now_fn=clock),
        make_get_venue_details_tool(resy, now_fn=clock),
        make_get_venue_calendar_tool(resy, now_fn=clock),
        make_prepare_booking_tool(resy, bookings, slot_map=slot_map),
        make_book_tool(resy, bookings),
    ]
    graph = build_graph(MemorySaver(), llm=FlowModel(messages=iter([])), tools=tools)
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph
    app.dependency_overrides[get_conversation_repository] = lambda: conversations
    app.dependency_overrides[get_spend_guard] = lambda: FakeSpendGuard()
    app.dependency_overrides[get_pending_booking_repository] = lambda: bookings
    client = TestClient(app)
    headers = {"X-Session-Id": SESSION_ID}

    chat = client.post(
        "/api/chat",
        headers=headers,
        json={
            "conversation_id": conversation_id,
            "message": "Table for 2 at Brooklyn Chop House FiDi on Oct 22 at noon",
            "timezone": TZ,
            "user_location": {**LOWER_MANHATTAN, "accuracy_m": 20},
        },
    )
    events = _parse_sse(chat.text)
    tools_run = [data["name"] for name, data in events if name == "tool_end"]
    assert tools_run == [
        "search_availability",
        "get_venue_details",
        "get_venue_calendar",
        "prepare_booking",
        "book",  # paused at interrupt(): reported ok, not as a failure
    ]
    assert all(data["ok"] for name, data in events if name == "tool_end")
    card = next(data for name, data in events if name == "confirmation_required")
    assert card["summary"]["time"] == "12:00"
    assert resy_fake.book_forms == []  # paused at the card

    confirm = client.post(
        f"/api/bookings/{card['pending_booking_id']}/confirm",
        headers=headers,
        json={"passcode": "e2e-passcode", "timezone": TZ},
    )
    resumed = _parse_sse(confirm.text)

    assert resy_fake.book_forms[0]["book_token"] == ["BOOK_TOKEN_E2E"]
    assert len(resy_fake.book_forms) == 1
    row = bookings.rows[uuid.UUID(card["pending_booking_id"])]
    assert (row.status, row.reservation_id) == ("confirmed", 933020068)
    text = "".join(data["text"] for name, data in resumed if name == "token")
    assert text == "Booking status: confirmed."
    for secret in ("BOOK_TOKEN_E2E", "SCRUBBED_RESY_TOKEN", "e2e-passcode", "rgs://"):
        assert secret not in chat.text + confirm.text
    assert "/3/details" in resy_fake.paths
