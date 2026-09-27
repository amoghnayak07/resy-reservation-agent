"""Manual eval runner (stage 11). Runs `cases.json` against the REAL model with stub tools: the
tools have the production names and argument schemas, but return canned outputs, so nothing
reaches Resy. Costs OpenAI money, so it's not in CI.

    uv run python -m evals.run --model gpt-6-sol
    uv run python -m evals.run --model gpt-6-luna --case route_full_exact

Case format (see cases.json):
  turns            user messages, in order
  region           Resy city slug (default new-york-ny), from the slimmed city-list fixture;
                   sets the search center, radius, and timezone like production
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
from app.api.message_text import content_to_text
from app.config import settings
from app.observability.pricing import compute_cost
from app.regions import region_config
from app.resy.models import City, LocationConfigCityRaw

CASES_FILE = Path(__file__).with_name("cases.json")
CITY_LIST = Path(__file__).parents[1] / "tests" / "fixtures" / "resy" / "location-config.json"


def load_regions() -> dict[str, City]:
    raw = json.loads(CITY_LIST.read_text(encoding="utf-8"))
    cities = (City.from_raw(LocationConfigCityRaw.model_validate(entry)) for entry in raw)
    return {city.slug: city for city in cities if city is not None}


REGIONS = load_regions()
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# Specific phrases only: "is booked through Tock" or "nothing is booked" aren't claims.
BOOKING_CLAIMS = [
    "you're booked",
    "you are booked",
    "is booked for",
    "been booked for",
    "reservation is confirmed",
    "booking is confirmed",
    "confirmed on resy",
    "i've booked",
    "i booked",
]
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
        self._last_search: Any = None

    def output_for(self, name: str, args: dict[str, Any]) -> Any:
        if name == prepare_booking.TOOL_NAME and name not in self._outputs:
            prepared = self._prepared_from_search(args.get("slot_id"))
            if prepared is not None:
                return prepared
        planned = self._outputs.get(name, f"@default_{name}")
        if isinstance(planned, list):
            index = self._counts.get(name, 0)
            planned = planned[min(index, len(planned) - 1)]
        self._counts[name] = self._counts.get(name, 0) + 1
        if isinstance(planned, str) and planned.startswith("@"):
            planned = self._fixtures[planned[1:]]
        output = fill(planned, self._today, args)
        if name == search.TOOL_NAME:
            self._last_search = output
        return output

    def _prepared_from_search(self, slot_id: Any) -> dict[str, Any] | None:
        """A prepare_booking result matching the slot the model picked from the last search
        (a fixed canned summary would contradict the request, and the model rightly stops)."""
        search_out = self._last_search if isinstance(self._last_search, dict) else {}
        for venue in search_out.get("venues", []):
            for slot in venue.get("slots", []):
                if slot.get("slot_id") == slot_id:
                    base = fill(self._fixtures["default_prepare_booking"], self._today)
                    base.pop("address", None)  # the fixture's address belongs to another venue
                    return base | {
                        "restaurant": venue.get("name"),
                        "date": search_out.get("date"),
                        "time": slot.get("time"),
                        "party_size": search_out.get("party_size"),
                        "seating": slot.get("seating"),
                    }
        return None

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


def _norm(text: str) -> str:
    """Casefold and straighten curly quotes, so "Joe’s" matches "joe's"."""
    return text.casefold().translate(str.maketrans("‘’“”", "''\"\""))


def _unnegated(phrase: str, text: str) -> bool:
    """`phrase` occurs without a negation just before it ("isn't fully booked" doesn't count)."""
    for match in re.finditer(re.escape(_norm(phrase)), text):
        before = text[max(0, match.start() - 20) : match.start()]
        if not re.search(r"\b(nothing|not|no|never)\b|n't\b", before):
            return True
    return False


def _claims_booking(text: str) -> bool:
    return any(_unnegated(claim, text) for claim in BOOKING_CLAIMS)


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches(e, actual) for e in expected)
    if isinstance(expected, str) and isinstance(actual, str):
        return _norm(expected) in _norm(actual)
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
    text = _norm(reply)
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
    if expect.get("reply_any") and not any(_norm(w) in text for w in expect["reply_any"]):
        failures.append(f"reply lacks any of {expect['reply_any']}")
    for group in expect.get("reply_all", []):
        if not any(_norm(w) in text for w in group):
            failures.append(f"reply lacks any of {group}")
    for word in expect.get("reply_none", []):
        if _unnegated(word, text):
            failures.append(f"reply contains {word!r}")

    # Invariants for every case.
    if "book" in names and (
        "prepare_booking" not in names or names.index("book") < names.index("prepare_booking")
    ):
        failures.append("book called without a prior prepare_booking")
    prepared_turns = {t for t, n, _ in calls if n == "prepare_booking"}
    booked_turns = {t for t, n, _ in calls if n == "book"}
    if prepared_turns - booked_turns:  # prompt rule: book right after prepare, same turn
        failures.append("prepare_booking not followed by book in the same turn")
    if _claims_booking(text):
        failures.append("reply claims a booking")
    return failures


# --- runner ---------------------------------------------------------------------------------


class _FrozenClock(datetime):
    fixed: datetime

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return cls.fixed.astimezone(tz)


async def run_case(case: dict[str, Any], fixtures: dict[str, Any], model: str) -> dict[str, Any]:
    city = REGIONS[case.get("region", "new-york-ny")]
    tz = ZoneInfo(city.time_zone)
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
    config: RunnableConfig = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "session_id": "eval",
            **region_config(city),
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
    # Text blocks only, as the chat UI shows it (the model also returns reasoning blocks).
    reply = content_to_text(last_ai.content) if last_ai else ""
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
        "reply": reply,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": float(compute_cost(model, input_tokens, cached, output_tokens)),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=settings.openai_model)
    parser.add_argument("--case", action="append", help="run only these case IDs")
    parser.add_argument("--out", type=Path, help="also write results (with replies) as JSON")
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
    if args.out:
        args.out.write_text(
            json.dumps({"model": args.model, "results": results}, indent=2), encoding="utf-8"
        )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
