from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.classifier.schemas import EmailExtraction
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.pipeline.models import ProcessedMessage
from app.sync.models import SyncJob
from app.sync.worker import _safe_error_message, claim_next_job, process_job, reap_stale_jobs
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

    try:
        conn_a = engine.connect()
        txn_a = conn_a.begin()
        session_a = Session(bind=conn_a)
        try:
            session_a.execute(
                SyncJob.__table__.select()
                .where(SyncJob.__table__.c.id == job_id)
                .with_for_update()
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
    finally:
        # Guaranteed to run even if the assertion (or anything else above)
        # raises, so a failing test never leaks committed rows into the
        # shared, session-scoped test database.
        with engine.begin() as cleanup_conn:
            cleanup_conn.execute(delete(SyncJob).where(SyncJob.id == job_id))
            cleanup_conn.execute(delete(User).where(User.id == user_id))


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


def test_process_job_requeues_with_backoff_on_unexpected_non_gmail_error(
    db_session, user, monkeypatch
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()

    def fake_list_page(token, **kw):
        raise RuntimeError("unexpected boom")

    monkeypatch.setattr(google_api, "list_message_ids_page", fake_list_page)

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.next_attempt_at > datetime.now(timezone.utc)


def test_process_job_fails_permanently_after_max_attempts_on_non_gmail_error(
    db_session, user, monkeypatch
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id, attempts=2, max_attempts=3)
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        google_api, "list_message_ids_page",
        lambda token, **kw: (_ for _ in ()).throw(ConnectionError("network died")),
    )

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_message == _safe_error_message(ConnectionError("network died"))
    assert "network died" not in job.error_message
    assert job.finished_at is not None


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
    assert job.error_message == _safe_error_message(google_api.GoogleApiError("still down"))
    assert "still down" not in job.error_message
    assert job.finished_at is not None


def test_process_job_recovers_from_a_db_level_failure_without_raising(
    db_session, user, monkeypatch
) -> None:
    # Simulates a genuine DB-level failure (poisons the session's transaction,
    # like a real IntegrityError/OperationalError from a failed commit would)
    # rather than a plain Python exception. This must happen at a point where
    # job's ORM attributes are genuinely expired (SessionLocal defaults to
    # expire_on_commit=True, and the prior page's `job.page_token = ...;
    # db.commit()` just expired everything) and NOTHING has re-touched a job
    # attribute since — i.e. the very first statement of a fresh page, before
    # list_message_ids_page's page_token argument would otherwise force a
    # refresh. get_valid_access_token failing on the second page is exactly
    # that: nothing touches `job` between the first page's trailing commit
    # and this call. If the exception handler accesses job.id/job.attempts
    # before rolling back, that access needs a fresh SELECT (expired), which
    # raises on a poisoned session — masking the original failure and leaving
    # the job stuck "running" instead of being requeued.
    connection = _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()

    call_count = {"n": 0}

    def fake_get_valid_access_token(db, conn):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "token"
        db.execute(text("SELECT 1/0"))  # a real Postgres error, not a Python one
        return "unreachable"

    monkeypatch.setattr(gmail_service, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(
        google_api, "list_message_ids_page",
        lambda token, *, query, page_token, max_results: ([], "page-2") if page_token is None else ([], None),
    )

    process_job(db_session, job, _FakeExtractor({}))

    db_session.refresh(job)
    assert call_count["n"] == 2  # confirms the second-page call path was exercised
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.next_attempt_at > datetime.now(timezone.utc)


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


def test_safe_error_message_returns_a_generic_gmail_message_for_google_api_error() -> None:
    message = _safe_error_message(google_api.GoogleApiError("500 from Gmail"))

    assert "Gmail" in message
    assert "500 from Gmail" not in message


def test_safe_error_message_returns_a_generic_message_for_unexpected_errors() -> None:
    message = _safe_error_message(RuntimeError("SELECT * FROM gmail_connections WHERE token='abc123'"))

    assert "unexpected error" in message
    assert "abc123" not in message
    assert "SELECT" not in message


def _backdate_updated_at(db_session, job: SyncJob, when: datetime) -> None:
    # Bypass the ORM's onupdate=clock_timestamp() entirely via a raw Core
    # UPDATE, so the test setup is unambiguous rather than relying on
    # SQLAlchemy's explicit-assignment-wins-over-onupdate behavior.
    db_session.execute(
        SyncJob.__table__.update().where(SyncJob.__table__.c.id == job.id).values(updated_at=when)
    )
    db_session.commit()
    db_session.refresh(job)


def test_reap_stale_jobs_requeues_a_stale_running_job(db_session, user) -> None:
    job = _make_job(user.id, status="running", page_token="page-2")
    db_session.add(job)
    db_session.commit()
    _backdate_updated_at(db_session, job, datetime.now(timezone.utc) - timedelta(minutes=30))

    reap_stale_jobs(db_session)

    db_session.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.next_attempt_at > datetime.now(timezone.utc)
    assert job.page_token == "page-2"  # checkpoint preserved


def test_reap_stale_jobs_ignores_a_fresh_running_job(db_session, user) -> None:
    job = _make_job(user.id, status="running")
    db_session.add(job)
    db_session.commit()
    _backdate_updated_at(db_session, job, datetime.now(timezone.utc) - timedelta(minutes=1))

    reap_stale_jobs(db_session)

    db_session.refresh(job)
    assert job.status == "running"
    assert job.attempts == 0


def test_reap_stale_jobs_ignores_non_running_statuses(db_session, user) -> None:
    for status in ("queued", "completed", "failed"):
        job = _make_job(user.id, status=status)
        db_session.add(job)
        db_session.commit()
        _backdate_updated_at(db_session, job, datetime.now(timezone.utc) - timedelta(minutes=30))

        reap_stale_jobs(db_session)

        db_session.refresh(job)
        assert job.status == status
        assert job.attempts == 0
        db_session.delete(job)
        db_session.commit()


def test_reap_stale_jobs_fails_permanently_after_max_attempts(db_session, user) -> None:
    job = _make_job(user.id, status="running", attempts=2, max_attempts=3)
    db_session.add(job)
    db_session.commit()
    _backdate_updated_at(db_session, job, datetime.now(timezone.utc) - timedelta(minutes=30))

    reap_stale_jobs(db_session)

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 3
    assert job.error_message is not None
    assert job.finished_at is not None


class _StubSession:
    """Stands in for SessionLocal() in run_forever tests — reap_stale_jobs and
    claim_next_job are always monkeypatched in these tests and never touch the
    session they're given, so this only needs to support close()/rollback().
    Without this, run_forever's real SessionLocal() would bind to the dev
    database URL (not the test DB) — harmless today since nothing calls it,
    but a latent footgun if a future edit to run_forever ever did."""

    def close(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _FakeTime:
    """Stands in for the `time` module inside worker.py's run_forever, so
    monkeypatching sleep() can't affect time.sleep anywhere else in the
    process (patching the real `time` module globally would, even though
    that's harmless under pytest's single-threaded execution)."""

    def __init__(self, sleep) -> None:
        self.sleep = sleep


def test_run_forever_reaps_stale_jobs_every_iteration(monkeypatch) -> None:
    from app.sync import worker as worker_module

    reap_calls = []
    claim_calls = []

    monkeypatch.setattr(worker_module, "reap_stale_jobs", lambda db: reap_calls.append(db))
    monkeypatch.setattr(worker_module, "claim_next_job", lambda db: claim_calls.append(db) or None)
    monkeypatch.setattr(worker_module, "SessionLocal", _StubSession)

    class _StopLoop(Exception):
        pass

    def fake_sleep(seconds):
        raise _StopLoop

    monkeypatch.setattr(worker_module, "time", _FakeTime(fake_sleep))

    with pytest.raises(_StopLoop):
        worker_module.run_forever(poll_interval=0)

    assert len(reap_calls) == 1
    assert len(claim_calls) == 1


def test_run_forever_reap_failure_does_not_block_claim_next_job(monkeypatch) -> None:
    # A best-effort janitor must never be able to block the primary work: if
    # reap_stale_jobs raises, claim_next_job must still run in the SAME tick
    # (not just "eventually, on some later tick") — otherwise a persistent
    # reaper failure would silently stop all sync processing forever while
    # the worker looks alive.
    from app.sync import worker as worker_module

    claim_calls = []

    def fake_reap(db):
        raise RuntimeError("db blip")

    monkeypatch.setattr(worker_module, "reap_stale_jobs", fake_reap)
    monkeypatch.setattr(worker_module, "claim_next_job", lambda db: claim_calls.append(db) or None)
    monkeypatch.setattr(worker_module, "SessionLocal", _StubSession)

    class _StopLoop(Exception):
        pass

    def fake_sleep(seconds):
        raise _StopLoop

    monkeypatch.setattr(worker_module, "time", _FakeTime(fake_sleep))

    with pytest.raises(_StopLoop):
        worker_module.run_forever(poll_interval=0)

    assert len(claim_calls) == 1
