# Phase 9 — On-Demand Sync with Import/Incremental Lanes: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Sync Gmail" click starts the GitHub Actions worker within seconds via `workflow_dispatch`, with separate initial/incremental lanes, while cron stays as a fallback and a failed dispatch never fails the request.

**Architecture:** FastAPI commits the `SyncJob`, returns, and then (as a background task) asks GitHub to run `sync-<job_type>.yml`. Each lane's workflow runs `python -m app.sync.drain --lane <job_type>`, which reaps and claims only that lane's jobs and waits in the same run for near-term retries. Execution never moves into the Render API.

**Tech Stack:** FastAPI, SQLAlchemy 2 (sync), httpx, pytest; GitHub Actions; React + TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-24-job-tracker-phase-9-on-demand-sync-design.md`

## Global Constraints

- Lanes = `SyncJob.job_type` values `incremental` and `initial`; **no migration**.
- Workflow files: `.github/workflows/sync-incremental.yml` (timeout 30 min, drain budget 20 min) and `.github/workflows/sync-initial.yml` (timeout 120 min, drain budget 100 min); concurrency groups `sync-incremental` / `sync-initial`, `cancel-in-progress: false`; triggers `workflow_dispatch` + `schedule` `*/15 * * * *`.
- Dispatch: `POST https://api.github.com/repos/{repo}/actions/workflows/sync-{job_type}.yml/dispatches`, body `{"ref": <ref>}`, headers `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Authorization: Bearer <token>`; timeout 5s; 200 or 204 = success; **never raises**; logs lane + status only, never the token or body.
- Settings: `sync_dispatch_token: str | None = None`, `sync_dispatch_repository: str | None = None`, `sync_dispatch_ref: str = "main"`. Dispatch is disabled unless token **and** repository are set.
- A dispatch failure must never change the HTTP response: new job → 202 + `queued`; existing active job → 409 + job body (unchanged contract).
- `run_forever` (local dev) and a lane-less `python -m app.sync.drain` keep today's behavior (all lanes, 240s budget).
- Public-log privacy: `httpx`/`httpcore` at WARNING in the drain entrypoint; Gmail message IDs in log lines are replaced by `message_ref(id)` = `"msg-" + sha256(id).hexdigest()[:10]`.
- Secrets are never printed, logged, committed or documented by value.
- Each checkpoint: full local checks (`cd backend && uv run pytest && uv run python -m evaluation.compare`; `cd frontend && npx tsc -b && npx oxlint && npm run build`), a review pass, commit to `main`, push, confirm CI green.

## Review Focus

1. **Dispatch raises inside the background task** (e.g. an unexpected exception type) → the request has already returned; the job must remain `queued`, with nothing but a WARNING. Pinned in Task 4 (`test_request_worker_never_raises_on_unexpected_exception`, `test_start_sync_still_returns_202_when_dispatch_fails`).
2. **Lane leakage**: an initial-lane drain must never claim, reap or wait on an incremental job (and vice versa). Pinned in Task 2 (claim/reap/drain filter tests + two-connection test).
3. **Retry wait never exceeds the budget** and never loops forever when a queued job is in the far future. Pinned in Task 2 (`test_drain_once_does_not_wait_for_a_retry_beyond_its_budget`).
4. **Re-kick only when useful**: a healthy `running` job must not trigger a dispatch; a stale one must. Pinned in Task 4.
5. **Single-fetch parity**: the summary built from `format=full` must equal what `format=metadata` produced (same keys, same header values, snippet), including payloads without an `id`. Pinned in Task 5.

---

### Task 1: Commit spec + plan (Checkpoint 1)

**Files:** `docs/superpowers/specs/2026-09-24-job-tracker-phase-9-on-demand-sync-design.md`, this plan.

- [ ] **Step 1:** `git add` both files; commit `docs: add Phase 9 on-demand sync spec and implementation plan`; push to `main`; confirm CI green.

---

### Task 2: Worker lanes, retry-aware drain, public-log privacy (Checkpoint 2)

**Files:**
- Create: `backend/app/core/privacy.py`
- Modify: `backend/app/sync/worker.py`, `backend/app/sync/drain.py`, `backend/app/pipeline/service.py`
- Test: `backend/tests/test_sync_worker.py`, `backend/tests/test_sync_drain_entrypoint.py`, `backend/tests/test_core_privacy.py` (new)

**Interfaces:**
- Produces: `claim_next_job(db, job_type: str | None = None)`, `reap_stale_jobs(db, job_type: str | None = None)`, `drain_once(max_runtime_seconds: float = DRAIN_MAX_RUNTIME_SECONDS, job_type: str | None = None) -> int`, `LANE_DRAIN_BUDGET_SECONDS: dict[str, float] = {"incremental": 1200.0, "initial": 6000.0}`, `app.sync.drain.main(argv: list[str] | None = None) -> int`, `app.core.privacy.message_ref(message_id: str) -> str`.

- [ ] **Step 1: Failing tests (`tests/test_core_privacy.py`)**

```python
from app.core.privacy import message_ref


def test_message_ref_is_stable_short_and_does_not_contain_the_id() -> None:
    ref = message_ref("1a0d5527a5984162")
    assert ref == message_ref("1a0d5527a5984162")
    assert ref.startswith("msg-") and len(ref) == 14
    assert "1a0d5527a5984162" not in ref
    assert ref != message_ref("1a0d54ed644451fb")
```

- [ ] **Step 2: Failing tests (append to `tests/test_sync_worker.py`)**

