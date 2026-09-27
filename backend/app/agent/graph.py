from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from app.agent.nodes import build_call_model
from app.agent.state import AgentState
from app.config import settings

# Separate from the SQLAlchemy pool (app/db/engine.py): the checkpointer only accepts
# psycopg connections. Both pools open in the FastAPI lifespan; see CLAUDE.md -> Database.
CHECKPOINTER_POOL_MIN_SIZE = 2
CHECKPOINTER_POOL_MAX_SIZE = 3


def build_graph(
    checkpointer: BaseCheckpointSaver,
    llm: BaseChatModel | None = None,
    tools: Sequence[BaseTool] = (),
) -> CompiledStateGraph:
    """call_model → (tools → call_model)* → END. Without tools, call_model → END."""
    llm = llm or ChatOpenAI(
        model=settings.openai_model,
        api_key=SecretStr(settings.openai_api_key),
        streaming=True,
        stream_usage=True,
        timeout=30,
        max_retries=2,
    )
    graph = StateGraph(AgentState)
    graph.add_node("call_model", build_call_model(llm, tools))
    graph.add_edge(START, "call_model")
    if tools:
        # Tools catch ResyError themselves; any other tool exception becomes an error
        # ToolMessage the model can relay instead of failing the turn.
        graph.add_node("tools", ToolNode(list(tools), handle_tool_errors=True))
        graph.add_conditional_edges("call_model", tools_condition, {"tools": "tools", END: END})
        graph.add_edge("tools", "call_model")
    else:
        graph.add_edge("call_model", END)
    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def open_checkpointer() -> AsyncGenerator[AsyncPostgresSaver]:
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        conninfo=settings.database_url_psycopg,
        min_size=CHECKPOINTER_POOL_MIN_SIZE,
        max_size=CHECKPOINTER_POOL_MAX_SIZE,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    async with pool:
        checkpointer = AsyncPostgresSaver(conn=pool)
        await checkpointer.setup()
        yield checkpointer
