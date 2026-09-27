"""search_availability tool end to end, against recorded fixtures via a mocked transport.
Nothing reaches api.resy.com, and /3/details is never called."""

import copy
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.agent.tools.search_availability import make_search_availability_tool
from app.agent.tools.slot_ids import SlotIdMap
from app.resy.client import ResyClient

FIXTURES = Path(__file__).parent / "fixtures" / "resy"
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=NY)  # fixtures are for 2026-10-22
LOWER_MANHATTAN = {"lat": 40.713, "lng": -74.006}
BOSTON = {"lat": 42.36, "lng": -71.059}
EMPTY_PAGE = {"search": {"hits": [], "nbHits": 0}}
DAY_ARGS: dict[str, Any] = {"date": "2026-10-22", "party_size": 2}
WIDE_RANGE: dict[str, Any] = {"time_precision": "range", "time_start": "11:00", "time_end": "23:00"}


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


Router = Callable[[httpx.Request, dict[str, Any] | None], httpx.Response]


class FakeResy:
    """MockTransport handler: routes Resy requests to fixtures and records them."""

    def __init__(self, route: Router) -> None:
        self.requests: list[tuple[str, dict[str, Any] | None]] = []
        self._route = route

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert path != "/3/details", "search must never call /3/details"
        body = json.loads(request.content) if request.content else None
        self.requests.append((path, body))
        return self._route(request, body)

    def paths(self) -> list[str]:
        return [p for p, _ in self.requests]

    def searches(self) -> list[dict[str, Any]]:
        return [b or {} for p, b in self.requests if p == "/3/venuesearch/search"]


def router(**responses: Any) -> Router:
    """Search responses keyed by query; "global:<query>" is the no-geo search and
    "<query>#2" is page 2. /3/venue and /4/find always return their fixtures."""

    def route(request: httpx.Request, body: dict[str, Any] | None) -> httpx.Response:
        if request.url.path == "/3/venue":
            return httpx.Response(200, json=fixture("venue-by-id.json"))
        if request.url.path == "/4/find":
            return httpx.Response(200, json=fixture("venue-find.json"))
        assert body is not None
        key = body["query"] if "geo" in body else f"global:{body['query']}"
        if body.get("page", 1) > 1:
            key = f"{key}#{body['page']}"
        response = responses[key]
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)

    return route


async def call_tool(
    fake: FakeResy,
    *,
    location: dict[str, float] | None = LOWER_MANHATTAN,
    slot_map: SlotIdMap | None = None,
    **args: Any,
) -> dict[str, Any]:
    resy = ResyClient(
        api_key="k",
        auth_token="t",
        writes_enabled=False,
        transport=httpx.MockTransport(fake),
        retry_backoff_s=0,
    )
    tool = make_search_availability_tool(
        resy, slot_map=slot_map or SlotIdMap(), now_fn=lambda tz: NOW.astimezone(tz)
    )
    config = {
        "configurable": {
            "thread_id": "conv-1",
            "timezone": "America/New_York",
            "location": location,
        }
    }
    async with resy:
        output = await tool.ainvoke(args, config=config)  # type: ignore[arg-type]
    return json.loads(output)


def names(result: dict[str, Any]) -> list[str]:
    return [v["name"] for v in result.get("venues", [])]


