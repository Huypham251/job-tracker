# Job Application Tracker — Project Notes

Read this before starting Phase 6. It's a snapshot of where the project stands after
Phase 5 (2026-09-17), not a spec — the specs under `docs/superpowers/specs/` and plans
under `docs/superpowers/plans/` remain the source of truth for what was decided and why.

## Status

Phases 1–5 are complete, merged to `main`. Backend: 232/232 tests passing. Frontend:
`tsc -b` clean, `oxlint` clean (0 errors, 3 pre-existing warnings in
`AuthContext.tsx`/`useApplications.ts`, unrelated to any phase and not touched by any of
them). Working tree clean, no uncommitted changes.

- Phase 1: manual CRUD for job applications.
- Phase 2: Google OAuth login, multi-user.
- Phase 3: Gmail connection (OAuth, read-only), manual "fetch recent messages" test view.
- Phase 4: classification/extraction pipeline, matching/dedup, trust model, review queue
  — originally built on the Anthropic API, **then fully replaced by Phase 4b**.
- Phase 4b: the Anthropic-backed classifier was swapped for a local, deterministic,
  rule-based one. `app/llm/` and the `anthropic` dependency are gone. No API key, no
  external service, no per-email cost for classification.
- Phase 5: the synchronous, 20-message-capped "Process Inbox" HTTP endpoint was replaced
  by a Postgres-backed job queue and a background worker process. Bounded initial
  backfill (180 days), watermark-based incremental sync thereafter, retry/backoff on
  transient Gmail failures, a stale-job reaper, and safe (non-leaking) user-facing error
  messages. `process_inbox` and `POST /pipeline/process` are gone.

**Read `2026-09-15-job-tracker-phase-4-pipeline-design.md` for the pipeline architecture
that's still current (matching, trust model, DB schema, `/pipeline/review*` API,
frontend), `2026-09-15-job-tracker-phase-4b-local-classifier-design.md` for how
classification actually works, and `2026-09-16-job-tracker-phase-5-gmail-sync-design.md`
for the sync job queue/worker.** All three specs carry supersession banners pointing at
whichever of the others changed their original decisions.

## Architecture

```
backend/app/
├── applications/    Phase 1 — CRUD, source column ("manual"|"gmail")
├── auth/            Phase 2 — Google OAuth login, JWT cookie sessions
├── gmail/            Phase 3 — connection (OAuth), message fetch; Phase 5 added
│   ├── google_api.py    paginated/date-bounded listing (list_message_ids_page)
│   └── service.py         get_valid_access_token (refreshes on demand)
├── classifier/       Phase 4b — local rule-based classification/extraction
│   ├── text.py        normalization, sender-domain/display-name parsing
│   ├── patterns.py     weighted phrase tables, ATS-domain lists, tunable constants
│   ├── fields.py        company/position regex extraction (ordered fallback tiers)
│   ├── extractor.py      Extractor Protocol, ClassificationError, RuleBasedExtractor
│   └── schemas.py         EmailExtraction (Pydantic contract)
├── pipeline/         Phase 4 — per-message decision/trust logic (provider-agnostic)
│   ├── matching.py     rapidfuzz company/position matching against existing apps
│   ├── service.py        process_message (classify → match → decide → persist one
│   │                        message; called by the Phase 5 worker) / review-queue /
│   │                        approve / reject, trust model — no HTTP-request orchestration
│   │                        here anymore, that moved to app/sync/
│   ├── models.py          ProcessedMessage (idempotency + audit + review queue;
│   │                        sync_job_id FK added in Phase 5)
│   └── router.py           /api/v1/pipeline/review* only — /process removed
├── sync/             Phase 5 — job queue, orchestration, worker
│   ├── models.py          SyncJob (queued/running/completed/failed; partial unique
│   │                        index enforces one active job per user)
│   ├── schemas.py           SyncJobRead
│   ├── service.py             enqueue_sync (initial vs incremental decision + 409
│   │                            on an already-active job), get_job, get_latest_job
│   ├── router.py               /api/v1/gmail/sync* (POST /sync, GET /sync/{id},
│   │                            GET /sync/latest)
│   └── worker.py                 claim_next_job (FOR UPDATE SKIP LOCKED),
│                                   process_job (pagination + retry/backoff),
│                                   reap_stale_jobs, run_forever — run as a SEPARATE
│                                   process: `uv run python -m app.sync.worker`
└── core/config.py    Settings — gmail_sync_backfill_days, sync_stale_job_threshold_minutes

frontend/src/
├── components/ApplicationsPage.tsx   dashboard shell: Gmail panel, SyncPanel,
│                                      ReviewQueue, add/edit form, applications list
├── components/SyncPanel.tsx            "Sync Gmail" button, polls job status,
│                                          shows progress/summary, resumes polling on
│                                          page refresh via GET /gmail/sync/latest
├── components/ReviewQueue.tsx          "Needs review" section, Approve/Edit/Reject;
│                                          takes a refreshSignal prop (bumped on sync
│                                          completion/failure) so new items appear
│                                          without a manual reload
└── components/GmailPanel.tsx           Connect Gmail, Fetch recent messages
                                           (Phase 3 display-only test view, untouched)
```

