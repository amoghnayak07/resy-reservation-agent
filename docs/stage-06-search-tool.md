# Stage 6 — Search tool + graph tool loop + tracing

**Goal:** The agent finds real Resy availability near the user from a natural-language request, resolves restaurant names correctly despite fuzzy search, handles cuisine requests, follows the conversation routing rules, and every tool and Resy call is visible in Langfuse with latency.

**Suggested branch (you create it):** `stage-06-search-tool`
**Depends on:** Stages 4 and 5

**Scope:** reservations only **in the user's area**, defined by a radius around the user's device location. Location permission is **required** for searches in this version; there is no city picker (planned right after the first build).

## Verified: venue search captures (2026-09-26)

`POST https://api.resy.com/3/venuesearch/search`

```json
{
  "geo": { "latitude": 40.7149, "longitude": -73.9893 },
  "highlight": { "pre_tag": "<b>", "post_tag": "</b>" },
  "include_tock_inventory": true,
  "per_page": 5,
  "query": "amori",
  "slot_filter": { "day": "2026-10-22", "party_size": 2 },
  "types": ["venue", "cuisine"]
}
```

Response shape:

- `meta`: `page`, `per_page`, `total`, `total_pages`, `engine` (`"ELASTIC"`).
- `search.hits[]`: venue objects. Fields seen: `id.resy`, `name`, `neighborhood`, `locality`, `cuisine[]`, `price_range_id`, `currency_code`, `currency_symbol`, `rating {average, count}`, `url_slug`, `location {code, id, url_slug, name}`, `_geoloc {lat, lng}`, `max_party_size`, `availability` (null unless the payload has `availability: true`; see Capture 3), `is_tock_inventory`, `feature_recaptcha`, `gda_concierge_booking`, `is_global_dining_access`, `requires_reservation_transfers`, `reopen {date}`, `images[]`, `contact`, `_highlightResult`.
- `search.cuisines[]`, `search.nbHits`, `search.nbPages`, `suggestions[]`.

### Capture 1: restaurant name (`query: "amori"`)

The top hit was **"Mori" (Soho)**. Search is fuzzy, so the top hit is not necessarily the user's restaurant. Name matching (step 4) is mandatory.

Fixtures (`tests/fixtures/resy/`): `venue-search-amori.json` (this query) and `venue-search-amor-loco.json` (exact name "Amor Loco" → Amor Loco first, then "Ador"). Both were built from preview captures, so collapsed fields (`availability`, `content`) are omitted.

### Capture 2: cuisine as free text (`query: "japanese"`)

- Payload (resy.com typeahead): `per_page: 5`, `types: ["venue", "cuisine"]`, `highlight`, no `radius` or `availability`.
- `meta.total`: 1505 hits across 301 pages, far more than the area's Japanese restaurants, so the text match is broad.
- Top hit is "Gen Japanese Restaurant" (Crown Heights, Brooklyn) although `geo` pointed at Lower Manhattan: ranked by name relevance, so **`geo` doesn't drive ranking for text queries**.
- Each hit carries `cuisine: ["Japanese"]`, a reliable post-filter.
- `suggestions[]` returns cuisine entries (`{type: "cuisine", value: "Japanese - Peruvian"}`); `search.cuisines[]` lists facet labels.
- Amor Loco has `reopen.date: "2021-07-07"`, a **past** date, on an open venue. `reopen.date` alone doesn't mean closed.

### Capture 3: search page, geo only (`query: ""`, 2026-09-27)

