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
