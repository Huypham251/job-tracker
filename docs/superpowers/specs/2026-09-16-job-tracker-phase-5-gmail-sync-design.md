# Job Application Tracker — Phase 5 Design: Background Gmail Sync & Job Queue

**Date:** 2026-09-16
**Status:** Approved for implementation planning
**Scope:** Phase 5 only — replace the Phase 4 synchronous, 20-message-capped "Process
Inbox" flow with a proper bounded initial/backfill sync, incremental syncs thereafter,
and a Postgres-backed job queue/worker so mailbox processing runs off the HTTP request
lifecycle. Builds directly on Phase 4's pipeline
(`docs/superpowers/specs/2026-09-15-job-tracker-phase-4-pipeline-design.md`) and Phase
4b's local classifier
(`docs/superpowers/specs/2026-09-15-job-tracker-phase-4b-local-classifier-design.md`),
both of which are preserved unchanged (see §13).

## 1. Goal & Non-Goals

### Goal

1. Let a user's first sync paginate through Gmail history within a bounded window
   (default 180 days), not just the most recent 20 messages.
2. After an initial sync, let subsequent syncs be incremental — process only messages
   newer than the last successful sync, not the whole window again.
3. Move email fetching/classification/matching off the HTTP request thread into a
   background worker, so starting a sync returns immediately and the UI never freezes.
4. Give every sync run visible, queryable state: queued/running/completed/failed,
   progress counts, and a final summary — not just a single blocking response.
5. Retry transient Gmail API failures with backoff; fail a sync cleanly (with a reason)
   after retries are exhausted, without losing partial progress.
6. Prevent two concurrent sync jobs for the same user, race-safely.
7. Preserve `ProcessedMessage`'s `(user_id, gmail_message_id)` idempotency guarantee —
   no sync strategy change is allowed to let a message be processed twice.
8. Preserve Phase 4/4b's classifier, matching, and trust-model behavior exactly.

### Non-Goals (explicitly deferred)