def venue(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(v for v in result["venues"] if v["name"] == name)


# --- guards -----------------------------------------------------------------------------


async def test_no_location_returns_location_required_without_calling_resy() -> None:
    fake = FakeResy(router())
    result = await call_tool(fake, location=None, cuisine="japanese")
    assert result["error"] == "location_required"
    assert fake.requests == []


async def test_past_date_is_rejected() -> None:
    fake = FakeResy(router())
    result = await call_tool(fake, cuisine="japanese", date="2026-10-01", party_size=2)
    assert result["error"] == "date_in_past"
    assert fake.requests == []


async def test_resy_error_becomes_friendly_message() -> None:
    fake = FakeResy(router(Tribeca=httpx.Response(500)))
    result = await call_tool(fake, neighborhood="Tribeca", **DAY_ARGS)
    assert result["error"] == "resy_unavailable"
    assert "temporarily unavailable" in result["message"]


# --- area search (neighborhood) ---------------------------------------------------------


async def test_neighborhood_search_payload_slots_and_notes() -> None:
    fake = FakeResy(router(Tribeca=fixture("venue-search-geo.json"), **{"Tribeca#2": EMPTY_PAGE}))
    slot_map = SlotIdMap()
    result = await call_tool(
        fake, slot_map=slot_map, neighborhood="Tribeca", **DAY_ARGS, **WIDE_RANGE
    )

    body = fake.searches()[0]
    assert body["geo"] == {"latitude": 40.713, "longitude": -74.006, "radius": 40000}
    assert body["include_tock_inventory"] is False
    assert body["slot_filter"] == {"day": "2026-10-22", "party_size": 2}
    assert (body["query"], body["per_page"]) == ("Tribeca", 20)  # neighborhood as search text

    assert result["mode"] == "area"
    assert result["city"] == "New York"
    assert names(result) == ["Artesano", "1803", "Holywater", "ITO NYC"]  # Resy's order
    notes = {
        f"{s['time']} {s['seating']}": s.get("note") for s in venue(result, "Artesano")["slots"]
    }
    assert notes == {
        "12:00 Dining": "requires payment",
        "17:00 Dining": "requires payment",
        "17:00 Patio": None,
    }
    assert [s["seating"] for s in venue(result, "1803")["slots"]] == ["Balcony"]
    assert venue(result, "ITO NYC")["slots"] == []
    assert result["venues_without_availability"] == 1

    serialized = json.dumps(result)
    assert "rgs://" not in serialized
    assert "40.71" not in serialized  # no coordinates

    patio = next(s for s in venue(result, "Artesano")["slots"] if s["seating"] == "Patio")
    ref = slot_map.get("conv-1", patio["slot_id"])
    assert ref is not None
    assert ref.config_token.startswith("rgs://resy/72993/4257306/")
    assert ref.bookable and not ref.requires_payment


async def test_fewer_than_five_bookable_fetches_page_two_and_flags_partial_on_failure() -> None:
    fake = FakeResy(
        router(Tribeca=fixture("venue-search-geo.json"), **{"Tribeca#2": httpx.Response(500)})
    )
    result = await call_tool(fake, neighborhood="Tribeca", **DAY_ARGS, **WIDE_RANGE)
    assert [b.get("page") for b in fake.searches()].count(2) == 3  # page 2, retried
    assert result["partial"] is True
    assert names(result) == ["Artesano", "1803", "Holywater", "ITO NYC"]


async def test_unknown_neighborhood_returns_neighborhoods_seen() -> None:
    fake = FakeResy(router(Astoria=fixture("venue-search-geo.json"), **{"Astoria#2": EMPTY_PAGE}))
    result = await call_tool(fake, neighborhood="Astoria", **DAY_ARGS)
    assert result["venues"] == []
    assert "Tribeca" in result["neighborhoods_seen"]
    assert "Financial District" in result["neighborhoods_seen"]


async def test_default_window_is_stated() -> None:
    fake = FakeResy(
        router(
            **{"Lower Manhattan": fixture("venue-search-geo.json"), "Lower Manhattan#2": EMPTY_PAGE}
        )
    )
    result = await call_tool(fake, neighborhood="Lower Manhattan", **DAY_ARGS)
    assert (result["window"], result["window_defaulted"]) == ("18:00-22:00", True)
    le_gratin = venue(result, "Le Gratin")
    assert le_gratin["bookable_via_agent"] is True  # venue-level Global Dining Access ignored
    assert [s["time"] for s in le_gratin["slots"]] == ["20:30"]


# --- cuisine search ---------------------------------------------------------------------


async def test_cuisine_search_filters_by_cuisine_and_excludes_tock() -> None:
    fake = FakeResy(router(sushi=fixture("venue-search-geo.json"), **{"sushi#2": EMPTY_PAGE}))
    result = await call_tool(fake, cuisine="sushi", **DAY_ARGS)
    assert result["mode"] == "cuisine"
    assert names(result) == ["ITO NYC"]  # icca also matches "sushi" but is Tock


async def test_cuisine_plus_neighborhood_searches_the_neighborhood_and_filters_cuisine() -> None:
    fake = FakeResy(router(Tribeca=fixture("venue-search-geo.json"), **{"Tribeca#2": EMPTY_PAGE}))
    result = await call_tool(fake, cuisine="sushi", neighborhood="Tribeca", **DAY_ARGS)
    body = fake.searches()[0]
    assert (body["query"], body["per_page"]) == ("Tribeca", 50)
    assert names(result) == ["ITO NYC"]


async def test_japanese_fixture_keeps_only_japanese_hits_in_order() -> None:
    data = fixture("venue-search-japanese.json")
    fake = FakeResy(router(Japanese=data))
    result = await call_tool(fake, cuisine="Japanese")

    expected = [
        h["name"]
        for h in data["search"]["hits"]
        if any("japanese" in c.lower() for c in h.get("cuisine") or [])
        and not h.get("is_tock_inventory")
    ]
    assert names(result) == expected[:10]
    assert "match" not in result  # no name-matching result in cuisine mode
    assert "availability" not in fake.searches()[0]  # no date â†’ no slots requested


async def test_cuisine_word_in_query_falls_back_to_cuisine_mode() -> None:
    data = fixture("venue-search-japanese.json")
    fake = FakeResy(router(japanese=data))
    result = await call_tool(fake, query="japanese")
    assert result["mode"] == "cuisine"
    assert len(fake.searches()) == 2  # name attempt, then cuisine search


# --- name search ------------------------------------------------------------------------


async def test_exact_name_resolves_without_slots_when_details_missing() -> None:
    fake = FakeResy(router(**{"Amor Loco": fixture("venue-search-amor-loco.json")}))
    result = await call_tool(fake, query="Amor Loco")
    assert (result["mode"], result["match"]) == ("name", "exact")
    assert names(result) == ["Amor Loco"]
    body = fake.searches()[0]
    assert body["per_page"] == 10
    assert "availability" not in body and "slot_filter" not in body


async def test_fuzzy_only_returns_did_you_mean_after_global_check() -> None:
    data = fixture("venue-search-amori.json")
    fake = FakeResy(router(amori=data, **{"global:amori": data}))
    result = await call_tool(fake, query="amori")
    assert result["match"] == "none"
    assert result["did_you_mean"][0]["name"] == "Mori"
    assert "global:amori" not in json.dumps(result)
    assert "geo" not in fake.searches()[1]  # the out-of-area check searched globally


async def test_exact_match_outside_radius_is_out_of_area() -> None:
    data = fixture("venue-search-amori.json")
    fake = FakeResy(router(Mori=data, **{"global:Mori": data}))
    result = await call_tool(fake, location=BOSTON, query="Mori")
    assert result["out_of_area"] is True
    assert (result["venue_name"], result["city"]) == ("Mori", "New York")


async def test_two_exact_matches_are_ambiguous() -> None:
    data = fixture("venue-search-amori.json")
    twin = copy.deepcopy(data["search"]["hits"][0])
    twin["id"]["resy"] = 1
    twin["neighborhood"] = "Tribeca"
    data["search"]["hits"].append(twin)
    fake = FakeResy(router(Mori=data))
    result = await call_tool(fake, query="Mori")
    assert result["match"] == "ambiguous"
    assert [c["neighborhood"] for c in result["candidates"]] == ["Soho", "Tribeca"]


async def test_match_in_another_neighborhood_is_flagged_without_slots() -> None:
    fake = FakeResy(router(Mori=fixture("venue-search-amori.json")))
    result = await call_tool(fake, query="Mori", neighborhood="West Village", **DAY_ARGS)
    assert result["neighborhood_mismatch"] is True
    assert "slots" not in result["venues"][0]


# --- venue by ID ------------------------------------------------------------------------


async def test_venue_id_uses_venue_and_find_never_search() -> None:
    fake = FakeResy(router())
    result = await call_tool(
        fake, venue_id=87134, time_precision="exact", requested_time="12:00", **DAY_ARGS
    )
    assert fake.paths() == ["/3/venue", "/4/find"]
    assert (result["mode"], result["match"]) == ("venue", "exact")
    assert len(result["exact_time_match"]) == 1
    slots = result["venues"][0]["slots"]
    assert [s["time"] for s in slots][0] == "12:00"
    assert len(slots) <= 8


async def test_venue_id_outside_radius_is_out_of_area() -> None:
    fake = FakeResy(router())
    result = await call_tool(fake, location=BOSTON, venue_id=87134, **DAY_ARGS)
    assert result["out_of_area"] is True
    assert fake.paths() == ["/3/venue"]


async def test_exact_time_unavailable_offers_nearby_times() -> None:
    fake = FakeResy(router())
    result = await call_tool(
        fake, venue_id=87134, time_precision="exact", requested_time="12:10", **DAY_ARGS
    )
    assert result["exact_time_match"] == []
    assert result["nearby_times"][:2] == ["12:00", "12:15"]
