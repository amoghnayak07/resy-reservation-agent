from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import build_graph
from app.agent.nodes import latest_city
from tests.fakes import scripted_model

TZ_CONFIGURABLE = {"timezone": "America/New_York"}


async def test_graph_returns_a_reply() -> None:
    llm = FakeListChatModel(responses=["hello there"])
    graph = build_graph(MemorySaver(), llm=llm)
    config: RunnableConfig = {"configurable": {"thread_id": "conv-a", **TZ_CONFIGURABLE}}

    result = await graph.ainvoke({"messages": [("user", "hi")]}, config=config)

    assert result["messages"][-1].content == "hello there"


async def test_graph_keeps_multi_turn_history_per_thread() -> None:
    llm = FakeListChatModel(responses=["first reply", "second reply"])
    checkpointer = MemorySaver()
    graph = build_graph(checkpointer, llm=llm)
    config: RunnableConfig = {"configurable": {"thread_id": "conv-a", **TZ_CONFIGURABLE}}

    await graph.ainvoke({"messages": [("user", "turn one")]}, config=config)
    result = await graph.ainvoke({"messages": [("user", "turn two")]}, config=config)

    assert [m.content for m in result["messages"]] == [
        "turn one",
        "first reply",
        "turn two",
        "second reply",
    ]


async def test_graph_isolates_history_between_threads() -> None:
    llm = FakeListChatModel(responses=["reply a", "reply b"])
    checkpointer = MemorySaver()
    graph = build_graph(checkpointer, llm=llm)
    config_a: RunnableConfig = {"configurable": {"thread_id": "conv-a", **TZ_CONFIGURABLE}}
    config_b: RunnableConfig = {"configurable": {"thread_id": "conv-b", **TZ_CONFIGURABLE}}

    await graph.ainvoke({"messages": [("user", "hi from a")]}, config=config_a)
    result_b = await graph.ainvoke({"messages": [("user", "hi from b")]}, config=config_b)

    assert [m.content for m in result_b["messages"]] == ["hi from b", "reply b"]


@tool
async def search_availability(cuisine: str) -> str:
    """Stand-in search tool."""
    return '{"mode": "cuisine", "city": "New York", "venues": []}'


@tool
async def broken_tool(cuisine: str) -> str:
    """Stand-in tool that fails unexpectedly."""
    raise RuntimeError("boom")


async def test_tool_loop_routes_model_tools_model_end() -> None:
    llm = scripted_model(
        AIMessage(
            content="",
            tool_calls=[{"name": "search_availability", "args": {"cuisine": "thai"}, "id": "c1"}],
        ),
        AIMessage(content="Nothing open nearby."),
    )
    graph = build_graph(MemorySaver(), llm=llm, tools=[search_availability])
    config: RunnableConfig = {"configurable": {"thread_id": "t", **TZ_CONFIGURABLE}}

    result = await graph.ainvoke({"messages": [("user", "thai tonight")]}, config=config)

    kinds = [type(m).__name__ for m in result["messages"]]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert result["messages"][-1].content == "Nothing open nearby."
    assert latest_city(result["messages"]) == "New York"


async def test_unexpected_tool_error_becomes_tool_message() -> None:
    llm = scripted_model(
        AIMessage(
            content="",
            tool_calls=[{"name": "broken_tool", "args": {"cuisine": "thai"}, "id": "c1"}],
        ),
        AIMessage(content="Sorry, something went wrong."),
    )
    graph = build_graph(MemorySaver(), llm=llm, tools=[broken_tool])
    config: RunnableConfig = {"configurable": {"thread_id": "t", **TZ_CONFIGURABLE}}

    result = await graph.ainvoke({"messages": [("user", "thai")]}, config=config)

    tool_message = result["messages"][2]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.status == "error"
    assert result["messages"][-1].content == "Sorry, something went wrong."
