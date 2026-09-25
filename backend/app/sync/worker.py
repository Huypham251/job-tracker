import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Literal, NamedTuple

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.classifier.extractor import Extractor, RuleBasedExtractor
from app.core.config import settings
from app.core.privacy import message_ref
from app.db.session import SessionLocal
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.exceptions import REAUTH_CODE, REAUTH_MESSAGE
from app.gmail.google_api import GmailAuthError, GoogleApiError
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
# One in-place retry for a single message's transient fetch failure (Phase 10).
PER_MESSAGE_RETRY_SECONDS = 2.0

_last_poll_at: datetime | None = None

LANES = ("incremental", "initial")

# What process_job did with the job: finished it, gave up on it, requeued it
# with backoff after an error, or checkpointed and requeued it at its slice
# deadline (Phase 10).
JobOutcome = Literal["completed", "failed", "retrying", "yielded"]


def lane_slice_seconds(lane: str) -> float:
    return float(
        settings.sync_initial_slice_seconds if lane == "initial" else settings.sync_incremental_slice_seconds
    )


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


def _is_transient(exc: GoogleApiError) -> bool:
    return exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500


def _describe_failure(exc: GoogleApiError) -> str:
    return "network error" if exc.status_code is None else f"HTTP {exc.status_code}"


def _fetch_message(access_token: str, message_id: str) -> tuple[dict, str]:
    """get_message with one in-place retry for a transient failure (429, 5xx,
    network). A message skipped here is never written to ProcessedMessage, so
    if it's older than the next incremental window it's never seen again."""
    try:
        return google_api.get_message(access_token, message_id)
    except GmailAuthError:
        raise
    except GoogleApiError as exc:
        if not _is_transient(exc):
            raise
        time.sleep(PER_MESSAGE_RETRY_SECONDS)
        return google_api.get_message(access_token, message_id)


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


