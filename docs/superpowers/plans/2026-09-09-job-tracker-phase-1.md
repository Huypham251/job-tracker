# Job Application Tracker — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-stack Job Application Tracker where a user can manually create, view, edit, and delete job applications.

**Architecture:** React (Vite + TS + Tailwind) single-page frontend calls a FastAPI REST API under `/api/v1`. FastAPI routers are thin HTTP adapters; a per-feature `service.py` owns the SQLAlchemy `Session` and all DB logic; SQLAlchemy 2.0 ORM models map to PostgreSQL. Alembic manages schema. Postgres runs via Docker Compose; the app runs on the host.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.0, Alembic, psycopg 3, uv, pytest, httpx. Node 20+, React 18, Vite, TypeScript (strict), Tailwind CSS 3, npm. PostgreSQL 16.

**Spec:** `docs/superpowers/specs/2026-09-09-job-tracker-phase-1-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **API base path:** all application endpoints live under `/api/v1`. `/health` is the only path outside it.
- **Enum values:** `application_status` values are stored lowercase: `applied`, `oa`, `interview`, `rejected`, `offer`, `withdrawn`. Default is `applied`.
- **The 7 fields, exactly:** `id` (UUID), `company` (str ≤255), `position` (str ≤255), `status` (enum), `applied_at` (date, nullable), `created_at` (timestamptz), `updated_at` (timestamptz). No `user_id` in Phase 1.
- **Update semantics:** `PATCH` with partial bodies. Service applies only fields present in the request (`model_dump(exclude_unset=True)`). "Field absent" ≠ "set to null".
- **SQLAlchemy style:** 2.0 declarative — `DeclarativeBase`, `Mapped[...]`, `mapped_column(...)`. No legacy `Column` on model classes.
- **DB driver:** SQLAlchemy URL uses `postgresql+psycopg://` (psycopg 3). Direct psycopg connections (test DB bootstrap) use `postgresql://`.
- **Layer discipline:** routers contain no queries and no business rules; services contain no `HTTPException` and no FastAPI imports; models/schemas contain data shapes only.
- **Out of scope (do not add):** auth, OAuth, Gmail, LLM, Redis, Celery, rate limiting, monitoring, CI, deployment, containerizing the app, pagination, optimistic UI, toasts, frontend tests, react-query, react-hook-form.
- **Commits:** conventional-commit subject lines. End every commit message with:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```
- **Working directory:** repo root is `job-tracker/`. Backend commands run from `backend/`. Frontend commands run from `frontend/`.

---

## File Structure

```
job-tracker/
├── docker-compose.yml          # Task 1 — Postgres service
├── .env.example                # Task 1 — documented env vars (committed)
├── .gitignore                  # Task 1
├── README.md                   # Task 1 (skeleton) → Task 10 (final)
├── docs/superpowers/           # already present (spec + this plan)
│
├── backend/
│   ├── pyproject.toml          # Task 2 — deps via uv
│   ├── uv.lock                 # Task 2
│   ├── .env                    # Task 2 — gitignored, copied from .env.example
│   ├── alembic.ini             # Task 3
│   ├── alembic/
│   │   ├── env.py              # Task 3 — wired to Base.metadata + settings
│   │   └── versions/
│   │       └── 0001_create_applications.py   # Task 3
│   ├── app/
│   │   ├── __init__.py         # Task 2
│   │   ├── main.py             # Task 2 (health) → Task 6 (router + exc handler)
│   │   ├── core/
│   │   │   ├── __init__.py     # Task 2
│   │   │   └── config.py       # Task 2 — Settings
│   │   ├── db/
│   │   │   ├── __init__.py     # Task 2
│   │   │   ├── base.py         # Task 2 — Base + TimestampMixin
│   │   │   └── session.py      # Task 2 — engine, SessionLocal, get_db
│   │   └── applications/
│   │       ├── __init__.py     # Task 3
│   │       ├── models.py       # Task 3 — Application, ApplicationStatus
│   │       ├── schemas.py      # Task 4 — Create/Update/Read
│   │       ├── exceptions.py   # Task 5 — ApplicationNotFound
│   │       ├── service.py      # Task 5 — CRUD against Session
│   │       └── router.py       # Task 6 — APIRouter
│   └── tests/
│       ├── __init__.py         # Task 4
│       ├── conftest.py         # Task 4 (schema-only) → Task 5 (DB fixtures) → Task 6 (client)
│       ├── test_schemas.py     # Task 4
│       ├── test_service.py     # Task 5
│       └── test_applications_api.py   # Task 6
│
└── frontend/                   # Task 7 — Vite scaffold
    ├── package.json
    ├── vite.config.ts          # Task 7 — /api proxy
    ├── tailwind.config.js      # Task 7
    ├── postcss.config.js       # Task 7
    ├── tsconfig.json           # Task 7 (from template)
    ├── index.html
    └── src/
        ├── main.tsx            # Task 7 (from template)
        ├── App.tsx             # Task 7 (placeholder) → Task 9 (real)
        ├── index.css           # Task 7 — tailwind directives
        ├── constants.ts        # Task 8 — STATUS_LABELS, STATUS_STYLES
        ├── types/
        │   └── application.ts  # Task 8
        ├── api/
        │   └── applications.ts # Task 8
        ├── hooks/
        │   └── useApplications.ts   # Task 8
        └── components/
            ├── ApplicationsPage.tsx  # Task 9
            ├── ApplicationList.tsx   # Task 9
            ├── ApplicationRow.tsx    # Task 9
            └── ApplicationForm.tsx   # Task 9
```

---

## Task 1: Repo scaffolding & Postgres via Docker Compose

**Files:**
- Create: `.gitignore`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a running Postgres at `localhost:5432`, database `jobtracker`, user/password `jobtracker`/`jobtracker`. The connection string other tasks use:
  `postgresql+psycopg://jobtracker:jobtracker@localhost:5432/jobtracker`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
backend/.env

# Node
node_modules/
frontend/dist/

# Editors / OS
.DS_Store
.idea/
.vscode/

# Misc
*.log
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: jobtracker
      POSTGRES_PASSWORD: jobtracker
      POSTGRES_DB: jobtracker
    ports:
      - "5432:5432"
    volumes:
      - jobtracker_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U jobtracker -d jobtracker"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  jobtracker_pgdata:
```

- [ ] **Step 3: Create `.env.example`**

```dotenv
# Backend environment (copy to backend/.env for local dev)
DATABASE_URL=postgresql+psycopg://jobtracker:jobtracker@localhost:5432/jobtracker
CORS_ORIGINS=http://localhost:5173
ENV=development
```

- [ ] **Step 4: Create `README.md` skeleton**

```markdown
# Job Application Tracker

Phase 1: manual CRUD for job applications. React + FastAPI + SQLAlchemy + PostgreSQL.

## Prerequisites

- Docker + Docker Compose
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 20+

## Setup

See "Running locally" below. Full spec: `docs/superpowers/specs/2026-09-09-job-tracker-phase-1-design.md`.

## Running locally

_(filled in at the end of Phase 1)_
```

- [ ] **Step 5: Start Postgres and verify it is healthy**

Run: `docker compose up -d`
Then: `docker compose ps`
Expected: the `db` service is listed as `running` and `(healthy)` (wait up to ~30s; re-run `docker compose ps` if still `starting`).

- [ ] **Step 6: Verify a client can connect**

Run: `docker compose exec -T db psql -U jobtracker -d jobtracker -c "select 1;"`
Expected: prints a table with `?column?` / `1` and no error.

- [ ] **Step 7: Commit**

```bash
git add .gitignore docker-compose.yml .env.example README.md
git commit -m "chore: repo scaffolding and Postgres via Docker Compose

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Backend skeleton — config, DB session, `/health`

**Files:**
- Create: `backend/pyproject.toml`, `backend/uv.lock` (via uv)
- Create: `backend/.env` (copy of `.env.example`, gitignored)
- Create: `backend/app/__init__.py`, `backend/app/core/__init__.py`, `backend/app/db/__init__.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/db/base.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/main.py`

**Interfaces:**
- Consumes: Postgres connection string from Task 1.
- Produces:
  - `app.core.config.settings` — instance of `Settings` with attrs `database_url: str`, `cors_origins: str`, `env: str`, and property `cors_origins_list: list[str]`.
  - `app.db.base.Base` — `DeclarativeBase` subclass. `app.db.base.TimestampMixin` — mixin providing `created_at: Mapped[datetime]` and `updated_at: Mapped[datetime]` (both timezone-aware, server-defaulted to `clock_timestamp()`; `updated_at` also `onupdate` to `clock_timestamp()`).
  - `app.db.session.engine`, `app.db.session.SessionLocal`, `app.db.session.get_db` — generator dependency yielding a `Session`.
  - `app.main.app` — `FastAPI` instance. `app.main.create_app() -> FastAPI` — factory.
  - `GET /health` → `200 {"status": "ok"}`.

- [ ] **Step 1: Initialize the uv project and add dependencies**

```bash
cd backend
uv init --no-package
rm -f main.py hello.py          # remove uv's sample module if it created one
uv add fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings alembic "psycopg[binary]"
uv add --dev pytest httpx
```

Expected: `pyproject.toml` and `uv.lock` exist; `uv run python -c "import fastapi, sqlalchemy, psycopg"` prints nothing and exits 0.

- [ ] **Step 2: Create the `.env` file (gitignored)**

```bash
cp ../.env.example .env
```

Verify `.gitignore` already ignores `backend/.env` (from Task 1). Run `git status --porcelain backend/.env` — expected: no output.

- [ ] **Step 3: Create package `__init__.py` files**

Create empty files: `backend/app/__init__.py`, `backend/app/core/__init__.py`, `backend/app/db/__init__.py`.

- [ ] **Step 4: Write `backend/app/core/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    cors_origins: str = "http://localhost:5173"
    env: str = "development"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()  # type: ignore[call-arg]
```

- [ ] **Step 5: Write `backend/app/db/base.py`**

```python
from datetime import datetime

from sqlalchemy import DateTime, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        onupdate=text("clock_timestamp()"),
        nullable=False,
    )
```

Note: `clock_timestamp()` (not `now()`) so rows created within one transaction get distinct, monotonically increasing timestamps — required for deterministic "newest first" ordering in tests.

- [ ] **Step 6: Write `backend/app/db/session.py`**

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 7: Write `backend/app/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Job Application Tracker", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 8: Write the failing test — `backend/tests/__init__.py` (empty) and `backend/tests/conftest.py`**

`backend/tests/__init__.py`: empty file.

`backend/tests/conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
```

Create `backend/tests/test_health.py`:

```python
from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: `test_health_returns_ok PASSED`.
(If import of `app.main` fails for missing `DATABASE_URL`, confirm `backend/.env` exists from Step 2.)

- [ ] **Step 10: Verify the dev server boots**

Run: `cd backend && uv run uvicorn app.main:app --port 8000` — then in another shell `curl -s localhost:8000/health`.
Expected: `{"status":"ok"}`. Stop the server (Ctrl-C).

- [ ] **Step 11: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app backend/tests
git commit -m "feat(backend): app skeleton with config, DB session, health endpoint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Application model, Alembic setup, initial migration

**Files:**
- Create: `backend/app/applications/__init__.py`
- Create: `backend/app/applications/models.py`
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/README` (via `alembic init`)
- Modify: `backend/alembic.ini` (remove hardcoded URL)
- Modify: `backend/alembic/env.py` (wire to `Base.metadata` + `settings`)
- Create: `backend/alembic/versions/0001_create_applications.py`

**Interfaces:**
- Consumes: `app.db.base.Base`, `app.db.base.TimestampMixin`, `app.core.config.settings`.
- Produces:
  - `app.applications.models.ApplicationStatus` — `enum.StrEnum` with members `applied`, `oa`, `interview`, `rejected`, `offer`, `withdrawn` (member name == value).
  - `app.applications.models.Application` — ORM model, `__tablename__ = "applications"`, columns per Global Constraints. `id` defaults to `uuid.uuid4`. `status` defaults to `ApplicationStatus.applied`. `applied_at` nullable.
  - A migration chain with head revision `0001` that creates the `application_status` PG enum and the `applications` table.

- [ ] **Step 1: Create `backend/app/applications/__init__.py`** (empty file)

- [ ] **Step 2: Write `backend/app/applications/models.py`**

```python
import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ApplicationStatus(enum.StrEnum):
    applied = "applied"
    oa = "oa"
    interview = "interview"
    rejected = "rejected"
    offer = "offer"
    withdrawn = "withdrawn"


