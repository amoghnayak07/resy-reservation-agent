"""prepare_booking against recorded /3/details fixtures via a mocked transport. Nothing
reaches api.resy.com, /3/book is never called, and no reservation is made."""

import copy
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from langchain_core.runnables import RunnableConfig

from app.agent.tools.prepare_booking import SLOT_EXPIRED, make_prepare_booking_tool
from app.agent.tools.slot_ids import SLOT_ID_TTL_SECONDS, SlotIdMap, SlotRef
from app.db.models import PendingBooking
from app.resy.client import ResyClient
from tests.fakes import FakePendingBookingRepository
from tests.test_search_tool import NY, fixture

NOW = datetime(2026, 10, 20, 14, 0, tzinfo=NY)
CONVERSATION_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())
TOKEN = "rgs://resy/87134/4257306/2/2026-10-22/2026-10-22/19:00:00/2/Dining Room"
URL = "https://resy.com/cities/new-york-ny/venues/brooklyn-chop-house-downtown-fidi"
CONFIG: RunnableConfig = {
    "configurable": {
        "thread_id": CONVERSATION_ID,
        "session_id": SESSION_ID,
        "timezone": "America/New_York",
    }
}


def slot_ref(**overrides: Any) -> SlotRef:
    fields: dict[str, Any] = {
        "config_token": TOKEN,
        "venue_id": 87134,
        "venue_name": "Brooklyn Chop House - Downtown FIDI",
        "party_size": 2,
        "start": datetime(2026, 10, 22, 19, tzinfo=NY),
        "seating_type": "Dining Room",
        "bookable": True,
        "requires_payment": False,
        "venue_url": URL,
    }
    return SlotRef(**(fields | overrides))


