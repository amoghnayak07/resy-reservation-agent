# Stage 5 — Resy client + pydantic models

**Goal:** A tested, typed Resy client in `app/resy/` with auth, token refresh, error classification, and domain models, built from real DevTools captures. No agent changes yet.

**Suggested branch (you create it):** `stage-05-resy-client-models`
**Depends on:** Stage 1 (independent of stages 2–4, but build in order)

## Steps

### 1. Capture and scrub fixtures (user provides captures; Claude scrubs)

Needed captures (request URL/method/params/body + response JSON) from resy.com DevTools:

- **Already captured** (see PLAN.md; save full response JSON as fixtures): venue search, `/4/venue/calendar`, `/4/find`, `/3/details` (`commit: 0`), `/3/book`.
- **Also captured:** `/3/details` with `commit: 1` returns the full details **and** `book_token {value, date_expires}`. An expanded `/4/find` slot (fields in PLAN.md). All three of `/4/find`, `/3/details`, `/3/book` are POST with `Content-Type: application/json`.
- **Still needed:** `/3/venue`; current user; the token refresh call (or email/password login).
- Venue search fixtures should include: a fuzzy name query (e.g., "amori" → "Mori"), an exact name query, a cuisine query (e.g., "indian", including the INDIAN TABLE hit with a past `reopen.date`), and a geo-only neighborhood query. Payload variants themselves are probed in stage 6.
- `/3/book` fixture: scrub `book_token` and `resy_token` (a `resy_token` can cancel the real reservation).

Scrub tokens (auth, book, `resy_token`), emails, names, phone numbers, payment method IDs. Save to `tests/fixtures/resy/<endpoint>.json`. **If a capture is missing, stop and ask; don't guess shapes.**

### 2. Minimal header probe (`scripts/resy_probe.py`, local only)

Read-only calls to the current-user endpoint and `/4/find` with progressively smaller header sets:

1. `Authorization: ResyAPI api_key="…"` + `X-Resy-Auth-Token` + `X-Resy-Universal-Auth`.
2. If that fails, add browser-like `Origin`/`Referer: https://resy.com` and `User-Agent`.
3. If that fails, add the `token_v2` cookie.

Record the minimal working set in Notes and in PLAN.md.

### 3. Settings

Add `RESY_API_KEY`, `RESY_AUTH_TOKEN`, `RESY_REFRESH_TOKEN`, `RESY_EMAIL`, `RESY_PASSWORD`, `RESY_WRITES_ENABLED` (default false) to `config.py` and `.env.example`.

### 4. Auth (`app/resy/auth.py`)

- `TokenManager` holds the current auth token in memory, seeded from env.
- Decode the JWT `exp` without signature verification. If it expires within 3 days, refresh proactively.
- On a 401: refresh once (refresh token if the endpoint was captured, else email/password login), retry the request once, then raise `ResyAuthError`.
- In-memory only (tokens last ~45 days; a cold start re-seeds from env). Note this trade-off in README.

### 5. Errors (`app/resy/errors.py`)

`ResyError` base with: `ResyAuthError` (401 after refresh), `ResyBlockedError` (403 / challenge page), `ResyRateLimitedError` (429), `ResyUpstreamError` (5xx, timeouts), `ResyNotFoundError` (404), `ResySchemaError` (pydantic validation failure; log field paths, never values).

### 6. Client (`app/resy/client.py`)

- `httpx.AsyncClient`, base `https://api.resy.com`, timeout 10s, header set from step 2.
- Read methods (shapes from captures): `venue_search(...)`, `find(venue_id, day, party_size)` (POST; sends `lat=0`, `long=0` as captured), `get_details(config_id, day, party_size, commit)`, `get_venue(venue_id)`, `get_calendar(venue_id, num_seats, start_date, end_date)`.
- Reads retry up to 2 times on `ResyUpstreamError` with backoff.
- Write method `book(book_token)`: sends `book_token`, `source_id="resy.com-venue-details"`, `venue_marketing_opt_in=0`; no payment method. Raises unless `RESY_WRITES_ENABLED` is true **and** the caller passes `allow_write=True`. Never retried.
- Every method logs endpoint, status, latency (no headers, no tokens).

### 7. Models (`app/resy/models.py`)

Adapt from `jeffknaide/resy-bot` (MIT; credit in README). Two layers:

