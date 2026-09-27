"""get_venue_details and get_venue_calendar against recorded fixtures via a mocked transport.
Nothing reaches api.resy.com, and /3/details is never called."""

import copy
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import build_graph
from app.agent.tools.get_venue_calendar import make_get_venue_calendar_tool
from app.agent.tools.get_venue_details import make_get_venue_details_tool
from app.agent.tools.search_availability import make_search_availability_tool
from app.agent.tools.slot_ids import SlotIdMap
from app.resy.client import ResyClient
from tests.fakes import scripted_model
from tests.test_search_tool import LOWER_MANHATTAN, NY, fixture

NOW = datetime(2026, 9, 27, 14, 0, tzinfo=NY)  # calendar fixture starts 2026-09-27
OUTPUT_BUDGET_CHARS = 2000

Handler = Callable[[httpx.Request], httpx.Response]


def resy_client(handler: Handler) -> ResyClient:
    def guarded(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/3/details", "venue tools must never call /3/details"
        return handler(request)

    return ResyClient(
        api_key="k",
        auth_token="t",
        writes_enabled=False,
        transport=httpx.MockTransport(guarded),
        retry_backoff_s=0,
    )


def serve(path: str, body: Any, seen: list[httpx.Request] | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        assert request.url.path == path
        return httpx.Response(200, json=body)

    return handler


CONFIG: RunnableConfig = {"configurable": {"thread_id": "conv-1", "timezone": "America/New_York"}}


def _clock(tz: Any) -> datetime:
    return NOW.astimezone(tz)


async def details(body: Any) -> tuple[str, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    resy = resy_client(serve("/3/venue", body, seen))
    tool = make_get_venue_details_tool(resy, now_fn=_clock)
    async with resy:
        output = await tool.ainvoke({"venue_id": 87134}, config=CONFIG)
    return output, seen


async def calendar(
    body: Any, *, party_size: int = 2, start_date: str | None = None
) -> tuple[dict[str, Any], list[httpx.Request]]:
    seen: list[httpx.Request] = []
    resy = resy_client(serve("/4/venue/calendar", body, seen))
    tool = make_get_venue_calendar_tool(resy, now_fn=_clock)
    args: dict[str, Any] = {"venue_id": 87134, "party_size": party_size}
    if start_date:
        args["start_date"] = start_date
    async with resy:
        output = await tool.ainvoke(args, config=CONFIG)
    assert len(output) < OUTPUT_BUDGET_CHARS
    return json.loads(output), seen


# --- get_venue_details ------------------------------------------------------------------


async def test_details_are_compact_and_token_free() -> None:
    output, seen = await details(fixture("venue-by-id.json"))
    result = json.loads(output)

    assert seen[0].url.params["id"] == "87134"
    assert result["venue_id"] == 87134
    assert result["name"] == "Brooklyn Chop House - Downtown FIDI"
    assert result["neighborhood"] == "Financial District"
    assert result["url"].startswith("https://resy.com/")
    assert "description" in result
    assert len(output) < OUTPUT_BUDGET_CHARS
    for secret in ("rgs://", "token", "lat", "lng", "google"):
        assert secret not in output


async def test_long_description_is_truncated() -> None:
    body = copy.deepcopy(fixture("venue-by-id.json"))
    for entry in body["content"]:
        if entry["name"] == "why_we_like_it":
            entry["body"] = "Great food and warm service. " * 60
    result = json.loads((await details(body))[0])
    assert len(result["description"]) <= 500
    assert result["description"].endswith("…")


async def test_details_resy_error_is_friendly() -> None:
    resy = resy_client(lambda request: httpx.Response(503))
    tool = make_get_venue_details_tool(resy, now_fn=_clock)
    async with resy:
        result = json.loads(await tool.ainvoke({"venue_id": 87134}, config=CONFIG))
    assert result["error"] == "resy_unavailable"


# --- get_venue_calendar -----------------------------------------------------------------


async def test_calendar_splits_sold_out_from_available_and_caps_lists() -> None:
    result, seen = await calendar(fixture("venue-calendar.json"))

    params = seen[0].url.params
    assert (params["venue_id"], params["num_seats"]) == ("87134", "2")
    assert (params["start_date"], params["end_date"]) == ("2026-09-27", "2026-12-26")

    assert result["reservations_open_through"] == "2026-10-31"
    assert result["sold_out_dates"] == ["2026-09-27"]  # released, fully booked
    assert result["available_dates"][0] == "2026-09-28"
    assert len(result["available_dates"]) == 14
    assert result["available_count"] == 34
    assert result["unavailable_count"] == 0
    assert "not released" in result["note"]
    assert "not_released_yet" not in result


async def test_unknown_inventory_value_is_not_available() -> None:
    body = copy.deepcopy(fixture("venue-calendar.json"))
    body["scheduled"][1]["inventory"]["reservation"] = "waitlist-only"
    result, _ = await calendar(body)
    assert "2026-09-28" not in result["available_dates"]
    assert result["available_count"] == 33
    assert result["unavailable_count"] == 1


async def test_start_date_past_release_horizon_is_not_released_yet() -> None:
    body = copy.deepcopy(fixture("venue-calendar.json"))
    body["scheduled"] = []
    result, seen = await calendar(body, start_date="2026-11-20")
    assert seen[0].url.params["start_date"] == "2026-11-20"
    assert result["not_released_yet"] is True
    assert "available_dates" not in result
    assert "2026-10-31" in result["note"]


async def test_past_start_date_is_clamped_to_today() -> None:
    _, seen = await calendar(fixture("venue-calendar.json"), start_date="2026-09-01")
    assert seen[0].url.params["start_date"] == "2026-09-27"


# --- graph ------------------------------------------------------------------------------


async def test_graph_checks_calendar_after_empty_search_then_searches_by_venue_id() -> None:
    empty_find = copy.deepcopy(fixture("venue-find.json"))
    empty_find["results"]["venues"] = []
    finds = iter([empty_find, fixture("venue-find.json")])
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/3/venue":
            return httpx.Response(200, json=fixture("venue-by-id.json"))
        if request.url.path == "/4/find":
            return httpx.Response(200, json=next(finds))
        if request.url.path == "/4/venue/calendar":
            return httpx.Response(200, json=fixture("venue-calendar.json"))
        raise AssertionError(f"unexpected {request.url.path}")

    def search_call(date: str, call_id: str) -> AIMessage:
        args = {"venue_id": 87134, "date": date, "party_size": 2}
        return AIMessage(
            content="", tool_calls=[{"name": "search_availability", "args": args, "id": call_id}]
        )

    llm = scripted_model(
        search_call("2026-09-29", "c1"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "get_venue_calendar",
                    "args": {"venue_id": 87134, "party_size": 2},
                    "id": "c2",
                }
            ],
        ),
        search_call("2026-10-22", "c3"),
        AIMessage(content="Here are the open times on Oct 22."),
    )
    resy = resy_client(handler)
    tools = [
        make_search_availability_tool(resy, slot_map=SlotIdMap(), now_fn=_clock),
        make_get_venue_calendar_tool(resy, now_fn=_clock),
    ]
    graph = build_graph(MemorySaver(), llm=llm, tools=tools)
    config: RunnableConfig = {
        "configurable": {
            "thread_id": "conv-1",
            "timezone": "America/New_York",
            "location": LOWER_MANHATTAN,
        }
    }

    async with resy:
        result = await graph.ainvoke({"messages": [("user", "table for 2 at BCH")]}, config=config)

    assert paths == ["/3/venue", "/4/find", "/4/venue/calendar", "/3/venue", "/4/find"]
    tool_results = [
        json.loads(str(m.content)) for m in result["messages"] if isinstance(m, ToolMessage)
    ]
    assert tool_results[0]["venues"][0]["slots"] == []
    assert tool_results[1]["available_count"] == 34
    assert tool_results[2]["venues"][0]["slots"]
    assert result["messages"][-1].content == "Here are the open times on Oct 22."
