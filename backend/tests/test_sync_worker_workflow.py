from pathlib import Path

import yaml

from app.sync.worker import DRAIN_MAX_RUNTIME_SECONDS

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sync-worker.yml"

# GitHub's own default job timeout — a timeout-minutes at or above this adds
# no protection at all.
GITHUB_DEFAULT_TIMEOUT_MINUTES = 360


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def test_sync_worker_workflow_never_runs_two_drains_at_once() -> None:
    """Production has only ever been verified with a single worker (see
    CLAUDE.md's "Multi-worker is unverified" gap), so overlapping scheduled
    runs must queue behind each other rather than run in parallel. And
    cancel-in-progress must stay false: cancelling a drain mid-job leaves
    that SyncJob stuck "running" until a later run's reaper requeues it
    with attempts += 1 — three such cancellations permanently fail it."""
    concurrency = _load_workflow().get("concurrency")
    assert isinstance(concurrency, dict), "sync-worker.yml needs a top-level concurrency block"
    assert concurrency.get("group"), "concurrency.group must be set"
    assert "${{" not in concurrency["group"], "group must be fixed, not per-run/per-ref"
    assert concurrency.get("cancel-in-progress") is False


def test_sync_worker_workflow_times_out_well_after_a_realistic_job() -> None:
    """drain_once's time budget is only checked between jobs — process_job
    itself is unbounded, and a 180-day initial backfill can legitimately run
    over an hour. The job timeout must sit above that (killing a job
    mid-run costs it an attempt, per the test above) but below GitHub's
    360-minute default, or it protects against nothing."""
    job = _load_workflow()["jobs"]["drain"]
    timeout = job.get("timeout-minutes")
    assert isinstance(timeout, int), "jobs.drain.timeout-minutes must be set"
    assert timeout * 60 > DRAIN_MAX_RUNTIME_SECONDS
    assert 90 <= timeout < GITHUB_DEFAULT_TIMEOUT_MINUTES
