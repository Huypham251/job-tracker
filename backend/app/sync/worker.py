import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.classifier.extractor import Extractor, RuleBasedExtractor
from app.core.config import settings
from app.core.privacy import message_ref
from app.db.session import SessionLocal
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.google_api import GoogleApiError
from app.pipeline import service as pipeline_service
from app.pipeline.models import ProcessedMessage
from app.sync.models import SyncJob
from app.users.models import User  # noqa: F401 — see test_sync_worker_entrypoint.py:
# GmailConnection.owner and Application.owner both reference "User" as a string
# relationship, resolved lazily by SQLAlchemy's class registry the first time any
# mapper configures. Neither gmail/models.py nor applications/models.py imports
# User at runtime (only under TYPE_CHECKING), so without this explicit import
# here, a worker process started standalone (unlike the FastAPI app or the test
# suite, both of which import app.users.models some other way first) crashes on
# its first query with "expression 'User' failed to locate a name".

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
PAGE_SIZE = 100
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600

_last_poll_at: datetime | None = None


def get_last_poll_at() -> datetime | None:
    """Used by app/main.py's /health/worker endpoint to check the in-process
    worker thread (app/sync/inprocess.py) is actually making progress, not
    just technically alive — a plain thread.is_alive() check would almost
    never go false, since run_forever()'s own broad exception handling means
    the thread practically never dies even when stuck."""
    return _last_poll_at


def _record_poll() -> None:
    """Records that the worker loop is alive and making progress — called
    once per run_forever() tick AND once per message inside process_job's
    inner loop, so a single long-running sync job (many messages, each with
    its own Gmail API calls) doesn't make /health/worker falsely report
    'stale' just because run_forever()'s own outer loop hasn't ticked again
    yet. Found via real production verification: a 300-message initial sync
    legitimately ran for several minutes inside one process_job() call,
    during which the outer-loop-only heartbeat sat frozen the entire time."""
    global _last_poll_at
    _last_poll_at = datetime.now(timezone.utc)


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


