import json
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import ANY

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import build_graph
from app.agent.tools.search_availability import make_search_availability_tool
from app.api.deps import (
    get_conversation_repository,
    get_graph,
    get_pending_booking_repository,
    get_region_directory,
    get_spend_guard,
)
from app.config import settings
from app.main import create_app
from app.resy.client import ResyClient
from tests.fakes import (
    FakeConversationRepository,
    FakePendingBookingRepository,
    FakeSpendGuard,
    RecordingGraph,
    fixture_regions,
    scripted_model,
)
from tests.test_search_tool import fixture

SESSION_ID = str(uuid.uuid4())


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event_name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        assert event_name is not None, f"malformed SSE block: {block!r}"
        events.append((event_name, data or {}))
    return events


@pytest.fixture
def recording_graph() -> RecordingGraph:
    llm = FakeListChatModel(responses=["hello there"])
    return RecordingGraph(build_graph(MemorySaver(), llm=llm))


@pytest.fixture
def client(recording_graph: RecordingGraph) -> Iterator[TestClient]:
    # No `with` block: the app's real lifespan (Postgres checkpointer pool, real
    # ChatOpenAI, real Langfuse client) must never run in a unit test. Dependency
    # overrides below replace everything a request actually touches.
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: recording_graph
    app.dependency_overrides[get_conversation_repository] = lambda: FakeConversationRepository()
    app.dependency_overrides[get_spend_guard] = lambda: FakeSpendGuard()
    app.dependency_overrides[get_pending_booking_repository] = lambda: (
        FakePendingBookingRepository()
    )
    regions = fixture_regions()
    app.dependency_overrides[get_region_directory] = lambda: regions
    yield TestClient(app)
    app.dependency_overrides.clear()


def _post_chat(client: TestClient, **body: Any) -> Any:
    payload = {"message": "hi", "region": "new-york-ny", **body}
    return client.post("/api/chat", json=payload, headers={"X-Session-Id": SESSION_ID})


def test_missing_session_id_returns_400(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "hi", "region": "new-york-ny"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_session"


def test_invalid_session_id_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json={"message": "hi", "region": "new-york-ny"},
        headers={"X-Session-Id": "not-a-uuid"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_session"


def test_stream_emits_meta_token_usage_done_in_order(client: TestClient) -> None:
    response = _post_chat(client)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    event_names = [name for name, _ in events]

    assert event_names[0] == "meta"
    assert "token" in event_names
    assert event_names[-2] == "usage"
    assert event_names[-1] == "done"
    assert event_names.index("token") > event_names.index("meta")
    assert event_names.index("usage") > event_names.index("token")

    meta_data = events[0][1]
    assert uuid.UUID(meta_data["conversation_id"])
    assert meta_data["trace_id"] == "fake-trace-id"

    tokens = "".join(data["text"] for name, data in events if name == "token")
    assert tokens == "hello there"


def test_region_resolves_to_center_radius_and_timezone_in_run_config(
    client: TestClient, recording_graph: RecordingGraph, mock_langfuse: list[dict[str, Any]]
) -> None:
    response = _post_chat(client, region="los-angeles-ca")
    assert response.status_code == 200

    assert recording_graph.last_config is not None
    configurable = recording_graph.last_config.get("configurable", {})
    assert configurable["region_slug"] == "los-angeles-ca"
    assert configurable["region_name"] == "Los Angeles"
    assert configurable["timezone"] == "PST8PDT"
    assert configurable["radius_m"] == round(19 * 1609.344)
    assert set(configurable["location"]) == {"lat", "lng"}
    assert configurable["session_id"] == SESSION_ID  # prepare_booking reads it from here

    assert mock_langfuse == [
        {"conversation_id": ANY, "session_id": SESSION_ID, "region": "los-angeles-ca", "tags": None}
    ]  # the slug only: no coordinates reach Langfuse


def test_unknown_region_returns_422(client: TestClient) -> None:
    response = _post_chat(client, region="atlantis")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_region"


def test_malformed_region_returns_422_validation_error(client: TestClient) -> None:
    response = _post_chat(client, region="New York!")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_chat_with_unknown_conversation_id_returns_404(client: TestClient) -> None:
    response = _post_chat(client, conversation_id=str(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_over_daily_budget_returns_429(client: TestClient) -> None:
    client.app.dependency_overrides[get_spend_guard] = lambda: FakeSpendGuard(over_cap=True)  # type: ignore[attr-defined]
    response = _post_chat(client)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "daily_budget_reached"


def test_message_too_long_returns_422_message_too_long(client: TestClient) -> None:
    response = _post_chat(client, message="a" * (settings.max_message_chars + 1))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "message_too_long"


def test_out_of_region_venue_streams_region_change_required(client: TestClient) -> None:
    """A New York venue asked for from the Los Angeles region: the tool reports out_of_area and
    the stream tells the frontend to highlight the region selector."""

    def resy_venue(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/venue"  # never /4/find for an out-of-region venue
        return httpx.Response(200, json=fixture("venue-by-id.json"))

    resy = ResyClient(
        api_key="k", auth_token="t", writes_enabled=False, transport=httpx.MockTransport(resy_venue)
    )
    llm = scripted_model(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "search_availability", "args": {"venue_id": 87134}, "id": "call-1"}
            ],
        ),
        AIMessage(content="That's in New York; change your location first."),
    )
    graph = build_graph(MemorySaver(), llm=llm, tools=[make_search_availability_tool(resy)])
    client.app.dependency_overrides[get_graph] = lambda: graph  # type: ignore[attr-defined]

    events = _parse_sse(_post_chat(client, message="BCH FiDi", region="los-angeles-ca").text)
    event_names = [name for name, _ in events]

    start = event_names.index("tool_start")
    assert event_names[start : start + 3] == ["tool_start", "region_change_required", "tool_end"]
    assert events[start + 1][1] == {"city": "New York"}
    tool_start = events[start][1]
    tool_end = events[start + 2][1]
    assert tool_start["name"] == "search_availability"
    assert tool_end["call_id"] == tool_start["call_id"]
    assert tool_end["ok"] is True
    assert isinstance(tool_end["duration_ms"], int)
    assert event_names[-2:] == ["usage", "done"]


async def test_conversation_at_turn_limit_returns_409(client: TestClient) -> None:
    fake_repo = FakeConversationRepository()
    conversation = await fake_repo.create(SESSION_ID)
    conversation.message_count = settings.max_turns_per_conversation
    client.app.dependency_overrides[get_conversation_repository] = lambda: fake_repo  # type: ignore[attr-defined]

    response = _post_chat(client, conversation_id=str(conversation.id))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conversation_full"
