"""Scheduled health check for the sync queue (Phase 10).

Run by .github/workflows/sync-monitor.yml. It exits 1 when anything needs the
maintainer's attention, and that failed run is the alert: GitHub emails the
repo owner. The workflow logs are public, so the output is counts, condition
IDs, error codes and 8-character job-ID prefixes only — never emails, user
IDs, message IDs or anything from a message.

  M1  a job has been due for 15+ minutes and no worker picked it up
      (dispatch broken or its token expired, workflows disabled, or a
      paused job's re-dispatch failed)
  M2  a running job hasn't made progress within the stale threshold
  M3  a job failed permanently since the last check (reported once each)
  M4  the dispatch token expires within 21 days
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.sync.models import SyncJob
# Every model the User mapper refers to by name, so this module configures its
# mappers when run on its own (`python -m app.sync.monitor`) — the same hazard
# worker.py documents; guarded by test_sync_drain_entrypoint.py.
from app.applications.models import Application  # noqa: F401
from app.gmail.models import GmailConnection  # noqa: F401
from app.users.models import User  # noqa: F401

logger = logging.getLogger(__name__)

DUE_UNCLAIMED_AFTER = timedelta(minutes=15)
TOKEN_EXPIRY_WARNING_DAYS = 21


class MonitorReport(NamedTuple):
    alerts: list[str]
    lines: list[str]


def _prefixes(job_ids) -> str:
    return "[" + ", ".join(str(job_id)[:8] for job_id in job_ids) + "]"


def evaluate(db: Session, *, now: datetime, today: date) -> MonitorReport:
    alerts: list[str] = []

    due_unclaimed = db.scalars(
        select(SyncJob.id).where(SyncJob.status == "queued", SyncJob.next_attempt_at < now - DUE_UNCLAIMED_AFTER)
    ).all()
    if due_unclaimed:
        alerts.append(f"M1 due-but-unclaimed jobs: {len(due_unclaimed)} {_prefixes(due_unclaimed)}")

    stale_before = now - timedelta(minutes=settings.sync_stale_job_threshold_minutes)
    stuck = db.scalars(
        select(SyncJob.id).where(SyncJob.status == "running", SyncJob.updated_at < stale_before)
    ).all()
    if stuck:
        alerts.append(f"M2 running jobs without progress: {len(stuck)} {_prefixes(stuck)}")

    newly_failed = db.scalars(
        select(SyncJob).where(SyncJob.status == "failed", SyncJob.alerted_at.is_(None)).order_by(SyncJob.created_at)
    ).all()
    if newly_failed:
        by_code: dict[str, int] = {}
        for job in newly_failed:
            code = job.error_code or "generic"
            by_code[code] = by_code.get(code, 0) + 1
        codes = ", ".join(f"{code}={count}" for code, count in sorted(by_code.items()))
        alerts.append(
            f"M3 newly failed jobs: {len(newly_failed)} ({codes}) {_prefixes(job.id for job in newly_failed)}"
        )
        for job in newly_failed:
            job.alerted_at = now
        db.commit()

    expires_on = settings.sync_dispatch_token_expires_on
    if expires_on is not None:
        days_left = (expires_on - today).days
        if days_left <= TOKEN_EXPIRY_WARNING_DAYS:
            alerts.append(
                f"M4 sync dispatch token expires in {days_left} day(s) on {expires_on.isoformat()}"
                " — rotate it (README, 'Production deployment')"
            )

    counts = dict(db.execute(select(SyncJob.status, func.count()).group_by(SyncJob.status)).all())
    summary = ", ".join(f"{status}={counts[status]}" for status in sorted(counts)) or "no jobs"
    lines = [f"Sync jobs by status: {summary}", *alerts]
    if not alerts:
        lines.append("All checks passed (M1–M4).")
    return MonitorReport(alerts, lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            report = evaluate(db, now=now, today=now.date())
        finally:
            db.close()
    except Exception as exc:
        # Being unable to check is itself worth an alert (e.g. the database
        # is down). Only the exception type: a connection error's text names
        # the database host, and these logs are public.
        logger.error("Monitor could not complete its checks: %s", type(exc).__name__)
        return 1

    for line in report.lines:
        (logger.error if line in report.alerts else logger.info)(line)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as fh:
            fh.write("## Sync monitor\n\n" + "".join(f"- {line}\n" for line in report.lines))
    return 1 if report.alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
