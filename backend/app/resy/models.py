"""Resy models in two layers, both built from DevTools captures (tests/fixtures/resy/):

- Raw response models mirror the captured JSON, with extra="ignore" so new fields don't
  break parsing. Field names differ by endpoint (search hits vs /4/find vs /3/venue), so
  each endpoint gets its own raw models.
- Domain models are what tools use. Their from_* constructors hold the mapping rules
  from stage 5 (seating type, payment, bookability, generic neighborhoods).

Tokens (config, book, resy_token) live on domain models for server-side use only.
"""

import datetime as dt
import logging
import re
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any, Literal, Self, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.resy.errors import ResySchemaError

logger = logging.getLogger("app.resy")

RESY_WEB = "https://resy.com"
_SLOT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_TAG_RE = re.compile(r"<[^>]+>")


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


M = TypeVar("M", bound=BaseModel)


def parse_response(model: type[M], data: Any, endpoint: str) -> M:
    """Validate a response, raising ResySchemaError with field paths only (never values)."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        paths = [".".join(str(part) for part in err["loc"]) for err in exc.errors()]
        raise ResySchemaError(
            f"Unexpected response shape from {endpoint}.", field_paths=paths
        ) from exc


# --- raw: shared ----------------------------------------------------------------------------


class ResyIdRaw(_Raw):
    resy: int


class ReopenRaw(_Raw):
    date: dt.date | None = None  # dt.date: a bare `date` would resolve to this field's default


class TemplateRaw(_Raw):
    is_paid: bool = False
    cancellation_fee: float | None = None
    deposit_fee: float | None = None


class SlotConfigRaw(_Raw):
    id: int | None = None
    type: str | None = None
    token: str | None = None


class SlotDateRaw(_Raw):
    start: str
    end: str


class IdRefRaw(_Raw):
    id: int | None = None


class ServiceRaw(_Raw):
    type: IdRefRaw | None = None


class ShiftRaw(_Raw):
    service: ServiceRaw | None = None


class ExclusiveRaw(_Raw):
    is_eligible: bool | None = None


class SlotSizeRaw(_Raw):
    min: int | None = None
    max: int | None = None


class SlotPaymentRaw(_Raw):
    is_paid: bool = False
    is_add_on_required: bool = False
    cancellation_fee: float | None = None
    deposit_fee: float | None = None


class SlotRaw(_Raw):
    """A slot from /4/find or from a search hit's `availability.slots`. Search slots have no
    `payment` or `size`; their payment info lives in the hit's `templates` map."""

    config: SlotConfigRaw
    date: SlotDateRaw
    template: IdRefRaw | None = None
    shift: ShiftRaw | None = None
    exclusive: ExclusiveRaw | None = None
    is_global_dining_access: bool = False
    size: SlotSizeRaw | None = None
    payment: SlotPaymentRaw | None = None


# --- raw: venue search (POST /3/venuesearch/search) ----------------------------------------


class SearchLocationRaw(_Raw):
    name: str | None = None
    url_slug: str | None = None


class SearchRatingRaw(_Raw):
    average: float | None = None
    count: int | None = None


class GeoLocRaw(_Raw):
    lat: float
    lng: float


class SearchAvailabilityRaw(_Raw):
    slots: list[SlotRaw] = []
    templates: dict[str, TemplateRaw] = {}


class SearchHitRaw(_Raw):
    id: ResyIdRaw
    name: str
    neighborhood: str | None = None
    locality: str | None = None
    location: SearchLocationRaw | None = None
    cuisine: list[str] = []
    price_range_id: int | None = None
    currency_symbol: str | None = None
    rating: SearchRatingRaw | None = None
    url_slug: str | None = None
    geoloc: GeoLocRaw | None = Field(default=None, alias="_geoloc")
    max_party_size: int | None = None
    reopen: ReopenRaw | None = None
    is_tock_inventory: bool = False
    feature_recaptcha: bool = False
    gda_concierge_booking: bool = False
    requires_reservation_transfers: int = 0
    availability: SearchAvailabilityRaw | None = None


class SearchResultsRaw(_Raw):
    hits: list[SearchHitRaw] = []
    nbHits: int | None = None
    cuisines: list[str] = []  # cuisine facet labels for this result set


class VenueSearchResponse(_Raw):
    search: SearchResultsRaw


