import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session, sessionmaker

from app.classifier.schemas import EmailExtraction
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.pipeline.models import ProcessedMessage
from app.sync.models import SyncJob
from app.sync.worker import (
    _safe_error_message,
    claim_next_job,
    drain_once,
    get_last_poll_at,
    process_job,
    reap_stale_jobs,
)
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
    monkeypatch.setattr(google_api, "get_message", lambda token, mid: (_make_summary(mid, f"Subject {mid}"), "body"))
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


def test_process_job_expunges_the_session_between_pages_to_bound_memory_growth(
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
    monkeypatch.setattr(google_api, "get_message", lambda token, mid: (_make_summary(mid, f"Subject {mid}"), "body"))
    extractor = _FakeExtractor(
        {
            "Subject m1": EmailExtraction(is_job_related=False, confidence=0.99),
            "Subject m2": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
            ),
        }
    )

    expunge_calls = []
    original_expunge_all = db_session.expunge_all

    def spy_expunge_all():
        expunge_calls.append(1)
        return original_expunge_all()

    monkeypatch.setattr(db_session, "expunge_all", spy_expunge_all)

    process_job(db_session, job, extractor)

    assert len(expunge_calls) == 2  # once per page — 2 pages in this test

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.messages_seen == 2
    assert job.messages_processed == 2


def test_process_job_updates_the_heartbeat_while_processing_messages(
    db_session, user, monkeypatch
) -> None:
    import app.sync.worker as worker_module

    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()

    pages = [(["m1"], "page-2"), (["m2"], None)]

    def fake_list_page(token, *, query, page_token, max_results):
        return pages.pop(0)

    monkeypatch.setattr(google_api, "list_message_ids_page", fake_list_page)
    monkeypatch.setattr(google_api, "get_message", lambda token, mid: (_make_summary(mid, f"Subject {mid}"), "body"))
    extractor = _FakeExtractor(
        {
            "Subject m1": EmailExtraction(is_job_related=False, confidence=0.99),
            "Subject m2": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
            ),
        }
    )

    monkeypatch.setattr(worker_module, "_last_poll_at", None)
    assert get_last_poll_at() is None

    process_job(db_session, job, extractor)

    assert get_last_poll_at() is not None


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
    monkeypatch.setattr(google_api, "get_message", lambda token, mid: (_make_summary(mid, "Newsletter"), "body"))
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
        google_api, "get_message", lambda token, mid: calls.append(mid) or (_make_summary(mid, "x"), "body")
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
        google_api, "get_message",
        lambda token, mid: (_ for _ in ()).throw(google_api.GoogleApiError("boom", status_code=404)),
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
    # Captured before process_job runs: process_job now expunges db_session's
    # entire identity map at each page boundary and re-attaches only `job` and
    # `connection` (see the memory-bounding fix in worker.py). `user` isn't one
    # of those two, so it comes out the other side detached-and-expired; reading
    # a stale `user.id` afterward would raise DetachedInstanceError trying to
    # lazy-refresh it against a session it's no longer part of. Holding the
    # plain UUID instead sidesteps that entirely, and matches how a real caller
    # would use this function — nothing outside process_job keeps ORM object
    # handles alive across it.
    user_id = user.id

    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: ([], None))
    process_job(db_session, job, _FakeExtractor({}))

    next_job = sync_service.enqueue_sync(db_session, user_id)

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

    monkeypatch.setattr(worker_module, "reap_stale_jobs", lambda db, job_type=None: reap_calls.append(db))
    monkeypatch.setattr(worker_module, "claim_next_job", lambda db, job_type=None: claim_calls.append(db) or None)
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

    def fake_reap(db, job_type=None):
        raise RuntimeError("db blip")

    monkeypatch.setattr(worker_module, "reap_stale_jobs", fake_reap)
    monkeypatch.setattr(worker_module, "claim_next_job", lambda db, job_type=None: claim_calls.append(db) or None)
    monkeypatch.setattr(worker_module, "SessionLocal", _StubSession)

    class _StopLoop(Exception):
        pass

    def fake_sleep(seconds):
        raise _StopLoop

    monkeypatch.setattr(worker_module, "time", _FakeTime(fake_sleep))

    with pytest.raises(_StopLoop):
        worker_module.run_forever(poll_interval=0)

    assert len(claim_calls) == 1


