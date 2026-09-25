# Phase 10 Production Hardening & Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployed app fail clearly, recover on its own, and alert the maintainer when it can't, with no new infrastructure.

**Architecture:** Adds error classes in `app/gmail/google_api.py`, a reauthorization state on `GmailConnection`, time-sliced `process_job` with self-re-dispatching lane workflows, a scheduled monitor workflow whose failed run is the alert, an in-memory per-user rate limiter, and static-site security headers. Queue, lanes, classifier, matching, review queue and CRUD are unchanged.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Postgres (Neon), httpx, pytest; React 18 + TypeScript; GitHub Actions; Render.

**Spec:** `docs/superpowers/specs/2026-09-25-job-tracker-phase-10-production-hardening-design.md`

## Global Constraints

- No new hosting, services, paid tiers or Python/npm dependencies.
- No changes under `backend/app/classifier/`, `backend/app/pipeline/matching.py`, `backend/app/applications/`, or to pipeline decision logic.
- `evaluation.compare` output must be identical to before Phase 10.
- Public (GitHub Actions) logs must never contain raw Gmail message IDs, Gmail message URLs, emails, subjects or companies.
- User-facing error text never includes raw exception text.
- Migration 0007 is deployed **alone** (no model/code referencing its columns) and verified in production before CP2b is pushed.
- Initial slice default `1500` s, incremental `1200` s, orphan threshold `120` s — all env-overridable settings.
- Reauth HTTP response: **403** `{"detail": ..., "code": "gmail_reauth_required"}`; 409 stays reserved for "already running" with a `SyncJob` body.
- Rate limits: `POST /gmail/sync` 10/60s per user; `GET /gmail/messages` 5/60s per user; `GET /gmail/connect` 5/60s per user; `GET /auth/google/login` 20/60s per client IP. Dispatch re-kick ≤ 1 per job per 30s.
- No Google Cloud changes in Phase 10 (CP0 chose option (a); CP7 dropped). Render dashboard changes only in CP5 (headers), guided step by step.
- Every checkpoint: full local checks (`cd backend && uv run pytest && uv run python -m evaluation.compare`; `cd frontend && npx tsc -b && npx oxlint && npm run build`) → commit → push to `main` → CI green → production verification.

## Review Focus

1. **Revocation mid-page** — a 401 on a single message fetch must abort the job as reauth, not be skipped as a per-message failure and repeated for every message. Pinned in Task 2b.2 (`test_process_job_treats_a_401_on_a_message_fetch_as_reauth`).
2. **Yield on the final page** — a job whose deadline passes on its last page must complete (and set the watermark), not requeue an empty job. Pinned in Task 3.1 (`test_process_job_completes_instead_of_yielding_on_the_last_page`).
3. **Reconnect while a failed-reauth job is the latest** — after reconnect, the next Sync must enqueue an incremental job, not 403. Pinned in Task 2b.1 (`test_connect_clears_reauth_and_keeps_the_watermark`) and 2b.3 (`test_enqueue_after_reconnect_is_incremental`).
4. **Sync polling never rate-limited** — `GET /gmail/sync/{id}` and `/sync/latest` hammered in a tight loop never return 429. Pinned in Task 5.1 (`test_polling_endpoints_are_never_rate_limited`).
5. **Monitor on a fresh deploy** — historical failed jobs must not alert (backfill), and a yielded job due seconds ago must not trip M1. Pinned in Task 2a.1 (backfill) and Task 4.1 (`test_m1_ignores_a_just_yielded_job`).

---

## CP0 — Google consent-screen investigation (read-only, maintainer-guided)

### Task 0.1: Record console state and requirements; write findings; stop

**Files:**
- Modify: `docs/superpowers/specs/2026-09-25-job-tracker-phase-10-production-hardening-design.md` (append `## 11. CP0 findings`)

- [ ] **Step 1:** Guide the maintainer, one screen at a time, to Google Cloud Console → Google Auth Platform → **Audience**: record user type, publishing status, test-user count. → **Data Access**: record configured scopes. → **Branding**: record app name, support email, homepage / privacy-policy / authorized-domain fields (set or empty). **No edits.**
- [ ] **Step 2:** Re-fetch Google docs (OAuth 2.0 refresh-token expiration; "When is verification not needed"; unverified-apps page; Gmail scopes page) and record quotes for: 7-day Testing expiry; `gmail.readonly` restricted; personal-use exemption (<100 users); unverified-app screen; what "Publish app" requires when unverified; token behavior after a status change (record "not documented" if so).
- [ ] **Step 3:** Append findings + options memo (a) stay Testing / (b) Production unverified personal-use (needs CP7 allowlist first) / (c) full verification + CASA, with recommendation, to the spec as §11. Commit `docs: record Phase 10 CP0 consent-screen findings`.
- [ ] **Step 4:** **STOP** — ask the maintainer to choose (a)/(b)/(c). CP7 runs only on (b).

---

## CP1 — Error classification & log hygiene (no migration, behavior-neutral for auth)

### Task 1.1: `GoogleApiError.status_code`, `GmailAuthError`, network-error wrapping

**Files:**
- Modify: `backend/app/gmail/google_api.py`
- Test: `backend/tests/test_gmail_google_api.py`

**Interfaces:**
- Produces: `GoogleApiError(message: str, status_code: int | None = None)` with `.status_code`; `class GmailAuthError(GoogleApiError)`; every public `google_api` function raises `GmailAuthError` on HTTP 401, `GmailAuthError` on refresh `400 invalid_grant`, `GoogleApiError(status_code=None)` on `httpx.TransportError`, `GoogleApiError(status_code=<code>)` on other non-200.

- [ ] **Step 1: Write failing tests** (append to `test_gmail_google_api.py`):

```python
def test_non_200_errors_carry_the_status_code(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(503, {}))
    with pytest.raises(google_api.GoogleApiError) as exc_info:
        google_api.list_message_ids_page("t", query="q", page_token=None, max_results=1)
    assert exc_info.value.status_code == 503
    assert not isinstance(exc_info.value, google_api.GmailAuthError)


def test_refresh_access_token_raises_auth_error_on_invalid_grant(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(400, {"error": "invalid_grant"})
    )
    with pytest.raises(google_api.GmailAuthError) as exc_info:
        google_api.refresh_access_token(client_id="c", client_secret="s", refresh_token="r")
    assert exc_info.value.status_code == 400


def test_refresh_access_token_invalid_client_is_not_an_auth_error(monkeypatch) -> None:
    # A wrong client secret is an operator problem, not the user's grant.
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(401, {"error": "invalid_client"})
    )
    with pytest.raises(google_api.GoogleApiError) as exc_info:
        google_api.refresh_access_token(client_id="c", client_secret="s", refresh_token="r")
    assert not isinstance(exc_info.value, google_api.GmailAuthError)


@pytest.mark.parametrize(
    "call",
    [
        lambda: google_api.list_message_ids_page("t", query="q", page_token=None, max_results=1),
        lambda: google_api.get_message("t", "m1"),
        lambda: google_api.get_profile("t"),
    ],
)
def test_gmail_api_401_raises_auth_error(monkeypatch, call) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(401, {}))
    with pytest.raises(google_api.GmailAuthError):
        call()


@pytest.mark.parametrize("exc_type", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_errors_become_google_api_errors_without_a_status(monkeypatch, exc_type) -> None:
    def boom(*a, **k):
        raise exc_type("network down")

    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx, "post", boom)
    for call in (
        lambda: google_api.get_message("t", "m1"),
        lambda: google_api.list_message_ids_page("t", query="q", page_token=None, max_results=1),
        lambda: google_api.refresh_access_token(client_id="c", client_secret="s", refresh_token="r"),
    ):
        with pytest.raises(google_api.GoogleApiError) as exc_info:
            call()
        assert exc_info.value.status_code is None
        assert "gmail.googleapis.com" not in str(exc_info.value)
```

- [ ] **Step 2:** Run `cd backend && uv run pytest tests/test_gmail_google_api.py -q` → new tests FAIL (`GmailAuthError` missing, `status_code` missing, `ReadTimeout` propagates).
- [ ] **Step 3: Implement** in `google_api.py`:

```python
class GoogleApiError(Exception):
    """A Google/Gmail call failed: a non-2xx response (status_code set) or a
    network-level failure (status_code None). The message carries only the
    operation and the status/error class — never a URL, token or message ID."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GmailAuthError(GoogleApiError):
    """The user's Gmail grant is no longer usable (revoked, expired, or the
    stored token is unreadable) — retrying can't help, only a reconnect can."""


def _send(method: str, url: str, operation: str, **kwargs) -> httpx.Response:
    try:
        return getattr(httpx, method)(url, **kwargs)
    except httpx.TransportError as exc:
        raise GoogleApiError(f"{operation} failed: network error ({type(exc).__name__})") from exc


def _raise_for_status(response, operation: str) -> None:
    if response.status_code == 200:
        return
    if response.status_code == 401:
        raise GmailAuthError(f"{operation} failed: 401", status_code=401)
    raise GoogleApiError(f"{operation} failed: {response.status_code}", status_code=response.status_code)
```

Each existing function switches `httpx.get(...)`/`httpx.post(...)` to `_send("get"|"post", url, "<operation>", ...)` and its `if response.status_code != 200: raise ...` to `_raise_for_status(response, "<operation>")`, keeping the existing operation names ("message list", "message fetch", "profile fetch", "token revoke"). `refresh_access_token` is special:

```python
    response = _send("post", TOKEN_URL, "token refresh", data={...}, timeout=_TIMEOUT)
    if response.status_code != 200:
        if response.status_code == 400 and _oauth_error(response) == "invalid_grant":
            raise GmailAuthError("token refresh failed: invalid_grant", status_code=400)
        raise GoogleApiError(f"token refresh failed: {response.status_code}", status_code=response.status_code)
    return response.json()


def _oauth_error(response) -> str | None:
    try:
        return response.json().get("error")
    except Exception:
        return None
```