class Harness:
    def __init__(self, details: Any = None, *, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.bookings = FakePendingBookingRepository()
        self.slot_map = SlotIdMap()
        self._details = details
        self._status = status

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/details", "prepare_booking may only call /3/details"
        self.requests.append(request)
        return httpx.Response(self._status, json=self._details or {})

    async def call(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        resy = ResyClient(
            api_key="k",
            auth_token="t",
            writes_enabled=False,
            transport=httpx.MockTransport(self.handler),
            retry_backoff_s=0,
        )
        tool = make_prepare_booking_tool(
            resy, self.bookings, slot_map=self.slot_map, now_fn=lambda tz: NOW.astimezone(tz)
        )
        async with resy:
            output = await tool.ainvoke(args, config=CONFIG)
        return output, json.loads(output)

    def add_slot(self, **overrides: Any) -> str:
        return self.slot_map.add(CONVERSATION_ID, slot_ref(**overrides))


async def test_free_slot_creates_pending_booking_with_summary() -> None:
    h = Harness(fixture("details-commit1.json"))
    slot_id = h.add_slot()

    output, result = await h.call({"slot_id": slot_id})

    body = json.loads(h.requests[0].content)
    assert body == {"commit": 1, "config_id": TOKEN, "day": "2026-10-22", "party_size": 2}
    assert result["restaurant"] == "Brooklyn Chop House - Downtown FIDI"
    assert (result["date"], result["weekday"], result["time"]) == (
        "2026-10-22",
        "Thursday",
        "19:00",
    )
    assert result["party_size"] == 2
    assert result["seating"] == "Dining Room"
    assert result["cost"] == "free"
    assert result["cancellation_policy"].startswith("While you won't be charged")
    assert result["free_cancellation_until"] == "12:00 PM Oct 22"
    assert result["changes_allowed_until"] == "12:00 PM Oct 22"
    assert result["hold_expires"] == "2:10 PM Oct 20"
    assert "not booked" in result["status"]
    assert "SCRUBBED_BOOK_TOKEN" not in output
    assert "rgs://" not in output

    (row,) = h.bookings.rows.values()
    assert str(row.id) == result["pending_booking_id"]
    assert row.book_token == "SCRUBBED_BOOK_TOKEN"
    assert row.book_token_expires is not None
    assert row.status == "pending"
    assert row.expires_at == NOW + timedelta(minutes=10)
    assert row.payment_type == "free"


async def test_session_and_conversation_come_from_run_config_not_args() -> None:
    h = Harness(fixture("details-commit1.json"))
    slot_id = h.add_slot()

    await h.call({"slot_id": slot_id, "session_id": "attacker", "thread_id": "other"})

    (row,) = h.bookings.rows.values()
    assert row.session_id == SESSION_ID
    assert row.conversation_id == uuid.UUID(CONVERSATION_ID)


async def test_missing_or_expired_slot_id_says_search_again() -> None:
    h = Harness()
    # Relative to the monotonic clock: its zero is arbitrary (e.g. boot time on a fresh CI runner).
    h.slot_map.add(CONVERSATION_ID, slot_ref(), now=time.monotonic() - SLOT_ID_TTL_SECONDS - 1)

    _, missing = await h.call({"slot_id": "s99"})
    _, expired = await h.call({"slot_id": "s1"})

    assert missing["message"] == expired["message"] == SLOT_EXPIRED
    assert not h.requests
    assert not h.bookings.rows


async def test_slot_from_another_conversation_is_not_found() -> None:
    h = Harness()
    h.slot_map.add(str(uuid.uuid4()), slot_ref())
    _, result = await h.call({"slot_id": "s1"})
    assert result["error"] == "slot_expired"
    assert not h.requests


@pytest.mark.parametrize(
    ("overrides", "code"),
    [({"requires_payment": True}, "requires_payment"), ({"bookable": False}, "not_bookable")],
)
async def test_unbookable_slots_are_refused_before_calling_details(
    overrides: dict[str, Any], code: str
) -> None:
    h = Harness()
    slot_id = h.add_slot(**overrides)
    _, result = await h.call({"slot_id": slot_id})
    assert result["error"] == code
    assert result["url"] == URL
    assert not h.requests
    assert not h.bookings.rows


def _paid(path: tuple[str, ...], value: Any) -> Any:
    details = copy.deepcopy(fixture("details-commit1.json"))
    node = details
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return details


@pytest.mark.parametrize(
    ("details", "reason"),
    [
        (_paid(("payment", "config", "type"), "deposit"), "card or payment"),
        (_paid(("payment", "amounts", "total"), 50.0), "$50.00"),
        (_paid(("cancellation", "fee"), {"amount": 25.0}), "cancellation fee"),
    ],
)
async def test_paid_reservations_are_refused_with_resy_link(details: Any, reason: str) -> None:
    h = Harness(details)
    slot_id = h.add_slot()
    output, result = await h.call({"slot_id": slot_id})
    assert result["error"] == "requires_payment"
    assert reason in result["message"]
    assert result["url"] == URL
    assert "SCRUBBED_BOOK_TOKEN" not in output
    assert not h.bookings.rows


async def test_details_without_book_token_creates_nothing() -> None:
    h = Harness(fixture("details-commit0.json"))
    slot_id = h.add_slot()
    _, result = await h.call({"slot_id": slot_id})
    assert result["error"] == "not_held"
    assert not h.bookings.rows


async def test_resy_error_creates_nothing_and_is_not_retried_as_booking() -> None:
    h = Harness(status=404)
    slot_id = h.add_slot()
    _, result = await h.call({"slot_id": slot_id})
    assert result["error"] == "not_held"
    assert not h.bookings.rows


async def test_reading_an_expired_row_marks_it_expired() -> None:
    repo = FakePendingBookingRepository()
    booking = PendingBooking(
        id=uuid.uuid4(),
        conversation_id=uuid.UUID(CONVERSATION_ID),
        session_id=SESSION_ID,
        venue_id=1,
        venue_name="Test",
        slot_start=NOW + timedelta(days=2),
        party_size=2,
        book_token="x",
        status="pending",
        expires_at=NOW + timedelta(minutes=10),
    )
    await repo.create(booking)
    owner = {"session_id": SESSION_ID, "conversation_id": uuid.UUID(CONVERSATION_ID)}

    fresh = await repo.get_owned(booking.id, now=NOW, **owner)
    assert fresh is not None and fresh.status == "pending"

    later = await repo.get_owned(booking.id, now=NOW + timedelta(minutes=11), **owner)
    assert later is not None and later.status == "expired"

    wrong_session = {**owner, "session_id": str(uuid.uuid4())}
    assert await repo.get_owned(booking.id, now=NOW, **wrong_session) is None
