# Job Application Tracker — Phase 9 Design: On-Demand Sync with Separate Import/Incremental Lanes

**Date:** 2026-09-24
**Status:** Implemented and verified in production 2026-09-24 (results in CLAUDE.md, "Phase 9 results"). Approved 2026-09-24 (two-lane design now; time-slicing deferred; 1-year dispatch token; lane isolation verified by automated test only)
**Scope:** Make an explicit "Sync Gmail" click start processing within seconds instead
of waiting hours for GitHub's throttled cron, without moving sync work back into the
Render API. The queue, worker, retry/backoff, reaper, idempotency, classifier,
matching and review pipeline are kept. Execution stays in GitHub Actions. What changes
is **who starts the worker**, and initial imports get **their own lane**.

## 0. Why

Phase 8's production verification showed the sync engine works but the trigger
doesn't: `sync-worker.yml`'s `*/5` cron fired about 11 times a day, with gaps of 2–5
hours, so a user who clicks Sync waits hours for a job that then finishes in under a
minute. The same run history shows **manually dispatched runs doing real work 11–18s
after dispatch** (runs `35803854530`, `36071358997`, `36074416159`, `36076402844`), and
a small incremental finishing about 20s after dispatch. The bottleneck is purely the
trigger.

A second problem hides behind the first: with one worker lane, a long initial import
(about 75 min for a 6,500-message mailbox at the ~0.7 s/message measured in production)
holds the lane's only slot, so another user's 30-second incremental sync would wait
behind it.

## 1. Goal & Non-Goals

### Goal

- Clicking **Sync Gmail** for an incremental sync completes in **about a minute
  or less** when the backend is warm (target: dispatch-to-work under 30s, plus
  processing time). A cold backend adds Render's documented ~1 minute wake.
- Initial imports stay **asynchronous** and can't delay anyone's incremental sync.
- **Failure property (hard requirement):** if the immediate dispatch fails for any
  reason (no token, expired token, GitHub outage, Render restart right after the
  response), the job stays safely `queued`, the API request still succeeds, and the
  cron fallback, or the user clicking Sync again, still gets it processed.
- Manual application tracking stays fully usable without Gmail, and the Sync UI stops
  offering a sync to users who haven't connected Gmail.

### Non-Goals

- **Time-limited slicing of large initial imports** (a job pauses at a deadline,
  checkpoints and re-queues without using an attempt). Deferred by decision and recorded
  as a known future improvement (§10). Phase 9 keeps "one initial job runs to
  completion inside one run, bounded by the lane's timeout".
- Running any sync work in the Render API process (synchronously or on a thread).
  Rejected on Phase 8 evidence (§2).
- Gmail History API (`history.list`) for incremental syncs. Not needed for the
  responsiveness goal.
- Paid infrastructure (Render Background Worker, Starter plan).
- Changes to the classifier, matching, trust model or review queue.

## 2. Verified constraints (checked 2026-09-24)

**GitHub**
- Dispatch endpoint: `POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`,
  body `{"ref": "main"}`. It currently returns **200** with `workflow_run_id`,
  `run_url` and `html_url`. Older behavior (and older docs) return **204** with no
  body, so both count as success.
  ([REST: workflows](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event))
- **Minimum token permission:** a fine-grained PAT with repository permission
  **Actions: write** and nothing else. The permissions reference lists this endpoint as
  `write | PAT | ✗`, and `✗` in "Additional Permissions" means no other permission is
  required. Fine-grained PATs are GA, can be limited to **only this repository**, and
  may have any lifetime, including none, unless an org policy caps it. That doesn't
  apply to a personal repo.
  ([fine-grained permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens),
  [managing PATs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens))
  Classic tokens would need the whole `repo` scope, so they're rejected.
- `workflow_dispatch` only triggers a workflow file that **exists on the default
  branch**.
- Scheduled runs "can be delayed during periods of high loads", "some queued jobs may
  be dropped", and in a public repo they're **disabled after 60 days without
  repository activity**. Dispatch isn't affected by either.
  ([events that trigger workflows](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows))
