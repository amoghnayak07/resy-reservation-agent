from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from app.agent.prompts import build_system_prompt
from app.agent.state import AgentState


def build_call_model(llm: BaseChatModel, tools: Sequence[BaseTool] = ()):
    model = llm.bind_tools(list(tools)) if tools else llm

    async def call_model(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        configurable = config.get("configurable", {})
        # Required: the selected region's timezone (CLAUDE.md rule 11). There is no server-side
        # default -- a missing key here means a caller bug upstream.
        tz = configurable["timezone"]
        now = datetime.now(ZoneInfo(tz))
        system_message = SystemMessage(
            content=build_system_prompt(
                now=now,
                tz=tz,
                region_name=configurable.get("region_name", ""),
                country_name=configurable.get("country_name", ""),
            )
        )
        response = await model.ainvoke([system_message, *state["messages"]])
        return {"messages": [response]}

    return call_model
