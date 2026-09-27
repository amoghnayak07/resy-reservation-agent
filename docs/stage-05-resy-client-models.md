# Stage 5 — Resy client + pydantic models

**Goal:** A tested, typed Resy client in `app/resy/` with static-token auth, error classification, and domain models, built from real DevTools captures. No agent changes yet.

**Suggested branch (you create it):** `stage-05-resy-client-models`
**Depends on:** Stage 1 (independent of stages 2–4, but build in order)

## Steps

### 1. Capture and scrub fixtures (user provides captures; Claude scrubs)

Needed captures (request URL/method/params/body + response JSON) from resy.com DevTools:

- **Captured** (see PLAN.md): venue search, `/4/venue/calendar`, `/4/find`, `/3/venue`, `/3/details` (`commit: 1`), `/3/book`. `/3/details` with `commit: 1` returns the full details **and** `book_token {value, date_expires}`; the `commit: 0` fixture is derived from it by removing `book_token`. `/4/find` and `/3/details` are POST with `Content-Type: application/json`; `/3/book` is POST with `Content-Type: application/x-www-form-urlencoded`.
- Venue search fixtures: the geo-only query (full response, trimmed to 8 real hits). Fuzzy ("amori" → "Mori") and exact ("Amor Loco") name fixtures use the one full hit shape plus the top-level fields visible in the preview captures. Payload variants themselves are probed in stage 6.
- `/3/book` fixture: scrub `book_token` and `resy_token` (a `resy_token` can cancel the real reservation).

Scrub tokens (auth, book, `resy_token`), emails, names, phone numbers, payment method IDs. Save to `tests/fixtures/resy/<endpoint>.json`. **If a capture is missing, stop and ask; don't guess shapes.**

### 2. Minimal header probe (`scripts/resy_probe.py`, local only)

Read-only calls to `/4/find` with progressively smaller header sets:

1. `Authorization: ResyAPI api_key="…"` + `X-Resy-Auth-Token` + `X-Resy-Universal-Auth`.
2. If that fails, add browser-like `Origin`/`Referer: https://resy.com` and `User-Agent`.
3. If that fails, add the `token_v2` cookie.

Record the minimal working set in Notes and in PLAN.md.

### 3. Settings

Add `RESY_API_KEY`, `RESY_AUTH_TOKEN`, `RESY_WRITES_ENABLED` (default false) to `config.py` and `.env.example`.

### 4. Auth (`app/resy/auth.py`)

- `auth.py` builds the request headers from `RESY_API_KEY` and `RESY_AUTH_TOKEN` (static, no refresh; the demo runs about a week).
- Any 401 raises `ResyAuthError` ("Resy token expired or invalid; rotate `RESY_AUTH_TOKEN`"). No retry.
- README: token rotation is manual (resy.com → DevTools → copy the token into Render env).

### 5. Errors (`app/resy/errors.py`)

`ResyError` base with: `ResyAuthError` (401), `ResyBlockedError` (403 / challenge page), `ResyRateLimitedError` (429), `ResyUpstreamError` (5xx, timeouts), `ResyNotFoundError` (404), `ResySchemaError` (pydantic validation failure; log field paths, never values).

### 6. Client (`app/resy/client.py`)

- `httpx.AsyncClient`, base `https://api.resy.com`, timeout 10s, header set from step 2.
- Read methods (shapes from captures): `venue_search(...)`, `find(venue_id, day, party_size)` (POST; sends `lat=0`, `long=0` as captured), `get_details(config_id, day, party_size, commit)`, `get_venue(url_slug, location)` (GET `/3/venue?url_slug=…&location=…`, as captured), `get_calendar(venue_id, num_seats, start_date, end_date)`.
- Reads retry up to 2 times on `ResyUpstreamError` with backoff.
- Write method `book(book_token)`: sends a form-encoded body exactly as captured: `book_token`, `replace=1`, `source_id="resy.com-venue-details"`, `venue_marketing_opt_in=0`; no payment method. (`replace` is undocumented; mirrored from the successful capture.) Raises unless `RESY_WRITES_ENABLED` is true **and** the caller passes `allow_write=True`. Never retried.
- Every method logs endpoint, status, latency (no headers, no tokens).

### 7. Models (`app/resy/models.py`)

Two layers:

