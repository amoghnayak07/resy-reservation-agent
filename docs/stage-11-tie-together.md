# Stage 11 — Tie together: evals, proof, README

**Goal:** Verify the whole flow, measure real cost and latency, capture booking proof, and write the README (architecture, trade-offs, cuts).

**Suggested branch (you create it):** `stage-11-tie-together`
**Depends on:** Stages 1–10

## Steps

### 1. End-to-end tests (offline)

Backend test that drives the API through a full flow with a scripted fake model and mocked Resy client: search → venue details → calendar → prepare booking → `confirmation_required` → confirm → confirmed. Runs in CI.

### 2. Manual eval set (`backend/evals/`, not in CI; costs money)

~20 cases in `cases.json`, run by `evals/run.py` against the real model with **mocked tools** (no Resy calls). Each case checks tool calls and/or reply content.

Dates:

- "Friday" / "next Friday" / "tomorrow" resolve to the right dates.
- "28th September" (no year) → next upcoming occurrence; same-day past time → asks.

Routing (one case per stage 6 routing row):

- Fully specified + exact slot available → search → `prepare_booking` → `book` in one turn, no "shall I book?"
- Fully specified, exact time taken → lists nearby times, no `prepare_booking`.
- Fully specified, two seating types at that time → asks which.
- Venue + date + party, no time → whole-day times listed.
- Venue + party, no date → calendar called, open dates offered.
- Venue only → venue resolved, then one message asking for date, party size, time.
- Missing party size → asks; never assumes 2.
- Neighborhood + date + time + party → multi-venue results, user picks.
- Neighborhood only → one combined clarifying question.
- Fuzzy-only match ("amori" → "Mori") → "did you mean…" or "not on Resy"; never proceeds with the wrong venue.
- Ambiguous name / neighborhood mismatch → asks.
- `bookable_via_agent: false` venue → explains, links to Resy, no `prepare_booking`.
- "I feel like eating Japanese today" → uses `cuisine` (not `query`), asks party size with time/area defaults in one message; after "2 people", searches tonight and lists Japanese restaurants.
- "Japanese in the West Village Friday at 8 for 4" → cuisine search near the user filtered to the West Village neighborhood, no clarifying question (location shared).
- A word that's both a name and a cuisine → exact name match preferred, otherwise cuisine search.

Location and timezone:

- Search request with no location → asks to tap "Use my location" (combined with any other missing details), no search.
- "Book something in San Francisco" from a New York location → out-of-scope explanation.
- Named restaurant found only outside the radius → out-of-area explanation.
- Unrecognized neighborhood → "did you mean…" from `neighborhoods_seen`.
- `America/Los_Angeles` timezone late on a Saturday: "tonight" and "tomorrow" resolve in that timezone.

Safety and accuracy:

- Never calls `book` without a prior `prepare_booking`; never claims a booking before confirmation.
- Dates after `last_calendar_day` described as not released, not fully booked.
- Resy errors relayed gracefully.

Output: pass/fail per case, tokens, cost. Run once on `gpt-6-sol` and once on `gpt-6-luna`; record results in README.

### 3. Measure real numbers

From Langfuse (and the dashboard), for warm runs:

- cost per search-only conversation and per booking conversation;
- time from request to shown times, and from confirm to booked (p50/p95);
- Render cold-start time (measured separately).

### 4. Booking proof

Store screenshots of real conversations and the stage 10 reservation in `docs/screenshots/` (scrub personal details); the README shows them.

### 5. README

Sections:

1. What it is + live URL, and the scope: one user (Amogh) for now, bookings gated by an unpublished demo passcode; per-user sign-in is the next version.
2. Notices at the top: first load can take ~1 minute (Render free tier); **allow location access**, since the agent only books restaurants near you.
3. Architecture diagram (from PLAN.md) and the LangGraph flow (nodes, edges, interrupt).
4. Stack and rationale (use the agreed text below).
5. Agent tools and the confirmation gate.
6. Observability and dashboard.
7. Running locally, env vars, CI/CD, platform setup.
8. Trade-offs and scope cuts (list below).
9. Screenshots of real conversations and a real reservation.
10. Why Resy.
11. Disclaimer: unofficial Resy API, used at low volume with a personal account.

### 6. Pre-launch checklist (maintainer only; not in the README)

- [x] Supabase project isn't paused (restore in dashboard if needed).
- [x] Resy auth token valid well past the review window.
- [x] `RESY_WRITES_ENABLED=true` and `DEMO_BOOKING_PASSCODE` set in production.
- [x] Spend cap and rate limits at intended values.
- [x] Open the app once to wake Render before sharing.
- [x] Sharing notes tell visitors to allow location access, and that the proof booking was made in New York.

