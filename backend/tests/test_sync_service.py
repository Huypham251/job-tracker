from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.gmail.crypto import encrypt_token
from app.gmail.exceptions import GmailNotConnected, GmailReauthRequired
from app.gmail.models import GmailConnection
from app.sync import service
from app.sync.exceptions import SyncAlreadyRunning
from app.sync.models import SyncJob


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


def test_enqueue_sync_refuses_a_connection_that_needs_reconnect(db_session, user) -> None:
    connection = _connect_gmail(db_session, user)
    connection.reauth_required_at = datetime.now(timezone.utc)
    db_session.commit()

    with pytest.raises(GmailReauthRequired):
        service.enqueue_sync(db_session, user.id)

    assert db_session.query(SyncJob).count() == 0


def test_enqueue_after_reconnect_is_incremental(db_session, user) -> None:
    # connect() clears reauth_required_at but keeps the watermark.
    _connect_gmail(db_session, user, last_synced_message_date=date(2026, 9, 1))

    job = service.enqueue_sync(db_session, user.id)

    assert job.job_type == "incremental"
    assert job.window_start == date(2026, 8, 31)
