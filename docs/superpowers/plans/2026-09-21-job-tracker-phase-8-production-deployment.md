# Job Tracker Phase 8: Production Deployment & CI/CD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the frontend, backend, worker, and database to free hosting
(Render + Neon), add a GitHub Actions CI pipeline gating merges to `main`, and verify
the whole system end-to-end in production — with minimal changes to existing
application behavior.

**Architecture:** Render Static Site (frontend, with a rewrite rule to the backend)
+ one Render free Web Service (FastAPI, with the existing sync worker running
in-process on a background thread) + Neon free Postgres. GitHub Actions runs backend
tests/evaluation and frontend type/lint/build on every PR; Render auto-deploys `main`
after CI passes.

**Tech Stack:** Render (web service + static site), Neon (Postgres), GitHub Actions,
`uv`, existing FastAPI/SQLAlchemy/Alembic/React/Vite stack — no new application
dependencies.

**Spec:** `docs/superpowers/specs/2026-09-21-job-tracker-phase-8-production-deployment-design.md`

## Global Constraints

- No paid AI/LLM API of any kind.
- `settings.classification_confidence_threshold`, the trust model
  (`pipeline/service.py::_apply_decision`), and fuzzy-matching thresholds
  (`pipeline/matching.py`) are never touched.
- `app/sync/worker.py`'s existing functions (`claim_next_job`, `process_job`,
  `reap_stale_jobs`, `run_forever`'s core loop logic) keep their exact current
  behavior — the only change anywhere in this file is a two-line heartbeat
  instrumentation addition (Task 1), justified there.
- Local dev must work exactly as documented in CLAUDE.md's "Local dev environment"
  section after every task in this plan — nothing here may require a local developer
  to change their workflow. `RUN_WORKER_IN_PROCESS` defaults to `false`.
- No Dockerfile, no containerization (per spec §10).
- No custom domain — free Render/Neon subdomains only.
- Every secret (`SECRET_KEY`, `GMAIL_TOKEN_ENCRYPTION_KEY`, Google OAuth credentials,
  `DATABASE_URL`) is set only in Render's/GitHub's own secret storage, never
  committed to the repo.
- Total expected cost: $0/month.

---

## File Structure

- `backend/app/sync/worker.py` — gains a module-level heartbeat timestamp and a
  getter (Task 1). No other function in this file changes.
- `backend/app/sync/inprocess.py` — **new**. Starts `run_forever()` on a background
  thread; the only integration point between the worker and the web process
  (Task 2).
- `backend/app/main.py` — gains `/health/ready`, `/health/worker`, and a `lifespan`
  handler that calls `inprocess.start_worker_thread()` when
  `settings.run_worker_in_process` is true (Tasks 1–2).
- `backend/app/core/config.py` — gains one new setting, `run_worker_in_process`
  (Task 2).
- `.github/workflows/ci.yml` — **new**. The CI pipeline (Task 4).
- No other application file changes. Tasks 5–13 are hosting-platform configuration,
  external service setup, and documentation — no new source files.

---

### Task 1: Worker heartbeat + `/health/ready` and `/health/worker` endpoints

**Files:**
- Modify: `backend/app/sync/worker.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_sync_worker.py`
- Test: `backend/tests/test_health.py`

