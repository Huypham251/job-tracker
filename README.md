# Job Application Tracker

Track job applications by hand, or connect Gmail and let a local, rule-based
classifier create and update them from recruiting emails. Anything uncertain, and
anything you've edited yourself, goes to a review queue instead of being changed
automatically.

**Live:** <https://job-tracker-1-ldy2.onrender.com>. Sign-in is limited to Google
accounts added as test users (see "Production deployment" below).

**Stack:** React + Vite + TypeScript + Tailwind (frontend) · FastAPI + SQLAlchemy +
Alembic (backend) · PostgreSQL 16 · deployed on Render + Neon, with CI and the
background sync worker on GitHub Actions.

Design docs for every phase: `docs/superpowers/specs/`. Project status and known gaps:
`CLAUDE.md`.

## Prerequisites

- PostgreSQL 16 running locally, **or** Docker + Docker Compose
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 20+
- A Google Cloud project with OAuth credentials — see "Google OAuth setup" below (placeholder credentials are enough to run tests, not to log in)

## Project layout

```
backend/             FastAPI app (app/), Alembic migrations (alembic/), tests (tests/)
frontend/            Vite + React app (src/)
docker-compose.yml   PostgreSQL for local dev
.env.example         Backend env template (copy to backend/.env)
```

Backend structure: `app/applications/` holds the feature (model, schemas,
service, router). `app/core/` is config, `app/db/` is the engine/session and
declarative base.

## Running locally

### 1. Start PostgreSQL

You need a database named `jobtracker` for development and a role `jobtracker`
(password `jobtracker`). The test suite additionally uses `jobtracker_test`,
which it creates automatically on first run if the role can create databases.

**Option A — local PostgreSQL (Homebrew):**

```bash
brew services start postgresql@16

# create the role — CREATEDB lets the test suite create jobtracker_test itself
psql postgres -c "CREATE ROLE jobtracker WITH LOGIN PASSWORD 'jobtracker' CREATEDB;"

# create the development database
createdb -O jobtracker jobtracker
```

Verify: `psql "postgresql://jobtracker:jobtracker@localhost:5432/jobtracker" -c "select 1"`

**Option B — Docker:**

```bash
docker compose up -d      # starts Postgres 16 on localhost:5432 (see docker-compose.yml)
```

This creates the `jobtracker` database and role. The test database
`jobtracker_test` is still created automatically by the test suite.

### 2. Backend (in `backend/`)

```bash
cp ../.env.example .env      # first time only; DATABASE_URL already matches the setup above
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs · Health check: http://localhost:8000/health

### 3. Frontend (in `frontend/`)

```bash
npm install
npm run dev
```

App: http://localhost:5173 — the Vite dev server proxies `/api` to the backend
on port 8000.

## Google OAuth setup (required for login)

The app ships with placeholder Google credentials that let the backend
start and the automated tests pass, but they cannot complete a real
sign-in. To actually log in through the browser:

1. Go to <https://console.cloud.google.com/> and create (or select) a project.
2. **Google Auth Platform → Branding**, then **Audience.** Choose "External",
   fill in an app name and your email as support/developer contact, save.
   While the app is in "Testing" mode (the default), add your own Google
   account under "Test users" on the Audience page — only test users can
   complete the OAuth flow before the app is published/verified. (Google
   redesigned this console around the "Google Auth Platform" product name
   in 2026 — if you land on an older "OAuth consent screen" single-page
   layout instead, the same fields exist there under different headings.)
3. **Google Auth Platform → Clients → Create Client.**
   Application type: "Web application".
4. Under "Authorized redirect URIs", add exactly:
   `http://localhost:5173/api/v1/auth/google/callback`

   The callback goes through the **frontend** origin (`FRONTEND_URL`), not the
   backend's port. The backend builds `redirect_uri` from `FRONTEND_URL`, and Vite's
   `/api` proxy forwards the request to the backend. If you run the frontend on a
   different port, use that port here and in `FRONTEND_URL`.
5. Create it. Copy the generated Client ID and Client Secret.
6. In `backend/.env`, replace the placeholder values:
   ```
   GOOGLE_CLIENT_ID=<your client id>
   GOOGLE_CLIENT_SECRET=<your client secret>
   ```
7. Generate a real `SECRET_KEY` if you haven't already:
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
8. Restart the backend. Open <http://localhost:5173>, click "Sign in with
   Google", approve the consent screen, and you should land back on the
   dashboard signed in.

## Gmail integration setup (Phase 3, optional)

Logging in with Google (above) does **not** grant Gmail access — that's a
second, explicit consent a signed-in user triggers from the dashboard's
"Connect Gmail" button. To enable it locally:

1. In the same Google Cloud project from "Google OAuth setup", enable the
   **Gmail API**: APIs & Services → Library → search "Gmail API" → Enable.
2. APIs & Services → OAuth consent screen → add scope
   `https://www.googleapis.com/auth/gmail.readonly`. This is a sensitive
   scope — while the app is in "Testing" mode, only your own account (added
   as a test user in the login setup above) can complete this consent.
