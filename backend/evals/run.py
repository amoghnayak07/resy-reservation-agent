"""Manual eval runner (stage 11). Runs `cases.json` against the REAL model with stub tools: the
tools have the production names and argument schemas, but return canned outputs, so nothing
reaches Resy. Costs OpenAI money, so it's not in CI.

    uv run python -m evals.run --model gpt-6-sol
    uv run python -m evals.run --model gpt-6-luna --case route_full_exact

Case format (see cases.json):
  turns            user messages, in order
  timezone         IANA name (default America/New_York)
  location         "ny" | "la" | null (null = location not shared)
  now              optional fixed local time "YYYY-MM-DDTHH:MM" (default: real now)
  tools            tool name -> output (or list of outputs, one per call; last repeats).
                   "@name" refers to cases.json "fixtures"; "{arg:x}" echoes call argument x.
  expect:
    calls_include  [{name, args?, turn?}]  some call matches (string args: case-insensitive
                                           substring; values may be a list = any of)
    calls_exclude  [name | {name, args?, turn?}]  no call matches
    order          [names]                 first calls appear in this order
    same_turn      [names]                 all called within one turn
    reply_any      [words]                 final reply contains at least one
    reply_all      [[words], ...]          final reply matches each group
    reply_none     [words]                 final reply contains none
Date placeholders in args: {today}, {tomorrow}, {next:Friday}, {next_md:09-28}.
Every case also checks: `book` only after `prepare_booking`, and no booking claimed.
"""

import argparse
import asyncio
import json
import re
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt
from pydantic import BaseModel, SecretStr

from app.agent import nodes
from app.agent.graph import build_graph
from app.agent.tools import book, get_venue_calendar, get_venue_details, prepare_booking
from app.agent.tools import search_availability as search
from app.config import settings
from app.observability.pricing import compute_cost

CASES_FILE = Path(__file__).with_name("cases.json")
LOCATIONS = {"ny": {"lat": 40.713, "lng": -74.006}, "la": {"lat": 34.052, "lng": -118.244}}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
BOOKING_CLAIMS = ["is booked", "has been booked", "you're booked", "reservation is confirmed"]
TOOL_SPECS: list[tuple[str, str, type[BaseModel]]] = [
    (search.TOOL_NAME, search.DESCRIPTION, search.SearchAvailabilityArgs),
    (
        get_venue_details.TOOL_NAME,
        get_venue_details.DESCRIPTION,
        get_venue_details.GetVenueDetailsArgs,
    ),
    (
        get_venue_calendar.TOOL_NAME,
        get_venue_calendar.DESCRIPTION,
        get_venue_calendar.GetVenueCalendarArgs,
    ),
    (prepare_booking.TOOL_NAME, prepare_booking.DESCRIPTION, prepare_booking.PrepareBookingArgs),
    (book.TOOL_NAME, book.DESCRIPTION, book.BookArgs),
]


# --- dates ----------------------------------------------------------------------------------


def resolve_date(token: str, today: date) -> str | list[str] | None:
    if token == "today":
        return today.isoformat()
    if token == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if token.startswith("next:"):
        ahead = (WEEKDAYS.index(token[5:].lower()) - today.weekday()) % 7
        if ahead == 0:  # "Friday" on a Friday is ambiguous: accept today or next week
            return [today.isoformat(), (today + timedelta(days=7)).isoformat()]
        return (today + timedelta(days=ahead)).isoformat()
    if token.startswith("next_md:"):
        month, day = (int(p) for p in token[8:].split("-"))
        candidate = date(today.year, month, day)
        return (candidate if candidate >= today else date(today.year + 1, month, day)).isoformat()
    return None


def fill(value: Any, today: date, call_args: dict[str, Any] | None = None) -> Any:
    """Resolves whole-string placeholders ({today}, {arg:x}, …) recursively."""
    if isinstance(value, dict):
        return {k: fill(v, today, call_args) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, today, call_args) for v in value]
    if isinstance(value, str) and (m := re.fullmatch(r"\{(.+)\}", value)):
        token = m.group(1)
        if token.startswith("arg:"):
            return (call_args or {}).get(token[4:])
        resolved = resolve_date(token, today)
        return value if resolved is None else resolved
    return value


# --- stub tools -----------------------------------------------------------------------------


class Recorder:
    def __init__(self, case: dict[str, Any], fixtures: dict[str, Any], today: date) -> None:
        self.calls: list[tuple[int, str, dict[str, Any]]] = []
        self.turn = 0
        self._outputs = case.get("tools", {})
        self._fixtures = fixtures
        self._today = today
        self._counts: dict[str, int] = {}

    def output_for(self, name: str, args: dict[str, Any]) -> Any:
        planned = self._outputs.get(name, f"@default_{name}")
        if isinstance(planned, list):
            index = self._counts.get(name, 0)
            planned = planned[min(index, len(planned) - 1)]
        self._counts[name] = self._counts.get(name, 0) + 1
        if isinstance(planned, str) and planned.startswith("@"):
            planned = self._fixtures[planned[1:]]
        return fill(planned, self._today, args)

    def tools(self) -> list[BaseTool]:
        return [self._stub(name, description, schema) for name, description, schema in TOOL_SPECS]

    def _stub(self, name: str, description: str, schema: type[BaseModel]) -> BaseTool:
        async def run(config: RunnableConfig, **kwargs: Any) -> str:
            args = json.loads(json.dumps(kwargs, default=str))
            self.calls.append((self.turn, name, args))
            output = self.output_for(name, args)
            if name == book.TOOL_NAME:
                interrupt({"pending_booking_id": args.get("pending_booking_id")})  # the card
            return json.dumps(output)

        return StructuredTool.from_function(
            coroutine=run, name=name, description=description, args_schema=schema
        )


