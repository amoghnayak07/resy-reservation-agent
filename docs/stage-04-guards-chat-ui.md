# Stage 4 — Guards + chat UI

**Goal:** The public app is safe to leave running (rate limits, spend cap, input limits) and has a usable MUI chat interface with streaming replies, per-message usage badges, and a wake-up banner for Render cold starts.

**Suggested branch (you create it):** `stage-04-guards-chat-ui`
**Depends on:** Stage 3

## Backend steps

### 1. Rate limits (`app/guards/rate_limit.py`)

- Small in-memory sliding-window limiter (no Redis; single Render instance; resets on restart; document this).
- Limits from env, applied to `/api/chat`:
  - per session: `RATE_LIMIT_SESSION_PER_MIN` (default 10);
  - per IP: `RATE_LIMIT_IP_PER_HOUR` (default 60) and `RATE_LIMIT_IP_PER_DAY` (default 200).
- Client IP = first entry of `X-Forwarded-For`, falling back to the socket address.
- Exceeded → 429 with `Retry-After` and `{"error": {"code": "rate_limited", "message": ...}}`.

### 2. Global daily spend cap (`app/guards/spend.py`)

- Before a turn: if today's (UTC) `daily_spend.spend_usd >= DAILY_SPEND_CAP_USD` (default 2.00), return 429 with code `daily_budget_reached` and a friendly message.
- After each LLM call: atomic upsert increment of `spend_usd` and `llm_calls` using `pricing.py`.
- This counter is only a guard; Langfuse remains the analytics source.

### 3. Input limits

- `MAX_MESSAGE_CHARS` (default 1000) → 422 `message_too_long`.
- `MAX_TURNS_PER_CONVERSATION` (default 30) → 409 `conversation_full` ("start a new conversation").
- LangGraph `recursion_limit` (e.g., 12) per turn so a tool loop can't run away.

### 4. Error handling and logging

- Map OpenAI rate-limit/timeout errors to friendly `error` events.
- Request logging middleware: request ID, hashed session ID, path, status, latency. No message content.

## Frontend steps

### 5. Session (`src/session.ts`)

`getSessionId()`: returns the UUID in `sessionStorage`, creating it with `crypto.randomUUID()` if missing. One session per tab by design.

### 6. API client (`src/api/`)

- Fetch wrapper adds `X-Session-Id`, parses `{error: {code, message}}`.
- Chat requests include `timezone: Intl.DateTimeFormat().resolvedOptions().timeZone`. (`user_location` is added by the location chip in stage 6.)
- SSE stream parser over `fetch` + `ReadableStream`: handles events split across chunks, ignores `: ping` comments, yields typed events (`types.ts` mirrors the protocol).

### 7. Chat page (MUI)

- Left drawer: session's conversations + "New chat" (collapses on mobile).
- Message list: user/assistant bubbles; assistant text rendered with `react-markdown` (no raw HTML plugin); auto-scroll.
- Input: multiline, disabled while streaming, character counter matching `MAX_MESSAGE_CHARS`, Enter to send / Shift+Enter newline.
- Streaming: append `token` events live; show a subtle "Working…" indicator (tool indicators come in stage 6).
- Usage badge under each assistant message: tokens in/out (cached), cost, latency, from the `usage` event.
- Errors: MUI Snackbar with friendly text; 429 shows the retry time; budget-reached shows "Daily demo budget reached, try again tomorrow."

### 8. Wake-up banner

On app load, call `/health` with a short timeout. Until it succeeds, show a banner: "Waking up the server (free tier), this can take about a minute." Poll every 3s for up to ~90s, then show a retry button. Disable the input until healthy.

### 9. Frontend tests

Add Vitest, add `npm test` to CI. Test the SSE parser (split chunks, multiple events per chunk, ping comments, malformed line handling).

## Backend tests

- Limiter: allows N, blocks N+1, window slides.
- Spend cap blocks when at/over cap; increment is correct under two concurrent turns.
- Message too long → 422; turn limit → 409.

## Exit criteria

- [ ] Deployed app: chat works end to end with streaming and usage badges.
- [ ] Rate limit and budget errors show friendly messages in the UI (test by lowering limits temporarily).
- [ ] Wake-up banner appears on a cold Render instance and clears when healthy.
- [ ] Opening a new tab starts a fresh session; conversations list is per tab.
- [ ] Vitest runs in CI.
- [ ] Layout usable on a phone-width screen.

## Out of scope

Resy, tools, dashboard, booking.

## Notes

_(Fill in during the build.)_
