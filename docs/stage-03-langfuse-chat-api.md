# Stage 3 — Langfuse + streaming chat API

**Goal:** Every LLM call is traced in Langfuse with correct token counts and cost, and the backend exposes a streaming chat API plus conversation endpoints scoped to the guest session.

**Suggested branch (you create it):** `stage-03-langfuse-chat-api`
**Depends on:** Stage 2

## Steps

### 1. Langfuse setup

- User creates a Langfuse Cloud account (Hobby plan) and project; keys go into env (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`).
- Dependency: `langfuse`. **Check the current Langfuse Python SDK docs for the LangChain callback import path and constructor**; it has changed between major versions.
- `app/observability/langfuse.py`: initialize the client; `make_callback(conversation_id, session_id)` returns a handler with `session_id=conversation_id`, `user_id=session_id`, environment tag. Flush in the FastAPI lifespan shutdown.

### 2. Pricing config

- `app/observability/pricing.py`: model → per-1M rates (input, cached input, output); `compute_cost(model, input, cached, output) -> Decimal`. Seed with the table in CLAUDE.md.

### 3. Cost verification in Langfuse

Make one real call and open the trace. If cost shows $0 or is wrong, create a custom model definition in Langfuse (match pattern on the exact model name; prices from OpenAI's pricing page). Record what you did in Notes.

### 4. Session handling

- Dependency `get_session_id`: reads `X-Session-Id`, validates it's a UUID, returns 400 `{"error": {"code": "invalid_session"}}` otherwise.
- All conversation reads/writes filter by `session_id`; accessing another session's conversation returns 404.

### 5. Endpoints (`app/api/`)

- `POST /api/conversations` → creates a conversation for the session.
- `GET /api/conversations` → session's conversations, newest first.
- `GET /api/conversations/{id}/messages` → human and AI text messages from checkpointer state (hide tool messages; they're internal).
- `POST /api/chat` with `{conversation_id?, message, timezone, user_location?}` → creates a conversation if none is given, streams the reply using the protocol in CLAUDE.md (`meta`, `token`, `usage`, `error`, `done`; tool and location events come in stage 6).
  - `timezone`: IANA name, validated with `zoneinfo`; invalid → 422.
  - `user_location`: optional `{lat, lng, accuracy_m}`; validate ranges; **round to 3 decimals immediately**.
  - Pass both to the graph via run config (`configurable`), never into messages. Never store, log, or trace coordinates; trace `location_used: true/false` only.

### 6. Streaming implementation

- Use the graph's async streaming (`astream_events` or `astream` with message streaming) and translate to protocol events.
- Usage: read `usage_metadata` from the final AI message(s) of the turn (input, output, cache-read tokens); compute cost with `pricing.py`; measure `latency_ms` and time-to-first-token `ttft_ms`. Emit one aggregated `usage` event per turn.
- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Comment ping every 15s.
- On completion, update the conversation row: `message_count`, `last_message_at`, and `title` (first user message, truncated to 60 chars) if empty.
- Errors mid-stream emit an `error` event then `done`; never leak stack traces.

## Tests

- Missing or invalid `X-Session-Id` → 400.
- Invalid `timezone` → 422; `user_location` rounded to 3 decimals before reaching the graph config; coordinates absent from logs and the mocked Langfuse payload.
- Session isolation: session B gets 404 for session A's conversation and doesn't see it in lists.
- `/api/chat` with a fake chat model streams `meta`, ≥1 `token`, `usage`, `done` in order, and the parser-level output is valid SSE.
- `compute_cost` matches a hand-calculated example, including cached tokens.
- Langfuse client is mocked in tests (no network).

## Exit criteria

- [ ] `curl -N` against the deployed Render `/api/chat` streams tokens.
- [ ] Langfuse shows traces grouped by session (conversation), with user ID set.
- [ ] Langfuse cost for a call matches `pricing.py` within rounding.
- [ ] The `usage` event's token counts match the Langfuse trace.
- [ ] Conversation list and history endpoints work and are session-scoped.

## Out of scope

Rate limits, spend cap, UI, tools.

## Notes

_(Fill in during the build, including Langfuse SDK version and price-definition steps.)_