# --- raw: /4/find ---------------------------------------------------------------------------


class FindGeoRaw(_Raw):
    lat: float
    lon: float


class FindLocationRaw(_Raw):
    time_zone: str | None = None
    neighborhood: str | None = None
    geo: FindGeoRaw | None = None
    name: str | None = None
    url_slug: str | None = None


class FindVenueRaw(_Raw):
    id: ResyIdRaw
    name: str
    type: str | None = None
    price_range: int | None = None
    location: FindLocationRaw | None = None
    rating: float | None = None
    total_ratings: int | None = None
    url_slug: str | None = None
    reopen: ReopenRaw | None = None
    currency_symbol: str | None = None
    feature_recaptcha: bool = False
    gda_concierge_booking: bool = False
    requires_reservation_transfers: int = 0


class FindVenueResultRaw(_Raw):
    venue: FindVenueRaw
    slots: list[SlotRaw] = []
    templates: dict[str, TemplateRaw] = {}


class FindResultsRaw(_Raw):
    venues: list[FindVenueResultRaw] = []


class FindResponse(_Raw):
    results: FindResultsRaw


# --- raw: /4/venue/calendar -----------------------------------------------------------------


class CalendarInventoryRaw(_Raw):
    reservation: str | None = None
    walk_in: str | None = Field(default=None, alias="walk-in")


class CalendarEntryRaw(_Raw):
    date: date
    inventory: CalendarInventoryRaw


class CalendarResponse(_Raw):
    last_calendar_day: date
    scheduled: list[CalendarEntryRaw] = []


# --- raw: /3/venue and /3/details (shared venue pieces) -------------------------------------


class ContentRaw(_Raw):
    name: str | None = None
    body: str | None = None


class AddressRaw(_Raw):
    address_1: str | None = None
    address_2: str | None = None
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = None
    neighborhood: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    url_slug: str | None = None


class ContactRaw(_Raw):
    phone_number: str | None = None
    url: str | None = None


class RaterRaw(_Raw):
    score: float | None = None
    total: int | None = None


class LinksRaw(_Raw):
    web: str | None = None


class VenueResponse(_Raw):
    id: ResyIdRaw
    name: str
    type: str | None = None
    price_range_id: int | None = None
    currency_symbol: str | None = None
    location: AddressRaw | None = None
    contact: ContactRaw | None = None
    rater: list[RaterRaw] = []
    content: list[ContentRaw] = []
    links: LinksRaw | None = None
    max_party_size: int | None = None
    min_party_size: int | None = None
    reopen: ReopenRaw | None = None


class CutOffRaw(_Raw):
    date_cut_off: datetime | None = None


class PolicyDisplayRaw(_Raw):
    policy: list[str] = []


class CancellationRaw(_Raw):
    fee: Any = None
    refund: CutOffRaw | None = None
    display: PolicyDisplayRaw | None = None


class PaymentConfigRaw(_Raw):
    type: str | None = None


class AmountsRaw(_Raw):
    total: float | None = None


class DetailsPaymentRaw(_Raw):
    config: PaymentConfigRaw | None = None
    amounts: AmountsRaw | None = None


class DetailsVenueRaw(_Raw):
    content: list[ContentRaw] = []
    location: AddressRaw | None = None
    contact: ContactRaw | None = None


class BookTokenRaw(_Raw):
    value: str
    date_expires: datetime | None = None


class DetailsResponse(_Raw):
    cancellation: CancellationRaw | None = None
    change: CutOffRaw | None = None
    payment: DetailsPaymentRaw | None = None
    venue: DetailsVenueRaw | None = None
    book_token: BookTokenRaw | None = None


class BookResponse(_Raw):
    reservation_id: int
    resy_token: str
    venue_opt_in: bool | None = None


# --- domain helpers -------------------------------------------------------------------------


def strip_markup(text: str) -> str:
    """Search can return highlight markup (e.g. <b>); it never reaches the LLM."""
    return _TAG_RE.sub("", text).strip()


def clean_neighborhood(value: str | None, *generic: str | None) -> str | None:
    """A neighborhood that's empty or equal to the city/locality (e.g. "New York") is
    unknown: it never matches a neighborhood filter."""
    if not value or not value.strip():
        return None
    generic_names = {g.strip().casefold() for g in generic if g}
    return None if value.strip().casefold() in generic_names else value.strip()


