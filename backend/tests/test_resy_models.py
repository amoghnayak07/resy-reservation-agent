"""Resy raw + domain models, parsed from recorded fixtures in tests/fixtures/resy/."""

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.resy.models import (
    Availability,
    BookingDetails,
    BookingResult,
    BookResponse,
    CalendarResponse,
    DetailsResponse,
    FindResponse,
    ReopenRaw,
    ReservationQuery,
    SearchHitRaw,
    Slot,
    Venue,
    VenueCalendar,
    VenueDetails,
    VenueResponse,
    VenueSearchResponse,
    dedupe_slots,
)

FIXTURES = Path(__file__).parent / "fixtures" / "resy"
NY = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 27)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def search_hits(name: str = "venue-search-geo.json") -> dict[str, SearchHitRaw]:
    response = VenueSearchResponse.model_validate(fixture(name))
    return {hit.name: hit for hit in response.search.hits}


def search_slots(hit: SearchHitRaw) -> list[Slot]:
    assert hit.availability is not None
    return [
        Slot.from_raw(
            raw, venue_id=hit.id.resy, party_size=2, tz=NY, templates=hit.availability.templates
        )
        for raw in hit.availability.slots
    ]


def find_slots() -> list[Slot]:
    result = FindResponse.model_validate(fixture("venue-find.json")).results.venues[0]
    return [
        Slot.from_raw(
            raw, venue_id=result.venue.id.resy, party_size=2, tz=NY, templates=result.templates
        )
        for raw in result.slots
    ]


# --- venue search -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["venue-search-geo.json", "venue-search-amori.json", "venue-search-amor-loco.json"]
)
def test_every_search_fixture_parses_to_venues(name: str) -> None:
    for hit in search_hits(name).values():
        Venue.from_search_hit(hit, TODAY)


def test_search_hit_maps_to_venue() -> None:
    venue = Venue.from_search_hit(search_hits()["Brooklyn Chop House - Downtown FIDI"], TODAY)
    assert venue.id == 87134
    assert venue.neighborhood == "Financial District"
    assert venue.city == "New York"
    assert venue.cuisine == ["Steakhouse"]
    assert venue.price_range == "$$$"
    assert (venue.rating, venue.rating_count) == (4.14474, 76)
    assert (
        venue.url == "https://resy.com/cities/new-york-ny/venues/brooklyn-chop-house-downtown-fidi"
    )
    assert (venue.lat, venue.lng) == (40.71164941860875, -74.00596236132077)
    assert venue.bookable_via_agent is True


def test_generic_and_empty_neighborhoods_are_unknown() -> None:
    hits = search_hits()
    assert Venue.from_search_hit(hits["Kesté Pizza & Vino"], TODAY).neighborhood is None
    assert Venue.from_search_hit(hits["icca"], TODAY).neighborhood is None
    amor_loco = search_hits("venue-search-amori.json")["Amor Loco"]
    assert Venue.from_search_hit(amor_loco, TODAY).neighborhood is None


def test_past_reopen_date_stays_bookable_and_future_one_does_not() -> None:
    artesano = search_hits()["Artesano"]
    assert Venue.from_search_hit(artesano, TODAY).bookable_via_agent is True

    closed = artesano.model_copy(update={"reopen": ReopenRaw(date=date(2026, 12, 1))})
    venue = Venue.from_search_hit(closed, TODAY)
    assert venue.bookable_via_agent is False
    assert venue.not_bookable_reason == "temporarily closed until 2026-12-01"


def test_venue_level_global_dining_access_is_ignored() -> None:
    le_gratin = Venue.from_search_hit(search_hits()["Le Gratin"], TODAY)
    assert le_gratin.bookable_via_agent is True


def test_tock_venue_is_not_bookable_and_its_slots_are_not_bookable() -> None:
    icca = search_hits()["icca"]
    assert Venue.from_search_hit(icca, TODAY).bookable_via_agent is False
    assert [slot.bookable for slot in search_slots(icca)] == [False]


def test_highlight_markup_is_stripped_from_names() -> None:
    hit = search_hits()["1803"].model_copy(update={"name": "<b>18</b>03"})
    assert Venue.from_search_hit(hit, TODAY).name == "1803"


