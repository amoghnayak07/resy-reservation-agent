"""`search_availability` tool (stage 6): finds Resy venues and open times near the user.

Modes: venue by ID (`/3/venue?id=` + `/4/find`), name search, cuisine search, area search.
Slots for name/cuisine/area come from the search response itself (already filtered by party
size). Results keep Resy's order; distance only filters. Output is compact JSON: no
coordinates, no config tokens (short `slot_id`s instead), no highlight markup.
"""

import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field

from app.agent.tools.cuisine import cuisine_matches, cuisine_terms, looks_like_cuisine
from app.agent.tools.location import (
    UserLocation,
    city_label,
    neighborhood_matches,
    within_radius,
)
from app.agent.tools.matching import NameMatch, classify_name
from app.agent.tools.slot_ids import SlotIdMap, SlotRef, slot_ids
from app.agent.tools.time_windows import SearchKind, TimeWindow, build_window
from app.config import settings
from app.resy.client import ResyClient
from app.resy.errors import ResyError
from app.resy.models import (
    ReservationQuery,
    SearchHitRaw,
    Slot,
    SlotRaw,
    TemplateRaw,
    Venue,
    VenueDetails,
    dedupe_slots,
)

logger = logging.getLogger(__name__)

TOOL_NAME = "search_availability"
NAME_PER_PAGE = 10
LIST_PER_PAGE = 20
NEIGHBORHOOD_CUISINE_PER_PAGE = 50
MAX_VENUES = 10
MAX_SLOTS_PER_VENUE = 8
MIN_BOOKABLE_BEFORE_PAGE_2 = 5
MAX_DID_YOU_MEAN = 3
MAX_NEIGHBORHOODS_SEEN = 15
MAX_NEARBY_TIMES = 6

DESCRIPTION = """Search Resy near the user for restaurants and open reservation times.

Use `query` for a restaurant name, `cuisine` for a type of food (e.g. "Japanese", "sushi"), \
`neighborhood` to restrict to an area by name, or `venue_id` for a venue already identified \
in this conversation. At least one of these is required; if the user gave none, ask them first. \
Add `date` and `party_size` to get open times; without both, it only identifies venues. \
Time: set `time_precision` and either `requested_time` ("8 PM" → exact; "around 8" → \
approximate) or `time_start`/`time_end` (a range; "lunch" = 11:30-14:30); omit for any \
time. Never guess a party size.

Results: `match` is "exact", "ambiguous", or "none" for name searches; never treat \
`did_you_mean` candidates as the user's restaurant. `out_of_area: true` means the venue is \
outside the user's area. Slots carry a `slot_id` for booking; slots with a `note` can't be \
booked here."""


class SearchAvailabilityArgs(ReservationQuery):
    """ReservationQuery with descriptions for the LLM's tool schema."""

    query: str | None = Field(default=None, description="Restaurant name, as the user said it.")
    cuisine: str | None = Field(default=None, description='Cuisine, e.g. "Japanese", "tacos".')
    neighborhood: str | None = Field(default=None, description='Neighborhood, e.g. "Tribeca".')
    venue_id: int | None = Field(default=None, description="Resy venue ID from earlier results.")
    date: dt.date | None = Field(default=None, description="Reservation date, YYYY-MM-DD.")
    party_size: int | None = Field(default=None, ge=1, le=20, description="Number of people.")
    requested_time: time | None = Field(default=None, description="Requested time, HH:MM (24h).")
    time_start: time | None = Field(default=None, description="Range start, HH:MM (24h).")
    time_end: time | None = Field(default=None, description="Range end, HH:MM (24h).")
    time_precision: Literal["exact", "approximate", "range", "any"] | None = Field(
        default=None, description="How precise the user's time is."
    )


@dataclass
class _Ctx:
    resy: ResyClient
    slot_map: SlotIdMap
    user: UserLocation
    tz: ZoneInfo
    now: datetime
    conversation_id: str
    radius_km: float
    query: ReservationQuery

    @property
    def today(self) -> date:
        return self.now.date()

    @property
    def wants_slots(self) -> bool:
        return self.query.date is not None and self.query.party_size is not None

    @property
    def geo(self) -> tuple[float, float, int]:
        return (self.user.lat, self.user.lng, int(self.radius_km * 1000))


