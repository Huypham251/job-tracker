# Job Application Tracker — Phase 2 Implementation Plan: Auth & Multi-User

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Google OAuth login, a `User` model, and scope all application CRUD to the authenticated user — while preserving every Phase 1 behavior and fixing the deferred Alembic-test-fixture debt.

**Architecture:** Backend-driven OAuth 2.0 authorization-code flow (Authlib) against Google; on success the backend mints its own JWT (PyJWT) and sets it as an HttpOnly cookie. A `get_current_user` FastAPI dependency decodes that cookie on every protected route. Application ownership is enforced by filtering every query on `user_id` in the service layer, never as a separate check. Frontend gates the dashboard behind a small `AuthContext`; no router added (two screens, conditional render).

**Tech Stack:** Adds `authlib`, `pyjwt`, `itsdangerous` to the existing Phase 1 backend (FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, uv, pytest). Frontend unchanged stack (Vite, React 18, TS strict, Tailwind 3) — no new frontend dependencies.

**Spec:** `docs/superpowers/specs/2026-09-11-job-tracker-phase-2-auth-design.md`

**Builds on:** `docs/superpowers/specs/2026-09-09-job-tracker-phase-1-design.md` and its implementation (already merged to `main`).

## Global Constraints

- **Cookie name:** `access_token` (constant `COOKIE_NAME` in `app/auth/dependencies.py`), `HttpOnly`, `SameSite=Lax`, `Secure=settings.cookie_secure`.
- **JWT claims:** `{"sub": "<user id>", "exp": <unix timestamp>}`, signed HS256 with `settings.secret_key`.
- **Identity key:** Google `sub` claim → `User.google_sub` (unique). Never join on email.
- **404, never 403,** for a resource that exists but belongs to another user. Reuses the existing `ApplicationNotFound`.
- **`user_id` is never client-suppliable** — always `current_user.id` from the dependency, never a request body field.
- **No Gmail scope requested.** OAuth scope stays `openid email profile`.
- **Layering discipline (unchanged from Phase 1):** routers are HTTP-only; `service.py` has no FastAPI imports and raises no `HTTPException`; `app/auth/dependencies.py` is the one place JWT/cookie logic lives and MAY raise `HTTPException` (it is a dependency, not a service).
- **No automated test ever calls real Google endpoints.** Auth tests mint a valid session JWT directly via `create_access_token()` for a test-created `User` — the OAuth handshake itself (`/google/login`, `/google/callback`) is not covered by automated tests in this phase (no network dependency on a third party in the suite); it is verified manually once real Google credentials exist (Task 8).
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```
- **Working directory:** repo root is `Job-tracker/` (already on `main`, no branch created yet — Task 0 of execution, not this plan, handles isolation). Backend commands run from `backend/`. Frontend commands from `frontend/`. Local Postgres 16 at `localhost:5432`, role/db `jobtracker` + `jobtracker_test` already exist (no Docker on this machine — same as Phase 1).
- **Out of scope (do not add):** Gmail API calls, LLM extraction, Redis, Celery, rate limiting, monitoring, CI, deployment, CSRF-token middleware, session revocation, a frontend router, multi-provider login.

---

## File Structure

```
backend/
├── .env                                              # Task 1 — MODIFIED (gitignored)
├── .env.example                                      # Task 1 — MODIFIED
├── pyproject.toml / uv.lock                          # Task 1 — MODIFIED (uv add)
├── alembic/
│   ├── env.py                                        # Task 2 (URL fix) → Task 3 (users import)
│   └── versions/
│       ├── 0002_create_users.py                      # Task 3 — NEW
│       └── 0003_add_user_id_to_applications.py       # Task 5 — NEW
├── app/
│   ├── main.py                                       # Task 4 (mount auth) → Task 5 (nothing further)
│   ├── core/config.py                                # Task 1 — MODIFIED
│   ├── users/                                        # Task 3 — NEW package
│   │   ├── __init__.py
│   │   ├── models.py
│   │   └── schemas.py
│   ├── auth/                                         # Task 4 — NEW package
│   │   ├── __init__.py
│   │   ├── oauth.py
│   │   ├── jwt.py
│   │   ├── dependencies.py
│   │   └── router.py
│   └── applications/
│       ├── models.py                                 # Task 5 — MODIFIED (user_id + relationship)
│       ├── service.py                                # Task 5 — MODIFIED (user_id scoping)
│       └── router.py                                 # Task 5 — MODIFIED (Depends(get_current_user))
└── tests/
    ├── conftest.py                                   # Task 2 (alembic fix) → Task 4 (user/auth_client) → Task 5 (other_user/other_auth_client)
    ├── test_service.py                                # Task 5 — MODIFIED
    ├── test_applications_api.py                       # Task 5 — MODIFIED
    └── test_auth.py                                   # Task 4 — NEW

frontend/src/
├── types/
│   └── user.ts                                       # Task 6 — NEW
├── api/
│   ├── http.ts                                       # Task 6 — NEW (shared parseResponse)
│   ├── applications.ts                               # Task 6 — MODIFIED (use shared helper)
│   └── auth.ts                                       # Task 6 — NEW
├── context/
│   └── AuthContext.tsx                               # Task 6 — NEW
├── components/
│   ├── LoginPage.tsx                                 # Task 7 — NEW
│   ├── UserMenu.tsx                                  # Task 7 — NEW
│   └── ApplicationsPage.tsx                          # Task 7 — MODIFIED
├── App.tsx                                           # Task 7 — MODIFIED
└── main.tsx                                          # Task 7 — MODIFIED

README.md                                             # Task 8 — MODIFIED
```

**Note on the spec's migration:** the design doc describes one migration; this plan splits it into `0002_create_users` (Task 3, standalone) and `0003_add_user_id_to_applications` (Task 5, bundled with the service/router/test changes that consume it) so every task leaves a fully green test suite. Same end state.

---

## Task 1: Config, dependencies, env files

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (via `uv add`)
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env` (gitignored — local placeholder values)
- Modify: `.env.example` (repo root, committed)

**Interfaces:**
- Consumes: nothing new.
- Produces: `settings.google_client_id`, `settings.google_client_secret`, `settings.secret_key`, `settings.jwt_algorithm` (default `"HS256"`), `settings.access_token_expire_minutes` (default `43200`), `settings.frontend_url` (default `"http://localhost:5173"`), `settings.cookie_secure` (default `False`). `authlib`, `pyjwt`, `itsdangerous` importable.

- [ ] **Step 1: Add dependencies**

Run: `cd backend && uv add authlib pyjwt itsdangerous`
Expected: `pyproject.toml` gains the three packages under `[project.dependencies]`; `uv.lock` updates.

- [ ] **Step 2: Update `backend/app/core/config.py`**

Replace the file with:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    cors_origins: str = "http://localhost:5173"
    env: str = "development"

    google_client_id: str
    google_client_secret: str
    secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 43200
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = False

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()  # type: ignore[call-arg]
```

- [ ] **Step 3: Add placeholder values to `backend/.env`**

`google_client_id`/`google_client_secret`/`secret_key` are required with no default, so `Settings()` — and therefore every backend command — fails until `.env` has values. Real Google credentials don't exist yet (Task 8 documents getting them), so append working placeholders for local dev/test now:

```bash
cd backend
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Append to `backend/.env` (keep the existing `DATABASE_URL`/`CORS_ORIGINS`/`ENV` lines as they are):