- Payload (`venue-search-geo-request.json`; what `ResyClient.venue_search` sends): `availability: true`, `geo {latitude, longitude, radius}` (`radius` in meters; 16100 captured), `include_tock_inventory: true`, `order_by: "availability"`, `page: 1`, `per_page: 20`, `query: ""`, `slot_filter {day, party_size}`, `types: ["venue"]`. No `highlight`.
- Response (`venue-search-geo.json`, trimmed to 8 of 20 real hits): 2722 hits in the radius, observed nearest-first (`travel_time.distance`, in miles, 0.09–0.27).
- Each hit's `availability` has `slots[]` (same shape as `/4/find` slots but **no `payment` or `size`**) and `templates{}` (payment per template: `is_paid`, `cancellation_fee`, `deposit_fee`). Tock hits have null slot tokens.
- `_highlightResult` carries no match signal (`matchLevel: "none"` everywhere).

Consequence: free-text cuisine queries bury restaurants without the cuisine in their name and ignore location. Cuisine search needs its own mode (step 5), and the tool must filter by distance itself (step 2).

## Steps

### 1. Payload probe (extend `scripts/resy_probe.py`; read-only; do this before writing the tool)

Run each variant and record in Notes: total hits, top 5 names + neighborhoods, latency, and whether any hit has non-null `availability`.

| #   | Variant                                                                                       | Question it answers                                                                                                                             |
| --- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Search-page payload (Capture 3) with a name `query`                                           | Baseline                                                                                                                                        |
| 2   | _(dropped: the baseline has no `highlight`)_                                                  | —                                                                                                                                               |
| 3   | `include_tock_inventory: false` (and removed)                                                 | Do Tock venues disappear? Tock venues likely can't be booked through Resy's book flow.                                                          |
| 4   | _(answered: the baseline already uses `types: ["venue"]`)_                                    | —                                                                                                                                               |
| 5   | `per_page`: 10, 20, 50                                                                        | Largest accepted value; latency cost of bigger pages                                                                                            |
| 6   | Remove `geo`                                                                                  | Does name search still work? How does ranking change?                                                                                           |
| 7   | _(answered by Capture 3)_                                                                     | Area discovery works: `geo` + `radius` + `query: ""` returns in-radius venues, observed nearest-first.                                          |
| 8   | _(answered by Capture 3)_                                                                     | With `availability: true` + `slot_filter`, hits include slots and templates. Open: are slots filtered by party size (they have no `size`)?     |
| 9   | `page: 2` (the baseline sends `page: 1`)                                                      | Does pagination work?                                                                                                                           |
| 10  | Query an exact known venue name (e.g., "Mori")                                                | Does an exact name rank first?                                                                                                                  |
| 11  | _(dropped: free text + `geo.radius` already returns ~98–100% cuisine matches; see Notes)_       | —                                                                                                                                               |
| 12  | `query: "japanese"` at max `per_page`, pages 1–2                                                | What share of hits have "Japanese" in `cuisine`? Where do Japanese restaurants without "Japanese" in their name rank?                                 |
| 13  | Cuisine query with `geo` vs no `geo`                                                          | Does `geo` change text-query ranking at all?                                                                                                    |

