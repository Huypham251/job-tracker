from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.gmail import service as gmail_service
from app.gmail.exceptions import GmailNotConnected, GmailReauthRequired
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
    if connection.reauth_required_at is not None:
        # The grant is gone (Phase 10): a job would only fail — and dispatch
        # a worker run — so ask for a reconnect instead.
        raise GmailReauthRequired(user_id)

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


def should_rekick(job: SyncJob | None, now: datetime | None = None) -> bool:
    """Whether a Sync click that hit an already-active job should ask for a
    worker again (Phase 9): yes if the job is still waiting to be claimed, or
    if it's "running" but hasn't made progress within the stale threshold —
    the dispatched run's reaper then requeues and processes it. A healthy
    running job needs nothing."""
    if job is None:
        return False
    if job.status == "queued":
        return True
    now = now or datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=settings.sync_stale_job_threshold_minutes)
    return job.status == "running" and job.updated_at < stale_before
