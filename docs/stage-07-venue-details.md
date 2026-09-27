# Stage 7 — Venue details + venue calendar tools

**Goal:** Once a user settles on a restaurant, the agent can describe it and show which dates have openings, correctly distinguishing "fully booked" from "not released yet."

**Suggested branch (you create it):** `stage-07-venue-details`
**Depends on:** Stage 6

## Steps

### 1. `get_venue_details` tool (`app/agent/tools/get_venue_details.py`)

- Args: `venue_id`.
- Calls `ResyClient.get_venue(venue_id)` (`GET /3/venue?id=`) and maps with `VenueDetails.from_response` (verified fields in the PLAN.md endpoint table).
- Compact output: name, neighborhood, address, cuisine, price range, rating if present, description (`content[]` entry `why_we_like_it`) truncated to ~500 characters, need-to-know notes if present, and the Resy URL (`links.web`).

### 2. `get_venue_calendar` tool (`app/agent/tools/get_venue_calendar.py`)

- Args: `venue_id`, `party_size`, optional `start_date` (default today in the user's timezone from run config).
- Calls `GET /4/venue/calendar` with `venue_id`, `num_seats` = party size, `start_date`, and `end_date` = start + 90 days. Resy caps the response at `last_calendar_day` regardless of `end_date`.
- Compact output:
  - `reservations_open_through`: `last_calendar_day`;
  - `available_dates`: dates where `inventory.reservation` is available;
  - `sold_out_dates`: dates Resy marks `"sold-out"` (fully booked, released);
  - `unavailable_count`: other dates in the window that are not available;
  - `walk_ins`: summary of walk-in availability;
  - a note that dates after `last_calendar_day` are **not released yet**.
- Don't send all 30 raw entries to the model.

### 3. Finding a venue by name

If the user names a restaurant, the agent uses `search_availability` in venue-resolution mode (`query` only) to get its `venue_id`. No separate lookup tool. Only an `exact` match counts (see stage 6 name matching); `ambiguous` or fuzzy-only results mean asking the user first. Details and calendar tools are never called for a fuzzy candidate the user hasn't confirmed.

### 4. Prompt updates

Apply the stage 6 routing rows that use these tools:

- **Venue only** ("book me a table at X"): resolve the venue, then ask for date, party size, and preferred time in one message. May include the next few open dates from `get_venue_calendar` (needs a party size; if unknown, skip the calendar and just ask).
- **Venue + party size, no date:** resolve the venue, call `get_venue_calendar`, offer the next open dates.
- **Exact or chosen date has nothing open:** call `get_venue_calendar` and suggest the nearest open dates.

Other rules:

- When the user picks or asks about a specific restaurant, call `get_venue_details`.
- If the requested date has no availability, call `get_venue_calendar` and suggest the nearest open dates.
- Dates after `reservations_open_through` must be described as not yet released (and mention when booking opens appears to extend), never as fully booked.
- The calendar is day-level only; to get times for a chosen date, use `search_availability` with the `venue_id`.

## Tests

- Venue details (`tests/fixtures/resy/venue-by-id.json`) → compact output with truncated description, no raw tokens.
- Calendar (`venue-calendar.json`) → `available_dates` correct; Sept 27 (`"sold-out"`) in `sold_out_dates`, not described as unreleased; `last_calendar_day` surfaced; unknown inventory value → `unknown` (not counted as available).
- Output size stays under a fixed character budget.
- Fake-model graph test: model calls calendar after an empty search, then search with `venue_id`.

## Exit criteria

- [x] "Tell me about <restaurant>" returns accurate details on the deployed app.
- [x] "When's the next open night at <restaurant>?" lists open dates and states the release horizon.
- [x] Asking about a date past `last_calendar_day` yields "not released yet."
- [x] "Book me a table at <restaurant>" resolves the venue and asks for all missing details in one message.
- [x] "Table for 2 at <restaurant>" (no date) offers the next open dates.
- [x] Both tools traced in Langfuse with latency.
- [x] Tests green.

## Out of scope

Booking, dashboard.

## Notes

- Calendar `inventory.reservation` values seen: `"available"`, `"sold-out"`, `"not available"` only. Anything else → `unknown`, counted in `unavailable_count`.
- `/4/venue/calendar` with `start_date` after `last_calendar_day` returns 200; the tool short-circuits to `not_released_yet`.
- Date lists capped at the 14 earliest plus counts; a past `start_date` is clamped to today. Details/calendar tools don't need the user's location.
