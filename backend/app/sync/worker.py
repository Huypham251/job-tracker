import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classifier.extractor import Extractor, RuleBasedExtractor
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
    # The watermark update (Task 9) is not part of this implementation yet.
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
    if job.started_at is not None:
        connection.last_synced_message_date = job.started_at.astimezone(timezone.utc).date()
    db.commit()


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
