"""Stage 10 booking gate end to end: chat → prepare_booking → book interrupts → confirm/decline
endpoints resume. Fake model, fake repositories, and a mocked Resy transport: nothing reaches
api.resy.com, so no real reservation can be made."""

import asyncio
import copy
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import SecretStr

from app.agent.graph import build_graph
from app.agent.tools.book import make_book_tool
from app.agent.tools.prepare_booking import make_prepare_booking_tool
from app.agent.tools.slot_ids import SlotIdMap, SlotRef
from app.api.deps import (
    get_conversation_repository,
    get_graph,
    get_pending_booking_repository,
    get_region_directory,
    get_spend_guard,
)
from app.config import settings
from app.db.models import PendingBooking
from app.guards import rate_limit
from app.main import create_app
from app.resy.client import ResyClient
from tests.fakes import (
    NY_REGION,
    FakeConversationRepository,
    FakePendingBookingRepository,
    FakeSpendGuard,
    ToolCallingFakeModel,
    fixture_regions,
)
from tests.test_chat_api import _parse_sse
from tests.test_search_tool import NY, fixture

SESSION_ID = str(uuid.uuid4())
PASSCODE = "correct-horse"
REGION = "new-york-ny"
TOKEN = "rgs://resy/87134/4257306/2/2026-10-22/2026-10-22/19:00:00/2/Dining Room"
FRESH = "2099-01-01T00:00:00Z"
SECRETS = ("SCRUBBED_RESY_TOKEN", "BOOK_TOKEN", PASSCODE, "rgs://")


def details(token: str = "BOOK_TOKEN_1", expires: str = FRESH, **payment: Any) -> Any:
    body = copy.deepcopy(fixture("details-commit1.json"))
    body["book_token"] = {"value": token, "date_expires": expires}
    if "config_type" in payment:
        body["payment"]["config"]["type"] = payment["config_type"]
    return body


class FakeResy:
    """Mocked Resy: /3/details returns queued responses; /3/book runs `book_behavior`."""

    def __init__(self, *detail_responses: Any, book: Callable[[], httpx.Response] | None = None):
        self.details_bodies: list[dict[str, Any]] = []
        self.book_forms: list[dict[str, list[str]]] = []
        self._details = list(detail_responses) or [details()]
        self._book = book or (lambda: httpx.Response(200, json=fixture("book.json")))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/details":
            self.details_bodies.append(json.loads(request.content))
            body = self._details.pop(0) if len(self._details) > 1 else self._details[0]
            return httpx.Response(200, json=body)
        if request.url.path == "/3/book":
            self.book_forms.append(parse_qs(request.content.decode()))
            return self._book()
        raise AssertionError(f"unexpected Resy call {request.url.path}")


