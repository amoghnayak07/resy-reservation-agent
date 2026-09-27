import json
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from app.agent.prompts import build_system_prompt
from app.agent.state import AgentState
from app.agent.tools.search_availability import TOOL_NAME as SEARCH_TOOL_NAME


def latest_city(messages: Sequence[AnyMessage]) -> str | None:
    """City label from the most recent search result (no geocoding; CLAUDE.md)."""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == SEARCH_TOOL_NAME:
            try:
                city = json.loads(str(message.content)).get("city")
            except (ValueError, AttributeError):
                continue
            if isinstance(city, str) and city:
                return city
    return None


def build_call_model(llm: BaseChatModel, tools: Sequence[BaseTool] = ()):
    model = llm.bind_tools(list(tools)) if tools else llm

    async def call_model(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        configurable = config.get("configurable", {})
        # Required: every chat request carries the user's own timezone (CLAUDE.md rule 11).
        # There is no server-side default -- a missing key here means a caller bug upstream.
        tz = configurable["timezone"]
        location_available = configurable.get("location_available", False)
        now = datetime.now(ZoneInfo(tz))
        system_message = SystemMessage(
            content=build_system_prompt(
                now=now,
                tz=tz,
                location_available=location_available,
                city=latest_city(state["messages"]) if location_available else None,
            )
        )
        response = await model.ainvoke([system_message, *state["messages"]])
        return {"messages": [response]}

    return call_model