```python
def test_claim_next_job_filters_by_lane(db_session, user, other_user) -> None:
    initial = _make_job(user.id, job_type="initial")
    incremental = _make_job(other_user.id, job_type="incremental")
    db_session.add_all([initial, incremental])
    db_session.commit()

    claimed = claim_next_job(db_session, job_type="incremental")

    assert claimed.id == incremental.id
    db_session.refresh(initial)
    assert initial.status == "queued"


def test_reap_stale_jobs_only_reaps_its_own_lane(db_session, user, other_user) -> None:
    stale_initial = _make_job(user.id, job_type="initial", status="running")
    stale_incremental = _make_job(other_user.id, job_type="incremental", status="running")
    db_session.add_all([stale_initial, stale_incremental])
    db_session.commit()
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    _backdate_updated_at(db_session, stale_initial, old)
    _backdate_updated_at(db_session, stale_incremental, old)

    reap_stale_jobs(db_session, job_type="incremental")

    db_session.refresh(stale_initial)
    db_session.refresh(stale_incremental)
    assert stale_incremental.status == "queued"
    assert stale_initial.status == "running"
```

Two-connection lane test (same committed-rows technique as `test_claim_next_job_skips_a_row_locked_by_another_connection`):

```python
def test_two_lanes_claim_concurrently_without_blocking_or_stealing(engine) -> None:
    with engine.begin() as setup_conn:
        user_ids = [
            setup_conn.execute(
                User.__table__.insert()
                .values(google_sub=f"lane-sub-{i}", email=f"lane-{i}@example.com", name="Lane")
                .returning(User.__table__.c.id)
            ).scalar_one()
            for i in range(2)
        ]
        initial_id = setup_conn.execute(
            SyncJob.__table__.insert()
            .values(user_id=user_ids[0], job_type="initial", window_start=date(2026, 1, 1))
            .returning(SyncJob.__table__.c.id)
        ).scalar_one()
        incremental_id = setup_conn.execute(
            SyncJob.__table__.insert()
            .values(user_id=user_ids[1], job_type="incremental", window_start=date(2026, 1, 1))
            .returning(SyncJob.__table__.c.id)
        ).scalar_one()

    try:
        conn_a = engine.connect()
        conn_b = engine.connect()
        session_a = Session(bind=conn_a)
        session_b = Session(bind=conn_b)
        try:
            # Lane A claims and keeps its transaction open (row lock held)...
            txn_a = conn_a.begin()
            locked = session_a.execute(
                SyncJob.__table__.select()
                .where(SyncJob.__table__.c.id == initial_id)
                .with_for_update()
            ).one()
            assert locked.id == initial_id
            # ...while lane B claims its own job without blocking or seeing A's.
            claimed_b = claim_next_job(session_b, job_type="incremental")
            assert claimed_b is not None and claimed_b.id == incremental_id
            assert claim_next_job(session_b, job_type="initial") is None  # A's row is locked, not stolen
            txn_a.rollback()
        finally:
            session_a.close()
            session_b.close()
            conn_a.close()
            conn_b.close()
    finally:
        with engine.begin() as cleanup_conn:
            cleanup_conn.execute(delete(SyncJob).where(SyncJob.id.in_([initial_id, incremental_id])))
            cleanup_conn.execute(delete(User).where(User.id.in_(user_ids)))
```

Drain tests (use the existing committed-rows + `SessionLocal` patch pattern from `test_drain_once_processes_all_queued_jobs_and_returns_the_count`; no Gmail connection, so each claimed job fails fast):

```python
class _FakeClock:
    """Replaces worker.time: monotonic() advances only when sleep() is called."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _commit_user_and_job(engine, tag: str, **job_values):
    with engine.begin() as conn:
        user_id = conn.execute(
            User.__table__.insert()
            .values(google_sub=f"{tag}-sub", email=f"{tag}@example.com", name=tag)
            .returning(User.__table__.c.id)
        ).scalar_one()
        values = dict(user_id=user_id, job_type="initial", window_start=date(2026, 1, 1))
        values.update(job_values)
        job_id = conn.execute(
            SyncJob.__table__.insert().values(**values).returning(SyncJob.__table__.c.id)
        ).scalar_one()
    return user_id, job_id


def _cleanup(engine, user_ids, job_ids) -> None:
    with engine.begin() as conn:
        conn.execute(delete(SyncJob).where(SyncJob.id.in_(job_ids)))
        conn.execute(delete(User).where(User.id.in_(user_ids)))


def test_drain_once_with_a_lane_leaves_other_lane_jobs_queued(engine, monkeypatch) -> None:
    import app.sync.worker as worker_module

    u1, initial_id = _commit_user_and_job(engine, "lane-drain-a", job_type="initial")
    u2, incremental_id = _commit_user_and_job(engine, "lane-drain-b", job_type="incremental")
    try:
        monkeypatch.setattr(worker_module, "SessionLocal", sessionmaker(bind=engine, future=True))
        assert drain_once(job_type="incremental") == 1
        with engine.connect() as conn:
            rows = {r.id: r.status for r in conn.execute(SyncJob.__table__.select().where(
                SyncJob.__table__.c.id.in_([initial_id, incremental_id])))}
        assert rows[incremental_id] == "failed"
        assert rows[initial_id] == "queued"
    finally:
        _cleanup(engine, [u1, u2], [initial_id, incremental_id])


def test_drain_once_waits_for_a_retry_due_within_its_budget(engine, monkeypatch) -> None:
    import app.sync.worker as worker_module

    due = datetime.now(timezone.utc) + timedelta(seconds=2)
    user_id, job_id = _commit_user_and_job(engine, "retry-wait", job_type="incremental", next_attempt_at=due)
    try:
        monkeypatch.setattr(worker_module, "SessionLocal", sessionmaker(bind=engine, future=True))
        clock = _FakeClock()
        real_sleep = __import__("time").sleep

        def sleep_for_real(seconds: float) -> None:
            clock.sleeps.append(seconds)
            clock.now += seconds
            real_sleep(seconds)  # the job's next_attempt_at is compared to real wall-clock time

        clock.sleep = sleep_for_real
        monkeypatch.setattr(worker_module, "time", clock)

        assert drain_once(max_runtime_seconds=60, job_type="incremental") == 1
        assert len(clock.sleeps) == 1 and 0 < clock.sleeps[0] <= 4
    finally:
        _cleanup(engine, [user_id], [job_id])


def test_drain_once_does_not_wait_for_a_retry_beyond_its_budget(engine, monkeypatch) -> None:
    import app.sync.worker as worker_module

    due = datetime.now(timezone.utc) + timedelta(hours=1)
    user_id, job_id = _commit_user_and_job(engine, "retry-far", job_type="incremental", next_attempt_at=due)
    try:
        monkeypatch.setattr(worker_module, "SessionLocal", sessionmaker(bind=engine, future=True))
        clock = _FakeClock()
        monkeypatch.setattr(worker_module, "time", clock)

        assert drain_once(max_runtime_seconds=60, job_type="incremental") == 0
        assert clock.sleeps == []
        with engine.connect() as conn:
            status = conn.execute(SyncJob.__table__.select().where(SyncJob.__table__.c.id == job_id)).one().status
        assert status == "queued"
    finally:
        _cleanup(engine, [user_id], [job_id])


def test_fetch_failure_log_line_does_not_contain_the_raw_message_id(db_session, user, monkeypatch, caplog) -> None:
    _connect_gmail(db_session, user)
    job = _make_job(user.id)
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(google_api, "list_message_ids_page", lambda token, **kw: (["rawid123"], None))

    def failing_fetch(token, mid):
        raise google_api.GoogleApiError("boom")

    monkeypatch.setattr(google_api, "get_message_summary", failing_fetch)
    with caplog.at_level("WARNING"):
        process_job(db_session, job, extractor=_FakeExtractor({}))
    assert "rawid123" not in caplog.text
    assert "msg-" in caplog.text
```

