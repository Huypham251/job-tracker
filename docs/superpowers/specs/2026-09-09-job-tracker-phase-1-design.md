# Job Application Tracker — Phase 1 Design

**Date:** 2026-09-09
**Status:** Approved for implementation planning
**Scope:** Phase 1 only — manual CRUD for job applications across a full stack.

## 1. Goal & Non-Goals

### Goal

A production-style full-stack skeleton where a user can manually manage job
applications:

1. Add a job application.
2. View all applications.
3. Edit an application.
4. Delete an application.

Architecture chain: **React frontend → FastAPI REST API → SQLAlchemy → PostgreSQL**.

The project is built incrementally so the developer understands each layer. Phase 1
establishes the structure, patterns, and seams that later phases extend.

### Non-Goals (explicitly deferred)

Authentication / Google OAuth, Gmail integration, LLM extraction, Redis, Celery /
background jobs, rate limiting, monitoring, CI (GitHub Actions), deployment,
containerizing the app itself, pagination, optimistic UI updates, toast
notifications, frontend tests.

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Repo layout | Single monorepo (`/backend`, `/frontend`) | Learn the full stack together; one `docker-compose.yml`. |
| Local Postgres | Docker Compose | Reproducible, no local install. |
| `status` field | Fixed enum | Validation + consistent filtering; matches later auto-extraction. |
| `user_id` | Not now — add via Alembic migration in the auth phase | YAGNI. |
| Backend structure | Feature modules + thin service layer (Approach A) | Smallest structure that won't need rework for Gmail/LLM/auth phases. |
| Python tooling | `uv` (pyproject.toml + uv.lock) | Fast, single tool for venv + deps + lockfile. |
| Frontend package manager | `npm` | Zero setup, sufficient at this size. |
| Phase 1 tests | A few backend smoke tests | Establishes the pytest harness without bloating Phase 1. |
| ID type | UUID (v4) | No enumeration; easier data merges later. |
| Update verb | `PATCH` (partial) | Edit form and later Gmail updates both do partial updates. |
| DB driver | `psycopg` (v3) | Current recommended driver. |

## 3. Project Structure

```
job-tracker/
├── docker-compose.yml          # Postgres (+ Redis later)
├── .env.example                # documented env vars, committed
├── .gitignore
├── README.md                   # setup + run instructions
│
├── backend/
│   ├── pyproject.toml          # deps, managed by uv
│   ├── uv.lock
│   ├── .env                    # DATABASE_URL etc (gitignored)
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   │       └── 0001_create_applications.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI app factory, CORS, router include, exc handlers
│   │   ├── core/
│   │   │   └── config.py       # Settings (pydantic-settings), reads .env
│   │   ├── db/
│   │   │   ├── base.py         # DeclarativeBase + TimestampMixin + model imports
│   │   │   └── session.py      # engine, SessionLocal, get_db() dependency
│   │   └── applications/
│   │       ├── __init__.py
│   │       ├── models.py       # Application ORM model, ApplicationStatus enum
│   │       ├── schemas.py      # ApplicationCreate, ApplicationUpdate, ApplicationRead
│   │       ├── service.py      # CRUD logic against the DB session
│   │       ├── exceptions.py   # ApplicationNotFound
│   │       └── router.py       # APIRouter, path operations
│   └── tests/
│       ├── conftest.py         # test DB, client fixtures
│       └── test_applications.py
│
└── frontend/
    ├── package.json
    ├── vite.config.ts          # dev server + /api proxy to backend
    ├── tailwind.config.js
    ├── postcss.config.js
    ├── tsconfig.json
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── index.css           # tailwind directives
        ├── constants.ts        # STATUS_LABELS, STATUS_STYLES
        ├── api/
        │   └── applications.ts # typed fetch functions
        ├── types/
        │   └── application.ts  # TS types mirroring API schema
        ├── components/
        │   ├── ApplicationsPage.tsx
        │   ├── ApplicationList.tsx
        │   ├── ApplicationRow.tsx
        │   ├── ApplicationForm.tsx   # shared create/edit form
        │   └── ui/                   # Button, Input, etc. (only where markup repeats)
        └── hooks/
            └── useApplications.ts    # data fetching + mutation state
```

Rationale notes:

- `db/base.py` vs `db/session.py` split so Alembic can import metadata without
  pulling in the running engine config.
- Frontend keeps `api/`, `types/`, and `hooks/` separate so the network layer is
  swappable and components stay presentational.
- No state library — one `useApplications` hook with `useState`/`useEffect`.