@dataclass
class _Candidate:
    hit: SearchHitRaw
    venue: Venue


@dataclass
class _Screened:
    kept: list[_Candidate] = field(default_factory=list)
    too_large_for_party: int = 0
    closed: list[_Candidate] = field(default_factory=list)


def make_search_availability_tool(
    resy: ResyClient,
    *,
    slot_map: SlotIdMap = slot_ids,
    now_fn: Callable[[ZoneInfo], datetime] | None = None,
) -> BaseTool:
    clock = now_fn or (lambda tz: datetime.now(tz))

    async def search_availability(config: RunnableConfig, **kwargs: Any) -> str:
        configurable = config.get("configurable", {})
        location = configurable.get("location")
        if not location:
            return _dump(
                {
                    "error": "location_required",
                    "message": "Ask the user to tap 'Use my location'; searches are local.",
                }
            )
        tz = ZoneInfo(configurable["timezone"])
        ctx = _Ctx(
            resy=resy,
            slot_map=slot_map,
            user=UserLocation(lat=location["lat"], lng=location["lng"]),
            tz=tz,
            now=clock(tz),
            conversation_id=str(configurable.get("thread_id", "")),
            radius_km=settings.search_radius_km,
            query=ReservationQuery(**kwargs),
        )
        if ctx.query.date is not None and ctx.query.date < ctx.today:
            return _dump({"error": "date_in_past", "message": "That date has already passed."})
        try:
            return _dump(await _run(ctx))
        except ResyError as exc:
            logger.warning("resy_error_in_tool", extra={"endpoint": type(exc).__name__})
            return _dump(
                {
                    "error": "resy_unavailable",
                    "error_type": type(exc).__name__,
                    "message": "Resy is temporarily unavailable. Try again in a minute.",
                }
            )

    return StructuredTool.from_function(
        coroutine=search_availability,
        name=TOOL_NAME,
        description=DESCRIPTION,
        args_schema=SearchAvailabilityArgs,
    )


async def _run(ctx: _Ctx) -> dict[str, Any]:
    q = ctx.query
    if q.venue_id is not None:
        return await _venue_by_id(ctx, q.venue_id)
    if q.query:
        result = await _name_search(ctx, q.query)
        if result is not None:
            return result
        # Ambiguous word: no name match, but it reads as a cuisine ("sushi").
        return await _list_search(ctx, "cuisine", q.query)
    if q.cuisine:
        return await _list_search(ctx, "cuisine", q.cuisine)
    return await _list_search(ctx, "area", None)


# --- venue by ID ----------------------------------------------------------------------------


async def _venue_by_id(ctx: _Ctx, venue_id: int) -> dict[str, Any]:
    raw = await ctx.resy.get_venue(venue_id)
    details = VenueDetails.from_response(raw)
    loc = raw.location
    city = loc.locality if loc else None
    if (
        loc is not None
        and loc.latitude is not None
        and loc.longitude is not None
        and not within_radius(ctx.user, loc.latitude, loc.longitude, ctx.radius_km)
    ):
        return {
            "mode": "venue",
            "match": "none",
            "out_of_area": True,
            "venue_name": details.name,
            "city": city,
        }
    base: dict[str, Any] = {"mode": "venue", "match": "exact", "city": city}
    venue_out: dict[str, Any] = {
        "venue_id": details.id,
        "name": details.name,
        "neighborhood": details.neighborhood,
        "cuisine": [details.cuisine] if details.cuisine else [],
        "price_range": details.price_range,
        "url": details.url,
    }
    if details.reopen_date and details.reopen_date > ctx.today:
        venue_out["closed_until"] = details.reopen_date.isoformat()
        return base | {"venues": [venue_out]}
    if not ctx.wants_slots:
        return base | {"venues": [venue_out]}

    assert ctx.query.date is not None and ctx.query.party_size is not None
    find = await ctx.resy.find(venue_id, ctx.query.date, ctx.query.party_size)
    if not find.results.venues:
        return base | {"venues": [venue_out | {"slots": []}], "venues_without_availability": 1}
    result = find.results.venues[0]
    venue = Venue.from_find_venue(result.venue, ctx.today)
    venue_out["bookable_via_agent"] = venue.bookable_via_agent
    if venue.not_bookable_reason:
        venue_out["not_bookable_reason"] = venue.not_bookable_reason
    slots = [
        s
        for s in _to_slots(result.slots, result.templates, venue.id, ctx)
        if _fits_party(s, ctx.query.party_size)
    ]
    return base | _with_slots(ctx, [(venue, venue_out, slots)], _window(ctx, "venue"))