(Task 5 changes `get_message_summary` in this test to `get_message`.)

Update the two existing `run_forever` tests: their patched `reap_stale_jobs`/`claim_next_job` lambdas must accept the new keyword, i.e. `lambda db, job_type=None: ...`.

- [ ] **Step 3: Failing tests (`tests/test_sync_drain_entrypoint.py`, append)**

```python
import logging


def test_drain_main_passes_the_lane_and_its_budget(monkeypatch) -> None:
    import app.sync.drain as drain_module

    calls = []
    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: calls.append(kw) or 2)

    assert drain_module.main(["--lane", "incremental"]) == 2
    assert calls == [{"max_runtime_seconds": 1200.0, "job_type": "incremental"}]


def test_drain_main_without_a_lane_keeps_the_legacy_behavior(monkeypatch) -> None:
    import app.sync.drain as drain_module

    calls = []
    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: calls.append(kw) or 0)

    drain_module.main([])
    assert calls == [{}]


def test_drain_main_silences_per_request_http_logging(monkeypatch) -> None:
    import app.sync.drain as drain_module

    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: 0)
    drain_module.main(["--lane", "initial"])
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
```

- [ ] **Step 4:** Run `uv run pytest tests/test_core_privacy.py tests/test_sync_worker.py tests/test_sync_drain_entrypoint.py -q` → new tests FAIL (ImportError / TypeError on `job_type`).

- [ ] **Step 5: Implement**

`app/core/privacy.py`:

```python
import hashlib


def message_ref(message_id: str) -> str:
    """A stable, non-reversible stand-in for a Gmail message ID in log lines.
    Production worker logs are public (GitHub Actions on a public repo), so raw
    IDs never go there; hash a DB row's gmail_message_id the same way to match."""
    return "msg-" + hashlib.sha256(message_id.encode()).hexdigest()[:10]
```

`app/sync/worker.py`:
- `reap_stale_jobs(db, job_type=None)`: build `query = select(SyncJob).where(SyncJob.status == "running", SyncJob.updated_at < cutoff)`; `if job_type is not None: query = query.where(SyncJob.job_type == job_type)`; `stale_jobs = db.scalars(query.with_for_update(skip_locked=True)).all()`.
- `claim_next_job(db, job_type=None)`: same filter pattern before `.order_by(SyncJob.created_at).limit(1).with_for_update(skip_locked=True)`.
- `_run_one_tick(db, job_type=None)`: pass `job_type=job_type` to both.
- Fetch-failure warning: `logger.warning("Skipping message %s: fetch failed", message_ref(message_id))` (import `from app.core.privacy import message_ref`).
- Add below `DRAIN_MAX_RUNTIME_SECONDS`:

```python
# Per-lane drain budgets for the GitHub Actions workflows, each below its
# workflow's timeout-minutes (30 / 120) so a run exits cleanly between jobs
# instead of being killed mid-job.
LANE_DRAIN_BUDGET_SECONDS: dict[str, float] = {"incremental": 20 * 60.0, "initial": 100 * 60.0}

# Retries are requeued with next_attempt_at a little in the future; sleep this
# much past it so the claim's `next_attempt_at <= now` check is sure to pass.
_RETRY_WAIT_SLACK_SECONDS = 1.0


def _seconds_until_next_retry(db: Session, job_type: str | None) -> float | None:
    query = select(func.min(SyncJob.next_attempt_at)).where(SyncJob.status == "queued")
    if job_type is not None:
        query = query.where(SyncJob.job_type == job_type)
    next_at = db.scalar(query)
    if next_at is None:
        return None
    return max(0.0, (next_at - datetime.now(timezone.utc)).total_seconds())
```

- Replace `drain_once` with:

```python
def drain_once(max_runtime_seconds: float = DRAIN_MAX_RUNTIME_SECONDS, job_type: str | None = None) -> int:
    """Processes queued sync jobs (optionally only one lane's job_type) until
    none are due or max_runtime_seconds is exceeded, then returns the number
    processed. Used by the GitHub Actions workflows via app/sync/drain.py.
    When nothing is due but a queued retry becomes due within the remaining
    budget, waits for it instead of leaving it for the next (possibly
    hours-away, cron-triggered) run. Local dev keeps using run_forever()."""
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
        if wait is None or wait + _RETRY_WAIT_SLACK_SECONDS >= remaining:
            return processed
        logger.info("Waiting %.0fs for a queued retry", wait)
        time.sleep(wait + _RETRY_WAIT_SLACK_SECONDS)
    logger.warning("drain_once hit its %.0fs time budget with jobs possibly still queued", max_runtime_seconds)
    return processed
```

(add `func` to the `from sqlalchemy import select` import).

`app/pipeline/service.py`: both `logger.warning("Skipping message %s: …", message_id)` calls → `message_ref(message_id)`.

`app/sync/drain.py`:

```python
import argparse
import logging

from app.sync.worker import LANE_DRAIN_BUDGET_SECONDS, drain_once

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drain queued Gmail sync jobs, then exit.")
    parser.add_argument("--lane", choices=sorted(LANE_DRAIN_BUDGET_SECONDS))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    # This runs in GitHub Actions on a public repo, so its logs are public:
    # httpx logs every request URL at INFO, and Gmail URLs contain message IDs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.lane is None:
        count = drain_once()
    else:
        count = drain_once(max_runtime_seconds=LANE_DRAIN_BUDGET_SECONDS[args.lane], job_type=args.lane)
    logger.info("Drained %d job(s)%s", count, f" from the {args.lane} lane" if args.lane else "")
    return count


if __name__ == "__main__":
    main()
```

- [ ] **Step 6:** Run the full backend suite → all pass. `uv run python -m app.sync.drain --help` shows `--lane {incremental,initial}`.
- [ ] **Step 7:** Checkpoint gate (full local checks, review, commit `feat(sync): add per-lane draining, retry-aware drain, and public-log privacy`, push, CI green). The existing `sync-worker.yml` (lane-less) keeps working.

---

### Task 3: Two lane workflows (Checkpoint 3)

**Files:**
- Create: `.github/workflows/sync-incremental.yml`, `.github/workflows/sync-initial.yml`
- Delete: `.github/workflows/sync-worker.yml`
- Test: rewrite `backend/tests/test_sync_worker_workflow.py`

**Interfaces:** Consumes `LANE_DRAIN_BUDGET_SECONDS` (Task 2). Produces workflow files named `sync-{lane}.yml` (Task 4 dispatches by this name).

- [ ] **Step 1: Rewrite the test file**

```python
from pathlib import Path

import pytest
import yaml

from app.sync.worker import LANE_DRAIN_BUDGET_SECONDS

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
EXPECTED_TIMEOUT_MINUTES = {"incremental": 30, "initial": 120}
GITHUB_DEFAULT_TIMEOUT_MINUTES = 360


def _load(lane: str) -> dict:
    return yaml.safe_load((WORKFLOWS_DIR / f"sync-{lane}.yml").read_text())


def _triggers(workflow: dict) -> dict:
    # PyYAML (YAML 1.1) parses the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def test_every_lane_has_a_workflow_and_the_old_single_worker_is_gone() -> None:
    assert set(EXPECTED_TIMEOUT_MINUTES) == set(LANE_DRAIN_BUDGET_SECONDS)
    for lane in LANE_DRAIN_BUDGET_SECONDS:
        assert (WORKFLOWS_DIR / f"sync-{lane}.yml").is_file()
    assert not (WORKFLOWS_DIR / "sync-worker.yml").exists()


@pytest.mark.parametrize("lane", sorted(EXPECTED_TIMEOUT_MINUTES))
def test_lane_workflow_is_dispatchable_with_a_cron_fallback(lane) -> None:
    triggers = _triggers(_load(lane))
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"][0]["cron"]


@pytest.mark.parametrize("lane", sorted(EXPECTED_TIMEOUT_MINUTES))
def test_lane_workflow_runs_one_drain_at_a_time_and_never_cancels_one(lane) -> None:
    """One running + at most one pending run per lane. cancel-in-progress
    must stay false: cancelling a drain mid-job leaves that job "running"
    until a later run's reaper requeues it, costing it an attempt."""
    concurrency = _load(lane)["concurrency"]
    assert concurrency["group"] == f"sync-{lane}"
    assert concurrency["cancel-in-progress"] is False


@pytest.mark.parametrize("lane", sorted(EXPECTED_TIMEOUT_MINUTES))
def test_lane_workflow_timeout_sits_above_its_drain_budget(lane) -> None:
    job = _load(lane)["jobs"]["drain"]
    timeout = job["timeout-minutes"]
    assert timeout == EXPECTED_TIMEOUT_MINUTES[lane]
    assert LANE_DRAIN_BUDGET_SECONDS[lane] < timeout * 60 < GITHUB_DEFAULT_TIMEOUT_MINUTES * 60


@pytest.mark.parametrize("lane", sorted(EXPECTED_TIMEOUT_MINUTES))
def test_lane_workflow_drains_only_its_own_lane(lane) -> None:
    steps = _load(lane)["jobs"]["drain"]["steps"]
    commands = [step.get("run", "") for step in steps]
    assert f"uv run python -m app.sync.drain --lane {lane}" in commands
```

- [ ] **Step 2:** Run it → FAIL (files missing).
- [ ] **Step 3: Create both workflows** (`sync-incremental.yml` shown; `sync-initial.yml` identical except `name: Sync Worker (initial)`, `group: sync-initial`, `timeout-minutes: 120`, `--lane initial`, and the timeout comment):

