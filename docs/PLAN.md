# PLAN.md — Build Plan

## Goal and deliverables

Build a restaurant reservation agent with a chat UI on Resy.

- [ ] Deployed app with a public URL
- [ ] Agent chat UI
- [ ] Public GitHub repo + README (architecture, trade-offs, what was cut)
- [ ] Working Resy integration (real availability)
- [ ] Proof of a real booking attempt (screenshot or recording)
- [ ] In-app analytics dashboard (token usage, cost, latency per LLM call)

## Stage status

Update the checkbox when a stage's exit criteria are met and its PR is merged.

| #   | Stage                                   | File                                                               | Status |
| --- | --------------------------------------- | ------------------------------------------------------------------ | ------ |
| 1   | Skeleton + CI/CD + first deploy         | [stage-01-skeleton-ci.md](stage-01-skeleton-ci.md)                 | [x]    |
| 2   | Database + LangGraph minimum            | [stage-02-db-langgraph.md](stage-02-db-langgraph.md)               | [x]    |
| 3   | Langfuse + streaming chat API           | [stage-03-langfuse-chat-api.md](stage-03-langfuse-chat-api.md)     | [x]    |
| 4   | Guards + chat UI                        | [stage-04-guards-chat-ui.md](stage-04-guards-chat-ui.md)           | [x]    |
| 5   | Resy client + pydantic models           | [stage-05-resy-client-models.md](stage-05-resy-client-models.md)   | [x]    |
| 6   | Search tool + graph tool loop + tracing | [stage-06-search-tool.md](stage-06-search-tool.md)                 | [x]    |
| 7   | Venue details + venue calendar tools    | [stage-07-venue-details.md](stage-07-venue-details.md)             | [x]    |
| 8   | Analytics dashboard                     | [stage-08-analytics-dashboard.md](stage-08-analytics-dashboard.md) | deferred |
| 9   | Prepare booking tool                    | [stage-09-prepare-booking.md](stage-09-prepare-booking.md)         | [x]    |
| 10  | Book + confirmation gate                | [stage-10-book-confirm.md](stage-10-book-confirm.md)               | [x]    |
| 11  | Tie together: evals, proof, README      | [stage-11-tie-together.md](stage-11-tie-together.md)               | [x]    |
| 12  | Regions: city picker + any Resy city    | [stage-12-regions.md](stage-12-regions.md)                         | [x]    |

**Current stage:** none in progress. Stages 1–7 and 9–12 are done; stage 8 (the in-app dashboard) is deferred.

## Architecture overview

```
Browser (React + MUI on Vercel)
  │  fetch + X-Session-Id header (sessionStorage guest ID)
  │  chat body: message, region (city slug chosen in the region modal, kept in localStorage)
  │  SSE-formatted stream for chat
  ▼
FastAPI on Render (single instance, free tier)
  ├─ guards: rate limits (in-memory), spend cap (Postgres), input limits
  ├─ /api/regions ──► Resy city list (/3/location/config, cached 24h, slimmed)
  ├─ /api/chat ──► LangGraph agent
  │                 ├─ call_model node (OpenAI via langchain-openai)
  │                 ├─ tools node ──► app/resy/ client ──► api.resy.com
  │                 └─ interrupt() before booking ◄── /api/bookings/{id}/confirm (passcode)
  ├─ /api/analytics ──► Langfuse Metrics API (cached 60s)
  └─ Postgres (Supabase, session pooler)
        ├─ conversations, daily_spend, pending_bookings   (Alembic)
        └─ LangGraph checkpoints                            (checkpointer setup())

Langfuse Cloud ◄── traces for every LLM call, tool call, and Resy request
```

## Agent tools

**In scope**

| Tool                  | Stage | Resy endpoint            | Notes                                                                  |
| --------------------- | ----- | ------------------------ | ---------------------------------------------------------------------- |
| `search_availability` | 6     | venue search + `/4/find` | Neighborhood or restaurant-name search, open times in a window         |
| `get_venue_details`   | 7     | `/3/venue`               | Description, address, cuisine, price, etc.                             |
| `get_venue_calendar`  | 7     | `/4/venue/calendar`      | Which dates have openings; `last_calendar_day`                         |
| `prepare_booking`     | 9     | `/3/details`             | Creates a pending booking (free reservations only); no reservation yet |
| `book`                | 10    | `/3/book`                | Gated by `interrupt()` + Confirm button + passcode                     |

**Not tools (handled elsewhere)**