- **Raw response models** mirroring captures, `extra="ignore"` so new fields don't break parsing.
- **Domain models** used by tools:
  - `ReservationQuery`: optional `query` (restaurant name), `cuisine`, `neighborhood`, `venue_id`; optional `date` and `party_size` (1–20), since venue resolution works without them; optional `requested_time`, `time_start`, `time_end`, `time_precision` (`exact | approximate | range | any`). Validator: at least one of `query`, `cuisine`, `neighborhood`, `venue_id`.
  - `Venue` (from search hits): `id` (`id.resy`), `name`, `neighborhood`, `city` (`location.name`), `cuisine` (list), `price_range` (from `price_range_id`, rendered with the hit's `currency_symbol`, e.g. `"$$"`), `rating` + `rating_count`, `url` (built from `location.url_slug` + `url_slug`; verify format), `lat`/`lng` (`_geoloc`), `max_party_size`, `reopen_date`, `bookable_via_agent` + `not_bookable_reason` (derived from `is_tock_inventory`, `feature_recaptcha`, `gda_concierge_booking` / `is_global_dining_access`, `requires_reservation_transfers`, and `reopen.date` **only when it's in the future**; past reopen dates like `2021-07-07` are common).
  - **Field names differ by endpoint; map each explicitly.** Search hits: `_geoloc {lat, lng}`, `cuisine[]`, `rating {average, count}`, `price_range_id`, `neighborhood`. `/4/find` venue: `location.geo {lat, lon}`, `type` (single string), `rating` (number) + `total_ratings`, `price_range`, `location.neighborhood`.
  - **Generic neighborhoods:** a `neighborhood` equal to the city or locality (e.g., Amor Loco's "New York") is treated as unknown: it never matches a neighborhood filter.
  - `Slot`: `venue_id`, `start` / `end` (from `date.start` / `date.end`, venue-local strings made timezone-aware with the user's timezone; searches are local), `party_size`, `size_min` / `size_max`, `seating_type` (`config.type`, e.g. "Dining Room"), `template_id` (`template.id`), `service_type_id` (`shift.service.type.id`), `requires_payment` (true if `payment.is_paid`, `payment.is_add_on_required`, or a non-null `payment.deposit_fee` / `payment.cancellation_fee`; the `templates` map's `is_paid` is a cross-check), `bookable` (false if `is_global_dining_access` or `exclusive.is_eligible` is false), `config_token` (`config.token`, the `rgs://resy/…` string passed as `config_id` to `/3/details`; server-side only; never built by hand).
  - **Dedupe slots by `config_token`.** Several `config.id`s (different tables) can share the same time, seating type, and token. Seating types come from `config.type`, not `config.id`. Ignore `config.is_visible` (it's false on bookable slots).
  - `CalendarDay`: `date`, `reservation` (`available | not_available | unknown`), `walk_in` (`available | not_available | unknown`).
  - `VenueCalendar`: `venue_id`, `last_calendar_day`, `days`.
  - `VenueDetails`.
  - `BookingDetails` (from `/3/details`): `book_token` + `book_token_expires` (from `book_token.value` / `date_expires`, only with `commit: 1`; server-side only), `payment_type` (`payment.config.type`), `total` (`payment.amounts.total`), `cancellation_fee` (`cancellation.fee`), `refund_cutoff`, `change_cutoff`, `policy_text` (`cancellation.display.policy[]` joined), `description` (`venue.content[]` entry named `why_we_like_it`), `address`, `phone`, and `is_free` = payment type `free` + `total == 0` + no cancellation fee.
  - `BookingResult`: `reservation_id`, `resy_token` (can cancel the booking: never logged, traced, or returned to the LLM or frontend).
- Unknown enum values from Resy map to `unknown` with a logged warning (don't fail the whole response).

## Tests (all offline, mocked transport)

- Every fixture parses into raw and domain models.
- `/4/find`: slots map to `Slot` with `config_token` (`config.token`), `seating_type` (`config.type`), `template_id`, `size_min`/`size_max`, `start`/`end`, and `requires_payment` from the slot's `payment` object (cross-checked with `templates`); `exclusive.is_eligible: false` or `is_global_dining_access: true` → `bookable: false`; two `config.id`s with the same token collapse into one slot (fixture: configs 1829819/1829820 at 12:00, both "Dining Room"); two different `config.type`s at one time stay as two seating types; a 168-slot fixture (Amor Loco) parses; `travel_time` is ignored; "New York" as neighborhood → unknown.
- `/3/details`: `commit: 0` fixture → `is_free` true, cut-offs parsed as timezone-aware datetimes, no book token; `commit: 1` fixture → same fields plus `book_token` and `book_token_expires`; policy text and `why_we_like_it` description extracted; synthetic nonzero `total` → `is_free` false.
- Request bodies for `/4/find`, `/3/details`, `/3/book` are sent as JSON.
- Venue search: hits map to `Venue` with correct price range, URL, and `bookable_via_agent`; highlight markup stripped from names; `ReservationQuery` rejects a query with no `query`/`neighborhood`/`venue_id`.
- Calendar: `"not available"` → `not_available`; an unseen value → `unknown` + warning.
- TokenManager: proactive refresh near expiry; 401 → refresh → retry succeeds; second 401 → `ResyAuthError`.
- Error classification for 403, 404, 429, 500, timeout, and malformed JSON.
- `book()` raises when writes are disabled or `allow_write` is missing; sends no payment method; parses `reservation_id` and `resy_token` from the fixture.

## Exit criteria

- [ ] Fixtures captured and scrubbed for all read endpoints.
- [ ] Minimal header set confirmed and documented.
- [ ] `resy_probe.py` succeeds locally with the documented headers.
- [ ] All tests pass with no network access.
- [ ] PLAN.md endpoint table updated with verified details.

## Out of scope

Agent tools, graph changes, booking execution.

## Notes

_(Fill in: minimal header set, refresh endpoint (or fallback), verified params per endpoint.)_