## 4. Data Model

### Table: `applications`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | `UUID` | PK, default `uuid4` | |
| `company` | `varchar(255)` | not null | |
| `position` | `varchar(255)` | not null | |
| `status` | `enum application_status` | not null, default `applied` | native Postgres enum |
| `applied_at` | `date` | nullable | day of application; nullable because Gmail-extracted rows later may not know it |
| `created_at` | `timestamptz` | not null, server default `now()` | |
| `updated_at` | `timestamptz` | not null, server default `now()`, `onupdate=now()` | |

### Enum `application_status`

Values (stored lowercase): `applied`, `oa`, `interview`, `rejected`, `offer`, `withdrawn`.

Defined once as a Python `enum.StrEnum` in `applications/models.py` and reused by the
Pydantic schemas — single source of truth. The frontend maps values to display
labels (`oa` → "OA").

### SQLAlchemy setup

- `DeclarativeBase` subclass in `db/base.py`.
- `TimestampMixin` (`created_at`, `updated_at`) so future tables reuse it.

### Alembic

- `alembic/env.py` wired to `Base.metadata` and `settings.DATABASE_URL` — no
  hardcoded URL.
- One initial migration `0001_create_applications.py` — creates the enum type + the
  table. Written explicitly and reviewed, not blindly autogenerated.
- README documents: `uv run alembic revision --autogenerate -m "..."` → review →
  `uv run alembic upgrade head`.
- No seed data.

## 5. API Contract

Base path `/api/v1`. JSON bodies. Automatic docs at `/docs`.

| Method | Path | Body | Success | Errors |
|---|---|---|---|---|
| `GET` | `/api/v1/applications` | — | `200` `ApplicationRead[]` (created_at desc) | — |
| `POST` | `/api/v1/applications` | `ApplicationCreate` | `201` `ApplicationRead` | `422` |
| `GET` | `/api/v1/applications/{id}` | — | `200` `ApplicationRead` | `404` |
| `PATCH` | `/api/v1/applications/{id}` | `ApplicationUpdate` | `200` `ApplicationRead` | `404`, `422` |
| `DELETE` | `/api/v1/applications/{id}` | — | `204` (no body) | `404` |
| `GET` | `/health` | — | `200` `{"status":"ok"}` | — |

### Schemas (`applications/schemas.py`)

```
ApplicationCreate:
  company:    str                         # required, 1..255, trimmed
  position:   str                         # required, 1..255, trimmed
  status:     ApplicationStatus = applied  # optional, defaults
  applied_at: date | None = None

ApplicationUpdate:                         # all optional; >=1 field required (model validator, else 422)
  company:    str | None
  position:   str | None
  status:     ApplicationStatus | None
  applied_at: date | None

ApplicationRead:
  id, company, position, status, applied_at, created_at, updated_at
  model_config = ConfigDict(from_attributes=True)
```

### Design choices

- `PATCH` not `PUT`; service uses `model_dump(exclude_unset=True)` so "field absent"
  ≠ "set to null".
- No pagination in Phase 1.
- Error shape: FastAPI default — `{"detail": "..."}` (404) / `{"detail": [...]}` (422).
- CORS: `main.py` allows origins from `settings.CORS_ORIGINS` (dev: `http://localhost:5173`).

## 6. Backend Request Flow & Error Handling

### Layers (example: `PATCH /applications/{id}`)

```
router.py    get_db() dependency yields a Session; parse/validate body -> ApplicationUpdate
   |
   v
service.py   update_application(db, id, data) -> Application
   |         - fetch row by id; raise ApplicationNotFound if None
   |         - apply data.model_dump(exclude_unset=True)
   |         - db.commit(); db.refresh(obj)
   v
router.py    return ORM obj; FastAPI serializes via response_model=ApplicationRead
```

### Responsibilities

- **router** — HTTP only: status codes, path/query/body binding, `response_model`. No
  queries, no business rules.
- **service** — owns the `Session`; all queries, mutations, transaction boundaries.
  Pure Python in/out — callable from tests or a future Celery task with no HTTP.
- **models / schemas** — data shapes only, no behavior.

### Session management

- `get_db()` in `db/session.py` — generator dependency: `SessionLocal()`, `yield`,
  `close()` in `finally`. One session per request.
- Service functions call `db.commit()` explicitly; on exception the session closes
  without commit.

### Error handling

- Service raises domain exceptions from `applications/exceptions.py`:
  `ApplicationNotFound(id)`.