def render_price_range(price_range_id: int | None, currency_symbol: str | None) -> str | None:
    if not price_range_id or price_range_id < 1:
        return None
    return (currency_symbol or "$") * price_range_id


def venue_web_url(city_slug: str | None, venue_slug: str | None) -> str | None:
    if not city_slug or not venue_slug:
        return None
    return f"{RESY_WEB}/cities/{city_slug}/venues/{venue_slug}"


def _content_body(content: list[ContentRaw], name: str) -> str | None:
    for item in content:
        if item.name == name and item.body and item.body.strip():
            return item.body.strip()
    return None


def _format_address(loc: AddressRaw | None) -> str | None:
    if loc is None:
        return None
    region_postal = " ".join(p for p in (loc.region, loc.postal_code) if p)
    parts = [loc.address_1, loc.address_2, loc.locality, region_postal]
    joined = ", ".join(p.strip() for p in parts if p and p.strip())
    return joined or None


def _not_bookable_reason(
    *,
    tock: bool,
    recaptcha: bool,
    concierge: bool,
    transfers: bool,
    reopen: date | None,
    today: date,
) -> str | None:
    if tock:
        return "booked through Tock, not Resy"
    if recaptcha:
        return "requires a reCAPTCHA check on resy.com"
    if concierge:
        return "concierge booking only"
    if transfers:
        return "requires reservation transfers"
    if reopen is not None and reopen > today:
        return f"temporarily closed until {reopen.isoformat()}"
    return None


# --- domain models --------------------------------------------------------------------------


class ReservationQuery(BaseModel):
    query: str | None = None
    cuisine: str | None = None
    neighborhood: str | None = None
    venue_id: int | None = None
    date: dt.date | None = None
    party_size: int | None = Field(default=None, ge=1, le=20)
    requested_time: time | None = None
    time_start: time | None = None
    time_end: time | None = None
    time_precision: Literal["exact", "approximate", "range", "any"] | None = None

    @model_validator(mode="after")
    def _needs_a_target(self) -> Self:
        if not (self.query or self.cuisine or self.neighborhood or self.venue_id):
            raise ValueError("one of query, cuisine, neighborhood, or venue_id is required")
        return self


class Venue(BaseModel):
    id: int
    name: str
    neighborhood: str | None
    city: str | None
    cuisine: list[str]
    price_range: str | None
    rating: float | None
    rating_count: int | None
    url: str | None
    lat: float | None
    lng: float | None
    max_party_size: int | None
    reopen_date: date | None
    bookable_via_agent: bool
    not_bookable_reason: str | None

    @classmethod
    def from_search_hit(cls, hit: SearchHitRaw, today: date) -> Self:
        city = hit.location.name if hit.location else None
        city_slug = hit.location.url_slug if hit.location else None
        reopen = hit.reopen.date if hit.reopen else None
        # The venue-level is_global_dining_access flag is deliberately ignored: venues with it
        # set still have normal bookable slots (bookability is decided per slot).
        reason = _not_bookable_reason(
            tock=hit.is_tock_inventory,
            recaptcha=hit.feature_recaptcha,
            concierge=hit.gda_concierge_booking,
            transfers=bool(hit.requires_reservation_transfers),
            reopen=reopen,
            today=today,
        )
        return cls(
            id=hit.id.resy,
            name=strip_markup(hit.name),
            neighborhood=clean_neighborhood(hit.neighborhood, hit.locality, city),
            city=city,
            cuisine=hit.cuisine,
            price_range=render_price_range(hit.price_range_id, hit.currency_symbol),
            rating=hit.rating.average if hit.rating else None,
            rating_count=hit.rating.count if hit.rating else None,
            url=venue_web_url(city_slug, hit.url_slug),
            lat=hit.geoloc.lat if hit.geoloc else None,
            lng=hit.geoloc.lng if hit.geoloc else None,
            max_party_size=hit.max_party_size,
            reopen_date=reopen,
            bookable_via_agent=reason is None,
            not_bookable_reason=reason,
        )

    @classmethod
    def from_find_venue(cls, venue: FindVenueRaw, today: date) -> Self:
        loc = venue.location
        reopen = venue.reopen.date if venue.reopen else None
        reason = _not_bookable_reason(
            tock=False,
            recaptcha=venue.feature_recaptcha,
            concierge=venue.gda_concierge_booking,
            transfers=bool(venue.requires_reservation_transfers),
            reopen=reopen,
            today=today,
        )
        return cls(
            id=venue.id.resy,
            name=strip_markup(venue.name),
            neighborhood=clean_neighborhood(
                loc.neighborhood if loc else None, loc.name if loc else None
            ),
            city=loc.name if loc else None,
            cuisine=[venue.type] if venue.type else [],
            price_range=render_price_range(venue.price_range, venue.currency_symbol),
            rating=venue.rating,
            rating_count=venue.total_ratings,
            url=venue_web_url(loc.url_slug if loc else None, venue.url_slug),
            lat=loc.geo.lat if loc and loc.geo else None,
            lng=loc.geo.lon if loc and loc.geo else None,
            max_party_size=None,
            reopen_date=reopen,
            bookable_via_agent=reason is None,
            not_bookable_reason=reason,
        )