```yaml
name: Sync Worker (incremental)

# Started on demand by the API (workflow_dispatch) as soon as a sync job is
# queued; the schedule is only a fallback for failed or missing dispatches.
on:
  workflow_dispatch: {}
  schedule:
    - cron: "*/15 * * * *"

permissions:
  contents: read

# One drain per lane at a time. Never cancel-in-progress: killing a drain
# mid-job leaves that job "running" until a later run's reaper requeues it,
# costing it an attempt.
concurrency:
  group: sync-incremental
  cancel-in-progress: false

jobs:
  drain:
    runs-on: ubuntu-latest
    # Covers a large catch-up incremental (about the whole 180-day window);
    # the drain's own 20-minute budget exits cleanly before this.
    timeout-minutes: 30
    env:
      DATABASE_URL: ${{ secrets.PROD_DATABASE_URL }}
      GOOGLE_CLIENT_ID: ${{ secrets.PROD_GOOGLE_CLIENT_ID }}
      GOOGLE_CLIENT_SECRET: ${{ secrets.PROD_GOOGLE_CLIENT_SECRET }}
      GMAIL_TOKEN_ENCRYPTION_KEY: ${{ secrets.PROD_GMAIL_TOKEN_ENCRYPTION_KEY }}
      SECRET_KEY: ${{ secrets.PROD_SECRET_KEY }}
      CORS_ORIGINS: https://job-tracker-1-ldy2.onrender.com
      FRONTEND_URL: https://job-tracker-1-ldy2.onrender.com
      ENV: production
      COOKIE_SECURE: "true"
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v10.1.0
        with:
          enable-cache: true
          python-version: "3.12"

      - name: Install dependencies
        working-directory: backend
        run: uv sync --locked

      - name: Drain sync queue
        working-directory: backend
        run: uv run python -m app.sync.drain --lane incremental
```

- [ ] **Step 4:** `git rm .github/workflows/sync-worker.yml`; tests PASS.
- [ ] **Step 5:** Checkpoint gate; commit `ci(sync): split the scheduled worker into dispatchable incremental and initial lanes`; push; CI green.
- [ ] **Step 6: Verify on GitHub:** `gh workflow run sync-incremental.yml --ref main` and `gh workflow run sync-initial.yml --ref main`; both succeed; the initial run logs `Drained 0 job(s) from the initial lane` (the safe production lane check from the spec); neither log contains `gmail.googleapis.com/gmail/v1/users/me/messages/`.

---

### Task 4: Dispatch client, re-kick, settings (Checkpoint 4)

**Files:**
- Create: `backend/app/sync/dispatch.py`
- Modify: `backend/app/core/config.py`, `backend/app/sync/router.py`, `backend/app/sync/service.py`
- Test: `backend/tests/test_sync_dispatch.py` (new), `backend/tests/test_sync_router.py`

**Interfaces:**
- Consumes: workflow names `sync-{job_type}.yml` (Task 3).
- Produces: `dispatch.workflow_file(job_type: str) -> str`, `dispatch.request_worker(job_type: str) -> bool`, `service.should_rekick(job: SyncJob | None, now: datetime | None = None) -> bool`.

- [ ] **Step 1: Failing tests (`tests/test_sync_dispatch.py`)**

```python
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.sync import dispatch
from app.sync.worker import LANE_DRAIN_BUDGET_SECONDS

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
SECRET = "github_pat_test_value_never_logged"


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "sync_dispatch_token", SECRET)
    monkeypatch.setattr(settings, "sync_dispatch_repository", "owner/repo")
    monkeypatch.setattr(settings, "sync_dispatch_ref", "main")


def test_every_lane_dispatches_to_a_workflow_that_exists() -> None:
    for lane in LANE_DRAIN_BUDGET_SECONDS:
        assert (WORKFLOWS_DIR / dispatch.workflow_file(lane)).is_file()


def test_request_worker_is_a_no_op_without_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sync_dispatch_token", None)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not call GitHub"))
    assert dispatch.request_worker("incremental") is False


@pytest.mark.parametrize("status", [200, 204])
def test_request_worker_posts_the_dispatch_and_reports_success(configured, monkeypatch, status) -> None:
    seen = {}

    def fake_post(url, *, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Resp(status)

    monkeypatch.setattr(httpx, "post", fake_post)

    assert dispatch.request_worker("initial") is True
    assert seen["url"] == "https://api.github.com/repos/owner/repo/actions/workflows/sync-initial.yml/dispatches"
    assert seen["json"] == {"ref": "main"}
    assert seen["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert seen["headers"]["Accept"] == "application/vnd.github+json"
    assert seen["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert seen["timeout"] == 5.0


@pytest.mark.parametrize("status", [401, 403, 404, 422, 500])
def test_request_worker_reports_rejection_without_leaking_the_token(configured, monkeypatch, caplog, status) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(status))
    with caplog.at_level("WARNING"):
        assert dispatch.request_worker("incremental") is False
    assert str(status) in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.ConnectError("c"), RuntimeError("?")])
def test_request_worker_never_raises_on_unexpected_exception(configured, monkeypatch, caplog, exc) -> None:
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(httpx, "post", boom)
    with caplog.at_level("WARNING"):
        assert dispatch.request_worker("incremental") is False
    assert SECRET not in caplog.text
```

- [ ] **Step 2: Failing tests (append to `tests/test_sync_router.py`)**

