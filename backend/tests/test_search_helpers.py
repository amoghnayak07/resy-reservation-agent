"""Search helpers: location, name matching, cuisine, time windows, slot-ID map."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.agent.tools.cuisine import cuisine_matches, cuisine_terms, looks_like_cuisine
from app.agent.tools.location import (
    UserLocation,
    city_label,
    haversine_km,
    neighborhood_matches,
    within_radius,
)
from app.agent.tools.matching import NameMatch, classify_name
from app.agent.tools.slot_ids import SlotIdMap, SlotRef
from app.agent.tools.time_windows import build_window

NY = ZoneInfo("America/New_York")
USER = UserLocation(lat=40.713, lng=-74.006)


# --- location ---------------------------------------------------------------------------


def test_haversine_and_radius() -> None:
    # Lower Manhattan → Midtown is ~5 km; → Boston is ~300 km.
    assert 4.5 < haversine_km(40.713, -74.006, 40.7585, -73.9842) < 6.0
    assert within_radius(USER, 40.7585, -73.9842, 40.0)
    assert not within_radius(USER, 42.3601, -71.0589, 40.0)
    assert not within_radius(USER, None, None, 40.0)


def test_neighborhood_matching_normalizes_and_uses_aliases() -> None:
    assert neighborhood_matches("west village", "West Village")
    assert neighborhood_matches("LES", "Lower East Side")
    assert neighborhood_matches("FiDi", "Financial District")
    assert not neighborhood_matches("Tribeca", "Soho")
    assert not neighborhood_matches("Tribeca", None)


def test_city_label_is_most_common() -> None:
    assert city_label(["New York", "New York", "Jersey City", None]) == "New York"
    assert city_label([None]) is None


# --- name matching ----------------------------------------------------------------------


def test_classify_name() -> None:
    assert classify_name("Mori", "Mori") is NameMatch.EXACT
    assert classify_name("kesté pizza & vino", "Kesté Pizza & Vino") is NameMatch.EXACT
    assert classify_name("The Bar Room", "Bar Room") is NameMatch.EXACT
    assert classify_name("Holywater", "Holywater Tribeca") is NameMatch.STRONG
    assert classify_name("amori", "Mori") is NameMatch.FUZZY
    assert classify_name("amor", "Amorina Cucina Rustica") is NameMatch.FUZZY


# --- cuisine ----------------------------------------------------------------------------


def test_cuisine_terms_and_matching() -> None:
    assert cuisine_terms("sushi") == ("sushi", "japanese", "omakase")
    assert cuisine_terms("Peruvian") == ("peruvian",)
    assert cuisine_matches(cuisine_terms("japanese"), ["Japanese - Peruvian"])
    assert cuisine_matches(cuisine_terms("sushi"), ["Edomae Sushi/ Italian Kappo"])
    assert not cuisine_matches(cuisine_terms("japanese"), ["Steakhouse"])


def test_looks_like_cuisine() -> None:
    assert looks_like_cuisine("sushi", [])
    assert looks_like_cuisine("Japanese", ["Japanese", "Sushi"])
    assert not looks_like_cuisine("Mori", ["Japanese"])


# --- time windows -----------------------------------------------------------------------

NOW = datetime(2026, 10, 20, 14, 0, tzinfo=NY)
DAY = date(2026, 10, 22)


def _window(**kwargs: object) -> tuple[str, str, str, bool]:
    defaults: dict[str, object] = {
        "day": DAY,
        "now": NOW,
        "kind": "area",
        "precision": None,
        "requested_time": None,
        "time_start": None,
        "time_end": None,
    }
    w = build_window(**(defaults | kwargs))  # type: ignore[arg-type]
    return (w.start.strftime("%H:%M"), w.end.strftime("%H:%M"), w.precision, w.defaulted)


def test_time_window_rules() -> None:
    assert _window(precision="exact", requested_time=time(20, 0)) == (
        "19:30",
        "20:30",
        "exact",
        False,
    )
    assert _window(precision="approximate", requested_time=time(20, 0)) == (
        "19:00",
        "21:00",
        "approximate",
        False,
    )
    assert _window(precision="range", time_start=time(19), time_end=time(21)) == (
        "19:00",
        "21:00",
        "range",
        False,
    )
    assert _window(kind="venue") == ("00:00", "23:59", "any", False)
    assert _window(kind="cuisine") == ("18:00", "22:00", "any", True)


def test_time_window_today_starts_after_now() -> None:
    today = NOW.date()
    assert _window(day=today) == ("14:30", "22:00", "any", True)
    start, _, precision, _ = _window(
        day=today, precision="range", time_start=time(12), time_end=time(21)
    )
    assert (start, precision) == ("14:30", "range")


def test_time_window_uses_request_timezone() -> None:
    tokyo_now = datetime(2026, 10, 20, 14, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    w = build_window(
        day=DAY,
        now=tokyo_now,
        kind="area",
        precision="exact",
        requested_time=time(20),
        time_start=None,
        time_end=None,
    )
    assert w.start.tzinfo == ZoneInfo("Asia/Tokyo")


# --- slot-ID map ------------------------------------------------------------------------


def _ref() -> SlotRef:
    return SlotRef(
        config_token="rgs://resy/1/2/3/2026-10-22/2026-10-22/19:00:00/2/Dining Room",
        venue_id=1,
        venue_name="Test",
        party_size=2,
        start=datetime(2026, 10, 22, 19, tzinfo=NY),
        seating_type="Dining Room",
        bookable=True,
        requires_payment=False,
    )


def test_slot_ids_are_per_conversation_and_expire() -> None:
    slot_map = SlotIdMap(ttl_seconds=900)
    first = slot_map.add("conv-a", _ref(), now=0)
    second = slot_map.add("conv-a", _ref(), now=0)
    assert (first, second) == ("s1", "s2")
    assert slot_map.get("conv-a", "s1", now=100) == _ref()
    assert slot_map.get("conv-b", "s1", now=100) is None
    assert slot_map.get("conv-a", "s1", now=901) is None