def test_get_last_poll_at_returns_none_before_any_poll(monkeypatch) -> None:
    import app.sync.worker as worker_module

    monkeypatch.setattr(worker_module, "_last_poll_at", None)
    assert get_last_poll_at() is None


def test_get_last_poll_at_returns_the_recorded_timestamp(monkeypatch) -> None:
    import app.sync.worker as worker_module

    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(worker_module, "_last_poll_at", fixed)
    assert get_last_poll_at() == fixed


def test_drain_once_processes_all_queued_jobs_and_returns_the_count(engine, monkeypatch) -> None:
    import app.sync.worker as worker_module

    # Uses the real `engine` fixture, not `db_session` — drain_once() creates
    # its own real SessionLocal() sessions internally (exactly like
    # run_forever() already does). app.db.session.SessionLocal is bound to
    # settings.database_url (the dev database), which is a genuinely
    # separate Postgres database from this `engine` fixture (bound to
    # settings.database_url's name + "_test") — not just a separate
    # connection to the same one. So drain_once()'s internal sessions are
    # monkeypatched below to use this test engine instead, or they'd never
    # see the rows this test commits. Mirrors the existing pattern in
    # test_claim_next_job_skips_a_row_locked_by_another_connection (same
    # file) for the "genuinely separate connection" part, and
    # test_run_forever_reaps_stale_jobs_every_iteration (same file) for the
    # "patch worker_module.SessionLocal" part. No GmailConnection rows are
    # created, so each job takes process_job's fast "connection is None" ->
    # "failed" path — this test is about drain_once's own looping/counting behavior,
    # not full Gmail processing (already covered elsewhere).
    #
    # NOTE: each job needs its own user, not just its own row — sync_jobs
    # has a partial unique index, uq_sync_jobs_user_active
    # (UNIQUE(user_id) WHERE status IN ('queued','running')), enforcing at
    # most one active job per user (see CLAUDE.md). Three queued jobs for
    # the same user would violate that constraint on insert.
    with engine.begin() as setup_conn:
        user_ids = [
            setup_conn.execute(
                User.__table__.insert()
                .values(
                    google_sub=f"drain-test-sub-{i}",
                    email=f"drain-test-{i}@example.com",
                    name="Drain Test",
                )
                .returning(User.__table__.c.id)
            ).scalar_one()
            for i in range(3)
        ]
        job_ids = [
            setup_conn.execute(
                SyncJob.__table__.insert()
                .values(user_id=user_id, job_type="initial", window_start=date(2026, 1, 1))
                .returning(SyncJob.__table__.c.id)
            ).scalar_one()
            for user_id in user_ids
        ]

    try:
        # app/db/session.py's module-level SessionLocal is bound to
        # settings.database_url (the dev database), while the `engine`
        # fixture above is bound to a genuinely separate database
        # (settings.database_url's name + "_test", created/migrated by
        # conftest.py) — two isolated Postgres databases, not just two
        # connections to the same one. drain_once() calls SessionLocal()
        # internally (necessarily, per its production Cron Job use), so
        # without this patch it would query the dev database and never see
        # the rows committed above via engine.begin(). Patching
        # app.sync.worker.SessionLocal (the name worker.py imported into its
        # own module namespace) to a sessionmaker bound to this test's real
        # `engine` fixture is what makes drain_once's internal sessions see
        # them. Mirrors how app/db/session.py itself constructs SessionLocal
        # (same sessionmaker kwargs), just bound to the test engine instead
        # of settings.database_url. monkeypatch reverts this automatically
        # at test teardown, so there's no cross-test leakage risk.
        test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        monkeypatch.setattr(worker_module, "SessionLocal", test_session_factory)

        processed = drain_once()

        assert processed == 3
        with engine.connect() as check_conn:
            statuses = check_conn.execute(
                SyncJob.__table__.select().where(SyncJob.__table__.c.id.in_(job_ids))
            ).fetchall()
            assert {row.status for row in statuses} == {"failed"}
            assert all(row.error_message == "Gmail connection no longer exists" for row in statuses)
    finally:
        with engine.begin() as cleanup_conn:
            cleanup_conn.execute(SyncJob.__table__.delete().where(SyncJob.__table__.c.id.in_(job_ids)))
            cleanup_conn.execute(User.__table__.delete().where(User.__table__.c.id.in_(user_ids)))