# --- name search ----------------------------------------------------------------------------


async def _name_search(ctx: _Ctx, name: str) -> dict[str, Any] | None:
    """None means "no name match and the word reads as a cuisine": caller falls back."""
    q = ctx.query
    response = await ctx.resy.venue_search(
        query=name, geo=ctx.geo, day=q.date, party_size=q.party_size, per_page=NAME_PER_PAGE
    )
    nearby = _in_radius(ctx, response.search.hits)
    city = city_label(c.venue.city for c in nearby)
    classified = [(c, classify_name(name, c.venue.name)) for c in nearby]
    exact = [c for c, m in classified if m is NameMatch.EXACT]
    strong = [c for c, m in classified if m is NameMatch.STRONG]
    # A cuisine word ("japanese") also appears in names ("Gen Japanese Restaurant"): only an
    # exact name match beats the cuisine reading.
    if not exact and looks_like_cuisine(name, response.search.cuisines):
        return None
    matches = exact if len(exact) == 1 else exact + strong

    if not matches:
        out_of_area = await _out_of_area_check(ctx, name)
        if out_of_area is not None:
            return out_of_area
        did_you_mean = [_brief(c.venue) for c, _ in classified[:MAX_DID_YOU_MEAN]]
        return {"mode": "name", "match": "none", "did_you_mean": did_you_mean, "city": city}

    if len(matches) > 1:
        return {
            "mode": "name",
            "match": "ambiguous",
            "candidates": [_brief(c.venue) for c in matches[:5]],
            "city": city,
        }

    match = matches[0]
    result: dict[str, Any] = {"mode": "name", "match": "exact", "city": city}
    venue_out = _venue_out(match.venue)
    if match.venue.reopen_date and match.venue.reopen_date > ctx.today:
        return result | {
            "venues": [venue_out | {"closed_until": match.venue.reopen_date.isoformat()}]
        }
    if q.party_size and match.venue.max_party_size and q.party_size > match.venue.max_party_size:
        venue_out["too_large_for_party"] = True
        return result | {"venues": [venue_out]}
    if q.neighborhood and not neighborhood_matches(q.neighborhood, match.venue.neighborhood):
        # Ask before continuing: no slots until the user confirms this is the right place.
        return result | {"neighborhood_mismatch": True, "venues": [venue_out]}
    if not ctx.wants_slots:
        return result | {"venues": [venue_out]}
    slots = _hit_slots(match.hit, match.venue, ctx)
    return result | _with_slots(ctx, [(match.venue, venue_out, slots)], _window(ctx, "venue"))


async def _out_of_area_check(ctx: _Ctx, name: str) -> dict[str, Any] | None:
    """The radius hides far venues, so search once without geo for an exact match."""
    response = await ctx.resy.venue_search(query=name, geo=None, per_page=NAME_PER_PAGE)
    for hit in response.search.hits:
        if classify_name(name, hit.name) is not NameMatch.EXACT:
            continue
        venue = Venue.from_search_hit(hit, ctx.today)
        if not within_radius(ctx.user, venue.lat, venue.lng, ctx.radius_km):
            return {
                "mode": "name",
                "match": "none",
                "out_of_area": True,
                "venue_name": venue.name,
                "city": venue.city,
            }
    return None


# --- cuisine and area searches --------------------------------------------------------------


