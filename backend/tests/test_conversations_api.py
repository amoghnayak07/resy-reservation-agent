import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import build_graph
from app.api.deps import get_conversation_repository, get_graph
from app.main import create_app
from tests.fakes import FakeConversationRepository

SESSION_A = str(uuid.uuid4())
SESSION_B = str(uuid.uuid4())


@pytest.fixture
def repo() -> FakeConversationRepository:
    return FakeConversationRepository()


@pytest.fixture
def app(repo: FakeConversationRepository) -> Iterator[FastAPI]:
    llm = FakeListChatModel(responses=["hello there"])
    graph = build_graph(MemorySaver(), llm=llm)

    fastapi_app = create_app()
    fastapi_app.dependency_overrides[get_conversation_repository] = lambda: repo
    fastapi_app.dependency_overrides[get_graph] = lambda: graph
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_create_conversation(client: TestClient) -> None:
    response = client.post("/api/conversations", headers={"X-Session-Id": SESSION_A})
    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["message_count"] == 0
    assert body["title"] is None


def test_missing_session_id_returns_400(client: TestClient) -> None:
    response = client.get("/api/conversations")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_session"


def test_list_conversations_is_session_scoped(client: TestClient) -> None:
    client.post("/api/conversations", headers={"X-Session-Id": SESSION_A})
    client.post("/api/conversations", headers={"X-Session-Id": SESSION_B})

    response_a = client.get("/api/conversations", headers={"X-Session-Id": SESSION_A})
    assert response_a.status_code == 200
    assert len(response_a.json()) == 1

    response_b = client.get("/api/conversations", headers={"X-Session-Id": SESSION_B})
    assert response_b.status_code == 200
    assert len(response_b.json()) == 1


def test_other_session_gets_404_for_conversation_messages(client: TestClient) -> None:
    create_response = client.post("/api/conversations", headers={"X-Session-Id": SESSION_A})
    conversation_id = create_response.json()["id"]

    own_response = client.get(
        f"/api/conversations/{conversation_id}/messages", headers={"X-Session-Id": SESSION_A}
    )
    assert own_response.status_code == 200

    other_response = client.get(
        f"/api/conversations/{conversation_id}/messages", headers={"X-Session-Id": SESSION_B}
    )
    assert other_response.status_code == 404
    assert other_response.json()["error"]["code"] == "not_found"


async def test_conversation_messages_hide_system_prompt_and_show_human_and_ai(
    app: FastAPI, client: TestClient
) -> None:
    create_response = client.post("/api/conversations", headers={"X-Session-Id": SESSION_A})
    conversation_id = create_response.json()["id"]

    llm = FakeListChatModel(responses=["hello there"])
    graph = build_graph(MemorySaver(), llm=llm)
    app.dependency_overrides[get_graph] = lambda: graph
    config: RunnableConfig = {
        "configurable": {"thread_id": conversation_id, "timezone": "America/New_York"}
    }
    await graph.ainvoke({"messages": [("user", "hi there")]}, config=config)

    response = client.get(
        f"/api/conversations/{conversation_id}/messages", headers={"X-Session-Id": SESSION_A}
    )
    assert response.status_code == 200
    messages = response.json()
    assert messages == [
        {"role": "human", "content": "hi there"},
        {"role": "ai", "content": "hello there"},
    ]
