"""Agent tools, one file per tool, plus the search helpers they share."""

from langchain_core.tools import BaseTool

from app.agent.tools.get_venue_calendar import make_get_venue_calendar_tool
from app.agent.tools.get_venue_details import make_get_venue_details_tool
from app.agent.tools.search_availability import make_search_availability_tool
from app.resy.client import ResyClient


def make_tools(resy: ResyClient) -> list[BaseTool]:
    return [
        make_search_availability_tool(resy),
        make_get_venue_details_tool(resy),
        make_get_venue_calendar_tool(resy),
    ]
