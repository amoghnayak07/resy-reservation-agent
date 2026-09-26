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
| 1   | Skeleton + CI/CD + first deploy         | [stage-01-skeleton-ci.md](stage-01-skeleton-ci.md)                 | [ ]    |
| 2   | Database + LangGraph minimum            | [stage-02-db-langgraph.md](stage-02-db-langgraph.md)               | [ ]    |
| 3   | Langfuse + streaming chat API           | [stage-03-langfuse-chat-api.md](stage-03-langfuse-chat-api.md)     | [ ]    |
| 4   | Guards + chat UI                        | [stage-04-guards-chat-ui.md](stage-04-guards-chat-ui.md)           | [ ]    |
| 5   | Resy client + pydantic models           | [stage-05-resy-client-models.md](stage-05-resy-client-models.md)   | [ ]    |
| 6   | Search tool + graph tool loop + tracing | [stage-06-search-tool.md](stage-06-search-tool.md)                 | [ ]    |
| 7   | Venue details + venue calendar tools    | [stage-07-venue-details.md](stage-07-venue-details.md)             | [ ]    |
| 8   | Analytics dashboard                     | [stage-08-analytics-dashboard.md](stage-08-analytics-dashboard.md) | [ ]    |
| 9   | Prepare booking tool                    | [stage-09-prepare-booking.md](stage-09-prepare-booking.md)         | [ ]    |
| 10  | Book + confirmation gate                | [stage-10-book-confirm.md](stage-10-book-confirm.md)               | [ ]    |
| 11  | Tie together: evals, proof, README      | [stage-11-tie-together.md](stage-11-tie-together.md)               | [ ]    |

**Current stage:** 1

## Architecture overview

