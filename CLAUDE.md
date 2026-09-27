# CLAUDE.md — Resy Reservation Agent

A chat agent that turns natural-language requests ("table for 2 in the West Village Friday, 7–9pm") into real Resy availability, shows open times, and books only after the user explicitly confirms.

**Before doing anything, read `docs/PLAN.md`** to find the current stage, then read that stage's file in `docs/`. Work only on the current stage. Do not start the next stage until its exit criteria are met and the user approves.

**Consistency check at the start of every stage.** Before writing any code, compare the stage file against this file and `docs/PLAN.md`. List any conflicts (e.g., a field name, endpoint, rule, or env var that differs) and stop for the user to resolve them. This file is the source of truth for cross-cutting decisions: when a decision changes, update CLAUDE.md first, then the affected stage files and PLAN.md.

---

## Stack

| Layer                | Choice                                                                                                                                                                              |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend             | React + TypeScript (strict), Vite, MUI, react-router. Deployed on **Vercel** (free).                                                                                                |
| Backend              | Python 3.11, FastAPI, uv. Deployed on **Render** free web service (single instance, sleeps after 15 idle min).                                                                      |
| Agent                | LangGraph + LangChain (`langchain-openai`; plain `langchain` is also required, by Langfuse's LangChain callback integration). Model from `OPENAI_MODEL` (default `gpt-6-sol`).      |
| Database             | **Supabase used only as plain Postgres.** SQLAlchemy 2.x (async, psycopg 3), Alembic migrations, LangGraph Postgres checkpointer. No Supabase SDK, Auth, Storage, RLS, or REST API. |
| Observability        | **Langfuse** (Cloud, Hobby plan) is the single source of truth for analytics.                                                                                                       |
| Reservation platform | Resy unofficial web API (`api.resy.com`).                                                                                                                                           |
| Package managers     | Backend: `uv`. Frontend: `npm`.                                                                                                                                                     |

## Repo layout

```
/
├─ CLAUDE.md
├─ README.md
├─ docs/                     # PLAN.md + stage-XX-*.md
├─ frontend/
│  ├─ src/
│  │  ├─ api/                # typed API client, SSE stream parser, types mirroring backend schemas
│  │  ├─ components/
│  │  ├─ pages/              # ChatPage, DashboardPage
│  │  ├─ hooks/
│  │  └─ session.ts          # guest session ID in sessionStorage
│  └─ vercel.json            # SPA catch-all rewrite to /index.html
├─ backend/
│  ├─ app/
│  │  ├─ main.py             # app factory, lifespan, CORS, routers
│  │  ├─ config.py           # pydantic-settings; all env vars defined here
│  │  ├─ api/                # routers: health, chat, conversations, analytics, bookings
│  │  ├─ schemas/            # API request/response models
│  │  ├─ agent/
│  │  │  ├─ state.py
│  │  │  ├─ prompts.py
│  │  │  ├─ nodes.py
│  │  │  ├─ graph.py
│  │  │  └─ tools/           # one file per tool
│  │  ├─ resy/               # the ONLY code that talks to api.resy.com
│  │  │  ├─ client.py
│  │  │  ├─ auth.py
│  │  │  ├─ models.py
│  │  │  └─ errors.py
│  │  ├─ db/                 # engine, session, ORM models
│  │  ├─ guards/             # rate limits, spend cap, input limits
│  │  └─ observability/      # langfuse setup, pricing, metrics client
│  ├─ alembic/
│  ├─ scripts/               # manual, local-only scripts (probes, CLI chat)
│  ├─ evals/                 # manual eval cases (not run in CI)
│  └─ tests/
│     └─ fixtures/resy/      # recorded + scrubbed Resy responses
└─ .github/workflows/
   ├─ ci.yml                 # PR checks
   └─ deploy.yml             # on merge to master: checks → migrate → deploy
```

## Commands

Backend (run from `backend/`):

```
uv sync                                      # install
uv run uvicorn app.main:app --reload         # dev server
uv run pytest                                # tests (unit; integration needs DATABASE_URL)
uv run ruff format . && uv run ruff check .  # format + lint
uv run pyright                               # typecheck
uv run alembic revision --autogenerate -m "msg"
uv run alembic upgrade head
```

Windows dev machines: add `--loop none` to the dev server command. Uvicorn on Python 3.11 builds its
event loop directly (`asyncio.ProactorEventLoop` on Windows) instead of going through the event loop
policy, so it ignores the `WindowsSelectorEventLoopPolicy` set in `app/__init__.py` and the psycopg
async checkpointer can't connect. `--loop none` makes uvicorn fall back to the current policy. Render
(Linux) is unaffected.

Frontend (run from `frontend/`):

```
npm ci
npm run dev
npm run typecheck      # tsc --noEmit
npm run lint           # eslint
npm run format:check   # prettier --check
npm run format         # prettier --write
npm test               # vitest (added in stage 4)
npm run build
```

Run all relevant checks before saying a task is done.

---

## Hard rules (never break these)

1. **Never create, modify, or cancel a real Resy reservation** from tests, CI, scripts, or during development unless the user explicitly asks for it in the current session. Resy write calls are disabled unless `RESY_WRITES_ENABLED=true` (production only). Treat `/3/details` with `commit: 1` as booking-adjacent (it issues a book token and may hold the table): call it only from `prepare_booking` and the confirm flow, never during search, tests, or probes.
2. **Tests never call `api.resy.com`.** Use recorded fixtures in `tests/fixtures/resy/` via a mocked HTTP transport.
3. **The LLM never books on its own.** Booking runs only in server code after: the graph pauses with `interrupt()`, the user clicks Confirm in the UI, and the demo passcode is valid. The approval comes from the resume value and DB state, never from LLM output or tool arguments.
4. **All Resy HTTP calls go through `backend/app/resy/`.** No other module talks to Resy directly.
5. **Never invent Resy request or response shapes.** Endpoints, params, and fields must come from DevTools captures the user provides (see the endpoint table in `docs/PLAN.md`). If a capture is missing, stop and ask.
6. **Secrets:** never commit them. `.env` is gitignored; `.env.example` has placeholders. Never log or trace tokens (including book tokens and reservation `resy_token`s, which can cancel a booking), the passcode, `Authorization` headers, emails, or payment method IDs. Scrub all of these from fixtures.
7. **Frontend gets no secrets.** Its only env var is `VITE_API_BASE_URL`. OpenAI, Langfuse, and Resy credentials stay on the backend.
8. **Never render LLM or tool output as raw HTML.** Use a Markdown renderer with raw HTML disabled.
9. **The guest session ID identifies; it never authorizes.** Every conversation/booking query filters by `session_id`. Booking always requires the passcode.
10. **Never retry a Resy write automatically.** Reads may retry on 5xx/timeouts with backoff.
11. **Dates:** all "today" logic uses the user's timezone sent with each chat request (a validated IANA name; required, and there is no server-side default timezone). The system prompt always includes today's date and a 14-day calendar in that timezone; don't rely on the LLM for weekday math.
12. **"Not released yet" ≠ "fully booked."** Dates after a venue's `last_calendar_day` haven't opened; the agent must say so.
13. **Don't add tools, dependencies outside the stack, or scope** without the user's approval. Deferred tools live in `docs/PLAN.md` → "Decide later."
14. **No git state changes.** Never run git commands that change the repository (`branch`, `checkout -b`, `add`, `commit`, `push`, `merge`, `rebase`, `reset`, `stash`, `tag`). Read-only commands (`status`, `diff`, `log`) are fine. The user creates branches, commits, pushes, and opens PRs. When work is ready, give a summary of changes and a suggested commit message.

## Agent behavior rules

The full routing table and time-window rules live in `docs/stage-06-search-tool.md` (steps 6–7). The durable principles:

- **Resy search is fuzzy.** Never treat a search hit as the user's restaurant unless the name matches (exact/strong match). Fuzzy-only results mean "did you mean…?" or "not on Resy."
- **Restaurant names and cuisines are different searches.** Names go through name matching; cuisines are filtered by each hit's `cuisine` list.
- **Show Resy's results in Resy's order.** Results are filtered to the user's area (distance from the user's location to each venue) and by the rules in stage 6, but never re-ranked.
- **Only free reservations are booked:** booking details must show `payment.config.type == "free"`, `payment.amounts.total == 0`, and no cancellation fee (and the slot itself must not be paid). Anything requiring payment or a card gets an explanation and a Resy link. Free reservations need no payment method.
- **Resolve the venue before asking questions,** then ask only for what's missing, in one message. Never re-ask for given details or assume a party size.
- **Never pick an alternative** time, venue, or seating type for the user.
- **Fully specified request + exact slot available** → `prepare_booking` then `book` in the same turn. The confirmation card is the user's consent; don't ask "shall I book?" first.
- **Never claim a reservation exists** until `book` succeeds.
- **Dates without a year** resolve to the next upcoming occurrence. If the date is today and the time has passed, ask.
- **Searches are local.** No location shared → ask the user to share it. Requests for another city, or restaurants outside the radius, get a clear "this version only books near you" answer.
- **Never pass search highlight markup** (e.g., `<b>`) or raw Resy tokens to the LLM.

