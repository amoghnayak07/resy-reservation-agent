# Resy Reservation Agent

A chat agent that turns natural-language requests ("table for 2 in the West Village Friday, 7–9pm") into real Resy availability, shows open times, and books only after the user explicitly confirms.

**Work in progress** — see [`docs/PLAN.md`](docs/PLAN.md) for build stages and status.

## Local development

Backend (from `backend/`):

```
uv sync
uv run uvicorn app.main:app --reload
```

Frontend (from `frontend/`):

```
npm ci
npm run dev
```

Copy each `.env.example` to `.env` and fill in values before running.

### Resy credentials

The backend uses one personal Resy account. `RESY_API_KEY` and `RESY_AUTH_TOKEN` come from resy.com: log in, open DevTools → Network, and copy the `api_key` from any `api.resy.com` request's `Authorization` header and the `X-Resy-Auth-Token` header value. The token is static (no automatic refresh; it lasts weeks), so when Resy calls start failing with an auth error, repeat this and update the value in `backend/.env` and on Render.

`RESY_WRITES_ENABLED` stays `false` everywhere except production; with it off, the client refuses to book.

Check the credentials locally with the read-only probe (never books): `uv run python -m scripts.resy_probe`.

## Deployment setup

### Render (backend)

1. New web service from this repo, root directory `backend`.
2. Runtime: Python 3.11 (pinned via `backend/.python-version`).
3. Build command: install `uv`, then `uv sync --frozen --no-dev`.
4. Start command: `uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
5. Set **Auto-Deploy** to off — deploys are triggered only by GitHub Actions.
6. Copy the service's deploy hook URL into the GitHub Actions secret `RENDER_DEPLOY_HOOK_URL`.
7. Set environment variables (see `backend/.env.example`; `CORS_ORIGINS` must include the production Vercel domain).

### Vercel (frontend)

1. New project, root directory `frontend`, framework preset Vite.
2. Under Git settings, disable automatic deploys from the Vercel GitHub integration — production deploys happen only via GitHub Actions.
3. Set `VITE_API_BASE_URL` to the Render backend URL.
4. Note: this repo must be under a personal GitHub account — Vercel's Hobby plan can't connect org-owned repos.

### GitHub

1. Add repository secrets: `RENDER_DEPLOY_HOOK_URL`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` (and, from stage 2 on, `DATABASE_URL`).
2. Enable branch protection on `master`: require pull requests before merging, and require the `frontend` and `backend` status checks to pass.

## What's next

See "V2 backlog" in [`docs/PLAN.md`](docs/PLAN.md) for planned follow-ups (city picker, multi-city support, account linking, etc.) and what was cut for this build.