**The extractor swap validated the original abstraction**: `app/pipeline/` needed only
import-path changes to move from `AnthropicExtractor` to `RuleBasedExtractor` — the
`Extractor` Protocol (`classify_and_extract(*, subject, sender, date, body) ->
EmailExtraction`) is the entire seam. **The Phase 5 sync-mechanism swap validated the
trust-model/matching abstraction the same way**: `process_message` in `pipeline/service.py`
is byte-for-byte the same decision logic `process_inbox` used to run inline — Phase 5
only changed who calls it (a worker instead of a request handler) and how messages are
sourced (paginated, bounded, resumable instead of a flat top-20 fetch).

## Key design decisions (why, not just what)

- **Trust model is a single column, `applications.source`.** `"manual"` vs `"gmail"`.
  Set to `"manual"` unconditionally by `update_application` on *any* manual edit — it
  never reverts, including when a human approves a classifier proposal through the
  review queue (`approve_review_item`'s update branch never touches `source`). This is a
  **one-way ratchet by design**: once a human has touched a row, every future classifier
  change to it requires approval, forever. Fail-closed, not a bug.
- **Three independent gates force review, not just ownership**: `_apply_decision` in
  `pipeline/service.py` (called from `process_message`, in turn called by
  `sync/worker.py::process_job`) routes to `pending_review` if the matched application
  isn't `source="gmail"`, **or** the fuzzy match is ambiguous, **or** confidence is below
  `settings.classification_confidence_threshold` (currently 0.85) — any one alone is
  enough, independent of the other two.
- **Fuzzy matching thresholds** (`pipeline/matching.py`, unchanged since Phase 4):
  `STRONG_MATCH_THRESHOLD=85`, `NO_MATCH_THRESHOLD=60`, `AMBIGUOUS_MARGIN=10`. Score
  ≥85 AND ≥10 points clear of the runner-up → confident match. 60–84, or ≥85 with a
  runner-up within 9 points → ambiguous → review. <60 → confident "no match" → create
  (still gated by confidence before it's *auto*-created).
- **Local classifier confidence formula** (`classifier/extractor.py`): three additive,
  individually-capped components — relatedness strength (`base`, up to 0.6), status-score
  margin clarity (`margin`, up to 0.3), a known-ATS-domain bonus (`domain_bonus`, 0.1) —
  minus an extraction-uncertainty penalty (0.0/0.15/0.35 depending on whether
  company/position came from a strong template match, a weaker domain/display-name
  guess, or nothing at all). All constants live in `classifier/patterns.py`, calibrated
  once against `evaluation/dataset.jsonl` (Task 8 of the 4b plan) — they are *starting
  points*, not physical constants; re-tune them the same way (measure, adjust one at a
  time, re-measure) if real usage reveals gaps.
- **Idempotency**: `ProcessedMessage`, unique on `(user_id, gmail_message_id)`. A message
  is classified at most once ever — holds across initial sync, incremental sync, and
  retries; `sync/worker.py::process_job` also does a defense-in-depth batched pre-check
  per page before re-fetching/re-processing anything.
- **Gmail fetch is now paginated and bounded, not a flat top-N.**
  `google_api.list_message_ids_page` pages via `pageToken` within a date-bounded query
  (`after:YYYY/MM/DD`). Initial sync looks back `settings.gmail_sync_backfill_days`
  (default 180); incremental sync re-queries from `GmailConnection.last_synced_message_date
  - 1 day` (the 1-day margin exists because Gmail's `after:`/`before:` operators are
  date-granularity only, not timestamp-precise — the overlap is caught for free by the
  idempotency constraint above). The watermark advances only on a *successful* sync, to
  that job's `started_at` date (not `finished_at`), so no message arriving mid-run can
  be skipped by the next incremental query.
- **The sync job queue is a Postgres table, not a new service.** `sync_jobs`, claimed via
  `SELECT ... FOR UPDATE SKIP LOCKED` (`worker.py::claim_next_job`) — race-safe today
  with one worker process, and ready for more than one without a code change. A partial
  unique index, `UNIQUE(user_id) WHERE status IN ('queued','running')`, is what makes
  "only one active sync per user" a DB-enforced guarantee rather than an app-level race.
- **Retry/backoff**: a page-level (not per-message) `GoogleApiError`, or in fact any
  unexpected exception, increments `SyncJob.attempts`; under `max_attempts` (default 3)
  it requeues with backoff `min(30 * 2**attempts, 3600)` seconds and *keeps* `page_token`
  so the retry resumes mid-pagination; at `max_attempts` it becomes permanently `failed`.
  Per-message failures (one bad email) remain non-fatal to the job, unchanged since
  Phase 4 — only counted in `failed_count`.
- **Stale-job reaper** (`worker.py::reap_stale_jobs`, runs once per `run_forever` tick):
  a `"running"` job whose `updated_at` hasn't advanced in
  `settings.sync_stale_job_threshold_minutes` (default 15) is requeued/failed the same
  way a caught exception would be. Exists because a handful of narrow DB-level failure
  paths inside `process_job` (not a Gmail API failure — that's already retried) can leave
  a job stuck `"running"` forever, which the partial unique index above would otherwise
  turn into a permanent per-user lockout. Reuses `updated_at` from the existing
  `TimestampMixin` — no schema change. Has its own try/except in `run_forever`, separate
  from `claim_next_job`'s, specifically so a failing reaper can never block real sync
  work. **Known remaining gap, not yet built**: no one-shot sweep at worker startup, so
  a job orphaned by a deploy/restart still waits up to the full threshold before the
  reaper notices — see the Phase 5 gap list below.
- **Error messages shown to the user are never raw exception text.**
  `worker.py::_safe_error_message` maps exceptions to one of two generic strings
  (Gmail-related vs. everything else) before writing to `SyncJob.error_message` (which
  `SyncJobRead` exposes and `SyncPanel.tsx` renders verbatim) — a raw `SQLAlchemyError`
  can otherwise carry SQL text and bound parameters. The real exception still goes to
  `logger.exception(...)` at the point of failure. **Deliberately only two categories**
  (YAGNI) — e.g. a revoked/expired Gmail grant currently gets the same "usually
  temporary, try again" message as a transient blip, which is mildly misleading since
  the actual fix is reconnecting Gmail; not fixed, no evidence yet it's confusing in
  practice.
- **Evaluation is free and always-on.** `evaluation/dataset.jsonl` +
  `evaluation/run_eval.py` cost nothing per run, and `tests/test_evaluation_accuracy.py`
  gates classification/status/company/position accuracy as part of the normal `pytest`
  run.

## Known gaps (not fixed, flagged for a future phase)

- **No startup sweep for orphaned "running" jobs.** After a worker deploy/restart, any
  job that was `"running"` at the moment of restart is orphaned by definition (nothing
  is working it), but nothing notices until `reap_stale_jobs` naturally catches it after
  `sync_stale_job_threshold_minutes`. A one-shot unconditional sweep before
  `run_forever`'s loop starts would cut this to zero — deferred because it's a new
  behavior beyond what the Phase 5 design covered, not because it's hard.
- **`_safe_error_message` has only two categories** (see above) — a revoked Gmail grant
  and a transient API blip render the same "try again shortly" message, which gives
  wrong advice for the former (needs reconnect, not a retry).
- **The reaper runs every worker poll tick (every 2s)** rather than being throttled to
  e.g. once a minute. Explicitly flagged as harmless at this project's scale, not
  fixed.
- **Multi-worker is unverified.** `claim_next_job` and `reap_stale_jobs` both use
  `FOR UPDATE SKIP LOCKED` and are believed safe with more than one worker process, but
  only one worker process has ever actually been run against this code. If a second
  worker is introduced, re-verify the reasoning in the Phase 5 spec's §2 concurrency
  discussion before trusting it.
- **The old Phase 4 "ReviewQueue only fetches once on mount" staleness gap is fixed.**
  (Noted here only so a search for it in old notes doesn't mislead — `ReviewQueue`'s
  `refreshSignal` prop, bumped by `ApplicationsPage` on sync completion/failure, closes
  it.)

## Local dev environment (this machine)

An unrelated project (`~/chasel/chasel-frontend`) occupies ports 5173–5177 on this
machine. This project's frontend is pinned to **5178** (`npm run dev -- --port 5178
--strictPort`), and `backend/.env`'s `CORS_ORIGINS`/`FRONTEND_URL` are set to
`http://localhost:5178` to match — don't "fix" this back to 5173 without checking
whether that conflict still exists. Backend runs on the default `:8000`. Both are
gitignored per-machine `.env` state, not something to commit.

**A third process is required for Gmail sync to do anything**: the background worker,
`uv run python -m app.sync.worker` from `backend/`. Clicking "Sync Gmail" only enqueues
a `SyncJob` row — without the worker running, it stays `queued` forever (the frontend
will show "Syncing…" indefinitely, indistinguishable from a slow real sync unless you
check that the worker process is actually up).

## Testing conventions

Backend: `cd backend && uv run pytest`. Frontend: `cd frontend && npx tsc -b && npx
oxlint`. Every backend test mocks the `Extractor` Protocol or Gmail's `google_api`
module — no test makes a real network call. `evaluation/run_eval.py` is *not* part of
the pytest suite (it's a standalone reporting script); `tests/test_evaluation_accuracy.py`
is the automated subset of it.

Two non-obvious test techniques introduced in Phase 5, in `backend/tests/test_sync_worker.py`,
worth knowing about before extending that file:
- **Real cross-connection locking tests** (`test_claim_next_job_skips_a_row_locked_by_another_connection`)
  bypass the standard `db_session` fixture entirely — it wraps every test in one
  uncommitted outer transaction, which a second real connection could never see. These
  tests use the session-scoped `engine` fixture directly, commit real rows, and clean
  them up manually in a `finally` block (required — a failed assertion must not leak
  committed rows into the shared test database).
- **Genuine DB-level failure tests** (`test_process_job_recovers_from_a_db_level_failure_without_raising`)
  inject a real Postgres error (`db.execute(text("SELECT 1/0"))`) rather than a plain
  Python exception, at the one point in `process_job` where `job`'s ORM attributes are
  actually expired and untouched since the prior commit (`SessionLocal` defaults to
  `expire_on_commit=True`) — a plain Python exception doesn't poison the session and so
  can pass even when the exception-handling code is subtly wrong. This is exactly how a
  real bug (`logger.exception()` called before `db.rollback()`, raising
  `PendingRollbackError` on a poisoned session and masking the original failure) was
  caught during Phase 5 review after an earlier, naive version of this test passed
  without proving anything.
