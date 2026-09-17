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
