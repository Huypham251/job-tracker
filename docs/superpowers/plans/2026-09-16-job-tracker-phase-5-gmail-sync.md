# Phase 5: Background Gmail Sync & Job Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 4's synchronous, 20-message-capped "Process Inbox" flow with a
bounded initial/backfill sync, watermark-based incremental syncs, and a Postgres-backed
job queue/worker so mailbox processing runs off the HTTP request lifecycle.

**Architecture:** A new `app/sync/` module owns a `sync_jobs` table (the queue, claimed
via `SELECT ... FOR UPDATE SKIP LOCKED`) and a separate worker process that pages
through Gmail, reusing Phase 4's unchanged classify/match/trust-model decision logic per
message. The frontend starts a sync via `POST /gmail/sync` and polls
`GET /gmail/sync/{id}` for progress.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Postgres (psycopg3) — all already in
use. No new dependencies (no Redis, no Celery). React/TypeScript frontend, polling-based
(no SSE/WebSocket).

**Spec:** `docs/superpowers/specs/2026-09-16-job-tracker-phase-5-gmail-sync-design.md`

## Global Constraints

- No new backend or frontend dependencies (spec §2: Job queue decision) — the queue is
  a Postgres table, the worker is a plain Python process, polling is plain `fetch`.
- Migration file is `backend/alembic/versions/0006_add_sync_jobs.py`, revision `"0006"`,
  `down_revision = "0005"` (the last existing migration).
- New setting: `settings.gmail_sync_backfill_days: int = 180` (spec §2: Backfill window).
- Incremental overlap margin is exactly 1 day (spec §2, §5).
- Job retry: `max_attempts = 3` default, backoff `min(30 * 2**attempts, 3600)` seconds
  (spec §2: Retry/backoff decision).
- Worker poll interval: 2 seconds (spec §2: Frontend progress decision, mirrored
  server-side).
- Every backend test mocks `Extractor` and `google_api` (via `httpx`/`monkeypatch`) —
  no real network calls anywhere in the suite, matching the existing convention in
  `backend/tests/`.
- `app/classifier/`, `app/pipeline/matching.py`, and `_apply_decision`'s trust-model
  logic are not modified anywhere in this plan.
- Frontend has no test runner configured (`package.json` has no `test` script) —
  frontend verification is `npx tsc -b`, `npx oxlint`, and manual browser checks, per
  existing project convention.

---

## File Structure

```
backend/
├── alembic/versions/0006_add_sync_jobs.py   NEW — sync_jobs table, gmail_connections
│                                              /processed_messages column additions
├── app/
│   ├── core/config.py                        MODIFIED — + gmail_sync_backfill_days
│   ├── gmail/
│   │   ├── models.py                          MODIFIED — + last_synced_message_date
│   │   └── google_api.py                       MODIFIED — + list_message_ids_page()
│   ├── pipeline/
│   │   ├── models.py                            MODIFIED — + sync_job_id
│   │   ├── service.py                            MODIFIED — + process_message();
│   │   │                                           process_inbox() removed (Task 10)
│   │   └── router.py                              MODIFIED — POST /process removed
│   │                                                (Task 10)
│   ├── sync/                                       NEW
│   │   ├── __init__.py
│   │   ├── models.py                                 SyncJob
│   │   ├── schemas.py                                 SyncJobRead
│   │   ├── exceptions.py                               SyncAlreadyRunning, SyncJobNotFound
│   │   ├── service.py                                   enqueue_sync, get_job, get_latest_job
│   │   ├── router.py                                     /gmail/sync* endpoints
│   │   └── worker.py                                      claim_next_job, process_job,
│   │                                                        run_forever, __main__
│   └── main.py                                       MODIFIED — register sync router +
│                                                        exception handlers
└── tests/
    ├── test_sync_models.py                       NEW
    ├── test_gmail_google_api.py                   MODIFIED — + pagination tests
    ├── test_sync_service.py                        NEW
    ├── test_sync_router.py                          NEW
    ├── test_sync_worker.py                           NEW
    ├── test_pipeline_service.py                       MODIFIED — process_inbox tests
    │                                                     replaced by process_message tests
    └── test_pipeline_router.py                          MODIFIED — /process tests removed
                                                            (Task 10)

frontend/src/
├── types/sync.ts              NEW
├── api/sync.ts                 NEW
├── components/
│   ├── SyncPanel.tsx             NEW — replaces the "Pipeline" section
│   ├── ApplicationsPage.tsx        MODIFIED — SyncPanel instead of Process Inbox
│   └── ReviewQueue.tsx               MODIFIED — + refreshSignal prop
├── api/pipeline.ts               MODIFIED — processInbox() removed (Task 11)
└── types/pipeline.ts               MODIFIED — ProcessResult removed (Task 11)
```

---

### Task 1: Database schema — `sync_jobs`, watermark, audit column

**Files:**
- Create: `backend/app/sync/__init__.py`
- Create: `backend/app/sync/models.py`
- Create: `backend/alembic/versions/0006_add_sync_jobs.py`
- Modify: `backend/app/gmail/models.py`
- Modify: `backend/app/pipeline/models.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/test_sync_models.py`

**Interfaces:**
- Produces: `app.sync.models.SyncJob` (columns per spec §4: `id, user_id, job_type,
  status, attempts, max_attempts, next_attempt_at, window_start, page_token,
  messages_seen, messages_processed, auto_applied, queued_for_review, ignored,
  failed_count, error_message, started_at, finished_at, created_at, updated_at`).
- Produces: `app.gmail.models.GmailConnection.last_synced_message_date: date | None`.
- Produces: `app.pipeline.models.ProcessedMessage.sync_job_id: UUID | None`.
- Produces: `app.core.config.settings.gmail_sync_backfill_days: int` (default 180).

- [ ] **Step 1: Create `app/sync/__init__.py` (empty) and write the failing model test**

Create `backend/app/sync/__init__.py` with empty content.

Create `backend/tests/test_sync_models.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.sync.models import SyncJob


def _make_job(user_id, *, status: str = "queued", job_type: str = "initial") -> SyncJob:
    return SyncJob(
        user_id=user_id, job_type=job_type, status=status, window_start=date(2026, 1, 1)
    )


def test_sync_job_can_be_created_with_defaults(db_session, user) -> None:
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    assert job.id is not None
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.messages_seen == 0
    assert job.auto_applied == 0
    assert job.failed_count == 0
    assert job.next_attempt_at is not None
    assert job.created_at is not None


def test_partial_unique_index_rejects_second_active_job(db_session, user) -> None:
    db_session.add(_make_job(user.id, status="queued"))
    db_session.commit()

    db_session.add(_make_job(user.id, status="running"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_partial_unique_index_allows_new_job_once_prior_one_is_completed(db_session, user) -> None:
    db_session.add(_make_job(user.id, status="completed"))
    db_session.commit()

    db_session.add(_make_job(user.id, status="queued"))
    db_session.commit()  # must not raise


def test_partial_unique_index_allows_new_job_once_prior_one_has_failed(db_session, user) -> None:
    db_session.add(_make_job(user.id, status="failed"))
    db_session.commit()

    db_session.add(_make_job(user.id, status="queued"))
    db_session.commit()  # must not raise


def test_deleting_user_cascades_to_sync_job(db_session, user) -> None:
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    db_session.delete(user)
    db_session.commit()

    assert db_session.get(SyncJob, job_id) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_sync_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sync.models'` (the module
doesn't exist yet).

- [ ] **Step 3: Write `app/sync/models.py`**

```python
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class SyncJob(TimestampMixin, Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        Index(
            "uq_sync_jobs_user_active",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    page_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    messages_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_applied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_for_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ignored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Add `gmail_connections.last_synced_message_date`**

Modify `backend/app/gmail/models.py` — change the import line and add the column:

```python
from sqlalchemy import Date, DateTime, ForeignKey, String, Text
```

(replaces the existing `from sqlalchemy import DateTime, ForeignKey, String, Text`)

Add after the `scope` column declaration:

```python
    last_synced_message_date: Mapped[date | None] = mapped_column(Date, nullable=True)
```

Also change `from datetime import datetime` to `from datetime import date, datetime` at
the top of the file.

- [ ] **Step 5: Add `processed_messages.sync_job_id`**

Modify `backend/app/pipeline/models.py` — add after `proposed_action`:

```python
    sync_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sync_jobs.id", ondelete="SET NULL"), nullable=True
    )