def reap_stale_jobs(db: Session, job_type: str | None = None) -> None:
    """A 'running' job whose updated_at hasn't advanced in
    settings.sync_stale_job_threshold_minutes was orphaned by a crash in one of
    process_job's own bookkeeping commits (a DB-level failure inside
    get_connection, the no-connection branch, the exception handler itself, or
    the success tail — not a Gmail API failure, which the try/except above
    already handles) — nothing else re-claims a non-'queued' row, so without
    this it would block that user's syncs forever via uq_sync_jobs_user_active.
    Reuses the same attempts/backoff/terminal-failure semantics process_job's
    own exception handler already has; page_token is left untouched so a
    requeued job resumes from its last checkpoint. job_type limits it to one
    lane (Phase 9), so a lane's worker never touches the other lane's jobs."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.sync_stale_job_threshold_minutes)
    # skip_locked, matching claim_next_job's idiom, so this stays safe if a
    # second worker process is ever introduced: a row genuinely still being
    # written by a live process is held by that process's own transaction
    # and gets skipped here rather than double-processed.
    query = select(SyncJob).where(SyncJob.status == "running", SyncJob.updated_at < cutoff)
    if job_type is not None:
        query = query.where(SyncJob.job_type == job_type)
    stale_jobs = db.scalars(query.with_for_update(skip_locked=True)).all()
    for job in stale_jobs:
        logger.warning("Reaping stale sync job %s (no progress since %s)", job.id, job.updated_at)
        _requeue_or_fail(job, "Sync stalled and was not recovered automatically. Try syncing again.")
    if stale_jobs:
        db.commit()


def claim_next_job(db: Session, job_type: str | None = None) -> SyncJob | None:
    query = select(SyncJob).where(
        SyncJob.status == "queued", SyncJob.next_attempt_at <= datetime.now(timezone.utc)
    )
    if job_type is not None:
        query = query.where(SyncJob.job_type == job_type)
    job = db.scalars(
        query.order_by(SyncJob.created_at).limit(1).with_for_update(skip_locked=True)
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
                _record_poll()
                if message_id in already_processed_ids:
                    continue

                try:
                    summary, body = google_api.get_message(access_token, message_id)
                except GoogleApiError:
                    logger.warning("Skipping message %s: fetch failed", message_ref(message_id))
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

            # Bound this job's memory footprint to roughly one page's worth of
            # SQLAlchemy-tracked objects rather than letting it grow with the
            # job's total message count — found via a real Render OOM restart
            # during a 300-message initial sync. expunge_all() drops every
            # object this session has accumulated from its identity map (safe
            # to garbage-collect); job and connection are the only two objects
            # the rest of this function still needs, so they're immediately
            # re-attached. The session itself stays open throughout, so their
            # now-expired attributes correctly lazy-reload on next access
            # rather than raising DetachedInstanceError (which WOULD happen if
            # the session were closed instead of just having objects expunged).
            db.expunge_all()
            db.add(job)
            db.add(connection)

            if next_page_token is None:
                break
    except Exception as exc:
        # Roll back BEFORE touching `job` — if `exc` came from a failed DB
        # statement, the session needs a rollback before it can run anything
        # else, including the implicit SELECT that refreshing an expired ORM
        # attribute (job.id, job.attempts — expire_on_commit=True) requires.
        # Logging first would raise PendingRollbackError on exactly that class
        # of failure, masking the original exception and leaving the job
        # stuck "running" instead of being requeued.
        db.rollback()
        logger.exception("Sync job %s failed on attempt %s", job.id, job.attempts + 1)
        _requeue_or_fail(job, _safe_error_message(exc))
        db.commit()
        return

    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    if job.started_at is not None:
        connection.last_synced_message_date = job.started_at.astimezone(timezone.utc).date()
    db.commit()


def _run_one_tick(db: Session, job_type: str | None = None) -> SyncJob | None:
    """One reap+claim+process cycle — shared by run_forever() (loops forever,
    sleeping between empty ticks; used by local dev's separate
    `python -m app.sync.worker` process, per CLAUDE.md's documented
    workflow) and drain_once() below (loops until the queue is empty or a
    time budget is hit, then returns; used by the GitHub Actions workflows
    in production, via app/sync/drain.py). Returns the claimed job, if any, so callers
    can tell an empty tick (nothing to do) from a worked one. job_type, when
    given, restricts both reaping and claiming to that lane."""
    try:
        reap_stale_jobs(db, job_type=job_type)
    except Exception:
        # A best-effort janitor must never block the primary work — roll
        # back to a clean session, then log, and still attempt
        # claim_next_job below in this same tick. Rollback-before-log
        # matches process_job's own handler for the same reason: this
        # message logs no ORM attributes today, but keeping the ordering
        # uniform means it stays safe if one is ever added here.
        db.rollback()
        logger.exception("Stale-job reaper failed; continuing")
    job = claim_next_job(db, job_type=job_type)
    if job is not None:
        process_job(db, job)
    return job


def run_forever(poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
    while True:
        _record_poll()
        db = SessionLocal()
        job = None
        try:
            job = _run_one_tick(db)
        except Exception:
            logger.exception("Unhandled error in sync worker loop")
            db.rollback()
        finally:
            db.close()
        if job is None:
            time.sleep(poll_interval)


DRAIN_MAX_RUNTIME_SECONDS = 240.0

# Per-lane drain budgets for the GitHub Actions workflows (Phase 9), each below
# its workflow's timeout-minutes (30 / 120) so a run exits cleanly between jobs
# instead of being killed mid-job.
LANE_DRAIN_BUDGET_SECONDS: dict[str, float] = {"incremental": 20 * 60.0, "initial": 100 * 60.0}

# A retry is requeued with next_attempt_at a little in the future; sleep this
# much past it so claim_next_job's `next_attempt_at <= now` is sure to pass.
_RETRY_WAIT_SLACK_SECONDS = 1.0


def _seconds_until_next_retry(db: Session, job_type: str | None) -> float | None:
    query = select(func.min(SyncJob.next_attempt_at)).where(SyncJob.status == "queued")
    if job_type is not None:
        query = query.where(SyncJob.job_type == job_type)
    next_at = db.scalar(query)
    if next_at is None:
        return None
    return max(0.0, (next_at - datetime.now(timezone.utc)).total_seconds())


def drain_once(max_runtime_seconds: float = DRAIN_MAX_RUNTIME_SECONDS, job_type: str | None = None) -> int:
    """Processes queued sync jobs (only one lane's job_type, if given) until
    none are due or max_runtime_seconds is exceeded, then returns the number
    processed. Used by the GitHub Actions workflows via app/sync/drain.py —
    production never runs the worker in the API process. When nothing is due
    but a queued retry becomes due within the remaining budget, it waits for
    that retry instead of leaving it for the next run, which may be a
    cron-triggered one hours away. Local dev keeps using run_forever(), as
    documented in CLAUDE.md's "Local dev environment" section."""
    start = time.monotonic()
    processed = 0
    while time.monotonic() - start < max_runtime_seconds:
        _record_poll()
        db = SessionLocal()
        job = None
        wait = None
        try:
            job = _run_one_tick(db, job_type=job_type)
            if job is None:
                wait = _seconds_until_next_retry(db, job_type)
        except Exception:
            logger.exception("Unhandled error in drain loop")
            db.rollback()
        finally:
            db.close()
        if job is not None:
            processed += 1
            continue
        remaining = max_runtime_seconds - (time.monotonic() - start)
        # wait == 0 means a due job exists that claim_next_job couldn't take
        # (row-locked by another worker) — leave it to that worker rather
        # than re-polling every second until the budget runs out.
        if wait is None or wait <= 0 or wait + _RETRY_WAIT_SLACK_SECONDS >= remaining:
            return processed
        logger.info("Waiting %.0fs for a queued retry", wait)
        time.sleep(wait + _RETRY_WAIT_SLACK_SECONDS)
    logger.warning("drain_once hit its %.0fs time budget with jobs possibly still queued", max_runtime_seconds)
    return processed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
