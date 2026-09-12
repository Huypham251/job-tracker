# Phase 3: Gmail Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an authenticated Job Tracker user explicitly connect their Gmail account via a second, incremental OAuth consent, store the resulting tokens securely, retrieve Gmail messages on demand, and disconnect/revoke — without touching login, and without persisting any message content.

**Architecture:** A new `app/gmail/` backend package mirrors the existing `app/auth/`/`app/users/` split: a second Authlib client (`google_gmail`, scope `gmail.readonly`) handles the consent handshake, plain `httpx` calls handle token refresh and the two Gmail REST endpoints needed, tokens are Fernet-encrypted in a new `gmail_connections` table (one row per user), and every `/api/v1/gmail/*` route requires the existing session cookie (`get_current_user`) so connecting Gmail is always an authenticated action layered on top of login, never a substitute for it. The frontend adds a `GmailPanel` following the exact patterns `LoginPage`/`UserMenu` already established.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Authlib (OAuth handshake), `httpx` (token refresh + Gmail REST calls), `cryptography`/Fernet (token encryption at rest), React + TypeScript (frontend), pytest + `monkeypatch` (backend tests, no real network calls).

**Spec:** `docs/superpowers/specs/2026-09-11-job-tracker-phase-3-gmail-design.md`

## Global Constraints

