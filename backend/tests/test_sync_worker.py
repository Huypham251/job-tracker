from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.classifier.schemas import EmailExtraction
from app.gmail import google_api
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.pipeline.models import ProcessedMessage
from app.sync.models import SyncJob
from app.sync.worker import claim_next_job, process_job
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