3. Google Auth Platform → Clients → open your existing OAuth client → add
   `http://localhost:5173/api/v1/gmail/callback` to "Authorized redirect
   URIs" (frontend origin, same reason as above). No new client ID/secret is
   needed.
4. Generate a token-encryption key and add it to `backend/.env`:
   ```
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   ```
   GMAIL_TOKEN_ENCRYPTION_KEY=<paste the generated key>
   ```
5. Restart the backend. On the dashboard, click "Connect Gmail" under the
   Gmail panel, approve the consent screen, and "Fetch recent messages"
   should return your actual recent inbox headers (subject/from/date/
   snippet only — Job Tracker never reads or stores message bodies in this
   phase).

Full design: `docs/superpowers/specs/2026-09-11-job-tracker-phase-3-gmail-design.md`

## Classification & extraction pipeline setup (Phase 4b, optional)

Requires Gmail to already be connected (Phase 3, above). No API key, no external service,
and no cost — classification and extraction run entirely locally using a deterministic,
rule-based classifier (`backend/app/classifier/`).

1. On the dashboard, click **Sync Gmail**. This starts a background sync job — an
   initial sync on first use (paginating back up to `gmail_sync_backfill_days`, default
   180 days) and an incremental sync thereafter — that fetches messages via the Gmail
   API and classifies each one locally, either auto-creating/updating an application,
   ignoring it, or adding it to the **Needs review** queue below the Gmail panel. The
   button shows live progress while the job runs and a summary when it finishes.
   Classification and extraction themselves never leave your machine and call no
   external service.
2. **The background worker must be running** for a sync job to actually be processed —
   clicking "Sync Gmail" only enqueues the job. In a second terminal, from `backend/`:
   ```
   uv run python -m app.sync.worker
   ```
   Without it running, the job stays queued forever.
3. For anything in the review queue, **Approve** (optionally editing a field first) or
   **Reject**. Automation never touches an application you created by hand — those always
   go through this queue, regardless of how confident the extraction was.
4. `backend/evaluation/dataset.jsonl` and `backend/evaluation/run_eval.py` measure
   classification/extraction accuracy against a labeled set — run
   `uv run python -m evaluation.run_eval` from `backend/` any time; it costs nothing.
   `backend/tests/test_evaluation_accuracy.py` runs a subset of the same check
   (is_job_related, status, company, and position accuracy, each against its own minimum
   bar) automatically as part of the normal test suite; it doesn't print every number
   `run_eval.py` does, so run `run_eval.py` directly for the full picture.

Full design: `docs/superpowers/specs/2026-09-15-job-tracker-phase-4b-local-classifier-design.md`
(classification/extraction) and
`docs/superpowers/specs/2026-09-16-job-tracker-phase-5-gmail-sync-design.md` (background
sync).

## Database migrations

```bash
cd backend
uv run alembic revision --autogenerate -m "describe change"   # generate
# review the file in alembic/versions/ before applying
uv run alembic upgrade head                                    # apply
```

## Production deployment

Everything runs on free tiers ($0/month):

```
Browser -> Render Static Site (frontend)  --rewrite /api/* -->  Render Web Service (FastAPI)
                                                                     |
GitHub Actions: ci.yml (PR gate)                                     v
                sync-worker.yml (scheduled) -- python -m app.sync.drain --> Neon Postgres
                                                                         -> Gmail API
```

| Piece | Where | Notes |
|---|---|---|
| Frontend | Render Static Site, <https://job-tracker-1-ldy2.onrender.com> | No env vars. All API calls are relative `/api/...` paths. |
| API | Render free Web Service, <https://job-tracker-lds1.onrender.com> | Reached through the frontend's rewrite, so the browser only ever sees one origin. |
| Database | Neon Postgres (free) | Autosuspends when idle. |
| Sync worker | GitHub Actions, `.github/workflows/sync-worker.yml` | Drains queued sync jobs, then exits. It does **not** run inside the API. |
| CI | GitHub Actions, `.github/workflows/ci.yml` | `backend` and `frontend` are required checks on `main`. |

**Deploy flow:** changes land on `main`, and Render auto-deploys both services from
it. CI runs on every push and PR. Changes are currently pushed straight to `main`,
which skips the PR merge gate, so Render can deploy a commit before CI has finished.
Run the checks under "Tests" locally before pushing. Database migrations run as part of the backend's Build Command,
so a failed migration fails the deploy and the previous version keeps serving. Keep
migrations backward-compatible with the previous release (add columns, don't drop
and recreate in one step), because rolling back the code doesn't roll back the
schema.

### Setting it up from scratch

1. **Neon:** create a project. Change the connection string's scheme to
   `postgresql+psycopg://…?sslmode=require`; that's your `DATABASE_URL`.
2. **Render Web Service** (backend):
   - Root Directory: `backend`
   - Build Command: `uv sync && uv run alembic upgrade head`
   - Start Command: `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
   - Instance type: Free
   - Environment: `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
     `SECRET_KEY`, `GMAIL_TOKEN_ENCRYPTION_KEY`, `CORS_ORIGINS` and `FRONTEND_URL`
     (both set to the frontend's URL), `ENV=production`, `COOKIE_SECURE=true`,
     `RUN_WORKER_IN_PROCESS=false`. See `.env.example` for what each one is.
     Generate `SECRET_KEY` and `GMAIL_TOKEN_ENCRYPTION_KEY` fresh for production
     (commands in the OAuth/Gmail sections above). Never reuse local values.
3. **Render Static Site** (frontend):
   - Root Directory: `frontend`
   - Build Command: `npm ci && npm run build`
   - Publish Directory: `dist`
   - Redirects/Rewrites: add a **Rewrite** from `/api/*` to
     `https://<backend>.onrender.com/api/*`
4. **Google Cloud Console:** add these redirect URIs, using the **frontend** domain:
   `https://<frontend>.onrender.com/api/v1/auth/google/callback` and
   `https://<frontend>.onrender.com/api/v1/gmail/callback`. Also add the frontend
   origin under "Authorized JavaScript origins". Keep the consent screen in "Testing"
   and list every account that should be able to sign in under Test users.
5. **GitHub → Settings → Secrets and variables → Actions:** add
   `PROD_DATABASE_URL`, `PROD_GOOGLE_CLIENT_ID`, `PROD_GOOGLE_CLIENT_SECRET`,
   `PROD_SECRET_KEY` and `PROD_GMAIL_TOKEN_ENCRYPTION_KEY`, all with the **same
   values** as the Render service. The frontend URL is hard-coded in
   `sync-worker.yml`; update it there if it ever changes.
6. **Verify:**
   - `curl https://<backend>.onrender.com/health/ready` returns 200.
     (`/health/worker` returns 503 `not_running` in production. That's expected,
     because the worker runs in Actions, not in the API.)
   - Sign in, connect Gmail, click **Sync Gmail**, and confirm the next scheduled
     *Sync Worker* run in the Actions tab logs `Drained 1 job(s)`.

### Running the sync worker

A sync clicked in the UI only **queues** a job. The scheduled workflow picks it up.
It's set to every 5 minutes, but GitHub runs scheduled workflows on a best-effort
basis, and in practice this one runs about every 2–5 hours. To process a sync right
away, open **Actions → Sync Worker → Run workflow**. Only one drain runs at a time
(`concurrency`), and each run has a 120-minute limit.

### Rotating production secrets

Rotate **one secret at a time**, and verify the app after each one. Each secret
lives in two places, **Render → backend → Environment** and **GitHub → Actions
secrets** (`PROD_*`), and both must hold the same new value. Saving on Render
redeploys the service.

| Secret | Where it's issued | Effect of rotating |
|---|---|---|
| `DATABASE_URL` | Neon → Roles → reset password | The old URL stops working immediately. Update Render and GitHub right away, or the API and worker fail to connect. |
| `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` | Every existing login session is invalidated, so everyone signs in again. No data is lost. |
| `GOOGLE_CLIENT_SECRET` | Google Console → Clients → your client → add a new secret, deploy it, then disable and delete the old one | If you do it in that order, nothing breaks. Stored Gmail refresh tokens stay valid (they're tied to the client ID). |
| `GMAIL_TOKEN_ENCRYPTION_KEY` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | **Every stored Gmail connection becomes unreadable.** The dashboard still says "Connected", but syncs fail until each user clicks **Disconnect**, removes the app at <https://myaccount.google.com/permissions> (the app can't revoke the old grant itself any more), then clicks **Connect Gmail**. Applications and review history are kept. Disconnecting resets the sync watermark, so the next sync is a full 180-day one. Already-processed messages are skipped, so it's quick. |

After rotating, check `/health/ready`, sign in, and (for the database URL or Gmail
key) run the Sync Worker once from the Actions tab and confirm it succeeds.

### Free-tier limitations

- The API sleeps after 15 minutes idle. The first request after that takes about
  30–60 seconds.
- Syncs are queued, and they can wait hours for the next scheduled run (see above).
- GitHub turns off scheduled workflows in a public repo after 60 days without a
  commit. Re-enable them from the Actions tab.
- Google expires Gmail authorizations for apps in "Testing" mode after about 7 days,
  so expect to reconnect Gmail roughly weekly.
- Render's free instance has 512 MB of memory and no metrics, which is why sync
  work doesn't run inside the API.
- There's no rate limiting on any endpoint yet.

## Tests

```bash
cd backend
uv run pytest
uv run python -m evaluation.compare   # classifier metrics vs. the frozen baseline
```

CI (`.github/workflows/ci.yml`) runs both of these, plus the frontend's
`npx tsc -b`, `npx oxlint` and `npm run build`, on every PR and push to `main`.

The suite runs against a separate `jobtracker_test` database on the same
Postgres instance (created automatically on first run). Each test executes
inside a transaction that is rolled back afterward — via SQLAlchemy's
`join_transaction_mode="create_savepoint"`, so a test calling `db.commit()`
still leaves no data behind.

Auth tests mint a valid session cookie directly (via the same JWT helper
the real login flow uses) rather than driving an actual Google OAuth
round-trip — the suite never makes a network call to a real Google
endpoint.

Frontend production build:

```bash
cd frontend
npm run build
```