- Concurrency with `cancel-in-progress: false` allows **one running and one pending**
  run per group. A newly queued run replaces the existing pending one. Max job
  timeout is 360 min.
  ([workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions))
- The repo is **public**, so Actions logs are publicly readable. A single Phase 8
  drain logged 153 Gmail request URLs containing message IDs (from `httpx` INFO
  logging).

**Render** ([free tier](https://render.com/docs/free), [compute plans](https://render.com/docs/compute-plans))
- Free web service: **512 MB RAM, 0.1 CPU**. Spins down after **15 min without
  inbound traffic** (HTTP requests or WebSocket messages) and takes **about a minute**
  to wake. Render "might restart a Free web service at any time".
- Background Workers have **no free plan** (start at Starter, 0.5 CPU).
- Web services allow long requests (up to 100 min), but the static site's rewrite
  proxy has **no documented timeout or limits**
  ([redirects/rewrites](https://render.com/docs/redirects-rewrites)). That's one more
  reason not to hold a sync inside a request.

**Consequences for the design:** sync work stays out of the API. The only thing the
API adds is one short outbound HTTPS call after the job commits, which fits 0.1 CPU and
a service that can restart at any moment, as long as that call can fail harmlessly.

## 3. Architecture

```
Browser ── POST /api/v1/gmail/sync ──> Render API
                                         1. enqueue_sync → SyncJob(queued) COMMIT
                                         2. 202 response returned to the browser
                                         3. background task: dispatch sync-<lane>.yml
                                            (best-effort; failure only logs a warning)
                                                  │ GitHub REST (fine-grained PAT, Actions:write)
                                                  v
GitHub Actions ── sync-incremental.yml  (dispatch + cron fallback, concurrency "sync-incremental")
              └── sync-initial.yml      (dispatch + cron fallback, concurrency "sync-initial")
                     └── python -m app.sync.drain --lane <lane>
                           reap(lane) → claim(lane) → process_job → … until the lane is idle
```

### 3.1 Lanes

A lane is simply a `SyncJob.job_type` value, which the column already stores, so
**there's no migration**.

| Lane | Claims | Workflow | Timeout | Drain budget |
|---|---|---|---|---|
| `incremental` | `job_type='incremental'` | `sync-incremental.yml` | 30 min | 20 min |
| `initial` | `job_type='initial'` | `sync-initial.yml` | 120 min | 100 min |

- `claim_next_job(db, job_type=None)` and `reap_stale_jobs(db, job_type=None)` gain an
  optional filter. `None` keeps today's behavior for `run_forever` (local dev) and for
  a lane-less `python -m app.sync.drain`.
- **Why two workers are safe here** (addressing CLAUDE.md's "multi-worker is
  unverified"): the two lanes claim **disjoint row sets** (different `job_type`), the
  partial unique index still guarantees at most one active job per user, and each lane
  is itself serialized by its concurrency group. So two processes never compete for the
  same row, even in principle. Claiming keeps `FOR UPDATE SKIP LOCKED` anyway. Each
  lane's reaper touches only its own lane's jobs.
- **Why the incremental timeout is 30 min, not 15:** an incremental sync after a long
  gap can be large. 30 min at about 0.35 s/message (after §3.4) covers roughly 5,000
  messages, which is about the whole 180-day window for the observed mailbox. The
  timeout only matters for a hung run.
- **Drain budgets** sit below each timeout so the run exits cleanly instead of being
  killed. The existing 240s default becomes a per-lane value.

### 3.2 Dispatch (`app/sync/dispatch.py`)

`request_worker(job_type: str) -> bool`:
- Does nothing (returns `False`) when `settings.sync_dispatch_token` is unset. That
  covers local dev and CI.
- `POST https://api.github.com/repos/{settings.sync_dispatch_repository}/actions/workflows/sync-{job_type}.yml/dispatches`
  with `{"ref": settings.sync_dispatch_ref}`, headers
  `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`,
  `Authorization: Bearer …`, and a **5s timeout**.
- A 200 or 204 response returns `True`. Anything else (non-2xx, timeout, connection
  error, any exception) logs one WARNING with the lane and HTTP status only, **never
  the token or response body**, and returns `False`. **It never raises.**

Router behavior (`POST /gmail/sync`):
- **New job:** commit (unchanged), then add `request_worker(job.job_type)` as a
  FastAPI background task, so it runs after the 202 is sent and adds no latency.
- **Existing active job (409 path, "re-kick"):** if that job is `queued`, or `running`
  with `updated_at` older than `sync_stale_job_threshold_minutes`, the 409 response
  carries the same background dispatch. The frontend already treats 409 like 202 (both
  return the job), so clicking Sync again means "try starting the worker again", with
  no API contract change. Re-kicks are bounded by one active job per user, and repeated
  dispatches collapse to one pending run per lane.
- The response is never affected by the dispatch result. That's the failure property
  in §1.

New settings (all optional; unset means dispatch is disabled):
`SYNC_DISPATCH_TOKEN`, `SYNC_DISPATCH_REPOSITORY` (`Huypham251/job-tracker`),
`SYNC_DISPATCH_REF` (default `main`). They're deliberately not named `GITHUB_*`, to
avoid colliding with variables Actions sets itself.

### 3.3 Retry-aware drain

Today a job that fails once is requeued with backoff (60s, then 120s) and then waits
for the *next* worker run. Under dispatch-driven runs that could mean waiting for cron.
So when a lane has no *due* job, `drain_once` checks the earliest `next_attempt_at`
among that lane's queued jobs. If it falls inside the remaining budget, it sleeps until
then and continues; otherwise it exits. Retries therefore complete within the same run.
No schema change.

### 3.4 One Gmail call per message

The worker currently makes two calls per new message (`format=metadata`, then
`format=full`). A new `google_api.get_message(access_token, message_id) -> (summary, body)`
does a single `format=full` fetch and builds the summary (id, Subject/From/Date
headers, snippet) from the same payload. The worker switches to it. The Phase 3
display endpoint keeps `get_message_summary`. Expected effect: about half the Gmail
round-trips per new message. Extraction input must be byte-identical, which the tests
assert against the same fixtures.

### 3.5 Log privacy

The drain entrypoint sets the `httpx` and `httpcore` loggers to WARNING. Worker and
pipeline warnings that print a Gmail message ID print a short hash instead
(`sha256(id)[:10]`), which can still be matched against the DB by hashing
`gmail_message_id`. Job IDs are app-generated UUIDs and stay as they are.

### 3.6 Workflows

`sync-worker.yml` is replaced by `sync-incremental.yml` and `sync-initial.yml`. Each
has:
- `workflow_dispatch` plus `schedule` (`*/15`, as a **fallback only**);
- its own `concurrency` group with `cancel-in-progress: false`;
- the timeout from §3.1;
- `python -m app.sync.drain --lane <lane>`;
- the same `PROD_*` secrets and `permissions: contents: read`.

`tests/test_sync_worker_workflow.py` is extended to check both files: fixed and distinct
groups, never cancel-in-progress, the timeouts, the right `--lane`, and both triggers.

## 4. Frontend

- **Queued vs running.** `queued` shows "Starting sync…"; `running` shows "Syncing — N
  of M messages". This fixes the Phase 8 confusion where a queued job read as "running
  — 0 of ?".
- **Initial import message:** "First import runs in the background and can take up to
  an hour for a large mailbox. You can close this page."
- **Stuck in the queue:** if a job is still `queued` about 2 min after the page first
  saw it, show "The sync worker hasn't started yet." with a **Retry** button that calls
  `POST /gmail/sync` (the re-kick path).
- **No Gmail:** `ApplicationsPage` lifts Gmail connection status (already fetched by
  `GmailPanel`) and hides `SyncPanel` when not connected. Manual add/edit/delete and the
  review queue are untouched.
- No new API endpoints or types.

## 5. Security

- **PAT scope:** fine-grained, **only `Huypham251/job-tracker`**, repository
  permission **Actions: write** only. GitHub may also attach its mandatory read-only
  Metadata permission; confirm at creation that nothing else is selected.
- **What someone with a leaked token could do:** start, re-run or cancel workflow runs,
  enable or disable workflows, and delete run logs and caches in this repo. They
  **could not** read secrets, read or push code, or touch Render or Neon. That's
  acceptable, and far narrower than a classic `repo` token.
- **Storage:** only in Render's backend Environment (`SYNC_DISPATCH_TOKEN`). It's never
  sent to the browser, never in the repo, and never needed in GitHub.
- **Expiry (decided): 1 year.** An expired token fails safe: dispatch gets a 401, the
  warning is logged, and jobs fall back to cron or a re-kick. But the app quietly goes
  back to waiting hours, so expiry must not be missed. Docs record: the creation date
  and exact expiry date (in CLAUDE.md, never the value); a rotation procedure in
  README's rotation table (create a replacement token with the same scope, update
  `SYNC_DISPATCH_TOKEN` on Render **before** the old one expires, verify a dispatched
  run, then delete the old token); and an explicit reminder to set a calendar event
  about 2 weeks before expiry. GitHub also emails the owner before a token expires.
- **Abuse:** only signed-in test users can reach `POST /gmail/sync`. Dispatches are
  bounded by one active job per user and collapse per lane. No workflow inputs are
  accepted, so there's no injection surface. Rate limiting is still a documented gap.
- **Log privacy:** §3.5.

## 6. Failure modes

| Failure | Result |
|---|---|
| Token unset, invalid or expired; GitHub API down; timeout | 202 as normal, job `queued`, WARNING logged; picked up by cron fallback or a Retry click |
| Render restarts after commit, before the background task runs | Same as above (the job was committed first) |
| Dispatch accepted but the run is delayed or dropped by GitHub | Job `queued`; the next dispatch or cron picks it up |
| Run killed mid-job (runner loss, timeout) | Job left `running`; a later run in the same lane reaps it after 15 min (existing reaper + backoff); a Retry click on a stale-running job dispatches that run |
| Transient Gmail/DB error mid-job | Existing retry/backoff; retry-aware drain finishes it in the same run (§3.3) |
| Initial import longer than 120 min | Unchanged Phase 8 behavior: killed, reaped, uses an attempt, resumes from `page_token`; fails after 3. Addressed by deferred slicing (§10) |
| Cron fallback disabled after 60 quiet days | Dispatch still works; only the fallback is lost (documented) |

## 7. What is preserved

Everything from Phases 5–8 below the trigger: `SyncJob`, the partial unique index,
`SKIP LOCKED` claiming, attempts/backoff/terminal failure, `page_token` checkpoints,
per-page memory bounding, the reaper, `ProcessedMessage` idempotency, watermark
advancement, safe error messages, `run_forever` for local dev (unchanged, all lanes),
workflow concurrency and timeouts, and the entire classifier/pipeline/review stack.
`app/sync/inprocess.py` stays dormant and off.

## 8. Testing plan

Backend (all network mocked, as today):
- `dispatch`: disabled without a token (no HTTP call); 200 and 204 count as success;
  401/404/500, timeout and connection error each return `False` and never raise;
  the warning never contains the token; the URL and headers are correct.
- Router: a new job schedules a dispatch for its `job_type`; **a failing dispatch still
  returns 202 with the job persisted as `queued`**; 409 with a queued job schedules a
  re-kick; 409 with a healthy running job doesn't; 409 with a stale running job does.
- Lanes: `claim_next_job(job_type=…)` ignores the other lane; `reap_stale_jobs(job_type=…)`
  only reaps its lane; `drain_once(lane=…)` leaves other-lane jobs `queued`. A real
  two-connection test (existing technique) claims one job from each lane concurrently
  and confirms neither blocks or steals from the other.
- Retry-aware drain: a job with `next_attempt_at` inside the budget is processed in
  the same call (sleep patched); one outside the budget is left for later.
- `get_message`: one HTTP call; the summary and body match what the two old calls
  produce on the same fixture payloads; the worker uses it.
- Log privacy: a drain log line never contains a raw message ID; `httpx` is at WARNING.
- Workflow config tests (§3.6); the subprocess entrypoint test still passes for
  `app.sync.drain --lane …`.

Frontend: `tsc -b`, `oxlint` (no new warnings), `npm run build`.
Evaluation: `evaluation.compare` unchanged, since the classifier isn't touched.

## 9. Implementation checkpoints

Per the maintainer's standing preference, each checkpoint is committed to `main` and
pushed **only after the full local check suite passes** (backend pytest,
`evaluation.compare`, frontend tsc/oxlint/build), then CI on `main` is confirmed green.
Each code checkpoint gets a review pass before its push, and a final whole-change
review runs before the cloud step.

1. **Spec + plan.** Commit this spec and a task-level plan under
   `docs/superpowers/plans/`.
2. **Worker lanes, retry-aware drain, log privacy.** Backend only; backward compatible
   (lane-less drain = today's behavior), so the existing `sync-worker.yml` keeps
   working after the push.
3. **Two lane workflows.** Add `sync-incremental.yml` and `sync-initial.yml`, remove
   `sync-worker.yml`, extend the workflow tests. After the push, one
   `workflow_dispatch` of each confirms both files are valid and drain their own lane.
4. **Dispatch client + router re-kick + settings.** No-op in production until the
   token exists.
5. **Single-fetch Gmail call.**
6. **Frontend UX** (§4).
7. **Final whole-change review** of checkpoints 2–6.
8. **Cloud configuration, guided one step at a time:** create the fine-grained PAT
   (only this repo, Actions: write, 1-year expiry); set `SYNC_DISPATCH_TOKEN`,
   `SYNC_DISPATCH_REPOSITORY` and `SYNC_DISPATCH_REF` on Render using the same
   no-echo clipboard method as the Phase 8 rotations; wait for Live.
9. **Production verification:**
   - incremental click-to-completed time, warm and cold backend (warm target about a
     minute or less);
   - the Actions run shows `event: workflow_dispatch`, started seconds after the click;
   - **failure drill:** set an invalid `SYNC_DISPATCH_TOKEN`, click Sync, confirm a
     202 and a `queued` job with a WARNING in Render logs; restore the token, click
     Retry, confirm completion;
   - public log check: no raw message IDs in the run log;
   - **lane isolation (decided): not verified in production.** No second Gmail account
     will be created for it. It rests on the automated two-connection test, and the docs
     say so explicitly. The part that *can* be checked safely in production without
     another account: after checkpoint 3, one `workflow_dispatch` of the **initial**
     workflow while no initial job exists shows it drains nothing and leaves the
     incremental queue untouched (log: `Drained 0 job(s) from the initial lane`).
10. **Docs:** CLAUDE.md (Phase 9 results, updated architecture and known gaps,
    multi-worker note), README (sync behavior, the new env vars, PAT setup and a
    rotation row), and a supersession note in the Phase 8 spec.

## 10. Known future improvement (deferred, not in Phase 9)

**Time-sliced initial imports.** `process_job` still has no time budget of its own.
The deferred design: stop between pages at a deadline, persist `page_token`, re-queue
**without** incrementing `attempts`, and have the run dispatch its own lane again
(GitHub's docs confirm that a `workflow_dispatch` sent with a workflow's
`GITHUB_TOKEN` does create a new run. The `permissions: actions: write` requirement
is inferred from the endpoint's permission mapping; re-verify it when this work is
scheduled). This would remove the 120-minute ceiling on
initial imports and make a killed run lose at most one slice.
