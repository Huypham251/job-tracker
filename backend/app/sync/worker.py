import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classifier.extractor import Extractor, RuleBasedExtractor
from app.core.config import settings
from app.db.session import SessionLocal
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.google_api import GoogleApiError
from app.pipeline import service as pipeline_service
from app.pipeline.models import ProcessedMessage
from app.sync.models import SyncJob

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
PAGE_SIZE = 100
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600


def _backoff_seconds(attempts: int) -> float:
    return min(BASE_BACKOFF_SECONDS * (2**attempts), MAX_BACKOFF_SECONDS)


def _requeue_or_fail(job: SyncJob, error_message: str) -> None:
    """Shared by process_job's own exception handler and reap_stale_jobs — a
    reaped job is recovering from exactly the same class of problem a caught
    exception is, just detected from outside process_job instead of from
    within it, so both use the same attempts/backoff/terminal-failure rule."""
    job.attempts += 1
    if job.attempts < job.max_attempts:
        job.status = "queued"
        job.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=_backoff_seconds(job.attempts))
    else:
        job.status = "failed"
        job.error_message = error_message
        job.finished_at = datetime.now(timezone.utc)


def _safe_error_message(exc: Exception) -> str:
    """The full exception (str(exc) + traceback) always goes to logger.exception()
    at the call site — this is only what's persisted to SyncJob.error_message and
    therefore rendered to the browser, so it must never carry SQL text, bound
    parameters, or other internal detail."""
    if isinstance(exc, GoogleApiError):
        return "Couldn't reach Gmail. This is usually temporary — try syncing again shortly."
    return "Sync failed due to an unexpected error. Try again; contact support if this keeps happening."


def reap_stale_jobs(db: Session) -> None:
    """A 'running' job whose updated_at hasn't advanced in
    settings.sync_stale_job_threshold_minutes was orphaned by a crash in one of
    process_job's own bookkeeping commits (a DB-level failure inside
    get_connection, the no-connection branch, the exception handler itself, or
    the success tail — not a Gmail API failure, which the try/except above
    already handles) — nothing else re-claims a non-'queued' row, so without
    this it would block that user's syncs forever via uq_sync_jobs_user_active.
    Reuses the same attempts/backoff/terminal-failure semantics process_job's
    own exception handler already has; page_token is left untouched so a
    requeued job resumes from its last checkpoint."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.sync_stale_job_threshold_minutes)
    stale_jobs = db.scalars(
        select(SyncJob).where(SyncJob.status == "running", SyncJob.updated_at < cutoff)
    ).all()
    for job in stale_jobs:
        logger.warning("Reaping stale sync job %s (no progress since %s)", job.id, job.updated_at)
        _requeue_or_fail(job, "Sync stalled and was not recovered automatically. Try syncing again.")
    if stale_jobs:
        db.commit()


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
        while True:
            access_token = gmail_service.get_valid_access_token(db, connection)

            message_ids, next_page_token = google_api.list_message_ids_page(
                access_token, query=query, page_token=job.page_token, max_results=PAGE_SIZE
            )
            job.messages_seen += len(message_ids)
            db.commit()

            already_processed_ids = set(
                db.scalars(
                    select(ProcessedMessage.gmail_message_id).where(
                        ProcessedMessage.user_id == job.user_id,
                        ProcessedMessage.gmail_message_id.in_(message_ids),
                    )
                ).all()
            )

            for message_id in message_ids:
                if message_id in already_processed_ids:
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
    except Exception as exc:
        logger.exception("Sync job %s failed on attempt %s", job.id, job.attempts + 1)
        db.rollback()
        _requeue_or_fail(job, _safe_error_message(exc))
        db.commit()
        return

    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    if job.started_at is not None:
        connection.last_synced_message_date = job.started_at.astimezone(timezone.utc).date()
    db.commit()


def run_forever(poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
    while True:
        db = SessionLocal()
        job = None
        try:
            reap_stale_jobs(db)
            job = claim_next_job(db)
            if job is not None:
                process_job(db, job)
        except Exception:
            logger.exception("Unhandled error in sync worker loop")
            db.rollback()
        finally:
            db.close()
        if job is None:
            time.sleep(poll_interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
