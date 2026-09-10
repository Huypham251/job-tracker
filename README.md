# Job Application Tracker

Phase 1: manually create, view, edit, and delete job applications.

**Stack:** React + Vite + TypeScript + Tailwind (frontend) · FastAPI + SQLAlchemy + Alembic (backend) · PostgreSQL 16.

Full design: `docs/superpowers/specs/2026-09-09-job-tracker-phase-1-design.md`

## Prerequisites

- PostgreSQL 16 running locally, **or** Docker + Docker Compose
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 20+

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

Frontend production build:

```bash
cd frontend
npm run build
```
