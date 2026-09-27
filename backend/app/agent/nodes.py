from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agent.prompts import build_system_prompt
from app.agent.state import AgentState


def build_call_model(llm: BaseChatModel):
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
                city=None,  # no geocoding yet (CLAUDE.md); populated once search tools land
            )
        )
        response = await llm.ainvoke([system_message, *state["messages"]])
        return {"messages": [response]}

    return call_model