class Slot(BaseModel):
    venue_id: int
    start: datetime
    end: datetime
    party_size: int
    size_min: int | None
    size_max: int | None
    seating_type: str | None
    template_id: int | None
    service_type_id: int | None
    requires_payment: bool
    bookable: bool
    config_token: str | None = Field(repr=False)  # server-side only; never sent to the LLM

    @classmethod
    def from_raw(
        cls,
        raw: SlotRaw,
        *,
        venue_id: int,
        party_size: int,
        tz: ZoneInfo,
        templates: dict[str, TemplateRaw],
    ) -> Self:
        token = raw.config.token
        template_id = raw.template.id if raw.template else None
        service = raw.shift.service if raw.shift else None
        return cls(
            venue_id=venue_id,
            start=_local_datetime(raw.date.start, tz),
            end=_local_datetime(raw.date.end, tz),
            party_size=party_size,
            size_min=raw.size.min if raw.size else None,
            size_max=raw.size.max if raw.size else None,
            seating_type=_seating_type(token, raw.config.type),
            template_id=template_id,
            service_type_id=service.type.id if service and service.type else None,
            requires_payment=_requires_payment(raw.payment, templates.get(str(template_id))),
            bookable=(
                token is not None
                and not raw.is_global_dining_access
                and not (raw.exclusive is not None and raw.exclusive.is_eligible is False)
            ),
            config_token=token,
        )


def _local_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Slot times are venue-local strings; searches are local, so the user's timezone is
    the venue's timezone."""
    return datetime.strptime(value, _SLOT_TIME_FORMAT).replace(tzinfo=tz)


def _seating_type(token: str | None, config_type: str | None) -> str | None:
    """The token's last segment is what gets booked (e.g. "Balcony"); config.type can be a
    service label ("Standard", "BREAKFAST", "Dinner ")."""
    if token and "/" in token:
        segment = token.rsplit("/", 1)[1].strip()
        if segment:
            return segment
    return config_type.strip() if config_type and config_type.strip() else None


def _requires_payment(payment: SlotPaymentRaw | None, template: TemplateRaw | None) -> bool:
    """Paid if either the slot's payment object or its template says so. With neither,
    payment status is unknown, so treat the slot as paid (never book on a guess)."""
    if payment is None and template is None:
        return True
    paid = False
    if payment is not None:
        paid = (
            payment.is_paid
            or payment.is_add_on_required
            or payment.cancellation_fee is not None
            or payment.deposit_fee is not None
        )
    if template is not None:
        paid = (
            paid
            or template.is_paid
            or template.cancellation_fee is not None
            or template.deposit_fee is not None
        )
    return paid


def dedupe_slots(slots: list[Slot]) -> list[Slot]:
    """Several config.ids (different tables) can share one token; keep the first, in order."""
    seen: set[str] = set()
    unique: list[Slot] = []
    for slot in slots:
        if slot.config_token is not None:
            if slot.config_token in seen:
                continue
            seen.add(slot.config_token)
        unique.append(slot)
    return unique


class Availability(StrEnum):
    AVAILABLE = "available"
    SOLD_OUT = "sold_out"
    NOT_AVAILABLE = "not_available"
    UNKNOWN = "unknown"


_CALENDAR_VALUES = {
    "available": Availability.AVAILABLE,
    "sold-out": Availability.SOLD_OUT,
    "not available": Availability.NOT_AVAILABLE,
}


