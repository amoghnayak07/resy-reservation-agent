from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import build_graph

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