**Final payload** (from the probe; also in the PLAN.md endpoint table): the Capture 3 search-page payload with `include_tock_inventory: false` and `geo {latitude, longitude, radius: SEARCH_RADIUS_KM × 1000}` (user's location). `availability: true` + `slot_filter {day, party_size}` only when date and party size are known (otherwise omitted: name resolution only). `per_page` 10 for name searches, 20 for area and cuisine searches (max accepted: 75).

### 2. Location scope and neighborhoods (`app/agent/tools/location.py`)

- **Reference point:** the user's device location from the run config (set by the chat API, rounded to 3 decimals). The LLM never sees coordinates; it only knows whether a location is available.
- **No location → no search.** The tool returns `{"error": "location_required"}`; the chat API emits a `location_required` event; the agent asks the user to tap "Use my location." No default city.
- **Radius:** `SEARCH_RADIUS_KM` (default 40). Compute haversine distance from the reference point to each hit's `_geoloc`; exclude hits outside the radius. **Keep Resy's order** for the remaining hits; distance is used only to filter, never to re-rank. Resy already enforces `geo.radius` (probe #15), so this filter rarely removes anything. Fetch page 2 only if fewer than 5 bookable venues remain after all filters (cap 2 pages).
- **Search `geo`:** always the user's location, with `geo.radius` = `SEARCH_RADIUS_KM` × 1000; the distance filter still runs on our side.
- **City label:** the most common `location.name` among in-radius hits (e.g., "New York"), returned as `city` so the agent can refer to it.
- **Neighborhood search text:** a named neighborhood is sent as the search text (Resy matches it against the `neighborhood` field); with a cuisine too, `per_page` is 50 and the cuisine is filtered on our side (see Notes).
- **Neighborhoods by name, not coordinates:** normalize the user's neighborhood and each hit's `neighborhood` (lowercase, strip punctuation) and keep matching hits. Venues whose `neighborhood` is just the city (e.g., "New York") can't be placed in a neighborhood; exclude them from neighborhood-filtered results and report how many were skipped. A small alias table covers common shorthand (e.g., "LES" → "Lower East Side", "UWS" → "Upper West Side", "FiDi" → "Financial District"). If nothing matches, return `neighborhoods_seen` (distinct in-radius neighborhood names, max 15) so the agent can ask "did you mean…?"
- **Out of area:** if the user asks about another city, or a named restaurant exists only outside the radius, return `out_of_area: true` with that venue's city. The radius hides far venues, so when a name search has no exact/strong match nearby, run one more search **without `geo`** (global); an exact match there → `out_of_area`. The agent explains this version only books near the user's location and that other cities are planned.

### 3. Hit normalization and filtering

Map each hit to `Venue` (see stage 5): `id.resy`, `name`, `neighborhood`, `city` (`location.name`), `cuisine`, price range from `price_range_id` using the hit's `currency_symbol`, `rating.average`/`count`, Resy URL `https://resy.com/cities/{location.url_slug}/venues/{url_slug}` (verified against `/3/venue`'s `links.web`), `_geoloc`, `max_party_size`.

Exclude or flag (confirm meanings during the probe; record in Notes):

- `is_tock_inventory: true` → exclude.
- `party_size > max_party_size` → exclude, note it in output.
- `reopen.date` **in the future** → temporarily closed until then → exclude (if the user asked for that venue by name, tell them when it reopens). A past date, like Amor Loco's `2021-07-07`, means nothing; keep the venue.
- `feature_recaptcha`, `gda_concierge_booking`, or `requires_reservation_transfers` truthy → keep but mark `bookable_via_agent: false` with a reason. The agent can show these venues but not book them.

Strip any highlight markup before building output.

### 4. Name matching (when `query` is given)

Normalize names (lowercase, strip accents and punctuation, drop a leading "the"). Classify each in-radius hit:

- **exact**: normalized names equal;
- **strong**: query is a whole-word part of the name or vice versa (token match);
- **fuzzy**: anything else (e.g., "amori" → "Mori").

Result:

- one exact/strong match → `match: "exact"`;
- several exact/strong matches → `match: "ambiguous"` with candidates (name, neighborhood, cuisine);
- only fuzzy hits → `match: "none"` with up to 3 `did_you_mean` candidates. **The agent must never treat these as the user's restaurant**; it asks;
- no exact/strong match nearby, but the follow-up search without `geo` finds an exact match → `out_of_area: true` (step 2);
- no hits → `match: "none"`.

If the user gave a neighborhood and the match is in a different one, set `neighborhood_mismatch: true` so the agent asks before continuing.

### 5. Cuisine search (when `cuisine` is given)

- **Names and cuisines are separate fields.** `ReservationQuery.query` is a restaurant name; `ReservationQuery.cuisine` is a cuisine. The model decides which: "Amori" → `query`; "Japanese", "sushi", "Italian" → `cuisine`. If a word could be either, the tool tries name matching first and falls back to cuisine mode when there's no exact/strong name match.
- **Normalize to Resy's labels.** A small synonym table maps common phrasing to cuisine labels (e.g., "sushi" → Sushi/Japanese, "tacos" → Mexican/Tacos, "pizza" → Pizza/Italian), checked against `search.cuisines[]` facet values seen in responses. Unknown phrasing passes through unchanged.
- **Request.** Send the cuisine label as free-text `query` with the final payload (`per_page` 20). With `geo.radius`, results are local and ~98–100% match the cuisine (probe #12). With a neighborhood too, the neighborhood is the search text instead (`per_page` 50) and the cuisine is filtered on our side.
- **Post-filter.** Keep only in-radius hits whose `cuisine` list contains the label (case-insensitive). Step 4 name matching is skipped in this mode, so "japanese" never ends in "no match."
- **Keep Resy's order.** After the area filter (plus the named neighborhood, if given) and the step 3 filters, take the first 10 bookable venues in Resy's order.
- **Thin results.** If fewer than ~3 venues have availability, say so and offer to widen the time window or drop the neighborhood filter.

### 6. `search_availability` tool (`app/agent/tools/search_availability.py`)

Args: `ReservationQuery` (`query`, `cuisine`, `neighborhood`, and/or `venue_id`; optional `date`, `party_size`, `time_start`, `time_end`, `requested_time`, `time_precision`). Location and timezone come from the run config, not tool args.

Modes:

- **Venue by ID** (`venue_id` given, e.g. a venue chosen earlier or from the calendar): no search. `GET /3/venue?id=` for venue info, then `/4/find` for slots (when `date` and `party_size` are present).
- **Venue resolution only** (no `date` or no `party_size`): search and match, no slot fetching. Used when the user named a restaurant but details are missing.
- **Availability** (`date` and `party_size` present): search, then slots.
  - Name search: show slots only for an `exact` match. If `ambiguous` or `none`, return candidates without slots.
  - Cuisine search: slots for the first 10 after step 5 filtering, in Resy's order.
  - Neighborhood search (no name or cuisine): slots for the neighborhood's venues in Resy's order (cap 10). A search needs a restaurant name, cuisine, neighborhood, or `venue_id` (`ReservationQuery` rejects none of them); with none, the agent asks the user what or where first.
  - **Slots come from the search results** (hits include `availability.slots` + `templates`). Search already filters slots by party size (probe #8; tokens carry the requested party size), so no `/4/find` per venue. Payment status comes from the hit's templates. `/4/find` is used only in the venue-by-ID mode (it's the endpoint resy.com calls on a venue page); it's verified with `lat=0`/`long=0`, and its `travel_time.distance` is ignored.
  - A day can have 150+ slots, so group by seating type and send only slots inside the time window (max 8 per venue).
  - Slot filters: for `/4/find` slots, drop slots whose `size {min, max}` excludes the party size. Slots with `requires_payment` (from the slot's `payment` object in `/4/find`, or its template in search results) or `bookable: false` (slot-level `is_global_dining_access`, `exclusive.is_eligible: false`, null token) can be shown but are never prepared or booked. Dedupe slots by `config_token`; seating types come from the token's last segment (several `config.id`s at one time can be the same seating type on different tables).

Short slot IDs: long config tokens stay server-side in the in-memory slot-ID map (15-min expiry) keyed by conversation. `prepare_booking` (stage 9) takes `slot_id`. An expired slot means "search again."

Compact output (JSON string):

- `mode` (`name | cuisine | area`), `city`, `match`, `candidates` / `did_you_mean`, `neighborhood_mismatch`, `neighborhoods_seen`, `out_of_area`;
- venues with `venue_id`, `name`, `neighborhood`, `cuisine`, `price_range`, `bookable_via_agent`, and up to 8 slots each (`slot_id`, local time, seating type);
- `exact_time_match`: the `slot_id`(s) at `requested_time`, if any (after deduping by token, more than one means several distinct seating types);
- `nearby_times` when the exact time isn't available;
- count of venues checked with no availability; `partial: true` if some slot lookups failed.

Caching: no Resy response caching (see CLAUDE.md); only the slot-ID map.

### 7. Time window rules

All dates and times are in the user's timezone (from the request), which is also the venue's since searches are local.

| User says                       | `time_precision` | Window                                   | Behavior                                                         |
| ------------------------------- | ---------------- | ---------------------------------------- | ---------------------------------------------------------------- |
| Exact time: "8 PM", "8:00"      | `exact`          | 7:30–8:30 PM                             | Only the 8:00 slot is a match; others are "nearby"               |
| Approximate: "around 8", "8ish" | `approximate`    | 7:00–9:00 PM                             | Closest slots first; never auto-booked                           |
| Range: "7–9pm"                  | `range`          | As given                                 | User picks; never auto-booked                                    |
| "Lunch"                         | `range`          | 11:30 AM–2:30 PM                         | User picks                                                       |
| No time, specific venue         | `any`            | Whole day                                | Show all times, grouped                                          |
| No time, area or cuisine search | `any`            | 6–10 PM, stated to user                  | User can change it                                               |
| No time, date is **today**      | `any`            | Now + 30 min until 10 PM, stated to user | If it's already late, say options are limited and offer tomorrow |

### 8. Conversation routing (prompt rules)

Principles:

- **Resolve the venue before asking questions.** Don't ask for a date at a restaurant that isn't on Resy.
- **Ask only for what's missing, all in one message.** Never re-ask for details already given.
- **Never pick an alternative** time, venue, or seating for the user.
- **The confirmation card is the only consent needed** for a fully specified request (stages 9–10).
- **Searches need the user's location.** If it isn't shared, ask for it together with any other missing details.

| User gave                                                         | Agent does                                                                                                                                                      | Stops at                     |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| No restaurant, cuisine, or neighborhood ("table for 2 tonight")   | Ask where or what they'd like (a restaurant, cuisine, or neighborhood), plus any other missing details, in one message                                           | User answers                 |
| Any search request, no location shared                            | Ask the user to tap "Use my location" (plus any other missing details, in the same message)                                                                     | User shares                  |
| Another city ("book in San Francisco")                            | Explain this version only books near the user's location; other cities are planned                                                                              | —                            |
| Venue + date + exact time + party size                            | Search → exact slot exists → `prepare_booking` → `book`                                                                                                         | Confirmation card, same turn |
| Same, exact time unavailable                                      | Search → list nearby times; if none that day, calendar                                                                                                          | User picks                   |
| Same, several seating types at that time                          | Search → ask which seating                                                                                                                                      | User picks                   |
| Venue + date + party, no time                                     | Search with whole-day window                                                                                                                                    | User picks a time            |
| Venue + date + approximate/range time + party                     | Search                                                                                                                                                          | User picks a time            |
| Venue + party, no date                                            | Resolve venue → `get_venue_calendar` → next open dates                                                                                                          | User picks a date            |
| Venue only                                                        | Resolve venue → ask for date, party size, and preferred time in one message (may mention next open dates from the calendar)                                     | User answers                 |
| Venue + date/time, no party size                                  | Resolve venue → ask party size (never assume)                                                                                                                   | User answers                 |
| Venue ambiguous / fuzzy-only / not on Resy                        | Show candidates or say it's not on Resy nearby; offer an area search                                                                                            | User clarifies               |
| Venue only found outside the radius                               | Explain it's outside the user's area (out of scope)                                                                                                             | —                            |
| Neighborhood + date + time + party                                | Area search filtered to that neighborhood                                                                                                                       | User picks venue + time      |
| Neighborhood not recognized                                       | Offer `neighborhoods_seen` as "did you mean…?"                                                                                                                  | User clarifies               |
| Neighborhood only / vague                                         | Ask for date, party size, time (and cuisine if they like) in one message                                                                                        | User answers                 |
| Cuisine + date, no party size ("I feel like eating Japanese today") | Ask party size in one message, offering defaults for the rest: "For how many? Any time or neighborhood in mind, or should I check everything near you tonight?" | User answers                 |
| Cuisine + date + party size (time/neighborhood optional)          | Cuisine search near the user with time-window defaults; state the assumptions                                                                                   | User picks venue + time      |
| Cuisine only                                                      | Ask for date and party size, offering time/neighborhood defaults, in one message                                                                                | User answers                 |
| Date after `last_calendar_day`                                    | Say reservations aren't released yet and when they currently open through                                                                                       | —                            |

Rows that use later tools apply once those stages land (calendar: stage 7; prepare/book: stages 9–10). Until then, the agent stops at the step before (e.g., lists the exact slot and says booking isn't available yet).

### 9. Graph changes

- `nodes.py`: bind tools to the model.
- `graph.py`: add a `tools` node (LangGraph `ToolNode`); conditional edge from `call_model` to `tools` or `END` based on tool calls; edge `tools → call_model`.
- Tool errors: catch `ResyError` subclasses and return a short message the model can relay ("Resy is temporarily unavailable, try again in a minute") instead of raising.
- The system prompt states whether a location is available and the city name once known (never coordinates).

### 10. Prompt updates

Add the routing table and time-window rules above. Present results grouped by restaurant with neighborhoods; mention how many venues had nothing open; never invent restaurants or availability; if results are empty, suggest widening the window or dropping the neighborhood filter.

### 11. Frontend: location chip and streaming updates

- **"Use my location" chip** next to the chat input. On click: `navigator.geolocation.getCurrentPosition` with `enableHighAccuracy: false`, `timeout: 8000`, `maximumAge: 300000`. Never request on page load.
- Keep the position in memory for the tab only (not `sessionStorage`); refresh if older than 10 minutes. When on, attach `user_location: {lat, lng, accuracy_m}` to chat requests.
- States: off, requesting, on, denied (explain how to re-enable in browser settings), unavailable/timeout (retry).
- On a `location_required` event, highlight the chip.
- Emit and render `tool_start` / `tool_end` (with `duration_ms`, `ok`): "Searching Resy…" while a tool runs.

### 12. Tracing and latency

_(Covered by the LangChain callback's LLM and tool spans; the Resy-call child spans, error-type tags, and per-turn timing metadata below were not built. See Notes.)_

- Tool calls appear as spans via the LangChain callback.
- Each Resy HTTP call is its own Langfuse span with metadata: endpoint, status, `latency_ms`, and for search: `mode`, `match`, hit counts before/after radius filtering. Never include headers, tokens, or coordinates; record `location_used: true` and the `city` label only.
- Tag traces with Resy error types when they occur.
- Record per-turn timing (time to first token, total tool time, total turn time) in trace metadata; the `usage` event keeps its CLAUDE.md fields (`latency_ms` = whole turn).

### 13. Verify from Render

Run real searches on the deployed app. **If Resy returns 403s or challenge pages from Render, stop and discuss options with the user.**

## Tests

- Payload builder produces the final payload (Tock excluded, `geo` = user location with `radius` = `SEARCH_RADIUS_KM` × 1000, `availability`/`slot_filter` only with date + party size, `per_page` 10 or 20).
- Location: missing location → `location_required`; hits outside `SEARCH_RADIUS_KM` excluded; Resy's order preserved after radius filtering; `city` label from in-radius hits; coordinates rounded and absent from tool output and traces.
- Neighborhoods: "West Village" matches hits with that `neighborhood`; alias "LES" matches "Lower East Side"; unknown neighborhood returns `neighborhoods_seen`.
- Out of area: no match nearby, follow-up search without `geo` finds an exact match → `out_of_area: true` with its city; follow-up is skipped when a nearby exact/strong match exists.
- Name matching: "amori" vs `venue-search-amori.json` → `none` with "Mori" in `did_you_mean`; "Amor Loco" vs `venue-search-amor-loco.json` → `exact`; two exact matches → `ambiguous`; match in another neighborhood → `neighborhood_mismatch`.
- Filtering (`venue-search-geo.json`): Tock hit (icca) excluded; party above `max_party_size` excluded; recaptcha/concierge hits marked `bookable_via_agent: false`; venue-level Global Dining Access (Le Gratin) stays bookable; paid slots (Artesano lunch, Holywater) never prepared; future `reopen.date` excluded; past `reopen.date` (Amor Loco in `venue-search-amor-loco.json`) kept.
- Cuisine mode: `venue-search-japanese.json` (probe #12) → only hits with "Japanese" in `cuisine` kept; no name-matching "none" result; synonym table maps "sushi" to the right label.
- Ambiguous word: exact name match wins over cuisine; no name match falls back to cuisine mode.
- Time windows: each row maps to the right window in a given timezone; "today" uses the request timezone.
- Venue-resolution mode fetches no slots.
- Search never calls `/3/details` (especially not `commit: 1`).
- `partial` flag when some slot lookups fail; slot IDs resolve from the slot-ID map; expired IDs → search again.
- `venue_id` mode calls `/3/venue?id=` + `/4/find` and never searches.
- Graph with a fake model that emits a tool call routes `call_model → tools → call_model → END`.
- A `ResyError` inside the tool becomes a friendly tool message.
- Frontend: location chip states (granted, denied, timeout) with a mocked `navigator.geolocation`; `user_location` attached only when on; tool indicator renders on `tool_start` and clears on `tool_end`.

## Exit criteria

- [x] Probe results recorded; final payload documented in Notes and PLAN.md.
- [x] Without location shared, a search request asks the user to share location; with it shared, results are within the radius.
- [x] "Table for 2 in the West Village Friday, 7–9pm" (from NYC) returns real open times in the West Village.
- [x] "Help me reserve a table for 2 at Amori in West Village on 28th September at 8 PM" either finds the exact venue and the 8:00 slot, or (if only fuzzy hits like "Mori") asks "did you mean…" / says it isn't on Resy nearby. It never proceeds with the wrong restaurant.
- [x] "I feel like eating Japanese today" asks for party size (offering time/neighborhood defaults) in one message, then returns nearby Japanese restaurants filtered by cuisine and area, in Resy's order, with tonight's times.
- [x] Asking for another city gets the out-of-scope explanation.
- [x] Venue-only and missing-party-size requests ask a single combined question.
- [x] Langfuse trace shows LLM → tool spans with latency, and no coordinates (Resy-call child spans not built; see Notes).
- [x] Resy works from Render's IPs (or the issue is raised with the user).
- [x] Tests green, no network in tests.

## Out of scope

Venue details, calendar, booking, dashboard, city picker, other cities, geocoding.

## Notes

- **Changed from stage 5:** `ResyClient.get_venue` now takes a `venue_id` (`GET /3/venue?id=`) instead of `url_slug` + `location`, and `Venue` no longer carries `url_slug` / `city_slug`. Reason: `/3/venue?id=` was verified (2026-09-27; `venue-by-id.json`, identical response to the slug lookup), so the `venue_id` mode and stage 7 can look venues up directly instead of caching slugs from earlier searches.
- **No Resy response caching** (decided at stage start): low-traffic one-week demo, and availability caches would be ≤ 60s, so hits would be rare. Only the slot-ID map (15-min expiry) is kept; CLAUDE.md updated.

- **Payload probe** (2026-09-27, local, `uv run python -m scripts.resy_probe --payloads`; probe point Lower Manhattan, day 2026-10-04, party 2; search-page payload unless noted):

  | #   | Variant                                   | Result                                                                                                     |
  | --- | ----------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
  | 1   | `query: "amori"`                          | 3 hits (Mori, Amor Loco, Amorina Cucina Rustica), all with slots, 390 ms                                   |
  | 3   | `include_tock_inventory` false / removed  | Tock gone either way (total 2724 → 2603)                                                                   |
  | 5   | `per_page` 10 / 20 / 50 / 100             | 10, 20, 50 returned; 100 → capped at **75**. ~400 ms (10–20), ~1 s (50+)                                   |
  | 6   | `geo` removed                             | Global results (Naples, Miami Beach, …): `geo` is required                                                 |
  | 8   | party 2 vs party 6                        | Totals differ (2724 vs 2486) and slot tokens carry the requested party size: **search filters by party size** |
  | 9   | `page: 2`                                 | Works; no overlap with page 1                                                                              |
  | 10  | `query: "Mori"`                           | Mori ranks first (9 hits, all within 15.8 km)                                                              |
  | 12  | `"japanese"`, `per_page: 50`, pages 1–2   | 225 total; 49/50 and 50/50 have "Japanese" in `cuisine`, only 3 in the name; all within 5.3 km             |
  | 13  | `"japanese"`, `geo` removed               | Global (Alpharetta, Toronto, …)                                                                            |
  | 14  | `"amori"`, `geo` without `radius`         | 4 hits, max 19.3 km: some default radius applies                                                           |
  | 15  | `"Mori"` (1.6 km away), `radius: 1000`    | 0 hits: **`radius` is enforced server-side**, for text queries too                                         |
  | 16  | `"Mori"`, no `availability`/`slot_filter` | Same names, no slots, 280 ms                                                                               |
  | —   | area, `radius` 1000 / 16100 / 40000       | totals 161 / 2724 / 3037; area results nearest-first                                                       |

- **Consequences:** with `geo.radius`, text queries are local (Capture 2's "geo doesn't drive ranking" was the typeahead payload without `radius`), so free-text cuisine search already returns ~98–100% cuisine matches nearby; a name outside the radius is only findable by searching without `geo`. Search slots fit the party size, so `/4/find` isn't needed for search results. `/4/find` is what resy.com calls on a venue page (bookable slots and table configs for one venue), which matches its use here: the venue-by-ID mode only.
- **Japanese fixture:** `venue-search-japanese.json` = #12 page 1 (50 real hits; slots, `content`, collections, and extra images removed; phones scrubbed).
- **Neighborhoods** (live check, 2026-09-27): area results are nearest-first, so from Lower Manhattan two pages never reach the West Village. Free-text `query: "West Village"` matches the `neighborhood` field (18/20 hits; Williamsburg 17/20, Tribeca 18/19), but `"japanese West Village"` matches either word (1/20 both). So the tool sends the neighborhood as the search text and filters cuisine itself (`per_page` 50 when both are given).
- **Tracing (step 12):** the LangChain callback's LLM and tool spans (with latency) were judged enough; Resy-call child spans, Resy error-type tags, and per-turn timing metadata were not built (user decision). Verified manually: no coordinates in traces.
- **Render (step 13):** stage 6 code deployed on Render with the `RESY_*` env vars; real searches returned results with no 403s or challenge pages.
- **Frontend tests without a DOM:** there's no React Testing Library or jsdom (not in the stack), so the chip/indicator logic lives in pure modules (`src/location.ts`, `src/hooks/streamStatus.ts`, `src/api/chatRequest.ts`) tested with Vitest; the components are thin wrappers and aren't render-tested.
- **Changed from stage 5:** `ResyClient.venue_search` now builds the final payload (`geo` optional for the global out-of-area search; `availability`/`slot_filter` only with date + party size; `page`), and `SearchResultsRaw` gained `cuisines` (facet labels, used for the ambiguous-word fallback).