```dotenv
GOOGLE_CLIENT_ID=dummy-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=dummy-client-secret
SECRET_KEY=<paste the generated value here>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=43200
FRONTEND_URL=http://localhost:5173
COOKIE_SECURE=false
```

These placeholders are enough for the app to start and for every automated test to pass (no test hits real Google endpoints — see Global Constraints). They are NOT enough to complete a real Google login — that needs real credentials (Task 8).

- [ ] **Step 4: Document the new variables in `.env.example`**

Append to the repo-root `.env.example`:

```dotenv
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
SECRET_KEY=generate-with-python3-c-import-secrets-print-secrets-token-urlsafe-32
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=43200
FRONTEND_URL=http://localhost:5173
COOKIE_SECURE=false
```

- [ ] **Step 5: Verify the app still boots and the existing suite still passes**

Run: `cd backend && uv run pytest -q`
Expected: `29 passed` (unchanged from Phase 1 — nothing behavioral changed yet).
Run: `cd backend && uv run python -c "from app.core.config import settings; print(settings.google_client_id)"`
Expected: prints `dummy-client-id.apps.googleusercontent.com` with no error.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/core/config.py .env.example
git commit -m "feat(backend): add auth config settings and OAuth/JWT dependencies

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

(`backend/.env` is gitignored — nothing to add there.)

---

## Task 2: Fix the Alembic test-fixture debt

**Files:**
- Modify: `backend/alembic/env.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Consumes: `app.core.config.settings`, existing migration `0001`.
- Produces: the `engine` test fixture now builds its schema by running real Alembic migrations (`alembic upgrade head`) against a database URL *derived from* `settings.database_url`, instead of `Base.metadata.create_all()` against a hardcoded URL. All other fixtures (`db_session`, `client`) and all existing tests are unchanged in behavior.

- [ ] **Step 1: Edit `backend/alembic/env.py` — let a pre-set URL win**

Find:
```python
config.set_main_option("sqlalchemy.url", settings.database_url)
```
Replace with:
```python
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)
```
This preserves the current CLI behavior (`uv run alembic upgrade head` still targets `settings.database_url` when nothing else set it) while letting test code pre-set a different URL before invoking Alembic programmatically.

- [ ] **Step 2: Replace `backend/tests/conftest.py`**

```python
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.main import app

# Models must be imported so their tables are registered on Base.metadata.
from app.applications import models  # noqa: F401

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

_dev_url = make_url(settings.database_url)
TEST_DB_NAME = f"{_dev_url.database}_test"
TEST_DATABASE_URL = str(_dev_url.set(database=TEST_DB_NAME))
ADMIN_URL = str(_dev_url.set(database="postgres", drivername="postgresql"))


def _ensure_test_database() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        ).fetchone()
        if row is None:
            conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def engine():
    _ensure_test_database()
    eng = create_engine(TEST_DATABASE_URL, future=True)
    # Guarantee a clean slate regardless of what a previous run left behind,
    # then run the REAL migrations (not Base.metadata.create_all) so
    # migration/model drift is caught by the test suite, not just by eye.
    Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    command.upgrade(_alembic_config(TEST_DATABASE_URL), "head")
    yield eng
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
def client(db_session) -> Iterator[TestClient]:
    from app.db.session import get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
```

This is behaviorally the Phase 1 file with three changes: (1) `TEST_DATABASE_URL`/`ADMIN_URL` derived from `settings.database_url` via `make_url(...).set(...)` instead of hardcoded literals, (2) `engine` fixture drops everything and runs `alembic upgrade head` instead of `Base.metadata.create_all()`, (3) explicit `Iterator[TestClient]` return type on `client` (was a lying non-generator annotation before).

- [ ] **Step 3: Run the full suite to confirm nothing regressed**

Run: `cd backend && uv run pytest -v`
Expected: all 29 tests pass, same as before this task — this task changes *how* the schema is built, not what's tested. If anything fails, the fixture rewrite has a bug — do not proceed until this is green.

- [ ] **Step 4: Verify the migration path actually runs (not skipped)**

Run: `cd backend && uv run pytest -v 2>&1 | grep -i alembic` — expect no errors printed (Alembic logs go to Python logging, not necessarily captured by pytest's default output; if nothing prints, that's fine). Instead confirm behaviorally: temporarily rename `backend/alembic/versions/0001_create_applications.py` to `.bak`, run `uv run pytest -q`, confirm it now FAILS (no `applications` table exists — proves the fixture genuinely depends on the migration file, not on some cached schema). Rename it back, run `uv run pytest -q` again, confirm `29 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/env.py backend/tests/conftest.py
git commit -m "fix(backend): run real Alembic migrations in the test fixture

The test suite previously built its schema with Base.metadata.create_all(),
which cannot detect migration/model drift (migration 0001 already had one:
a server_default on 'status' the ORM model never declared). The test
database URL was also hardcoded instead of derived from settings.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: `User` model + `users` table

**Files:**
- Create: `backend/app/users/__init__.py`
- Create: `backend/app/users/models.py`
- Create: `backend/app/users/schemas.py`
- Modify: `backend/alembic/env.py` (register the new model)
- Modify: `backend/tests/conftest.py` (register the new model for `Base.metadata.drop_all()`)
- Create: `backend/alembic/versions/0002_create_users.py`

**Interfaces:**
- Consumes: `app.db.base.Base`, `app.db.base.TimestampMixin`.
- Produces:
  - `app.users.models.User` — ORM model, `__tablename__ = "users"`. Columns only in this task: `id` (UUID PK, default `uuid4`), `google_sub` (str, unique, not null), `email` (str, unique, not null), `name` (str, not null), `picture_url` (str, nullable). **No `applications` relationship yet** — see the note below.
  - `app.users.schemas.UserRead` — `id`, `email`, `name`, `picture_url`; `ConfigDict(from_attributes=True)`.
  - Migration `0002` (`down_revision = "0001"`) creating the `users` table with unique constraints on `google_sub` and `email`.
- Nothing yet references `User` from `applications` — that's Task 5. This task is purely additive and does not touch `applications`.