(The refresh endpoint's 401 `invalid_client` must NOT go through `_raise_for_status`, which would turn it into `GmailAuthError`.)

- [ ] **Step 4:** Run `uv run pytest tests/test_gmail_google_api.py tests/test_gmail_service.py tests/test_gmail_router.py -q` → PASS.

### Task 1.2: Per-message single retry with status logging

**Files:**
- Modify: `backend/app/sync/worker.py`
- Test: `backend/tests/test_sync_worker.py`

**Interfaces:**
- Consumes: `GoogleApiError.status_code` (Task 1.1).
- Produces: `worker.PER_MESSAGE_RETRY_SECONDS: float = 2.0`; `worker._fetch_message(access_token, message_id) -> tuple[dict, str]`.

- [ ] **Step 1: Update existing tests** that raise `GoogleApiError("boom")` from `get_message` (`test_process_job_fails_a_message_on_fetch_error_without_failing_the_job`, `test_fetch_failure_log_line_does_not_contain_the_raw_message_id`) to `GoogleApiError("boom", status_code=404)` so they keep testing the non-transient skip path without a real 2s sleep.
- [ ] **Step 2: Write failing tests:**

```python
def _no_sleep(monkeypatch) -> list[float]:
    import app.sync.worker as worker_module
    sleeps: list[float] = []
    monkeypatch.setattr(worker_module.time, "sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("status_code", [None, 429, 503])
def test_process_job_retries_a_transiently_failed_message_once(db_session, user, monkeypatch, status_code) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    sleeps = _no_sleep(monkeypatch)
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["m1"], None))
    calls = []

    def flaky(token, mid):
        calls.append(mid)
        if len(calls) == 1:
            raise google_api.GoogleApiError("blip", status_code=status_code)
        return _make_summary(mid, "Subject m1"), "body"

    monkeypatch.setattr(google_api, "get_message", flaky)
    extractor = _FakeExtractor({"Subject m1": EmailExtraction(is_job_related=False, confidence=0.99)})

    process_job(db_session, job, extractor)

    db_session.refresh(job)
    assert calls == ["m1", "m1"]
    assert sleeps == [2.0]
    assert job.status == "completed"
    assert job.attempts == 0
    assert job.failed_count == 0
    assert db_session.query(ProcessedMessage).filter_by(gmail_message_id="m1").count() == 1


def test_process_job_skips_a_message_after_two_transient_failures_without_using_an_attempt(
    db_session, user, monkeypatch
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    _no_sleep(monkeypatch)
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["m1"], None))
    monkeypatch.setattr(
        google_api, "get_message",
        lambda token, mid: (_ for _ in ()).throw(google_api.GoogleApiError("down", status_code=None)),
    )

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.attempts == 0
    assert job.failed_count == 1


def test_process_job_does_not_retry_a_non_transient_message_error(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    sleeps = _no_sleep(monkeypatch)
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["m1"], None))
    calls = []

    def gone(token, mid):
        calls.append(mid)
        raise google_api.GoogleApiError("gone", status_code=404)

    monkeypatch.setattr(google_api, "get_message", gone)

    process_job(db_session, job, _FakeExtractor({}))

    assert calls == ["m1"]
    assert sleeps == []


def test_fetch_failure_log_line_includes_the_status_but_not_the_raw_id(
    db_session, user, monkeypatch, caplog
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    _no_sleep(monkeypatch)
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["rawid456"], None))
    monkeypatch.setattr(
        google_api, "get_message",
        lambda token, mid: (_ for _ in ()).throw(google_api.GoogleApiError("x", status_code=503)),
    )
    monkeypatch.setattr(logging.getLogger("app.sync.worker"), "disabled", False)

    with caplog.at_level("WARNING"):
        process_job(db_session, job, extractor=_FakeExtractor({}))

    assert "HTTP 503" in caplog.text
    assert "rawid456" not in caplog.text
```

- [ ] **Step 3:** Run → FAIL (no retry; no status in log).
- [ ] **Step 4: Implement** in `worker.py`:

```python
PER_MESSAGE_RETRY_SECONDS = 2.0


def _is_transient(exc: GoogleApiError) -> bool:
    return exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500


def _describe_failure(exc: GoogleApiError) -> str:
    return "network error" if exc.status_code is None else f"HTTP {exc.status_code}"


def _fetch_message(access_token: str, message_id: str) -> tuple[dict, str]:
    """One in-place retry for a transient failure (429, 5xx, network): a
    message skipped here is never written to ProcessedMessage, so if it's
    older than the next incremental window it's never seen again."""
    try:
        return google_api.get_message(access_token, message_id)
    except GoogleApiError as exc:
        if not _is_transient(exc):
            raise
        time.sleep(PER_MESSAGE_RETRY_SECONDS)
        return google_api.get_message(access_token, message_id)
```

and in `process_job`'s per-message block:

```python
                try:
                    summary, body = _fetch_message(access_token, message_id)
                except GoogleApiError as exc:
                    logger.warning(
                        "Skipping message %s: fetch failed (%s)", message_ref(message_id), _describe_failure(exc)
                    )
```

- [ ] **Step 5:** Run `uv run pytest tests/test_sync_worker.py -q` → PASS.

### Task 1.3: Hide SQL parameters from all logs

**Files:**
- Modify: `backend/app/db/session.py`, `backend/tests/conftest.py`
- Test: `backend/tests/test_db_session.py`, `backend/tests/test_sync_worker.py`

**Interfaces:**
- Produces: `app.db.session.build_engine(url: str) -> Engine` (`future=True, pool_pre_ping=True, hide_parameters=True`); conftest's `engine` fixture uses it so tests mirror production.

- [ ] **Step 1: Write failing tests:**

```python
# test_db_session.py
def test_app_engine_hides_sql_parameters_from_exception_text() -> None:
    from app.db.session import engine
    assert engine.hide_parameters is True
```

```python
# test_sync_worker.py
def test_a_db_error_in_the_precheck_query_does_not_log_raw_message_ids(
    db_session, user, monkeypatch, caplog
) -> None:
    # A real Postgres error raised from the actual pre-check query, which binds
    # the page's raw Gmail message IDs — its traceback goes to public logs.
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["rawid789"], None))
    real_scalars = db_session.scalars

    def poisoned(statement, *args, **kwargs):
        if "processed_messages" in str(statement):
            statement = statement.where(text("1/0 = 1"))
        return real_scalars(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalars", poisoned)
    monkeypatch.setattr(logging.getLogger("app.sync.worker"), "disabled", False)

    with caplog.at_level("ERROR"):
        process_job(db_session, job, extractor=_FakeExtractor({}))

    assert "division by zero" in caplog.text  # the failure really happened and was logged
    assert "rawid789" not in caplog.text
```

- [ ] **Step 2:** Run → FAIL (`hide_parameters` False; raw ID in `[parameters: ...]`).
- [ ] **Step 3: Implement** `session.py`:

```python
def build_engine(url: str) -> Engine:
    # hide_parameters: SQLAlchemy exception text otherwise lists every bound
    # value, and worker tracebacks go to public GitHub Actions logs — the
    # per-page pre-check query binds raw Gmail message IDs (Phase 10).
    return create_engine(url, future=True, pool_pre_ping=True, hide_parameters=True)


engine = build_engine(settings.database_url)
```

and conftest: `eng = build_engine(TEST_DATABASE_URL)`.

- [ ] **Step 4:** Full local checks → PASS. Commit `fix(sync): classify Gmail failures, retry one message once, and hide SQL parameters from logs`. Push, CI green.
- [ ] **Step 5: Production verification:** click Sync (incremental) → "Synced" timing noted; `gh run view <id> --log` for the run scanned: `grep -E '[0-9a-f]{16}|gmail.googleapis.com/.*/messages'` finds nothing.

---

## CP2a — Migration 0007 only

### Task 2a.1: Additive migration, no model changes

**Files:**
- Create: `backend/alembic/versions/0007_add_reauth_and_alerting_columns.py`
- Test: `backend/tests/test_migrations_0007.py`

- [ ] **Step 1: Failing test** (uses the migrated test DB):

```python
from sqlalchemy import inspect


def test_migration_0007_adds_the_phase_10_columns(engine) -> None:
    inspector = inspect(engine)
    gmail_cols = {c["name"]: c for c in inspector.get_columns("gmail_connections")}
    job_cols = {c["name"]: c for c in inspector.get_columns("sync_jobs")}
    assert gmail_cols["reauth_required_at"]["nullable"] is True
    assert job_cols["error_code"]["nullable"] is True
    assert job_cols["alerted_at"]["nullable"] is True
```

- [ ] **Step 2:** Run → FAIL (KeyError).
- [ ] **Step 3: Implement:**

```python
"""add gmail reauth flag, sync job error code and alert bookkeeping

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25

Deployed ALONE, before any code that reads these columns (Phase 10 spec §7):
the GitHub Actions worker runs main as soon as it's pushed, while Render only
migrates during its build. All three columns are nullable, so code from before
this migration keeps working against the new schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gmail_connections", sa.Column("reauth_required_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sync_jobs", sa.Column("error_code", sa.String(length=40), nullable=True))
    op.add_column("sync_jobs", sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True))
    # Failures from before monitoring existed are history, not news — the
    # monitor's first run must not alert on them.
    op.execute("UPDATE sync_jobs SET alerted_at = now() WHERE status = 'failed'")


def downgrade() -> None:
    op.drop_column("sync_jobs", "alerted_at")
    op.drop_column("sync_jobs", "error_code")
    op.drop_column("gmail_connections", "reauth_required_at")
```

- [ ] **Step 4:** Local: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` succeeds; full checks PASS. `git diff --stat` shows only the migration + test. Commit `feat(db): add migration 0007 for reauth and alerting columns (schema only)`. Push. CI green.
- [ ] **Step 5: Production verification (maintainer-guided, one step at a time):** (1) Render → backend → Events: the deploy for this commit is **Live** and its build log shows `Running upgrade 0006 -> 0007`; (2) `curl https://job-tracker-lds1.onrender.com/health/ready` → 200; (3) maintainer runs, locally with the prod URL from their password manager (never pasted into chat): `psql "$PROD_URL" -c "select version_num from alembic_version" -c "select column_name from information_schema.columns where table_name in ('gmail_connections','sync_jobs') and column_name in ('reauth_required_at','error_code','alerted_at')" -c "select count(*) filter (where status='failed' and alerted_at is null) from sync_jobs"` → `0007`, three columns, `0`; (4) one incremental Sync still works (old code on new schema). **Only then start CP2b.**

---

## CP2b — Reauthorization & reconnect flow

### Task 2b.1: Models, service state, `InvalidToken` → reauth, reconnect clears flag

**Files:**
- Modify: `backend/app/gmail/models.py`, `backend/app/sync/models.py`, `backend/app/gmail/service.py`, `backend/app/gmail/exceptions.py`, `backend/app/gmail/schemas.py`, `backend/app/gmail/router.py`, `backend/app/main.py`
- Test: `backend/tests/test_gmail_service.py`, `backend/tests/test_gmail_router.py`

**Interfaces:**
- Produces: `GmailConnection.reauth_required_at: Mapped[datetime | None]`; `SyncJob.error_code: Mapped[str | None]` (String(40)); `SyncJob.alerted_at: Mapped[datetime | None]`; `class GmailReauthRequired(Exception)` (`__init__(self, user_id: UUID)`), `REAUTH_CODE = "gmail_reauth_required"` and `REAUTH_MESSAGE = "Gmail access has expired or was revoked. Reconnect Gmail to keep syncing."` in `app/gmail/exceptions.py`; `gmail_service.mark_reauth_required(db, connection) -> None`; `GmailStatus.needs_reconnect: bool = False`; app handler `GmailReauthRequired → 403 {"detail": REAUTH_MESSAGE, "code": REAUTH_CODE}`.

- [ ] **Step 1: Failing tests:**

```python
def test_get_valid_access_token_raises_auth_error_when_the_stored_token_is_unreadable(db_session, user) -> None:
    connection = _make_connection_encrypted_with_a_rotated_away_key(db_session, user)
    with pytest.raises(google_api.GmailAuthError):
        service.get_valid_access_token(db_session, connection)


def test_connect_clears_reauth_and_keeps_the_watermark(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)
    connection.reauth_required_at = datetime.now(timezone.utc)
    connection.last_synced_message_date = date(2026, 9, 1)
    db_session.commit()
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})
    monkeypatch.setattr(google_api, "revoke_token", lambda token: None)

    service.connect(db_session, user.id, {"access_token": "a2", "refresh_token": "r2", "expires_in": 3600, "scope": "s"})

    db_session.refresh(connection)
    assert connection.reauth_required_at is None
    assert connection.last_synced_message_date == date(2026, 9, 1)


def test_list_recent_messages_flags_reauth_and_raises(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)
    monkeypatch.setattr(
        google_api, "list_message_ids",
        lambda token, limit: (_ for _ in ()).throw(google_api.GmailAuthError("x", status_code=401)),
    )
    with pytest.raises(GmailReauthRequired):
        service.list_recent_messages(db_session, user.id, 5)
    db_session.refresh(connection)
    assert connection.reauth_required_at is not None
```

```python
# test_gmail_router.py
def test_status_reports_needs_reconnect(authed_client, db_session, user) -> None:
    ...  # connection with reauth_required_at set → GET /api/v1/gmail/status → needs_reconnect True

def test_messages_returns_403_with_code_when_reauth_required(authed_client, ...) -> None:
    ...  # list_message_ids raises GmailAuthError → 403, body["code"] == "gmail_reauth_required"
```

(Router tests follow the existing fixture names in `test_gmail_router.py`; copy its client/connection setup verbatim.)

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.** Model columns (`reauth_required_at = mapped_column(DateTime(timezone=True), nullable=True)`, etc.). In `gmail/service.py`:

```python
def _decrypt_or_reauth(ciphertext: str) -> str:
    try:
        return decrypt_token(ciphertext)
    except InvalidToken as exc:
        # GMAIL_TOKEN_ENCRYPTION_KEY was rotated: only a reconnect can fix it.
        raise google_api.GmailAuthError("stored Gmail token is unreadable") from exc


def mark_reauth_required(db: Session, connection: GmailConnection) -> None:
    connection.reauth_required_at = datetime.now(timezone.utc)
    db.commit()
```

`get_valid_access_token` uses `_decrypt_or_reauth` for both decrypts. `connect()` sets `connection.reauth_required_at = None`. `list_recent_messages` wraps its Gmail calls: `except google_api.GmailAuthError: mark_reauth_required(db, connection); raise GmailReauthRequired(user_id) from None`. `gmail_status` returns `needs_reconnect=connection.reauth_required_at is not None`. `main.py` adds the 403 handler (registered before the generic `GoogleApiError` handler is irrelevant — `GmailReauthRequired` is not a `GoogleApiError`).

- [ ] **Step 4:** Run gmail tests → PASS.

### Task 2b.2: Worker fails fast on `GmailAuthError`

**Files:**
- Modify: `backend/app/sync/worker.py`
- Test: `backend/tests/test_sync_worker.py`

**Interfaces:**
- Consumes: `GmailAuthError`, `REAUTH_CODE`, `REAUTH_MESSAGE`, `SyncJob.error_code`, `GmailConnection.reauth_required_at`.

- [ ] **Step 1: Failing tests:**

```python
@pytest.mark.parametrize("where", ["refresh", "list", "message"])
def test_process_job_fails_fast_and_flags_the_connection_on_reauth(db_session, user, monkeypatch, where) -> None:
    connection = _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    auth_error = google_api.GmailAuthError("revoked", status_code=401)

    def raise_auth(*a, **k):
        raise auth_error

    if where == "refresh":
        monkeypatch.setattr(gmail_service, "get_valid_access_token", raise_auth)
    else:
        monkeypatch.setattr(google_api, "list_message_ids_page",
                            raise_auth if where == "list" else (lambda token, **kw: (["m1"], None)))
        if where == "message":
            monkeypatch.setattr(google_api, "get_message", raise_auth)

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(job)
    db_session.refresh(connection)
    assert job.status == "failed"
    assert job.error_code == "gmail_reauth_required"
    assert "Reconnect Gmail" in job.error_message
    assert job.attempts == 0
    assert job.finished_at is not None
    assert connection.reauth_required_at is not None
    assert connection.last_synced_message_date is None  # watermark never advanced


def test_process_job_treats_a_401_on_a_message_fetch_as_reauth(db_session, user, monkeypatch) -> None:
    # Review Focus #1: must abort, not skip every message on the page.
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["m1", "m2", "m3"], None))
    calls = []

    def revoked(token, mid):
        calls.append(mid)
        raise google_api.GmailAuthError("x", status_code=401)

    monkeypatch.setattr(google_api, "get_message", revoked)
    process_job(db_session, job, _FakeExtractor({}))

    assert calls == ["m1"]
    db_session.refresh(job)
    assert job.failed_count == 0
    assert job.error_code == "gmail_reauth_required"
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.** `_fetch_message` re-raises `GmailAuthError` before the transient check (`except GmailAuthError: raise` above `except GoogleApiError`); the per-message `try` likewise adds `except GmailAuthError: raise` above `except GoogleApiError`. In `process_job`, add before `except Exception`:

```python
    except GmailAuthError:
        # Same rollback-first rule as the generic handler below.
        db.rollback()
        logger.warning("Sync job %s stopped: Gmail authorization is no longer valid", job.id)
        job.status = "failed"
        job.error_code = REAUTH_CODE
        job.error_message = REAUTH_MESSAGE
        job.finished_at = datetime.now(timezone.utc)
        connection.reauth_required_at = datetime.now(timezone.utc)
        db.commit()
        return
```

- [ ] **Step 4:** Run worker tests → PASS.

### Task 2b.3: Enqueue refuses flagged connections; `SyncJobRead.error_code`

**Files:**
- Modify: `backend/app/sync/service.py`, `backend/app/sync/schemas.py`
- Test: `backend/tests/test_sync_service.py`, `backend/tests/test_sync_router.py`

- [ ] **Step 1: Failing tests:**

```python
def test_enqueue_sync_refuses_a_connection_that_needs_reconnect(db_session, user) -> None:
    connection = _connect(db_session, user)  # existing helper in test_sync_service.py
    connection.reauth_required_at = datetime.now(timezone.utc)
    db_session.commit()
    with pytest.raises(GmailReauthRequired):
        enqueue_sync(db_session, user.id)
    assert db_session.query(SyncJob).count() == 0


def test_enqueue_after_reconnect_is_incremental(db_session, user) -> None:
    connection = _connect(db_session, user)
    connection.last_synced_message_date = date(2026, 9, 1)
    connection.reauth_required_at = None  # as connect() leaves it
    db_session.commit()
    job = enqueue_sync(db_session, user.id)
    assert job.job_type == "incremental"
    assert job.window_start == date(2026, 8, 31)
```

Router: `POST /api/v1/gmail/sync` with a flagged connection → 403, `code == "gmail_reauth_required"`, and dispatch **not** requested (monkeypatch `dispatch.request_worker` to record calls; assert none). `GET /sync/{id}` of a reauth-failed job includes `"error_code": "gmail_reauth_required"`.

- [ ] **Step 2:** Run → FAIL. **Step 3:** In `enqueue_sync`, right after the `connection is None` check: `if connection.reauth_required_at is not None: raise GmailReauthRequired(user_id)`. Add `error_code: str | None` to `SyncJobRead`. **Step 4:** PASS.

### Task 2b.4: Frontend Reconnect UX

**Files:**
- Modify: `frontend/src/api/http.ts`, `frontend/src/api/sync.ts`, `frontend/src/types/gmail.ts`, `frontend/src/types/sync.ts`, `frontend/src/components/GmailPanel.tsx`, `frontend/src/components/SyncPanel.tsx`

**Interfaces:**
- Produces: `export class ApiError extends Error { status: number; code: string | null }` thrown by `parseResponse`; `export const REAUTH_CODE = 'gmail_reauth_required'`; `GmailStatus.needs_reconnect?: boolean`; `SyncJob.error_code?: string | null`.

- [ ] **Step 1:** `http.ts`: parse `detail` and `code` from the JSON body; `throw new ApiError(message, response.status, typeof body.code === 'string' ? body.code : null)` (ApiError extends Error, so existing `err.message` consumers are unaffected).
- [ ] **Step 2:** `GmailPanel`: when `status.needs_reconnect`, render an amber notice "Gmail access expired or was revoked." with a **Reconnect Gmail** link to the same connect URL the Connect button uses (keeps history — do not route through Disconnect).
- [ ] **Step 3:** `SyncPanel`: when the latest job is `failed` with `error_code === REAUTH_CODE`, or `startSync` rejects with `ApiError` whose `code === REAUTH_CODE`, show the error text plus the same Reconnect link; the Sync button stays visible.
- [ ] **Step 4:** `npx tsc -b && npx oxlint && npm run build` clean; manual local check with `reauth_required_at` set by SQL on the dev DB: banner shows, Sync shows reconnect message, reconnect clears both.
- [ ] **Step 5:** Full local checks. Commit `feat(gmail): treat expired or revoked Gmail access as reconnect-required, not a retryable error`. Push; CI green.
- [ ] **Step 6: Production drill (maintainer-guided):** myaccount.google.com → Security → Third-party connections → this app → remove access; click Sync → failed within ~1 min, Reconnect shown, job `attempts` 0 (`GET /api/v1/gmail/sync/latest` in DevTools); click Sync again → 403 message; Reconnect → consent → Sync → job type `incremental`. Record in notes for CLAUDE.md.

---

## CP3 — Time-sliced jobs, orphan sweep, self-re-dispatch

### Task 3.1: Settings + `process_job` deadline/yield

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/sync/worker.py`
- Test: `backend/tests/test_sync_worker.py`

**Interfaces:**
- Produces: settings `sync_initial_slice_seconds: int = Field(default=1500, gt=0)`, `sync_incremental_slice_seconds: int = Field(default=1200, gt=0)`, `sync_orphan_threshold_seconds: int = Field(default=120, gt=0)`; `worker.lane_slice_seconds(lane: str) -> float`; `worker.LANES = ("incremental", "initial")`; `JobOutcome = Literal["completed", "failed", "retrying", "yielded"]`; `process_job(db, job, extractor=None, *, deadline: float | None = None) -> JobOutcome`. `LANE_DRAIN_BUDGET_SECONDS` is removed.

- [ ] **Step 1: Failing tests:**

```python
class _Clock:
    """time.monotonic stand-in: advances a fixed step every call."""
    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step
    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_process_job_yields_at_a_page_boundary_after_its_deadline(db_session, user, monkeypatch) -> None:
    import app.sync.worker as worker_module
    _connect_gmail(db_session, user)
    started = datetime.now(timezone.utc) - timedelta(minutes=1)
    job = _make_job(user.id, started_at=started)
    db_session.add(job)
    db_session.commit()
    pages = [(["m1"], "page-2"), (["m2"], None)]
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: pages.pop(0))
    monkeypatch.setattr(google_api, "get_message", lambda token, mid: (_make_summary(mid, f"S {mid}"), "b"))
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: 100.0)
    extractor = _FakeExtractor({"S m1": EmailExtraction(is_job_related=False, confidence=0.99)})

    outcome = process_job(db_session, job, extractor, deadline=50.0)

    db_session.refresh(job)
    assert outcome == "yielded"
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.page_token == "page-2"
    assert job.started_at == started
    assert job.next_attempt_at <= datetime.now(timezone.utc)
    assert job.messages_processed == 1
    assert db_session.query(ProcessedMessage).filter_by(gmail_message_id="m1").count() == 1


def test_process_job_completes_instead_of_yielding_on_the_last_page(db_session, user, monkeypatch) -> None:
    import app.sync.worker as worker_module
    connection = _connect_gmail(db_session, user)
    job = _make_job(user.id, started_at=datetime.now(timezone.utc))
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: ([], None))
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: 100.0)

    assert process_job(db_session, job, _FakeExtractor({}), deadline=50.0) == "completed"
    db_session.refresh(connection)
    assert connection.last_synced_message_date is not None


def test_process_job_processes_at_least_one_page_with_a_zero_budget(db_session, user, monkeypatch) -> None:
    # deadline already passed before the first page: still one page of progress.
    ...  # same setup as the yield test with deadline=0.0; assert m1 processed and outcome == "yielded"


def test_process_job_returns_retrying_and_failed_outcomes(db_session, user, monkeypatch) -> None:
    ...  # list raises GoogleApiError → "retrying"; with attempts=2,max=3 → "failed"


def test_lane_slice_seconds_reads_settings(monkeypatch) -> None:
    from app.core.config import settings
    from app.sync.worker import lane_slice_seconds
    monkeypatch.setattr(settings, "sync_initial_slice_seconds", 120)
    assert lane_slice_seconds("initial") == 120.0
    assert lane_slice_seconds("incremental") == float(settings.sync_incremental_slice_seconds)
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.** Right after `job.page_token = next_page_token; db.commit()` and the expunge/re-add block:

```python
            if next_page_token is None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                # Safe checkpoint: every message on the page is committed and
                # page_token points at the next unprocessed page. Requeue
                # without using an attempt; started_at (the watermark basis)
                # and the counters carry over to the next slice.
                job.status = "queued"
                job.next_attempt_at = datetime.now(timezone.utc)
                db.commit()
                logger.info("Sync job %s yielded at its slice deadline", job.id)
                return "yielded"
```

Existing `return` statements become `return "failed"` (no-connection branch), `return "retrying" if job.status == "queued" else "failed"` (exception handler), `return "failed"` (reauth handler), and the success tail `return "completed"`.

```python
LANES = ("incremental", "initial")


def lane_slice_seconds(lane: str) -> float:
    return float(
        settings.sync_initial_slice_seconds if lane == "initial" else settings.sync_incremental_slice_seconds
    )
```

- [ ] **Step 4:** PASS.

### Task 3.2: `drain_once` returns `DrainResult`; stops after a yield; orphan sweep

**Files:**
- Modify: `backend/app/sync/worker.py`, `backend/app/sync/drain.py`
- Test: `backend/tests/test_sync_worker.py`, `backend/tests/test_sync_drain_entrypoint.py`

**Interfaces:**
- Produces: `class DrainResult(NamedTuple): processed: int; requeued: bool`; `drain_once(max_runtime_seconds=DRAIN_MAX_RUNTIME_SECONDS, job_type=None, *, sweep: bool = False) -> DrainResult`; `sweep_orphans(db, job_type: str) -> int`; `drain.main(argv) -> DrainResult`, writing `requeued=<true|false>` to `$GITHUB_OUTPUT` when set.

- [ ] **Step 1:** Update the 6 existing `drain_once(...) == N` assertions to `drain_once(...).processed == N`; update `test_drain_main_passes_the_lane_and_its_budget` to expect `{"max_runtime_seconds": 1200.0, "job_type": "incremental", "sweep": True}` and a `DrainResult` return; `test_sync_worker_workflow.py` stops importing `LANE_DRAIN_BUDGET_SECONDS` (rewritten in Task 3.3).
- [ ] **Step 2: Failing tests:**

```python
def test_sweep_orphans_requeues_only_its_lanes_running_jobs_older_than_the_threshold(db_session, user) -> None:
    old = datetime.now(timezone.utc) - timedelta(seconds=300)
    orphan = _make_job(user.id, job_type="initial", status="running")
    db_session.add(orphan)
    db_session.commit()
    db_session.execute(text("UPDATE sync_jobs SET updated_at = :t WHERE id = :id"), {"t": old, "id": orphan.id})
    db_session.commit()

    assert sweep_orphans(db_session, "initial") == 1
    db_session.refresh(orphan)
    assert orphan.status == "queued"
    assert orphan.attempts == 1


def test_sweep_orphans_leaves_a_fresh_running_job_and_the_other_lane(db_session, user, other_user) -> None:
    ...  # fresh initial running (updated_at now) untouched; old incremental running untouched by sweep("initial")
```

Drain tests (real `engine`, following the existing committed-rows + `_cleanup_committed` pattern): a job whose `process_job` is patched to return `"yielded"` → `drain_once(...)` returns `DrainResult(1, True)` and `process_job` was called exactly once even though the job is due again; `sweep=True` calls `sweep_orphans` once before the first claim (patched to record calls). Entry point: with `GITHUB_OUTPUT` pointed at `tmp_path / "out"`, `main(["--lane", "initial"])` with a patched `drain_once` returning `DrainResult(1, True)` writes `requeued=true\n`; without the env var nothing is written.

- [ ] **Step 3: Implement:**

```python
class DrainResult(NamedTuple):
    processed: int
    requeued: bool


def sweep_orphans(db: Session, job_type: str) -> int:
    """Run once when a lane drain starts. The lane workflow's concurrency
    group means no other production run of this lane is working a job right
    now, so a 'running' job that hasn't committed in
    sync_orphan_threshold_seconds was abandoned by a killed run. A healthy
    job commits after every message. Not used by run_forever/in-process,
    where that single-worker-per-lane guarantee doesn't hold."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.sync_orphan_threshold_seconds)
    orphans = db.scalars(
        select(SyncJob)
        .where(SyncJob.status == "running", SyncJob.job_type == job_type, SyncJob.updated_at < cutoff)
        .with_for_update(skip_locked=True)
    ).all()
    for job in orphans:
        logger.warning("Recovering orphaned sync job %s (no progress since %s)", job.id, job.updated_at)
        _requeue_or_fail(job, "Sync stalled and was not recovered automatically. Try syncing again.")
    if orphans:
        db.commit()
    return len(orphans)
```

`_run_one_tick(db, job_type=None, deadline=None)` passes `deadline` to `process_job` and returns `(job, outcome)`. `drain_once` computes `deadline = start + max_runtime_seconds` (monotonic), runs the sweep once first when `sweep and job_type` (own session, try/except + rollback like the reaper), and returns `DrainResult(processed, True)` immediately after a `"yielded"` outcome; all other returns become `DrainResult(processed, False)`.

`drain.py`:

```python
    parser.add_argument("--lane", choices=LANES)
    ...
    if args.lane is None:
        result = drain_once()
    else:
        result = drain_once(max_runtime_seconds=lane_slice_seconds(args.lane), job_type=args.lane, sweep=True)
    logger.info("Drained %d job(s)%s%s", result.processed,
                f" from the {args.lane} lane" if args.lane else "",
                "; a job was paused and will continue in a new run" if result.requeued else "")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as fh:
            fh.write(f"requeued={'true' if result.requeued else 'false'}\n")
    return result
```

- [ ] **Step 4:** PASS.

### Task 3.3: Lane workflows re-dispatch themselves; guard tests

**Files:**
- Modify: `.github/workflows/sync-initial.yml`, `.github/workflows/sync-incremental.yml`, `backend/tests/test_sync_worker_workflow.py`

- [ ] **Step 1: Rewrite guard tests:**

```python
EXPECTED_TIMEOUT_MINUTES = {"incremental": 30, "initial": 40}
SLICE_ENV = {"incremental": "SYNC_INCREMENTAL_SLICE_SECONDS", "initial": "SYNC_INITIAL_SLICE_SECONDS"}
SLICE_MARGIN_SECONDS = 600  # one page overrun + checkout/uv setup


def test_every_lane_has_a_workflow_and_the_old_single_worker_is_gone() -> None:
    assert set(EXPECTED_TIMEOUT_MINUTES) == set(LANES_FROM_WORKER)  # from app.sync.worker import LANES
    ...


@pytest.mark.parametrize("lane", LANES)
def test_lane_timeout_leaves_margin_above_its_configured_slice(lane) -> None:
    job = _load(lane)["jobs"]["drain"]
    slice_seconds = int(job["env"][SLICE_ENV[lane]])
    assert job["timeout-minutes"] == EXPECTED_TIMEOUT_MINUTES[lane]
    assert job["timeout-minutes"] * 60 >= slice_seconds + SLICE_MARGIN_SECONDS


@pytest.mark.parametrize("lane", LANES)
def test_lane_redispatches_itself_only_when_a_job_was_paused(lane) -> None:
    workflow = _load(lane)
    assert workflow["permissions"] == {"contents": "read", "actions": "write"}
    steps = workflow["jobs"]["drain"]["steps"]
    drain = next(s for s in steps if s.get("id") == "drain")
    assert f"--lane {lane}" in drain["run"]
    redispatch = steps[-1]
    assert redispatch["if"] == "steps.drain.outputs.requeued == 'true'"
    assert redispatch["run"].strip() == f"gh workflow run sync-{lane}.yml --ref main"
    assert redispatch["env"]["GH_TOKEN"] == "${{ github.token }}"
```

(Remove the old `test_lane_workflow_timeout_sits_above_its_drain_budget`.)

- [ ] **Step 2:** Run → FAIL. **Step 3:** Edit both workflows: `permissions: {contents: read, actions: write}`; job `env:` gains `SYNC_INITIAL_SLICE_SECONDS: "1500"` (initial) / `SYNC_INCREMENTAL_SLICE_SECONDS: "1200"` (incremental); initial `timeout-minutes: 40` with an updated comment; the drain step gets `id: drain`; append:

```yaml
      # A job that reached its slice deadline was checkpointed and requeued
      # without using an attempt — start the next slice now instead of
      # waiting for the throttled cron. workflow_dispatch sent with
      # GITHUB_TOKEN does create a run; concurrency queues it behind this one.
      - name: Continue a paused job in a new run
        if: steps.drain.outputs.requeued == 'true'
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh workflow run sync-initial.yml --ref main
```

(`sync-incremental.yml` for the incremental file.) **Step 4:** PASS.

### Task 3.4: Frontend "continuing" state

**Files:**
- Modify: `frontend/src/components/SyncPanel.tsx`

- [ ] **Step 1:** A job with `status === 'queued' && job.started_at !== null` renders the running-style progress line "Syncing — {messages_processed} of {messages_seen} (continuing…)" instead of "Starting sync…"; the queued Retry hint logic is unchanged.
- [ ] **Step 2:** `tsc`/`oxlint`/build clean. Full local checks. **Check no initial-lane run is active** (`gh run list --workflow sync-initial.yml --limit 3`). Commit `feat(sync): time-slice long jobs, recover orphaned jobs at drain start, and continue in a new run`. Push; CI green.
- [ ] **Step 3: Production drills (maintainer-guided):**
  - **Short slice:** temporary commit setting `SYNC_INITIAL_SLICE_SECONDS: "120"` in `sync-initial.yml`; maintainer Disconnects then Connects Gmail (forces an initial job; watermark reset is expected); observe ≥2 `workflow_dispatch` runs chained; job completes with `attempts == 0`; duplicate check `select gmail_message_id, count(*) from processed_messages group by 1 having count(*) > 1` returns 0 rows; watermark = first `started_at` date. Revert the commit.
  - **Cancel:** while a slice is running, `gh run cancel <id>`; then `gh workflow run sync-initial.yml`; the job is re-claimed within 3 min.
  - **Incremental timing:** 3 Sync clicks, median ≤ 90 s.

---

## CP4 — Monitor

### Task 4.1: `app/sync/monitor.py`

**Files:**
- Create: `backend/app/sync/monitor.py`
- Modify: `backend/app/core/config.py` (add `sync_dispatch_token_expires_on: date | None = None`)
- Test: `backend/tests/test_sync_monitor.py`

**Interfaces:**
- Produces: `evaluate(db, *, now: datetime, today: date) -> MonitorReport` where `MonitorReport(alerts: list[str], lines: list[str])`; `main() -> int` (0 healthy, 1 alert or error). Thresholds: `DUE_UNCLAIMED_AFTER = timedelta(minutes=15)`, `TOKEN_EXPIRY_WARNING_DAYS = 21`.

- [ ] **Step 1: Failing tests** (all against `db_session`; `evaluate` commits only the M3 `alerted_at` update):

```python
def test_healthy_state_has_no_alerts(db_session) -> None:
    report = evaluate(db_session, now=NOW, today=NOW.date())
    assert report.alerts == []


def test_m1_alerts_on_a_job_due_but_unclaimed_for_15_minutes(db_session, user) -> None:
    db_session.add(_job(user, status="queued", next_attempt_at=NOW - timedelta(minutes=16)))
    db_session.commit()
    assert [a.split()[0] for a in evaluate(db_session, now=NOW, today=NOW.date()).alerts] == ["M1"]


def test_m1_ignores_a_just_yielded_job(db_session, user) -> None:
    db_session.add(_job(user, status="queued", started_at=NOW - timedelta(hours=1),
                        next_attempt_at=NOW - timedelta(seconds=30)))
    db_session.commit()
    assert evaluate(db_session, now=NOW, today=NOW.date()).alerts == []


def test_m2_alerts_on_a_stuck_running_job(db_session, user) -> None: ...  # updated_at 16 min old


def test_m3_alerts_once_per_failed_job(db_session, user) -> None:
    job = _job(user, status="failed", error_code="gmail_reauth_required")
    db_session.add(job)
    db_session.commit()
    first = evaluate(db_session, now=NOW, today=NOW.date())
    assert first.alerts and "gmail_reauth_required" in first.alerts[0]
    assert evaluate(db_session, now=NOW, today=NOW.date()).alerts == []


def test_m4_alerts_when_the_dispatch_token_expires_within_21_days(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sync_dispatch_token_expires_on", NOW.date() + timedelta(days=20))
    assert evaluate(db_session, now=NOW, today=NOW.date()).alerts[0].startswith("M4")


def test_report_output_never_contains_emails_or_full_ids(db_session, user) -> None:
    job = _job(user, status="failed")
    db_session.add(job)
    db_session.commit()
    text_out = "\n".join(evaluate(db_session, now=NOW, today=NOW.date()).lines)
    assert user.email not in text_out
    assert str(job.id) not in text_out
    assert str(user.id) not in text_out
    assert str(job.id)[:8] in text_out


def test_main_exits_nonzero_when_the_database_is_unreachable(monkeypatch) -> None:
    import app.sync.monitor as monitor
    monkeypatch.setattr(monitor, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert monitor.main() == 1
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `monitor.py`: four queries (M1 `status='queued' AND next_attempt_at < now-15min`; M2 `status='running' AND updated_at < now - sync_stale_job_threshold_minutes`; M3 `status='failed' AND alerted_at IS NULL`, grouped by `coalesce(error_code, 'generic')`, then `alerted_at = now`, commit; M4 from `settings.sync_dispatch_token_expires_on`). Each alert line: `"M1 due-but-unclaimed jobs: 1 [ab12cd34]"`, `"M3 newly failed jobs: 2 (gmail_reauth_required=1, generic=1) [ab12cd34, ef56...]"`, `"M4 sync dispatch token expires in 20 day(s) on 2027-09-24 — rotate it (README)"`. `main()` logs lines, appends a markdown summary to `$GITHUB_STEP_SUMMARY` if set, wraps everything in `try/except Exception: logger.exception("Monitor could not complete"); return 1`, silences httpx logging like `drain.py`, and `raise SystemExit(main())` under `__main__`. Module imports `app.users.models.User` (same standalone-mapper reason as `worker.py`); add the subprocess mapper test to `tests/test_sync_drain_entrypoint.py` for `app.sync.monitor`.
- [ ] **Step 4:** PASS.

### Task 4.2: `sync-monitor.yml`

**Files:**
- Create: `.github/workflows/sync-monitor.yml`
- Test: `backend/tests/test_sync_worker_workflow.py`

- [ ] **Step 1: Failing test:**

```python
def test_monitor_workflow_is_read_only_scheduled_and_dispatchable() -> None:
    workflow = yaml.safe_load((WORKFLOWS_DIR / "sync-monitor.yml").read_text())
    triggers = _triggers(workflow)
    assert "workflow_dispatch" in triggers and triggers["schedule"][0]["cron"]
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["monitor"]
    assert job["timeout-minutes"] <= 10
    assert job["env"]["SYNC_DISPATCH_TOKEN_EXPIRES_ON"] == "2027-09-24"
    # Only the database secret is real; the monitor never talks to Google.
    secrets_used = {v for v in job["env"].values() if "secrets." in str(v)}
    assert secrets_used == {"${{ secrets.PROD_DATABASE_URL }}"}
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Create the workflow: `on: {workflow_dispatch: {}, schedule: [{cron: "23 * * * *"}]}`, `permissions: {contents: read}`, `concurrency: {group: sync-monitor, cancel-in-progress: false}`, job `monitor`, `timeout-minutes: 5`, env `DATABASE_URL: ${{ secrets.PROD_DATABASE_URL }}`, placeholders `GOOGLE_CLIENT_ID: unused`, `GOOGLE_CLIENT_SECRET: unused`, `SECRET_KEY: unused-by-the-monitor`, `GMAIL_TOKEN_ENCRYPTION_KEY: unused`, `ENV: production`, `SYNC_DISPATCH_TOKEN_EXPIRES_ON: "2027-09-24"`; steps checkout → setup-uv → `uv sync --locked` → `uv run python -m app.sync.monitor`. A header comment explains: a failed run **is** the alert (GitHub emails the owner), output is counts-only because logs are public. **Step 4:** PASS. Full checks, commit `feat(sync): add a scheduled monitor whose failed run alerts on stuck, failed or expiring sync state`, push, CI green.
- [ ] **Step 5: Production verification (maintainer-guided):** `gh workflow run sync-monitor.yml` → green run with counts. Drill: Render env `SYNC_DISPATCH_REPOSITORY` → `nonexistent/repo` (maintainer), click Sync, wait 16 min, `gh workflow run sync-monitor.yml` → failed run with M1 + email received; restore the env var, click Retry in the UI, re-run monitor → green. Start the 7-day observation (record Neon compute hours baseline now).

---

## CP5 — Security hardening

### Task 5.1: In-memory rate limiter + dispatch cooldown

**Files:**
- Create: `backend/app/core/ratelimit.py`
- Modify: `backend/app/sync/router.py`, `backend/app/gmail/router.py`, `backend/app/auth/router.py`, `backend/tests/conftest.py`
- Test: `backend/tests/test_ratelimit.py`, `backend/tests/test_sync_router.py`

**Interfaces:**
- Produces: `FixedWindowLimiter(clock=time.monotonic)` with `.hit(bucket: str, key: str, limit: int, window_seconds: int) -> int | None` (seconds to wait, or None if allowed) and `.reset()`; module-level `limiter`; dependency factories `limit_per_user(bucket, limit, window_seconds)` and `limit_per_ip(bucket, limit, window_seconds)` raising `HTTPException(429, detail=f"Too many requests — try again in {n} seconds.", headers={"Retry-After": str(n)})`; in `sync/router.py`, `REKICK_COOLDOWN_SECONDS = 30` and `_rekick_allowed(job_id: UUID, now: float) -> bool`.

- [ ] **Step 1: Failing tests:**

```python
def test_limiter_allows_up_to_the_limit_then_reports_the_wait() -> None:
    clock = [1000.0]
    lim = FixedWindowLimiter(clock=lambda: clock[0])
    assert all(lim.hit("b", "u1", 3, 60) is None for _ in range(3))
    wait = lim.hit("b", "u1", 3, 60)
    assert wait is not None and 0 < wait <= 60
    assert lim.hit("b", "u2", 3, 60) is None  # per-key isolation
    clock[0] += 60
    assert lim.hit("b", "u1", 3, 60) is None  # new window


def test_sync_post_is_limited_to_10_per_minute(authed_client, monkeypatch) -> None:
    ...  # enqueue patched to raise SyncAlreadyRunning(queued job) with rekick allowed; 10 calls → not 429; 11th → 429 + Retry-After


def test_polling_endpoints_are_never_rate_limited(authed_client, sync_job) -> None:
    for _ in range(200):
        assert authed_client.get(f"/api/v1/gmail/sync/{sync_job.id}").status_code == 200
        assert authed_client.get("/api/v1/gmail/sync/latest").status_code == 200


def test_rekick_dispatches_at_most_once_per_30_seconds_per_job(authed_client, queued_job, monkeypatch) -> None:
    ...  # two POSTs hitting the same queued job within 30s → request_worker called once; both return 409 with the job body
```

A conftest autouse fixture calls `limiter.reset()` and clears the re-kick map before each test.

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** `ratelimit.py`:

```python
"""Per-process fixed-window rate limiting (Phase 10). The API runs as one
uvicorn process on one Render instance, so in-memory counters are coherent;
a restart resets them, which is acceptable. Adding --workers N would
multiply every limit by N."""

import threading
import time
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from app.auth.dependencies import get_current_user
from app.users.models import User


class FixedWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._counts: dict[tuple[str, str, int], int] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int, window_seconds: int) -> int | None:
        now = self._clock()
        window = int(now // window_seconds)
        with self._lock:
            self._counts = {k: v for k, v in self._counts.items() if k[2] >= window - 1}
            count = self._counts.get((bucket, key, window), 0) + 1
            self._counts[(bucket, key, window)] = count
        if count <= limit:
            return None
        return max(1, int((window + 1) * window_seconds - now))

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


limiter = FixedWindowLimiter()


def _too_many(wait: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many requests — try again in {wait} seconds.",
        headers={"Retry-After": str(wait)},
    )


def limit_per_user(bucket: str, limit: int, window_seconds: int = 60):
    def dependency(current_user: User = Depends(get_current_user)) -> None:
        wait = limiter.hit(bucket, str(current_user.id), limit, window_seconds)
        if wait is not None:
            raise _too_many(wait)
    return dependency


def limit_per_ip(bucket: str, limit: int, window_seconds: int = 60):
    # Best-effort: uvicorn trusts X-Forwarded-For from any proxy here
    # (--forwarded-allow-ips='*'), so this only slows casual hammering.
    def dependency(request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        wait = limiter.hit(bucket, key, limit, window_seconds)
        if wait is not None:
            raise _too_many(wait)
    return dependency
```

Apply via `dependencies=[Depends(limit_per_user("sync", 10))]` on `POST /sync`, `("gmail_messages", 5)` on `GET /messages`, `("gmail_connect", 5)` on `GET /connect`, and `Depends(limit_per_ip("login", 20))` on `GET /auth/google/login`. Re-kick cooldown in `sync/router.py`:

```python
REKICK_COOLDOWN_SECONDS = 30.0
_last_rekick: dict[UUID, float] = {}


def _rekick_allowed(job_id: UUID, now: float) -> bool:
    last = _last_rekick.get(job_id)
    if last is not None and now - last < REKICK_COOLDOWN_SECONDS:
        return False
    _last_rekick[job_id] = now
    return True
```

used as `background=BackgroundTask(...) if _rekick_allowed(exc.job.id, time.monotonic()) else None` on the 409 response.

- [ ] **Step 4:** PASS.

### Task 5.2: API docs off in production; actions pinned to SHAs

**Files:**
- Modify: `backend/app/main.py`, all four `.github/workflows/*.yml`
- Test: `backend/tests/test_health.py` (or new `test_main.py`), `backend/tests/test_sync_worker_workflow.py`

- [ ] **Step 1: Failing tests:**

```python
def test_api_docs_are_disabled_in_production(monkeypatch) -> None:
    from app.core.config import settings
    from app.main import create_app
    monkeypatch.setattr(settings, "env", "production")
    client = TestClient(create_app())
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_api_docs_stay_available_in_development() -> None:
    from app.main import create_app
    assert TestClient(create_app()).get("/openapi.json").status_code == 200
```

```python
SHA_PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def test_every_workflow_action_is_pinned_to_a_full_commit_sha() -> None:
    for path in WORKFLOWS_DIR.glob("*.yml"):
        for job in yaml.safe_load(path.read_text())["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    assert SHA_PINNED.match(step["uses"]), f"{path.name}: {step['uses']}"
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** `create_app`: `docs = {"docs_url": None, "redoc_url": None, "openapi_url": None} if settings.env == "production" else {}`, passed into `FastAPI(...)`. Resolve SHAs with `gh api repos/actions/checkout/commits/v4 --jq .sha`, `gh api repos/astral-sh/setup-uv/commits/v10.1.0 --jq .sha`, `gh api repos/actions/setup-node/commits/v4 --jq .sha`, and replace each `uses:` with `owner/repo@<sha> # <tag>`. **Step 4:** PASS. Full checks; commit `feat(security): per-user rate limits, dispatch cooldown, no API docs in production, SHA-pinned actions`; push; CI green (CI itself proves the pinned SHAs resolve).
- [ ] **Step 5: Production verification:** `curl -s -o /dev/null -w "%{http_code}" https://job-tracker-lds1.onrender.com/docs` → 404 (also `/redoc`, `/openapi.json`); 11 rapid Sync clicks via DevTools `fetch` loop → the 11th returns 429; a full sync's polling shows no 429 in the Network tab.

### Task 5.3: Static-site security headers (Render dashboard, maintainer-guided)

- [ ] **Step 1:** Guide: Render → static site `job-tracker-1-ldy2` → Settings → **Headers** → add, path `/*`: `X-Frame-Options: DENY`; `Referrer-Policy: strict-origin-when-cross-origin`; `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`; `Content-Security-Policy-Report-Only: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://*.googleusercontent.com; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'`. Save.
- [ ] **Step 2:** `curl -sI https://job-tracker-1-ldy2.onrender.com/` shows all four (wait ≤5 min for CDN `s-maxage=300`).
- [ ] **Step 3:** Maintainer walks every flow with DevTools Console open (login, logout, avatar, Gmail connect/reconnect, Sync all states, review approve/edit/reject, application create/edit/delete, hard refresh) and reports any `[Report Only]` CSP messages. Zero required; otherwise adjust the policy and repeat.
- [ ] **Step 4:** Rename the header to `Content-Security-Policy` (enforcing); repeat Steps 2–3. Record the final header set in README's Production deployment section (Task 6.2).
- [ ] **Step 5:** 3 incremental Sync timings, median ≤ 90 s.

---

## CP6 — Failure-injection matrix & documentation

### Task 6.1: `tests/test_sync_failure_injection.py`

**Files:**
- Create: `backend/tests/test_sync_failure_injection.py`

- [ ] **Step 1:** Parametrized DB-failure injection: for each injection point `("after_list_commit", "precheck", "process_message", "page_token_commit", "yield_commit", "success_tail")`, poison the session at that point by wrapping `db_session.commit`/`db_session.scalars` (as in Task 1.3) so the N-th matching call executes `SELECT 1/0` first. Assert: `process_job` does not raise; afterwards the job is `queued` with `attempts == 1` (or recoverable by `sweep_orphans` when the failure hit the handler itself — covered by injecting on the handler's own commit and asserting a subsequent `sweep_orphans` after aging `updated_at` requeues it); a second `process_job` on a fresh claim completes; `select gmail_message_id, count(*) ... having count(*) > 1` is empty.
- [ ] **Step 2:** Neon-style drop: raise `sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("SSL connection has been closed unexpectedly"))` from `list_message_ids_page` on the second page → `retrying`, `page_token` = second page, resume completes.
- [ ] **Step 3:** Worker interruption: commit a claimed job and one processed message, age `updated_at` 5 min, `sweep_orphans` → queued; `drain_once(job_type="initial", sweep=True)` completes it with exactly one `ProcessedMessage` per message (real `engine` + `_cleanup_committed` pattern).
- [ ] **Step 4:** Token matrix in one place: access expiry → refresh called; `invalid_grant` / Gmail 401 on list / 401 on get / `InvalidToken` → reauth outcome; `invalid_client` → `retrying`.
- [ ] **Step 5:** Run; all PASS (these exercise already-built behavior — any failure is a real bug, fixed in its owning module with its own regression test). Commit `test(sync): add a failure-injection matrix for worker interruptions, DB failures and token expiry`.

### Task 6.2: Documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`, spec status line

- [ ] **Step 1:** CLAUDE.md: Status/phase list entry for Phase 10; architecture tree additions (`monitor.py`, `ratelimit.py`, `sync-monitor.yml`, migration 0007); "Phase 10 results" with production evidence (run IDs, timings, drills); Known gaps updated (remove fixed: time budget, startup sweep, two-category errors, monitoring, rate limiting; keep/add: IP limit spoofable, crash-time `messages_seen` over-count, 60-day cron disable applies to the monitor, Testing-mode weekly reconnect if CP7 not taken).
- [ ] **Step 2:** README: alert runbook table (M1–M4: meaning → first check → fix), slice settings, final header set, migration-first deployment rule.
- [ ] **Step 3:** Verify P3: `git diff --stat 38a38e1..HEAD -- backend/app/classifier backend/app/pipeline/matching.py backend/app/applications` is empty; `evaluation.compare` unchanged. Commit `docs: document Phase 10 hardening, alert runbook and production verification`. Push.
- [ ] **Step 4:** After 7 days: record M-c (monitor false positives, Neon compute hours delta) in CLAUDE.md.

---

## CP7 — DROPPED 2026-09-25 (CP0 chose option (a); see spec §11.5)

> Kept below for reference only; do not execute. Recorded as a future improvement in the spec.

### (former) App allowlist, then consent-screen switch

### Task 7.1: `AUTH_ALLOWED_EMAILS`

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/auth/router.py`
- Test: `backend/tests/test_auth.py`

- [ ] **Step 1: Failing tests:** with `settings.auth_allowed_emails = "owner@example.com"`, a callback for `stranger@example.com` redirects to `frontend_url` with **no** `access_token` cookie and creates no `User` row; `Owner@Example.com` (case-insensitive) succeeds; `email_verified: False` is refused; unset → existing behavior.
- [ ] **Step 2:** Implement: `auth_allowed_emails: str | None = None` + `allowed_emails_set` property (lower-cased, stripped); in `google_callback`, after reading `claims`, refuse when the set is non-empty and (`claims["email"].lower()` not in it or `claims.get("email_verified") is not True`).
- [ ] **Step 3:** Full checks, commit, push, CI green. Maintainer sets `AUTH_ALLOWED_EMAILS` on Render (guided). Verify owner login works.
- [ ] **Step 4: STOP for explicit approval**, then guide the maintainer through Google Auth Platform → Audience → **Publish app**; reconnect Gmail once; verify a sync ≥ 8 days later succeeds without reauth (G2). Record in CLAUDE.md.