def sweep_orphans(db: Session, job_type: str) -> int:
    """Runs once when a lane drain starts (Phase 10). The lane workflow's
    concurrency group means no other production run of this lane is working
    a job right now, so a "running" job that hasn't committed in
    sync_orphan_threshold_seconds was abandoned by a killed run (cancelled,
    timed out, runner lost) — recover it now instead of waiting out
    reap_stale_jobs' 15 minutes. A healthy job commits after every message.
    Never used by run_forever or the in-process worker, where that
    one-worker-per-lane guarantee doesn't hold. Like the reaper it costs an
    attempt, so a job that keeps killing its run ends up failed instead of
    looping forever — but it's due again immediately, with no backoff."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.sync_orphan_threshold_seconds)
    orphans = db.scalars(
        select(SyncJob)
        .where(SyncJob.status == "running", SyncJob.job_type == job_type, SyncJob.updated_at < cutoff)
        .with_for_update(skip_locked=True)
    ).all()
    for job in orphans:
        logger.warning("Recovering orphaned sync job %s (no progress since %s)", job.id, job.updated_at)
        _requeue_or_fail(job, "Sync stalled and was not recovered automatically. Try syncing again.")
        if job.status == "queued":
            # Due now, not after backoff: backoff gives a flaky upstream time
            # to recover, but this job's "failure" was its killed runner.
            job.next_attempt_at = datetime.now(timezone.utc)
    if orphans:
        db.commit()
    return len(orphans)


def _seconds_until_running_job_is_orphaned(db: Session, job_type: str) -> float | None:
    """How long until the most recently active "running" job in this lane
    crosses sync_orphan_threshold_seconds, or None if none is running."""
    age = db.scalar(
        select(func.extract("epoch", text("clock_timestamp()") - func.max(SyncJob.updated_at))).where(
            SyncJob.status == "running", SyncJob.job_type == job_type
        )
    )
    if age is None:
        return None
    return max(0.0, settings.sync_orphan_threshold_seconds - float(age))


def _sweep_lane(job_type: str, deadline: float) -> None:
    """sweep_orphans at drain start, plus one bounded wait: a run killed just
    before this drain started (say, a queued dispatch starting right after a
    cancel) leaves a "running" job too fresh for the threshold. The lane's
    concurrency group means it's orphaned all the same, so wait out the rest
    of the threshold and sweep again rather than leaving it for the 15-minute
    reaper. Best-effort, like the reaper: never blocks real work."""
    db = SessionLocal()
    try:
        sweep_orphans(db, job_type)
        wait = _seconds_until_running_job_is_orphaned(db, job_type)
        db.commit()  # don't sit idle in a transaction while waiting
        if wait is not None and 0 < wait + _RETRY_WAIT_SLACK_SECONDS < deadline - time.monotonic():
            logger.info("Waiting %.0fs to recover a sync job abandoned by a previous run", wait)
            time.sleep(wait + _RETRY_WAIT_SLACK_SECONDS)
            sweep_orphans(db, job_type)
    except Exception:
        db.rollback()
        logger.exception("Orphan sweep failed; continuing")
    finally:
        db.close()


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


def process_job(
    db: Session, job: SyncJob, extractor: Extractor | None = None, *, deadline: float | None = None
) -> JobOutcome:
    """deadline is a time.monotonic() value: once it has passed, the job
    yields at the next page boundary instead of starting another page."""
    extractor = extractor or RuleBasedExtractor()
    connection = gmail_service.get_connection(db, job.user_id)
    if connection is None:
        job.status = "failed"
        job.error_message = "Gmail connection no longer exists"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return "failed"

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
                    summary, body = _fetch_message(access_token, message_id)
                except GmailAuthError:
                    # The grant is gone — every remaining message would fail
                    # the same way, so abort the job (handled below).
                    raise
                except GoogleApiError as exc:
                    logger.warning(
                        "Skipping message %s: fetch failed (%s)", message_ref(message_id), _describe_failure(exc)
                    )
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
            if deadline is not None and time.monotonic() >= deadline:
                # Safe checkpoint: every message on the page has its own
                # committed ProcessedMessage row (or is counted as failed),
                # and page_token points at the next unprocessed page. Requeue
                # without using an attempt; started_at (the watermark basis)
                # and the counters carry over to the next slice.
                job.status = "queued"
                job.next_attempt_at = datetime.now(timezone.utc)
                db.commit()
                logger.info("Sync job %s paused at its slice deadline; it will continue in a new run", job.id)
                return "yielded"
    except GmailAuthError:
        # Revoked, expired or unreadable grant: retrying can't help, so fail
        # now (attempts untouched) and flag the connection so the UI offers a
        # reconnect and enqueue_sync refuses doomed jobs. Same rollback-first
        # rule as the generic handler below.
        db.rollback()
        logger.warning("Sync job %s stopped: Gmail authorization is no longer valid", job.id)
        now = datetime.now(timezone.utc)
        job.status = "failed"
        job.error_code = REAUTH_CODE
        job.error_message = REAUTH_MESSAGE
        job.finished_at = now
        connection.reauth_required_at = now
        db.commit()
        return "failed"
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
        return "retrying" if job.status == "queued" else "failed"

    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    if job.started_at is not None:
        connection.last_synced_message_date = job.started_at.astimezone(timezone.utc).date()
    db.commit()
    return "completed"


def _run_one_tick(
    db: Session, job_type: str | None = None, deadline: float | None = None
) -> tuple[SyncJob | None, JobOutcome | None]:
    """One reap+claim+process cycle — shared by run_forever() (loops forever,
    sleeping between empty ticks; used by local dev's separate
    `python -m app.sync.worker` process, per CLAUDE.md's documented
    workflow) and drain_once() below (loops until the queue is empty or a
    time budget is hit, then returns; used by the GitHub Actions workflows
    in production, via app/sync/drain.py). Returns the claimed job, if any, so callers
    can tell an empty tick (nothing to do) from a worked one, plus what
    process_job did with it. job_type, when given, restricts both reaping and
    claiming to that lane; deadline is passed through to process_job."""
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
    if job is None:
        return None, None
    return job, process_job(db, job, deadline=deadline)


def run_forever(poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
    while True:
        _record_poll()
        db = SessionLocal()
        job = None
        try:
            job, _ = _run_one_tick(db)
        except Exception:
            logger.exception("Unhandled error in sync worker loop")
            db.rollback()
        finally:
            db.close()
        if job is None:
            time.sleep(poll_interval)


DRAIN_MAX_RUNTIME_SECONDS = 240.0


class DrainResult(NamedTuple):
    processed: int
    # True when a job was checkpointed at the slice deadline and requeued —
    # the lane workflow then dispatches itself again to continue it.
    requeued: bool

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


def drain_once(
    max_runtime_seconds: float = DRAIN_MAX_RUNTIME_SECONDS,
    job_type: str | None = None,
    *,
    sweep: bool = False,
) -> DrainResult:
    """Processes queued sync jobs (only one lane's job_type, if given) until
    none are due or max_runtime_seconds is used up. Used by the GitHub Actions
    workflows via app/sync/drain.py — production never runs the worker in the
    API process. The budget is also each job's slice deadline (Phase 10): a
    job still going when it runs out is checkpointed at a page boundary and
    requeued, and the drain stops and reports requeued=True so the workflow
    can start the next slice. sweep=True (lane drains only) recovers the
    lane's orphaned jobs first, see sweep_orphans. When nothing is due but a
    queued retry becomes due within the remaining budget, it waits for that
    retry instead of leaving it for the next run, which may be a
    cron-triggered one hours away. Local dev keeps using run_forever(), as
    documented in CLAUDE.md's "Local dev environment" section."""
    start = time.monotonic()
    deadline = start + max_runtime_seconds
    processed = 0
    if sweep and job_type is not None:
        _sweep_lane(job_type, deadline)
    while time.monotonic() < deadline:
        _record_poll()
        db = SessionLocal()
        job = None
        outcome = None
        wait = None
        try:
            job, outcome = _run_one_tick(db, job_type=job_type, deadline=deadline)
            if job is None:
                wait = _seconds_until_next_retry(db, job_type)
        except Exception:
            logger.exception("Unhandled error in drain loop")
            db.rollback()
        finally:
            db.close()
        if job is not None:
            processed += 1
            if outcome == "yielded":
                return DrainResult(processed, True)
            continue
        remaining = deadline - time.monotonic()
        # wait == 0 means a due job exists that claim_next_job couldn't take
        # (row-locked by another worker) — leave it to that worker rather
        # than re-polling every second until the budget runs out.
        if wait is None or wait <= 0 or wait + _RETRY_WAIT_SLACK_SECONDS >= remaining:
            return DrainResult(processed, False)
        logger.info("Waiting %.0fs for a queued retry", wait)
        time.sleep(wait + _RETRY_WAIT_SLACK_SECONDS)
    logger.warning("drain_once hit its %.0fs time budget with jobs possibly still queued", max_runtime_seconds)
    return DrainResult(processed, False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
