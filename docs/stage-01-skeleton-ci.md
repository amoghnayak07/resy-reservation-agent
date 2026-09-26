# Stage 1 — Skeleton + CI/CD + first deploy

**Goal:** A monorepo with hello-world frontend and backend, PR checks, and automatic production deploys on merge. By the end, the public Vercel URL shows the Render backend's health status, which proves CORS, env vars, and the pipeline all work before any features exist.

**Branch (you create it):** `stage-01`
**Depends on:** nothing

## Steps

### 1. Repo basics

- Root `.gitignore` covering Python (`.venv`, `__pycache__`, `.pytest_cache`), Node (`node_modules`, `dist`), and all `.env*` files except `.env.example`.
- `README.md` stub (title, one-line description, "work in progress").

### 2. Backend skeleton (`backend/`)

- `uv init`, Python 3.11 pinned (`.python-version`).
- Dependencies: `fastapi`, `uvicorn[standard]`, `pydantic-settings`.
- Dev dependencies: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `pyright`.
- `app/config.py`: `Settings` via pydantic-settings with `APP_ENV`, `CORS_ORIGINS` (parsed from comma-separated string), `APP_TIMEZONE`, `RENDER_GIT_COMMIT` (optional, default `"dev"`; set automatically by Render).
- `app/main.py`: `create_app()` factory, CORS middleware from settings, JSON structured logging setup, `GET /health` returning `{"status": "ok", "env": ..., "version": <git sha from RENDER_GIT_COMMIT or "dev">}`.
- Ruff config in `pyproject.toml` (line length 100; rules `E, F, I, UP, B`). Pyright config (`typeCheckingMode = "standard"`).
- `backend/.env.example` with placeholders for stage 1 variables only (`APP_ENV`, `CORS_ORIGINS`, `APP_TIMEZONE`); later stages add their own.

### 3. Frontend skeleton (`frontend/`)

- `npm create vite@latest frontend -- --template react-ts`; confirm `strict: true`.
- Install `@mui/material`, `@emotion/react`, `@emotion/styled`, `react-router-dom`.
- Dev: `prettier`, `eslint` (Vite's config plus `eslint-config-prettier`).
- `package.json` scripts: `typecheck` (`tsc --noEmit`), `lint`, `format`, `format:check`, `build`, `dev`.
- MUI `ThemeProvider` + `CssBaseline`; routes `/` (placeholder chat page) and `/dashboard` (placeholder).
- `src/api/client.ts`: reads `import.meta.env.VITE_API_BASE_URL`; home page calls `/health` and shows the result.
- `vercel.json`: catch-all rewrite to `/index.html` so refreshing `/dashboard` doesn't 404.
- `frontend/.env.example`.

### 4. CI (`.github/workflows/ci.yml`)

Trigger: `pull_request` targeting `master`. Two parallel jobs with dependency caching:

- **frontend:** `npm ci` → `typecheck` → `lint` → `format:check` → `build`.
- **backend:** install uv → `uv sync` → `ruff format --check .` → `ruff check .` → `pyright` → `pytest`.

Make the checks reusable (`workflow_call`) so `deploy.yml` can run the same jobs.

### 5. Deploy (`.github/workflows/deploy.yml`)

Trigger: `push` to `master`. Use a `concurrency` group so deploys never overlap.

1. Run the CI jobs (reuse `ci.yml`).
2. `migrate` job: placeholder that no-ops until stage 2 adds Alembic.
3. `deploy-backend` (needs checks + migrate): `curl -fsS -X POST "$RENDER_DEPLOY_HOOK_URL"`.
4. `deploy-frontend` (needs checks): `vercel pull --yes --environment=production`, `vercel build --prod`, `vercel deploy --prebuilt --prod`, using `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`.

### 6. Platform setup (user does this; Claude writes the instructions into README)

- **Render:** new web service from the repo, root directory `backend`, Python 3.11 pinned, build command installs uv and runs `uv sync --frozen --no-dev`, start command `uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port $PORT` (`--no-sync` stops `uv run` from re-syncing and installing the dev group at boot). **Auto-deploy off.** Copy the deploy hook URL into GitHub secret `RENDER_DEPLOY_HOOK_URL`. Set env vars (`CORS_ORIGINS` includes the Vercel production domain).
- **Vercel:** project with root directory `frontend`, framework Vite. Production deploys happen only from GitHub Actions (don't let Vercel's Git integration auto-deploy production). Set `VITE_API_BASE_URL` to the Render URL. Repo must be under a personal GitHub account (Hobby plan can't connect org-owned repos).
- **GitHub:** branch protection on `master`: require PRs and require the `frontend` and `backend` CI jobs to pass.

### 7. Verify the pipeline

Merge a trivial PR and confirm: checks run on the PR, deploy runs on merge, both services update, and the Vercel page shows backend health.

## Tests

- `tests/test_health.py`: `/health` returns 200 with expected keys.
- CORS test: allowed origin gets `Access-Control-Allow-Origin`; a disallowed origin does not.

## Exit criteria

- [ ] PR checks run and block merging when they fail (verify by pushing a formatting error once).
- [ ] Merging to `master` deploys both apps automatically; auto-deploy is off on both platforms.
- [ ] Public Vercel URL loads and displays the Render `/health` response (CORS works).
- [ ] Refreshing `/dashboard` on Vercel doesn't 404.
- [ ] `.env.example` files exist; no secrets in the repo.
- [ ] README has setup notes for Render, Vercel, and GitHub secrets.

## Out of scope

Database, LLM, Langfuse, any Resy code.

## Notes

_(Fill in during the build: surprises, exact platform settings used, anything that differed from this plan.)_

- 2026-09-26 consistency check decisions: integration branch is `master` (set on GitHub by the user); work branch is `stage-01`; `RENDER_GIT_COMMIT` added to `Settings` and CLAUDE.md's env list; Render start command uses `uv run --no-sync`; `.env.example` holds only variables from stages built so far.
- `create-vite` now scaffolds `oxlint` by default; swapped for real ESLint (`eslint.config.js` + `eslint-config-prettier`) to match the stack decision, and added `"strict": true` to both tsconfigs (not on by default in the current template).
- CI pins Node 22 (frontend's `engines` field requires `^20.19.0 || >=22.12.0`).