def test_drain_once_stops_at_its_time_budget_leaving_jobs_queued(engine, monkeypatch) -> None:
    import app.sync.worker as worker_module

    with engine.begin() as setup_conn:
        user_id = setup_conn.execute(
            User.__table__.insert()
            .values(google_sub="drain-budget-sub", email="drain-budget@example.com", name="Drain Budget")
            .returning(User.__table__.c.id)
        ).scalar_one()
        job_id = setup_conn.execute(
            SyncJob.__table__.insert()
            .values(user_id=user_id, job_type="initial", window_start=date(2026, 1, 1))
            .returning(SyncJob.__table__.c.id)
        ).scalar_one()

    try:
        test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        monkeypatch.setattr(worker_module, "SessionLocal", test_session_factory)

        processed = drain_once(max_runtime_seconds=0)

        assert processed == 0
        with engine.connect() as check_conn:
            row = check_conn.execute(
                SyncJob.__table__.select().where(SyncJob.__table__.c.id == job_id)
            ).one()
            assert row.status == "queued"
    finally:
        with engine.begin() as cleanup_conn:
            cleanup_conn.execute(SyncJob.__table__.delete().where(SyncJob.__table__.c.id == job_id))
            cleanup_conn.execute(User.__table__.delete().where(User.__table__.c.id == user_id))


def test_claim_next_job_filters_by_lane(db_session, user, other_user) -> None:
    initial = _make_job(user.id, job_type="initial")
    incremental = _make_job(other_user.id, job_type="incremental")
    db_session.add_all([initial, incremental])
    db_session.commit()

    claimed = claim_next_job(db_session, job_type="incremental")

    assert claimed.id == incremental.id
    db_session.refresh(initial)
    assert initial.status == "queued"


def test_reap_stale_jobs_only_reaps_its_own_lane(db_session, user, other_user) -> None:
    stale_initial = _make_job(user.id, job_type="initial", status="running")
    stale_incremental = _make_job(other_user.id, job_type="incremental", status="running")
    db_session.add_all([stale_initial, stale_incremental])
    db_session.commit()
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    _backdate_updated_at(db_session, stale_initial, old)
    _backdate_updated_at(db_session, stale_incremental, old)

    reap_stale_jobs(db_session, job_type="incremental")

    db_session.refresh(stale_initial)
    db_session.refresh(stale_incremental)
    assert stale_incremental.status == "queued"
    assert stale_initial.status == "running"