# --- checks ---------------------------------------------------------------------------------


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches(e, actual) for e in expected)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.casefold() in actual.casefold()
    return expected == actual


def _call_matches(spec: str | dict[str, Any], call: tuple[int, str, dict[str, Any]]) -> bool:
    turn, name, args = call
    if isinstance(spec, str):
        return name == spec
    if spec["name"] != name or ("turn" in spec and spec["turn"] != turn):
        return False
    return all(k in args and _matches(v, args[k]) for k, v in spec.get("args", {}).items())


def check(case: dict[str, Any], rec: Recorder, reply: str, today: date) -> list[str]:
    expect = fill(case.get("expect", {}), today)
    calls = rec.calls
    names = [name for _, name, _ in calls]
    text = reply.casefold()
    failures: list[str] = []

    for spec in expect.get("calls_include", []):
        if not any(_call_matches(spec, c) for c in calls):
            failures.append(f"missing call {json.dumps(spec)}")
    for spec in expect.get("calls_exclude", []):
        if any(_call_matches(spec, c) for c in calls):
            failures.append(f"unexpected call {json.dumps(spec)}")
    order = expect.get("order", [])
    firsts = [names.index(n) if n in names else -1 for n in order]
    if order and (-1 in firsts or firsts != sorted(firsts)):
        failures.append(f"order {order} not followed: {names}")
    same = expect.get("same_turn", [])
    if same and not any(
        all((t, n) in {(c[0], c[1]) for c in calls} for n in same) for t in {c[0] for c in calls}
    ):
        failures.append(f"{same} not called in one turn")
    if expect.get("reply_any") and not any(w.casefold() in text for w in expect["reply_any"]):
        failures.append(f"reply lacks any of {expect['reply_any']}")
    for group in expect.get("reply_all", []):
        if not any(w.casefold() in text for w in group):
            failures.append(f"reply lacks any of {group}")
    for word in expect.get("reply_none", []):
        if word.casefold() in text:
            failures.append(f"reply contains {word!r}")

    # Invariants for every case.
    if "book" in names and (
        "prepare_booking" not in names or names.index("book") < names.index("prepare_booking")
    ):
        failures.append("book called without a prior prepare_booking")
    if any(claim in text for claim in BOOKING_CLAIMS):
        failures.append("reply claims a booking")
    return failures


# --- runner ---------------------------------------------------------------------------------


class _FrozenClock(datetime):
    fixed: datetime

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return cls.fixed.astimezone(tz)


async def run_case(case: dict[str, Any], fixtures: dict[str, Any], model: str) -> dict[str, Any]:
    tz_name = case.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)
    fixed = datetime.fromisoformat(case["now"]).replace(tzinfo=tz) if case.get("now") else None
    today = (fixed or datetime.now(tz)).date()
    rec = Recorder(case, fixtures, today)
    llm = ChatOpenAI(
        model=model,
        api_key=SecretStr(settings.openai_api_key),
        stream_usage=True,
        timeout=60,
        max_retries=2,
    )
    graph = build_graph(MemorySaver(), llm=llm, tools=rec.tools())
    location_key = case.get("location", "ny")
    location = LOCATIONS[location_key] if location_key else None
    config: RunnableConfig = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "session_id": "eval",
            "timezone": tz_name,
            "location_available": location is not None,
            "location": location,
        },
        "recursion_limit": 12,
    }

    real_datetime = nodes.datetime
    if fixed is not None:
        _FrozenClock.fixed = fixed
        nodes.datetime = _FrozenClock  # the system prompt's "today" (eval-only patch)
    try:
        state: dict[str, Any] = {}
        for index, turn in enumerate(case["turns"]):
            rec.turn = index
            state = await graph.ainvoke({"messages": [("user", turn)]}, config=config)
    finally:
        nodes.datetime = real_datetime

    messages = state.get("messages", [])
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    reply = str(last_ai.content) if last_ai else ""
    input_tokens = cached = output_tokens = 0
    for message in messages:
        usage = getattr(message, "usage_metadata", None) if isinstance(message, AIMessage) else None
        if usage:
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            cached += (usage.get("input_token_details") or {}).get("cache_read", 0)
    return {
        "id": case["id"],
        "failures": check(case, rec, reply, today),
        "calls": [name for _, name, _ in rec.calls],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": float(compute_cost(model, input_tokens, cached, output_tokens)),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=settings.openai_model)
    parser.add_argument("--case", action="append", help="run only these case IDs")
    args = parser.parse_args()

    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    cases = [c for c in data["cases"] if not args.case or c["id"] in args.case]
    results = []
    for case in cases:
        result = await run_case(case, data["fixtures"], args.model)
        results.append(result)
        status = "PASS" if not result["failures"] else "FAIL"
        tokens = f"{result['input_tokens']:>7} in {result['output_tokens']:>6} out"
        print(
            f"{status}  {result['id']:<28} {tokens}  ${result['cost']:.4f}  calls={result['calls']}"
        )
        for failure in result["failures"]:
            print(f"        - {failure}")

    passed = sum(1 for r in results if not r["failures"])
    total_cost = sum(r["cost"] for r in results)
    total_in = sum(r["input_tokens"] for r in results)
    total_out = sum(r["output_tokens"] for r in results)
    print(
        f"\n{args.model}: {passed}/{len(results)} passed, "
        f"{total_in} input + {total_out} output tokens, ${total_cost:.4f}"
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
