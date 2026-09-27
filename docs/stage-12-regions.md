# Stage 12 — Regions: city picker + any Resy city

**Goal:** Users pick a country and city/region, like on resy.com, and the agent searches and books there, in that region's timezone. The picker replaces device location entirely.

**Suggested branch (you create it):** `stage-12-regions`
**Depends on:** Stages 6, 9, 10

## The flow

1. The user opens the app. A modal asks for a **country**, then a **city/region** (from Resy's city list). The modal can't be dismissed until both are chosen.
2. The modal closes and the region is shown **top right**. Clicking it reopens the modal to change country or region.
3. The selection is stored in `localStorage` and sent with every request that needs location or time.
4. If the user asks about another city or country, the agent tells them to change the location first, and the region selector is highlighted. It never searches outside the selected region.

## Backend steps

### 1. Resy city list (`app/resy/`)

- `ResyClient.get_location_config()`: `GET /3/location/config`, same auth headers as the other reads, no params (verified; see the PLAN.md endpoint table).
- Parse into a `City` model: `slug` (`url_slug`), `name`, `code`, `country_code`, `country_name`, `latitude`, `longitude`, `radius_miles` (`radius`), `time_zone`, `visible` (`show_on_web == 1`). Ignore `metadata` and `shape_data`.
- Fixtures:
  - commit a slimmed `tests/fixtures/resy/location-config.json` with about six real entries: New York (`EST5EDT`), Los Angeles, a city with another legacy zone name (e.g. `CST6CDT`), a non-US city (e.g. `Europe/Madrid`), a `show_on_web: 0` city, and one with `shape_data`;
  - gitignore the full capture, `tests/fixtures/resy/locations.json` (2.8 MB).

### 2. Region directory (`app/regions.py`)

- Holds the parsed city list in memory for **24h** (the one exception to "Resy responses aren't cached"; CLAUDE.md "State and caching"). It keeps only the slimmed fields.
- `get(slug) -> City | None`, `visible_countries()`.
- `radius_m(city)` returns `radius_miles × 1609.344`. The miles reading comes from New York's `radius: 10` matching resy.com's `geo.radius: 16100` m.
- **Refresh failure:** keep serving the stale list. If there's no list at all, `/api/regions` returns 503 `regions_unavailable`, and chat requests return the same error.

### 3. `GET /api/regions` (`app/api/regions.py`)

- Public. Returns only what the picker needs: `{countries: [{code, name, cities: [{slug, name}]}]}`, visible cities only, sorted by name. No coordinates.
- `Cache-Control: public, max-age=3600`. Rate limit 30/min per IP (a constant, like the confirm limit).

### 4. Requests carry `region`

- **Chat** (`ChatRequest`): drop `timezone` and `user_location`, add `region` (a slug, required).
- **Confirm/decline bodies:** `region` replaces `timezone`.
- **Validation:** an unknown slug gets 422 `unknown_region`, and the frontend reopens the modal.
- **Run config:** `region_slug`, `region_name`, `country_name`, `location` (the region's center, for the search tool), `radius_m`, and `timezone` (the region's). Tools keep reading `timezone` and `location` from config as today. `location_available` goes away.
- **Langfuse:** trace metadata records `region` (the slug) instead of `location_used`.

### 5. Tools

- **`search_availability`:**
  - geo center and radius come from the run config (the region), not `SEARCH_RADIUS_KM`;
  - the `location_required` path becomes `region_required`, which is defensive only, since every request has a region;
  - out-of-area is measured from the region's center against its radius. It returns `out_of_area: true` with the venue's city name, from the hit's `location.name`.
- **Slot times:** use the venue's city timezone. That's the hit's `location.url_slug` looked up in the directory (for the venue-by-ID path, `/4/find` `venue.location.time_zone`), falling back to the region's timezone.
- **`get_venue_calendar`, `prepare_booking` and `book`** use the region's timezone for "today", labels and the card, as they use config `timezone` today.

### 6. Streaming

- `location_required` becomes `region_change_required {city?}`. It's sent when a search result has `out_of_area: true`, and the frontend highlights the region selector.

### 7. Prompt

- The location line becomes: "The user's selected region is {region_name}, {country_name}. Searches stay within it. If they ask about another city or country, tell them to change the location with the selector at the top right first, and don't search."
- Remove the "tap Use my location" rules.
- Dates and the 14-day calendar are in the region's timezone.

### 8. Config and cleanup

- Remove `SEARCH_RADIUS_KM` from settings, `.env.example` and README.
- Update the evals runner and cases: `location`/`timezone` become a `region` with a fixed center, radius and timezone.

## Frontend steps

### 9. Region selection

- `src/region.ts` loads and saves `{slug, name, countryCode, countryName}` in `localStorage`. On load, it checks the slug against `/api/regions`, and a missing or stale slug reopens the modal.
- `RegionDialog`: an MUI `Dialog` with a country `Autocomplete`, then a city `Autocomplete` filtered to that country. The Confirm button is enabled once both are chosen. It can't be dismissed while no region is set.
- `RegionChip`, top right on all screen sizes (the header becomes visible on desktop too). It shows the city name and opens the dialog. It's highlighted after `region_change_required`.
- The chat input stays disabled until a region is selected.

### 10. Requests and cleanup

- Chat, confirm and decline send `region`.
- Remove `LocationChip`, `useLocation`, `location.ts` (and its tests) and the geolocation code.
- `streamStatus`: `locationRequired` becomes `regionChangeRequired`.

## Tests

- Parsing `location-config.json`: fields, visibility filter, radius conversion, and legacy time zones loading in `zoneinfo`.
- Directory: cache hit within 24h, refresh after expiry, stale list served when a refresh fails, and 503 when there's nothing cached.
- `/api/regions`: grouped by country, visible cities only, no coordinates in the response.
- Chat and confirm with an unknown region get 422. Run config carries the region's center, radius and timezone, and the LLM prompt has no coordinates.
- Search sends the region's center and `radius_m` in `geo`. A Los Angeles hit gets Los Angeles slot times. Out-of-area sends `region_change_required`.
- Prompt: region name and a calendar in the region's timezone (e.g. a late Saturday in `PST8PDT`).
- Frontend: region storage (save, load, stale slug), city filtering by country, and `region_change_required` in `streamStatus`.

## Exit criteria

- [x] First visit shows the region modal; chat is disabled until a country and city are chosen; the region shows top right, reopens the modal on click, and survives a reload.
- [x] A search in a non-New-York region (e.g. Los Angeles) returns local venues with local times, and a confirmation card shows local time.
- [x] Asking about another city gets "change your location first" with the selector highlighted; no search runs elsewhere.
- [x] `/api/regions` is served from the cache (at most one Resy city-list call per 24h per instance).
- [x] The browser sends no coordinates or timezone; the LLM never sees coordinates; traces record the region slug.
- [x] Full city-list capture gitignored; slimmed fixture committed.
- [x] Tests green; README updated (scope, privacy, trade-offs).

## Out of scope

Device geolocation, geocoding free-text places, several regions in one conversation (switching regions applies to the next message), per-venue timezones beyond the city-list lookup.

## Notes

- Live city list (2026-09-27): 2,122 of the entries parse (13 lack a center, radius, or timezone and are skipped); 2,014 visible cities in 29 countries. A read-only live Los Angeles search returned local venues with `-07:00` slot times.
- The radius is read as miles (New York `10` ↔ resy.com's `geo.radius: 16100` m); Los Angeles is 19 mi (30.6 km).
- Changed from stage 3: chat and confirm/decline bodies send `region` instead of `timezone` and `user_location`; Langfuse metadata records `region` instead of `location_used`.
- Changed from stage 4/6: the "Use my location" chip and geolocation code are removed; `location_required` became `region_change_required`, which is sent on `out_of_area` search results; `SEARCH_RADIUS_KM` is removed (each city's own radius is used).
- The frontend loads `/api/regions` only after the health check passes, so a sleeping Render instance doesn't fail the picker.