```
Browser (React + MUI on Vercel)
  │  fetch + X-Session-Id header (sessionStorage guest ID)
  │  chat body: message, timezone, user_location (only after "Use my location")
  │  SSE-formatted stream for chat
  ▼
FastAPI on Render (single instance, free tier)
  ├─ guards: rate limits (in-memory), spend cap (Postgres), input limits
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
- Location scope: searches only within a radius of the user's device location (required; no geocoding, no city picker yet). Neighborhoods matched by name against Resy's `neighborhood` field (stage 6).
- Conversation routing (what to ask, what to skip): prompt rules defined in stage 6, step 7.

## Decide later

These have no stage file yet. If one is approved, add a stage file before stage 11 and renumber.

| Tool                 | What it does                                                                  | Reference                                                       |
| -------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `list_reservations`  | Shows the account's upcoming bookings (read-only)                             | resy-mcp `resy_list_reservations`; capture endpoint in DevTools |
| `cancel_reservation` | Cancels a booking by reservation token; must reuse the book confirmation gate | resy-mcp `resy_cancel`; capture endpoint in DevTools            |

## V2 backlog (README "what's next")

- **First after the initial build:** city picker fallback when location is denied or unavailable (ideally populated from Resy's city list captured on resy.com).
- Booking in other cities (travel planning): geocoding for named places and per-venue timezones.
- Per-user Resy account linking instead of one demo account.
- Synthetic canary + alerting (cut for budget).
- Authentication and durable per-user history.
- Reservations that require payment, a card on file, or a cancellation fee.
- Automated eval suite in CI.

## Resy endpoint reference

Source of truth is DevTools captures. "Community" means documented by open-source projects but **not yet verified** by a capture in this repo; verify before implementing. Record verified details in the stage file Notes and update this table.

| Purpose              | Method + path                | Status                  | Known details                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------- | ---------------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Venue search         | `POST /3/venuesearch/search` | **Verified 2026-09-26** | Body: `geo {latitude, longitude}`, `query`, `per_page`, `slot_filter {day, party_size}`, `types`, `include_tock_inventory`, `highlight`. Response: `meta` (page, total, total_pages), `search.hits[]` (venue objects: `id.resy`, `name`, `neighborhood`, `cuisine[]`, `price_range_id`, `rating`, `url_slug`, `_geoloc`, `max_party_size`, `availability`, `is_tock_inventory`, `feature_recaptcha`, …). **Fuzzy:** "amori" returned "Mori" first. Text queries rank by name relevance, not `geo` ("indian" → 559 hits, top hit in Brooklyn); structured cuisine filter unknown (capture pending, stage 6 probe #11). `reopen.date` can be in the past on open venues. Payload variants to be probed in stage 6.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Slots for venue/date | `POST /4/find`               | **Verified 2026-09-26** | JSON body: `day`, `lat`, `long` (0/0 accepted), `party_size`, `venue_id`. Response: `query`, `results.venues[]` with `slots[]` (each: `date {start, end}` as venue-local `"YYYY-MM-DD HH:MM:SS"`, `template.id`, `size {min, max}`, `payment {is_paid, is_add_on_required, cancellation_fee, deposit_fee, …}`, `shift {id, service.type.id, day}`, `is_global_dining_access`, `exclusive {is_eligible}`, `config {id, token, type, is_visible, custom_config_name}` where `token` is the `rgs://…` string used as `config_id` in `/3/details` and `type` is the seating type (e.g. "Dining Room"). Different `config.id`s can share the same time, type, and token (different tables), so dedupe by token. `is_visible: false` appears on bookable slots; don't filter on it. plus `availability`, `status`, `quantity`, `score`, `market.date {on, off}` (look like release/close epoch times; unused)), `templates` keyed by template ID (`is_paid`, `payment_structure`, …), and `venue` (`location.time_zone` e.g. `EST5EDT`, `location.neighborhood` (can be just the city, e.g. "New York"), `location.geo {lat, lon}`, `type` (single cuisine string), `rating` (number) + `total_ratings`, `currency`, `allow_bypass_payment_method`, `feature_recaptcha`, `waitlist`). A day can have 150+ slots. `travel_time.distance` is ~5.39 km for two different venues with `lat/long = 0`, so it's meaningless here; don't use it. |
| Booking details      | `POST /3/details`            | **Verified 2026-09-26** | Body: `commit` (0 = preview, 1 = issues book token), `config_id` (`rgs://resy/<venue_id>/<template_id>/<service_type>/<date>/<date>/<HH:MM:SS>/<party>/<seating>`), `day`, `party_size`. Response: `cancellation` (`fee`, `refund.date_cut_off`, `credit`, `display.policy[]`), `change.date_cut_off`, `config`, `locale` (`currency`, `time_zone`), `payment` (`config.type` e.g. `"free"`, `amounts.reservation_charge`, fees), `user.payment_methods`, `venue` (address, contact, config). `commit: 1` returns **all of the above plus** `book_token {value, date_expires}` (short-lived), so one call is enough. `payment.amounts` includes `total`, `tax`, `service_fee`, `resy_fee`, `surcharge`, `reservation_charge`. `cancellation.display.policy[]` is readable policy text. `venue.content[]` includes a `why_we_like_it` description. Whether `commit: 1` holds the table is unknown. Content-Type: `application/json`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Book                 | `POST /3/book`               | **Verified 2026-09-26** | Body: `book_token`, `source_id` (`resy.com-venue-details`), `venue_marketing_opt_in` (0). **No payment method needed for free reservations.** Response: `reservation_id`, `resy_token`, `venue_opt_in`. Content-Type: `application/json`. **Never in tests.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Venue details        | `GET /3/venue`               | Community               | —                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Venue calendar       | `GET /4/venue/calendar`      | **Verified 2026-09-26** | Params: `venue_id`, `num_seats` (not `party_size`), `start_date`, `end_date`. Response: `last_calendar_day`, `scheduled[]` with `date` and `inventory: {reservation, event, walk-in}`; observed values `"available"`, `"not available"`. Window capped at `last_calendar_day` (30 days for venue 892) regardless of `end_date`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Current user         | `GET /2/user` or `/4/user`   | Community               | Use for the minimal-header probe                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

**Auth headers (observed, minimal set to be confirmed in stage 5):**

- `Authorization: ResyAPI api_key="<RESY_API_KEY>"`
- `X-Resy-Auth-Token: <RESY_AUTH_TOKEN>` (JWT, ~45-day lifetime per community sources)
- `X-Resy-Universal-Auth: <same token>` (verify value matches)
- Cookies observed after login: `token_v2` (**differs** from the auth token), `production_refresh_token` (refresh token, ~90 days per community sources). A "Legacy Token" was also observed. Stage 5 determines which are actually required.

## References

- `jeffknaide/resy-bot` (MIT): auth pattern, pydantic models to adapt. Credit in README.
- `karthikvetrivel/resy-sniper`: `docs/API_DOCUMENTATION.md` endpoint notes.
- `Alkaar/resy-booking-bot`: query parameter examples.
- `daylamtayari/cierge` (Go): booking flow and token lifetimes.
- `chrischall/resy-mcp`: reservation list/cancel references.