- Date resolution: today's date + 14-day calendar in the system prompt (stage 2).
- Region scope: searches stay within the selected Resy city (its center and radius) and use its timezone (stage 12). Neighborhoods matched by name against Resy's `neighborhood` field (stage 6).
- Conversation routing (what to ask, what to skip): prompt rules defined in stage 6, step 7.

## Decide later

These have no stage file yet. If one is approved, add a stage file before stage 11 and renumber.

| Tool                 | What it does                                                                  | Reference                                                       |
| -------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `list_reservations`  | Shows the account's upcoming bookings (read-only)                             | Capture endpoint in DevTools                                    |
| `cancel_reservation` | Cancels a booking by reservation token; must reuse the book confirmation gate | Capture endpoint in DevTools                                    |

## V2 backlog (README "what's next")

- Per-user Resy account linking instead of one demo account.
- Automatic Resy token refresh (currently rotated by hand).
- Synthetic canary + alerting (cut for budget).
- Authentication and durable per-user history.
- Reservations that require payment, a card on file, or a cancellation fee.
- Automated eval suite in CI.

## Resy endpoint reference

Source of truth is DevTools captures. "Community" means documented by open-source projects but **not yet verified** by a capture in this repo; verify before implementing. Record verified details in the stage file Notes and update this table.