- Gmail OAuth scope is `https://www.googleapis.com/auth/gmail.readonly` — nothing broader.
- Every `/api/v1/gmail/*` route requires `Depends(get_current_user)` — no anonymous Gmail routes, and Gmail connection is never established as a side effect of login.
- No Gmail message content (subject/body/snippet) is ever written to the database — `GET /gmail/messages` is transient: fetch from Gmail, return to the caller, persist nothing.
- The test-retrieval path fetches `format=metadata` with only `Subject`/`From`/`Date` headers — never full message bodies or attachments, even though the scope would allow it.
- `access_token` and `refresh_token` are Fernet-encrypted at rest (`GMAIL_TOKEN_ENCRYPTION_KEY`), decrypted only in-memory inside `app/gmail/service.py`, never logged, never returned in any API response.
- Disconnect always deletes our `gmail_connections` row, even if the upstream Google revoke call fails (log a warning, don't block the delete).
- Reuse the existing `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` — no new Google Cloud OAuth client.
- `GmailNotConnected` maps to `404`, matching the existing `ApplicationNotFound` → `404` convention.
- All schema changes go through Alembic (`backend/alembic/versions/`, next revision is `0004`, `down_revision = "0003"`).
- Follow existing test conventions exactly: monkeypatch Authlib/`httpx`, never call real Google endpoints from tests; reuse the `client`/`auth_client`/`other_auth_client`/`user`/`other_user`/`db_session` fixtures already in `backend/tests/conftest.py` (no conftest.py changes needed for this phase).

---

## Task 1: Dependencies, config, and token encryption helper

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/core/config.py`
- Modify: `.env.example`
- Create: `backend/app/gmail/__init__.py`
- Create: `backend/app/gmail/crypto.py`
- Test: `backend/tests/test_gmail_crypto.py`

**Interfaces:**
- Produces: `app.gmail.crypto.encrypt_token(plaintext: str) -> str`, `app.gmail.crypto.decrypt_token(ciphertext: str) -> str` — used by every later task that touches stored tokens.
- Produces: `settings.gmail_token_encryption_key: str` on `app.core.config.settings`.

- [ ] **Step 1: Add `cryptography` and `httpx` as direct dependencies**

`httpx` is currently only in the `dev` group (pulled in transitively for `TestClient`); Phase 3 uses it directly in production code (`app/gmail/google_api.py`, Task 4), so it needs to be a direct dependency. `cryptography` is already installed transitively via `authlib`, but Phase 3 imports it directly too.

Edit `backend/pyproject.toml`:

```toml
dependencies = [
    "alembic>=1.19.2",
    "authlib>=1.8.0",
    "cryptography>=43.0.0",
    "fastapi>=0.141.1",
    "httpx>=0.28.1",
    "itsdangerous>=2.2.0",
    "psycopg[binary]>=3.3.5",
    "pydantic>=2.13.5",
    "pydantic-settings>=2.15.0",
    "pyjwt>=2.13.0",
    "sqlalchemy>=2.0.52",
    "uvicorn[standard]>=0.52.4",
]

[dependency-groups]
dev = [
    "pytest>=9.1.1",
]
```

(`httpx` is removed from the `dev` group since it's now covered by the main dependency list.)

Run: `cd backend && uv sync`
Expected: lock file updates, no errors; `uv run python -c "import cryptography, httpx"` succeeds.

- [ ] **Step 2: Add the `gmail_token_encryption_key` setting**

Edit `backend/app/core/config.py`, adding the new field next to the other Phase 2 auth settings (required, no default — same pattern as `google_client_id`/`google_client_secret`/`secret_key`, so a missing key fails loudly at startup rather than silently):

```python
    google_client_id: str
    google_client_secret: str
    secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 43200
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = False
    gmail_token_encryption_key: str
```

- [ ] **Step 3: Generate a real key for local dev and add it to `backend/.env`**

`backend/.env` is gitignored and already holds your real Google OAuth credentials — this only touches your local file, not anything committed.

Run:
```bash
cd backend
KEY=$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
echo "GMAIL_TOKEN_ENCRYPTION_KEY=$KEY" >> .env
```
Expected: `backend/.env` now has a `GMAIL_TOKEN_ENCRYPTION_KEY=...` line. Without this, every test in the suite will fail at collection time (`Settings()` is built eagerly on import and now requires this field).

- [ ] **Step 4: Add the placeholder to `.env.example`**

Edit `.env.example`, appending after `COOKIE_SECURE=false`:

```
# Fernet key for encrypting stored Gmail OAuth tokens at rest. The value
# below is a syntactically valid but non-secret placeholder — generate your
# own with:
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
GMAIL_TOKEN_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
```

- [ ] **Step 5: Write the failing test for the crypto helper**

Create `backend/tests/test_gmail_crypto.py`:

```python
from app.gmail.crypto import decrypt_token, encrypt_token


def test_encrypt_then_decrypt_round_trips() -> None:
    plaintext = "ya29.fake-access-token"
    ciphertext = encrypt_token(plaintext)
    assert ciphertext != plaintext
    assert decrypt_token(ciphertext) == plaintext


def test_encrypted_output_does_not_contain_the_plaintext() -> None:
    plaintext = "1//fake-refresh-token-value"
    ciphertext = encrypt_token(plaintext)
    assert plaintext not in ciphertext
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_gmail_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.gmail'`

- [ ] **Step 7: Create the package and the crypto helper**

Create `backend/app/gmail/__init__.py` (empty file).

Create `backend/app/gmail/crypto.py`:

```python
from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    # Built per call (not cached at import time) so a bad/missing key
    # surfaces only when encryption is actually used, not at import time —
    # matches the lazy, settings-read-per-call style of app/auth/jwt.py.
    return Fernet(settings.gmail_token_encryption_key.encode())


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_gmail_crypto.py -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/core/config.py \
        backend/app/gmail/__init__.py backend/app/gmail/crypto.py \
        backend/tests/test_gmail_crypto.py .env.example
git commit -m "feat(backend): Gmail token encryption helper and config

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `GmailConnection` model and migration

**Files:**
- Create: `backend/app/gmail/models.py`
- Create: `backend/alembic/versions/0004_add_gmail_connections.py`
- Modify: `backend/app/users/models.py`
- Test: `backend/tests/test_gmail_models.py`

**Interfaces:**
- Consumes: `app.db.base.Base`, `app.db.base.TimestampMixin` (existing).
- Produces: `app.gmail.models.GmailConnection` with columns `id: UUID`, `user_id: UUID` (unique FK), `google_email: str`, `access_token_encrypted: str`, `refresh_token_encrypted: str`, `token_expiry: datetime`, `scope: str`, `created_at`/`updated_at: datetime`. Produces `User.gmail_connection: GmailConnection | None`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_gmail_models.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.gmail.models import GmailConnection
from app.users.models import User


def _make_connection(user: User) -> GmailConnection:
    return GmailConnection(
        user_id=user.id,
        google_email=user.email,
        access_token_encrypted="enc-access",
        refresh_token_encrypted="enc-refresh",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )


def test_create_and_load_gmail_connection(db_session, user) -> None:
    connection = _make_connection(user)
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)

    assert connection.id is not None
    assert connection.google_email == user.email
    assert connection.created_at is not None


def test_user_id_must_be_unique(db_session, user) -> None:
    db_session.add(_make_connection(user))
    db_session.commit()

    db_session.add(_make_connection(user))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_deleting_user_cascades_to_gmail_connection(db_session, user) -> None:
    connection = _make_connection(user)
    db_session.add(connection)
    db_session.commit()
    connection_id = connection.id

    db_session.delete(user)
    db_session.commit()

    assert db_session.get(GmailConnection, connection_id) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_gmail_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.gmail.models'`

- [ ] **Step 3: Create the model**

Create `backend/app/gmail/models.py`:

```python
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.users.models import User


class GmailConnection(TimestampMixin, Base):
    __tablename__ = "gmail_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    google_email: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_expiry: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(255), nullable=False)

    owner: Mapped["User"] = relationship(back_populates="gmail_connection")
```

- [ ] **Step 4: Add the `User` side of the relationship**

Edit `backend/app/users/models.py` — add the import and the relationship:

```python
if TYPE_CHECKING:
    from app.applications.models import Application
    from app.gmail.models import GmailConnection
```

```python
    applications: Mapped[list["Application"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    gmail_connection: Mapped["GmailConnection | None"] = relationship(
        back_populates="owner", cascade="all, delete-orphan", uselist=False
    )
```

(`uselist=False` is required — without it SQLAlchemy defaults to a list, not a scalar, for this relationship direction.)

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0004_add_gmail_connections.py`:

```python
"""add gmail_connections table

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gmail_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("google_email", sa.String(length=255), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(length=255), nullable=False),
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
    op.create_unique_constraint(
        "uq_gmail_connections_user_id", "gmail_connections", ["user_id"]
    )
    op.create_foreign_key(
        "fk_gmail_connections_user_id_users",
        "gmail_connections",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_gmail_connections_user_id_users", "gmail_connections", type_="foreignkey"
    )
    op.drop_table("gmail_connections")
```

Apply it to your local dev database: `cd backend && uv run alembic upgrade head`

- [ ] **Step 6: Import the model in `conftest.py` so Alembic/metadata registration picks it up**

Edit `backend/tests/conftest.py`, adding the model import next to the existing `applications` one:

```python
# Models must be imported so their tables are registered on Base.metadata.
from app.applications import models  # noqa: F401
from app.gmail import models as gmail_models  # noqa: F401
```

(The `engine` fixture runs real `alembic upgrade head` migrations, not `Base.metadata.create_all()`, so this import isn't strictly required for the schema itself — but SQLAlchemy's mapper configuration step still needs every mapped class imported somewhere before relationships like `User.gmail_connection` can be resolved.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_gmail_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Run the full existing suite to confirm nothing broke**

Run: `cd backend && uv run pytest -v`
Expected: all previously-passing tests still pass (the `engine` fixture drops and re-migrates the test DB from scratch each session, so this also proves migration `0004` applies cleanly on top of `0001`–`0003`).

- [ ] **Step 9: Commit**

```bash
git add backend/app/gmail/models.py backend/app/users/models.py \
        backend/alembic/versions/0004_add_gmail_connections.py \
        backend/tests/test_gmail_models.py backend/tests/conftest.py
git commit -m "feat(backend): GmailConnection model and migration

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Second Authlib client for Gmail's incremental consent

**Files:**
- Create: `backend/app/gmail/oauth.py`
- Test: `backend/tests/test_gmail_oauth.py`

**Interfaces:**
- Consumes: `app.auth.oauth.oauth` (existing `OAuth()` registry), `settings.google_client_id`, `settings.google_client_secret`.
- Produces: re-exports `oauth` from `app.gmail.oauth` with a `"google_gmail"` client registered on it (scope `gmail.readonly`, `access_type=offline`, `prompt=consent`). Later tasks import it as `from app.gmail.oauth import oauth` and use `oauth.google_gmail`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_gmail_oauth.py`:

```python
def test_google_gmail_client_is_registered_with_readonly_scope() -> None:
    from app.gmail.oauth import oauth

    client = oauth.google_gmail
    assert client.client_kwargs["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
    assert client.authorize_params == {"access_type": "offline", "prompt": "consent"}


def test_google_gmail_client_is_distinct_from_the_login_client() -> None:
    from app.auth.oauth import oauth as login_oauth
    from app.gmail.oauth import oauth as gmail_oauth

    assert login_oauth is gmail_oauth  # same registry
    assert login_oauth.google is not gmail_oauth.google_gmail  # different clients
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_gmail_oauth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.gmail.oauth'`

- [ ] **Step 3: Register the client**

Create `backend/app/gmail/oauth.py`:

```python
from app.auth.oauth import oauth
from app.core.config import settings

# Registered on the SAME OAuth() registry as the login client ("google") —
# Authlib supports multiple named clients on one registry. Kept as a
# separate client (not a second scope on "google") so logging in can never
# implicitly grant Gmail access: this consent is only ever requested by the
# explicit "Connect Gmail" action in app/gmail/router.py.
oauth.register(
    name="google_gmail",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "code_challenge_method": "S256",
    },
    # access_type=offline requests a refresh_token; prompt=consent forces
    # Google to issue one on every connect (not just the very first time).
    authorize_params={"access_type": "offline", "prompt": "consent"},
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_gmail_oauth.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/gmail/oauth.py backend/tests/test_gmail_oauth.py
git commit -m "feat(backend): register Gmail's second OAuth client

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Raw Gmail/Google HTTP calls

**Files:**
- Create: `backend/app/gmail/google_api.py`
- Create: `backend/app/gmail/exceptions.py`
- Test: `backend/tests/test_gmail_google_api.py`

**Interfaces:**
- Produces: `app.gmail.google_api.GoogleApiError` (exception), `refresh_access_token(*, client_id: str, client_secret: str, refresh_token: str) -> dict`, `revoke_token(token: str) -> None`, `get_profile(access_token: str) -> dict`, `list_message_ids(access_token: str, *, limit: int) -> list[str]`, `get_message_summary(access_token: str, message_id: str) -> dict` (returns `{"id", "subject", "from_", "date", "snippet"}`).
- Produces: `app.gmail.exceptions.GmailNotConnected(user_id: UUID)`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_gmail_google_api.py`:

```python
import httpx
import pytest

from app.gmail import google_api


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict) -> None:
        self.status_code = status_code
        self._json_data = json_data

    def json(self) -> dict:
        return self._json_data


def test_refresh_access_token_returns_json_on_success(monkeypatch) -> None:
    def fake_post(url, data, timeout):
        assert url == google_api.TOKEN_URL
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "rtoken"
        return _FakeResponse(200, {"access_token": "new-token", "expires_in": 3600})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = google_api.refresh_access_token(
        client_id="cid", client_secret="csecret", refresh_token="rtoken"
    )
    assert result["access_token"] == "new-token"


def test_refresh_access_token_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.refresh_access_token(client_id="c", client_secret="s", refresh_token="r")


def test_revoke_token_succeeds_on_200(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, {}))
    google_api.revoke_token("token")  # no exception raised


def test_revoke_token_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.revoke_token("token")


def test_get_profile_returns_email(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse(200, {"emailAddress": "a@gmail.com"})
    )
    profile = google_api.get_profile("access-token")
    assert profile["emailAddress"] == "a@gmail.com"


def test_list_message_ids_extracts_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _FakeResponse(200, {"messages": [{"id": "m1"}, {"id": "m2"}]}),
    )
    assert google_api.list_message_ids("token", limit=2) == ["m1", "m2"]


def test_list_message_ids_returns_empty_list_when_no_messages(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {}))
    assert google_api.list_message_ids("token", limit=20) == []


def test_get_message_summary_extracts_headers_and_snippet(monkeypatch) -> None:
    payload = {
        "id": "m1",
        "snippet": "hello there",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Your application"},
                {"name": "From", "value": "jobs@acme.com"},
                {"name": "Date", "value": "Wed, 1 Jan 2026 00:00:00 +0000"},
            ]
        },
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    summary = google_api.get_message_summary("token", "m1")
    assert summary == {
        "id": "m1",
        "subject": "Your application",
        "from_": "jobs@acme.com",
        "date": "Wed, 1 Jan 2026 00:00:00 +0000",
        "snippet": "hello there",
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.gmail.google_api'`

- [ ] **Step 3: Write the exception**

Create `backend/app/gmail/exceptions.py`:

```python
from uuid import UUID


class GmailNotConnected(Exception):
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"No Gmail connection for user {user_id}")
```

- [ ] **Step 4: Write the HTTP helpers**

Create `backend/app/gmail/google_api.py`:

```python
import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

_TIMEOUT = 10.0


class GoogleApiError(Exception):
    """A Google/Gmail HTTP call returned a non-2xx response."""


def refresh_access_token(*, client_id: str, client_secret: str, refresh_token: str) -> dict:
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"token refresh failed: {response.status_code}")
    return response.json()


def revoke_token(token: str) -> None:
    response = httpx.post(REVOKE_URL, data={"token": token}, timeout=_TIMEOUT)
    if response.status_code != 200:
        raise GoogleApiError(f"token revoke failed: {response.status_code}")


def get_profile(access_token: str) -> dict:
    response = httpx.get(
        f"{GMAIL_API_BASE}/profile",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"profile fetch failed: {response.status_code}")
    return response.json()


def list_message_ids(access_token: str, *, limit: int) -> list[str]:
    response = httpx.get(
        f"{GMAIL_API_BASE}/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"maxResults": limit},
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"message list failed: {response.status_code}")
    return [item["id"] for item in response.json().get("messages", [])]


def get_message_summary(access_token: str, message_id: str) -> dict:
    response = httpx.get(
        f"{GMAIL_API_BASE}/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        # format=metadata + an explicit header allowlist — we deliberately
        # never fetch the message body, even though gmail.readonly permits
        # it (see spec §10, data minimization).
        params={
            "format": "metadata",
            "metadataHeaders": ["Subject", "From", "Date"],
        },
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"message fetch failed: {response.status_code}")
    payload = response.json()
    headers = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
    return {
        "id": payload["id"],
        "subject": headers.get("Subject", ""),
        "from_": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": payload.get("snippet", ""),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/gmail/google_api.py backend/app/gmail/exceptions.py \
        backend/tests/test_gmail_google_api.py
git commit -m "feat(backend): raw Gmail/Google HTTP helpers (refresh, revoke, list, get, profile)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Gmail service layer (business logic)

**Files:**
- Create: `backend/app/gmail/service.py`
- Test: `backend/tests/test_gmail_service.py`

**Interfaces:**
- Consumes: `app.gmail.google_api.*` (Task 4), `app.gmail.crypto.encrypt_token`/`decrypt_token` (Task 1), `app.gmail.models.GmailConnection` (Task 2), `app.gmail.exceptions.GmailNotConnected` (Task 4), `settings.google_client_id`/`google_client_secret`.
- Produces: `get_connection(db, user_id) -> GmailConnection | None`, `connect(db, user_id, token: dict) -> GmailConnection`, `get_valid_access_token(db, connection: GmailConnection) -> str`, `list_recent_messages(db, user_id, limit: int) -> list[dict]` (raises `GmailNotConnected`), `disconnect(db, user_id) -> None` (raises `GmailNotConnected`). These are exactly what Task 6's router calls.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_gmail_service.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.gmail import google_api, service
from app.gmail.crypto import decrypt_token, encrypt_token
from app.gmail.exceptions import GmailNotConnected
from app.gmail.models import GmailConnection

FAKE_TOKEN = {
    "access_token": "fake-access",
    "refresh_token": "fake-refresh",
    "expires_in": 3600,
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
}


def _make_connection(db_session, user, *, expires_in_seconds: int) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("current-access"),
        refresh_token_encrypted=encrypt_token("current-refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


def test_connect_creates_new_connection_with_encrypted_tokens(db_session, user, monkeypatch) -> None:
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})

    connection = service.connect(db_session, user.id, FAKE_TOKEN)

    assert connection.google_email == "alice@gmail.com"
    assert connection.access_token_encrypted != FAKE_TOKEN["access_token"]
    assert decrypt_token(connection.access_token_encrypted) == FAKE_TOKEN["access_token"]
    assert decrypt_token(connection.refresh_token_encrypted) == FAKE_TOKEN["refresh_token"]


def test_connect_updates_existing_connection_on_reconnect(db_session, user, monkeypatch) -> None:
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})
    service.connect(db_session, user.id, FAKE_TOKEN)

    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice.new@gmail.com"})
    new_token = {**FAKE_TOKEN, "access_token": "new-access"}
    connection = service.connect(db_session, user.id, new_token)

    assert connection.google_email == "alice.new@gmail.com"
    assert decrypt_token(connection.access_token_encrypted) == "new-access"
    count = (
        db_session.query(GmailConnection).filter_by(user_id=user.id).count()
    )
    assert count == 1


def test_get_valid_access_token_returns_cached_token_when_not_expiring_soon(
    db_session, user, monkeypatch
) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)
    calls = []
    monkeypatch.setattr(google_api, "refresh_access_token", lambda **kw: calls.append(kw))

    token = service.get_valid_access_token(db_session, connection)

    assert token == "current-access"
    assert calls == []


def test_get_valid_access_token_refreshes_when_expiring_soon(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=30)
    monkeypatch.setattr(
        google_api,
        "refresh_access_token",
        lambda **kw: {"access_token": "refreshed-access", "expires_in": 3600},
    )

    token = service.get_valid_access_token(db_session, connection)

    assert token == "refreshed-access"
    assert decrypt_token(connection.access_token_encrypted) == "refreshed-access"
    assert connection.token_expiry > datetime.now(timezone.utc) + timedelta(minutes=30)


def test_list_recent_messages_raises_when_not_connected(db_session, user) -> None:
    with pytest.raises(GmailNotConnected):
        service.list_recent_messages(db_session, user.id, 20)


def test_list_recent_messages_returns_summaries(db_session, user, monkeypatch) -> None:
    _make_connection(db_session, user, expires_in_seconds=3600)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1", "m2"])
    monkeypatch.setattr(
        google_api,
        "get_message_summary",
        lambda token, message_id: {
            "id": message_id, "subject": "s", "from_": "f", "date": "d", "snippet": "sn"
        },
    )

    messages = service.list_recent_messages(db_session, user.id, 2)

    assert [m["id"] for m in messages] == ["m1", "m2"]


def test_disconnect_revokes_and_deletes_row(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)
    revoked = []
    monkeypatch.setattr(google_api, "revoke_token", lambda token: revoked.append(token))

    service.disconnect(db_session, user.id)

    assert revoked == ["current-refresh"]
    assert db_session.get(GmailConnection, connection.id) is None


def test_disconnect_deletes_row_even_if_revoke_raises(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)

    def failing_revoke(token):
        raise google_api.GoogleApiError("boom")

    monkeypatch.setattr(google_api, "revoke_token", failing_revoke)

    service.disconnect(db_session, user.id)

    assert db_session.get(GmailConnection, connection.id) is None


def test_disconnect_raises_when_not_connected(db_session, user) -> None:
    with pytest.raises(GmailNotConnected):
        service.disconnect(db_session, user.id)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_gmail_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.gmail.service'`

- [ ] **Step 3: Write the service module**

Create `backend/app/gmail/service.py`:

```python
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.gmail import google_api
from app.gmail.crypto import decrypt_token, encrypt_token
from app.gmail.exceptions import GmailNotConnected
from app.gmail.models import GmailConnection

logger = logging.getLogger(__name__)

# Refresh a minute before the stored access token actually expires, rather
# than racing expiry mid-request.
REFRESH_BUFFER = timedelta(seconds=60)


def get_connection(db: Session, user_id: UUID) -> GmailConnection | None:
    return db.scalars(
        select(GmailConnection).where(GmailConnection.user_id == user_id)
    ).one_or_none()


def connect(db: Session, user_id: UUID, token: dict) -> GmailConnection:
    access_token = token["access_token"]
    refresh_token = token["refresh_token"]
    expires_in = token["expires_in"]
    scope = token["scope"]

    # gmail.readonly alone is enough to call this endpoint — we don't
    # request the "email"/"openid" scopes just to learn the connected
    # address.
    profile = google_api.get_profile(access_token)

    connection = get_connection(db, user_id)
    if connection is None:
        connection = GmailConnection(user_id=user_id)
        db.add(connection)

    connection.google_email = profile["emailAddress"]
    connection.access_token_encrypted = encrypt_token(access_token)
    connection.refresh_token_encrypted = encrypt_token(refresh_token)
    connection.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    connection.scope = scope

    db.commit()
    db.refresh(connection)
    return connection


def get_valid_access_token(db: Session, connection: GmailConnection) -> str:
    if connection.token_expiry - datetime.now(timezone.utc) > REFRESH_BUFFER:
        return decrypt_token(connection.access_token_encrypted)

    refresh_token = decrypt_token(connection.refresh_token_encrypted)
    token = google_api.refresh_access_token(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        refresh_token=refresh_token,
    )
    connection.access_token_encrypted = encrypt_token(token["access_token"])
    connection.token_expiry = datetime.now(timezone.utc) + timedelta(
        seconds=token["expires_in"]
    )
    db.commit()
    db.refresh(connection)
    return token["access_token"]


def list_recent_messages(db: Session, user_id: UUID, limit: int) -> list[dict]:
    connection = get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    access_token = get_valid_access_token(db, connection)
    message_ids = google_api.list_message_ids(access_token, limit=limit)
    return [google_api.get_message_summary(access_token, message_id) for message_id in message_ids]


def disconnect(db: Session, user_id: UUID) -> None:
    connection = get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    refresh_token = decrypt_token(connection.refresh_token_encrypted)
    try:
        google_api.revoke_token(refresh_token)
    except google_api.GoogleApiError:
        # A failed revoke leaves a stale grant at Google — logged, not
        # fatal. We still delete our row: a disconnect button that doesn't
        # disconnect locally is worse than a lagging revoke upstream.
        logger.warning("Failed to revoke Gmail token for user %s", user_id)

    db.delete(connection)
    db.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_gmail_service.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/gmail/service.py backend/tests/test_gmail_service.py
git commit -m "feat(backend): Gmail connect/disconnect/token-refresh/message service

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Router, schemas, and app wiring

**Files:**
- Create: `backend/app/gmail/schemas.py`
- Create: `backend/app/gmail/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_gmail_router.py`

**Interfaces:**
- Consumes: `app.gmail.service.*` (Task 5), `app.gmail.oauth.oauth` (Task 3), `app.auth.dependencies.get_current_user`, `app.gmail.exceptions.GmailNotConnected` (Task 4).
- Produces: `GET/POST /api/v1/gmail/{connect,callback,status,disconnect,messages}` per the spec's §8 API contract; `app.gmail.schemas.GmailStatus`, `app.gmail.schemas.GmailMessageSummary`.

- [ ] **Step 1: Write the schemas**

Create `backend/app/gmail/schemas.py`:

```python
from datetime import datetime

from pydantic import BaseModel


class GmailStatus(BaseModel):
    connected: bool
    email: str | None
    connected_at: datetime | None


class GmailMessageSummary(BaseModel):
    id: str
    subject: str
    from_: str
    date: str
    snippet: str
```

- [ ] **Step 2: Write the failing router tests**

Create `backend/tests/test_gmail_router.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from authlib.integrations.base_client import OAuthError
from fastapi.testclient import TestClient

from app.gmail import google_api
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.gmail.oauth import oauth

BASE = "/api/v1/gmail"

FAKE_TOKEN = {
    "access_token": "fake-access",
    "refresh_token": "fake-refresh",
    "expires_in": 3600,
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
}


@pytest.fixture
def connected_gmail(db_session, user) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("current-access"),
        refresh_token_encrypted=encrypt_token("current-refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


@pytest.mark.parametrize(
    "method,path",
    [("get", "/connect"), ("get", "/status"), ("get", "/messages"), ("post", "/disconnect")],
)
def test_gmail_endpoints_require_authentication(client: TestClient, method, path) -> None:
    response = getattr(client, method)(f"{BASE}{path}")
    assert response.status_code == 401


def test_status_when_not_connected(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/status")
    assert response.status_code == 200
    assert response.json() == {"connected": False, "email": None, "connected_at": None}


def test_status_when_connected(auth_client: TestClient, connected_gmail) -> None:
    response = auth_client.get(f"{BASE}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["email"] == "alice@gmail.com"


def test_callback_success_creates_connection(auth_client: TestClient, monkeypatch) -> None:
    async def fake_authorize_access_token(request):
        return FAKE_TOKEN

    monkeypatch.setattr(oauth.google_gmail, "authorize_access_token", fake_authorize_access_token)
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})

    response = auth_client.get(f"{BASE}/callback?state=x&code=y", follow_redirects=False)
    assert response.status_code in (302, 307)

    status_response = auth_client.get(f"{BASE}/status")
    assert status_response.json()["connected"] is True


def test_callback_oauth_error_redirects_without_creating_connection(
    auth_client: TestClient, monkeypatch
) -> None:
    async def fake_authorize_access_token(request):
        raise OAuthError(description="access_denied")

    monkeypatch.setattr(oauth.google_gmail, "authorize_access_token", fake_authorize_access_token)

    response = auth_client.get(f"{BASE}/callback?state=x&code=y", follow_redirects=False)
    assert response.status_code in (302, 307)

    status_response = auth_client.get(f"{BASE}/status")
    assert status_response.json()["connected"] is False


def test_messages_endpoint_returns_summaries(
    auth_client: TestClient, connected_gmail, monkeypatch
) -> None:
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(
        google_api,
        "get_message_summary",
        lambda token, message_id: {
            "id": message_id, "subject": "Hi", "from_": "a@b.com", "date": "d", "snippet": "s"
        },
    )

    response = auth_client.get(f"{BASE}/messages?limit=1")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "m1", "subject": "Hi", "from_": "a@b.com", "date": "d", "snippet": "s"}
    ]


def test_messages_endpoint_404_when_not_connected(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/messages")
    assert response.status_code == 404


def test_disconnect_endpoint_deletes_connection(
    auth_client: TestClient, connected_gmail, monkeypatch
) -> None:
    monkeypatch.setattr(google_api, "revoke_token", lambda token: None)

    response = auth_client.post(f"{BASE}/disconnect")

    assert response.status_code == 204
    status_response = auth_client.get(f"{BASE}/status")
    assert status_response.json()["connected"] is False


def test_disconnect_endpoint_404_when_not_connected(auth_client: TestClient) -> None:
    response = auth_client.post(f"{BASE}/disconnect")
    assert response.status_code == 404


def test_cross_user_isolation(
    auth_client: TestClient, other_auth_client: TestClient, connected_gmail
) -> None:
    other_status = other_auth_client.get(f"{BASE}/status")
    assert other_status.json()["connected"] is False

    other_disconnect = other_auth_client.post(f"{BASE}/disconnect")
    assert other_disconnect.status_code == 404

    own_status = auth_client.get(f"{BASE}/status")
    assert own_status.json()["connected"] is True
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_gmail_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.gmail.router'`

- [ ] **Step 4: Write the router**

Create `backend/app/gmail/router.py`:

```python
from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.gmail import service
from app.gmail.oauth import oauth
from app.gmail.schemas import GmailMessageSummary, GmailStatus
from app.users.models import User

router = APIRouter(prefix="/gmail", tags=["gmail"])


@router.get("/connect")
async def gmail_connect(request: Request, current_user: User = Depends(get_current_user)):
    redirect_uri = str(request.url_for("gmail_callback"))
    return await oauth.google_gmail.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="gmail_callback")
async def gmail_callback(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        token = await oauth.google_gmail.authorize_access_token(request)
    except OAuthError:
        # Consent declined, expired/replayed state, or a token-exchange
        # failure — send the user back to the frontend instead of a 500.
        return RedirectResponse(url=settings.frontend_url)

    service.connect(db, current_user.id, token)
    return RedirectResponse(url=settings.frontend_url)


@router.get("/status", response_model=GmailStatus)
def gmail_status(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> GmailStatus:
    connection = service.get_connection(db, current_user.id)
    if connection is None:
        return GmailStatus(connected=False, email=None, connected_at=None)
    return GmailStatus(
        connected=True, email=connection.google_email, connected_at=connection.created_at
    )


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def gmail_disconnect(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    service.disconnect(db, current_user.id)


@router.get("/messages", response_model=list[GmailMessageSummary])
def gmail_messages(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    return service.list_recent_messages(db, current_user.id, limit)
```

- [ ] **Step 5: Mount the router and register the `GmailNotConnected` exception handler**

Edit `backend/app/main.py`:

```python
from app.applications.exceptions import ApplicationNotFound
from app.applications.router import router as applications_router
from app.auth.router import router as auth_router
from app.core.config import settings
from app.gmail.exceptions import GmailNotConnected
from app.gmail.router import router as gmail_router
```

```python
    @app.exception_handler(ApplicationNotFound)
    async def handle_application_not_found(
        request: Request, exc: ApplicationNotFound
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(GmailNotConnected)
    async def handle_gmail_not_connected(
        request: Request, exc: GmailNotConnected
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
```

```python
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(applications_router, prefix="/api/v1")
    app.include_router(gmail_router, prefix="/api/v1")
```

- [ ] **Step 6: Run the router tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_gmail_router.py -v`
Expected: PASS (11 tests, counting the 4 parametrized auth cases)

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: every test passes — Phase 1/2 tests unaffected, all new Gmail tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/gmail/schemas.py backend/app/gmail/router.py backend/app/main.py \
        backend/tests/test_gmail_router.py
git commit -m "feat(backend): mount Gmail connect/status/disconnect/messages endpoints

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Frontend — Gmail connection panel

**Files:**
- Create: `frontend/src/types/gmail.ts`
- Create: `frontend/src/api/gmail.ts`
- Create: `frontend/src/components/GmailPanel.tsx`
- Modify: `frontend/src/components/ApplicationsPage.tsx`

**Interfaces:**
- Consumes: `../api/http`'s `parseResponse<T>` (existing).
- Produces: `getGmailStatus(): Promise<GmailStatus>`, `disconnectGmail(): Promise<void>`, `fetchGmailMessages(limit?: number): Promise<GmailMessageSummary[]>`, `GMAIL_CONNECT_URL: string`, `<GmailPanel />` component (no props).

There's no frontend test framework in this repo (no Vitest/Jest config, no `*.test.*` files anywhere under `frontend/src`) — Phase 1 and Phase 2 frontend work was verified manually the same way, so this task is too. Verification is running the dev server and exercising the UI by hand (Step 6).

- [ ] **Step 1: Add the Gmail types**

Create `frontend/src/types/gmail.ts`:

```typescript
export interface GmailStatus {
  connected: boolean
  email: string | null
  connected_at: string | null
}

export interface GmailMessageSummary {
  id: string
  subject: string
  from_: string
  date: string
  snippet: string
}
```

- [ ] **Step 2: Add the Gmail API client**

Create `frontend/src/api/gmail.ts`:

```typescript
import { parseResponse } from './http'
import type { GmailMessageSummary, GmailStatus } from '../types/gmail'

const BASE = '/api/v1/gmail'

export function getGmailStatus(): Promise<GmailStatus> {
  return fetch(`${BASE}/status`).then((r) => parseResponse<GmailStatus>(r))
}

export function disconnectGmail(): Promise<void> {
  return fetch(`${BASE}/disconnect`, { method: 'POST' }).then((r) => parseResponse<void>(r))
}

export function fetchGmailMessages(limit = 20): Promise<GmailMessageSummary[]> {
  return fetch(`${BASE}/messages?limit=${limit}`).then((r) =>
    parseResponse<GmailMessageSummary[]>(r),
  )
}

export const GMAIL_CONNECT_URL = `${BASE}/connect`
```

- [ ] **Step 3: Build the Gmail panel component**

Create `frontend/src/components/GmailPanel.tsx`:

```tsx
import { useEffect, useState } from 'react'

import {
  disconnectGmail,
  fetchGmailMessages,
  getGmailStatus,
  GMAIL_CONNECT_URL,
} from '../api/gmail'
import type { GmailMessageSummary, GmailStatus } from '../types/gmail'

export function GmailPanel() {
  const [status, setStatus] = useState<GmailStatus | null>(null)
  const [messages, setMessages] = useState<GmailMessageSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getGmailStatus()
      .then(setStatus)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load Gmail status'))
      .finally(() => setLoading(false))
  }, [])

  const handleDisconnect = async () => {
    setError(null)
    try {
      await disconnectGmail()
      setStatus({ connected: false, email: null, connected_at: null })
      setMessages(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to disconnect Gmail')
    }
  }

  const handleFetchMessages = async () => {
    setError(null)
    try {
      setMessages(await fetchGmailMessages())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch Gmail messages')
    }
  }

  if (loading) return null

  return (
    <section className="space-y-3 rounded border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">Gmail</h2>
          <p className="text-sm text-gray-500">
            {status?.connected ? `Connected as ${status.email}` : 'Not connected'}
          </p>
        </div>
        {status?.connected ? (
          <div className="flex gap-2">
            <button
              onClick={handleFetchMessages}
              className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Fetch recent messages
            </button>
            <button
              onClick={handleDisconnect}
              className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Disconnect
            </button>
          </div>
        ) : (
          <a
            href={GMAIL_CONNECT_URL}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            Connect Gmail
          </a>
        )}
      </div>

      {error && (
        <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>
      )}

      {messages && (
        <ul className="divide-y divide-gray-100">
          {messages.length === 0 && <li className="py-2 text-sm text-gray-500">No messages found.</li>}
          {messages.map((message) => (
            <li key={message.id} className="py-2 text-sm">
              <p className="font-medium text-gray-900">{message.subject || '(no subject)'}</p>
              <p className="text-gray-500">
                {message.from_} · {message.date}
              </p>
              <p className="text-gray-600">{message.snippet}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
```

- [ ] **Step 4: Render it on the dashboard**

Edit `frontend/src/components/ApplicationsPage.tsx`:

```tsx
import { useApplications } from '../hooks/useApplications'
import type { Application } from '../types/application'
import type { User } from '../types/user'
import { ApplicationForm } from './ApplicationForm'
import { ApplicationList } from './ApplicationList'
import { GmailPanel } from './GmailPanel'
import { UserMenu } from './UserMenu'
```

```tsx
        <UserMenu user={user} onLogout={onLogout} />
      </header>

      <GmailPanel />

      {editing ? (
```

- [ ] **Step 5: Type-check and lint**

Run: `cd frontend && npm run build`
Expected: `tsc -b` and the Vite build both succeed with no type errors.

Run: `cd frontend && npm run lint`
Expected: no new lint errors from the files this task added/touched.

- [ ] **Step 6: Manually verify against the running app**

With the backend running (`cd backend && uv run uvicorn app.main:app --reload --port 8000`) and Gmail credentials configured (Task 8 covers the Google Cloud Console side — do that first if you haven't), run `cd frontend && npm run dev`, open the dashboard, and confirm:
1. The Gmail panel shows "Not connected" with a "Connect Gmail" button.
2. Clicking it navigates to Google's consent screen naming Gmail read access specifically (separate from the login consent you already granted).
3. After approving, you land back on the dashboard and the panel now shows "Connected as `<your gmail address>`".
4. "Fetch recent messages" shows a list of your actual recent emails (subject/from/date/snippet only).
5. "Disconnect" flips the panel back to "Not connected", and your Google Account's [connected apps page](https://myaccount.google.com/permissions) no longer lists this app's Gmail grant (it may still list the original login grant, which is a separate, unaffected authorization).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/gmail.ts frontend/src/api/gmail.ts \
        frontend/src/components/GmailPanel.tsx frontend/src/components/ApplicationsPage.tsx
git commit -m "feat(frontend): Gmail connection panel (connect, status, disconnect, test fetch)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Google Cloud Console setup and docs

**Files:**
- Modify: `README.md`

**Interfaces:** None — documentation only.

- [ ] **Step 1: Enable the Gmail API and add the scope/redirect URI**

In the same Google Cloud project used for Phase 2 login:
1. **APIs & Services → Library** → search "Gmail API" → **Enable**.
2. **APIs & Services → OAuth consent screen → Data Access** (or "Scopes", depending on console version) → **Add or Remove Scopes** → add `https://www.googleapis.com/auth/gmail.readonly` → Save. This is a sensitive scope; while the app is in "Testing" mode, only accounts already listed under "Test users" (added during Phase 2 setup) can complete this consent.
3. **APIs & Services → Credentials** → open the existing Web application OAuth client (the one from Phase 2) → under "Authorized redirect URIs" add: `http://localhost:8000/api/v1/gmail/callback` → Save.

No new Client ID/Secret — this reuses `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` already in `backend/.env`.

- [ ] **Step 2: Document it in the README**

Edit `README.md`, adding a new section right after "Google OAuth setup (required for login)":

```markdown
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
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: Gmail integration setup instructions for Phase 3

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