## Agreed README content

**Guest sessions (no authentication):**

> No authentication (scope cut); each browser tab gets an anonymous guest session ID. It's stored in `sessionStorage` so it's scoped to the tab and cleared on close, which limits how long a leaked ID is useful. Neither `sessionStorage` nor `localStorage` protects against XSS; an `httpOnly` cookie would, but the frontend and backend are on different domains, and cross-site cookies are blocked by many browsers. Fixing that needs a shared domain or proxy, which is out of scope. The session ID never authorizes booking; the demo passcode does.

**Hosting:**

> Render (backend) and Vercel (frontend) were chosen for their free tiers. The trade-off is that Render's free web service sleeps after 15 minutes of inactivity and takes about a minute to wake. That's a budget constraint, not an architectural one: a paid instance removes it with no code changes. No keep-alive job was added, deliberately, as a budget scope cut.

**Database:**

> Supabase for managed Postgres on a free tier: no database server to run, a web dashboard and SQL editor for inspecting data, and an IPv4 connection pooler that works from Render. Schema migrations are handled in code with Alembic, so the app isn't tied to Supabase-specific features and could move to any Postgres host. Free projects pause after 7 days of inactivity; that's accepted as a budget constraint.

**Location scope and privacy:**

> This version books restaurants near you only: searches use your device location (you'll be asked when you tap "Use my location") within about 40 km. Coordinates are rounded to about 100 m, used only for that request, and never stored, logged, or sent to the analytics service. Other cities and a manual city picker are planned next.

**Agent and observability:**

> LangGraph for agent orchestration: explicit nodes and edges, conversation state persisted by its Postgres checkpointer, and `interrupt()` to pause before booking until the user confirms. Langfuse for tracing and analytics: per-call token usage, cost, and latency out of the box, a Metrics API for the planned in-app dashboard, and a free tier (50,000 units a month) that comfortably covers this project.

## Scope cuts to list in README

- Authentication and per-user accounts (guest session per tab; one demo Resy account).
- Durable history across tabs (sessionStorage by design).
- Keep-alive for Render and Supabase (budget).
- Synthetic canary/alerting (budget); breakage detected via typed errors in logs and Langfuse traces.
- Reservations that require payment, a card on file, or a cancellation fee (free reservations only).
- `list_reservations` and `cancel_reservation` tools (deferred; see PLAN.md).
- Hot-table sniping / Priority Notify (not planned).
- Multi-platform support (not planned; the Scale answer explains what it would take).
- Automated evals in CI (manual eval script instead).
- Rate limits and the slot-ID map are in-memory (single instance; reset on restart). Resy responses aren't cached.
- Reservations only near the user's current location (~40 km radius); location permission required; no city picker (planned first after the build) and no other-city bookings or geocoding.
- English only.

## Exit criteria

- [ ] E2E test passes in CI.
- [x] Eval results recorded for both models.
- [ ] README complete: architecture, trade-offs, cuts, what's next, screenshots.
- [x] Pre-launch checklist done.
- [ ] All PLAN.md stages checked except stage 8 (dashboard), which follows this stage.
- [ ] Deferred from stage 1: PR checks block merging on failure (verify by pushing a formatting error once).
- [x] Deferred from stage 1: merging to `master` deploys both apps automatically via CI, with auto-deploy off on both platforms.

## Notes

_(Fill in: final measured numbers and any last-minute changes.)_

- Stage 8 (dashboard) moved after this stage: the README describes observability through Langfuse and lists the dashboard as next; measured numbers come from the Langfuse UI.
- README scope (user decision): architecture, trade-offs, cuts, screenshots, and why Resy. Cost/latency numbers and the review questions stay out of the README (kept for discussion; numbers from Langfuse). Eval results are kept locally in gitignored `backend/evals/results.md`, not in the README. No third-party repo references anywhere.
- Eval cases are JSON (`cases.json`), not YAML: PyYAML is only a transitive dependency.
- Frontend CI deploy fixed: `VERCEL_ORG_ID` must be the team ID (`team_…`), matching `vercel link`'s `.vercel/project.json`; the Vercel CLI runs from the repo root because the project's Root Directory is `frontend` (running from `frontend/` resolves `frontend/frontend`). Vercel Git auto-deploy is off via `vercel.json` `git.deploymentEnabled: false`.