def test_search_slot_seating_type_comes_from_token() -> None:
    hits = search_hits()
    assert [s.seating_type for s in search_slots(hits["1803"])] == ["Balcony"]
    assert [s.seating_type for s in search_slots(hits["Holywater"])] == ["Dining Room", "Lounge"]


def test_search_slot_payment_comes_from_template() -> None:
    artesano = search_slots(search_hits()["Artesano"])
    # 12:00 lunch and 17:00 "Dinner " use paid templates; the 17:00 Patio slot is free.
    assert [s.requires_payment for s in artesano] == [True, True, False]
    assert all(s.requires_payment for s in search_slots(search_hits()["Holywater"]))
    assert not any(s.requires_payment for s in search_slots(search_hits()["Le Gratin"]))


def test_two_seating_types_at_one_time_stay_separate() -> None:
    keste = dedupe_slots(search_slots(search_hits()["Kesté Pizza & Vino"]))
    at_11 = [s.seating_type for s in keste if s.start.hour == 11 and s.start.minute == 0]
    assert at_11 == ["Dining Room", "Table"]


def test_slot_ending_after_midnight_parses() -> None:
    lounge = search_slots(search_hits()["Holywater"])[1]
    assert lounge.end == datetime(2026, 10, 23, 0, 15, tzinfo=NY)


# --- /4/find ----------------------------------------------------------------------------


def test_find_slots_map_to_domain() -> None:
    slots = find_slots()
    assert len(slots) == 33
    first = slots[0]
    assert first.venue_id == 87134
    assert first.start == datetime(2026, 10, 22, 12, 0, tzinfo=NY)
    assert first.end == datetime(2026, 10, 22, 13, 30, tzinfo=NY)
    assert (first.size_min, first.size_max) == (1, 2)
    assert first.seating_type == "Dining Room"
    assert first.template_id == 3533613
    assert first.service_type_id == 3
    assert first.requires_payment is False
    assert first.bookable is True
    assert first.config_token == (
        "rgs://resy/87134/3533613/3/2026-10-22/2026-10-22/12:00:00/2/Dining Room"
    )


def test_find_slot_flags_make_slot_unbookable() -> None:
    raw = FindResponse.model_validate(fixture("venue-find.json")).results.venues[0]
    base = raw.slots[0]
    templates = raw.templates

    def build(**update: Any) -> Slot:
        return Slot.from_raw(
            base.model_copy(update=update), venue_id=1, party_size=2, tz=NY, templates=templates
        )

    assert build(is_global_dining_access=True).bookable is False
    ineligible = base.exclusive.model_copy(update={"is_eligible": False})  # type: ignore[union-attr]
    assert build(exclusive=ineligible).bookable is False
    paid = base.payment.model_copy(update={"is_paid": True})  # type: ignore[union-attr]
    assert build(payment=paid).requires_payment is True


def test_duplicate_tokens_collapse_to_one_slot() -> None:
    raw = FindResponse.model_validate(fixture("venue-find.json")).results.venues[0]
    original = raw.slots[0]
    other_table = original.model_copy(
        update={"config": original.config.model_copy(update={"id": 1829819})}
    )
    slots = [
        Slot.from_raw(s, venue_id=87134, party_size=2, tz=NY, templates=raw.templates)
        for s in (original, other_table, raw.slots[1])
    ]
    assert len(dedupe_slots(slots)) == 2


def test_find_venue_maps_endpoint_specific_fields() -> None:
    raw = FindResponse.model_validate(fixture("venue-find.json")).results.venues[0].venue
    venue = Venue.from_find_venue(raw, TODAY)
    assert venue.cuisine == ["Steakhouse"]
    assert venue.price_range == "$$$"
    assert (venue.rating, venue.rating_count) == (4.14474, 76)
    assert venue.lng == -74.00596236132077
    assert venue.neighborhood == "Financial District"


# --- /4/venue/calendar ------------------------------------------------------------------