**Interfaces:**
- Produces: `app.sync.worker.get_last_poll_at() -> datetime | None` — read by
  `/health/worker` in this task, and by nothing else in this plan (Task 2's
  `inprocess.py` only calls `run_forever`, it doesn't touch the heartbeat).

**Note on the spec**: §3.3 of the design spec states `run_forever()` is not modified.
This task adds two lines inside its loop (a heartbeat timestamp update) — a
deliberate, minimal refinement discovered while planning: `/health/worker`'s whole
purpose (spec §7) is observing whether the worker thread is *making progress*, not
just technically alive. A plain `thread.is_alive()` check would almost never go
false, since `run_forever`'s own broad `except Exception` handling means the thread
practically never dies — making that signal nearly useless for its stated purpose.
The two added lines don't change any decision logic, retry behavior, or job
processing — only add an observability hook, consistent with the spec's actual
intent even though it's a literal (trivial) modification to the file.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_sync_worker.py`, change the existing import at line 14 from:

```python
from app.sync.worker import _safe_error_message, claim_next_job, process_job, reap_stale_jobs
```

to:

```python
from app.sync.worker import (
    _safe_error_message,
    claim_next_job,
    get_last_poll_at,
    process_job,
    reap_stale_jobs,
)
```

Then add these tests at the end of the file:

```python
def test_get_last_poll_at_returns_none_before_any_poll(monkeypatch) -> None:
    import app.sync.worker as worker_module

    monkeypatch.setattr(worker_module, "_last_poll_at", None)
    assert get_last_poll_at() is None


def test_get_last_poll_at_returns_the_recorded_timestamp(monkeypatch) -> None:
    import app.sync.worker as worker_module

    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(worker_module, "_last_poll_at", fixed)
    assert get_last_poll_at() == fixed
```

Add to `backend/tests/test_health.py` (the whole file, replacing its current content —
it's currently 8 lines, this adds the two new endpoints' tests alongside the existing
one):

```python
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_returns_ok_when_db_is_reachable(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_worker_returns_503_when_worker_has_never_polled(
    client: TestClient, monkeypatch
) -> None:
    import app.sync.worker as worker_module

    monkeypatch.setattr(worker_module, "_last_poll_at", None)
    response = client.get("/health/worker")
    assert response.status_code == 503
    assert response.json()["status"] == "not_running"


def test_health_worker_returns_ok_when_poll_is_recent(
    client: TestClient, monkeypatch
) -> None:
    import app.sync.worker as worker_module

    monkeypatch.setattr(worker_module, "_last_poll_at", datetime.now(timezone.utc))
    response = client.get("/health/worker")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_worker_returns_503_when_poll_is_stale(
    client: TestClient, monkeypatch
) -> None:
    import app.sync.worker as worker_module

    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    monkeypatch.setattr(worker_module, "_last_poll_at", stale)
    response = client.get("/health/worker")
    assert response.status_code == 503
    assert response.json()["status"] == "stale"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_health.py tests/test_sync_worker.py -k "poll or health" -v`

Expected: FAIL — `get_last_poll_at` doesn't exist yet (`ImportError`), and
`/health/ready`/`/health/worker` don't exist yet (404s).

- [ ] **Step 3: Implement the heartbeat in `backend/app/sync/worker.py`**

Add after the module-level constants (after `MAX_BACKOFF_SECONDS = 3600` at line 31):

```python
_last_poll_at: datetime | None = None


def get_last_poll_at() -> datetime | None:
    """Used by app/main.py's /health/worker endpoint to check the in-process
    worker thread (app/sync/inprocess.py) is actually making progress, not
    just technically alive — a plain thread.is_alive() check would almost
    never go false, since run_forever()'s own broad exception handling means
    the thread practically never dies even when stuck."""
    return _last_poll_at
```

In `run_forever` (currently at line 195-221), add the heartbeat update as the first
line inside the `while True:` loop, before `db = SessionLocal()`:

```python
def run_forever(poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
    global _last_poll_at
    while True:
        _last_poll_at = datetime.now(timezone.utc)
        db = SessionLocal()
        job = None
        try:
            try:
                reap_stale_jobs(db)
            except Exception:
                db.rollback()
                logger.exception("Stale-job reaper failed; continuing")
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
```

(Only the `global _last_poll_at` declaration and the `_last_poll_at =
datetime.now(timezone.utc)` line are new — everything else in this function is
unchanged from the current file, reproduced here so the diff is unambiguous.)

- [ ] **Step 4: Implement the two endpoints in `backend/app/main.py`**

Add these imports at the top, alongside the existing ones:

```python
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
```

(`Request` and `Depends` — `Depends` needs adding to the existing
`from fastapi import FastAPI, Request` line, making it
`from fastapi import Depends, FastAPI, Request`.)

Add after the existing `/health` endpoint (after `return {"status": "ok"}`):

```python
    @app.get("/health/ready")
    def health_ready(db: Session = Depends(get_db)) -> JSONResponse:
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ok"})

    _WORKER_STALE_SECONDS = 30  # 15x POLL_INTERVAL_SECONDS — generous margin

    @app.get("/health/worker")
    def health_worker() -> JSONResponse:
        from datetime import datetime, timezone

        from app.sync.worker import get_last_poll_at

        last_poll = get_last_poll_at()
        if last_poll is None:
            return JSONResponse(
                status_code=503, content={"status": "not_running", "last_poll_at": None}
            )
        staleness = (datetime.now(timezone.utc) - last_poll).total_seconds()
        body = {"status": "ok", "last_poll_at": last_poll.isoformat(), "seconds_since_poll": staleness}
        if staleness > _WORKER_STALE_SECONDS:
            body["status"] = "stale"
            return JSONResponse(status_code=503, content=body)
        return JSONResponse(status_code=200, content=body)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_health.py tests/test_sync_worker.py -v`

Expected: all PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest -q`

Expected: all tests pass (this task adds tests, doesn't change any existing
behavior — `run_forever`'s decision logic is byte-identical apart from the heartbeat
line).

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/sync/worker.py app/main.py tests/test_sync_worker.py tests/test_health.py
git commit -m "$(cat <<'EOF'
feat(sync): add worker heartbeat and /health/ready, /health/worker endpoints

/health/ready checks real DB connectivity; /health/worker exposes whether
the (soon to be in-process) sync worker loop is actually making progress,
via a heartbeat timestamp rather than a bare thread-alive check, which
would rarely go false given run_forever()'s own broad exception handling.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: In-process worker startup wrapper

**Files:**
- Create: `backend/app/sync/inprocess.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_sync_inprocess.py`

**Interfaces:**
- Consumes: `app.sync.worker.run_forever() -> None` (existing, unmodified by this
  task).
- Produces: `app.sync.inprocess.start_worker_thread() -> None` — called only from
  `app/main.py`'s `lifespan`, gated on `settings.run_worker_in_process`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sync_inprocess.py`:

```python
import threading
import time

from app.sync import inprocess


def test_start_worker_thread_starts_a_background_daemon_thread(monkeypatch) -> None:
    started = threading.Event()

    def fake_run_forever() -> None:
        started.set()
        time.sleep(10)  # would block forever if this were the real loop

    monkeypatch.setattr(inprocess, "run_forever", fake_run_forever)
    monkeypatch.setattr(inprocess, "_worker_thread", None)

    inprocess.start_worker_thread()

    assert started.wait(timeout=2), "worker thread never called run_forever"
    assert inprocess._worker_thread is not None
    assert inprocess._worker_thread.daemon is True
    assert inprocess._worker_thread.is_alive()


def test_start_worker_thread_is_idempotent(monkeypatch) -> None:
    calls = []

    def fake_run_forever() -> None:
        calls.append(1)
        time.sleep(10)

    monkeypatch.setattr(inprocess, "run_forever", fake_run_forever)
    monkeypatch.setattr(inprocess, "_worker_thread", None)

    inprocess.start_worker_thread()
    first_thread = inprocess._worker_thread
    inprocess.start_worker_thread()  # second call must not start a second thread

    assert inprocess._worker_thread is first_thread
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_sync_inprocess.py -v`

Expected: FAIL — `app.sync.inprocess` doesn't exist yet (`ModuleNotFoundError`).

- [ ] **Step 3: Create `backend/app/sync/inprocess.py`**

```python
import logging
import threading

from app.sync.worker import run_forever

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None


def start_worker_thread() -> None:
    """Starts sync/worker.py's run_forever() loop on a background daemon
    thread, unmodified — used only when settings.run_worker_in_process is
    true (production, where there's no separate `python -m app.sync.worker`
    process). Local dev keeps running the worker as that separate process,
    per CLAUDE.md's documented workflow; this function is never called
    unless the env var opts in, so local dev is unaffected. Idempotent: a
    second call while a thread is already running is a no-op, so the
    lifespan hook can call this without tracking whether it already ran."""
    global _worker_thread
    if _worker_thread is not None:
        return
    _worker_thread = threading.Thread(target=run_forever, name="sync-worker", daemon=True)
    _worker_thread.start()
    logger.info("Started in-process sync worker thread")
```

- [ ] **Step 4: Add the setting in `backend/app/core/config.py`**

Add after `sync_stale_job_threshold_minutes: int = Field(default=15, gt=0)`:

```python
    # Production only (set via the Render Web Service's environment) — starts
    # sync/worker.py's run_forever() on a background thread at FastAPI startup
    # instead of requiring a separate `python -m app.sync.worker` process.
    # Defaults false so local dev is unaffected — see app/sync/inprocess.py.
    run_worker_in_process: bool = False
```

- [ ] **Step 5: Wire the lifespan hook in `backend/app/main.py`**

Add this import at the top:

```python
from contextlib import asynccontextmanager
```

Add before `def create_app() -> FastAPI:`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.run_worker_in_process:
        from app.sync.inprocess import start_worker_thread

        start_worker_thread()
    yield
```

Change the `FastAPI(...)` construction inside `create_app`:

```python
    app = FastAPI(title="Job Application Tracker", version="0.1.0", lifespan=lifespan)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_sync_inprocess.py -v`

Expected: both PASS.

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && uv run pytest -q`

Expected: all tests pass. `run_worker_in_process` defaults `false`, and the existing
`client` fixture constructs `TestClient(app)` without entering it as a context
manager (confirmed in `conftest.py`), so the lifespan handler never actually fires
during the rest of the suite — no risk of a stray worker thread starting during
unrelated tests.

- [ ] **Step 8: Commit**

```bash
cd backend && git add app/sync/inprocess.py app/core/config.py app/main.py tests/test_sync_inprocess.py
git commit -m "$(cat <<'EOF'
feat(sync): add in-process worker startup, gated by RUN_WORKER_IN_PROCESS

Lets a single Render web service run both the API and the existing sync
worker loop, with zero cost for a separate background-worker instance.
sync/worker.py's run_forever() is called exactly as-is on a background
daemon thread; local dev is unaffected since the new setting defaults
false and keeps running the worker as today's separate process.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Push to a new public GitHub repository

**Files:** none (git/GitHub operations only).

**Interfaces:** none — this task has no code dependencies on any other task and no
task depends on it beyond needing the repo to exist before Task 4's CI can run and
Task 6/8's Render services can be connected.

- [ ] **Step 1: Create the repository**

On GitHub, create a new **public** repository (per your earlier answer) named
`job-tracker` (or your preferred name — used as `<repo>` below). Do not initialize it
with a README, `.gitignore`, or license — this local repo already has all of those.

- [ ] **Step 2: Add the remote and push**

```bash
cd /Users/itshuy/Documents/Projects/Job-tracker
git remote add origin https://github.com/<your-github-username>/<repo>.git
git push -u origin main
```

- [ ] **Step 3: Verify**

Run: `git remote -v` — expect `origin` listed for both fetch and push. Visit the
GitHub repo URL in a browser and confirm all commits and files are present.

- [ ] **Step 4: Enable Dependabot alerts (Task 11 will configure the full security
  pass, but this is a zero-cost, zero-risk toggle worth doing now)**

On GitHub: repo → Settings → Code security → enable "Dependabot alerts" and
"Dependabot security updates."

No commit for this task — it's entirely GitHub-side configuration.

---

### Task 4: GitHub Actions CI pipeline

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:** none — this workflow only runs existing commands
(`uv sync`, `uv run pytest`, `npm ci`, `npx tsc -b`, `npx oxlint`, `npm run build`),
none of which are modified by this task.

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: jobtracker
          POSTGRES_PASSWORD: jobtracker
          POSTGRES_DB: jobtracker
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U jobtracker -d jobtracker"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql+psycopg://jobtracker:jobtracker@localhost:5432/jobtracker
      CORS_ORIGINS: http://localhost:5173
      ENV: development
      GOOGLE_CLIENT_ID: ci-test-client-id.apps.googleusercontent.com
      GOOGLE_CLIENT_SECRET: ci-test-client-secret
      SECRET_KEY: ci-only-secret-key-never-used-for-anything-real-00000000
      GMAIL_TOKEN_ENCRYPTION_KEY: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
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

      - name: Run backend tests
        working-directory: backend
        run: uv run pytest

      - name: Run evaluation comparison (informational — pytest above already gates regressions)
        working-directory: backend
        run: uv run python -m evaluation.compare

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install Node
        uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        working-directory: frontend
        run: npm ci

      - name: Type check
        working-directory: frontend
        run: npx tsc -b

      - name: Lint
        working-directory: frontend
        run: npx oxlint

      - name: Build
        working-directory: frontend
        run: npm run build
```

`GMAIL_TOKEN_ENCRYPTION_KEY`'s value is the exact same syntactically-valid Fernet-key
placeholder already committed in `.env.example` — reused deliberately for
consistency, not a new value to invent.

- [ ] **Step 2: Commit and push**

```bash
git add .github/workflows/ci.yml
git commit -m "$(cat <<'EOF'
ci: add GitHub Actions pipeline (backend tests+eval, frontend lint/build)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
git push
```

- [ ] **Step 3: Verify both jobs pass**

On GitHub, open the "Actions" tab for the pushed commit. Expected: both `backend` and
`frontend` jobs complete with a green checkmark. If either fails, read its log output
before changing anything — the two most likely first-run issues are the Postgres
service container not being ready when `pytest` starts (the `options:` health-check
block above is what prevents this) or a `uv.lock`/`package-lock.json` drift from
`--locked`/`npm ci`'s strict mode (fix by running `uv sync` / `npm install` locally
and committing the updated lockfile, not by loosening the CI command).

