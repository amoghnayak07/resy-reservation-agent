# Stage 9 — Prepare booking tool

**Goal:** When the user picks a time, the agent fetches Resy's booking details and creates a server-side pending booking with a clear summary (time, party, seating, cancellation policy, fees). No reservation is made in this stage.

**Suggested branch (you create it):** `stage-09-prepare-booking`
**Depends on:** Stage 6

## Steps

### 1. Migration: `pending_bookings`

Columns: `id` (UUID PK), `conversation_id`, `session_id` (indexed), `venue_id`, `venue_name`, `slot_start` (timestamptz), `party_size`, `seating_type`, `book_token` (server-side only), `book_token_expires` (timestamptz), `cancellation_policy` (text), `refund_cutoff` (timestamptz, nullable), `change_cutoff` (timestamptz, nullable), `payment_type` (text), `status` (`pending | confirming | confirmed | declined | expired | failed | unknown`), `expires_at`, `reservation_id` (nullable), `resy_token` (nullable; never logged, traced, or returned), `error_code` (nullable), `created_at`, `updated_at`.

### 2. `prepare_booking` tool (`app/agent/tools/prepare_booking.py`)

- Args: `slot_id` (from `search_availability` output).
- Resolve `slot_id` from the cache; if missing/expired → return "That time is no longer held, please search again."
- Call `ResyClient.get_details` with `commit: 1` (never cached) to get the payment/cancellation details and `book_token {value, date_expires}` in one call (verified: `commit: 1` returns both). `commit: 1` may hold the table, so call it only here and in the confirm flow.
- **Free reservations only.** Proceed only when `BookingDetails.is_free` is true: `payment.config.type == "free"`, `payment.amounts.total == 0`, and `cancellation.fee` is null. Anything else (deposit, prepayment, card required for a no-show fee) → no pending booking; explain and link to the venue on Resy. A slot already marked `requires_payment` in search is refused before calling details. The verified manual booking needed no payment method, so free reservations need no card.
- Otherwise insert a `pending_bookings` row with `book_token`, `book_token_expires` (from `date_expires`), and `expires_at` = now + 10 min (the user's decision window). The book token may expire sooner; stage 10 refreshes it at confirm time.
- Return a compact summary: `pending_booking_id`, restaurant, address, date/time, party size, seating type, cancellation policy text (e.g., "While you won't be charged if you need to cancel, we ask that you do so at least 24 hours in advance."), refund and change cut-offs in local time (e.g., "free cancellation until 12:00 PM Oct 22"), expiry. Never return the book token.

### 3. Session ownership

The tool reads the current conversation and session from the run config (not from LLM arguments) and stores them on the row. Later lookups must match both.

### 4. Prompt updates

Call `prepare_booking` in exactly two situations:

1. **Fully specified request with an exact match:** venue (exact name match), date, exact time, and party size were all given, and `search_availability` returned exactly one `exact_time_match` slot. Call `prepare_booking` in the same turn, without asking "shall I book?"
2. **The user picked a specific slot** from options the agent listed.

Never call it when:

- the time was approximate or a range (the user picks);
- the exact time is unavailable (list nearby times; never substitute one);
- several seating types exist at the requested time (ask which);
- the venue match was ambiguous, fuzzy, or outside the requested neighborhood (ask);
- `bookable_via_agent` is false (explain and link to Resy).

After `prepare_booking`: until stage 10, present the summary (including cancellation policy and fees) and say booking confirmation isn't available yet. From stage 10, call `book` immediately so the confirmation card appears. Never state or imply a reservation exists after `prepare_booking`.

### 5. Expiry

A small helper marks rows past `expires_at` as `expired` when they're read (no background job needed).

## Tests

- Details fixture (`tests/fixtures/resy/details-commit1.json`; `details-commit0.json` for the no-token case) → summary with policy/fees; book token absent from tool output.
- Expired/missing `slot_id` → "search again" message.
- Paid variants (synthetic: `payment.config.type` not `free`, nonzero `payment.amounts.total`, non-null `cancellation.fee`) → refused with explanation, no row created; `requires_payment` slot refused before details is called.
- Session/conversation stored from run config, not tool args.
- Reading an expired row marks it `expired`.

## Exit criteria

- [ ] Choosing a time on the deployed app yields an accurate summary and a `pending_bookings` row.
- [ ] A fully specified request with an exact available slot reaches `prepare_booking` in one turn; a near-miss time does not.
- [ ] No reservation appears in the Resy account.
- [ ] Reservations requiring payment are refused politely with a Resy link.
- [ ] Traced in Langfuse; book token never appears in traces.
- [ ] Tests green.

## Out of scope

Executing the booking, confirmation UI.

## Notes

_(Fill in: typical `book_token` lifetime (compare `date_expires` with the request time), whether `commit: 1` holds the table, other payment types observed.)_
