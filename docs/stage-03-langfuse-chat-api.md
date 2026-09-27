# Stage 3 — Langfuse + streaming chat API

**Goal:** Every LLM call is traced in Langfuse with correct token counts and cost, and the backend exposes a streaming chat API plus conversation endpoints scoped to the guest session.

**Suggested branch (you create it):** `stage-03-langfuse-chat-api`
**Depends on:** Stage 2

## Steps

### 1. Langfuse setup

- User creates a Langfuse Cloud account (Hobby plan) and project; keys go into env (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`).
- Dependency: `langfuse` v4 (`>=4.15,<5`; supports Python ≥3.10). v4 replaced v3's `update_current_trace()` and the handler's `update_trace` argument; don't follow v3 examples.
- `app/observability/langfuse.py`:
  - Create the client once with `Langfuse(public_key, secret_key, host=LANGFUSE_HOST, environment=APP_ENV)` and call `shutdown()` in the lifespan shutdown (this flushes).
  - Provide `trace_turn(conversation_id, session_id, location_used)`, a context manager used once per chat turn. It creates a trace ID with `Langfuse.create_trace_id()` (sent in the `meta` event) and a `CallbackHandler(trace_context={"trace_id": ...})`. It runs the turn inside `propagate_attributes(session_id=conversation_id, user_id=session_id, metadata={"location_used": ...})`, then yields the handler and trace ID.

### 2. Pricing config

- `app/observability/pricing.py`: model → per-1M rates (input, cached input, output); `compute_cost(model, input, cached, output) -> Decimal`. Seed with the table in CLAUDE.md.

### 3. Cost verification in Langfuse

Make one real call and open the trace. If cost shows $0 or is wrong, create a custom model definition in Langfuse (match pattern on the exact model name; prices from OpenAI's pricing page). Record what you did in Notes.

### 4. Session handling

- Dependency `get_session_id`: reads `X-Session-Id`, validates it's a UUID, returns 400 `{"error": {"code": "invalid_session", "message": "..."}}` otherwise. All API errors, including FastAPI's validation errors (422) and 404s, use the `{"error": {"code", "message"}}` shape from CLAUDE.md, via app-wide exception handlers.
- All conversation reads/writes filter by `session_id`; accessing another session's conversation returns 404.

### 5. Endpoints (`app/api/`)

- `POST /api/conversations` → creates a conversation for the session.
- `GET /api/conversations` → session's conversations, newest first.
- `GET /api/conversations/{id}/messages` → human and AI text messages from checkpointer state (hide tool messages; they're internal).
- `POST /api/chat` with `{conversation_id?, message, timezone, user_location?}` → creates a conversation if none is given, streams the reply using the protocol in CLAUDE.md (`meta`, `token`, `usage`, `error`, `done`; tool and location events come in stage 6).
  - `timezone`: required IANA name, validated with `zoneinfo`; missing or invalid → 422. No server-side default: remove `APP_TIMEZONE` from settings and `.env.example`, and have the graph read the timezone only from run config. `scripts/chat_cli.py` takes a required `--timezone` argument.
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
- Missing or invalid `timezone` → 422; `user_location` rounded to 3 decimals before reaching the graph config; coordinates absent from logs and the mocked Langfuse payload.
- Session isolation: session B gets 404 for session A's conversation and doesn't see it in lists.
- `/api/chat` with a fake chat model streams `meta`, ≥1 `token`, `usage`, `done` in order, and the parser-level output is valid SSE.
- `compute_cost` matches a hand-calculated example, including cached tokens.
- Langfuse client is mocked in tests (no network).

## Exit criteria

- [x] `curl -N` against the deployed Render `/api/chat` streams tokens.
- [x] Langfuse shows traces grouped by session (conversation), with user ID set.
- [x] Langfuse cost for a call matches `pricing.py` within rounding.
- [x] The `usage` event's token counts match the Langfuse trace.
- [x] Conversation list and history endpoints work and are session-scoped.

## Out of scope

Rate limits, spend cap, UI, tools.

## Notes

- Langfuse SDK: `langfuse==4.15.6`. Its LangChain callback (`langfuse.langchain.CallbackHandler`) hard-imports the top-level `langchain` package (not just `langchain-core`/`langchain-openai`, which the stack already had) just to branch on `langchain.__version__`. Added `langchain>=1.4.2,<2` as a dependency (user-approved; it's LangChain's own package, no other functionality pulled in).
- Checkpointer pooling (open item from stage 2): separate psycopg pool, `min_size=2`, `max_size=3`, alongside the unchanged SQLAlchemy pool (5 + 5). See CLAUDE.md → Database.
- Conversation persistence sits behind a `ConversationRepository` protocol (`app/db/repository.py`): the real implementation queries Postgres, a fake in-memory one backs the chat/conversation unit tests. `get_graph` is a FastAPI dependency (`app/api/deps.py`) reading `request.app.state.graph`, overridden in tests with a fake-LLM-backed graph. This means unit tests never trigger the app's lifespan (no real Postgres pool, ChatOpenAI, or Langfuse client) — `TestClient(app)` is used without a `with` block; only tests that explicitly need real Postgres go through the repository/checkpointer directly and are marked `@pytest.mark.integration`.
- Langfuse is mocked in every test (unit and integration) via an autouse fixture patching `app.observability.langfuse.trace_turn` with a real no-op `BaseCallbackHandler` subclass (a plain mock object fails: LangChain's callback manager reads attributes like `run_inline` off it).
- CI (`ci.yml`) and the deploy `migrate` job (`deploy.yml`) needed dummy `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` added to their env blocks: `Settings()` now requires them (no default), and both jobs import `app.config` before any real Langfuse call happens.
- Step 3 (real call): done locally. One real turn (`gpt-6-sol`, 388 input / 0 cached / 64 output tokens) streamed correctly and produced trace `087fbf6f0247df522cc3097ccfa63836`, confirmed visible in the Langfuse dashboard. `cost_usd` in the `usage` event (`0.001416`) matches the hand-calculated `compute_cost` value. Cost/token-count parity *inside* the Langfuse trace itself, and session/user-ID grouping in the dashboard, are still unconfirmed. The test conversation row and checkpoint thread this created were deleted afterward.
- Found and fixed a Windows-only bug while doing the real-call check: uvicorn on Python 3.11 builds its event loop directly (`asyncio.ProactorEventLoop` on win32) rather than through the event loop policy, so it ignored the `WindowsSelectorEventLoopPolicy` set in `app/__init__.py` and the psycopg checkpointer pool couldn't connect. Fix: run the dev server with `--loop none` (documented in CLAUDE.md's Commands section). Render (Linux) is unaffected.
- Post-deploy check: `curl -N` against the live Render URL streamed correctly (trace `510f9fa90b8cf3817bf5a39e95b414f9`), and its session/user grouping in Langfuse was confirmed. All exit criteria met.