- [ ] **Step 4: Add branch protection**

On GitHub: repo → Settings → Branches → add a branch protection rule for `main` →
enable "Require status checks to pass before merging" → select both `backend` and
`frontend` as required checks. (This is the mechanism Task 10 relies on to gate
Render's auto-deploy on CI passing.)

No further commit for this task.

---

### Task 5: Provision the Neon database

**Files:** none (external service configuration + a local verification run).

**Interfaces:**
- Produces: a Neon `DATABASE_URL` connection string, consumed by Task 6 (set as the
  Render Web Service's `DATABASE_URL` env var).

- [ ] **Step 1: Create the Neon project**

At <https://neon.tech>, sign up (free tier, per the spec's §3.2 research) and create a
new project. Note the connection string it gives you — it will look like
`postgresql://<user>:<password>@<host>.neon.tech/<dbname>?sslmode=require`.

- [ ] **Step 2: Adapt the connection string for this app's driver**

This app's `DATABASE_URL` uses the `postgresql+psycopg://` scheme (confirmed in
`backend/.env.example` and `app/core/config.py`), not bare `postgresql://`. Take
Neon's connection string and change only the scheme prefix:

```
postgresql+psycopg://<user>:<password>@<host>.neon.tech/<dbname>?sslmode=require
```

- [ ] **Step 3: Apply migrations against Neon from your local machine**

```bash
cd backend
DATABASE_URL="postgresql+psycopg://<user>:<password>@<host>.neon.tech/<dbname>?sslmode=require" \
  uv run alembic upgrade head
```

- [ ] **Step 4: Verify**

```bash
psql "postgresql://<user>:<password>@<host>.neon.tech/<dbname>?sslmode=require" \
  -c "\dt"
```

Expected: all six application tables listed (`applications`, `users`,
`gmail_connections`, `processed_messages`, `sync_jobs`, `alembic_version`, plus
whatever else `alembic upgrade head` created — confirm it matches the same table set
`\dt` shows against your local `jobtracker` database).

No commit for this task — it's entirely Neon-side and a one-off local command.

---

### Task 6: Deploy the backend to Render

**Files:** none (Render dashboard configuration).

**Interfaces:**
- Consumes: the Neon `DATABASE_URL` from Task 5.
- Produces: the backend's public Render URL (e.g. `https://job-tracker-api.onrender.com`),
  needed by Task 7 (OAuth redirect URIs) and Task 8 (the frontend's rewrite rule).

- [ ] **Step 1: Create the Web Service**

At <https://dashboard.render.com>, "New +" → "Web Service" → connect the GitHub repo
from Task 3. Configure:
- **Root Directory**: `backend`
- **Runtime**: Python 3
- **Build Command**: `uv sync`
- **Pre-Deploy Command**: `uv run alembic upgrade head`
- **Start Command**: `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
- **Instance Type**: Free

- [ ] **Step 2: Set environment variables**

In the service's "Environment" tab, add (values marked `<generate>` below are
generated once, right now, and pasted in — never reused from local `.env`):

| Key | Value |
|---|---|
| `DATABASE_URL` | the Neon connection string from Task 5 |
| `CORS_ORIGINS` | *(leave as a placeholder for now — updated in Task 8 once the frontend's real Render URL exists)* |
| `FRONTEND_URL` | *(same — updated in Task 8)* |
| `ENV` | `production` |
| `COOKIE_SECURE` | `true` |
| `GOOGLE_CLIENT_ID` | *(from Task 7 — placeholder for now, this service can be created before Task 7 runs)* |
| `GOOGLE_CLIENT_SECRET` | *(from Task 7)* |
| `SECRET_KEY` | `<generate>` — run `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` locally and paste the output |
| `GMAIL_TOKEN_ENCRYPTION_KEY` | `<generate>` — run `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` locally and paste the output |
| `RUN_WORKER_IN_PROCESS` | `false` *(Task 9 flips this to `true` only after the rest of the deploy is verified working)* |

- [ ] **Step 2a: This step has a real ordering dependency, noted explicitly**

`CORS_ORIGINS`, `FRONTEND_URL`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET` can't
be filled in with final values until Tasks 7 and 8 produce them. Use any syntactically
valid placeholder now (e.g. `CORS_ORIGINS=http://localhost:5173`,
`GOOGLE_CLIENT_ID=placeholder.apps.googleusercontent.com`) so the service deploys
successfully (Pydantic settings will reject a missing required field, not a wrong-but-
present one), then return to this step after Tasks 7 and 8 to set the real values and
trigger a redeploy.

- [ ] **Step 3: Deploy and verify**

Wait for the build + pre-deploy + deploy to complete. Then:

```bash
curl https://<your-service>.onrender.com/health
curl https://<your-service>.onrender.com/health/ready
```

Expected: both return `{"status": "ok"}` with HTTP 200. `/health/ready` returning 200
confirms the Neon connection and the Task 5 migrations are both actually working from
Render, not just locally.

No commit for this task — it's entirely Render dashboard configuration.

---

### Task 7: Reconfigure Google OAuth for production

**Files:** none (Google Cloud Console configuration).

**Interfaces:**
- Consumes: the backend's Render URL from Task 6.
- Produces: nothing new consumed by later tasks in this plan — Task 6's OAuth env
  vars are filled in here, closing the loop from Task 6's Step 2a.

- [ ] **Step 1: Add production redirect URIs**

At <https://console.cloud.google.com/>, in the same OAuth client used for local dev
(per `README.md`'s existing "Google OAuth setup" section — do not create a new
client), under "Authorized redirect URIs" **add** (don't remove the existing
`localhost` ones):

```
https://<your-backend>.onrender.com/api/v1/auth/google/callback
https://<your-backend>.onrender.com/api/v1/gmail/callback
```

- [ ] **Step 2: Add the production JavaScript origin**

Under "Authorized JavaScript origins," add:

```
https://<your-frontend>.onrender.com
```

(The frontend's exact Render URL comes from Task 8 — if Task 8 hasn't run yet, this
step can wait until immediately after it; nothing else in this task depends on the
frontend URL.)

- [ ] **Step 3: Confirm the consent screen stays in "Testing" mode**

Under "OAuth consent screen," confirm "Publishing status" is still "Testing" (per the
spec §5 decision — do not click "Publish App"). Under "Test users," add any reviewer
Google accounts that should be able to sign in and connect Gmail on the deployed app,
alongside your own.

- [ ] **Step 4: Return to Task 6 and set the real values**

Back in the Render dashboard, update `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to
the real (already-existing, same as local dev) values, which triggers a redeploy.

- [ ] **Step 5: Verify the login round-trip**

```bash
curl -sI https://<your-backend>.onrender.com/api/v1/auth/google/login | grep -i location
```

Expected: a `302` redirect `Location` header pointing at
`https://accounts.google.com/...` with a `redirect_uri` query parameter matching
exactly what was registered in Step 1 (check the URL-decoded value carefully — a
scheme mismatch, e.g. `http://` instead of `https://`, here means the Start Command's
`--proxy-headers` flag from Task 6 isn't taking effect and needs revisiting before
continuing).

No commit for this task — it's entirely external-service configuration.

---

### Task 8: Deploy the frontend to Render Static Site with a rewrite rule

**Files:** none (Render dashboard configuration).

**Interfaces:**
- Consumes: the backend's Render URL from Task 6.
- Produces: the frontend's public Render URL, needed by Task 7's Step 2 (if not
  already done) and Task 6's `CORS_ORIGINS`/`FRONTEND_URL` (this task's Step 4 closes
  that loop).

- [ ] **Step 1: Create the Static Site**

In the Render dashboard, "New +" → "Static Site" → connect the same GitHub repo.
Configure:
- **Root Directory**: `frontend`
- **Build Command**: `npm ci && npm run build`
- **Publish Directory**: `dist`

- [ ] **Step 2: Add the rewrite rule**

In the Static Site's "Redirects/Rewrites" tab, add a rule:
- **Source**: `/api/*`
- **Destination**: `https://<your-backend>.onrender.com/api/*`
- **Action**: Rewrite

- [ ] **Step 3: Deploy and verify same-origin behavior**

Once deployed, open `https://<your-frontend>.onrender.com` in a browser, open
DevTools → Network, and trigger any API call (e.g. load the applications list).
Expected: the request URL shown is a **relative** `/api/v1/...` path (not
`https://<backend>...`) — confirming the browser sees it as same-origin, exactly
matching what Vite's dev-server proxy already does locally. No CORS error should
appear in the console even though `CORS_ORIGINS` on the backend is still a
placeholder at this point — same-origin requests never trigger CORS at all, which is
the whole reason this architecture avoids needing that setting to be exactly right
just to load the page (it still matters for other purposes, closed in the next step).

- [ ] **Step 4: Close the loop back in Render's backend service (Task 6)**

Update the backend Web Service's `CORS_ORIGINS` and `FRONTEND_URL` env vars to
`https://<your-frontend>.onrender.com`, triggering a redeploy.

- [ ] **Step 5: Verify the full login flow end-to-end**

In the browser, click "Sign in with Google" on the deployed frontend, complete
consent as a registered test user (Task 7, Step 3), and confirm you land back on the
dashboard authenticated — this is the first point where Tasks 6, 7, and 8 are all
exercised together.

No commit for this task — it's entirely Render dashboard configuration.

---

### Task 9: Enable the in-process worker in production

**Files:** none (a single Render environment variable change).

**Interfaces:**
- Consumes: `app.sync.inprocess.start_worker_thread` (Task 2) and `/health/worker`
  (Task 1) — this task is where they're first exercised against a real deployment.

- [ ] **Step 1: Flip the flag**

In the Render backend service's environment variables, change
`RUN_WORKER_IN_PROCESS` from `false` to `true`. This triggers a redeploy.

- [ ] **Step 2: Verify the worker thread is running**

```bash
curl https://<your-backend>.onrender.com/health/worker
```

Expected: `{"status": "ok", "last_poll_at": "<recent ISO timestamp>", "seconds_since_poll": <a small number>}`
with HTTP 200. Run it again a few seconds later and confirm `last_poll_at` has
advanced — proving the thread is genuinely looping, not just started once and stuck.

- [ ] **Step 3: Verify a real sync job gets processed**

On the deployed frontend, connect Gmail (this also exercises the `google_gmail` OAuth
client registered in Task 7) and click "Sync Gmail." Poll:

```bash
curl https://<your-backend>.onrender.com/api/v1/gmail/sync/latest
```

(this requires the session cookie from the browser login — easiest to check via the
browser's own Network tab rather than a bare `curl`, unless you copy the cookie value
into the request). Expected: the `SyncJob`'s `status` progresses from `queued` to
`running` to `completed` without requiring any separate worker process to be running
anywhere — confirming the in-process thread is doing real work, not just responding
to the heartbeat check.

No commit for this task — it's a single dashboard environment variable change,
already committed in Task 2's code.

---

### Task 10: CI-gated auto-deploy

**Files:** none (Render + GitHub dashboard configuration).

- [ ] **Step 1: Enable auto-deploy on both Render services**

For both the Web Service (Task 6) and the Static Site (Task 8), confirm "Auto-Deploy"
is set to deploy on pushes to `main` (this is Render's default when connecting a
repo, but verify it explicitly).

- [ ] **Step 2: Confirm the effective flow**

Branch protection (Task 4, Step 4) already requires both CI jobs to pass before a PR
can merge to `main`. Render's auto-deploy fires independently on any push to `main`.
Together, the effective flow is: **open a PR → CI must go green → merge → Render
deploys the merged `main`.** There is no additional workflow file needed to "connect"
these two systems — they're both already watching the same branch, gated by the same
merge requirement.

- [ ] **Step 3: Verify with a real trivial change**

Make a small, harmless change (e.g. a comment or README typo fix) on a branch, open a
PR, confirm both CI jobs run and are required before the merge button is enabled,
merge it, and confirm both Render services show a new deploy triggered by that merge
commit in their "Events" tab.

No commit beyond the trivial verification change itself, which should be a real,
useful fix rather than a throwaway — e.g., use this step to fix any small
inaccuracy noticed while executing this plan.

---

### Task 11: Security & observability pass

**Files:** none (verification and dashboard configuration only).

- [ ] **Step 1: Git history secret scan**

```bash
cd /Users/itshuy/Documents/Projects/Job-tracker
git log --all --full-history -- backend/.env
git log --all -p | grep -iE "GOOGLE_CLIENT_SECRET *=|SECRET_KEY *=|GMAIL_TOKEN_ENCRYPTION_KEY *=" | grep -v "your-client-secret\|generate-with\|AAAAAAAA"
```

Expected: the first command returns nothing (confirming `backend/.env` itself was
never committed); the second returns nothing beyond the known `.env.example`
placeholder lines (filtered out above) — confirming no real secret value ever landed
in a commit.

- [ ] **Step 2: Confirm Dependabot is active**

Repo → Security tab → confirm Dependabot alerts are enabled (done in Task 3, Step 4)
and check whether it's already opened any alerts for `uv.lock` or `package-lock.json`
dependencies — triage any that appear before considering this task done.

- [ ] **Step 3: Confirm production cookie/CORS settings directly**

```bash
curl -sI https://<your-backend>.onrender.com/api/v1/auth/logout -X POST | grep -i "set-cookie"
```

Expected: the `Set-Cookie` header (if present on a relevant authenticated request —
easier to check via the browser's DevTools Application tab after logging in) shows
`Secure` and `SameSite=Lax` attributes, confirming `COOKIE_SECURE=true` actually took
effect in production.

```bash
curl -sI -H "Origin: https://evil-example.com" https://<your-backend>.onrender.com/api/v1/applications | grep -i "access-control-allow-origin"
```

Expected: no `Access-Control-Allow-Origin` header for an origin that isn't the real
deployed frontend — confirming `CORS_ORIGINS` is the exact production value, not a
wildcard or a stale placeholder.

- [ ] **Step 4: Document the known, un-fixed rate-limiting gap**

No code change — this is recorded in Task 13's CLAUDE.md update, matching this
project's existing convention of documenting known gaps rather than speculatively
fixing them.

No commit for this task beyond what Task 13 covers.

---

### Task 12: Full end-to-end verification pass

**Files:** none — this task is the spec's §12 checklist, run once, in order, against
the live deployment, as the final gate before calling Phase 8 done.

- [ ] **Step 1: Run through the checklist**

In order, against `https://<your-frontend>.onrender.com`:

1. `/health`, `/health/ready`, `/health/worker` all return 200.
2. Frontend loads; Network tab shows same-origin `/api/*` calls.
3. Google login completes as a registered test user.
4. "Connect Gmail" completes the second OAuth consent.
5. "Sync Gmail" creates and completes a `SyncJob` (confirmed via `/health/worker`'s
   advancing timestamp and the sync panel's UI reaching "completed").
6. At least one real email lands in the review queue or auto-applies as an
   `Application`.
7. Approve/Reject on a review-queue item works and moves it out of the queue.
8. Manually add, edit, and delete an application; confirm `source="manual"` behavior
   matches documented expectations (never silently overwritten by later sync
   activity).
9. Let the service go idle for 15+ minutes, then hit it again — confirm the cold-start
   wake succeeds and every check above still passes afterward.
10. Push one trivial, reversible migration (e.g. a no-op comment-only migration, or a
    genuinely reversible column addition if there's a real one queued) through the
    full CI → merge → auto-deploy pipeline, and confirm Render's Pre-Deploy Command
    log shows it being applied before the new instance starts serving.

- [ ] **Step 2: Record results**

Note the outcome of each of the 10 checks — this record becomes the evidence cited in
Task 13's CLAUDE.md "Phase 8 results" section, matching how Phases 5-7 documented
their own manual/end-to-end verification findings.

No commit for this task — verification only. Task 13 commits the write-up.

---

### Task 13: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `README.md`**

Add a new "## Production deployment" section (after the existing "## Database
migrations" section, before "## Tests") documenting: the live URLs, that the stack is
Render (web service + static site) + Neon, the env vars required for a from-scratch
redeploy (cross-referencing `.env.example` rather than duplicating it), and one line
noting the free-tier cold-start behavior a visitor might notice.

- [ ] **Step 2: Add a "Phase 8 results" section to `CLAUDE.md`**

Insert after the existing "## Phase 7 results (2026-09-20)" section (before "##
Local dev environment"), following this project's established per-phase-results
format: what was deployed and where, the exact findings from Task 12's verification
pass (including anything that didn't work on the first attempt and how it was fixed —
matching how Phase 5's "Manual testing findings" documented its own real setup
issues), and the final cost confirmation (should read $0/month, or note the exact
reason if anything ended up costing something).

Update the "## Status" section's opening line to mention Phase 8 is complete and
deployed, following the same pattern used for Phases 1–7.

- [ ] **Step 3: Commit**

```bash
cd /Users/itshuy/Documents/Projects/Job-tracker
git add README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: document Phase 8 production deployment and verification results

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
git push
```

- [ ] **Step 4: Confirm a clean working tree**

Run: `git status --short` — expected: no output.

---

## Self-Review Notes

- **Spec coverage**: §3 (architecture/topology) → Tasks 6, 8. §3.3 (in-process
  worker) → Tasks 1, 2, 9. §4 (secrets) → Tasks 6, 7. §5 (OAuth) → Task 7. §6
  (migrations) → Task 6's Pre-Deploy Command, verified in Task 5 and Task 12 step 10.
  §7 (health checks) → Task 1. §8 (logging) → no dedicated task; Render's built-in log
  capture requires no setup, confirmed as a non-action in §8 of the spec itself. §9
  (CI) → Task 4. §10 (Docker) → a decision, not an implementation task — nothing to
  build. §11 (security) → Task 11. §12 (verification) → Task 12. §13 (checkpoints) →
  this plan's task breakdown directly mirrors the spec's 10 checkpoints, split slightly
  finer where a checkpoint had independently-reviewable pieces (e.g. spec checkpoint 6
  "in-process worker integration" split into Task 2's code and Task 9's production
  enablement, since the code can be reviewed before any Render account changes).
- **Placeholder scan**: manual configuration steps (Render dashboard fields, Google
  Console entries) give exact field names and values, with clearly-marked, explicitly
  justified exceptions only where a value doesn't exist until a *specific later step*
  produces it (Task 6 Step 2a, Task 7 Step 2) — each such exception names exactly
  which later step closes it, not left open-ended.
- **Type/interface consistency**: `get_last_poll_at()` (Task 1) is consumed by
  `/health/worker` (also Task 1, same task) and not referenced anywhere in Task 2 —
  confirmed Task 2's `inprocess.py` only imports `run_forever`, matching the spec's
  framing of the heartbeat and the thread-starter as two independent small additions.