```python
from app.core.config import settings
from app.sync import dispatch
from app.sync.models import SyncJob


@pytest.fixture
def dispatched(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(dispatch, "request_worker", lambda job_type: calls.append(job_type) or True)
    return calls


def test_start_sync_dispatches_the_worker_for_the_new_jobs_lane(auth_client, connected_gmail, dispatched) -> None:
    response = auth_client.post(f"{BASE}/sync")
    assert response.status_code == 202
    assert dispatched == ["initial"]


def test_start_sync_still_returns_202_when_dispatch_fails(auth_client, connected_gmail, db_session, monkeypatch) -> None:
    # Real request_worker, configured, with GitHub unreachable.
    monkeypatch.setattr(settings, "sync_dispatch_token", "t")
    monkeypatch.setattr(settings, "sync_dispatch_repository", "owner/repo")

    def unreachable(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", unreachable)

    response = auth_client.post(f"{BASE}/sync")

    assert response.status_code == 202
    job = db_session.get(SyncJob, response.json()["id"])
    assert job.status == "queued"


def test_start_sync_rekicks_the_worker_for_an_already_queued_job(auth_client, connected_gmail, dispatched) -> None:
    auth_client.post(f"{BASE}/sync")
    second = auth_client.post(f"{BASE}/sync")
    assert second.status_code == 409
    assert dispatched == ["initial", "initial"]


def test_start_sync_does_not_rekick_a_healthy_running_job(auth_client, connected_gmail, db_session, dispatched) -> None:
    job_id = auth_client.post(f"{BASE}/sync").json()["id"]
    job = db_session.get(SyncJob, job_id)
    job.status = "running"
    db_session.commit()

    second = auth_client.post(f"{BASE}/sync")

    assert second.status_code == 409
    assert dispatched == ["initial"]


def test_start_sync_rekicks_a_stale_running_job(auth_client, connected_gmail, db_session, dispatched) -> None:
    job_id = auth_client.post(f"{BASE}/sync").json()["id"]
    db_session.execute(
        SyncJob.__table__.update()
        .where(SyncJob.__table__.c.id == job_id)
        .values(status="running", updated_at=datetime.now(timezone.utc) - timedelta(minutes=30))
    )
    db_session.commit()

    second = auth_client.post(f"{BASE}/sync")

    assert second.status_code == 409
    assert dispatched == ["initial", "initial"]
```

(add `import httpx` at the top of the file).

- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4: Implement**

`app/core/config.py` (after `run_worker_in_process`):

```python
    # Phase 9 — when both are set, POST /gmail/sync asks GitHub to start the
    # matching lane's worker workflow right away (workflow_dispatch) instead of
    # waiting for its cron fallback. Production only (Render env); unset
    # locally, where `python -m app.sync.worker` runs instead. The token is a
    # fine-grained PAT limited to this repo with only "Actions: write".
    sync_dispatch_token: str | None = None
    sync_dispatch_repository: str | None = None
    sync_dispatch_ref: str = "main"
```

`app/sync/dispatch.py`:

```python
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_TIMEOUT = 5.0


def workflow_file(job_type: str) -> str:
    return f"sync-{job_type}.yml"


def request_worker(job_type: str) -> bool:
    """Best-effort: ask GitHub Actions to start the worker lane for job_type.
    Never raises and never affects the caller — the job is already committed
    as "queued", so a failed dispatch only means it waits for the lane's cron
    fallback or the user's next Sync click. Logs the lane and HTTP status,
    never the token or response body."""
    token = settings.sync_dispatch_token
    repository = settings.sync_dispatch_repository
    if not token or not repository:
        return False
    url = f"{GITHUB_API}/repos/{repository}/actions/workflows/{workflow_file(job_type)}/dispatches"
    try:
        response = httpx.post(
            url,
            json={"ref": settings.sync_dispatch_ref},
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # deliberately broad: a dispatch must never propagate
        logger.warning("Sync worker dispatch for the %s lane failed: %s", job_type, type(exc).__name__)
        return False
    # 200 with run details today; 204 with no body in older API behavior.
    if response.status_code in (200, 204):
        logger.info("Dispatched the %s sync worker", job_type)
        return True
    logger.warning("Sync worker dispatch for the %s lane was rejected: HTTP %s", job_type, response.status_code)
    return False
```

`app/sync/service.py` (add):

```python
def should_rekick(job: SyncJob | None, now: datetime | None = None) -> bool:
    """Whether a Sync click that hit an already-active job should ask for a
    worker again: yes if it's still waiting to be claimed, or if it's
    "running" but hasn't made progress within the stale threshold (the
    dispatched run's reaper will then requeue and process it)."""
    if job is None:
        return False
    if job.status == "queued":
        return True
    now = now or datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=settings.sync_stale_job_threshold_minutes)
    return job.status == "running" and job.updated_at < stale_before
```

(import `datetime, timezone` alongside the existing `date, timedelta`).

`app/sync/router.py` `start_sync`:

```python
@router.post("/sync", response_model=SyncJobRead, status_code=status.HTTP_202_ACCEPTED)
def start_sync(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # The worker is requested only after the job is committed and the response
    # is on its way (background task); request_worker never raises, so a failed
    # dispatch can't change this response — the job just stays queued.
    try:
        job = service.enqueue_sync(db, current_user.id)
    except SyncAlreadyRunning as exc:
        if not service.should_rekick(exc.job):
            raise
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=SyncJobRead.model_validate(exc.job).model_dump(mode="json"),
            background=BackgroundTask(dispatch.request_worker, exc.job.job_type),
        )
    background_tasks.add_task(dispatch.request_worker, job.job_type)
    return job
```

(imports: `BackgroundTasks` from fastapi, `JSONResponse` from `fastapi.responses`, `BackgroundTask` from `starlette.background`, `from app.sync import dispatch`, `from app.sync.exceptions import SyncAlreadyRunning`.) Note: `dispatch.request_worker` is looked up at call time, so the tests' monkeypatch of the module attribute takes effect.