```

- [ ] **Step 6: Add the new setting**

Modify `backend/app/core/config.py` — add after `pipeline_batch_limit: int = 20`:

```python
    gmail_sync_backfill_days: int = 180
```

- [ ] **Step 7: Register the new model module in conftest.py**

Modify `backend/tests/conftest.py` — add to the model-import block:

```python
from app.sync import models as sync_models  # noqa: F401
```

(alongside the existing `from app.pipeline import models as pipeline_models  # noqa: F401`)

- [ ] **Step 8: Write the migration**

Create `backend/alembic/versions/0006_add_sync_jobs.py`:

```python
"""add sync_jobs table, gmail sync watermark, processed_messages audit link

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("page_token", sa.String(length=255), nullable=True),
        sa.Column("messages_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_applied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_for_review", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ignored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_sync_jobs_user_id", "sync_jobs", ["user_id"])
    op.create_index(
        "uq_sync_jobs_user_active",
        "sync_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_foreign_key(
        "fk_sync_jobs_user_id_users", "sync_jobs", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )

    op.add_column(
        "gmail_connections", sa.Column("last_synced_message_date", sa.Date(), nullable=True)
    )

    op.add_column(
        "processed_messages",
        sa.Column("sync_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_processed_messages_sync_job_id_sync_jobs",
        "processed_messages",
        "sync_jobs",
        ["sync_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_processed_messages_sync_job_id_sync_jobs", "processed_messages", type_="foreignkey"
    )
    op.drop_column("processed_messages", "sync_job_id")
    op.drop_column("gmail_connections", "last_synced_message_date")
    op.drop_table("sync_jobs")
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_sync_models.py tests/test_gmail_models.py tests/test_pipeline_models.py -v`
Expected: PASS (all tests, including the pre-existing gmail/pipeline model tests, which
must still pass unmodified).

- [ ] **Step 10: Commit**

```bash
cd backend
git add app/sync/__init__.py app/sync/models.py app/gmail/models.py app/pipeline/models.py \
  app/core/config.py tests/conftest.py tests/test_sync_models.py alembic/versions/0006_add_sync_jobs.py
git commit -m "feat(sync): add sync_jobs table, gmail sync watermark, and audit link"
```

---

### Task 2: Gmail pagination support

**Files:**
- Modify: `backend/app/gmail/google_api.py`
- Modify: `backend/tests/test_gmail_google_api.py`

**Interfaces:**
- Produces: `app.gmail.google_api.list_message_ids_page(access_token: str, *, query: str, page_token: str | None, max_results: int) -> tuple[list[str], str | None]`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_gmail_google_api.py` (after the existing
`list_message_ids` tests):

```python
def test_list_message_ids_page_returns_ids_and_next_page_token(monkeypatch) -> None:
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured["params"] = params
        return _FakeResponse(200, {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "next-tok"})

    monkeypatch.setattr(httpx, "get", fake_get)

    ids, next_page_token = google_api.list_message_ids_page(
        "token", query="after:2026/01/01", page_token="prev-tok", max_results=100
    )

    assert ids == ["m1", "m2"]
    assert next_page_token == "next-tok"
    assert captured["params"]["q"] == "after:2026/01/01"
    assert captured["params"]["pageToken"] == "prev-tok"
    assert captured["params"]["maxResults"] == 100


def test_list_message_ids_page_omits_page_token_param_when_none(monkeypatch) -> None:
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured["params"] = params
        return _FakeResponse(200, {"messages": []})

    monkeypatch.setattr(httpx, "get", fake_get)

    ids, next_page_token = google_api.list_message_ids_page(
        "token", query="after:2026/01/01", page_token=None, max_results=100
    )

    assert ids == []
    assert next_page_token is None
    assert "pageToken" not in captured["params"]