class Application(TimestampMixin, Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        SAEnum(
            ApplicationStatus,
            name="application_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ApplicationStatus.applied,
    )
    applied_at: Mapped[date | None] = mapped_column(Date, nullable=True)
```

- [ ] **Step 3: Initialize Alembic**

Run: `cd backend && uv run alembic init alembic`
Expected: creates `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/`.

- [ ] **Step 4: Edit `backend/alembic.ini` — neutralize the hardcoded URL**

Find the line:
```ini
sqlalchemy.url = driver://user:pass@localhost/dbname
```
Replace it with:
```ini
# URL is set from app.core.config.settings in alembic/env.py
sqlalchemy.url =
```

- [ ] **Step 5: Edit `backend/alembic/env.py` — wire to app metadata and settings**

Near the top, after `config = context.config`, add:

```python
from app.core.config import settings
from app.db.base import Base
from app.applications import models  # noqa: F401  (register models on Base.metadata)

config.set_main_option("sqlalchemy.url", settings.database_url)
```

Then change:
```python
target_metadata = None
```
to:
```python
target_metadata = Base.metadata
```

- [ ] **Step 6: Write `backend/alembic/versions/0001_create_applications.py`**

```python
"""create applications table

Revision ID: 0001
Revises:
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_VALUES = ("applied", "oa", "interview", "rejected", "offer", "withdrawn")


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company", sa.String(length=255), nullable=False),
        sa.Column("position", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*STATUS_VALUES, name="application_status"),
            nullable=False,
            server_default="applied",
        ),
        sa.Column("applied_at", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("applications")
    sa.Enum(name="application_status").drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 7: Apply the migration**

Run: `cd backend && uv run alembic upgrade head`
Expected: output ends with `Running upgrade  -> 0001, create applications table`.

- [ ] **Step 8: Verify the schema**

Run: `docker compose exec -T db psql -U jobtracker -d jobtracker -c "\d applications"`
Expected: table `applications` with the 7 columns; `status` type `application_status`; `id` is primary key.
Run: `docker compose exec -T db psql -U jobtracker -d jobtracker -c "\dT+ application_status"`
Expected: enum with elements `applied, oa, interview, rejected, offer, withdrawn`.

- [ ] **Step 9: Verify the migration reverses cleanly**

Run: `cd backend && uv run alembic downgrade base && uv run alembic upgrade head`
Expected: both commands succeed with no error.

- [ ] **Step 10: Verify autogenerate sees no drift**

Run: `cd backend && uv run alembic revision --autogenerate -m "drift check"`
Open the generated file in `alembic/versions/`. Expected: `upgrade()` and `downgrade()` bodies are empty (just `pass`). Delete that file:
```bash
rm backend/alembic/versions/*drift_check*.py
```

- [ ] **Step 11: Commit**

```bash
git add backend/app/applications backend/alembic.ini backend/alembic
git commit -m "feat(backend): applications model and initial migration

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Pydantic schemas (TDD)

**Files:**
- Create: `backend/app/applications/schemas.py`
- Create: `backend/tests/test_schemas.py`
- Modify: `backend/tests/conftest.py` (no change needed yet — leave as is; DB fixtures come in Task 5)

**Interfaces:**
- Consumes: `app.applications.models.ApplicationStatus`.
- Produces (in `app.applications.schemas`):
  - `ApplicationCreate` — fields: `company: str` (1–255, trimmed), `position: str` (1–255, trimmed), `status: ApplicationStatus = ApplicationStatus.applied`, `applied_at: date | None = None`.
  - `ApplicationUpdate` — fields all optional and default `None`: `company: str | None` (1–255, trimmed when present), `position: str | None` (same), `status: ApplicationStatus | None`, `applied_at: date | None`. Model validator rejects a body where no field was provided (raises → FastAPI 422).
  - `ApplicationRead` — fields: `id: UUID`, `company: str`, `position: str`, `status: ApplicationStatus`, `applied_at: date | None`, `created_at: datetime`, `updated_at: datetime`. `model_config = ConfigDict(from_attributes=True)`.

- [ ] **Step 1: Write the failing tests — `backend/tests/test_schemas.py`**

```python
import pytest
from pydantic import ValidationError

from app.applications.models import ApplicationStatus
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def test_create_defaults_status_to_applied() -> None:
    schema = ApplicationCreate(company="Acme", position="SWE Intern")
    assert schema.status is ApplicationStatus.applied
    assert schema.applied_at is None


def test_create_trims_whitespace() -> None:
    schema = ApplicationCreate(company="  Acme  ", position="  SWE  ")
    assert schema.company == "Acme"
    assert schema.position == "SWE"


def test_create_rejects_blank_company() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="   ", position="SWE")


def test_create_rejects_overlong_company() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="x" * 256, position="SWE")


def test_create_accepts_valid_status() -> None:
    schema = ApplicationCreate(company="Acme", position="SWE", status="interview")
    assert schema.status is ApplicationStatus.interview


def test_create_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="Acme", position="SWE", status="ghosted")


def test_update_allows_single_field() -> None:
    schema = ApplicationUpdate(status="offer")
    assert schema.model_dump(exclude_unset=True) == {"status": ApplicationStatus.offer}


def test_update_rejects_empty_body() -> None:
    with pytest.raises(ValidationError):
        ApplicationUpdate()


def test_update_trims_company_when_present() -> None:
    schema = ApplicationUpdate(company="  Acme  ")
    assert schema.company == "Acme"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.applications.schemas'`.

- [ ] **Step 3: Write `backend/app/applications/schemas.py`**

```python
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.applications.models import ApplicationStatus


def _strip(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=255)
    position: str = Field(min_length=1, max_length=255)
    status: ApplicationStatus = ApplicationStatus.applied
    applied_at: date | None = None

    @field_validator("company", "position", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return _strip(value)


class ApplicationUpdate(BaseModel):
    company: str | None = Field(default=None, min_length=1, max_length=255)
    position: str | None = Field(default=None, min_length=1, max_length=255)
    status: ApplicationStatus | None = None
    applied_at: date | None = None

    @field_validator("company", "position", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return _strip(value)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "ApplicationUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company: str
    position: str
    status: ApplicationStatus
    applied_at: date | None
    created_at: datetime
    updated_at: datetime
```

Note: `Field(min_length=1)` runs *after* the `mode="before"` trim, so `"   "` becomes `""` and is rejected — `test_create_rejects_blank_company` passes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_schemas.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/applications/schemas.py backend/tests/test_schemas.py
git commit -m "feat(backend): application request/response schemas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Service layer + domain exceptions (TDD)

**Files:**
- Create: `backend/app/applications/exceptions.py`
- Create: `backend/app/applications/service.py`
- Modify: `backend/tests/conftest.py` (add DB bootstrap + `db_session` fixture)
- Create: `backend/tests/test_service.py`

**Interfaces:**
- Consumes: `Application`, `ApplicationStatus` (models); `ApplicationCreate`, `ApplicationUpdate` (schemas); `Session` (SQLAlchemy).
- Produces:
  - `app.applications.exceptions.ApplicationNotFound(application_id: UUID)` — subclass of `Exception`; `str(exc)` == `"Application <id> not found"`; attribute `.application_id`.
  - `app.applications.service` functions, all taking `db: Session` first:
    - `list_applications(db) -> list[Application]` — ordered by `created_at` descending.
    - `get_application(db, application_id: UUID) -> Application` — raises `ApplicationNotFound` if absent.
    - `create_application(db, data: ApplicationCreate) -> Application` — commits, returns refreshed row.
    - `update_application(db, application_id: UUID, data: ApplicationUpdate) -> Application` — applies `model_dump(exclude_unset=True)`, commits, returns refreshed row; raises `ApplicationNotFound` if absent.
    - `delete_application(db, application_id: UUID) -> None` — raises `ApplicationNotFound` if absent; commits.
  - Test fixtures in `conftest.py`: `db_session` (function-scoped `Session`, rolled back after each test).

- [ ] **Step 1: Write `backend/app/applications/exceptions.py`**

```python
from uuid import UUID


class ApplicationNotFound(Exception):
    def __init__(self, application_id: UUID) -> None:
        self.application_id = application_id
        super().__init__(f"Application {application_id} not found")
```

- [ ] **Step 2: Replace `backend/tests/conftest.py` with the DB-aware version**

```python
import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.main import app

# Models must be imported so their tables are registered on Base.metadata.
from app.applications import models  # noqa: F401

TEST_DB_NAME = "jobtracker_test"
ADMIN_URL = "postgresql://jobtracker:jobtracker@localhost:5432/postgres"
TEST_DATABASE_URL = f"postgresql+psycopg://jobtracker:jobtracker@localhost:5432/{TEST_DB_NAME}"


def _ensure_test_database() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        ).fetchone()
        if row is None:
            conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')


@pytest.fixture(scope="session")
def engine():
    _ensure_test_database()
    eng = create_engine(TEST_DATABASE_URL, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session) -> TestClient:
    from app.db.session import get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
```

Note: `client` is now a generator fixture (`yield`), and `test_health.py` from Task 2 still works unchanged — it just gains a DB session it does not use. `join_transaction_mode="create_savepoint"` lets service code call `db.commit()` while the outer test transaction still rolls everything back.

- [ ] **Step 3: Write the failing tests — `backend/tests/test_service.py`**

```python
import uuid

import pytest

from app.applications import service
from app.applications.exceptions import ApplicationNotFound
from app.applications.models import ApplicationStatus
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def test_create_persists_with_defaults(db_session) -> None:
    created = service.create_application(
        db_session, ApplicationCreate(company="Acme", position="SWE Intern")
    )
    assert created.id is not None
    assert created.status is ApplicationStatus.applied
    assert created.created_at is not None
    assert created.updated_at is not None


def test_get_missing_raises(db_session) -> None:
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, uuid.uuid4())


def test_list_returns_newest_first(db_session) -> None:
    first = service.create_application(
        db_session, ApplicationCreate(company="A", position="P1")
    )
    second = service.create_application(
        db_session, ApplicationCreate(company="B", position="P2")
    )
    listed = service.list_applications(db_session)
    assert [row.id for row in listed] == [second.id, first.id]


def test_update_applies_only_provided_fields(db_session) -> None:
    created = service.create_application(
        db_session, ApplicationCreate(company="A", position="P")
    )
    updated = service.update_application(
        db_session, created.id, ApplicationUpdate(status="interview")
    )
    assert updated.status is ApplicationStatus.interview
    assert updated.company == "A"
    assert updated.position == "P"


def test_update_missing_raises(db_session) -> None:
    with pytest.raises(ApplicationNotFound):
        service.update_application(
            db_session, uuid.uuid4(), ApplicationUpdate(status="offer")
        )


def test_delete_removes_row(db_session) -> None:
    created = service.create_application(
        db_session, ApplicationCreate(company="A", position="P")
    )
    service.delete_application(db_session, created.id)
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, created.id)


def test_delete_missing_raises(db_session) -> None:
    with pytest.raises(ApplicationNotFound):
        service.delete_application(db_session, uuid.uuid4())
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.applications.service'`.
(If instead it fails at fixture setup with a psycopg connection error, ensure `docker compose up -d` is running.)

- [ ] **Step 5: Write `backend/app/applications/service.py`**

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.exceptions import ApplicationNotFound
from app.applications.models import Application
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def list_applications(db: Session) -> list[Application]:
    statement = select(Application).order_by(Application.created_at.desc())
    return list(db.scalars(statement))


def get_application(db: Session, application_id: UUID) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise ApplicationNotFound(application_id)
    return application


def create_application(db: Session, data: ApplicationCreate) -> Application:
    application = Application(**data.model_dump())
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def update_application(
    db: Session, application_id: UUID, data: ApplicationUpdate
) -> Application:
    application = get_application(db, application_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(application, field, value)
    db.commit()
    db.refresh(application)
    return application


def delete_application(db: Session, application_id: UUID) -> None:
    application = get_application(db, application_id)
    db.delete(application)
    db.commit()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_service.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 7: Run the whole backend suite**

Run: `cd backend && uv run pytest -v`
Expected: `test_health`, `test_schemas`, `test_service` — all green.

- [ ] **Step 8: Commit**

```bash
git add backend/app/applications/exceptions.py backend/app/applications/service.py backend/tests/conftest.py backend/tests/test_service.py
git commit -m "feat(backend): application service layer and domain exceptions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: API router + wire into app + exception handler (TDD)

**Files:**
- Create: `backend/app/applications/router.py`
- Modify: `backend/app/main.py` (register router under `/api/v1`, add `ApplicationNotFound` handler)
- Create: `backend/tests/test_applications_api.py`

**Interfaces:**
- Consumes: `service` functions, schemas, `get_db`, `ApplicationNotFound`.
- Produces:
  - `app.applications.router.router` — `APIRouter(prefix="/applications", tags=["applications"])` with: `GET ""`, `POST ""` (201), `GET "/{application_id}"`, `PATCH "/{application_id}"`, `DELETE "/{application_id}"` (204). All use `response_model=ApplicationRead` except `DELETE`.
  - `app.main`: router mounted at `/api/v1`; exception handler maps `ApplicationNotFound` → `404 {"detail": "<message>"}`.

- [ ] **Step 1: Write the failing tests — `backend/tests/test_applications_api.py`**

```python
from fastapi.testclient import TestClient

BASE = "/api/v1/applications"
MISSING_ID = "00000000-0000-0000-0000-000000000000"


def test_create_returns_201_with_body(client: TestClient) -> None:
    response = client.post(BASE, json={"company": "Acme", "position": "SWE Intern"})
    assert response.status_code == 201
    body = response.json()
    assert body["company"] == "Acme"
    assert body["position"] == "SWE Intern"
    assert body["status"] == "applied"
    assert body["applied_at"] is None
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


def test_create_accepts_status_and_applied_at(client: TestClient) -> None:
    response = client.post(
        BASE,
        json={
            "company": "Acme",
            "position": "SWE",
            "status": "interview",
            "applied_at": "2026-09-01",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "interview"
    assert body["applied_at"] == "2026-09-01"


def test_create_rejects_blank_company_with_422(client: TestClient) -> None:
    response = client.post(BASE, json={"company": "   ", "position": "SWE"})
    assert response.status_code == 422


def test_list_returns_newest_first(client: TestClient) -> None:
    client.post(BASE, json={"company": "A", "position": "P1"})
    client.post(BASE, json={"company": "B", "position": "P2"})
    response = client.get(BASE)
    assert response.status_code == 200
    assert [row["company"] for row in response.json()] == ["B", "A"]


def test_patch_updates_only_status(client: TestClient) -> None:
    created = client.post(BASE, json={"company": "A", "position": "P"}).json()
    response = client.patch(f"{BASE}/{created['id']}", json={"status": "offer"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    assert body["company"] == "A"
    assert body["updated_at"] >= created["updated_at"]


def test_patch_empty_body_returns_422(client: TestClient) -> None:
    created = client.post(BASE, json={"company": "A", "position": "P"}).json()
    response = client.patch(f"{BASE}/{created['id']}", json={})
    assert response.status_code == 422


def test_patch_missing_returns_404(client: TestClient) -> None:
    response = client.patch(f"{BASE}/{MISSING_ID}", json={"status": "offer"})
    assert response.status_code == 404
    assert MISSING_ID in response.json()["detail"]


def test_delete_returns_204_then_get_404(client: TestClient) -> None:
    created = client.post(BASE, json={"company": "A", "position": "P"}).json()
    assert client.delete(f"{BASE}/{created['id']}").status_code == 204
    assert client.get(f"{BASE}/{created['id']}").status_code == 404


def test_delete_missing_returns_404(client: TestClient) -> None:
    assert client.delete(f"{BASE}/{MISSING_ID}").status_code == 404


def test_get_missing_returns_404(client: TestClient) -> None:
    response = client.get(f"{BASE}/{MISSING_ID}")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_applications_api.py -v`
Expected: FAIL — all requests to `/api/v1/applications` return 404 (router not mounted).

- [ ] **Step 3: Write `backend/app/applications/router.py`**

```python
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.applications import service
from app.applications.schemas import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
)
from app.db.session import get_db

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("", response_model=list[ApplicationRead])
def list_applications(db: Session = Depends(get_db)) -> list:
    return service.list_applications(db)


@router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate, db: Session = Depends(get_db)
):
    return service.create_application(db, payload)


@router.get("/{application_id}", response_model=ApplicationRead)
def get_application(application_id: UUID, db: Session = Depends(get_db)):
    return service.get_application(db, application_id)


@router.patch("/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
):
    return service.update_application(db, application_id, payload)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(
    application_id: UUID, db: Session = Depends(get_db)
) -> Response:
    service.delete_application(db, application_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Update `backend/app/main.py`**

```python
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.applications.exceptions import ApplicationNotFound
from app.applications.router import router as applications_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Job Application Tracker", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApplicationNotFound)
    async def handle_application_not_found(
        request: Request, exc: ApplicationNotFound
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(applications_router, prefix="/api/v1")

    return app


app = create_app()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_applications_api.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: every test green (health + schemas + service + api).

- [ ] **Step 7: Manual smoke test against the real dev DB**

Start the API: `cd backend && uv run uvicorn app.main:app --reload --port 8000`
In another shell:
```bash
curl -s -X POST localhost:8000/api/v1/applications -H 'content-type: application/json' -d '{"company":"Acme","position":"SWE Intern"}'
curl -s localhost:8000/api/v1/applications
```
Expected: POST returns a JSON object with an `id`; GET returns a list containing it. Open `http://localhost:8000/docs` and confirm all five endpoints are listed. Stop the server.

- [ ] **Step 8: Commit**

```bash
git add backend/app/applications/router.py backend/app/main.py backend/tests/test_applications_api.py
git commit -m "feat(backend): applications REST endpoints under /api/v1

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Frontend scaffold — Vite + Tailwind + API proxy

**Files:**
- Create: `frontend/` (Vite `react-ts` template — `package.json`, `index.html`, `tsconfig*.json`, `src/main.tsx`, `src/vite-env.d.ts`, etc.)
- Modify: `frontend/vite.config.ts` (add `/api` proxy)
- Create: `frontend/tailwind.config.js`, `frontend/postcss.config.js`
- Replace: `frontend/src/index.css` (Tailwind directives)
- Replace: `frontend/src/App.tsx` (placeholder)
- Delete: `frontend/src/App.css`, `frontend/src/assets/react.svg` (unused template files)

**Interfaces:**
- Consumes: the backend at `http://localhost:8000`.
- Produces: `npm run dev` serves the app at `http://localhost:5173`; requests to `/api/*` are proxied to the backend. `npm run build` type-checks and builds with no errors.

- [ ] **Step 1: Scaffold the Vite project**

From the repo root:
```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D tailwindcss@^3 postcss autoprefixer
```

- [ ] **Step 2: Create `frontend/tailwind.config.js`**

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {} },
  plugins: [],
}
```

- [ ] **Step 3: Create `frontend/postcss.config.js`**

```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 4: Replace `frontend/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 5: Replace `frontend/vite.config.ts`**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
```

- [ ] **Step 6: Replace `frontend/src/App.tsx` with a placeholder**

```tsx
export default function App() {
  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <h1 className="text-2xl font-bold text-gray-900">Job Application Tracker</h1>
    </div>
  )
}
```

- [ ] **Step 7: Remove unused template files**

```bash
rm -f frontend/src/App.css frontend/src/assets/react.svg
```
If `frontend/src/main.tsx` imports `./App.css`, remove that import line. Keep its `import './index.css'`.

- [ ] **Step 8: Verify the build type-checks**

Run: `cd frontend && npm run build`
Expected: completes with no TypeScript errors; `dist/` is produced.

- [ ] **Step 9: Verify Tailwind is active in the dev server**

Run: `cd frontend && npm run dev` — open `http://localhost:5173`.
Expected: the heading renders in a bold, dark, sans-serif style on a light-gray full-height background (proves Tailwind classes apply). Stop the server.

- [ ] **Step 10: Commit**

```bash
git add frontend .gitignore
git commit -m "chore(frontend): Vite + React + Tailwind scaffold with API proxy

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Frontend data layer — types, constants, API client, hook

**Files:**
- Create: `frontend/src/types/application.ts`
- Create: `frontend/src/constants.ts`
- Create: `frontend/src/api/applications.ts`
- Create: `frontend/src/hooks/useApplications.ts`

**Interfaces:**
- Consumes: backend endpoints under `/api/v1/applications` (proxied).
- Produces:
  - `types/application.ts`: `APPLICATION_STATUSES` (readonly tuple), `ApplicationStatus` (union type), `Application` (the 7 fields; dates as ISO strings; `applied_at: string | null`), `ApplicationCreate` (`company`, `position` required; `status?`, `applied_at?`), `ApplicationUpdate = Partial<ApplicationCreate>`.
  - `constants.ts`: `STATUS_LABELS: Record<ApplicationStatus, string>`, `STATUS_STYLES: Record<ApplicationStatus, string>` (Tailwind badge classes).
  - `api/applications.ts`: `listApplications()`, `createApplication(data)`, `updateApplication(id, data)`, `deleteApplication(id)` — all throw `Error` (message from response `detail`) on non-2xx.
  - `hooks/useApplications.ts`: `useApplications()` returning `{ applications: Application[], loading: boolean, error: string | null, refetch, create, update, remove }`. Mutations re-fetch the list on success and let errors propagate to the caller.

- [ ] **Step 1: Write `frontend/src/types/application.ts`**

```typescript
export const APPLICATION_STATUSES = [
  'applied',
  'oa',
  'interview',
  'rejected',
  'offer',
  'withdrawn',
] as const

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number]

export interface Application {
  id: string
  company: string
  position: string
  status: ApplicationStatus
  applied_at: string | null
  created_at: string
  updated_at: string
}

export interface ApplicationCreate {
  company: string
  position: string
  status?: ApplicationStatus
  applied_at?: string | null
}

export type ApplicationUpdate = Partial<ApplicationCreate>
```

- [ ] **Step 2: Write `frontend/src/constants.ts`**

```typescript
import type { ApplicationStatus } from './types/application'

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  applied: 'Applied',
  oa: 'OA',
  interview: 'Interview',
  rejected: 'Rejected',
  offer: 'Offer',
  withdrawn: 'Withdrawn',
}

export const STATUS_STYLES: Record<ApplicationStatus, string> = {
  applied: 'bg-blue-100 text-blue-800',
  oa: 'bg-purple-100 text-purple-800',
  interview: 'bg-amber-100 text-amber-800',
  rejected: 'bg-red-100 text-red-800',
  offer: 'bg-green-100 text-green-800',
  withdrawn: 'bg-gray-100 text-gray-700',
}
```

- [ ] **Step 3: Write `frontend/src/api/applications.ts`**

```typescript
import type {
  Application,
  ApplicationCreate,
  ApplicationUpdate,
} from '../types/application'

const BASE = '/api/v1/applications'

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // response had no JSON body; keep the default message
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export function listApplications(): Promise<Application[]> {
  return fetch(BASE).then((r) => parse<Application[]>(r))
}

export function createApplication(
  data: ApplicationCreate,
): Promise<Application> {
  return fetch(BASE, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  }).then((r) => parse<Application>(r))
}

export function updateApplication(
  id: string,
  data: ApplicationUpdate,
): Promise<Application> {
  return fetch(`${BASE}/${id}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  }).then((r) => parse<Application>(r))
}

export function deleteApplication(id: string): Promise<void> {
  return fetch(`${BASE}/${id}`, { method: 'DELETE' }).then((r) =>
    parse<void>(r),
  )
}
```

- [ ] **Step 4: Write `frontend/src/hooks/useApplications.ts`**

```typescript
import { useCallback, useEffect, useState } from 'react'

import * as api from '../api/applications'
import type {
  Application,
  ApplicationCreate,
  ApplicationUpdate,
} from '../types/application'

export function useApplications() {
  const [applications, setApplications] = useState<Application[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setApplications(await api.listApplications())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load applications')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refetch()
  }, [refetch])

  const create = useCallback(
    async (data: ApplicationCreate) => {
      await api.createApplication(data)
      await refetch()
    },
    [refetch],
  )

  const update = useCallback(
    async (id: string, data: ApplicationUpdate) => {
      await api.updateApplication(id, data)
      await refetch()
    },
    [refetch],
  )

  const remove = useCallback(
    async (id: string) => {
      await api.deleteApplication(id)
      await refetch()
    },
    [refetch],
  )

  return { applications, loading, error, refetch, create, update, remove }
}
```

- [ ] **Step 5: Verify the build type-checks**

Run: `cd frontend && npm run build`
Expected: no TypeScript errors. (Nothing imports the new files yet; this only proves they compile.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types frontend/src/constants.ts frontend/src/api frontend/src/hooks
git commit -m "feat(frontend): types, API client, and useApplications hook

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: Frontend components — form, row, list, page

**Files:**
- Create: `frontend/src/components/ApplicationForm.tsx`
- Create: `frontend/src/components/ApplicationRow.tsx`
- Create: `frontend/src/components/ApplicationList.tsx`
- Create: `frontend/src/components/ApplicationsPage.tsx`
- Modify: `frontend/src/App.tsx` (render `ApplicationsPage`)

**Interfaces:**
- Consumes: `useApplications` hook; `Application`, `ApplicationCreate`, `ApplicationStatus`, `APPLICATION_STATUSES` types; `STATUS_LABELS`, `STATUS_STYLES`.
- Produces:
  - `ApplicationForm` — props `{ initial?: Application; submitLabel: string; onSubmit: (data: ApplicationCreate) => Promise<void>; onCancel?: () => void }`. Controlled inputs for company, position, status (`<select>`), applied_at (`<input type="date">`). Shows a submission error. Clears itself after a successful create (when `initial` is undefined).
  - `ApplicationRow` — props `{ application: Application; onEdit: () => void; onDelete: () => void }`. One table row: company, position, status badge, applied date, Edit + Delete buttons. Delete calls `window.confirm` first.
  - `ApplicationList` — props `{ applications: Application[]; onEdit: (a: Application) => void; onDelete: (id: string) => void }`. Renders a table, or an empty-state message.
  - `ApplicationsPage` — owns "which row is being edited" and "is the add form shown" state; wires the hook to the components.
  - `App` renders `<ApplicationsPage />`.

- [ ] **Step 1: Write `frontend/src/components/ApplicationForm.tsx`**

```tsx
import { useState, type FormEvent } from 'react'

import {
  APPLICATION_STATUSES,
  type Application,
  type ApplicationCreate,
  type ApplicationStatus,
} from '../types/application'
import { STATUS_LABELS } from '../constants'

interface Props {
  initial?: Application
  submitLabel: string
  onSubmit: (data: ApplicationCreate) => Promise<void>
  onCancel?: () => void
}

export function ApplicationForm({ initial, submitLabel, onSubmit, onCancel }: Props) {
  const [company, setCompany] = useState(initial?.company ?? '')
  const [position, setPosition] = useState(initial?.position ?? '')
  const [status, setStatus] = useState<ApplicationStatus>(initial?.status ?? 'applied')
  const [appliedAt, setAppliedAt] = useState(initial?.applied_at ?? '')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({
        company: company.trim(),
        position: position.trim(),
        status,
        applied_at: appliedAt || null,
      })
      if (!initial) {
        setCompany('')
        setPosition('')
        setStatus('applied')
        setAppliedAt('')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass =
    'w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none'

  return (
    <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">Company</span>
          <input
            className={inputClass}
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            required
            maxLength={255}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">Position</span>
          <input
            className={inputClass}
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            required
            maxLength={255}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">Status</span>
          <select
            className={inputClass}
            value={status}
            onChange={(e) => setStatus(e.target.value as ApplicationStatus)}
          >
            {APPLICATION_STATUSES.map((value) => (
              <option key={value} value={value}>
                {STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">Applied on</span>
          <input
            type="date"
            className={inputClass}
            value={appliedAt ?? ''}
            onChange={(e) => setAppliedAt(e.target.value)}
          />
        </label>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {submitting ? 'Saving…' : submitLabel}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
```

- [ ] **Step 2: Write `frontend/src/components/ApplicationRow.tsx`**

```tsx
import type { Application } from '../types/application'
import { STATUS_LABELS, STATUS_STYLES } from '../constants'

interface Props {
  application: Application
  onEdit: () => void
  onDelete: () => void
}

export function ApplicationRow({ application, onEdit, onDelete }: Props) {
  function handleDelete() {
    if (window.confirm(`Delete the ${application.company} application?`)) {
      onDelete()
    }
  }

  return (
    <tr className="border-b border-gray-100 last:border-0">
      <td className="px-4 py-3 text-sm font-medium text-gray-900">{application.company}</td>
      <td className="px-4 py-3 text-sm text-gray-700">{application.position}</td>
      <td className="px-4 py-3 text-sm">
        <span
          className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[application.status]}`}
        >
          {STATUS_LABELS[application.status]}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-gray-700">{application.applied_at ?? '—'}</td>
      <td className="px-4 py-3 text-right text-sm">
        <button onClick={onEdit} className="mr-3 text-blue-600 hover:underline">
          Edit
        </button>
        <button onClick={handleDelete} className="text-red-600 hover:underline">
          Delete
        </button>
      </td>
    </tr>
  )
}
```

- [ ] **Step 3: Write `frontend/src/components/ApplicationList.tsx`**

```tsx
import type { Application } from '../types/application'
import { ApplicationRow } from './ApplicationRow'

interface Props {
  applications: Application[]
  onEdit: (application: Application) => void
  onDelete: (id: string) => void
}

export function ApplicationList({ applications, onEdit, onDelete }: Props) {
  if (applications.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-500">
        No applications yet. Add your first one above.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="min-w-full">
        <thead>
          <tr className="border-b border-gray-200 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
            <th className="px-4 py-3">Company</th>
            <th className="px-4 py-3">Position</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Applied</th>
            <th className="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {applications.map((application) => (
            <ApplicationRow
              key={application.id}
              application={application}
              onEdit={() => onEdit(application)}
              onDelete={() => onDelete(application.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 4: Write `frontend/src/components/ApplicationsPage.tsx`**

```tsx
import { useState } from 'react'

import { useApplications } from '../hooks/useApplications'
import type { Application } from '../types/application'
import { ApplicationForm } from './ApplicationForm'
import { ApplicationList } from './ApplicationList'

export function ApplicationsPage() {
  const { applications, loading, error, create, update, remove } = useApplications()
  const [editing, setEditing] = useState<Application | null>(null)

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 sm:p-8">
      <header>
        <h1 className="text-2xl font-bold text-gray-900">Job Application Tracker</h1>
        <p className="text-sm text-gray-500">
          {applications.length} application{applications.length === 1 ? '' : 's'}
        </p>
      </header>

      {editing ? (
        <ApplicationForm
          key={editing.id}
          initial={editing}
          submitLabel="Save changes"
          onSubmit={async (data) => {
            await update(editing.id, data)
            setEditing(null)
          }}
          onCancel={() => setEditing(null)}
        />
      ) : (
        <ApplicationForm submitLabel="Add application" onSubmit={create} />
      )}

      {error && (
        <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <ApplicationList
          applications={applications}
          onEdit={setEditing}
          onDelete={(id) => {
            void remove(id)
          }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 5: Update `frontend/src/App.tsx`**

```tsx
import { ApplicationsPage } from './components/ApplicationsPage'

export default function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <ApplicationsPage />
    </div>
  )
}
```

- [ ] **Step 6: Verify the build type-checks**

Run: `cd frontend && npm run build`
Expected: no TypeScript errors; `dist/` produced.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): applications page with create, edit, delete

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 10: End-to-end verification + README

**Files:**
- Modify: `README.md` (fill in "Running locally", project layout, testing)

**Interfaces:**
- Consumes: everything built so far.
- Produces: a README that a new developer can follow start-to-finish, and a verified working full stack.

- [ ] **Step 1: Fresh full-stack bring-up**

In three terminals from the repo root:
```bash
# 1
docker compose up -d

# 2
cd backend && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --reload --port 8000

# 3
cd frontend && npm install && npm run dev
```
Open `http://localhost:5173`.

- [ ] **Step 2: Exercise every Phase 1 requirement in the browser**

Confirm each of these works and note the result:
1. **Add** — fill the form (company, position, status, applied date), submit → row appears in the table, form clears.
2. **View all** — reload the page → the row is still there, newest first.
3. **Edit** — click Edit → form populates → change status → Save changes → the row's badge updates.
4. **Delete** — click Delete → confirm the dialog → the row disappears; reload → still gone.
5. **Validation** — submit with an empty company (browser blocks it) and, to see the server path, temporarily remove `required` in devtools or POST a blank company via `curl` → API returns 422.

- [ ] **Step 3: Run the whole backend test suite once more**

Run: `cd backend && uv run pytest -v`
Expected: all tests green.

- [ ] **Step 4: Rewrite `README.md`**

```markdown
# Job Application Tracker

Phase 1: manually create, view, edit, and delete job applications.

**Stack:** React + Vite + TypeScript + Tailwind (frontend) · FastAPI + SQLAlchemy + Alembic (backend) · PostgreSQL.

Full design: `docs/superpowers/specs/2026-09-09-job-tracker-phase-1-design.md`

## Prerequisites

- Docker + Docker Compose
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 20+

## Project layout

```
backend/    FastAPI app (app/), Alembic migrations (alembic/), tests (tests/)
frontend/   Vite + React app (src/)
docker-compose.yml   PostgreSQL for local dev
```

Backend structure: `app/applications/` holds the feature (model, schemas,
service, router). `app/core/` is config, `app/db/` is the engine/session and
declarative base.

## Running locally

Start PostgreSQL:

```bash
docker compose up -d
```

Backend (in `backend/`):

```bash
cp ../.env.example .env      # first time only
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

Frontend (in `frontend/`):

```bash
npm install
npm run dev
```

App: http://localhost:5173 (the dev server proxies `/api` to the backend).

## Database migrations

```bash
cd backend
uv run alembic revision --autogenerate -m "describe change"   # generate
# review the file in alembic/versions/ before applying
uv run alembic upgrade head
```

## Tests

```bash
cd backend
uv run pytest
```

The test suite creates and uses a separate `jobtracker_test` database on the
same Postgres instance; each test runs in a rolled-back transaction.
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: Phase 1 setup and usage instructions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Final verification summary**

Confirm and report:
- `docker compose ps` → `db` healthy
- `cd backend && uv run pytest` → all pass (state the count)
- `cd frontend && npm run build` → succeeds
- The five browser actions from Step 2 → all worked

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §2 decisions: monorepo, docker-compose, enum, no user_id, feature modules, uv, npm, smoke tests, UUID, PATCH, psycopg | Tasks 1–10 (constraints encoded in Global Constraints + tasks) |
| §3 project structure | Task 1 (root), 2 (skeleton), 3 (applications pkg), 7 (frontend) |
| §4 data model: table, 7 columns, enum, TimestampMixin, Alembic env + 0001, no seed | Tasks 2 (mixin), 3 (model + migration) |
| §5 API contract: all 6 endpoints, schemas, PATCH semantics, no pagination, error shape, CORS | Tasks 4 (schemas), 6 (router + main + CORS already in Task 2) |
| §6 request flow: layers, get_db, explicit commit, domain exception + single handler, config | Tasks 2 (config, session), 5 (service, exceptions), 6 (handler) |
| §7 frontend: layers, component tree, data flow, proxy, styling, constants, omissions | Tasks 7 (scaffold + proxy), 8 (types/api/hook/constants), 9 (components) |
| §8 dev setup: compose Postgres-only, env vars, README run sequence | Tasks 1, 10 |
| §9 testing: conftest test DB, transaction rollback, the 5 named tests + 404 test | Tasks 5 (conftest, service tests), 6 (API tests incl. all 5 named cases + get-missing-404) |

The spec's function-scoped fixture is implemented with SQLAlchemy 2.0's
`join_transaction_mode="create_savepoint"` (Task 5, conftest) rather than a
manual savepoint/event-listener — same behavior (per-test rollback tolerant of
`db.commit()`), less code. Session-scoped `engine` fixture creates the schema
once via `Base.metadata.create_all` and bootstraps `jobtracker_test` with a
direct psycopg connection. This matches spec §9 intent.

The spec lists `GET /health` returning `{"status":"ok"}`; implemented in Task 2
and tested in `test_health.py`. Spec §5's `/health` example body is
`{"status":"ok"}` — consistent.

**2. Placeholder scan:** No "TBD"/"TODO"/"handle edge cases"/"similar to Task N".
Every code step contains full file contents or an exact find/replace. The README
in Task 1 contains a deliberate `_(filled in at the end of Phase 1)_` marker that
Task 10 Step 4 replaces with the complete document — noted here so it is not
mistaken for an unfinished plan step.

**3. Type consistency:**
- Service signatures in Task 5 Interfaces match their definitions in Task 5 Step 5 and their use in Task 6 router (`list_applications(db)`, `get_application(db, id)`, `create_application(db, data)`, `update_application(db, id, data)`, `delete_application(db, id)`).
- `ApplicationNotFound(application_id)` — defined Task 5 Step 1, raised in service Task 5 Step 5, handled in main Task 6 Step 4, asserted via `MISSING_ID in detail` in Task 6 Step 1.
- Schema class names (`ApplicationCreate`, `ApplicationUpdate`, `ApplicationRead`) consistent across Tasks 4, 5, 6.
- Frontend: `useApplications()` returns `{ applications, loading, error, refetch, create, update, remove }` — defined Task 8 Step 4, consumed in `ApplicationsPage` Task 9 Step 4 (`create`, `update`, `remove`, `applications`, `loading`, `error`).
- `ApplicationForm` prop `submitLabel` — declared in Task 9 Step 1 `Props`, passed in Task 9 Step 4 (`"Add application"` / `"Save changes"`).
- API client names (`listApplications`, `createApplication`, `updateApplication`, `deleteApplication`) consistent across Task 8 Steps 3–4.
- `clock_timestamp()` used in both `TimestampMixin` (Task 2 Step 5) and migration `0001` (Task 3 Step 6) — the "newest first" tests (Task 5, Task 6) depend on this agreement.
```