- [ ] **Step 5:** Full suite PASS. Checkpoint gate; commit `feat(sync): start the lane's worker immediately via workflow_dispatch when a sync is queued`; push; CI green. Production stays unchanged (no token configured yet).

---

### Task 5: One Gmail call per message (Checkpoint 5)

**Files:** Modify `backend/app/gmail/google_api.py`, `backend/app/sync/worker.py`; Test `backend/tests/test_gmail_google_api.py`, `backend/tests/test_sync_worker.py`.

**Interfaces:** Produces `google_api.get_message(access_token: str, message_id: str) -> tuple[dict, str]`. Removes `get_message_body` (no remaining caller). `get_message_summary` stays (Phase 3 endpoint).

- [ ] **Step 1: Failing tests (`tests/test_gmail_google_api.py`)**

```python
def test_get_message_makes_one_full_fetch_and_returns_summary_and_body(monkeypatch) -> None:
    calls = []
    payload = {
        "id": "m1",
        "snippet": "Thanks for applying",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Your application"},
                {"name": "From", "value": "jobs@acme.com"},
                {"name": "Date", "value": "Mon, 1 Sep 2026 10:00:00 +0000"},
                {"name": "X-Other", "value": "ignored"},
            ],
            "body": {"data": _b64("Hello there")},
        },
    }

    def fake_get(url, headers, params, timeout):
        calls.append(params)
        return _FakeResponse(200, payload)

    monkeypatch.setattr(httpx, "get", fake_get)

    summary, body = google_api.get_message("token", "m1")

    assert calls == [{"format": "full"}]
    assert summary == {
        "id": "m1",
        "subject": "Your application",
        "from_": "jobs@acme.com",
        "date": "Mon, 1 Sep 2026 10:00:00 +0000",
        "snippet": "Thanks for applying",
    }
    assert body == "Hello there"


def test_get_message_summary_matches_get_message_for_the_same_headers(monkeypatch) -> None:
    payload = {"id": "m1", "snippet": "s", "payload": {"headers": [
        {"name": "Subject", "value": "S"}, {"name": "From", "value": "F"}, {"name": "Date", "value": "D"}]}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message_summary("t", "m1") == google_api.get_message("t", "m1")[0]


def test_get_message_uses_the_requested_id_when_the_payload_has_none(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {"payload": {}}))
    summary, body = google_api.get_message("t", "m9")
    assert summary["id"] == "m9" and body == ""


def test_get_message_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.get_message("t", "m1")
```

In the existing `get_message_body` tests, replace each `google_api.get_message_body(<args>)` with `google_api.get_message(<args>)[1]` and rename the tests `test_get_message_body_*` → `test_get_message_extracts_body_*` (the body-cleaning assertions are unchanged; they're the parity proof).

In `tests/test_sync_worker.py`, replace every pair of `get_message_summary` + `get_message_body` patches with one `monkeypatch.setattr(google_api, "get_message", lambda token, mid: (_make_summary(mid, <same subject as before>), "body"))`; the idempotency test's call-recording lambda becomes `lambda token, mid: calls.append(mid) or (_make_summary(mid, "x"), "body")`; the fetch-failure tests (including Task 2's log-privacy test) patch `get_message` to raise `GoogleApiError`.

- [ ] **Step 2:** Run → FAIL (`get_message` missing).
- [ ] **Step 3: Implement** in `google_api.py`:

```python
def _summary_from_payload(payload: dict, message_id: str) -> dict:
    headers = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
    return {
        "id": payload.get("id", message_id),
        "subject": headers.get("Subject", ""),
        "from_": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": payload.get("snippet", ""),
    }


def _body_from_payload(payload: dict) -> str:
    found = _find_text_part(payload.get("payload", {}))
    if found is None:
        return ""
    mime_type, text = found
    return _clean_text(text, is_html=(mime_type == "text/html"))


def get_message(access_token: str, message_id: str) -> tuple[dict, str]:
    """One format=full fetch for the sync worker, returning (summary, body) —
    the headers/snippet that format=metadata would give are in the same
    payload, so fetching both separately doubled the Gmail round-trips per
    message. The body is still only cleaned, truncated plain text, never the
    raw MIME structure or attachments, and never persisted."""
    response = httpx.get(
        f"{GMAIL_API_BASE}/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "full"},
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"message fetch failed: {response.status_code}")
    payload = response.json()
    return _summary_from_payload(payload, message_id), _body_from_payload(payload)
```

`get_message_summary` returns `_summary_from_payload(response.json(), message_id)`; update its comment (it no longer references `get_message_body`). Delete `get_message_body` (move its comment's data-minimization note into `get_message`'s docstring, done above; `_find_text_part`/`_clean_text` stay).

`worker.py`: replace the two fetch lines with `summary, body = google_api.get_message(access_token, message_id)`.

- [ ] **Step 4:** Full suite PASS; `evaluation.compare` unchanged. Checkpoint gate; commit `perf(sync): fetch each Gmail message once instead of twice`; push; CI green.

---

### Task 6: Frontend UX (Checkpoint 6)

**Files:** Modify `frontend/src/components/SyncPanel.tsx`, `frontend/src/components/GmailPanel.tsx`, `frontend/src/components/ApplicationsPage.tsx`.

**Interfaces:** `GmailPanel` gains an optional prop `onConnectionChange?: (connected: boolean) => void`. `SyncPanel` is only rendered when connected. No API/type changes.

- [ ] **Step 1: `GmailPanel.tsx`**: add `interface Props { onConnectionChange?: (connected: boolean) => void }`; signature `export function GmailPanel({ onConnectionChange }: Props)`; in the status effect, `.then((s) => { setStatus(s); onConnectionChange?.(s.connected) })`; in `handleDisconnect` after the successful disconnect, `onConnectionChange?.(false)`.

