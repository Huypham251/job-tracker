import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.sync.models import SyncJob

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0


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


def process_job(db: Session, job: SyncJob) -> None:
    # Filled in by Task 7 (message processing), Task 8 (retries), and
    # Task 9 (watermark update). Placeholder that fails loudly so an
    # incomplete worker never silently marks jobs as done.
    raise NotImplementedError


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