def test_two_lanes_claim_concurrently_without_blocking_or_stealing(engine) -> None:
    # Same committed-rows technique as
    # test_claim_next_job_skips_a_row_locked_by_another_connection: lane A
    # holds a row lock on its initial job in one real connection while lane B,
    # in a second connection, claims its incremental job — B must neither
    # block on A's lock nor take A's row.
    with engine.begin() as setup_conn:
        user_ids = [
            setup_conn.execute(
                User.__table__.insert()
                .values(google_sub=f"lane-sub-{i}", email=f"lane-{i}@example.com", name="Lane")
                .returning(User.__table__.c.id)
            ).scalar_one()
            for i in range(2)
        ]
        initial_id = setup_conn.execute(
            SyncJob.__table__.insert()
            .values(user_id=user_ids[0], job_type="initial", window_start=date(2026, 1, 1))
            .returning(SyncJob.__table__.c.id)
        ).scalar_one()
        incremental_id = setup_conn.execute(
            SyncJob.__table__.insert()
            .values(user_id=user_ids[1], job_type="incremental", window_start=date(2026, 1, 1))
            .returning(SyncJob.__table__.c.id)
        ).scalar_one()

    try:
        conn_a = engine.connect()
        conn_b = engine.connect()
        session_a = Session(bind=conn_a)
        session_b = Session(bind=conn_b)
        try:
            txn_a = conn_a.begin()
            session_a.execute(
                SyncJob.__table__.select()
                .where(SyncJob.__table__.c.id == initial_id)
                .with_for_update()
            ).one()

            claimed_b = claim_next_job(session_b, job_type="incremental")
            assert claimed_b is not None and claimed_b.id == incremental_id
            assert claim_next_job(session_b, job_type="initial") is None
            txn_a.rollback()
        finally:
            session_a.close()
            session_b.close()
            conn_a.close()
            conn_b.close()
    finally:
        with engine.begin() as cleanup_conn:
            cleanup_conn.execute(delete(SyncJob).where(SyncJob.id.in_([initial_id, incremental_id])))
            cleanup_conn.execute(delete(User).where(User.id.in_(user_ids)))


