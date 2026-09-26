from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import SecretStr

from app.agent.nodes import build_call_model
from app.agent.state import AgentState
from app.config import settings


def build_graph(
    checkpointer: BaseCheckpointSaver, llm: BaseChatModel | None = None
) -> CompiledStateGraph:
    llm = llm or ChatOpenAI(
        model=settings.openai_model,
        api_key=SecretStr(settings.openai_api_key),
        streaming=True,
        stream_usage=True,
        timeout=30,
        max_retries=2,
    )
    graph = StateGraph(AgentState)
    graph.add_node("call_model", build_call_model(llm))
    graph.add_edge(START, "call_model")
    graph.add_edge("call_model", END)
    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def open_checkpointer() -> AsyncGenerator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(settings.database_url_psycopg) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
