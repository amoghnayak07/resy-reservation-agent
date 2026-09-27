# Stage 8 — Analytics dashboard

**Goal:** A public `/dashboard` page showing token usage, cost, and latency per LLM call, conversation, model, and tool, powered by Langfuse. Aggregates only; no message content.

**Suggested branch (you create it):** `stage-08-analytics-dashboard`
**Depends on:** Stage 6 (needs tool spans); can run after Stage 7

## Backend steps

### 1. Langfuse metrics client (`app/observability/metrics.py`)

- `httpx` client with basic auth (public key : secret key). The secret key never leaves the backend.
- **Check the current Langfuse docs** for the Metrics API version and query format (there is a daily metrics endpoint and a newer Metrics API). Use whichever currently supports grouping by day, model, session, and observation name.
- If a metric isn't available from the API (e.g., latency percentiles), fetch observations for the window and compute it server-side, with a cap on rows fetched.

### 2. Endpoints (`app/api/analytics.py`), public

- `GET /api/analytics/summary?days=7|30` → totals (cost, input/output/cached tokens, LLM calls, conversations, avg cost per conversation, avg and p95 turn latency), daily series (cost, tokens), per-model breakdown.
- `GET /api/analytics/conversations?days=7|30` → one row per conversation: short ID, started at, turns, tokens, cost, avg latency, error count. **No message text.**
- `GET /api/analytics/tools?days=7|30` → per tool: calls, error rate, p50/p95 latency.
- `days` max 30 (Hobby plan retention).

### 3. Caching and protection

- In-memory cache per query, 60s TTL.
- Rate limit per IP (e.g., 30/min).
- On Langfuse 429 or errors, serve the last cached result with `stale: true`; if nothing is cached, return a friendly error.

## Frontend steps

### 4. Dashboard page (`/dashboard`)

- Charts with `@mui/x-charts` (free community package); tables with MUI `Table` (or DataGrid community).
- Range toggle: 7 / 30 days.
- KPI cards: total cost, total tokens, conversations, avg cost per conversation, p95 latency.
- Daily cost and tokens chart; per-model table; tools table; conversations table.
- Note: "Data can lag about a minute behind live usage." Show a "stale" chip when applicable.
- Link to the dashboard from the chat header, and back.

## Tests

- Metrics mapping from mocked Langfuse responses → endpoint schemas.
- Cache hit within TTL; stale fallback on Langfuse 429.
- Conversations endpoint contains no message content fields (schema test).
- Frontend: typecheck/lint; render test for KPI cards with sample data (Vitest).

## Exit criteria

- [ ] Public `/dashboard` shows real numbers from recent usage.
- [ ] Numbers match the Langfuse UI for the same window (spot-check cost and tokens).
- [ ] No Langfuse keys or message text reach the browser.
- [ ] Repeated refreshes don't trigger Langfuse 429s (cache works).
- [ ] Tests green.

## Out of scope

Auth for the dashboard, per-user drill-down with content, alerts.

## Notes

_(Fill in: Langfuse API version used, which metrics were computed server-side.)_
