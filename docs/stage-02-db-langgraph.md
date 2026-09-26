# Stage 2 — Database + LangGraph minimum

**Goal:** Supabase Postgres connected with migrations, and a one-node LangGraph agent (LLM only, no tools) whose conversation state persists in Postgres via the checkpointer.

**Suggested branch (you create it):** `stage-02-db-langgraph`
**Depends on:** Stage 1

## Steps

### 1. Supabase project (user does this; Claude documents it in README)

1. Create a project at supabase.com in a US East region. Save the database password.
2. Click **Connect** → copy the **Session pooler** string (host `…pooler.supabase.com`, port 5432). Don't use the direct connection: it's IPv6-only on the free tier and Render is IPv4.
3. Set `DATABASE_URL=postgresql+psycopg://…` locally, on Render, and as a GitHub secret. URL-encode special characters in the password.

### 2. Database layer (`app/db/`)

- Dependencies: `sqlalchemy[asyncio]`, `psycopg[binary,pool]`, `alembic`.
- Async engine: `pool_size=5`, `max_overflow=5`, `pool_pre_ping=True`. Session dependency for FastAPI.
- Alembic initialized in `backend/alembic/`, `env.py` reads `DATABASE_URL` from settings.
- First migration:
  - `conversations`: `id` (UUID PK), `session_id` (text, indexed), `title` (text, nullable), `message_count` (int, default 0), `created_at`, `updated_at`, `last_message_at`.
  - `daily_spend`: `day` (date PK, UTC date), `spend_usd` (numeric(12,6)), `llm_calls` (int), `updated_at`.

### 3. LangGraph minimum (`app/agent/`)

- Dependencies: `langgraph`, `langchain-openai`, `langgraph-checkpoint-postgres`.
- `state.py`: `AgentState` TypedDict with `messages: Annotated[list[AnyMessage], add_messages]`.
- `prompts.py`: `build_system_prompt(now: datetime, tz: str, location_available: bool, city: str | None) -> str`, including:
  - current date and time in the user's timezone (`tz` comes from the chat request from stage 3 on; until then use `APP_TIMEZONE`);
  - whether the user's location is available and the city name once known (never coordinates);
  - a 14-day calendar table (date + weekday) starting today;
  - date rules: a date without a year ("28th September") means its next upcoming occurrence; weekday names ("Friday") mean the next such day, per the calendar; if the date is today and the requested time has already passed, ask; never book in the past;
  - role: Resy reservation assistant for restaurants near the user;
  - rules: be concise; ask for missing party size or date before searching; never claim a reservation is made unless a tool confirms it; never book without the confirmation step (defense in depth; the real gate is in code).
- `nodes.py`: `call_model` node using `ChatOpenAI(model=settings.OPENAI_MODEL, streaming=True, stream_usage=True, timeout=30, max_retries=2)`; prepends the system prompt at call time (don't store it in state, so it always has today's date).
- `graph.py`: `StateGraph(AgentState)`, `START → call_model → END`, compiled with the async Postgres checkpointer. `thread_id` = conversation ID.
- Checkpointer: follow the current `langgraph-checkpoint-postgres` docs for connection settings (autocommit and dict rows when passing your own connection/pool). Call its idempotent `setup()` in the FastAPI lifespan.

### 4. Local CLI for verification

`scripts/chat_cli.py`: takes a conversation ID, runs turns against the graph. Used to prove persistence across process restarts. Not deployed.

### 5. CI and deploy updates

- `ci.yml` backend job: add a `postgres:16` service container and set `DATABASE_URL` for integration tests; run `alembic upgrade head` before tests.
- `deploy.yml` `migrate` job: `uv run alembic upgrade head` with the `DATABASE_URL` secret, before `deploy-backend`. GitHub runners are IPv4, so the session pooler string works.

## Tests

- `test_prompts.py`: freeze time at 2026-09-26 (Saturday); the calendar lists 14 days starting 2026-09-26 with correct weekdays, and the next Friday is 2026-10-02. The prompt contains the date rules text. A second case: at 2026-09-27T05:00Z, `America/Los_Angeles` shows Saturday Sep 26 while `America/New_York` shows Sunday Sep 27. (Whether the model actually follows them is checked by evals in stage 11.)
- `test_graph.py`: graph with a LangChain fake chat model and an in-memory checkpointer returns a reply and keeps multi-turn history per `thread_id`, isolated between threads.
- `test_db.py` (`@pytest.mark.integration`): migrations apply; insert/read a conversation; checkpointer `setup()` runs twice without error.

## Exit criteria

- [x] Migrations apply to Supabase through the deploy workflow.
- [x] `scripts/chat_cli.py`: a second turn references the first turn's content, and still does after restarting the process (checkpointer works).
- [x] System prompt calendar test passes.
- [x] CI runs integration tests against the Postgres service container.

## Out of scope

HTTP chat endpoint, Langfuse, guards, UI, tools.

## Notes

- Windows dev machines need `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` before any psycopg async connection (added in `app/__init__.py`) — psycopg's async mode can't run on Windows' default ProactorEventLoop.
- `alembic/env.py` reads `DATABASE_URL` from `settings` directly rather than via `config.set_main_option`/ConfigParser: a percent-encoded password containing a literal `%` trips ConfigParser's interpolation syntax.
- Checkpointer uses `AsyncPostgresSaver.from_conn_string` (single connection, opened/closed per CLI run) rather than a connection pool — sufficient since there's no HTTP endpoint yet. Revisit pooling when stage 3 wires the checkpointer into the FastAPI lifespan.