---

## Architecture conventions

**Two origins.** Frontend (`*.vercel.app`) and backend (`*.onrender.com`) are different sites.

- CORS allowlist comes from `CORS_ORIGINS` (comma-separated; production Vercel domain + `http://localhost:5173`).
- The guest session ID is a UUID created with `crypto.randomUUID()`, stored in `sessionStorage`, and sent as the `X-Session-Id` header. No cookies.
- Client IP for rate limiting comes from the first entry of `X-Forwarded-For` (Render proxy).

**Location and timezone (scope: the user's own area).**

- Reservations are searched only within `SEARCH_RADIUS_KM` (default 40) of the user's device location. **Location permission is required for searches**; there's no default city and no city picker in this build (planned next). No geocoding.
- The frontend requests location only when the user taps "Use my location" (`navigator.geolocation`), keeps it in memory for the tab, and sends `user_location: {lat, lng, accuracy_m}` in the chat request body with each message.
- The chat request body also carries `timezone` (browser IANA name). Since searches are local, it's also the venues' timezone.
- The backend rounds coordinates to 3 decimals on receipt and passes location and timezone to the graph via run config. **The LLM never sees coordinates.** Coordinates are never stored in the database, logged, or sent to Langfuse; traces record only `location_used` and the city label.
- Neighborhoods are matched by name against each hit's `neighborhood` field, not by coordinates.

**Database.** Connect through Supabase's **session pooler** (IPv4, port 5432). URL-encode the password. Use a small pool (`pool_size=5`, `max_overflow=5`, `pool_pre_ping=True`). The LangGraph checkpointer uses its own `psycopg_pool.AsyncConnectionPool` (`min_size=2`, `max_size=3`, autocommit, dict rows), separate from the SQLAlchemy pool, because the checkpointer only accepts psycopg connections. Both pools open in the FastAPI lifespan; worst case is 13 connections, which fits the free-tier session pooler limit. App tables are managed by Alembic; LangGraph checkpointer tables are created by the checkpointer's own idempotent `setup()` at startup.

**State and caching.** All caches live in the FastAPI process's memory on Render (in-process TTL dicts, e.g. `cachetools.TTLCache`): Resy responses, the slot-ID map, analytics results, and rate-limit counters. Render runs a single instance, so this is acceptable; everything resets when the service restarts or sleeps (document this). Anything that must survive restarts (daily spend, pending bookings, conversations) lives in Postgres.

Cache TTLs: venue search / venue details ≈ 6h; venue calendar ≈ 5 min; slot availability (`/4/find`) ≤ 60s. **Never cache** booking details, book, or cancel.

**Tool outputs are compact.** Return only the fields the LLM needs, with caps on list sizes. Long Resy tokens (config/book tokens) stay server-side, referenced by short IDs.

**Langfuse.** `session_id` = conversation ID, `user_id` = guest session ID. Tag traces with environment. Flush on app shutdown. If Langfuse shows $0 cost for the model, add a custom model price definition in Langfuse.

**Pricing.** Model prices live in config (`backend/app/observability/pricing.py`), never inline. Used by the spend cap and the per-message usage badge. As of 2026-09-26 (verify on OpenAI's pricing page before changing):

| Model      | Input / 1M | Cached input / 1M | Output / 1M |
| ---------- | ---------- | ----------------- | ----------- |
| gpt-6-sol  | $2.00      | $0.20             | $10.00      |
| gpt-6-luna | $0.10      | $0.01             | $0.50       |

Cost = `(input − cached) × input_rate + cached × cached_rate + output × output_rate`.

**Streaming protocol.** `POST /api/chat` returns `text/event-stream` (read with `fetch` + a readable stream on the frontend, not `EventSource`). Each event has `event:` and a JSON `data:` line:

| Event                   | Data                                                                                                       |
| ----------------------- | ---------------------------------------------------------------------------------------------------------- |
| `meta`                  | `{conversation_id, trace_id}`                                                                              |
| `token`                 | `{text}`                                                                                                   |
| `tool_start`            | `{name, call_id}`                                                                                          |
| `tool_end`              | `{name, call_id, ok, duration_ms}`                                                                         |
| `location_required`     | `{}` (a search needed the user's location; frontend highlights the location chip)                          |
| `confirmation_required` | `{pending_booking_id, summary}` (stage 10)                                                                 |
| `usage`                 | `{model, input_tokens, cached_tokens, output_tokens, cost_usd, latency_ms, ttft_ms}` (aggregated per turn) |
| `error`                 | `{code, message}`                                                                                          |
| `done`                  | `{}`                                                                                                       |

Send a comment ping (`: ping`) every 15s during long tool calls. Set `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

**Errors.** API errors use `{"error": {"code": "...", "message": "..."}}`. Resy errors are typed (`ResyAuthError`, `ResyBlockedError`, `ResyRateLimitedError`, `ResyUpstreamError`, `ResySchemaError`, `ResyNotFoundError`) and tagged on Langfuse traces.

---

## Code conventions

**Python:** type hints everywhere; pyright passes; pydantic v2; async endpoints and `httpx.AsyncClient`; settings only via `app/config.py`; JSON structured logging; logs never include message content (length only).

**TypeScript:** `strict: true`, no `any`; MUI components and theme; API types in `src/api/types.ts` mirror backend schemas; all fetches go through `src/api/`.

**Tests:** each stage adds tests for what it builds. Unit tests use fake LLMs (LangChain fake chat models) and mocked transports. Integration tests needing Postgres are marked `@pytest.mark.integration` and run in CI against a Postgres service container.

---

## Environment variables

Backend (`backend/.env`, mirrored on Render):

```
APP_ENV, CORS_ORIGINS, SEARCH_RADIUS_KM=40
OPENAI_API_KEY, OPENAI_MODEL=gpt-6-sol
DATABASE_URL                      # postgresql+psycopg://…@…pooler.supabase.com:5432/postgres
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
RESY_API_KEY, RESY_AUTH_TOKEN      # personal account tokens, rotated by hand (no auto-refresh)
RESY_WRITES_ENABLED=false
DEMO_BOOKING_PASSCODE
DAILY_SPEND_CAP_USD, RATE_LIMIT_SESSION_PER_MIN, RATE_LIMIT_IP_PER_HOUR, RATE_LIMIT_IP_PER_DAY
MAX_MESSAGE_CHARS, MAX_TURNS_PER_CONVERSATION
RENDER_GIT_COMMIT                 # set automatically by Render (not in .env); /health version, defaults to "dev"
```

`.env.example` files list only the variables used by stages built so far; each stage adds its own.

Frontend (`frontend/.env`, mirrored on Vercel): `VITE_API_BASE_URL`

GitHub Actions secrets: `DATABASE_URL`, `RENDER_DEPLOY_HOOK_URL`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`.

---

## Definition of done (every stage)

- All exit criteria in the stage file are checked.
- Claude Code reports the stage ready with a change summary and suggested commit message; the user commits, opens the PR, and merges once CI is green.
- `docs/PLAN.md` status updated.
- Decisions, surprises, and verified Resy details are written in the stage file's **Notes** section.
- README updated if architecture, setup, or env vars changed.

## When to stop and ask the user

- A Resy request/response shape you haven't seen in a capture.
- Anything that could create or cancel a real reservation.
- Resy blocks requests from Render (403s, challenges).
- A new dependency, tool, or scope change.
- An exit criterion that can't be met as written.