def test_calendar_maps_values() -> None:
    calendar = VenueCalendar.from_response(
        CalendarResponse.model_validate(fixture("venue-calendar.json")), venue_id=87134
    )
    assert calendar.last_calendar_day == date(2026, 10, 31)
    assert calendar.days[0].reservation is Availability.SOLD_OUT
    assert calendar.days[0].walk_in is Availability.AVAILABLE
    assert calendar.days[1].reservation is Availability.AVAILABLE


def test_calendar_not_available_and_unseen_values(caplog: pytest.LogCaptureFixture) -> None:
    raw = CalendarResponse.model_validate(
        {
            "last_calendar_day": "2026-10-31",
            "scheduled": [
                {"date": "2026-10-01", "inventory": {"reservation": "not available"}},
                {"date": "2026-10-02", "inventory": {"reservation": "waitlist-only"}},
            ],
        }
    )
    with caplog.at_level(logging.WARNING, logger="app.resy"):
        days = VenueCalendar.from_response(raw, venue_id=1).days
    assert days[0].reservation is Availability.NOT_AVAILABLE
    assert days[1].reservation is Availability.UNKNOWN
    assert any(r.message == "resy_unknown_calendar_value" for r in caplog.records)


# --- /3/venue ---------------------------------------------------------------------------


def test_venue_details_map() -> None:
    details = VenueDetails.from_response(VenueResponse.model_validate(fixture("venue.json")))
    assert details.id == 87134
    assert details.cuisine == "Steakhouse"
    assert details.price_range == "$$$"
    assert details.address == "150 Nassau St, New York, NY 10038"
    assert details.url == (
        "https://resy.com/cities/new-york-ny/venues/brooklyn-chop-house-downtown-fidi"
    )
    assert details.description is not None and details.description.startswith("Only in New York")
    assert (details.rating, details.rating_count) == (4.14474, 76)


# --- /3/details and /3/book -------------------------------------------------------------


def test_details_commit0_is_free_without_book_token() -> None:
    details = BookingDetails.from_response(
        DetailsResponse.model_validate(fixture("details-commit0.json"))
    )
    assert details.is_free is True
    assert details.book_token is None
    assert details.refund_cutoff == datetime(2026, 10, 22, 16, 0, tzinfo=UTC)
    assert details.change_cutoff == datetime(2026, 10, 22, 16, 0, tzinfo=UTC)
    assert details.policy_text is not None and "24 hours" in details.policy_text
    assert details.description is not None and details.description.startswith("Only in New York")
    assert details.address == "150 Nassau St, New York, NY 10038"


def test_details_commit1_adds_book_token() -> None:
    details = BookingDetails.from_response(
        DetailsResponse.model_validate(fixture("details-commit1.json"))
    )
    assert details.is_free is True
    assert details.book_token == "SCRUBBED_BOOK_TOKEN"
    assert details.book_token_expires == datetime(2026, 9, 27, 12, 6, 55, tzinfo=UTC)
    assert "SCRUBBED_BOOK_TOKEN" not in repr(details)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["payment"]["amounts"].update(total=25.0),
        lambda d: d["payment"]["config"].update(type="paid"),
        lambda d: d["cancellation"].update(fee={"amount": 25.0}),
    ],
)
def test_details_paid_variants_are_not_free(mutate: Any) -> None:
    data = fixture("details-commit0.json")
    mutate(data)
    assert BookingDetails.from_response(DetailsResponse.model_validate(data)).is_free is False


def test_book_result_parses_and_hides_token() -> None:
    result = BookingResult.from_response(BookResponse.model_validate(fixture("book.json")))
    assert result.reservation_id == 933020068
    assert "SCRUBBED_RESY_TOKEN" not in repr(result)


# --- ReservationQuery -------------------------------------------------------------------


def test_reservation_query_needs_a_target() -> None:
    with pytest.raises(ValidationError):
        ReservationQuery(party_size=2)
    assert ReservationQuery(cuisine="japanese").cuisine == "japanese"
    assert ReservationQuery(neighborhood="Tribeca").neighborhood == "Tribeca"


def test_reservation_query_party_size_bounds() -> None:
    with pytest.raises(ValidationError):
        ReservationQuery(query="Mori", party_size=21)
