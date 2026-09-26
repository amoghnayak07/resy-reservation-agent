from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage

from app.agent.prompts import build_system_prompt
from app.agent.state import AgentState
from app.config import settings


def build_call_model(llm: BaseChatModel):
    async def call_model(state: AgentState) -> dict[str, list[BaseMessage]]:
        now = datetime.now(ZoneInfo(settings.app_timezone))
        system_message = SystemMessage(
            content=build_system_prompt(
                now=now,
                tz=settings.app_timezone,
                location_available=False,
                city=None,
            )
        )
        response = await llm.ainvoke([system_message, *state["messages"]])
        return {"messages": [response]}

    return call_model
