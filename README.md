# Job Application Tracker

Phase 1: manually create, view, edit, and delete job applications.

**Stack:** React + Vite + TypeScript + Tailwind (frontend) · FastAPI + SQLAlchemy + Alembic (backend) · PostgreSQL 16.

Full design: `docs/superpowers/specs/2026-09-09-job-tracker-phase-1-design.md`

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
2. **APIs & Services → OAuth consent screen.** Choose "External", fill in
   an app name and your email as support/developer contact, save. While
   the app is in "Testing" mode, add your own Google account under "Test
   users" — only test users can complete the OAuth flow before the app is
   published/verified.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID.**
   Application type: "Web application".
4. Under "Authorized redirect URIs", add exactly:
   `http://localhost:8000/api/v1/auth/google/callback`
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
3. APIs & Services → Credentials → open your existing OAuth client → add
   `http://localhost:8000/api/v1/gmail/callback` to "Authorized redirect
   URIs". No new client ID/secret is needed.
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

1. On the dashboard, click **Process Inbox**. This fetches your recent Gmail messages (up
   to `PIPELINE_BATCH_LIMIT`, default 20), classifies each one locally, and either
   auto-creates/updates an application, ignores it, or adds it to the **Needs review**
   queue below the Gmail panel. Nothing about this step leaves your machine.
2. For anything in the review queue, **Approve** (optionally editing a field first) or
   **Reject**. Automation never touches an application you created by hand — those always
   go through this queue, regardless of how confident the extraction was.
3. `backend/evaluation/dataset.jsonl` and `backend/evaluation/run_eval.py` measure
   classification/extraction accuracy against a labeled set — run
   `uv run python -m evaluation.run_eval` from `backend/` any time; it costs nothing.
   `backend/tests/test_evaluation_accuracy.py` runs the same check automatically as part
   of the normal test suite.

Full design: `docs/superpowers/specs/2026-09-15-job-tracker-phase-4b-local-classifier-design.md`

## Database migrations

```bash
cd backend
uv run alembic revision --autogenerate -m "describe change"   # generate
# review the file in alembic/versions/ before applying
uv run alembic upgrade head                                    # apply
```

## Tests

```bash
cd backend
uv run pytest
```

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