- **Raw response models** mirroring captures, `extra="ignore"` so new fields don't break parsing.
- **Domain models** used by tools:
  - `ReservationQuery`: optional `query` (restaurant name), `cuisine`, `neighborhood`, `venue_id`; optional `date` and `party_size` (1–20), since venue resolution works without them; optional `requested_time`, `time_start`, `time_end`, `time_precision` (`exact | approximate | range | any`). Validator: at least one of `query`, `cuisine`, `neighborhood`, `venue_id`.
  - `Venue` (from search hits): `id` (`id.resy`), `name`, `neighborhood`, `city` (`location.name`), `cuisine` (list), `price_range` (from `price_range_id`, rendered with the hit's `currency_symbol`, e.g. `"$$"`), `rating` + `rating_count`, `url` (`https://resy.com/cities/{location.url_slug}/venues/{url_slug}`, verified against `/3/venue` `links.web`), `url_slug` + `city_slug` (`location.url_slug`; server-side keys for `/3/venue` lookups), `lat`/`lng` (`_geoloc`), `max_party_size`, `reopen_date`, `bookable_via_agent` + `not_bookable_reason` (derived from `is_tock_inventory`, `feature_recaptcha`, `gda_concierge_booking`, `requires_reservation_transfers`, and `reopen.date` **only when it's in the future**; past reopen dates like `2021-07-07` are common). The venue-level `is_global_dining_access` is ignored: venues with it set still have normal bookable slots (e.g., Le Gratin).
  - **Field names differ by endpoint; map each explicitly.** Search hits: `_geoloc {lat, lng}`, `cuisine[]`, `rating {average, count}`, `price_range_id`, `neighborhood`. `/4/find` venue: `location.geo {lat, lon}`, `type` (single string), `rating` (number) + `total_ratings`, `price_range`, `location.neighborhood`.
  - **Generic neighborhoods:** a `neighborhood` equal to the city or locality (e.g., Amor Loco's "New York") is treated as unknown: it never matches a neighborhood filter.
  - `Slot`: `venue_id`, `start` / `end` (from `date.start` / `date.end`, venue-local strings made timezone-aware with the user's timezone; searches are local), `party_size`, `size_min` / `size_max`, `seating_type` (last segment of `config.token`, trimmed; falls back to `config.type`, which can be a service label like "Standard", "BREAKFAST", or "Dinner "), `template_id` (`template.id`), `service_type_id` (`shift.service.type.id`), `requires_payment` (`/4/find`: true if `payment.is_paid`, `payment.is_add_on_required`, or a non-null `payment.deposit_fee` / `payment.cancellation_fee`. Search results have no slot `payment` object, so use `templates[template.id]`: `is_paid`, or a non-null `cancellation_fee` / `deposit_fee`), `bookable` (false if the slot's `is_global_dining_access` is true, `exclusive.is_eligible` is false, or `config.token` is null), `config_token` (`config.token`, the `rgs://resy/…` string passed as `config_id` to `/3/details`; server-side only; never built by hand).
  - **Dedupe slots by `config_token`.** Several `config.id`s (different tables) can share the same time, seating type, and token. Seating types come from the token's last segment, not `config.id` or `config.type`. Ignore `config.is_visible` (it's false on bookable slots).
  - `CalendarDay`: `date`, `reservation` (`available | sold_out | not_available | unknown`), `walk_in` (same values). `sold_out` (Resy's `"sold-out"`) means fully booked, distinct from a date after `last_calendar_day` that isn't released yet.
  - `VenueCalendar`: `venue_id`, `last_calendar_day`, `days`.
  - `VenueDetails`.
  - `BookingDetails` (from `/3/details`): `book_token` + `book_token_expires` (from `book_token.value` / `date_expires`, only with `commit: 1`; server-side only), `payment_type` (`payment.config.type`), `total` (`payment.amounts.total`), `cancellation_fee` (`cancellation.fee`), `refund_cutoff`, `change_cutoff`, `policy_text` (`cancellation.display.policy[]` joined), `description` (`venue.content[]` entry named `why_we_like_it`), `address`, `phone`, and `is_free` = payment type `free` + `total == 0` + no cancellation fee.
  - `BookingResult`: `reservation_id`, `resy_token` (can cancel the booking: never logged, traced, or returned to the LLM or frontend).
- Unknown enum values from Resy map to `unknown` with a logged warning (don't fail the whole response).

## Tests (all offline, mocked transport)

- Every fixture parses into raw and domain models.
- Search slots: 1803 → "Balcony"; Holywater → "Dining Room" and "Lounge"; Artesano's lunch slot `requires_payment: true` via its template; Le Gratin `bookable_via_agent: true` despite the venue-level Global Dining Access flag; icca (Tock) slots `bookable: false`.
- `/4/find`: slots map to `Slot` with `config_token` (`config.token`), `seating_type` (token's last segment), `template_id`, `size_min`/`size_max`, `start`/`end`, and `requires_payment` from the slot's `payment` object (cross-checked with `templates`); `exclusive.is_eligible: false` or `is_global_dining_access: true` → `bookable: false`; the 33-slot Brooklyn Chop House fixture parses; two `config.id`s with the same token collapse into one slot (a real slot copied under a different `config.id`); two seating types at one time stay separate (Kesté's 11:00 slots in the search fixture); `travel_time` is ignored; "New York" as neighborhood → unknown.
- `/3/details`: `commit: 0` fixture → `is_free` true, cut-offs parsed as timezone-aware datetimes, no book token; `commit: 1` fixture → same fields plus `book_token` and `book_token_expires`; policy text and `why_we_like_it` description extracted; synthetic nonzero `total` → `is_free` false.
- Request bodies for `/4/find` and `/3/details` are sent as JSON; `/3/book` as form-encoded with `replace=1`.
- Venue search: hits map to `Venue` with correct price range, URL, and `bookable_via_agent`; highlight markup stripped from names; `ReservationQuery` rejects a query with no `query`/`cuisine`/`neighborhood`/`venue_id`.
- Calendar: `"not available"` → `not_available`; `"sold-out"` → `sold_out`; an unseen value → `unknown` + warning.
- Auth: any 401 (read or `book()`) → `ResyAuthError` with no retry.
- Error classification for 403, 404, 429, 500, timeout, and malformed JSON.
- `book()` raises when writes are disabled or `allow_write` is missing; sends no payment method; parses `reservation_id` and `resy_token` from the fixture.

## Exit criteria

- [x] Fixtures captured and scrubbed for all read endpoints.
- [x] Minimal header set confirmed and documented.
- [x] `resy_probe.py` succeeds locally with the documented headers.
- [x] All tests pass with no network access.
- [x] PLAN.md endpoint table updated with verified details.

## Out of scope

Agent tools, graph changes, booking execution.

## Notes

- **Minimal headers:** `Authorization`, `X-Resy-Auth-Token`, `X-Resy-Universal-Auth`, plus a browser `User-Agent` (in `app/resy/auth.py`). Without the User-Agent Resy returns **500, not 403**, so a missing/blocked UA looks like an upstream error. No cookies, `Origin`, or `Referer` needed. Not yet verified from Render's IPs (stage 6, step 13).
- **Verified params per endpoint:** see the PLAN.md endpoint table. `/3/venue` is looked up by `url_slug` + city `location` slug (no venue-ID lookup observed). `/3/book` is form-encoded with an undocumented `replace=1`, mirrored from the capture.
- **Search hits embed slots** (`availability.slots` + `templates`) when the payload has `availability: true` + `slot_filter`, but those slots have no `payment` or `size`: payment comes from the template, and the party-size fit is unknown until `/4/find`. Stage 6 can decide whether search slots replace per-venue `/4/find`.
- **`config.type` isn't reliably the seating type** (e.g. 1803: type "Dining Room", token "…/Balcony"; Holywater: type "Standard"). Seating type = the token's last segment.
- **Venue-level `is_global_dining_access: true`** (Le Gratin, Temple Court) doesn't block booking; slots there are normal. Only the slot-level flag, `exclusive.is_eligible: false`, or a null token (Tock) make a slot unbookable.
- **`requires_payment` defaults to true** when a slot has neither a `payment` object nor a matching template, so unknown payment status is never booked.
- **`_highlightResult` carries no match signal** (`matchLevel: "none"` even on exact name matches); name matching must be our own.
- **Calendar value `"sold-out"`** exists (fully booked; mapped to `sold_out`), distinct from dates past `last_calendar_day` (not released).
- `travel_time.distance` in search hits is in miles from `geo`; unused (we compute distance ourselves).
- **Fixtures** are trimmed real responses: `venue-search-geo.json` (8 of 20 hits, template text/images trimmed), name-search fixtures built from preview captures (collapsed fields omitted), `details-commit0.json` derived from the `commit: 1` capture by removing `book_token`. Venue phone numbers, the book token, and `resy_token` are scrubbed.