async def _list_search(
    ctx: _Ctx, kind: Literal["cuisine", "area"], cuisine: str | None
) -> dict[str, Any]:
    q = ctx.query
    terms = cuisine_terms(cuisine) if cuisine else ()
    # Resy's text search matches the neighborhood field, but "cuisine + neighborhood" text
    # matches either word. So a neighborhood is the search text (cuisine filtered here),
    # with a bigger page when both are given; area results alone are nearest-first and
    # never reach a neighborhood a few km away.
    search_text = q.neighborhood or cuisine or ""
    per_page = NEIGHBORHOOD_CUISINE_PER_PAGE if (q.neighborhood and cuisine) else LIST_PER_PAGE
    candidates: list[_Candidate] = []
    all_nearby: list[_Candidate] = []
    too_large = 0
    partial = False

    for page in (1, 2):
        try:
            response = await ctx.resy.venue_search(
                query=search_text,
                geo=ctx.geo,
                day=q.date,
                party_size=q.party_size,
                per_page=per_page,
                page=page,
            )
        except ResyError:
            if page == 1:
                raise
            partial = True
            break
        nearby = _in_radius(ctx, response.search.hits)
        all_nearby.extend(nearby)
        if terms:
            nearby = [c for c in nearby if cuisine_matches(terms, c.venue.cuisine)]
        if q.neighborhood:
            nearby = [
                c for c in nearby if neighborhood_matches(q.neighborhood, c.venue.neighborhood)
            ]
        screened = _screen(nearby, q.party_size, ctx.today)
        too_large += screened.too_large_for_party
        candidates.extend(screened.kept)
        bookable = [c for c in candidates if c.venue.bookable_via_agent]
        more = (response.search.nbHits or 0) > page * per_page
        if len(bookable) >= MIN_BOOKABLE_BEFORE_PAGE_2 or not more:
            break

    result: dict[str, Any] = {"mode": kind, "city": city_label(c.venue.city for c in all_nearby)}
    if cuisine:
        result["cuisine"] = cuisine
    if too_large:
        result["too_large_for_party_count"] = too_large
    if partial:
        result["partial"] = True
    if q.neighborhood and not candidates:
        seen = sorted({c.venue.neighborhood for c in all_nearby if c.venue.neighborhood})
        return result | {"venues": [], "neighborhoods_seen": seen[:MAX_NEIGHBORHOODS_SEEN]}

    shown = [c for c in candidates if c.venue.bookable_via_agent][:MAX_VENUES]
    not_bookable = len([c for c in candidates if not c.venue.bookable_via_agent])
    if not_bookable:
        result["not_bookable_count"] = not_bookable
    if not ctx.wants_slots:
        return result | {"venues": [_venue_out(c.venue) for c in shown]}
    entries = [(c.venue, _venue_out(c.venue), _hit_slots(c.hit, c.venue, ctx)) for c in shown]
    return result | _with_slots(ctx, entries, _window(ctx, kind))


# --- shared helpers -------------------------------------------------------------------------


def _in_radius(ctx: _Ctx, hits: list[SearchHitRaw]) -> list[_Candidate]:
    """Keep Resy's order; distance only filters (CLAUDE.md agent rules)."""
    out: list[_Candidate] = []
    for hit in hits:
        venue = Venue.from_search_hit(hit, ctx.today)
        if within_radius(ctx.user, venue.lat, venue.lng, ctx.radius_km):
            out.append(_Candidate(hit=hit, venue=venue))
    return out


def _screen(candidates: list[_Candidate], party_size: int | None, today: date) -> _Screened:
    screened = _Screened()
    for c in candidates:
        if c.hit.is_tock_inventory:
            continue
        if c.venue.reopen_date and c.venue.reopen_date > today:
            screened.closed.append(c)
            continue
        if party_size and c.venue.max_party_size and party_size > c.venue.max_party_size:
            screened.too_large_for_party += 1
            continue
        screened.kept.append(c)
    return screened


def _hit_slots(hit: SearchHitRaw, venue: Venue, ctx: _Ctx) -> list[Slot]:
    if hit.availability is None:
        return []
    return _to_slots(hit.availability.slots, hit.availability.templates, venue.id, ctx)


def _to_slots(
    raws: list[SlotRaw], templates: dict[str, TemplateRaw], venue_id: int, ctx: _Ctx
) -> list[Slot]:
    assert ctx.query.party_size is not None
    slots = [
        Slot.from_raw(
            raw, venue_id=venue_id, party_size=ctx.query.party_size, tz=ctx.tz, templates=templates
        )
        for raw in raws
    ]
    return dedupe_slots(slots)