class _FakeClock:
    """Replaces worker.time in drain_once tests: monotonic() only advances
    when sleep() is called, so budget arithmetic is deterministic."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _commit_user_and_job(engine, tag: str, **job_values):
    with engine.begin() as conn:
        user_id = conn.execute(
            User.__table__.insert()
            .values(google_sub=f"{tag}-sub", email=f"{tag}@example.com", name=tag)
            .returning(User.__table__.c.id)
        ).scalar_one()
        values = dict(user_id=user_id, job_type="initial", window_start=date(2026, 1, 1))
        values.update(job_values)
        job_id = conn.execute(
            SyncJob.__table__.insert().values(**values).returning(SyncJob.__table__.c.id)
        ).scalar_one()
    return user_id, job_id


def _cleanup_committed(engine, user_ids, job_ids) -> None:
    with engine.begin() as conn:
        conn.execute(delete(SyncJob).where(SyncJob.id.in_(job_ids)))
        conn.execute(delete(User).where(User.id.in_(user_ids)))


def _job_statuses(engine, job_ids) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(SyncJob.__table__.select().where(SyncJob.__table__.c.id.in_(job_ids)))
        return {row.id: row.status for row in rows}


def test_drain_once_with_a_lane_leaves_other_lane_jobs_queued(engine, monkeypatch) -> None:
    import app.sync.worker as worker_module

    u1, initial_id = _commit_user_and_job(engine, "lane-drain-a", job_type="initial")
    u2, incremental_id = _commit_user_and_job(engine, "lane-drain-b", job_type="incremental")
    try:
        monkeypatch.setattr(worker_module, "SessionLocal", sessionmaker(bind=engine, future=True))

        assert drain_once(job_type="incremental") == 1

        statuses = _job_statuses(engine, [initial_id, incremental_id])
        assert statuses[incremental_id] == "failed"  # no Gmail connection -> fast fail
        assert statuses[initial_id] == "queued"
    finally:
        _cleanup_committed(engine, [u1, u2], [initial_id, incremental_id])


def test_drain_once_waits_for_a_retry_due_within_its_budget(engine, monkeypatch) -> None:
    import time as real_time

    import app.sync.worker as worker_module

    due = datetime.now(timezone.utc) + timedelta(seconds=2)
    user_id, job_id = _commit_user_and_job(engine, "retry-wait", job_type="incremental", next_attempt_at=due)
    try:
        monkeypatch.setattr(worker_module, "SessionLocal", sessionmaker(bind=engine, future=True))
        clock = _FakeClock()

        def sleep_for_real(seconds: float) -> None:
            # next_attempt_at is compared against real wall-clock time.
            clock.sleeps.append(seconds)
            clock.now += seconds
            real_time.sleep(seconds)

        clock.sleep = sleep_for_real
        monkeypatch.setattr(worker_module, "time", clock)

        assert drain_once(max_runtime_seconds=60, job_type="incremental") == 1
        assert len(clock.sleeps) == 1 and 0 < clock.sleeps[0] <= 4
        assert _job_statuses(engine, [job_id])[job_id] == "failed"
    finally:
        _cleanup_committed(engine, [user_id], [job_id])


def test_drain_once_does_not_wait_for_a_retry_beyond_its_budget(engine, monkeypatch) -> None:
    import app.sync.worker as worker_module

    due = datetime.now(timezone.utc) + timedelta(hours=1)
    user_id, job_id = _commit_user_and_job(engine, "retry-far", job_type="incremental", next_attempt_at=due)
    try:
        monkeypatch.setattr(worker_module, "SessionLocal", sessionmaker(bind=engine, future=True))
        clock = _FakeClock()
        monkeypatch.setattr(worker_module, "time", clock)

        assert drain_once(max_runtime_seconds=60, job_type="incremental") == 0
        assert clock.sleeps == []
        assert _job_statuses(engine, [job_id])[job_id] == "queued"
    finally:
        _cleanup_committed(engine, [user_id], [job_id])


def test_drain_once_does_not_wait_for_another_lanes_retry(engine, monkeypatch) -> None:
    import app.sync.worker as worker_module

    due = datetime.now(timezone.utc) + timedelta(seconds=2)
    user_id, job_id = _commit_user_and_job(engine, "retry-other-lane", job_type="initial", next_attempt_at=due)
    try:
        monkeypatch.setattr(worker_module, "SessionLocal", sessionmaker(bind=engine, future=True))
        clock = _FakeClock()
        monkeypatch.setattr(worker_module, "time", clock)

        assert drain_once(max_runtime_seconds=60, job_type="incremental") == 0
        assert clock.sleeps == []
    finally:
        _cleanup_committed(engine, [user_id], [job_id])


def test_fetch_failure_log_line_does_not_contain_the_raw_message_id(
    db_session, user, monkeypatch, caplog
) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["rawid123"], None))

    def failing_fetch(token, mid):
        raise google_api.GoogleApiError("boom", status_code=404)

    monkeypatch.setattr(google_api, "get_message", failing_fetch)
    # conftest.py's Alembic run calls logging.config.fileConfig, which disables
    # every logger that already exists (including this one) — re-enable it so
    # caplog can see the line this test is about.
    monkeypatch.setattr(logging.getLogger("app.sync.worker"), "disabled", False)

    with caplog.at_level("WARNING"):
        process_job(db_session, job, extractor=_FakeExtractor({}))

    assert "rawid123" not in caplog.text
    assert "msg-" in caplog.text


def test_drain_once_exits_instead_of_spinning_when_a_due_job_is_locked_elsewhere(monkeypatch) -> None:
    # A due job that claim_next_job can't take (row-locked by another worker)
    # yields wait == 0; the drain must exit, not sleep-and-retry until its
    # budget runs out.
    import app.sync.worker as worker_module

    monkeypatch.setattr(worker_module, "SessionLocal", _StubSession)
    monkeypatch.setattr(worker_module, "_run_one_tick", lambda db, job_type=None: None)
    monkeypatch.setattr(worker_module, "_seconds_until_next_retry", lambda db, job_type: 0.0)
    clock = _FakeClock()
    monkeypatch.setattr(worker_module, "time", clock)

    assert drain_once(max_runtime_seconds=60, job_type="incremental") == 0
    assert clock.sleeps == []


def _no_sleep(monkeypatch) -> list[float]:
    import app.sync.worker as worker_module

    sleeps: list[float] = []
    monkeypatch.setattr(worker_module.time, "sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("status_code", [None, 429, 503])
def test_process_job_retries_a_transiently_failed_message_once(
    db_session, user, monkeypatch, status_code
) -> None:
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