| Purpose              | Method + path                | Status                  | Known details                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------- | ---------------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Venue search         | `POST /3/venuesearch/search` | **Verified 2026-09-26** | Body: `geo {latitude, longitude}`, `query`, `per_page`, `slot_filter {day, party_size}`, `types`, `include_tock_inventory`, `highlight`. The search-page payload (verified 2026-09-27, stage 6 Capture 3) also sends `availability: true`, `geo.radius` (meters), `order_by: "availability"`, `page`; with it, hits include `availability {slots[], templates{}}`. **Final payload (stage 6 probe):** search-page payload, `include_tock_inventory: false`, `geo.radius` = `SEARCH_RADIUS_KM` × 1000 (enforced server-side, text queries too; without `geo` results are global), `availability` + `slot_filter` only with date + party size (search filters slots by party size), `per_page` 10 (name) / 20 (area, cuisine), max 75. Response: `meta` (page, total, total_pages), `search.hits[]` (venue objects: `id.resy`, `name`, `neighborhood`, `cuisine[]`, `price_range_id`, `rating`, `url_slug`, `_geoloc`, `max_party_size`, `availability`, `is_tock_inventory`, `feature_recaptcha`, …). **Fuzzy:** "amori" returned "Mori" first. Text queries rank by name relevance, not `geo` ("japanese" → 1505 hits, top hit in Crown Heights, Brooklyn); structured cuisine filter unknown (capture pending, stage 6 probe #11). `reopen.date` can be in the past on open venues. Payload variants to be probed in stage 6.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Slots for venue/date | `POST /4/find`               | **Verified 2026-09-26** | JSON body: `day`, `lat`, `long` (0/0 accepted), `party_size`, `venue_id`. Response: `query`, `results.venues[]` with `slots[]` (each: `date {start, end}` as venue-local `"YYYY-MM-DD HH:MM:SS"`, `template.id`, `size {min, max}`, `payment {is_paid, is_add_on_required, cancellation_fee, deposit_fee, …}`, `shift {id, service.type.id, day}`, `is_global_dining_access`, `exclusive {is_eligible}`, `config {id, token, type, is_visible, custom_config_name}` where `token` is the `rgs://…` string used as `config_id` in `/3/details` and `type` is usually the seating type but can be a service label; the seating type is the token's last segment. Different `config.id`s can share the same time, type, and token (different tables), so dedupe by token. `is_visible: false` appears on bookable slots; don't filter on it. plus `availability`, `status`, `quantity`, `score`, `market.date {on, off}` (look like release/close epoch times; unused)), `templates` keyed by template ID (`is_paid`, `payment_structure`, …), and `venue` (`location.time_zone` e.g. `EST5EDT`, `location.neighborhood` (can be just the city, e.g. "New York"), `location.geo {lat, lon}`, `type` (single cuisine string), `rating` (number) + `total_ratings`, `currency`, `allow_bypass_payment_method`, `feature_recaptcha`, `waitlist`). A day can have 150+ slots. `travel_time.distance` is ~5.39 km for two different venues with `lat/long = 0`, so it's meaningless here; don't use it. |
| Booking details      | `POST /3/details`            | **Verified 2026-09-26** | Body: `commit` (0 = preview, 1 = issues book token), `config_id` (`rgs://resy/<venue_id>/<template_id>/<service_type>/<date>/<date>/<HH:MM:SS>/<party>/<seating>`), `day`, `party_size`. Response: `cancellation` (`fee`, `refund.date_cut_off`, `credit`, `display.policy[]`), `change.date_cut_off`, `config`, `locale` (`currency`, `time_zone`), `payment` (`config.type` e.g. `"free"`, `amounts.reservation_charge`, fees), `user.payment_methods`, `venue` (address, contact, config). `commit: 1` returns **all of the above plus** `book_token {value, date_expires}` (short-lived), so one call is enough. `payment.amounts` includes `total`, `tax`, `service_fee`, `resy_fee`, `surcharge`, `reservation_charge`. `cancellation.display.policy[]` is readable policy text. `venue.content[]` includes a `why_we_like_it` description. Whether `commit: 1` holds the table is unknown. Content-Type: `application/json`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Book                 | `POST /3/book`               | **Verified 2026-09-26** | Form-encoded body (`Content-Type: application/x-www-form-urlencoded`, verified 2026-09-27): `book_token`, `replace` (1; undocumented, mirrored from the capture), `source_id` (`resy.com-venue-details`), `venue_marketing_opt_in` (0). **No payment method needed for free reservations.** Response: `reservation_id` (int), `resy_token`, `venue_opt_in` (bool). **Never in tests.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Venue details        | `GET /3/venue`               | **Verified 2026-09-27** | Params: `id` (venue ID) **or** `url_slug` + `location` (city slug, e.g. `new-york-ny`); both verified, same response. Response: `id.resy`, `name`, `type` (cuisine string), `price_range_id`, `location {address_1, address_2, locality, region, postal_code, neighborhood, latitude, longitude, url_slug}`, `contact {phone_number, url}`, `rater[] {score, total}`, `content[]` (`why_we_like_it`, `about`, `need_to_know`, …), `links.web` (resy.com venue URL: `https://resy.com/cities/{location.url_slug}/venues/{url_slug}`), `max_party_size`, `min_party_size`, `reopen.date`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Venue calendar       | `GET /4/venue/calendar`      | **Verified 2026-09-26** | Params: `venue_id`, `num_seats` (not `party_size`), `start_date`, `end_date`. Response: `last_calendar_day`, `scheduled[]` with `date` and `inventory: {reservation, event, walk-in}`; observed values `"available"`, `"not available"`, `"sold-out"`. Window capped at `last_calendar_day` (30 days for venue 892) regardless of `end_date`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| City list            | `GET /3/location/config`     | **Verified 2026-09-27** | Same auth headers as the other reads, no params. Response: a JSON **list** of cities (2,135 in the capture; 2,025 with `show_on_web: 1`). Each: `id`, `code` (e.g. `ny`), `name`, `url_slug` (e.g. `new-york-ny`, matches search hits' `location.url_slug`), `country_code`, `country_name`, `country_id`, `latitude`, `longitude`, `map_center {latitude, longitude}`, `radius` (small integer; New York is 10, and resy.com's New York search sent `geo.radius: 16100` m, so treated as miles), `time_zone` (42 distinct: IANA names plus legacy `EST5EDT`, `PST8PDT`, `CST6CDT`, `MST7MDT`, `EST`, `MST`, `HST`, `Hongkong`; all load in `zoneinfo`), `show_on_web`, `show_in_app`, `show_in_display`, `content_tier`, `shape_data` (rare GeoJSON polygon string), `metadata` (SEO text; ~45% of the 2.8 MB body). Used by stage 12; cached 24h, slimmed. |
| Current user         | `GET /2/user` or `/4/user`   | Community               | Not used (the header probe uses `/4/find`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

**Auth headers (minimal set verified 2026-09-27 by `scripts/resy_probe.py`, local):**

- `Authorization: ResyAPI api_key="<RESY_API_KEY>"`
- `X-Resy-Auth-Token: <RESY_AUTH_TOKEN>` (JWT, ~45-day lifetime per community sources; rotated by hand)
- `X-Resy-Universal-Auth: <same token>`
- `User-Agent`: a browser UA string (in `app/resy/auth.py`). **Without it Resy returns 500**, not 403.
- Not needed: `Origin`, `Referer`, and all cookies (`token_v2`, `production_refresh_token`).