def test_list_message_ids_page_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.list_message_ids_page(
            "token", query="after:2026/01/01", page_token=None, max_results=100
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py -k list_message_ids_page -v`
Expected: FAIL — `AttributeError: module 'app.gmail.google_api' has no attribute 'list_message_ids_page'`

- [ ] **Step 3: Implement `list_message_ids_page`**

Add to `backend/app/gmail/google_api.py`, directly after `list_message_ids`:

```python
def list_message_ids_page(
    access_token: str, *, query: str, page_token: str | None, max_results: int
) -> tuple[list[str], str | None]:
    params: dict[str, str | int] = {"maxResults": max_results, "q": query}
    if page_token is not None:
        params["pageToken"] = page_token

    response = httpx.get(
        f"{GMAIL_API_BASE}/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"message list failed: {response.status_code}")
    payload = response.json()
    ids = [item["id"] for item in payload.get("messages", [])]
    return ids, payload.get("nextPageToken")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py -v`
Expected: PASS (all tests in the file, including pre-existing ones).

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/gmail/google_api.py tests/test_gmail_google_api.py
git commit -m "feat(gmail): add paginated, date-bounded message listing"
```

---

### Task 3: Sync enqueue service

**Files:**
- Create: `backend/app/sync/schemas.py`
- Create: `backend/app/sync/exceptions.py`
- Create: `backend/app/sync/service.py`
- Create: `backend/tests/test_sync_service.py`

**Interfaces:**
- Consumes: `app.gmail.service.get_connection(db, user_id) -> GmailConnection | None`
  (existing); `app.gmail.exceptions.GmailNotConnected` (existing);
  `app.sync.models.SyncJob` (Task 1); `settings.gmail_sync_backfill_days` (Task 1).
- Produces: `app.sync.exceptions.SyncAlreadyRunning(job: SyncJob)`,
  `app.sync.exceptions.SyncJobNotFound(job_id: UUID)`;
  `app.sync.service.enqueue_sync(db: Session, user_id: UUID) -> SyncJob`,
  `app.sync.service.get_job(db: Session, user_id: UUID, job_id: UUID) -> SyncJob | None`,
  `app.sync.service.get_latest_job(db: Session, user_id: UUID) -> SyncJob | None`;
  `app.sync.schemas.SyncJobRead` (Pydantic model, `from_attributes=True`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_sync_service.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.gmail.crypto import encrypt_token
from app.gmail.exceptions import GmailNotConnected
from app.gmail.models import GmailConnection
from app.sync import service
from app.sync.exceptions import SyncAlreadyRunning


def _connect_gmail(db_session, user, *, last_synced_message_date=None) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("access"),
        refresh_token_encrypted=encrypt_token("refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
        last_synced_message_date=last_synced_message_date,
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def test_enqueue_sync_raises_when_not_connected(db_session, user) -> None:
    with pytest.raises(GmailNotConnected):
        service.enqueue_sync(db_session, user.id)


def test_enqueue_sync_creates_initial_job_when_never_synced(db_session, user) -> None:
    _connect_gmail(db_session, user)

    job = service.enqueue_sync(db_session, user.id)

    assert job.job_type == "initial"
    assert job.status == "queued"
    assert job.window_start == date.today() - timedelta(days=settings.gmail_sync_backfill_days)


def test_enqueue_sync_creates_incremental_job_with_overlap_margin(db_session, user) -> None:
    watermark = date(2026, 6, 1)
    _connect_gmail(db_session, user, last_synced_message_date=watermark)

    job = service.enqueue_sync(db_session, user.id)

    assert job.job_type == "incremental"
    assert job.window_start == watermark - timedelta(days=1)


def test_enqueue_sync_raises_when_a_job_is_already_active(db_session, user) -> None:
    _connect_gmail(db_session, user)
    first = service.enqueue_sync(db_session, user.id)

    with pytest.raises(SyncAlreadyRunning) as exc_info:
        service.enqueue_sync(db_session, user.id)
    assert exc_info.value.job.id == first.id


def test_enqueue_sync_allows_new_job_after_previous_one_finished(db_session, user) -> None:
    _connect_gmail(db_session, user)
    first = service.enqueue_sync(db_session, user.id)
    first.status = "completed"
    db_session.commit()

    second = service.enqueue_sync(db_session, user.id)

    assert second.id != first.id


def test_get_job_returns_none_for_another_users_job(db_session, user, other_user) -> None:
    _connect_gmail(db_session, user)
    job = service.enqueue_sync(db_session, user.id)

    assert service.get_job(db_session, other_user.id, job.id) is None


def test_get_job_returns_the_job_for_its_owner(db_session, user) -> None:
    _connect_gmail(db_session, user)
    job = service.enqueue_sync(db_session, user.id)

    found = service.get_job(db_session, user.id, job.id)

    assert found is not None
    assert found.id == job.id


def test_get_latest_job_returns_none_when_no_jobs_exist(db_session, user) -> None:
    assert service.get_latest_job(db_session, user.id) is None


def test_get_latest_job_returns_the_most_recently_created_job(db_session, user) -> None:
    _connect_gmail(db_session, user)
    first = service.enqueue_sync(db_session, user.id)
    first.status = "completed"
    db_session.commit()
    second = service.enqueue_sync(db_session, user.id)

    latest = service.get_latest_job(db_session, user.id)

    assert latest is not None
    assert latest.id == second.id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_sync_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sync.service'`

- [ ] **Step 3: Write `app/sync/exceptions.py`**

```python
from uuid import UUID

from app.sync.models import SyncJob


class SyncAlreadyRunning(Exception):
    def __init__(self, job: SyncJob) -> None:
        self.job = job
        super().__init__(f"A sync job is already active for user {job.user_id}")


class SyncJobNotFound(Exception):
    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(f"Sync job {job_id} not found")
```

- [ ] **Step 4: Write `app/sync/schemas.py`**

```python
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SyncJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: str
    status: str
    attempts: int
    window_start: date
    messages_seen: int
    messages_processed: int
    auto_applied: int
    queued_for_review: int
    ignored: int
    failed_count: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
```

- [ ] **Step 5: Write `app/sync/service.py`**

```python
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.gmail import service as gmail_service
from app.gmail.exceptions import GmailNotConnected
from app.sync.exceptions import SyncAlreadyRunning
from app.sync.models import SyncJob

_ACTIVE_STATUSES = ("queued", "running")
_INCREMENTAL_OVERLAP_DAYS = 1


def _get_active_job(db: Session, user_id: UUID) -> SyncJob | None:
    return db.scalars(
        select(SyncJob).where(SyncJob.user_id == user_id, SyncJob.status.in_(_ACTIVE_STATUSES))
    ).one_or_none()


def enqueue_sync(db: Session, user_id: UUID) -> SyncJob:
    connection = gmail_service.get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    active = _get_active_job(db, user_id)
    if active is not None:
        raise SyncAlreadyRunning(active)

    if connection.last_synced_message_date is None:
        job_type = "initial"
        window_start = date.today() - timedelta(days=settings.gmail_sync_backfill_days)
    else:
        job_type = "incremental"
        window_start = connection.last_synced_message_date - timedelta(
            days=_INCREMENTAL_OVERLAP_DAYS
        )

    job = SyncJob(user_id=user_id, job_type=job_type, window_start=window_start)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SyncAlreadyRunning(_get_active_job(db, user_id)) from None
    db.refresh(job)
    return job


def get_job(db: Session, user_id: UUID, job_id: UUID) -> SyncJob | None:
    return db.scalars(
        select(SyncJob).where(SyncJob.id == job_id, SyncJob.user_id == user_id)
    ).one_or_none()


def get_latest_job(db: Session, user_id: UUID) -> SyncJob | None:
    return db.scalars(
        select(SyncJob)
        .where(SyncJob.user_id == user_id)
        .order_by(SyncJob.created_at.desc())
        .limit(1)
    ).one_or_none()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_sync_service.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/sync/schemas.py app/sync/exceptions.py app/sync/service.py tests/test_sync_service.py
git commit -m "feat(sync): add sync job enqueue service"
```

---

### Task 4: Sync API endpoints

**Files:**
- Create: `backend/app/sync/router.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_sync_router.py`

**Interfaces:**
- Consumes: `app.sync.service.{enqueue_sync,get_job,get_latest_job}` (Task 3);
  `app.sync.exceptions.{SyncAlreadyRunning,SyncJobNotFound}` (Task 3);
  `app.sync.schemas.SyncJobRead` (Task 3); `app.auth.dependencies.get_current_user`
  (existing).
- Produces: `POST /api/v1/gmail/sync` (202/409), `GET /api/v1/gmail/sync/latest`
  (200, body may be `null`), `GET /api/v1/gmail/sync/{job_id}` (200/404).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_sync_router.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection

BASE = "/api/v1/gmail"


@pytest.fixture
def connected_gmail(db_session, user) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("access"),
        refresh_token_encrypted=encrypt_token("refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    return connection


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/sync"),
        ("get", "/sync/latest"),
        ("get", "/sync/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_sync_endpoints_require_authentication(client: TestClient, method, path) -> None:
    response = getattr(client, method)(f"{BASE}{path}")
    assert response.status_code == 401


def test_start_sync_returns_202_and_initial_job(auth_client: TestClient, connected_gmail) -> None:
    response = auth_client.post(f"{BASE}/sync")

    assert response.status_code == 202
    body = response.json()
    assert body["job_type"] == "initial"
    assert body["status"] == "queued"


def test_start_sync_requires_gmail_connection(auth_client: TestClient) -> None:
    response = auth_client.post(f"{BASE}/sync")
    assert response.status_code == 404


def test_start_sync_returns_409_with_existing_job_when_already_running(
    auth_client: TestClient, connected_gmail
) -> None:
    first = auth_client.post(f"{BASE}/sync")
    assert first.status_code == 202

    second = auth_client.post(f"{BASE}/sync")

    assert second.status_code == 409
    assert second.json()["id"] == first.json()["id"]


def test_get_sync_returns_the_job(auth_client: TestClient, connected_gmail) -> None:
    started = auth_client.post(f"{BASE}/sync").json()

    response = auth_client.get(f"{BASE}/sync/{started['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == started["id"]


def test_get_sync_404_for_unknown_job(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/sync/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_sync_latest_returns_null_when_no_jobs(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/sync/latest")
    assert response.status_code == 200
    assert response.json() is None


def test_get_sync_latest_returns_most_recent_job(auth_client: TestClient, connected_gmail) -> None:
    started = auth_client.post(f"{BASE}/sync").json()

    response = auth_client.get(f"{BASE}/sync/latest")

    assert response.status_code == 200
    assert response.json()["id"] == started["id"]


def test_cross_user_isolation(
    auth_client: TestClient, other_auth_client: TestClient, connected_gmail
) -> None:
    started = auth_client.post(f"{BASE}/sync").json()

    assert other_auth_client.get(f"{BASE}/sync/{started['id']}").status_code == 404
    assert other_auth_client.get(f"{BASE}/sync/latest").json() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_sync_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sync.router'`

- [ ] **Step 3: Write `app/sync/router.py`**

```python
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.sync import service
from app.sync.exceptions import SyncJobNotFound
from app.sync.schemas import SyncJobRead
from app.users.models import User

router = APIRouter(prefix="/gmail", tags=["sync"])


@router.post("/sync", response_model=SyncJobRead, status_code=status.HTTP_202_ACCEPTED)
def start_sync(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> SyncJobRead:
    return service.enqueue_sync(db, current_user.id)


@router.get("/sync/latest", response_model=SyncJobRead | None)
def get_latest_sync(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> SyncJobRead | None:
    return service.get_latest_job(db, current_user.id)


@router.get("/sync/{job_id}", response_model=SyncJobRead)
def get_sync(
    job_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SyncJobRead:
    job = service.get_job(db, current_user.id, job_id)
    if job is None:
        raise SyncJobNotFound(job_id)
    return job
```

- [ ] **Step 4: Register the router and exception handlers in `main.py`**

Modify `backend/app/main.py` — add imports:

```python
from app.sync.exceptions import SyncAlreadyRunning, SyncJobNotFound
from app.sync.router import router as sync_router
from app.sync.schemas import SyncJobRead
```

Add exception handlers (after the existing `handle_review_item_not_found` handler):

```python
    @app.exception_handler(SyncAlreadyRunning)
    async def handle_sync_already_running(request: Request, exc: SyncAlreadyRunning) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=SyncJobRead.model_validate(exc.job).model_dump(mode="json"),
        )

    @app.exception_handler(SyncJobNotFound)
    async def handle_sync_job_not_found(request: Request, exc: SyncJobNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
```

Add the router registration (after `app.include_router(pipeline_router, prefix="/api/v1")`):

```python
    app.include_router(sync_router, prefix="/api/v1")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_sync_router.py -v`
Expected: PASS

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && uv run pytest -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/sync/router.py app/main.py tests/test_sync_router.py
git commit -m "feat(sync): add /gmail/sync API endpoints"
```

---

### Task 5: Worker job claiming and poll loop

**Files:**
- Create: `backend/app/sync/worker.py`
- Create: `backend/tests/test_sync_worker.py`

**Interfaces:**
- Consumes: `app.sync.models.SyncJob` (Task 1); `app.db.session.SessionLocal`
  (existing).
- Produces: `app.sync.worker.claim_next_job(db: Session) -> SyncJob | None`,
  `app.sync.worker.run_forever(poll_interval: float = 2.0) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_sync_worker.py`:

```python
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.sync.models import SyncJob
from app.sync.worker import claim_next_job
from app.users.models import User


def _make_job(user_id, **overrides) -> SyncJob:
    defaults = dict(user_id=user_id, job_type="initial", window_start=date(2026, 1, 1))
    defaults.update(overrides)
    return SyncJob(**defaults)


def test_claim_next_job_returns_none_when_no_queued_jobs(db_session) -> None:
    assert claim_next_job(db_session) is None


def test_claim_next_job_claims_a_queued_job_and_marks_it_running(db_session, user) -> None:
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()

    claimed = claim_next_job(db_session)

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.started_at is not None


def test_claim_next_job_does_not_reset_started_at_if_already_set(db_session, user) -> None:
    original_start = datetime.now(timezone.utc) - timedelta(minutes=5)
    job = _make_job(user.id, started_at=original_start)
    db_session.add(job)
    db_session.commit()

    claimed = claim_next_job(db_session)

    assert claimed.started_at == original_start


def test_claim_next_job_ignores_jobs_not_yet_due_for_retry(db_session, user) -> None:
    job = _make_job(user.id, next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(job)
    db_session.commit()

    assert claim_next_job(db_session) is None


def test_claim_next_job_ignores_non_queued_jobs(db_session, user) -> None:
    job = _make_job(user.id, status="completed")
    db_session.add(job)
    db_session.commit()

    assert claim_next_job(db_session) is None


def test_claim_next_job_skips_a_row_locked_by_another_connection(engine) -> None:
    # FOR UPDATE SKIP LOCKED can only be exercised with two genuinely
    # separate, independently-committed connections — the standard
    # db_session fixture wraps everything in one uncommitted outer
    # transaction that a second connection could never see. Rows created
    # here are committed for real and cleaned up manually at the end.
    with engine.begin() as setup_conn:
        user_id = setup_conn.execute(
            User.__table__.insert()
            .values(google_sub="lock-test-sub", email="lock-test@example.com", name="Lock Test")
            .returning(User.__table__.c.id)
        ).scalar_one()
        job_id = setup_conn.execute(
            SyncJob.__table__.insert()
            .values(user_id=user_id, job_type="initial", window_start=date(2026, 1, 1))
            .returning(SyncJob.__table__.c.id)
        ).scalar_one()

    conn_a = engine.connect()
    txn_a = conn_a.begin()
    session_a = Session(bind=conn_a)
    try:
        session_a.execute(
            SyncJob.__table__.select().where(SyncJob.__table__.c.id == job_id).with_for_update()
        ).one()

        conn_b = engine.connect()
        session_b = Session(bind=conn_b)
        try:
            assert claim_next_job(session_b) is None
        finally:
            session_b.close()
            conn_b.close()
    finally:
        session_a.close()
        txn_a.rollback()
        conn_a.close()

    with engine.begin() as cleanup_conn:
        cleanup_conn.execute(delete(SyncJob).where(SyncJob.id == job_id))
        cleanup_conn.execute(delete(User).where(User.id == user_id))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_sync_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sync.worker'`

- [ ] **Step 3: Write `app/sync/worker.py` (claiming + poll loop only)**

```python
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.sync.models import SyncJob

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0


def claim_next_job(db: Session) -> SyncJob | None:
    job = db.scalars(
        select(SyncJob)
        .where(SyncJob.status == "queued", SyncJob.next_attempt_at <= datetime.now(timezone.utc))
        .order_by(SyncJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).one_or_none()
    if job is None:
        return None
    if job.started_at is None:
        job.started_at = datetime.now(timezone.utc)
    job.status = "running"
    db.commit()
    db.refresh(job)
    return job


def process_job(db: Session, job: SyncJob) -> None:
    # Filled in by Task 7 (message processing), Task 8 (retries), and
    # Task 9 (watermark update). Placeholder that fails loudly so an
    # incomplete worker never silently marks jobs as done.
    raise NotImplementedError


def run_forever(poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
    while True:
        db = SessionLocal()
        try:
            job = claim_next_job(db)
            if job is not None:
                process_job(db, job)
        finally:
            db.close()
        if job is None:
            time.sleep(poll_interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_sync_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/sync/worker.py tests/test_sync_worker.py
git commit -m "feat(sync): add worker job claiming and poll loop"
```

---

### Task 6: Extract reusable per-message processing from the pipeline

**Files:**
- Modify: `backend/app/pipeline/service.py`
- Modify: `backend/tests/test_pipeline_service.py`

**Interfaces:**
- Consumes: `app.pipeline.matching.find_candidate` (existing, unchanged);
  `app.classifier.extractor.{Extractor,ClassificationError}` (existing, unchanged).
- Produces: `app.pipeline.service.process_message(db: Session, user_id: UUID,
  extractor: Extractor, *, sync_job_id: UUID | None, message_id: str, summary: dict,
  body: str) -> str | None` — returns the `review_status`
  (`"auto_applied"`/`"pending_review"`/`"ignored"`), or `None` if the message was
  skipped (extraction or persistence failure).
- `process_inbox()` and `_apply_decision()` are **not removed or changed** in this task
  — `process_message()` is purely additive, calling the same unchanged
  `_apply_decision()`. `process_inbox()` is removed later, in Task 10, once the worker
  fully replaces it.

- [ ] **Step 1: Write the failing tests**

Create a new test file focused on `process_message` (this supersedes the
`process_inbox`-oriented tests in `test_pipeline_service.py`, which move to Task 10 once
`process_inbox` is removed). Add these tests to `backend/tests/test_pipeline_service.py`,
**above** the existing `process_inbox` tests (do not delete the existing tests yet —
they still pass, since `process_inbox` is untouched):

```python
def _summary(message_id: str = "m1", subject: str = "App received") -> dict:
    return {"id": message_id, "subject": subject, "from_": "jobs@acme.com", "date": "d", "snippet": "s"}


class _SingleResultExtractor:
    def __init__(self, extraction: EmailExtraction) -> None:
        self._extraction = extraction

    def classify_and_extract(self, *, subject, sender, date, body):
        return self._extraction


def test_process_message_ignores_non_job_email(db_session, user) -> None:
    extractor = _SingleResultExtractor(EmailExtraction(is_job_related=False, confidence=0.99))

    review_status = service.process_message(
        db_session, user.id, extractor,
        sync_job_id=None, message_id="m1", summary=_summary(), body="body",
    )

    assert review_status == "ignored"
    stored = db_session.query(ProcessedMessage).one()
    assert stored.review_status == "ignored"
    assert stored.sync_job_id is None


def test_process_message_auto_creates_a_new_application_when_confident(db_session, user) -> None:
    extractor = _SingleResultExtractor(
        EmailExtraction(is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied")
    )

    review_status = service.process_message(
        db_session, user.id, extractor,
        sync_job_id=None, message_id="m1", summary=_summary(), body="body",
    )

    assert review_status == "auto_applied"
    app = db_session.query(Application).one()
    assert app.company == "Acme"
    assert app.source == "gmail"


def test_process_message_always_queues_when_touching_a_manual_application(db_session, user) -> None:
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.applied, source="manual"
    )
    db_session.add(existing)
    db_session.commit()
    extractor = _SingleResultExtractor(
        EmailExtraction(is_job_related=True, confidence=0.99, company="Acme", position="SWE", status="interview")
    )

    review_status = service.process_message(
        db_session, user.id, extractor,
        sync_job_id=None, message_id="m1", summary=_summary(subject="Interview invite"), body="body",
    )

    assert review_status == "pending_review"
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.applied  # untouched


def test_process_message_records_the_owning_sync_job(db_session, user) -> None:
    job = SyncJob(user_id=user.id, job_type="initial", window_start=date(2026, 1, 1))
    db_session.add(job)
    db_session.commit()
    extractor = _SingleResultExtractor(EmailExtraction(is_job_related=False, confidence=0.99))

    service.process_message(
        db_session, user.id, extractor,
        sync_job_id=job.id, message_id="m1", summary=_summary(), body="body",
    )

    stored = db_session.query(ProcessedMessage).one()
    assert stored.sync_job_id == job.id


def test_process_message_returns_none_and_skips_on_persistence_failure(db_session, user) -> None:
    oversized_subject = "x" * 1000  # ProcessedMessage.subject is String(998)
    extractor = _SingleResultExtractor(
        EmailExtraction(is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied")
    )

    review_status = service.process_message(
        db_session, user.id, extractor,
        sync_job_id=None, message_id="m1", summary=_summary(subject=oversized_subject), body="body",
    )

    assert review_status is None
    assert db_session.query(ProcessedMessage).count() == 0
    assert db_session.query(Application).count() == 0
```

Add the required imports at the top of `backend/tests/test_pipeline_service.py`:

```python
from datetime import date

from app.sync.models import SyncJob
```

(alongside the existing imports — `date` joins the existing `from datetime import
datetime, timedelta, timezone` line).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_pipeline_service.py -k process_message -v`
Expected: FAIL — `AttributeError: module 'app.pipeline.service' has no attribute 'process_message'`

- [ ] **Step 3: Implement `process_message`**

Add to `backend/app/pipeline/service.py`, directly after the `process_inbox` function
(before `_apply_decision`):

```python
def process_message(
    db: Session,
    user_id: UUID,
    extractor: Extractor,
    *,
    sync_job_id: UUID | None,
    message_id: str,
    summary: dict,
    body: str,
) -> str | None:
    try:
        extraction = extractor.classify_and_extract(
            subject=summary["subject"], sender=summary["from_"], date=summary["date"], body=body
        )
    except ClassificationError:
        logger.warning("Skipping message %s: extraction failed", message_id)
        return None

    review_status, matched_application_id, proposed_action = _apply_decision(
        db, user_id, extraction
    )

    try:
        db.add(
            ProcessedMessage(
                user_id=user_id,
                sync_job_id=sync_job_id,
                gmail_message_id=message_id,
                subject=summary["subject"],
                sender=summary["from_"],
                message_date=summary["date"],
                snippet=summary["snippet"],
                is_job_related=extraction.is_job_related,
                confidence=extraction.confidence,
                extracted_company=extraction.company,
                extracted_position=extraction.position,
                extracted_status=extraction.status,
                extracted_status_date=extraction.status_date,
                matched_application_id=matched_application_id,
                proposed_action=proposed_action,
                review_status=review_status,
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Skipping message %s: failed to persist", message_id)
        return None

    return review_status
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_pipeline_service.py -v`
Expected: PASS (both the new `process_message` tests and the pre-existing
`process_inbox` tests, which are untouched).

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/pipeline/service.py tests/test_pipeline_service.py
git commit -m "feat(pipeline): extract process_message for reuse by the sync worker"
```

---

### Task 7: Worker message processing — pagination happy path

**Files:**
- Modify: `backend/app/sync/worker.py`
- Modify: `backend/tests/test_sync_worker.py`

**Interfaces:**
- Consumes: `app.pipeline.service.process_message` (Task 6);
  `app.gmail.google_api.{list_message_ids_page,get_message_summary,get_message_body,GoogleApiError}`
  (existing/Task 2); `app.gmail.service.{get_connection,get_valid_access_token}`
  (existing); `app.classifier.extractor.RuleBasedExtractor` (existing);
  `app.pipeline.models.ProcessedMessage` (existing).
- Produces: `app.sync.worker.process_job(db: Session, job: SyncJob, extractor:
  Extractor | None = None) -> None` (full implementation, replacing the Task 5
  placeholder — retries and watermark update are added in Tasks 8 and 9).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_sync_worker.py`:

```python
from app.classifier.schemas import EmailExtraction
from app.gmail import google_api
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.pipeline.models import ProcessedMessage
from app.sync.worker import process_job


class _FakeExtractor:
    def __init__(self, results: dict[str, EmailExtraction]) -> None:
        self._results = results

    def classify_and_extract(self, *, subject, sender, date, body):
        return self._results[subject]


def _connect_gmail(db_session, user) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("access"),
        refresh_token_encrypted=encrypt_token("refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def _make_summary(message_id: str, subject: str) -> dict:
    return {"id": message_id, "subject": subject, "from_": "jobs@acme.com", "date": "d", "snippet": "s"}


def test_process_job_pages_through_gmail_and_processes_each_message(
    db_session, user, monkeypatch
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()

    pages = [(["m1"], "page-2"), (["m2"], None)]

    def fake_list_page(token, *, query, page_token, max_results):
        return pages.pop(0)

    monkeypatch.setattr(google_api, "list_message_ids_page", fake_list_page)
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, f"Subject {mid}"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Subject m1": EmailExtraction(is_job_related=False, confidence=0.99),
            "Subject m2": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
            ),
        }
    )

    process_job(db_session, job, extractor)

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.messages_seen == 2
    assert job.messages_processed == 2
    assert job.ignored == 1
    assert job.auto_applied == 1
    assert job.page_token is None
    stored = {m.gmail_message_id: m for m in db_session.query(ProcessedMessage).all()}
    assert stored["m1"].review_status == "ignored"
    assert stored["m2"].review_status == "auto_applied"
    assert stored["m1"].sync_job_id == job.id


def test_process_job_checkpoints_page_token_after_each_page(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()

    seen_page_tokens = []

    def fake_list_page(token, *, query, page_token, max_results):
        seen_page_tokens.append(page_token)
        if page_token is None:
            return (["m1"], "page-2")
        return ([], None)

    monkeypatch.setattr(google_api, "list_message_ids_page", fake_list_page)
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Newsletter"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor({"Newsletter": EmailExtraction(is_job_related=False, confidence=0.99)})

    process_job(db_session, job, extractor)

    assert seen_page_tokens == [None, "page-2"]


def test_process_job_skips_messages_already_processed_by_an_earlier_attempt(
    db_session, user, monkeypatch
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    db_session.add(
        ProcessedMessage(
            user_id=user.id, gmail_message_id="m1", subject="s", sender="jobs@acme.com",
            message_date="d", snippet="s", is_job_related=False, confidence=0.9,
            review_status="ignored",
        )
    )
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["m1"], None))
    calls = []
    monkeypatch.setattr(
        google_api, "get_message_summary", lambda token, mid: calls.append(mid) or _make_summary(mid, "x")
    )
    extractor = _FakeExtractor({})

    process_job(db_session, job, extractor)

    assert calls == []  # never re-fetched
    db_session.refresh(job)
    assert job.status == "completed"
    assert job.messages_processed == 0


def test_process_job_fails_a_message_on_fetch_error_without_failing_the_job(
    db_session, user, monkeypatch
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["m1"], None))
    monkeypatch.setattr(
        google_api, "get_message_summary",
        lambda token, mid: (_ for _ in ()).throw(google_api.GoogleApiError("boom")),
    )
    extractor = _FakeExtractor({})

    process_job(db_session, job, extractor)

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.failed_count == 1
    assert job.messages_processed == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_sync_worker.py -k process_job -v`
Expected: FAIL — the placeholder `process_job` raises `NotImplementedError`.

- [ ] **Step 3: Implement `process_job` (happy path, no retries yet)**

Replace the placeholder `process_job` in `backend/app/sync/worker.py` and add the
needed imports:

```python
from sqlalchemy import select

from app.classifier.extractor import Extractor, RuleBasedExtractor
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.google_api import GoogleApiError
from app.pipeline import service as pipeline_service
from app.pipeline.models import ProcessedMessage
```

(add these alongside the existing imports at the top of the file)

```python
PAGE_SIZE = 100


def process_job(db: Session, job: SyncJob, extractor: Extractor | None = None) -> None:
    extractor = extractor or RuleBasedExtractor()
    connection = gmail_service.get_connection(db, job.user_id)
    if connection is None:
        job.status = "failed"
        job.error_message = "Gmail connection no longer exists"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    query = f"after:{job.window_start.strftime('%Y/%m/%d')}"
    access_token = gmail_service.get_valid_access_token(db, connection)

    while True:
        message_ids, next_page_token = google_api.list_message_ids_page(
            access_token, query=query, page_token=job.page_token, max_results=PAGE_SIZE
        )
        job.messages_seen += len(message_ids)

        for message_id in message_ids:
            already_processed = db.scalars(
                select(ProcessedMessage.id).where(
                    ProcessedMessage.user_id == job.user_id,
                    ProcessedMessage.gmail_message_id == message_id,
                )
            ).one_or_none()
            if already_processed is not None:
                continue

            try:
                summary = google_api.get_message_summary(access_token, message_id)
                body = google_api.get_message_body(access_token, message_id)
            except GoogleApiError:
                logger.warning("Skipping message %s: fetch failed", message_id)
                job.failed_count += 1
                job.messages_processed += 1
                db.commit()
                continue

            review_status = pipeline_service.process_message(
                db, job.user_id, extractor,
                sync_job_id=job.id, message_id=message_id, summary=summary, body=body,
            )
            job.messages_processed += 1
            if review_status is None:
                job.failed_count += 1
            elif review_status == "auto_applied":
                job.auto_applied += 1
            elif review_status == "pending_review":
                job.queued_for_review += 1
            else:
                job.ignored += 1
            db.commit()

        job.page_token = next_page_token
        db.commit()

        if next_page_token is None:
            break

    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_sync_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/sync/worker.py tests/test_sync_worker.py
git commit -m "feat(sync): wire worker pagination through to per-message processing"
```

---

### Task 8: Retry/backoff and job-level failure handling

**Files:**
- Modify: `backend/app/sync/worker.py`
- Modify: `backend/tests/test_sync_worker.py`

**Interfaces:**
- Produces: `process_job` now catches page-level `GoogleApiError`, requeues with
  backoff up to `job.max_attempts`, then marks `status="failed"`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_sync_worker.py`:

```python
def test_process_job_requeues_with_backoff_on_transient_gmail_error(
    db_session, user, monkeypatch
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()

    def fake_list_page(token, **kw):
        raise google_api.GoogleApiError("rate limited")

    monkeypatch.setattr(google_api, "list_message_ids_page", fake_list_page)

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.next_attempt_at > datetime.now(timezone.utc)


def test_process_job_fails_permanently_after_max_attempts(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id, attempts=2, max_attempts=3)
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        google_api, "list_message_ids_page",
        lambda token, **kw: (_ for _ in ()).throw(google_api.GoogleApiError("still down")),
    )

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_message == "still down"
    assert job.finished_at is not None


def test_process_job_retry_resumes_from_the_checkpointed_page_token(
    db_session, user, monkeypatch
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id, page_token="page-2")
    db_session.add(job)
    db_session.commit()

    seen_page_tokens = []

    def fake_list_page(token, *, query, page_token, max_results):
        seen_page_tokens.append(page_token)
        return ([], None)

    monkeypatch.setattr(google_api, "list_message_ids_page", fake_list_page)

    process_job(db_session, job, _FakeExtractor({}))

    assert seen_page_tokens == ["page-2"]  # resumed, did not restart from None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_sync_worker.py -k "backoff or max_attempts or resumes" -v`
Expected: FAIL — the current `process_job` lets `GoogleApiError` propagate uncaught out
of `list_message_ids_page`, so the test raises instead of asserting on job state.

- [ ] **Step 3: Wrap the pagination loop in retry handling**

Modify `process_job` in `backend/app/sync/worker.py` — wrap the `while True: ...`
pagination loop (everything from `while True:` down to the closing `if
next_page_token is None: break`) in a `try`/`except GoogleApiError`, and add a backoff
helper. The function becomes:

```python
from datetime import timedelta

BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600


def _backoff_seconds(attempts: int) -> float:
    return min(BASE_BACKOFF_SECONDS * (2**attempts), MAX_BACKOFF_SECONDS)


def process_job(db: Session, job: SyncJob, extractor: Extractor | None = None) -> None:
    extractor = extractor or RuleBasedExtractor()
    connection = gmail_service.get_connection(db, job.user_id)
    if connection is None:
        job.status = "failed"
        job.error_message = "Gmail connection no longer exists"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    query = f"after:{job.window_start.strftime('%Y/%m/%d')}"

    try:
        access_token = gmail_service.get_valid_access_token(db, connection)

        while True:
            message_ids, next_page_token = google_api.list_message_ids_page(
                access_token, query=query, page_token=job.page_token, max_results=PAGE_SIZE
            )
            job.messages_seen += len(message_ids)

            for message_id in message_ids:
                already_processed = db.scalars(
                    select(ProcessedMessage.id).where(
                        ProcessedMessage.user_id == job.user_id,
                        ProcessedMessage.gmail_message_id == message_id,
                    )
                ).one_or_none()
                if already_processed is not None:
                    continue

                try:
                    summary = google_api.get_message_summary(access_token, message_id)
                    body = google_api.get_message_body(access_token, message_id)
                except GoogleApiError:
                    logger.warning("Skipping message %s: fetch failed", message_id)
                    job.failed_count += 1
                    job.messages_processed += 1
                    db.commit()
                    continue

                review_status = pipeline_service.process_message(
                    db, job.user_id, extractor,
                    sync_job_id=job.id, message_id=message_id, summary=summary, body=body,
                )
                job.messages_processed += 1
                if review_status is None:
                    job.failed_count += 1
                elif review_status == "auto_applied":
                    job.auto_applied += 1
                elif review_status == "pending_review":
                    job.queued_for_review += 1
                else:
                    job.ignored += 1
                db.commit()

            job.page_token = next_page_token
            db.commit()

            if next_page_token is None:
                break
    except GoogleApiError as exc:
        job.attempts += 1
        if job.attempts < job.max_attempts:
            job.status = "queued"
            job.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                seconds=_backoff_seconds(job.attempts)
            )
        else:
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_sync_worker.py -v`
Expected: PASS (all worker tests, including Task 7's happy-path tests, which must
still pass unchanged).

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/sync/worker.py tests/test_sync_worker.py
git commit -m "feat(sync): add retry/backoff for transient Gmail API failures"
```

---

### Task 9: Incremental sync watermark update

**Files:**
- Modify: `backend/app/sync/worker.py`
- Modify: `backend/tests/test_sync_worker.py`

**Interfaces:**
- Produces: `process_job`, on successful completion, sets
  `connection.last_synced_message_date = job.started_at.date()`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_sync_worker.py`:

```python
def test_process_job_sets_the_connection_watermark_on_success(db_session, user, monkeypatch) -> None:
    connection = _connect_gmail(db_session, user)
    job = _make_job(user.id)
    job.started_at = datetime(2026, 6, 15, tzinfo=timezone.utc)
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: ([], None))

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(connection)
    assert connection.last_synced_message_date == date(2026, 6, 15)


def test_process_job_does_not_advance_the_watermark_on_failure(db_session, user, monkeypatch) -> None:
    connection = _connect_gmail(db_session, user)
    job = _make_job(user.id, attempts=2, max_attempts=3)
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        google_api, "list_message_ids_page",
        lambda token, **kw: (_ for _ in ()).throw(google_api.GoogleApiError("down")),
    )

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(connection)
    assert connection.last_synced_message_date is None


def test_enqueue_sync_uses_the_watermark_set_by_a_prior_successful_job(
    db_session, user, monkeypatch
) -> None:
    from app.sync import service as sync_service

    connection = _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    # process_job sets started_at itself only via claim_next_job; set it directly
    # here since this test drives process_job without going through claim_next_job.
    job.started_at = datetime(2026, 6, 15, tzinfo=timezone.utc)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: ([], None))
    process_job(db_session, job, _FakeExtractor({}))

    next_job = sync_service.enqueue_sync(db_session, user.id)

    assert next_job.job_type == "incremental"
    assert next_job.window_start == date(2026, 6, 14)  # 06-15 minus the 1-day margin
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_sync_worker.py -k watermark -v`
Expected: FAIL — `connection.last_synced_message_date` stays `None` after a successful
job, since `process_job` doesn't set it yet.

- [ ] **Step 3: Set the watermark on successful completion**

Modify `process_job` in `backend/app/sync/worker.py` — change the final three lines
(the success path after the `try`/`except` block) from:

```python
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
```

to:

```python
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    connection.last_synced_message_date = job.started_at.date()
    db.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_sync_worker.py tests/test_sync_service.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/sync/worker.py tests/test_sync_worker.py
git commit -m "feat(sync): advance the connection watermark on successful sync"
```

---

### Task 10: Remove the old synchronous Process Inbox path

**Files:**
- Modify: `backend/app/pipeline/service.py`
- Modify: `backend/app/pipeline/router.py`
- Modify: `backend/tests/test_pipeline_service.py`
- Modify: `backend/tests/test_pipeline_router.py`

**Interfaces:**
- Removes: `app.pipeline.service.process_inbox`, `POST /api/v1/pipeline/process`.
- `process_message`, `_apply_decision`, `list_review_queue`, `approve_review_item`,
  `reject_review_item` and their endpoints are unaffected.

- [ ] **Step 1: Remove `process_inbox` from `pipeline/service.py`**

Modify `backend/app/pipeline/service.py` — delete the entire `process_inbox` function
(from `def process_inbox(...)` down through its `return ProcessResult(...)`, i.e. the
whole function that currently sits above `_apply_decision`). Also remove the now-unused
imports it required: `google_api` (the module import, since `process_message` never
calls `google_api` directly — it receives `summary`/`body` already fetched) and
`ProcessResult` from `app.pipeline.schemas` if `ProcessResult` is no longer referenced
anywhere else in the file. Check with:

```bash
cd backend && grep -n "ProcessResult\|google_api" app/pipeline/service.py
```

Remove any import line whose only remaining use was inside the deleted function.

- [ ] **Step 2: Remove the `/process` endpoint from `pipeline/router.py`**

Modify `backend/app/pipeline/router.py` — delete the `pipeline_process` endpoint:

```python
@router.post("/process", response_model=ProcessResult)
def pipeline_process(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> ProcessResult:
    return service.process_inbox(db, current_user.id, RuleBasedExtractor())
```

Remove the now-unused `RuleBasedExtractor` import and `ProcessResult` from the
`app.pipeline.schemas` import line (keep `ReviewDecision, ReviewItem`).

- [ ] **Step 3: Remove the obsolete tests**

Modify `backend/tests/test_pipeline_service.py` — delete every test function that calls
`service.process_inbox(...)` (all of the tests below the `process_message` tests added
in Task 6: `test_process_inbox_raises_when_not_connected` through
`test_process_inbox_is_idempotent`). Also remove the `_FakeExtractor` class defined at
the top of the file if it becomes unused (the `process_message` tests use
`_SingleResultExtractor` instead), and remove `_connect_gmail`/`_make_summary` helpers
if nothing else in the file references them — check with:

```bash
cd backend && grep -n "_FakeExtractor\|_connect_gmail\|_make_summary" tests/test_pipeline_service.py
```

Modify `backend/tests/test_pipeline_router.py` — remove `test_process_endpoint_returns_summary`
and `test_process_endpoint_requires_gmail_connection`, and remove the `("post",
"/process")` case from the `test_pipeline_endpoints_require_authentication`
parametrize list. Remove the now-unused `classifier_extractor` and `EmailExtraction`
imports and the `connected_gmail` fixture if nothing else in the file uses them —
check with:

```bash
cd backend && grep -n "connected_gmail\|classifier_extractor\|EmailExtraction" tests/test_pipeline_router.py
```

(`connected_gmail` is likely still unused elsewhere in this file and should be
removed along with its fixture definition; the review-queue tests don't need a
live Gmail connection.)

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS — no test references `process_inbox` or `POST /pipeline/process`
anymore, and every other test (models, matching, review queue, sync, evaluation
accuracy) still passes.

- [ ] **Step 5: Verify nothing else references the removed code**

Run: `cd backend && grep -rn "process_inbox\|pipeline/process" app/ tests/`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/pipeline/service.py app/pipeline/router.py tests/test_pipeline_service.py tests/test_pipeline_router.py
git commit -m "refactor(pipeline): remove the synchronous Process Inbox path, superseded by sync jobs"
```

---

### Task 11: Frontend — Sync Gmail UI

**Files:**
- Create: `frontend/src/types/sync.ts`
- Create: `frontend/src/api/sync.ts`
- Create: `frontend/src/components/SyncPanel.tsx`
- Modify: `frontend/src/components/ApplicationsPage.tsx`
- Modify: `frontend/src/components/ReviewQueue.tsx`
- Modify: `frontend/src/api/pipeline.ts`
- Modify: `frontend/src/types/pipeline.ts`

**Interfaces:**
- Produces: `SyncJob` type; `startSync(): Promise<SyncJob>`, `getSyncJob(id:
  string): Promise<SyncJob>`, `getLatestSyncJob(): Promise<SyncJob | null>`;
  `<SyncPanel onSyncCompleted={() => void}>`.
- Modifies: `<ReviewQueue refreshSignal={number} ...>` (new required prop).
- Removes: `processInbox()` from `api/pipeline.ts`, `ProcessResult` from
  `types/pipeline.ts` (both now unused).

- [ ] **Step 1: Add the `SyncJob` type**

Create `frontend/src/types/sync.ts`:

```typescript
export interface SyncJob {
  id: string
  job_type: 'initial' | 'incremental'
  status: 'queued' | 'running' | 'completed' | 'failed'
  attempts: number
  window_start: string
  messages_seen: number
  messages_processed: number
  auto_applied: number
  queued_for_review: number
  ignored: number
  failed_count: number
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}
```

- [ ] **Step 2: Add the sync API client**

Create `frontend/src/api/sync.ts`:

```typescript
import { parseResponse } from './http'
import type { SyncJob } from '../types/sync'

const BASE = '/api/v1/gmail'

async function parseSyncStartResponse(response: Response): Promise<SyncJob> {
  // 202 (newly started) and 409 (already running) both carry a SyncJob body —
  // only a genuine error should throw.
  if (response.status === 202 || response.status === 409) {
    return (await response.json()) as SyncJob
  }
  return parseResponse<SyncJob>(response)
}

export function startSync(): Promise<SyncJob> {
  return fetch(`${BASE}/sync`, { method: 'POST' }).then(parseSyncStartResponse)
}

export function getSyncJob(id: string): Promise<SyncJob> {
  return fetch(`${BASE}/sync/${id}`).then((r) => parseResponse<SyncJob>(r))
}

export function getLatestSyncJob(): Promise<SyncJob | null> {
  return fetch(`${BASE}/sync/latest`).then((r) => parseResponse<SyncJob | null>(r))
}
```

- [ ] **Step 3: Add `refreshSignal` to `ReviewQueue`**

Modify `frontend/src/components/ReviewQueue.tsx`:

```typescript
interface Props {
  applications: Application[]
  onApplicationsChanged: () => void
  refreshSignal: number
}
```

(replaces the existing two-field `Props` interface)

```typescript
export function ReviewQueue({ applications, onApplicationsChanged, refreshSignal }: Props) {
```

(replaces the existing destructured parameter list)

```typescript
  useEffect(refresh, [refreshSignal])
```

(replaces the existing `useEffect(refresh, [])`)

- [ ] **Step 4: Create `SyncPanel`**

Create `frontend/src/components/SyncPanel.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'

import { getLatestSyncJob, getSyncJob, startSync } from '../api/sync'
import type { SyncJob } from '../types/sync'

const POLL_INTERVAL_MS = 2000

interface Props {
  onSyncCompleted: () => void
}

export function SyncPanel({ onSyncCompleted }: Props) {
  const [job, setJob] = useState<SyncJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const pollJob = (id: string) => {
    stopPolling()
    pollRef.current = window.setInterval(() => {
      getSyncJob(id)
        .then((updated) => {
          setJob(updated)
          if (updated.status === 'completed' || updated.status === 'failed') {
            stopPolling()
            if (updated.status === 'completed') onSyncCompleted()
          }
        })
        .catch((err) => {
          stopPolling()
          setError(err instanceof Error ? err.message : 'Failed to check sync status')
        })
    }, POLL_INTERVAL_MS)
  }

  useEffect(() => {
    getLatestSyncJob()
      .then((latest) => {
        setJob(latest)
        if (latest && (latest.status === 'queued' || latest.status === 'running')) {
          pollJob(latest.id)
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load sync status'))

    return stopPolling
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSync = async () => {
    setError(null)
    try {
      const started = await startSync()
      setJob(started)
      pollJob(started.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start sync')
    }
  }

  const isActive = job?.status === 'queued' || job?.status === 'running'

  return (
    <section className="space-y-2 rounded border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold text-gray-900">Gmail Sync</h2>
        <button
          onClick={handleSync}
          disabled={isActive}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {isActive ? 'Syncing…' : 'Sync Gmail'}
        </button>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {job && isActive && (
        <p className="text-sm text-gray-600">
          {job.job_type === 'initial' ? 'Initial sync' : 'Incremental sync'} running —{' '}
          {job.messages_processed} of {job.messages_seen || '?'} messages processed.
        </p>
      )}
      {job && job.status === 'completed' && (
        <p className="text-sm text-gray-600">
          Synced: {job.auto_applied} auto-applied, {job.queued_for_review} queued for review,{' '}
          {job.ignored} ignored{job.failed_count > 0 ? `, ${job.failed_count} failed` : ''}.
        </p>
      )}
      {job && job.status === 'failed' && (
        <p className="text-sm text-red-700">Sync failed: {job.error_message}</p>
      )}
    </section>
  )
}
```

- [ ] **Step 5: Wire `SyncPanel` into `ApplicationsPage`**

Modify `frontend/src/components/ApplicationsPage.tsx` — replace the whole file
content with:

```tsx
import { useState } from 'react'

import { useApplications } from '../hooks/useApplications'
import type { Application } from '../types/application'
import type { User } from '../types/user'
import { ApplicationForm } from './ApplicationForm'
import { ApplicationList } from './ApplicationList'
import { GmailPanel } from './GmailPanel'
import { ReviewQueue } from './ReviewQueue'
import { SyncPanel } from './SyncPanel'
import { UserMenu } from './UserMenu'

interface Props {
  user: User
  onLogout: () => void
}

export function ApplicationsPage({ user, onLogout }: Props) {
  const { applications, loading, error, refetch, create, update, remove } = useApplications()
  const [editing, setEditing] = useState<Application | null>(null)
  const [reviewRefreshSignal, setReviewRefreshSignal] = useState(0)

  const handleSyncCompleted = () => {
    void refetch()
    setReviewRefreshSignal((n) => n + 1)
  }

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

      <GmailPanel />

      <SyncPanel onSyncCompleted={handleSyncCompleted} />

      <ReviewQueue
        applications={applications}
        onApplicationsChanged={() => void refetch()}
        refreshSignal={reviewRefreshSignal}
      />

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

- [ ] **Step 6: Remove the now-unused Process Inbox frontend code**

Modify `frontend/src/api/pipeline.ts` — remove the `processInbox` function and the now
unused `ProcessResult` import:

```typescript
import { parseResponse } from './http'
import type { Application } from '../types/application'
import type { ReviewDecision, ReviewItem } from '../types/pipeline'

const BASE = '/api/v1/pipeline'

export function getReviewQueue(): Promise<ReviewItem[]> {
  return fetch(`${BASE}/review`).then((r) => parseResponse<ReviewItem[]>(r))
}

export function approveReviewItem(id: string, edits: ReviewDecision = {}): Promise<Application> {
  return fetch(`${BASE}/review/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(edits),
  }).then((r) => parseResponse<Application>(r))
}

export function rejectReviewItem(id: string): Promise<void> {
  return fetch(`${BASE}/review/${id}/reject`, { method: 'POST' }).then((r) => parseResponse<void>(r))
}
```

Modify `frontend/src/types/pipeline.ts` — remove the `ProcessResult` interface,
keeping `ReviewItem` and `ReviewDecision`:

```typescript
export interface ReviewItem {
  id: string
  subject: string
  sender: string
  snippet: string
  confidence: number
  proposed_action: 'create' | 'update' | null
  matched_application_id: string | null
  extracted_company: string | null
  extracted_position: string | null
  extracted_status: string | null
  extracted_status_date: string | null
  created_at: string
}

export interface ReviewDecision {
  company?: string
  position?: string
  status?: string
  status_date?: string
}
```

- [ ] **Step 7: Type-check and lint**

Run: `cd frontend && npx tsc -b && npx oxlint`
Expected: both succeed with zero errors (0 new warnings beyond the pre-existing 3 in
`AuthContext.tsx`/`useApplications.ts` noted in `CLAUDE.md`).

- [ ] **Step 8: Manual browser verification**

With the backend, worker (`uv run python -m app.sync.worker`), and frontend
(`npm run dev -- --port 5178 --strictPort`) all running against a real connected Gmail
account:

1. Click "Sync Gmail" — button becomes disabled and shows "Syncing…", progress text
   appears and updates every ~2s.
2. Refresh the browser page mid-sync — polling resumes automatically (via
   `/gmail/sync/latest`) without needing to click Sync again.
3. Click "Sync Gmail" again while the first sync is still running — no duplicate job
   is created; the panel keeps showing the same job's progress.
4. Wait for completion — the summary line appears, the applications list refreshes,
   and any new review-queue items appear **without a manual page reload** (verifying
   the `refreshSignal` fix).
5. Click "Sync Gmail" a second time after completion — this time it enqueues an
   incremental job (confirm via the network tab or backend logs showing
   `job_type=incremental`) and completes quickly with few or zero new messages.

- [ ] **Step 9: Commit**

```bash
cd frontend
git add src/types/sync.ts src/api/sync.ts src/components/SyncPanel.tsx \
  src/components/ApplicationsPage.tsx src/components/ReviewQueue.tsx \
  src/api/pipeline.ts src/types/pipeline.ts
git commit -m "feat(frontend): replace Process Inbox with polling-based Gmail sync"
```

---

### Task 12: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS, including `tests/test_evaluation_accuracy.py` (classifier is untouched
by this phase).

- [ ] **Step 2: Run backend type/lint checks used elsewhere in this plan's CI-equivalent steps**

Run: `cd backend && uv run python -c "import app.main"` (sanity import check — catches
any leftover reference to removed code that pytest's test collection might not exercise
directly, e.g. an unused import left behind).
Expected: no error.

- [ ] **Step 3: Run frontend checks**

Run: `cd frontend && npx tsc -b && npx oxlint`
Expected: PASS, 0 errors.

- [ ] **Step 4: Manual end-to-end verification against a real Gmail account**

1. Disconnect and reconnect Gmail (or use a fresh account) so
   `last_synced_message_date` is `NULL`.
2. Start the worker: `cd backend && uv run python -m app.sync.worker`.
3. Click "Sync Gmail" in the UI — confirm it runs as `job_type=initial`, paginates
   (visible via `messages_seen` climbing past 100 if the mailbox has more than one
   page within the 180-day window), and completes with a sensible summary.
4. Confirm new applications and review-queue items match what's actually in the
   mailbox (spot-check a few).
5. Click "Sync Gmail" again — confirm it runs as `job_type=incremental` and does not
   create duplicate applications or duplicate review-queue items for mail already
   seen (idempotency).
6. Stop the worker process mid-sync (Ctrl+C) during a fresh initial sync on a large
   mailbox, then restart it — confirm the job resumes (via `claim_next_job` picking
   the still-`queued`-or-stuck-`running` job back up... note: if the worker is killed
   while a job is `status="running"`, that job will never be re-claimed automatically,
   since `claim_next_job` only claims `status="queued"` rows. This is a known,
   accepted limitation of this phase — worth flagging to the user, not silently
   working around it).

- [ ] **Step 5: Report the mid-sync-crash limitation found in Step 4.6**

If Step 4.6 confirms a worker crash mid-`running` job leaves it permanently stuck
(never retried, since only `queued` rows are claimed), tell the user this is a real gap
worth a follow-up (e.g., a periodic reaper that requeues `running` jobs whose
`updated_at` is older than some staleness threshold) rather than fixing it
unprompted — it wasn't in the approved spec and deserves its own design discussion.