Automatic/scheduled periodic sync (a cron-like enqueuer for all connected users) — this
phase is manual-trigger only ("Sync Gmail" button); the job-queue design makes adding a
scheduler later straightforward (it would just be another enqueuer), but building the
scheduler itself is out of scope now. Multiple concurrent worker processes (the design
is safe for it via `FOR UPDATE SKIP LOCKED`, but one worker process is all that's run).
Gmail History API (`historyId`-based deltas) — deferred in favor of a simpler
date-watermark approach (§2). Redis/Celery or any other new infra service. Real-time
push (SSE/WebSocket) progress — the frontend polls instead. Per-field confidence,
fuzzy-match tuning, and multi-provider classification remain Phase 4/4b non-goals,
untouched here.

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Job queue | A `sync_jobs` Postgres table, claimed via `SELECT ... FOR UPDATE SKIP LOCKED` | No new infrastructure — Postgres is already required. Row-claiming is race-safe and scales to multiple worker processes later with no code change, without paying for that complexity now. |
| Worker | A separate Python process (`app/sync/worker.py`, run via `uv run python -m app.sync.worker`), not an in-process asyncio task | Survives independently of the API process; a crashed/restarted API server doesn't kill or duplicate in-flight sync state, which lives entirely in `sync_jobs`. |
| Incremental strategy | Date watermark (`GmailConnection.last_synced_message_date`) + re-list with `after:`, not the Gmail History API | Simpler and has no expiry edge case (Gmail's `historyId` expires after ~7 days of inactivity, forcing a full-resync fallback). `ProcessedMessage`'s existing uniqueness constraint already makes any watermark overlap harmless, so History API's efficiency gain isn't worth its added failure modes here. |
| Backfill window | 180 days, via a new `settings.gmail_sync_backfill_days` | Bounds worst-case pagination/classification work on a first sync, the same spirit as `pipeline_batch_limit` bounding Phase 4's per-click cost. |
| Incremental overlap margin | Re-query from `last_synced_message_date - 1 day` | Gmail's `after:`/`before:` search operators are date-granularity only, not timestamp-precise — there's no way to express "since this exact second." The 1-day margin guarantees no message is missed at a day boundary; the resulting overlap is a few wasted list/classify calls, deduplicated for free by `ProcessedMessage`. |
| Watermark update timing | Set to the job's `started_at` date, only when the job completes successfully | Using the job's start time (not completion time or "now") closes the gap where a message arrives between when listing started and when the job finished — that message's date is still `>=` the new watermark, so the next incremental sync's overlap window naturally re-sees it. A failed job never advances the watermark, so nothing already-attempted is silently skipped next time. |
| Duplicate-sync prevention | A partial unique index, `UNIQUE(user_id) WHERE status IN ('queued','running')`, plus a friendly pre-check in the API returning 409 | DB-enforced uniqueness is race-safe against concurrent requests in a way an app-level check-then-insert isn't; the pre-check just makes the common case return a clean 409 instead of surfacing a raw integrity error. |
| Retry/backoff | Job-level: transient `GoogleApiError` during list/page fetch requeues with exponential backoff (30s × 2^attempts, capped), up to `max_attempts=3`, then `status="failed"`. Message-level: unchanged from Phase 4 — a single message's fetch/classification failure is logged and skipped, doesn't fail the job. | Distinguishes "Gmail's API had a blip" (worth retrying the whole page) from "this one email is malformed" (never worth retrying, already Phase 4's proven behavior). Two tiers, not one, because they have different causes and different correct responses. |
| Progress checkpointing | `sync_jobs.page_token` is persisted after every completed page | A retried or resumed job continues from its last completed page instead of restarting the whole window, bounding wasted work from a mid-run failure. |
| Frontend progress | Polling `GET /gmail/sync/{id}` every ~2s, not SSE/WebSocket | Matches the existing REST-only frontend; no new client/server streaming pattern to build and debug for a personal-scale app. |
| Trigger mechanism | Manual "Sync Gmail" button only | Matches this phase's explicit non-goal (no scheduler yet); mirrors the existing manual "Process Inbox" trigger pattern it replaces. |
| Old sync trigger | `POST /pipeline/process` and `process_inbox` are removed, not kept alongside the new flow | Two competing ways to pull mail in would be confusing and would both need to respect the same idempotency/trust rules — one flow, replacing the old one, is simpler and matches the phase goal of moving processing off the request thread entirely. |

## 3. Project Structure — additions and changes

```
backend/app/
├── gmail/
│   ├── google_api.py     MODIFIED — add list_message_ids_page() (query + pageToken)
│   └── models.py          MODIFIED — GmailConnection.last_synced_message_date
├── pipeline/
│   ├── service.py          MODIFIED — process_inbox() and its request-scoped fetch
│   │                          loop removed; _apply_decision() and ProcessedMessage
│   │                          persistence extracted into a function callable by
│   │                          app/sync/worker.py, unchanged in behavior
│   └── router.py            MODIFIED — POST /pipeline/process removed;
│                              /pipeline/review* unchanged
└── sync/                    NEW — job queue, orchestration, worker
    ├── models.py              SyncJob
    ├── schemas.py             SyncJobRead
    ├── service.py             enqueue_sync(), get_job(), get_latest_job()
    ├── exceptions.py          SyncAlreadyRunning
    ├── router.py              POST /gmail/sync, GET /gmail/sync/{id},
    │                            GET /gmail/sync/latest
    └── worker.py              claim_next_job(), process_job(), the poll loop;
                                 run via `uv run python -m app.sync.worker`

frontend/src/
├── api/sync.ts               NEW — startSync(), getSyncJob(), getLatestSyncJob()
├── types/sync.ts              NEW — SyncJob type
└── components/
    ├── ApplicationsPage.tsx   MODIFIED — "Process Inbox" section replaced by sync UI
    └── SyncPanel.tsx            NEW — button, polling, progress/summary display
```

`app/classifier/`, `app/pipeline/matching.py`, and the trust-model logic inside
`_apply_decision` are **not modified**.

## 4. Data Model

### New table: `sync_jobs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → `users.id`, `ON DELETE CASCADE` | indexed |
| `job_type` | String(20) | `"initial"` \| `"incremental"` |
| `status` | String(20) | `"queued"` \| `"running"` \| `"completed"` \| `"failed"` |
| `attempts` | Integer, default 0 | |
| `max_attempts` | Integer, default 3 | |
| `next_attempt_at` | timestamptz, default `now()` at insert | worker only claims rows where this is `<= now()`; a retry pushes it into the future, a fresh job is immediately eligible |
| `window_start` | Date | the `after:` bound used for this job's Gmail query |
| `page_token` | String, nullable | Gmail pagination cursor; checkpointed after every page |
| `messages_seen` | Integer, default 0 | messages listed so far |
| `messages_processed` | Integer, default 0 | messages that reached a decision (success, skip, or failure) |
| `auto_applied`, `queued_for_review`, `ignored`, `failed_count` | Integer, default 0 | mirrors Phase 4's `ProcessResult` shape, plus `failed_count` for per-message failures |
| `error_message` | Text, nullable | set when `status="failed"` |
| `started_at`, `finished_at` | timestamptz, nullable | |
| `created_at`, `updated_at` | via `TimestampMixin` (existing convention) | |

```sql
CREATE UNIQUE INDEX uq_sync_jobs_user_active
  ON sync_jobs (user_id)
  WHERE status IN ('queued', 'running');
```

### Modified table: `gmail_connections`

- `+ last_synced_message_date` (Date, nullable). `NULL` means "never synced" →
  `enqueue_sync` chooses `job_type="initial"`. Set only on a job's successful
  completion, to that job's `started_at` date.

### Modified table: `processed_messages`

- `+ sync_job_id` (UUID, FK → `sync_jobs.id`, `ON DELETE SET NULL`, nullable) — audit
  trail only. Existing rows get `NULL`. The `(user_id, gmail_message_id)` unique
  constraint is unchanged and remains the sole idempotency guarantee.

### Migration `0006_add_sync_jobs`

Creates `sync_jobs` (with the partial unique index above), adds
`gmail_connections.last_synced_message_date`, adds
`processed_messages.sync_job_id`. Purely additive — no backfill needed, no data loss
risk to existing rows.

## 5. Sync Flows

### Enqueue (`POST /gmail/sync` → `sync/service.py::enqueue_sync`)

1. Look up the user's `GmailConnection`; `404`/`GmailNotConnected` if absent (existing
   exception, reused).
2. Check for an existing row with `status IN ('queued','running')` for this user; if
   found, raise `SyncAlreadyRunning` → router returns `409` with that job's
   `SyncJobRead`.
3. `job_type = "initial" if connection.last_synced_message_date is None else "incremental"`.
4. `window_start = today - settings.gmail_sync_backfill_days` for initial;
   `connection.last_synced_message_date - 1 day` for incremental.
5. Insert the `SyncJob` row (`status="queued"`), commit, return it. The partial unique
   index is the race-safe backstop if two requests land at nearly the same instant.

### Worker loop (`sync/worker.py`)

```
while True:
    job = claim_next_job()   # FOR UPDATE SKIP LOCKED, status='queued', next_attempt_at <= now()
    if job is None:
        sleep(POLL_INTERVAL_SECONDS)  # 2s
        continue
    process_job(job)
```

`claim_next_job()` marks the row `status="running"`, sets `started_at` on first claim
only (not on a resumed retry), and commits immediately to release the row lock before
any Gmail API call is made.

`process_job(job)`:

1. Build the Gmail query: `after:<job.window_start>`.
2. Loop pages: call `google_api.list_message_ids_page(token, query=..., page_token=job.page_token, max_results=100)`.
   - For each message id not already in `ProcessedMessage` for this user (defense in
     depth — the page itself shouldn't repeat ids): fetch summary + body, run the
     extractor, call the reused `_apply_decision`, persist `ProcessedMessage` with
     `sync_job_id=job.id`, commit. On `GoogleApiError`/`ClassificationError` for a
     single message: log, increment `failed_count`, continue (unchanged Phase 4
     behavior).
   - Update `messages_seen`/`messages_processed`/`auto_applied`/`queued_for_review`/
     `ignored` as messages are decided.
   - After the page: `job.page_token = next_page_token`, commit (checkpoint).
3. If a page-level `GoogleApiError` is raised (the list call itself, not a single
   message): `job.attempts += 1`; if `attempts < max_attempts`, set
   `status="queued"`, `next_attempt_at = now() + backoff(attempts)`, commit, return
   (another worker tick will pick it back up from the checkpointed `page_token`); else
   `status="failed"`, `error_message=str(e)`, `finished_at=now()`, commit.
4. When `next_page_token` is `None` (pagination exhausted): `status="completed"`,
   `finished_at=now()`, and `connection.last_synced_message_date = job.started_at.date()`.
   Commit. The partial unique index guarantees no other job for this user could have
   been running concurrently, so this update never races another job's watermark write.

### Idempotency

Unchanged guarantee from Phase 4: `ProcessedMessage` unique on `(user_id,
gmail_message_id)`. Every insert in step 2 above either succeeds once or — if a retry
re-lists a message already processed in an earlier attempt of the *same* job (possible
if a page was fully processed but the checkpoint commit raced a crash) — is skipped by
the "not already in `ProcessedMessage`" check before re-processing. Cross-job overlap
(incremental's 1-day margin) is caught the same way.

## 6. API Contract

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| POST | `/api/v1/gmail/sync` | — | `202 SyncJobRead` | `409 SyncJobRead` (the active job) if one is already running |
| GET | `/api/v1/gmail/sync/{id}` | — | `200 SyncJobRead` | `404` if not found or not owned by the caller |
| GET | `/api/v1/gmail/sync/latest` | — | `200 SyncJobRead \| null` | for resuming polling after a page refresh |

`SyncJobRead`: `id, job_type, status, attempts, messages_seen, messages_processed,
auto_applied, queued_for_review, ignored, failed_count, error_message, started_at,
finished_at, created_at`.

`POST /api/v1/pipeline/process` is removed. `GET /api/v1/pipeline/review`,
`POST /api/v1/pipeline/review/{id}/approve`, `POST /api/v1/pipeline/review/{id}/reject`
are unchanged.

## 7. Frontend Structure & Data Flow

`SyncPanel.tsx` (new, replaces the "Pipeline" section in `ApplicationsPage.tsx`):

- On mount, calls `GET /gmail/sync/latest`; if the result is `queued`/`running`, starts
  polling immediately (survives a page refresh mid-sync).
- "Sync Gmail" button calls `POST /gmail/sync`. A `202` starts polling
  `GET /gmail/sync/{id}` every 2s; a `409` starts polling the returned existing job
  instead of showing an error.
- While polling, shows `messages_processed` / `messages_seen` and disables the button.
- On `completed`, shows the auto_applied/queued_for_review/ignored/failed_count summary,
  calls `refetch()` (applications list) and triggers `ReviewQueue`'s refresh — the same
  trigger point that today only refreshes the applications list, so this closes the gap
  noted in `CLAUDE.md` where newly-queued review items don't appear without a manual
  reload.
- On `failed`, shows `error_message` and re-enables the button for a fresh attempt.

`GmailPanel.tsx`'s existing "Fetch recent messages" test view is untouched — it's a
separate, explicitly display-only Phase 3 feature, not a sync trigger.

## 8. Testing Plan

Every test continues the existing convention: mock `Extractor` and `google_api`
(via `httpx` mocking, matching current tests) — no real network calls anywhere in the
suite.

- **`test_sync_models.py`** — `SyncJob` CRUD; partial unique index rejects a second
  `queued`/`running` row for the same user; allows a new row once the prior one is
  `completed`/`failed`.
- **`test_gmail_google_api.py`** (extended) — `list_message_ids_page`: query string
  construction (`after:` date), single-page and multi-page (`pageToken`) sequences,
  error propagation on non-200.
- **`test_sync_service.py`** — `enqueue_sync`: `initial` when
  `last_synced_message_date is None`, `incremental` with the 1-day-margin
  `window_start` otherwise; `SyncAlreadyRunning` raised when a job is already
  active; `GmailNotConnected` when no connection exists.
- **`test_sync_worker.py`** — `claim_next_job` under simulated concurrent claims (two
  sessions attempting to claim the same queued row — exactly one succeeds); full
  initial-sync happy path against mocked multi-page Gmail responses and a mocked
  extractor (verifies `_apply_decision` reuse, `ProcessedMessage` persistence,
  progress counters, `page_token` checkpointing); page-level `GoogleApiError` triggers
  requeue-with-backoff and eventual `failed` after `max_attempts`; a single message's
  `ClassificationError` increments `failed_count` without failing the job; a
  successful job's completion sets `GmailConnection.last_synced_message_date`.
- **`test_sync_router.py`** — endpoint auth (401 unauthenticated), `202`/`409`/`404`
  contracts, `latest` returning `null` when no job exists yet.
- **`test_pipeline_service.py`/`test_pipeline_router.py`** — `process_inbox`-specific
  tests removed; `_apply_decision`, matching, approve/reject tests unchanged (they
  don't exercise the removed fetch loop).
- **`tests/test_evaluation_accuracy.py`** — unchanged; the classifier isn't touched.
- **Frontend** — `npx tsc -b` and `npx oxlint` clean, as always. Manual verification:
  start a sync, watch progress update, refresh the page mid-sync and confirm polling
  resumes, attempt a second sync while one is running and confirm it attaches to the
  existing job rather than erroring, review the completion summary, confirm the review
  queue shows new items without a manual reload, run a second (incremental) sync and
  confirm no duplicate applications/review items are created for already-seen mail.

## 9. Preserving Phase 1–4b Functionality

Manual CRUD (Phase 1), auth (Phase 2), the Gmail OAuth connect/disconnect flow and the
"Fetch recent messages" test view (Phase 3), and the classifier/matching/trust-model
(Phase 4/4b) are all unmodified by this design. The only Phase 4 code removed is
`process_inbox` and its router endpoint — the logic it contained
(`_apply_decision`, `ProcessedMessage` persistence, the confidence/matching gates) is
preserved verbatim, just called from `app/sync/worker.py` instead of from a
request handler. `docs/superpowers/specs/2026-09-15-job-tracker-phase-4-pipeline-design.md`
and `...-phase-4b-local-classifier-design.md` remain accurate for everything except
§2's "Trigger mechanism" row (superseded by this document's §2).