def _availability(value: str | None) -> Availability:
    if value is None:
        return Availability.UNKNOWN
    mapped = _CALENDAR_VALUES.get(value)
    if mapped is None:
        logger.warning("resy_unknown_calendar_value", extra={"endpoint": "/4/venue/calendar"})
        return Availability.UNKNOWN
    return mapped


class CalendarDay(BaseModel):
    date: date
    reservation: Availability
    walk_in: Availability


class VenueCalendar(BaseModel):
    venue_id: int
    last_calendar_day: date
    days: list[CalendarDay]

    @classmethod
    def from_response(cls, raw: CalendarResponse, venue_id: int) -> Self:
        return cls(
            venue_id=venue_id,
            last_calendar_day=raw.last_calendar_day,
            days=[
                CalendarDay(
                    date=entry.date,
                    reservation=_availability(entry.inventory.reservation),
                    walk_in=_availability(entry.inventory.walk_in),
                )
                for entry in raw.scheduled
            ],
        )


class VenueDetails(BaseModel):
    id: int
    name: str
    cuisine: str | None
    price_range: str | None
    neighborhood: str | None
    address: str | None
    phone: str | None
    website: str | None
    url: str | None
    rating: float | None
    rating_count: int | None
    description: str | None
    about: str | None
    need_to_know: str | None
    min_party_size: int | None
    max_party_size: int | None
    reopen_date: date | None

    @classmethod
    def from_response(cls, raw: VenueResponse) -> Self:
        loc = raw.location
        rater = raw.rater[0] if raw.rater else None
        return cls(
            id=raw.id.resy,
            name=strip_markup(raw.name),
            cuisine=raw.type,
            price_range=render_price_range(raw.price_range_id, raw.currency_symbol),
            neighborhood=clean_neighborhood(
                loc.neighborhood if loc else None, loc.locality if loc else None
            ),
            address=_format_address(loc),
            phone=raw.contact.phone_number if raw.contact else None,
            website=raw.contact.url if raw.contact else None,
            url=raw.links.web if raw.links else None,
            rating=rater.score if rater else None,
            rating_count=rater.total if rater else None,
            description=_content_body(raw.content, "why_we_like_it"),
            about=_content_body(raw.content, "about"),
            need_to_know=_content_body(raw.content, "need_to_know"),
            min_party_size=raw.min_party_size,
            max_party_size=raw.max_party_size,
            reopen_date=raw.reopen.date if raw.reopen else None,
        )


class BookingDetails(BaseModel):
    book_token: str | None = Field(repr=False)  # server-side only; issued with commit=1
    book_token_expires: datetime | None
    payment_type: str | None
    total: float | None
    cancellation_fee: Any
    refund_cutoff: datetime | None
    change_cutoff: datetime | None
    policy_text: str | None
    description: str | None
    address: str | None
    phone: str | None
    is_free: bool

    @classmethod
    def from_response(cls, raw: DetailsResponse) -> Self:
        cancellation = raw.cancellation
        payment = raw.payment
        payment_type = payment.config.type if payment and payment.config else None
        total = payment.amounts.total if payment and payment.amounts else None
        fee = cancellation.fee if cancellation else None
        policy = cancellation.display.policy if cancellation and cancellation.display else []
        venue = raw.venue
        return cls(
            book_token=raw.book_token.value if raw.book_token else None,
            book_token_expires=raw.book_token.date_expires if raw.book_token else None,
            payment_type=payment_type,
            total=total,
            cancellation_fee=fee,
            refund_cutoff=cancellation.refund.date_cut_off
            if cancellation and cancellation.refund
            else None,
            change_cutoff=raw.change.date_cut_off if raw.change else None,
            policy_text=" ".join(p.strip() for p in policy if p.strip()) or None,
            description=_content_body(venue.content, "why_we_like_it") if venue else None,
            address=_format_address(venue.location) if venue else None,
            phone=venue.contact.phone_number if venue and venue.contact else None,
            is_free=payment_type == "free" and total == 0 and fee is None,
        )


class BookingResult(BaseModel):
    reservation_id: int
    resy_token: str = Field(repr=False)  # can cancel the booking: never logged or returned

    @classmethod
    def from_response(cls, raw: BookResponse) -> Self:
        return cls(reservation_id=raw.reservation_id, resy_token=raw.resy_token)
