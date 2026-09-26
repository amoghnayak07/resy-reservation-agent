# Stage 10 — Book + confirmation gate

**Goal:** The agent can book a pending reservation, but only after the graph pauses, the user clicks Confirm in the UI, and the demo passcode is valid. Enforced in code, idempotent, never retried.

**Suggested branch (you create it):** `stage-10-book-confirm`
**Depends on:** Stage 9

## Steps

### 1. `book` tool with `interrupt()` (`app/agent/tools/book.py`)

- **The confirmation card is the consent step.** The model calls `book` right after a successful `prepare_booking` in the same turn (per the stage 9 rules), so a fully specified request goes from one message to the card with no "shall I book?" question. The interrupt guarantees nothing is booked until the user acts on the card.
- Args: `pending_booking_id`.
- Before interrupting: load the row, check it belongs to the current session/conversation (from run config), is `pending`, and isn't expired. Otherwise return an explanatory message.
- Call `interrupt({"pending_booking_id": ..., "summary": ...})`. The graph pauses and the checkpoint is saved.
- **Code before `interrupt()` re-runs on resume**, so it must be read-only and idempotent.
- After resume: proceed only if the resume value is `{"approved": true}` **and** the DB row was moved to `confirming` by the confirm endpoint.
- **Book token freshness:** if `book_token_expires` has passed (or is within 30s), call `/3/details` with `commit: 1` again, re-check `is_free`, and store the new token. If the slot is gone or no longer free → `failed` with a clear message ("that time is no longer available"), no booking.
- Then call `ResyClient.book(book_token, allow_write=True)` (no payment method).
- Outcomes:
  - success → `confirmed`, store `reservation_id` and `resy_token` (never returned to the LLM or frontend, never logged), return a confirmation summary;
  - Resy error before sending → `failed` with error code;
  - network error or timeout **after** sending → `unknown`; tell the user to check the Resy app; **never retry** (avoids double-booking);
  - declined → `declined`, return "Okay, not booked."

### 2. Streaming the pause

When the graph interrupts, `/api/chat` emits `confirmation_required` with `pending_booking_id` and `summary`, then `done`.

### 3. Confirm and decline endpoints (`app/api/bookings.py`)

- `POST /api/bookings/{id}/confirm` with `{passcode}`:
  1. Validate session ownership (404 if not owned).
  2. Constant-time compare `passcode` with `DEMO_BOOKING_PASSCODE` (403 `invalid_passcode`).
  3. Atomic transition `pending → confirming` (`UPDATE … WHERE status = 'pending' AND expires_at > now() RETURNING`); if no row, 409 `already_handled` or 410 `expired`.
  4. Resume the graph with `Command(resume={"approved": True})` for that conversation's thread; stream the result using the same protocol.
- `POST /api/bookings/{id}/decline`: ownership check, `pending → declined`, resume with `{"approved": False}`, stream the result.
- Rate limit confirm attempts: 5 per 15 min per session and per IP (blocks passcode guessing).
- The passcode never goes through the LLM, tool args, logs, or traces.

### 4. Production write switch

`RESY_WRITES_ENABLED=true` only in the Render production env. Local and CI stay false.

### 5. Frontend confirmation card

- On `confirmation_required`, render an MUI Card in the chat: restaurant, date/time, party size, seating, cancellation policy/fees, expiry countdown.
- Passcode field + **Confirm** and **Decline** buttons; buttons disable on click; show progress while the resumed stream runs.
- Handle 403 (wrong passcode, allow retry), 409/410 (already handled or expired, card becomes read-only with a note), and success/failure/unknown states.

### 6. Tracing

Tag traces with `booking_attempt` and outcome. Confirm no passcode, book token, or `resy_token` in traces.

### 7. Live booking (only with explicit user go-ahead in the session)

One real booking at a no-fee restaurant through the deployed app. The user then cancels it in the Resy app or website (cancel tool is deferred).

## Tests (fake model + mocked client; writes never hit Resy)

- Happy path: tool interrupts → `confirmation_required` emitted → confirm with correct passcode → resume → mocked `book` called once → `confirmed`.
- Wrong passcode → 403, no resume, `book` not called.
- Double confirm → second gets 409; `book` called once.
- Expired pending booking → 410.
- Session mismatch → 404.
- Decline → `declined`, `book` not called.
- Timeout after send → `unknown`, no retry.
- Resume value without DB `confirming` state → tool refuses to book.
- Expired book token → details re-fetched with `commit: 1`, then booked with the new token; re-fetch shows slot gone or not free → `failed`, `book` not called.

## Exit criteria

- [ ] No code path books without interrupt + valid passcode + DB transition (tests prove each).
- [ ] Confirmation card works on the deployed app, including wrong passcode and expiry.
- [ ] A fully specified request with an exact available slot reaches the confirmation card from a single user message.
- [ ] One real booking made with the user's explicit approval; confirmation captured for proof; then cancelled in Resy.
- [ ] Traces show booking outcome with no secrets.
- [ ] Tests green.

## Out of scope

Cancel/list tools (deferred), paid reservations, modifying bookings.

## Notes

_(Fill in: verified `/3/book` request/response fields, live booking date/venue, cancellation done.)_
