import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import build_graph
from app.api.deps import get_conversation_repository, get_graph
from app.main import create_app
from tests.fakes import FakeConversationRepository, RecordingGraph

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
    yield TestClient(app)
    app.dependency_overrides.clear()


def _post_chat(client: TestClient, **body: Any) -> Any:
    payload = {"message": "hi", "timezone": "America/New_York", **body}
    return client.post("/api/chat", json=payload, headers={"X-Session-Id": SESSION_ID})


def test_missing_session_id_returns_400(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "hi", "timezone": "America/New_York"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_session"


def test_invalid_session_id_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json={"message": "hi", "timezone": "America/New_York"},
        headers={"X-Session-Id": "not-a-uuid"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_session"


def test_invalid_timezone_returns_422(client: TestClient) -> None:
    response = _post_chat(client, timezone="Not/A_Zone")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


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


def test_user_location_is_rounded_and_coordinates_never_reach_langfuse(
    client: TestClient, recording_graph: RecordingGraph, mock_langfuse: list[dict[str, Any]]
) -> None:
    response = _post_chat(
        client, user_location={"lat": 40.71234567, "lng": -74.00987654, "accuracy_m": 15}
    )
    assert response.status_code == 200

    assert recording_graph.last_config is not None
    configurable = recording_graph.last_config.get("configurable", {})
    assert configurable["location"] == {"lat": 40.712, "lng": -74.01}

    assert len(mock_langfuse) == 1
    assert mock_langfuse[0]["location_used"] is True
    assert "lat" not in mock_langfuse[0]
    assert "lng" not in mock_langfuse[0]


def test_no_location_sets_location_used_false(
    client: TestClient, recording_graph: RecordingGraph, mock_langfuse: list[dict[str, Any]]
) -> None:
    response = _post_chat(client)
    assert response.status_code == 200
    assert recording_graph.last_config is not None
    configurable = recording_graph.last_config.get("configurable", {})
    assert configurable["location"] is None
    assert mock_langfuse[0]["location_used"] is False


def test_chat_with_unknown_conversation_id_returns_404(client: TestClient) -> None:
    response = _post_chat(client, conversation_id=str(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