- [ ] **Step 2: `ApplicationsPage.tsx`**: `const [gmailConnected, setGmailConnected] = useState(false)`; `<GmailPanel onConnectionChange={setGmailConnected} />`; `{gmailConnected && <SyncPanel onSyncCompleted={handleSyncCompleted} />}`.

- [ ] **Step 3: `SyncPanel.tsx`**:
  - Constant `const QUEUED_HINT_AFTER_MS = 2 * 60 * 1000`.
  - State `const [queuedSeenAt, setQueuedSeenAt] = useState<number | null>(null)` and `const [now, setNow] = useState(() => Date.now())`.
  - Effect: when `job?.status === 'queued'` and `queuedSeenAt === null`, `setQueuedSeenAt(Date.now())`; when the status is anything else, `setQueuedSeenAt(null)`. In the poll callback, also `setNow(Date.now())`.
  - `const waitingTooLong = job?.status === 'queued' && queuedSeenAt !== null && now - queuedSeenAt > QUEUED_HINT_AFTER_MS`
  - `handleRetry`: `setError(null); setQueuedSeenAt(Date.now()); try { setJob(await startSync()) } catch (err) { setError(...'Failed to restart sync') }` (a 409 resolves to the job, which is the re-kick).
  - Render, replacing the single "running —" paragraph:

```tsx
{job && job.status === 'queued' && (
  <p className="text-sm text-gray-600">
    Starting {job.job_type === 'initial' ? 'your first Gmail import' : 'sync'}…
  </p>
)}
{job && job.status === 'running' && (
  <p className="text-sm text-gray-600">
    {job.job_type === 'initial' ? 'Importing' : 'Syncing'} — {job.messages_processed} of{' '}
    {job.messages_seen || '?'} messages processed.
  </p>
)}
{job && isActive && job.job_type === 'initial' && (
  <p className="text-xs text-gray-500">
    The first import runs in the background and can take up to an hour for a large
    mailbox. You can close this page.
  </p>
)}
{waitingTooLong && (
  <p className="text-sm text-amber-700">
    The sync worker hasn't started yet.{' '}
    <button onClick={handleRetry} className="font-medium underline">
      Retry
    </button>
  </p>
)}
```

- [ ] **Step 4:** `npx tsc -b && npx oxlint && npm run build` clean (no new oxlint warnings — if `react(set-state-in-effect)` flags the queuedSeenAt effect, derive it in the `setJob` call sites instead: set `queuedSeenAt` wherever `setJob` receives a job, using the job's status).
- [ ] **Step 5:** Run the app locally (backend + frontend + `python -m app.sync.worker`) and check in the browser: Sync panel hidden when disconnected; "Starting…" then "Syncing — N of M" then summary; stop the worker, click Sync, and after 2 min the Retry hint appears; Retry keeps the same job.
- [ ] **Step 6:** Checkpoint gate; commit `feat(frontend): distinguish starting vs syncing, explain the first import, and offer a retry`; push; CI green.

---

### Task 7: Final whole-change review (Checkpoint 7)

- [ ] Review the combined Phase 9 diff (from the Task 1 commit to `HEAD`) against the spec, focusing on the Review Focus list; fix findings with tests; re-run the full checks; push.

### Task 8: Cloud configuration (Checkpoint 8, guided with the maintainer, one step at a time)

- [ ] Maintainer creates a fine-grained PAT: repository access **Only select repositories → job-tracker**; repository permission **Actions: Read and write**; nothing else except GitHub's mandatory read-only Metadata; expiration **1 year**. Record the creation and expiry dates (not the value).
- [ ] Token → Render `SYNC_DISPATCH_TOKEN` via clipboard, never printed. Also set `SYNC_DISPATCH_REPOSITORY=Huypham251/job-tracker` and `SYNC_DISPATCH_REF=main`. Wait for Live. Clear the clipboard.

### Task 9: Production verification (Checkpoint 9)

- [ ] Warm backend: click Sync → a `workflow_dispatch` run of `sync-incremental.yml` created within seconds → job completed; record click-to-completed time.
- [ ] Cold backend (after ≥15 min idle): same; record the time.
- [ ] Failure drill: set `SYNC_DISPATCH_TOKEN` to an invalid value → click Sync → 202, job `queued`, Render log shows `…was rejected: HTTP 401`; restore the real token (clipboard, not printed) → click Retry → completes.
- [ ] Run logs contain no `gmail.googleapis.com/gmail/v1/users/me/messages/` URLs and no raw message IDs.
- [ ] Lane isolation: recorded as **not verified in production** (automated two-connection test only); the Task 3 Step 6 initial-lane no-op check is the partial production evidence.

### Task 10: Docs (Checkpoint 10)

- [ ] CLAUDE.md: status (Phase 9 complete), architecture tree (`dispatch.py`, lanes, two workflows, `core/privacy.py`), "Phase 9 results" (measured timings, drills, the lane-isolation caveat), known gaps (deferred time-slicing, cron fallback 60-day disable, rate limiting), dispatch token creation date and **expiry date** with the reminder, multi-worker note (two lanes, disjoint rows).
- [ ] README: sync behavior (immediate start, lanes, fallback, Retry), new env vars, PAT setup, rotation-table row for `SYNC_DISPATCH_TOKEN` including "replace on Render **before** it expires; set a calendar reminder ~2 weeks ahead; GitHub also emails before expiry"; free-tier limitations updated.
- [ ] Phase 8 spec banner: note the Phase 9 supersession of the single cron-driven worker.
- [ ] Final full checks (pytest, `evaluation.run_eval`, `evaluation.compare`, tsc, oxlint, build), CI green on `main`, clean tree.