**Why no relationship yet:** a `User.applications = relationship(back_populates="owner", ...)` here would reference `Application.owner`, which doesn't exist until Task 5. That's not just a forward-reference — the moment anything commits a mapped object (which this task's own Step 9 does, by re-running the pre-existing `test_service.py`/`test_applications_api.py`), SQLAlchemy runs `configure_mappers()` across the *entire* registry, which validates every `back_populates` target immediately — it would raise `InvalidRequestError` on `Application` having no `owner` property, breaking every Phase 1 test in this task. Task 5 adds both sides of the relationship together, atomically, once `Application.owner` exists.

- [ ] **Step 1: Create `backend/app/users/__init__.py`** (empty file)

- [ ] **Step 2: Write `backend/app/users/models.py`**

```python
import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    google_sub: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    picture_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
```

`unique=True` alone (not `unique=True, index=True`) on both columns — that combination makes SQLAlchemy emit a unique `Index` in metadata instead of a `UniqueConstraint`, which won't match the migration's `create_unique_constraint` below and shows up as spurious drift on every future `alembic revision --autogenerate`. A unique constraint already creates a unique btree index in Postgres under the hood, so the separate `index=True` bought nothing anyway.

Task 5 adds the `applications` relationship here (and `Application.owner` on the other side) in the same commit that adds `Application.user_id` — see Task 5 Step 2a.

- [ ] **Step 3: Write `backend/app/users/schemas.py`**

```python
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    picture_url: str | None
```

- [ ] **Step 4: Register `User` in `backend/alembic/env.py`**

Find:
```python
from app.applications import models  # noqa: F401  (register models on Base.metadata)
```
Replace with:
```python
from app.applications import models  # noqa: F401  (register models on Base.metadata)
from app.users import models as _user_models  # noqa: F401
```

- [ ] **Step 4a: Also register `User` in `backend/tests/conftest.py`**

`Base.metadata.drop_all(eng)` in the `engine` fixture only knows about tables whose model module has actually been imported into the process. `conftest.py` currently only imports `app.applications.models` — leaving `users` un-registered means a *second* consecutive `pytest` run in the same environment will crash with `DuplicateTable: users` (the first run's `users` table survives `drop_all`, then the migration tries to create it again). Any task that adds a mapped model must register it here too, not just in `alembic/env.py`.

Find:
```python
# Models must be imported so their tables are registered on Base.metadata.
from app.applications import models  # noqa: F401
```
Replace with:
```python
# Models must be imported so their tables are registered on Base.metadata.
from app.applications import models  # noqa: F401
from app.users import models as _user_models  # noqa: F401
```

- [ ] **Step 5: Write `backend/alembic/versions/0002_create_users.py`**

```python
"""create users table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("google_sub", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("picture_url", sa.String(length=1024), nullable=True),
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
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])
    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_table("users")
```

- [ ] **Step 6: Apply and verify the migration against the dev database**

```bash
cd backend
uv run alembic upgrade head
psql "postgresql://jobtracker:jobtracker@localhost:5432/jobtracker" -c "\d users"
```
Expected: `users` table with the 6 columns; unique constraints on `google_sub` and `email` (shown as indexes in `\d` output).

- [ ] **Step 7: Verify the migration reverses cleanly**

```bash
uv run alembic downgrade base && uv run alembic upgrade head
```
Expected: both succeed with no error (this also re-verifies `0001` still applies cleanly).

- [ ] **Step 8: Verify autogenerate sees no drift**

```bash
uv run alembic revision --autogenerate -m "drift check"
```
Expected: the generated file's `upgrade()`/`downgrade()` are empty (`pass`). Delete it: `rm backend/alembic/versions/*drift_check*.py`.

- [ ] **Step 9: Run the backend suite TWICE in a row**

```bash
cd backend
uv run pytest -q
uv run pytest -q
```
Expected: `29 passed` both times. Running it twice specifically catches the `DuplicateTable` failure mode Step 4a exists to prevent — a single run alone would pass even without Step 4a's fix (the first run always starts from whatever state the DB happens to be in). This task doesn't change any test assertion or touch `applications`, so the count itself shouldn't move — only the schema-building mechanics underneath it just grew to include `users`.

- [ ] **Step 10: Commit**

```bash
git add backend/app/users backend/alembic/env.py backend/alembic/versions/0002_create_users.py backend/tests/conftest.py
git commit -m "feat(backend): add User model and users table migration

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `app/auth/` package — OAuth client, JWT, `get_current_user`, `/auth/*` routes

**Files:**
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/auth/oauth.py`
- Create: `backend/app/auth/jwt.py`
- Create: `backend/app/auth/dependencies.py`
- Create: `backend/app/auth/router.py`
- Modify: `backend/app/main.py` (mount `SessionMiddleware` + auth router)
- Modify: `backend/tests/conftest.py` (add `user` and `auth_client` fixtures)
- Create: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `app.users.models.User`, `app.core.config.settings`, `app.db.session.get_db`.
- Produces:
  - `app.auth.oauth.oauth` — Authlib `OAuth()` instance with a `"google"` client registered.
  - `app.auth.jwt.create_access_token(user_id: UUID) -> str` and `app.auth.jwt.decode_access_token(token: str) -> UUID` (raises `jwt.PyJWTError` subclasses on invalid/expired tokens).
  - `app.auth.dependencies.COOKIE_NAME = "access_token"` and `app.auth.dependencies.get_current_user(access_token: str | None = Cookie(...), db: Session = Depends(get_db)) -> User` — raises `HTTPException(401)` if missing/invalid/expired/user-not-found.
  - `app.auth.router.router` — `GET /google/login`, `GET /google/callback` (route name `"google_callback"`), `GET /me` (→ `UserRead`), `POST /logout` (→ `204`).
  - `app.main`: `SessionMiddleware` added (secret from `settings.secret_key`, used only for the OAuth handshake's transient state/nonce); auth router mounted at `/api/v1`.
  - Test fixtures: `user` (a `User` row), `auth_client` (a `TestClient` with a valid `access_token` cookie for `user`, sharing the test's `db_session`).

- [ ] **Step 1: Create `backend/app/auth/__init__.py`** (empty file)

- [ ] **Step 2: Write `backend/app/auth/oauth.py`**

```python
from authlib.integrations.starlette_client import OAuth

from app.core.config import settings

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
```

- [ ] **Step 3: Write `backend/app/auth/jwt.py`**

```python
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.core.config import settings


def create_access_token(user_id: UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> UUID:
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    return UUID(payload["sub"])
```

- [ ] **Step 4: Write `backend/app/auth/dependencies.py`**

```python
import jwt
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.jwt import decode_access_token
from app.db.session import get_db
from app.users.models import User

COOKIE_NAME = "access_token"


def get_current_user(
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    try:
        user_id = decode_access_token(access_token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session"
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return user
```

- [ ] **Step 5: Write `backend/app/auth/router.py`**

```python
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import COOKIE_NAME, get_current_user
from app.auth.jwt import create_access_token
from app.auth.oauth import oauth
from app.core.config import settings
from app.db.session import get_db
from app.users.models import User
from app.users.schemas import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google/login")
async def google_login(request: Request):
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    claims = token["userinfo"]

    user = db.scalars(
        select(User).where(User.google_sub == claims["sub"])
    ).one_or_none()
    if user is None:
        user = User(
            google_sub=claims["sub"],
            email=claims["email"],
            name=claims.get("name") or claims["email"],
            picture_url=claims.get("picture"),
        )
        db.add(user)
    else:
        user.email = claims["email"]
        user.name = claims.get("name") or claims["email"]
        user.picture_url = claims.get("picture")
    db.commit()
    db.refresh(user)

    access_token = create_access_token(user.id)
    response = RedirectResponse(url=settings.frontend_url)
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.access_token_expire_minutes * 60,
    )
    return response


@router.get("/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(key=COOKIE_NAME, samesite="lax")
    return response
```

- [ ] **Step 6: Update `backend/app/main.py` — mount `SessionMiddleware` and the auth router**

Replace the file with:

```python
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.applications.exceptions import ApplicationNotFound
from app.applications.router import router as applications_router
from app.auth.router import router as auth_router
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
    # Used only for the few seconds of the OAuth state/nonce handshake —
    # entirely separate from the app's own access_token cookie.
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

    @app.exception_handler(ApplicationNotFound)
    async def handle_application_not_found(
        request: Request, exc: ApplicationNotFound
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(applications_router, prefix="/api/v1")

    return app


app = create_app()
```

- [ ] **Step 7: Update `backend/tests/conftest.py` — add `user` and `auth_client` fixtures**

Replace the whole file with (this is Task 2's file plus: three new imports, and the new helpers/fixtures appended at the end):

```python
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth.dependencies import COOKIE_NAME
from app.auth.jwt import create_access_token
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.users.models import User

# Models must be imported so their tables are registered on Base.metadata.
from app.applications import models  # noqa: F401

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

_dev_url = make_url(settings.database_url)
TEST_DB_NAME = f"{_dev_url.database}_test"
TEST_DATABASE_URL = str(_dev_url.set(database=TEST_DB_NAME))
ADMIN_URL = str(_dev_url.set(database="postgres", drivername="postgresql"))


def _ensure_test_database() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        ).fetchone()
        if row is None:
            conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def engine():
    _ensure_test_database()
    eng = create_engine(TEST_DATABASE_URL, future=True)
    Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    command.upgrade(_alembic_config(TEST_DATABASE_URL), "head")
    yield eng
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
def client(db_session) -> Iterator[TestClient]:
    from app.db.session import get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _make_user(db_session: Session, *, google_sub: str, email: str, name: str) -> User:
    user = User(google_sub=google_sub, email=email, name=name)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _authenticated_client(db_session: Session, user: User) -> Iterator[TestClient]:
    from app.db.session import get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    test_client.cookies.set(COOKIE_NAME, create_access_token(user.id))
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def user(db_session: Session) -> User:
    return _make_user(
        db_session, google_sub="google-sub-1", email="alice@example.com", name="Alice"
    )


@pytest.fixture
def auth_client(db_session: Session, user: User) -> Iterator[TestClient]:
    yield from _authenticated_client(db_session, user)
```

(The three new top-level imports — `app.auth.dependencies`, `app.auth.jwt`, `app.users.models` — are safe at the top: by Step 6 of this task, `app.main` already imports `app.auth.router`, which already imports both `app.auth.dependencies` and (transitively) `app.users.models`, so nothing here is circular; it's just normal, already-cached re-importing.)

- [ ] **Step 8: Write the failing tests — `backend/tests/test_auth.py`**

```python
from fastapi.testclient import TestClient

from app.auth.dependencies import COOKIE_NAME


def test_me_without_cookie_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_with_garbage_cookie_returns_401(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, "not-a-real-jwt")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_with_valid_cookie_returns_user(auth_client: TestClient, user) -> None:
    response = auth_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user.id)
    assert body["email"] == user.email
    assert body["name"] == user.name


def test_logout_clears_session(auth_client: TestClient) -> None:
    assert auth_client.get("/api/v1/auth/me").status_code == 200
    logout_response = auth_client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204
    set_cookie_header = logout_response.headers.get("set-cookie", "")
    assert COOKIE_NAME in set_cookie_header
    assert "max-age=0" in set_cookie_header.lower()


def test_applications_endpoint_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/applications")
    assert response.status_code == 401
```

Note: `test_applications_endpoint_requires_authentication` will currently FAIL until Task 5 adds `Depends(get_current_user)` to the applications router — expected. Run it now to confirm that specific, expected failure (not a different error).

Note on `test_logout_clears_session`: it asserts on the logout response's `Set-Cookie` header directly rather than making a second request on the same client to check it's now unauthenticated. That second-request approach is tempting but flaky under httpx's `TestClient` — a cookie set via `client.cookies.set(...)` (domain `""`) and a cookie cleared via the server's `Set-Cookie` response can resolve to different effective domains in httpx's cookie jar (an internal quirk of matching against the fake `testserver` host), so the manually-injected cookie may not actually get cleared in the jar even though a real browser would clear it correctly. Asserting on the header is both more robust (no httpx-version-dependent behavior) and more direct (it tests the actual contract the endpoint promises).

- [ ] **Step 9: Run the new tests**

Run: `cd backend && uv run pytest tests/test_auth.py -v`
Expected: `test_me_without_cookie_returns_401`, `test_me_with_garbage_cookie_returns_401`, `test_me_with_valid_cookie_returns_user`, `test_logout_clears_session` all PASS. `test_applications_endpoint_requires_authentication` FAILS with `assert 200 == 401` (the applications router doesn't require auth yet) — this is the expected, documented gap Task 5 closes. Capture this output.

- [ ] **Step 10: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: `33 passed, 1 failed` (29 Phase-1 + 4 new auth passes; the 1 failure is the documented Task-5 dependency above). Confirm the failure is exactly `test_applications_endpoint_requires_authentication` and nothing else broke.

- [ ] **Step 11: Manual sanity check that the OAuth client registers without a network call**

Run: `cd backend && uv run python -c "from app.auth.oauth import oauth; print(oauth.google.name)"`
Expected: prints `google` with no error (proves registration itself doesn't require network access — Authlib defers the OIDC discovery fetch to first actual use).

- [ ] **Step 12: Commit**

```bash
git add backend/app/auth backend/app/main.py backend/tests/conftest.py backend/tests/test_auth.py
git commit -m "feat(backend): Google OAuth login, JWT sessions, get_current_user

Adds /api/v1/auth/{google/login,google/callback,me,logout}. The
applications router does not yet require authentication — that is
Task 5 — so test_applications_endpoint_requires_authentication is
expected to fail until then.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Scope applications to the authenticated user

**Files:**
- Create: `backend/alembic/versions/0003_add_user_id_to_applications.py`
- Modify: `backend/app/applications/models.py`
- Modify: `backend/app/users/models.py` (add the `applications` relationship — the other half of Task 3's deferred back-reference)
- Modify: `backend/app/applications/service.py`
- Modify: `backend/app/applications/router.py`
- Modify: `backend/tests/conftest.py` (add `other_user`, `other_auth_client`)
- Modify: `backend/tests/test_service.py`
- Modify: `backend/tests/test_applications_api.py`

**Interfaces:**
- Consumes: `app.users.models.User`, `app.auth.dependencies.get_current_user`.
- Produces:
  - `Application.user_id: Mapped[uuid.UUID]` (FK → `users.id`, `ondelete="CASCADE"`, not null, indexed); `Application.owner: Mapped["User"]` relationship.
  - Every `app.applications.service` function signature gains `user_id: UUID` as its second positional parameter (after `db`): `list_applications(db, user_id)`, `get_application(db, user_id, application_id)`, `create_application(db, user_id, data)`, `update_application(db, user_id, application_id, data)`, `delete_application(db, user_id, application_id)`. All queries filter by `user_id` directly (no separate ownership check).
  - Every `app.applications.router` route gains `current_user: User = Depends(get_current_user)` and passes `current_user.id` into the matching service call.
  - Test fixtures `other_user` / `other_auth_client` (mirrors `user` / `auth_client` for a second identity).

This is one task, not several, because the migration, model, service, router, and their tests must land together — splitting them would leave a commit where `user_id` is `NOT NULL` in the database but nothing in the code supplies it yet.

- [ ] **Step 1: Write `backend/alembic/versions/0003_add_user_id_to_applications.py`**

```python
"""add user_id to applications

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Local dev/test data only — no production users exist to preserve.
    op.execute("TRUNCATE TABLE applications")

    op.add_column(
        "applications",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.alter_column("applications", "user_id", nullable=False)
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_foreign_key(
        "fk_applications_user_id_users",
        "applications",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_applications_user_id_users", "applications", type_="foreignkey")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_column("applications", "user_id")
```

- [ ] **Step 2: Update `backend/app/applications/models.py`**

Replace the file with:

```python
import enum
import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.users.models import User


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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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

    owner: Mapped["User"] = relationship(back_populates="applications")
```

- [ ] **Step 2a: Update `backend/app/users/models.py` — add the other half of the relationship**

Replace the file with:

```python
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.applications.models import Application


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    google_sub: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    picture_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    applications: Mapped[list["Application"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
```

Both sides of the relationship (`Application.owner` from Step 2, `User.applications` here) land in this same task, so mapper configuration never sees one side without the other — this is exactly the ordering problem Task 3 flagged and deferred.

- [ ] **Step 3: Apply and verify the migration against the dev database**

```bash
cd backend
uv run alembic upgrade head
psql "postgresql://jobtracker:jobtracker@localhost:5432/jobtracker" -c "\d applications"
```
Expected: `applications` now has `user_id` (not null, FK to `users.id`, indexed); it's empty (the `TRUNCATE` ran — this is the deliberate data-wipe from the design).

- [ ] **Step 4: Verify the migration reverses cleanly**

```bash
uv run alembic downgrade base && uv run alembic upgrade head
```
Expected: both succeed (full chain `0001→0002→0003` down and back up).

- [ ] **Step 5: Verify autogenerate sees no drift**

```bash
uv run alembic revision --autogenerate -m "drift check"
```
Expected: empty `upgrade()`/`downgrade()`. Delete the generated file.

- [ ] **Step 6: Add `other_user` / `other_auth_client` fixtures to `backend/tests/conftest.py`**

Append after the existing `auth_client` fixture:

```python


@pytest.fixture
def other_user(db_session: Session) -> User:
    return _make_user(
        db_session, google_sub="google-sub-2", email="bob@example.com", name="Bob"
    )


@pytest.fixture
def other_auth_client(db_session: Session, other_user: User) -> Iterator[TestClient]:
    yield from _authenticated_client(db_session, other_user)
```

- [ ] **Step 7: Update `backend/tests/test_service.py` — expect the new signatures (RED)**

Replace the file with:

```python
import uuid

import pytest

from app.applications import service
from app.applications.exceptions import ApplicationNotFound
from app.applications.models import ApplicationStatus
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def test_create_persists_with_defaults(db_session, user) -> None:
    created = service.create_application(
        db_session, user.id, ApplicationCreate(company="Acme", position="SWE Intern")
    )
    assert created.id is not None
    assert created.user_id == user.id
    assert created.status is ApplicationStatus.applied
    assert created.created_at is not None
    assert created.updated_at is not None


def test_get_missing_raises(db_session, user) -> None:
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, user.id, uuid.uuid4())


def test_list_returns_newest_first(db_session, user) -> None:
    first = service.create_application(
        db_session, user.id, ApplicationCreate(company="A", position="P1")
    )
    second = service.create_application(
        db_session, user.id, ApplicationCreate(company="B", position="P2")
    )
    listed = service.list_applications(db_session, user.id)
    assert [row.id for row in listed] == [second.id, first.id]


def test_update_applies_only_provided_fields(db_session, user) -> None:
    created = service.create_application(
        db_session, user.id, ApplicationCreate(company="A", position="P")
    )
    updated = service.update_application(
        db_session, user.id, created.id, ApplicationUpdate(status="interview")
    )
    assert updated.status is ApplicationStatus.interview
    assert updated.company == "A"
    assert updated.position == "P"


def test_update_missing_raises(db_session, user) -> None:
    with pytest.raises(ApplicationNotFound):
        service.update_application(
            db_session, user.id, uuid.uuid4(), ApplicationUpdate(status="offer")
        )


def test_delete_removes_row(db_session, user) -> None:
    created = service.create_application(
        db_session, user.id, ApplicationCreate(company="A", position="P")
    )
    service.delete_application(db_session, user.id, created.id)
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, user.id, created.id)


def test_delete_missing_raises(db_session, user) -> None:
    with pytest.raises(ApplicationNotFound):
        service.delete_application(db_session, user.id, uuid.uuid4())


def test_list_excludes_other_users_applications(db_session, user, other_user) -> None:
    service.create_application(
        db_session, user.id, ApplicationCreate(company="Mine", position="P")
    )
    service.create_application(
        db_session, other_user.id, ApplicationCreate(company="Theirs", position="P")
    )
    listed = service.list_applications(db_session, user.id)
    assert [row.company for row in listed] == ["Mine"]


def test_get_other_users_application_raises(db_session, user, other_user) -> None:
    theirs = service.create_application(
        db_session, other_user.id, ApplicationCreate(company="Theirs", position="P")
    )
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, user.id, theirs.id)
```

- [ ] **Step 8: Run to verify RED**

Run: `cd backend && uv run pytest tests/test_service.py -v`
Expected: FAIL — `TypeError` on argument count/order (the current `service.py` doesn't accept `user_id`).

- [ ] **Step 9: Update `backend/app/applications/service.py`**

Replace the file with:

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.exceptions import ApplicationNotFound
from app.applications.models import Application
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def list_applications(db: Session, user_id: UUID) -> list[Application]:
    statement = (
        select(Application)
        .where(Application.user_id == user_id)
        .order_by(Application.created_at.desc())
    )
    return list(db.scalars(statement))


def get_application(db: Session, user_id: UUID, application_id: UUID) -> Application:
    statement = select(Application).where(
        Application.id == application_id, Application.user_id == user_id
    )
    application = db.scalars(statement).one_or_none()
    if application is None:
        raise ApplicationNotFound(application_id)
    return application


def create_application(
    db: Session, user_id: UUID, data: ApplicationCreate
) -> Application:
    application = Application(user_id=user_id, **data.model_dump())
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def update_application(
    db: Session, user_id: UUID, application_id: UUID, data: ApplicationUpdate
) -> Application:
    application = get_application(db, user_id, application_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(application, field, value)
    db.commit()
    db.refresh(application)
    return application


def delete_application(db: Session, user_id: UUID, application_id: UUID) -> None:
    application = get_application(db, user_id, application_id)
    db.delete(application)
    db.commit()
```

Note: `get_application` changed from `db.get(Application, application_id)` (primary-key-only lookup — can't add a second WHERE condition) to an explicit `select(...).where(id == ..., user_id == ...)`. This is the "single indexed query, no separate ownership check" mechanism from the design.

- [ ] **Step 10: Run to verify GREEN**

Run: `cd backend && uv run pytest tests/test_service.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 11: Update `backend/tests/test_applications_api.py` — expect auth (RED)**

Replace the file with:

```python
from fastapi.testclient import TestClient

BASE = "/api/v1/applications"
MISSING_ID = "00000000-0000-0000-0000-000000000000"


def test_create_returns_201_with_body(auth_client: TestClient) -> None:
    response = auth_client.post(BASE, json={"company": "Acme", "position": "SWE Intern"})
    assert response.status_code == 201
    body = response.json()
    assert body["company"] == "Acme"
    assert body["position"] == "SWE Intern"
    assert body["status"] == "applied"
    assert body["applied_at"] is None
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]
    assert "user_id" not in body


def test_create_accepts_status_and_applied_at(auth_client: TestClient) -> None:
    response = auth_client.post(
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


def test_create_rejects_blank_company_with_422(auth_client: TestClient) -> None:
    response = auth_client.post(BASE, json={"company": "   ", "position": "SWE"})
    assert response.status_code == 422


def test_list_returns_newest_first(auth_client: TestClient) -> None:
    auth_client.post(BASE, json={"company": "A", "position": "P1"})
    auth_client.post(BASE, json={"company": "B", "position": "P2"})
    response = auth_client.get(BASE)
    assert response.status_code == 200
    assert [row["company"] for row in response.json()] == ["B", "A"]


def test_patch_updates_only_status(auth_client: TestClient) -> None:
    created = auth_client.post(BASE, json={"company": "A", "position": "P"}).json()
    response = auth_client.patch(f"{BASE}/{created['id']}", json={"status": "offer"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    assert body["company"] == "A"
    assert body["updated_at"] > created["updated_at"]


def test_patch_empty_body_returns_422(auth_client: TestClient) -> None:
    created = auth_client.post(BASE, json={"company": "A", "position": "P"}).json()
    response = auth_client.patch(f"{BASE}/{created['id']}", json={})
    assert response.status_code == 422


def test_patch_missing_returns_404(auth_client: TestClient) -> None:
    response = auth_client.patch(f"{BASE}/{MISSING_ID}", json={"status": "offer"})
    assert response.status_code == 404
    assert MISSING_ID in response.json()["detail"]


def test_patch_clears_applied_at_with_null(auth_client: TestClient) -> None:
    created = auth_client.post(
        BASE, json={"company": "A", "position": "P", "applied_at": "2026-09-01"}
    ).json()
    assert created["applied_at"] == "2026-09-01"
    response = auth_client.patch(f"{BASE}/{created['id']}", json={"applied_at": None})
    assert response.status_code == 200
    assert response.json()["applied_at"] is None


def test_patch_omitting_applied_at_leaves_it_unchanged(auth_client: TestClient) -> None:
    created = auth_client.post(
        BASE, json={"company": "A", "position": "P", "applied_at": "2026-09-01"}
    ).json()
    response = auth_client.patch(f"{BASE}/{created['id']}", json={"status": "offer"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    assert body["applied_at"] == "2026-09-01"


def test_delete_returns_204_then_get_404(auth_client: TestClient) -> None:
    created = auth_client.post(BASE, json={"company": "A", "position": "P"}).json()
    assert auth_client.delete(f"{BASE}/{created['id']}").status_code == 204
    assert auth_client.get(f"{BASE}/{created['id']}").status_code == 404


def test_delete_missing_returns_404(auth_client: TestClient) -> None:
    assert auth_client.delete(f"{BASE}/{MISSING_ID}").status_code == 404


def test_get_missing_returns_404(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/{MISSING_ID}")
    assert response.status_code == 404


def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    assert client.get(BASE).status_code == 401
    assert client.post(BASE, json={"company": "A", "position": "P"}).status_code == 401


def test_list_only_returns_own_applications(
    auth_client: TestClient, other_auth_client: TestClient
) -> None:
    auth_client.post(BASE, json={"company": "Mine", "position": "P"})
    other_auth_client.post(BASE, json={"company": "Theirs", "position": "P"})
    response = auth_client.get(BASE)
    assert [row["company"] for row in response.json()] == ["Mine"]


def test_get_other_users_application_returns_404(
    auth_client: TestClient, other_auth_client: TestClient
) -> None:
    theirs = other_auth_client.post(BASE, json={"company": "Theirs", "position": "P"}).json()
    response = auth_client.get(f"{BASE}/{theirs['id']}")
    assert response.status_code == 404


def test_patch_other_users_application_returns_404(
    auth_client: TestClient, other_auth_client: TestClient
) -> None:
    theirs = other_auth_client.post(BASE, json={"company": "Theirs", "position": "P"}).json()
    response = auth_client.patch(f"{BASE}/{theirs['id']}", json={"status": "offer"})
    assert response.status_code == 404


def test_delete_other_users_application_returns_404(
    auth_client: TestClient, other_auth_client: TestClient
) -> None:
    theirs = other_auth_client.post(BASE, json={"company": "Theirs", "position": "P"}).json()
    response = auth_client.delete(f"{BASE}/{theirs['id']}")
    assert response.status_code == 404
    assert other_auth_client.get(f"{BASE}/{theirs['id']}").status_code == 200
```

- [ ] **Step 12: Run to verify RED**

Run: `cd backend && uv run pytest tests/test_applications_api.py -v`
Expected: most tests FAIL with `401` instead of the expected success code (routes don't require/use auth yet), except `test_unauthenticated_requests_are_rejected`, which ALSO fails today (currently every request succeeds without auth — there's nothing enforcing the 401 yet). Confirm every failure is auth-shaped, not something else.

- [ ] **Step 13: Update `backend/app/applications/router.py`**

Replace the file with:

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
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.users.models import User

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("", response_model=list[ApplicationRead])
def list_applications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list:
    return service.list_applications(db, current_user.id)


@router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.create_application(db, current_user.id, payload)


@router.get("/{application_id}", response_model=ApplicationRead)
def get_application(
    application_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_application(db, current_user.id, application_id)


@router.patch("/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.update_application(db, current_user.id, application_id, payload)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(
    application_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    service.delete_application(db, current_user.id, application_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 14: Run to verify GREEN**

Run: `cd backend && uv run pytest tests/test_applications_api.py -v`
Expected: all 17 tests PASS.

- [ ] **Step 15: Run the entire backend suite**

Run: `cd backend && uv run pytest -v`
Expected: every test passes — `test_health` (1) + `test_schemas` (9) + `test_service` (9) + `test_auth` (5, including the one that was failing in Task 4) + `test_applications_api` (17) = **41 passed**, output pristine.

- [ ] **Step 16: Manual smoke test against the real dev DB**

```bash
cd backend && uv run uvicorn app.main:app --port 8000 &
sleep 2
# no cookie — must be rejected
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/applications
kill %1
```
Expected: prints `401`.

- [ ] **Step 17: Commit**

```bash
git add backend/alembic/versions/0003_add_user_id_to_applications.py backend/app/applications backend/app/users/models.py backend/tests
git commit -m "feat(backend): scope application CRUD to the authenticated user

Every applications endpoint now requires authentication and only
operates on the current user's own rows. Cross-user access returns
404, matching the design's no-enumeration rule.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Frontend auth plumbing — types, API client, AuthContext

**Files:**
- Create: `frontend/src/types/user.ts`
- Create: `frontend/src/api/http.ts`
- Modify: `frontend/src/api/applications.ts` (use the shared helper)
- Create: `frontend/src/api/auth.ts`
- Create: `frontend/src/context/AuthContext.tsx`

**Interfaces:**
- Consumes: backend `/api/v1/auth/{me,logout}` (cookie rides along automatically — same-origin via the Vite proxy, no `credentials` option needed).
- Produces:
  - `types/user.ts`: `User { id, email, name, picture_url: string | null }`.
  - `api/http.ts`: `parseResponse<T>(response: Response): Promise<T>` — the exact logic currently duplicated nowhere else yet (it lives only in `api/applications.ts` today); extracting it now avoids a second near-identical copy in `api/auth.ts`.
  - `api/auth.ts`: `getCurrentUser(): Promise<User>`, `logout(): Promise<void>`, `GOOGLE_LOGIN_URL` constant (`"/api/v1/auth/google/login"`).
  - `context/AuthContext.tsx`: `AuthProvider` component, `useAuth()` hook returning `{ user: User | null, loading: boolean, error: string | null, refetch, logout }`.

- [ ] **Step 1: Write `frontend/src/types/user.ts`**

```typescript
export interface User {
  id: string
  email: string
  name: string
  picture_url: string | null
}
```

- [ ] **Step 2: Write `frontend/src/api/http.ts`**

```typescript
export async function parseResponse<T>(response: Response): Promise<T> {
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
```

- [ ] **Step 3: Update `frontend/src/api/applications.ts` to use the shared helper**

Replace the file with:

```typescript
import { parseResponse } from './http'
import type {
  Application,
  ApplicationCreate,
  ApplicationUpdate,
} from '../types/application'

const BASE = '/api/v1/applications'
const JSON_HEADERS = { 'Content-Type': 'application/json' }

export function listApplications(): Promise<Application[]> {
  return fetch(BASE).then((r) => parseResponse<Application[]>(r))
}

export function createApplication(
  data: ApplicationCreate,
): Promise<Application> {
  return fetch(BASE, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  }).then((r) => parseResponse<Application>(r))
}

export function updateApplication(
  id: string,
  data: ApplicationUpdate,
): Promise<Application> {
  return fetch(`${BASE}/${id}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  }).then((r) => parseResponse<Application>(r))
}

export function deleteApplication(id: string): Promise<void> {
  return fetch(`${BASE}/${id}`, { method: 'DELETE' }).then((r) =>
    parseResponse<void>(r),
  )
}
```

(Identical behavior to Phase 1 — only the `parse` function moved to `./http` and was renamed `parseResponse` in the import.)

- [ ] **Step 4: Write `frontend/src/api/auth.ts`**

```typescript
import { parseResponse } from './http'
import type { User } from '../types/user'

const BASE = '/api/v1/auth'

export function getCurrentUser(): Promise<User> {
  return fetch(`${BASE}/me`).then((r) => parseResponse<User>(r))
}

export function logout(): Promise<void> {
  return fetch(`${BASE}/logout`, { method: 'POST' }).then((r) =>
    parseResponse<void>(r),
  )
}

export const GOOGLE_LOGIN_URL = `${BASE}/google/login`
```

- [ ] **Step 5: Write `frontend/src/context/AuthContext.tsx`**

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

import * as authApi from '../api/auth'
import type { User } from '../types/user'

interface AuthContextValue {
  user: User | null
  loading: boolean
  error: string | null
  refetch: () => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setUser(await authApi.getCurrentUser())
    } catch {
      // A 401 here just means "not logged in" — expected on first load,
      // not a real error to surface. Any genuine failure just leaves the
      // user on the login screen, which is the safe default anyway.
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refetch()
  }, [refetch])

  const logout = useCallback(async () => {
    setError(null)
    try {
      await authApi.logout()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to log out')
    } finally {
      setUser(null)
    }
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, error, refetch, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
```

- [ ] **Step 6: Verify the build type-checks**

Run: `cd frontend && npm run build`
Expected: no TypeScript errors, `dist/` produced. (Nothing imports `AuthContext`/`api/auth.ts` yet — this only proves they compile. `api/applications.ts`'s refactor IS exercised, since `useApplications` already imports it.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/user.ts frontend/src/api/http.ts frontend/src/api/applications.ts frontend/src/api/auth.ts frontend/src/context
git commit -m "feat(frontend): auth types, API client, and AuthContext

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Frontend UI — login screen, user menu, gated dashboard

**Files:**
- Create: `frontend/src/components/LoginPage.tsx`
- Create: `frontend/src/components/UserMenu.tsx`
- Modify: `frontend/src/components/ApplicationsPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `useAuth()` (Task 6), `GOOGLE_LOGIN_URL` (Task 6), `User` type (Task 6).
- Produces:
  - `LoginPage` — no props; renders a "Sign in with Google" real `<a>` link (not a JS click handler).
  - `UserMenu` — props `{ user: User; onLogout: () => void }`; shows avatar/initial + name + a logout button.
  - `ApplicationsPage` — now takes `{ user: User; onLogout: () => void }` props and renders `<UserMenu>` in its header; CRUD body unchanged from Phase 1.
  - `App` — renders a loading state, then `<LoginPage/>` or `<ApplicationsPage/>` based on `useAuth()`.
  - `main.tsx` — wraps `<App/>` in `<AuthProvider>`.

- [ ] **Step 1: Write `frontend/src/components/LoginPage.tsx`**

```tsx
import { GOOGLE_LOGIN_URL } from '../api/auth'

export function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-8">
      <div className="w-full max-w-sm space-y-4 rounded-lg border border-gray-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-xl font-bold text-gray-900">Job Application Tracker</h1>
        <p className="text-sm text-gray-500">Sign in to track your applications.</p>
        <a
          href={GOOGLE_LOGIN_URL}
          className="inline-flex w-full items-center justify-center gap-2 rounded border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Sign in with Google
        </a>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Write `frontend/src/components/UserMenu.tsx`**

```tsx
import type { User } from '../types/user'

interface Props {
  user: User
  onLogout: () => void
}

export function UserMenu({ user, onLogout }: Props) {
  return (
    <div className="flex items-center gap-3">
      {user.picture_url ? (
        <img src={user.picture_url} alt="" className="h-8 w-8 rounded-full" />
      ) : (
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gray-200 text-xs font-medium text-gray-600">
          {user.name.slice(0, 1).toUpperCase()}
        </div>
      )}
      <span className="text-sm text-gray-700">{user.name}</span>
      <button
        onClick={onLogout}
        className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        Log out
      </button>
    </div>
  )
}
```

- [ ] **Step 3: Update `frontend/src/components/ApplicationsPage.tsx`**

Replace the file with:

```tsx
import { useState } from 'react'

import { useApplications } from '../hooks/useApplications'
import type { Application } from '../types/application'
import type { User } from '../types/user'
import { ApplicationForm } from './ApplicationForm'
import { ApplicationList } from './ApplicationList'
import { UserMenu } from './UserMenu'

interface Props {
  user: User
  onLogout: () => void
}

export function ApplicationsPage({ user, onLogout }: Props) {
  const { applications, loading, error, create, update, remove } = useApplications()
  const [editing, setEditing] = useState<Application | null>(null)

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 sm:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Job Application Tracker</h1>
          <p className="text-sm text-gray-500">
            {applications.length} application{applications.length === 1 ? '' : 's'}
          </p>
        </div>
        <UserMenu user={user} onLogout={onLogout} />
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

(Only the header and the new `Props`/imports changed from Phase 1 — the form/list/error/loading body is untouched.)

- [ ] **Step 4: Update `frontend/src/App.tsx`**

```tsx
import { ApplicationsPage } from './components/ApplicationsPage'
import { LoginPage } from './components/LoginPage'
import { useAuth } from './context/AuthContext'

export default function App() {
  const { user, loading, logout } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <p className="text-sm text-gray-500">Loading…</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {user ? <ApplicationsPage user={user} onLogout={logout} /> : <LoginPage />}
    </div>
  )
}
```

- [ ] **Step 5: Update `frontend/src/main.tsx` — wrap the app in `AuthProvider`**

Find:
```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```
Replace with:
```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './context/AuthContext'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>,
)
```

- [ ] **Step 6: Verify the build type-checks**

Run: `cd frontend && npm run build`
Expected: zero TypeScript errors, `dist/` produced.

- [ ] **Step 7: Headless smoke check — unauthenticated state renders the login shell**

With the backend NOT running (or running — doesn't matter, this only checks the static served HTML/JS bundle, not runtime behavior, since this is a client-rendered SPA):

```bash
cd frontend && npm run dev &
sleep 3
curl -s http://localhost:5173/ | grep -o '<title>[^<]*</title>'
kill %1
```
Expected: `<title>Job Application Tracker</title>` (unchanged from Phase 1 — confirms the dev server still serves the shell; the actual login-vs-dashboard branch only resolves once JS runs in a real browser, which this curl check cannot exercise — that's Task 8's manual step).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components frontend/src/App.tsx frontend/src/main.tsx
git commit -m "feat(frontend): Google sign-in screen, user menu, gated dashboard

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Google Cloud Console setup docs + README + final verification

**Files:**
- Modify: `README.md` (repo root)

**Interfaces:**
- Consumes: everything built in Tasks 1-7.
- Produces: a README section a new developer can follow to get real Google credentials and run the full authenticated app; a verified backend suite; a verified build; documented next manual step (the actual browser login) for whoever has real credentials.

- [ ] **Step 1: Run the full backend suite once more**

Run: `cd backend && uv run pytest -v`
Expected: `41 passed`, output pristine (2 pre-existing third-party deprecation warnings are fine, same as Phase 1).

- [ ] **Step 2: Run the frontend build once more**

Run: `cd frontend && npm run build`
Expected: clean, zero TypeScript errors.

- [ ] **Step 3: Headless auth-gate verification against the running stack**

```bash
cd backend && uv run alembic upgrade head && uv run uvicorn app.main:app --port 8000 &
sleep 3
cd frontend && npm run dev &
sleep 4

# Unauthenticated, through the Vite proxy — must be rejected
curl -s -o /dev/null -w "me: %{http_code}\n" http://localhost:5173/api/v1/auth/me
curl -s -o /dev/null -w "applications: %{http_code}\n" http://localhost:5173/api/v1/applications

kill %1 %2
```
Expected: both print `401`. This proves the whole chain (Vite proxy → FastAPI → `get_current_user` → 401) works end-to-end without a session — the same headless-verification spirit as Phase 1's Task 10, applied to the new auth gate. The actual login redirect through Google cannot be verified headlessly (needs real credentials + a browser) — that's the manual step below.

- [ ] **Step 4: Add the Google Cloud Console setup section to `README.md`**

Insert a new section (after "Running locally", before "Database migrations"):

```markdown
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
```

- [ ] **Step 5: Update the "Running locally" section's Prerequisites**

Find the `## Prerequisites` list and add one line:

```markdown
- A Google Cloud project with OAuth credentials — see "Google OAuth setup" below (placeholder credentials are enough to run tests, not to log in)
```

- [ ] **Step 6: Add a note to the Tests section**

Append to the existing `## Tests` section:

```markdown
Auth tests mint a valid session cookie directly (via the same JWT helper
the real login flow uses) rather than driving an actual Google OAuth
round-trip — the suite never makes a network call to a real Google
endpoint.
```

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: Google OAuth setup instructions for Phase 2

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Final verification summary**

Confirm and report:
- `cd backend && uv run pytest` → 41 passed (state the count)
- `cd frontend && npm run build` → succeeds
- Step 3's two `401` checks → both passed
- Remind the user: real Google credentials (Task 8 Step 4's instructions) are still needed before anyone can click "Sign in with Google" and actually complete a login — that final click-through is a manual, human-in-a-browser step this plan cannot automate.

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §2 Phase 1 review findings (Alembic test-fixture drift, hardcoded test URL) | Task 2 |
| §3 decisions (OAuth flow, session mechanism, identity key, routing, 404-not-403, data wipe, libraries, no Gmail scope) | encoded in Global Constraints + Tasks 1, 3, 4, 5 |
| §4 project structure | Tasks 1, 3, 4, 5, 6, 7 (file structure table mirrors it, with the one deliberate migration-split deviation noted) |
| §5 data model: `users` table, `applications.user_id`, relationship, migration | Task 3 (`users`), Task 5 (`user_id` + relationship + migration `0003`) |
| §6 Google auth flow end-to-end (all 9 steps) | Task 4 (steps 1-7, 9 logout) + Task 6/7 (step 8, frontend) |
| §7 authorization/ownership model | Task 5 (`get_current_user` consumed by router; service filters by `user_id`; 404-not-403; `user_id` never client-suppliable) |
| §8 security considerations | reflected as Global Constraints and inline code comments (SessionMiddleware separation, cookie flags, no CSRF middleware, no revocation) — no separate task needed, these are properties of how Tasks 4-5 are built, not standalone work items |
| §9 API contract (`/auth/*` endpoints, unchanged applications shape) | Task 4 (auth endpoints), Task 5 (applications endpoints keep their shape, `user_id` absent from `ApplicationRead`, asserted directly in `test_create_returns_201_with_body`) |
| §10 backend request flow | Task 5 |
| §11 frontend structure & data flow | Tasks 6, 7 |
| §12 testing plan (conftest changes, `test_auth.py`, `test_applications_api.py` changes) | Task 2 (conftest alembic fix), Task 4 (`test_auth.py`, `user`/`auth_client`), Task 5 (`other_user`/`other_auth_client`, `test_service.py`, `test_applications_api.py`) |
| §13 Google Cloud Console setup | Task 8 |
| §14 preserving Phase 1 functionality | Every Phase 1 test is carried forward (updated for auth, not deleted) across Tasks 2 and 5; same fields, same enum, same PATCH semantics, same validation |

**2. Placeholder scan:** No "TBD"/"TODO"/"handle edge cases"/"similar to Task N". Every code step contains full file contents or an exact find/replace with real code. The one explicitly-flagged manual, unautomatable step (the actual browser Google login) is called out as exactly that, in Task 8 Step 8 — not a hidden gap.

**3. Type consistency:**
- `app.auth.dependencies.get_current_user(...) -> User` — defined Task 4 Step 4; consumed identically in `app/applications/router.py` (Task 5 Step 13) and `app/auth/router.py`'s `/me` (Task 4 Step 5).
- `app.auth.jwt.create_access_token(user_id: UUID) -> str` / `decode_access_token(token: str) -> UUID` — defined Task 4 Step 3; `create_access_token` consumed in `app/auth/router.py` (Task 4 Step 5) and in `conftest.py`'s `_authenticated_client` (Task 4 Step 7); `decode_access_token` consumed only in `dependencies.py`.
- `COOKIE_NAME = "access_token"` — defined once in `app/auth/dependencies.py` (Task 4 Step 4), imported (never redefined) in `app/auth/router.py` (Task 4 Step 5) and `tests/conftest.py` (Task 4 Step 7, Task 5 test files use it only indirectly via fixtures).
- Service signatures `list_applications(db, user_id)`, `get_application(db, user_id, application_id)`, `create_application(db, user_id, data)`, `update_application(db, user_id, application_id, data)`, `delete_application(db, user_id, application_id)` — declared in Task 5's Interfaces block, defined in Step 9, consumed identically in Step 13's router and both Step 7/11 test files.
- Fixture names `user`, `other_user`, `auth_client`, `other_auth_client`, `client`, `db_session`, `engine` — each defined exactly once (Task 2 for `engine`/`db_session`/`client`; Task 4 for `user`/`auth_client`; Task 5 for `other_user`/`other_auth_client`) and consumed by name in every later test file with no renaming.
- `UserRead` fields (`id`, `email`, `name`, `picture_url`) — defined Task 3 Step 3, matches `test_me_with_valid_cookie_returns_user`'s assertions (Task 4 Step 8) and the frontend `User` type (Task 6 Step 1).
- Frontend: `useAuth()` returns `{ user, loading, error, refetch, logout }` (Task 6 Step 5), consumed in `App.tsx` (Task 7 Step 4: `user, loading, logout`). `ApplicationsPage` props `{ user, onLogout }` (Task 7 Step 3) match how `App.tsx` invokes it (Task 7 Step 4). `UserMenu` props `{ user, onLogout }` (Task 7 Step 2) match `ApplicationsPage`'s usage (Task 7 Step 3).
- `parseResponse<T>` — defined once in `api/http.ts` (Task 6 Step 2), imported by both `api/applications.ts` (Task 6 Step 3) and `api/auth.ts` (Task 6 Step 4); no second inline copy remains.