def _fits_party(slot: Slot, party_size: int | None) -> bool:
    if party_size is None:
        return True
    if slot.size_min is not None and party_size < slot.size_min:
        return False
    return not (slot.size_max is not None and party_size > slot.size_max)


def _window(ctx: _Ctx, kind: SearchKind) -> TimeWindow:
    q = ctx.query
    assert q.date is not None
    return build_window(
        day=q.date,
        now=ctx.now,
        kind=kind,
        precision=q.time_precision,
        requested_time=q.requested_time,
        time_start=q.time_start,
        time_end=q.time_end,
    )


def _with_slots(
    ctx: _Ctx, entries: list[tuple[Venue, dict[str, Any], list[Slot]]], window: TimeWindow
) -> dict[str, Any]:
    venues_out: list[dict[str, Any]] = []
    exact_ids: list[str] = []
    in_window_times: set[datetime] = set()
    empty = 0
    for venue, venue_out, slots in entries:
        chosen = [s for s in slots if window.contains(s.start)]
        in_window_times.update(s.start for s in chosen)
        if window.requested is not None:
            requested = window.requested
            chosen.sort(key=lambda s: (abs((s.start - requested).total_seconds()), s.start))
        else:
            chosen.sort(key=lambda s: s.start)
        chosen = chosen[:MAX_SLOTS_PER_VENUE]
        chosen.sort(key=lambda s: (s.start, s.seating_type or ""))
        if not chosen:
            empty += 1
        slot_out = []
        for slot in chosen:
            slot_id = _register(ctx, venue, slot)
            entry: dict[str, Any] = {
                "slot_id": slot_id,
                "time": slot.start.strftime("%H:%M"),
                "seating": slot.seating_type,
            }
            note = _slot_note(venue, slot)
            if note:
                entry["note"] = note
            elif window.precision == "exact" and slot.start == window.requested:
                exact_ids.append(slot_id)
            slot_out.append(entry)
        venues_out.append(venue_out | {"slots": slot_out})

    result: dict[str, Any] = {
        "date": ctx.query.date.isoformat() if ctx.query.date else None,
        "party_size": ctx.query.party_size,
        "window": window.describe(),
        "venues": venues_out,
    }
    if window.defaulted:
        result["window_defaulted"] = True
    if empty:
        result["venues_without_availability"] = empty
    if window.precision == "exact":
        result["exact_time_match"] = exact_ids
        if not exact_ids:
            times = sorted(t.strftime("%H:%M") for t in in_window_times)
            result["nearby_times"] = times[:MAX_NEARBY_TIMES]
    return result


def _register(ctx: _Ctx, venue: Venue, slot: Slot) -> str:
    ref = SlotRef(
        config_token=slot.config_token or "",
        venue_id=venue.id,
        venue_name=venue.name,
        party_size=slot.party_size,
        start=slot.start,
        seating_type=slot.seating_type,
        bookable=slot.bookable and venue.bookable_via_agent,
        requires_payment=slot.requires_payment,
    )
    return ctx.slot_map.add(ctx.conversation_id, ref)


def _slot_note(venue: Venue, slot: Slot) -> str | None:
    if not venue.bookable_via_agent or not slot.bookable:
        return "can't be booked here"
    if slot.requires_payment:
        return "requires payment"
    return None


def _venue_out(venue: Venue) -> dict[str, Any]:
    out: dict[str, Any] = {
        "venue_id": venue.id,
        "name": venue.name,
        "neighborhood": venue.neighborhood,
        "cuisine": venue.cuisine,
        "price_range": venue.price_range,
        "bookable_via_agent": venue.bookable_via_agent,
        "url": venue.url,
    }
    if venue.not_bookable_reason:
        out["not_bookable_reason"] = venue.not_bookable_reason
    return out


def _brief(venue: Venue) -> dict[str, Any]:
    return {
        "venue_id": venue.id,
        "name": venue.name,
        "neighborhood": venue.neighborhood,
        "cuisine": venue.cuisine,
    }


def _dump(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)