- One exception handler in `main.py` maps `ApplicationNotFound` → `404
  {"detail": "Application <id> not found"}`. Keeps `HTTPException` out of the service
  layer.
- Pydantic input validation → automatic `422`.
- Unhandled errors → FastAPI default `500` (no custom handler in Phase 1). DB
  connectivity failure surfaces as `500` — acceptable for Phase 1.

### Config (`core/config.py`)

- `pydantic-settings` `Settings`: `DATABASE_URL`, `CORS_ORIGINS`, `ENV`. Loaded once,
  imported where needed. No scattered `os.getenv`.

## 7. Frontend Structure & Data Flow

Stack: Vite + React 18 + TS, Tailwind. No router (single page), no state library.

### Layers

```
types/application.ts     Application, ApplicationStatus, Create/Update payloads (hand-mirrored from API)
   |
   v
api/applications.ts      listApplications(), createApplication(data),
   |                     updateApplication(id, data), deleteApplication(id)
   |                     thin fetch wrappers; base URL "/api/v1"; throw on !res.ok (message from `detail`)
   v
hooks/useApplications.ts owns: applications[], loading, error
   |                     exposes: refetch, create, update, remove
   |                     re-fetches the list after each mutation (simple, correct)
   v
components/              presentational; call hook actions, render state
```

### Component tree

```
App
 └─ ApplicationsPage        layout; holds add/edit form visibility + which row is being edited
     ├─ ApplicationForm     shared create+edit; controlled inputs:
     │                      company, position, status <select>, applied_at <input type=date>
     ├─ ApplicationList
     │   └─ ApplicationRow  one row; Edit -> opens form with values; Delete -> confirm() -> remove(id)
     └─ loading / error / empty states
```

### Data flow — add an application

1. `ApplicationForm` submit → `useApplications().create(payload)`
2. hook → `api.createApplication` → `POST /api/v1/applications`
3. success → hook calls `refetch()` → list re-renders
4. failure → hook sets `error`; form shows the message

### Dev networking

`vite.config.ts` proxies `/api` → `http://localhost:8000`. Frontend code uses
relative URLs; no CORS issue in dev. Backend CORS config is belt-and-suspenders for
other setups.

### Styling

Tailwind utility classes inline. `ui/` wrappers (`Button`, `Input`) only where markup
repeats. No component library. `constants.ts` holds `STATUS_LABELS` and
`STATUS_STYLES` (badge colors).

### Deliberately omitted

Optimistic updates, toasts, react-hook-form, react-query.

## 8. Local Dev Setup

### docker-compose.yml (Phase 1 — Postgres only)

- `postgres:16`, named volume for persistence, port `5432` exposed.
- env: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB=jobtracker`.
- healthcheck via `pg_isready`.
- Backend and frontend run on the host (not containerized yet).

### Env vars (`.env.example` committed, `.env` gitignored)

```
DATABASE_URL=postgresql+psycopg://jobtracker:jobtracker@localhost:5432/jobtracker
CORS_ORIGINS=http://localhost:5173
ENV=development
```

### README run sequence

```
docker compose up -d                    # start Postgres
cd backend
uv sync                                 # install deps
uv run alembic upgrade head             # create tables
uv run uvicorn app.main:app --reload    # API on :8000
# new terminal
cd frontend
npm install
npm run dev                             # UI on :5173
```

## 9. Testing (Phase 1)

### backend/tests/conftest.py

- Session-scoped fixture: separate test database `jobtracker_test`
  (created/dropped, or schema recreated per session).
- Function-scoped fixture: wrap each test in a transaction rolled back at teardown —
  no state leakage.
- `client` fixture: `TestClient` with `get_db` overridden to the test session.

### backend/tests/test_applications.py

- `test_create_application` — POST → 201; body has id, timestamps, default status.
- `test_list_applications` — create two → GET returns both, newest first.
- `test_update_status` — PATCH `{status: "interview"}` changes only that field, bumps
  `updated_at`.
- `test_delete_application` — DELETE → 204; subsequent GET → 404.
- `test_get_missing_returns_404`.

### Not in Phase 1

Frontend tests (Vitest), CI (GitHub Actions).

## 10. Future Phases (context, not scope)

Roughly, in order: auth (Google OAuth, add `user_id`) → Gmail API read + job-email
detection → LLM structured extraction → Redis + Celery for background email polling →
rate limiting + monitoring → CI + deployment. Each gets its own spec → plan →
implementation cycle. The feature-module backend layout and the service-layer seam
are chosen so these slot in without restructuring.