class ScriptedByStateModel(ToolCallingFakeModel):
    """Answers from the conversation so far: prepare the slot, book what was prepared, then
    report the book tool's status."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        last = messages[-1]
        if isinstance(last, HumanMessage):
            reply = AIMessage(
                content="",
                tool_calls=[{"name": "prepare_booking", "args": {"slot_id": "s1"}, "id": _id()}],
            )
        elif isinstance(last, ToolMessage) and last.name == "prepare_booking":
            pending_id = json.loads(str(last.content))["pending_booking_id"]
            reply = AIMessage(
                content="",
                tool_calls=[
                    {"name": "book", "args": {"pending_booking_id": pending_id}, "id": _id()}
                ],
            )
        else:
            status = json.loads(str(last.content)).get("status", "error")
            reply = AIMessage(content=f"Booking status: {status}.")
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _id() -> str:
    return f"call-{uuid.uuid4().hex[:8]}"


class Harness:
    def __init__(self, resy_fake: FakeResy) -> None:
        self.resy_fake = resy_fake
        self.bookings = FakePendingBookingRepository()
        self.conversations = FakeConversationRepository()
        self.conversation_id = str(asyncio.run(self.conversations.create(SESSION_ID)).id)
        self.slot_map = SlotIdMap()
        self.slot_map.add(
            self.conversation_id,
            SlotRef(
                config_token=TOKEN,
                venue_id=87134,
                venue_name="Brooklyn Chop House - Downtown FIDI",
                party_size=2,
                start=datetime(2026, 10, 22, 19, tzinfo=NY),
                seating_type="Dining Room",
                bookable=True,
                requires_payment=False,
                venue_url="https://resy.com/cities/new-york-ny/venues/bch",
            ),
        )
        resy = ResyClient(
            api_key="k",
            auth_token="t",
            writes_enabled=True,  # mocked transport only; lets the gate reach /3/book
            transport=httpx.MockTransport(resy_fake),
            retry_backoff_s=0,
        )
        tools = [
            make_prepare_booking_tool(resy, self.bookings, slot_map=self.slot_map),
            make_book_tool(resy, self.bookings),
        ]
        self.graph: CompiledStateGraph = build_graph(
            MemorySaver(), llm=ScriptedByStateModel(messages=iter([])), tools=tools
        )
        app = create_app()
        app.dependency_overrides[get_graph] = lambda: self.graph
        app.dependency_overrides[get_conversation_repository] = lambda: self.conversations
        app.dependency_overrides[get_spend_guard] = lambda: FakeSpendGuard()
        app.dependency_overrides[get_pending_booking_repository] = lambda: self.bookings
        regions = fixture_regions()
        app.dependency_overrides[get_region_directory] = lambda: regions
        self.client = TestClient(app)

    def chat(self, message: str = "book 7pm for 2") -> list[tuple[str, dict[str, Any]]]:
        response = self.client.post(
            "/api/chat",
            json={"message": message, "region": REGION, "conversation_id": self.conversation_id},
            headers={"X-Session-Id": SESSION_ID},
        )
        assert response.status_code == 200
        return _parse_sse(response.text)

    def pause(self) -> str:
        """Runs the chat turn up to the confirmation card; returns the pending booking ID."""
        events = self.chat()
        (card,) = [data for name, data in events if name == "confirmation_required"]
        assert [name for name, _ in events][-2:] == ["confirmation_required", "done"]
        return card["pending_booking_id"]

    def confirm(
        self, booking_id: str, passcode: str = PASSCODE, session_id: str = SESSION_ID
    ) -> Any:
        return self.client.post(
            f"/api/bookings/{booking_id}/confirm",
            json={"passcode": passcode, "region": REGION},
            headers={"X-Session-Id": session_id},
        )

    def decline(self, booking_id: str) -> Any:
        return self.client.post(
            f"/api/bookings/{booking_id}/decline",
            json={"region": REGION},
            headers={"X-Session-Id": SESSION_ID},
        )

    def row(self, booking_id: str) -> PendingBooking:
        return self.bookings.rows[uuid.UUID(booking_id)]


@pytest.fixture(autouse=True)
def passcode_and_fresh_limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "demo_booking_passcode", SecretStr(PASSCODE))
    monkeypatch.setattr(rate_limit, "_limiter", rate_limit.SlidingWindowLimiter())
    yield


def _tokens(events: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(data["text"] for name, data in events if name == "token")


# --- happy path ---------------------------------------------------------------------------


def test_confirm_with_passcode_books_once(mock_langfuse: list[dict[str, Any]]) -> None:
    h = Harness(FakeResy())

    events = h.chat()
    card = next(data for name, data in events if name == "confirmation_required")
    booking_id = card["pending_booking_id"]
    assert card["summary"]["restaurant"] == "Brooklyn Chop House - Downtown FIDI"
    assert card["summary"]["time"] == "19:00"
    assert datetime.fromisoformat(card["summary"]["expires_at"]) > datetime.now(UTC)
    assert h.resy_fake.book_forms == []  # paused: nothing booked yet
    assert h.row(booking_id).status == "pending"

    response = h.confirm(booking_id)
    assert response.status_code == 200
    resumed = _parse_sse(response.text)

    assert len(h.resy_fake.book_forms) == 1
    assert h.resy_fake.book_forms[0]["book_token"] == ["BOOK_TOKEN_1"]
    row = h.row(booking_id)
    assert (row.status, row.reservation_id, row.resy_token) == (
        "confirmed",
        933020068,
        "SCRUBBED_RESY_TOKEN",
    )
    assert "confirmed" in _tokens(resumed)
    assert [name for name, _ in resumed][-1] == "done"
    everything = events + resumed
    for secret in SECRETS:
        assert secret not in json.dumps(everything)
    assert {"booking_outcome": "confirmed"} in mock_langfuse
    assert mock_langfuse[1]["tags"] == ["booking_attempt"]


# --- the gate -----------------------------------------------------------------------------


def test_wrong_passcode_is_403_and_does_not_book() -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()

    response = h.confirm(booking_id, passcode="wrong")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_passcode"
    assert h.row(booking_id).status == "pending"
    assert h.resy_fake.book_forms == []


def test_unset_passcode_refuses_every_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()
    monkeypatch.setattr(settings, "demo_booking_passcode", SecretStr(""))
    assert h.confirm(booking_id, passcode="anything").status_code == 403
    assert h.resy_fake.book_forms == []


def test_double_confirm_is_409_and_books_once() -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()

    assert h.confirm(booking_id).status_code == 200
    second = h.confirm(booking_id)

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_handled"
    assert len(h.resy_fake.book_forms) == 1


def test_expired_pending_booking_is_410() -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()
    h.row(booking_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)

    response = h.confirm(booking_id)

    assert response.status_code == 410
    assert h.row(booking_id).status == "expired"
    assert h.resy_fake.book_forms == []


def test_other_session_gets_404() -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()
    response = h.confirm(booking_id, session_id=str(uuid.uuid4()))
    assert response.status_code == 404
    assert h.row(booking_id).status == "pending"


def test_confirm_attempts_are_rate_limited() -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()
    codes = [h.confirm(booking_id, passcode="wrong").status_code for _ in range(6)]
    assert codes == [403] * 5 + [429]


def test_confirm_without_a_paused_graph_is_409() -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()
    h.chat("never mind")  # closes the card
    response = h.confirm(booking_id)
    assert response.status_code == 409
    assert h.resy_fake.book_forms == []


async def test_resume_without_confirming_state_does_not_book() -> None:
    fake = FakeResy()
    h = await asyncio.to_thread(Harness, fake)
    config: RunnableConfig = {
        "configurable": {"thread_id": h.conversation_id, "session_id": SESSION_ID, **NY_REGION}
    }
    await h.graph.ainvoke({"messages": [("user", "book it")]}, config=config)
    (booking_id,) = [str(k) for k in h.bookings.rows]

    # A forged approval straight into the graph, skipping the confirm endpoint.
    result = await h.graph.ainvoke(Command(resume={"approved": True}), config=config)

    assert fake.book_forms == []
    assert h.row(booking_id).status == "pending"
    tool_result = json.loads(str(result["messages"][-2].content))
    assert tool_result["error"] == "not_confirmed"


# --- decline and superseded cards -----------------------------------------------------------


def test_decline_marks_declined_and_does_not_book() -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()

    response = h.decline(booking_id)

    assert response.status_code == 200
    assert "declined" in _tokens(_parse_sse(response.text))
    assert h.row(booking_id).status == "declined"
    assert h.resy_fake.book_forms == []


def test_new_message_while_card_is_open_declines_it() -> None:
    h = Harness(FakeResy())
    booking_id = h.pause()

    events = h.chat("actually, somewhere else")

    assert h.row(booking_id).status == "declined"
    assert h.resy_fake.book_forms == []
    assert "done" in [name for name, _ in events]
    state = h.graph.get_state({"configurable": {"thread_id": h.conversation_id}})
    book_results = [
        m
        for m in state.values["messages"]
        if isinstance(m, ToolMessage) and m.name != "prepare_booking"
    ]
    assert json.loads(str(book_results[0].content))["status"] == "declined"


# --- Resy outcomes --------------------------------------------------------------------------


def _raise(exc: Exception) -> Callable[[], httpx.Response]:
    def behavior() -> httpx.Response:
        raise exc

    return behavior


def test_timeout_after_sending_is_unknown_and_never_retried() -> None:
    h = Harness(FakeResy(book=_raise(httpx.ReadTimeout("slow"))))
    booking_id = h.pause()

    resumed = _parse_sse(h.confirm(booking_id).text)

    assert len(h.resy_fake.book_forms) == 1
    row = h.row(booking_id)
    assert (row.status, row.error_code) == ("unknown", "no_response")
    assert "unknown" in _tokens(resumed)


def test_server_error_is_unknown() -> None:
    h = Harness(FakeResy(book=lambda: httpx.Response(502)))
    booking_id = h.pause()
    h.confirm(booking_id)
    assert h.row(booking_id).status == "unknown"
    assert len(h.resy_fake.book_forms) == 1


def test_connect_error_before_sending_is_failed() -> None:
    h = Harness(FakeResy(book=_raise(httpx.ConnectError("refused"))))
    booking_id = h.pause()
    h.confirm(booking_id)
    assert h.row(booking_id).status == "failed"


def test_resy_rejection_is_failed() -> None:
    h = Harness(FakeResy(book=lambda: httpx.Response(412, json={"message": "no"})))
    booking_id = h.pause()
    h.confirm(booking_id)
    row = h.row(booking_id)
    assert (row.status, row.error_code) == ("failed", "ResyError")


# --- book token freshness -------------------------------------------------------------------


def test_expired_book_token_is_reissued_then_booked() -> None:
    stale = details(token="BOOK_TOKEN_OLD", expires="2020-01-01T00:00:00Z")
    fake = FakeResy(stale, details(token="BOOK_TOKEN_NEW"))
    h = Harness(fake)
    booking_id = h.pause()

    h.confirm(booking_id)

    assert [b["commit"] for b in fake.details_bodies] == [1, 1]
    assert fake.details_bodies[1]["config_id"] == TOKEN
    assert fake.book_forms[0]["book_token"] == ["BOOK_TOKEN_NEW"]
    assert h.row(booking_id).status == "confirmed"


def test_reissue_that_is_no_longer_free_fails_without_booking() -> None:
    stale = details(expires="2020-01-01T00:00:00Z")
    fake = FakeResy(stale, details(config_type="deposit"))
    h = Harness(fake)
    booking_id = h.pause()

    resumed = _parse_sse(h.confirm(booking_id).text)

    assert fake.book_forms == []
    row = h.row(booking_id)
    assert (row.status, row.error_code) == ("failed", "slot_unavailable")
    assert "failed" in _tokens(resumed)
