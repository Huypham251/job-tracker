# Job Application Tracker — Phase 10 Design: Production Hardening & Observability

**Date:** 2026-09-25
**Status:** Implemented and verified in production 2026-09-25 (results in CLAUDE.md, "Phase 10 results"; S4 automated-only by decision; M-c 7-day observation pending). Scope decisions approved 2026-09-25: full scope (items 1–6,
including rate limiting); 25-minute initial slice, configurable; GitHub failed-run email
as the only alert channel; a Google consent-screen investigation that stops for approval
before any Google Cloud change; static-site security headers in CP5 with a conservative
CSP. **CP0 decided 2026-09-25: option (a), stay in Testing; CP7 dropped** (§11.5).
**Scope:** Make the deployed app fail clearly, recover on its own, and tell the
maintainer when it can't, **without new infrastructure or hosting cost**. The
architecture stays as it is: Render Static Site + Render free Web Service + Neon + GitHub
Actions lane workers. The classifier, matching, trust model, review queue and manual
CRUD are not touched. The ~1-minute user-triggered incremental sync from Phase 9 must be
preserved and is re-measured.

## 0. Why

Phase 9 made syncs fast. Phase 10 is about what happens when things go wrong. Reading
the code and the production setup on 2026-09-25 turned up these gaps, most likely first:

| # | Gap | Where |
|---|---|---|
| A | An expired or revoked Gmail grant is treated as a transient error. Google's refusal (`400 invalid_grant`) becomes a generic `GoogleApiError`, is retried 3 times, and then tells the user "usually temporary, try again", which is the wrong advice. While the consent screen is in **Testing** mode, Google expires refresh tokens after **7 days** (§2), so this is expected **weekly**. | `gmail/service.py::get_valid_access_token`, `google_api.refresh_access_token`, `worker.py::_safe_error_message` |
| B | A network error (`httpx.TransportError`) on **one** message escapes the per-message `except GoogleApiError`, reaches the job-level handler and requeues the whole job with `attempts += 1`. Three network blips fail a long import permanently. | `google_api.py` (only non-2xx responses are wrapped), `worker.py::process_job` |
| C | A message whose fetch fails is skipped for good if it's older than the next incremental window: it's never written to `ProcessedMessage`. 37 of these on the real mailbox and 2 in production, with no status code logged, so the cause is unknown. | `worker.py::process_job` |
| D | `process_job` has no time budget. A job that outlives the workflow timeout, or a runner that dies, is left `running`, reaped 15 min later, and uses up an attempt. | Phase 8/9 known gap |
| E | No startup sweep: after a killed run, an orphaned `running` job waits the full 15-minute stale threshold. | Phase 5 known gap |
| F | No alerting. A stuck queue (for example an expired dispatch token), a stuck job, a failed job or a lapsed Gmail grant is only noticed by looking. | Phase 8 known gap |
| G | `logger.exception` in `process_job` prints full tracebacks to **public** GitHub Actions logs. SQLAlchemy exception text includes bound parameters, and the per-page pre-check query binds **raw Gmail message IDs**, which breaks Phase 9's log-privacy rule. | `worker.py`, `db/session.py` |
| H | The workflows that hold every `PROD_*` secret use actions pinned by mutable tag (`actions/checkout@v4`, `astral-sh/setup-uv@v10.1.0`). | `.github/workflows/*.yml` |
| I | No rate limiting. `GET /gmail/messages` makes up to 51 Gmail calls per request, and every Sync click on a queued job sends another GitHub dispatch. | spec §11 of Phase 8 |
| J | `/docs` and `/openapi.json` are public in production (verified: both return 200). The static site sends no `X-Frame-Options`, `Referrer-Policy` or CSP (verified: it sends only HSTS and `X-Content-Type-Options`). | `main.py`, Render static-site settings |

Known gaps in CLAUDE.md are handled like this: `_safe_error_message`'s two categories →
A; no time budget → D; no startup sweep → E; no monitoring → F; no rate limiting → I.
"Reaper runs every 2s" doesn't apply to production drains (the reaper runs once per
claimed job) and is dropped. The `_SUBDOMAIN_PREFIXES` classifier hazard is out of scope.

## 1. Goal & Non-Goals

### Goals

1. **Reauthorization is a clear, terminal state:** detected without retries, shown as
   "Reconnect Gmail", and fixed by a reconnect that **keeps** the sync watermark.
2. **Transient failures cost nothing permanent:** a network blip on one message
   neither fails the job nor silently loses the message.
3. **Bounded, resumable jobs:** no job runs longer than its lane's configurable slice.
   It checkpoints at a page boundary, requeues **without** using an attempt, and the
   run dispatches its own lane again. An orphaned job is recovered in minutes, not 15.
4. **Actionable alerts at $0:** a scheduled monitor workflow **fails** (and GitHub
   emails the owner) when a job is stuck, a job failed, or the dispatch token is about to
   expire.
5. **Security hardening proportional to a small single-instance app:** per-user rate
   limits on the expensive endpoints, a dispatch cooldown, API docs off in production,
   actions pinned to SHAs, conservative static-site security headers, and no raw IDs or
   SQL parameters in public logs.
6. **An evidence-based decision on the Google consent screen**, with no Google Cloud
   change made without explicit approval.

### Non-Goals

- New hosting, services or paid tiers (no Sentry, uptime pinger, Redis, extra Render
  service or Neon upgrade).
- Changes to the classifier, matching, trust model, review queue or manual CRUD.
- Server-side session revocation (rotating `SECRET_KEY` remains the kill switch).
- Gmail History API, a multi-instance API, or multi-worker lanes.
- Fixing `messages_seen` over-counting after a **crash** mid-page (cosmetic; slicing
  yields at page boundaries, so yields don't cause it).
- Full Google OAuth verification or a CASA security assessment (see §2).

## 2. Verified constraints (checked 2026-09-25)

**Google OAuth** (Google docs, fetched 2026-09-25)
- *"A Google Cloud Platform project with an OAuth consent screen configured for an
  external user type and a publishing status of 'Testing' is issued a refresh token
  expiring in 7 days, unless the only OAuth scopes requested are a subset of name, email
  address, and user profile."* This app requests `gmail.readonly`, so the rule applies.
  Weekly reconnects are expected until the publishing status changes.
- `https://www.googleapis.com/auth/gmail.readonly` is a **restricted** scope. Apps using
  restricted scopes need verification **and** an annual security assessment, **unless
  exempt**.
- Exemptions include *"Personal Use apps — if the app is for your personal use (fewer
  than 100 users)"*. Unverified apps are subject to the "unverified app" warning screen,
  which users can click through, and the 100-user cap.
- Moving to **Production** also **removes the test-user allowlist**, which today is the
  *only* thing restricting who can sign in (login requests only `openid email profile`,
  which triggers no warning screen). So the switch must be preceded by an **app-side
  allowlist** (§3.8).
- Not yet verified, and part of CP0: whether refresh tokens issued while in Testing
  keep their 7-day expiry after the switch (assume yes → one reconnect after
  switching); exactly what the console requires to publish (homepage, privacy-policy
  URL, authorized domain); whether Google shows or blocks anything beyond the unverified
  warning for this restricted scope.

**GitHub**
- *"events triggered by the `GITHUB_TOKEN` will not create a new workflow run, with the
  following exceptions: `workflow_dispatch` and `repository_dispatch` events always
  create workflow runs."* A lane can re-dispatch itself with its own `GITHUB_TOKEN`
  (`permissions: actions: write`), so the dispatch PAT never goes into GitHub.
- A workflow's `concurrency` group allows at most one running and one pending run; a new
  dispatch replaces the pending one. Re-dispatch during a run is therefore safe.
- Scheduled workflows are throttled (Phase 8: `*/5` produced ~11 runs a day) and are
  disabled after 60 days with no commit. This applies to the monitor too.
- A failed scheduled run emails the user who last changed the workflow's cron.

**Render / Neon**
- Static-site responses currently carry `strict-transport-security` and
  `x-content-type-options: nosniff` (Render/Cloudflare defaults). **Rewritten `/api/*`
  responses do not get static-site headers** (verified with `curl -I`).
- The API runs as a **single** uvicorn process (no `--workers`) on one instance, so
  in-memory rate-limit state is coherent. A restart resets it, which is acceptable.
- `DATABASE_URL` uses Neon's **pooled** host (PgBouncer, transaction mode), so
  session-level advisory locks can't be relied on.
- The worker (GitHub Actions) runs `main` **immediately** on push, while Render
  migrates in its build step. Code that uses a new column must not reach `main` before
  the migration has been applied (§7).

## 3. Architecture

### 3.1 Error classification (`app/gmail/google_api.py`)

- `GoogleApiError` gains `status_code: int | None` (`None` = network error).
- New `GmailAuthError(GoogleApiError)`: "the grant is no longer usable; only a
  reconnect fixes it". Raised when:
  - token refresh returns 400 with `error == "invalid_grant"`;
  - any Gmail API call returns **401** (the access token was just refreshed or is
    within its validity, so a 401 means revoked).
- Token refresh returning `invalid_client` / `unauthorized_client` stays a **plain**
  `GoogleApiError`: that's an operator misconfiguration (a rotated client secret), not
  the user's grant. It's retried and then fails, and the monitor alerts on it.
- Every `httpx.TransportError` (timeouts, connection errors) in `google_api` is wrapped
  as `GoogleApiError(status_code=None)`. The message text carries only the operation name
  and the status or error class, never a URL.
- `gmail/service.get_valid_access_token`: a `cryptography.fernet.InvalidToken` on
  decrypt (the encryption key was rotated) raises `GmailAuthError`, since a reconnect is
  the fix.

### 3.2 Worker behavior (`app/sync/worker.py`)

- **Per-message fetch:** on a `GoogleApiError` with `status_code` 429, ≥500 or `None`,
  retry **once** after `PER_MESSAGE_RETRY_SECONDS = 2`. On a second failure or any other
  4xx, skip as today (count in `failed_count`). The warning line logs the status or
  "network error" plus `message_ref(id)`. A `GmailAuthError` is **not** caught per
  message; it aborts the job (below).
- **Auth failure:** `process_job` catches `GmailAuthError` separately from the generic
  handler:
  - `job.status = "failed"`, `job.error_code = "gmail_reauth_required"`,
    `job.error_message = "Gmail access has expired or was revoked. Reconnect Gmail to
    keep syncing."`, `finished_at` set, `attempts` **unchanged** (no retry).
  - `connection.reauth_required_at = now`.
  - Rollback-before-touching-`job` ordering is kept, same as the existing handler.
- **Watermark** is still only advanced on `completed`, to the first claim's
  `started_at` date. Unchanged.

### 3.3 Reconnect flow

- `GmailConnection.reauth_required_at: datetime | None` (new column).
- `sync/service.enqueue_sync`: if `reauth_required_at` is set, raise
  `GmailReauthRequired` → **HTTP 403** `{"detail": "...Reconnect Gmail...", "code":
  "gmail_reauth_required"}`. 403 rather than 409, because the frontend already reads
  409's body as a `SyncJob`; an old frontend shows the 403's `detail` as a plain error, so
  a frontend/backend version mismatch is safe.
- `gmail/service.list_recent_messages` (the Phase 3 test view): a `GmailAuthError` sets
  the flag and returns the same 403.
- `GET /gmail/status` adds `needs_reconnect: bool`.
- `gmail/service.connect` (the existing OAuth callback path) clears
  `reauth_required_at`. It updates the existing row, so `last_synced_message_date`
  survives and the next sync is **incremental**. (Disconnect deletes the row and resets
  the watermark; the UI steers users to Reconnect instead.)
- `SyncJobRead` adds `error_code: str | None`.
- **Frontend:** `GmailPanel` shows a warning and a **Reconnect Gmail** button (link to
  `/api/v1/gmail/connect`) when `needs_reconnect` is true. `SyncPanel` shows the same
  link when a failed job has `error_code == "gmail_reauth_required"` or a Sync click
  gets a 403 with that code. `http.ts` gains a typed error that carries `status` and
  `code`.

### 3.4 Time-sliced jobs and checkpoint safety

**Configuration** (`core/config.py`, env-overridable, `gt=0`):
- `sync_initial_slice_seconds: int = 1500` (25 min)
- `sync_incremental_slice_seconds: int = 1200` (20 min, same as today's budget)
- `sync_orphan_threshold_seconds: int = 120` (§3.5)

These replace `LANE_DRAIN_BUDGET_SECONDS`. The lane workflows set them explicitly in
`env:`, so the value that governs production is visible in the workflow file, next to
`timeout-minutes`.

**`process_job(db, job, extractor=None, *, deadline: float | None = None) -> JobOutcome`**
(`"completed" | "failed" | "retrying" | "yielded"`):
- The deadline is checked **only** after a page's final commit (`job.page_token =
  next_page_token; db.commit()`). At that point every message on the page has its own
  committed `ProcessedMessage` row (or is counted as failed), and `page_token` points to
  the next unprocessed page. That's the safe checkpoint.
- If `time.monotonic() >= deadline` **and** `next_page_token is not None`: set
  `status = "queued"`, `next_attempt_at = now`, leave `attempts`, `started_at`,
  `page_token` and the counters unchanged, commit, return `"yielded"`.
- If the yield commit itself fails, the existing handler requeues with an attempt. That's
  safe because `page_token` is already committed.
- Overrun past the deadline is at most one page (≤100 messages; about 35–70s measured).
  Each slice processes at least one page, so a job always makes progress.

**`drain_once(max_runtime_seconds, job_type) -> DrainResult(processed, requeued)`**
- `deadline = start + max_runtime_seconds` is passed into every `process_job`.
- After a `"yielded"` outcome the drain **stops** (returns `requeued=True`) instead of
  claiming again. It doesn't re-claim its own yielded job in the same run.
- Other jobs keep the existing FIFO `created_at` order. A yielded job is usually the
  oldest, so it resumes first in the next run.

**Re-dispatch** (`app/sync/drain.py` + lane workflows):
- `drain.py` writes `requeued=true|false` to `$GITHUB_OUTPUT` when that env var exists
  (a no-op locally).
- Each lane workflow gains `permissions: actions: write` and a final step:
  `if: steps.drain.outputs.requeued == 'true'` → `gh workflow run sync-<lane>.yml
  --ref main` with `GH_TOKEN: ${{ github.token }}`. No inputs, no user-controlled values.
- **Failsafes if the re-dispatch fails:** the lane's cron fallback, the user's Sync
  click (`should_rekick` already re-dispatches a queued job), and the monitor's
  due-but-unclaimed alert (§3.6).

**Timeouts:** initial lane `timeout-minutes: 40` (was 120) = 25 min slice + one page +
setup, with margin. Incremental stays 30. `test_sync_worker_workflow.py` asserts
`timeout-minutes * 60 ≥ slice_seconds + 600` for both lanes, reading the slice from the
workflow's own `env:`.

**Frontend:** a `queued` job with `started_at` set is shown as "Syncing — N of M
(continuing…)", not "Starting sync…". The Retry hint stays as it is.

### 3.5 Orphan sweep at drain start

- New `sweep_orphans(db, job_type)` runs **once** at the start of each lane drain,
  before the first claim. It requeues (existing `_requeue_or_fail`: uses an attempt,
  backoff) every `running` job **in that lane** whose `updated_at` is older than
  `sync_orphan_threshold_seconds` (120s).
- **Why it's safe:** the lane's `concurrency` group means no other production run of
  that lane can be working a job when a drain starts. A healthy job commits after every
  message; the longest gap between commits is one token refresh plus one list call
  (≤10s timeout each) plus the new 2s retry, well under 120s.
- It is **not** run in `run_forever` (local dev, all lanes) or the in-process worker,
  where the lane-concurrency assumption doesn't hold. The regular 15-minute reaper is
  unchanged.
- Orphans still use an attempt, so a job that keeps crashing the process ends up
  `failed` rather than looping forever.

### 3.6 Monitoring (`app/sync/monitor.py` + `.github/workflows/sync-monitor.yml`)

`python -m app.sync.monitor` evaluates the conditions below, prints a counts-only
summary (also to `$GITHUB_STEP_SUMMARY`), and **exits 1 if any alert fires**. The failed
run is the alert (GitHub's email); no other channel.

| ID | Condition | Meaning / first action | Dedup |
|---|---|---|---|
| M1 | `status='queued' AND next_attempt_at < now − 15 min` | Due work nobody picked up: dispatch broken (PAT expired / 401 in Render logs), workflows disabled, or re-dispatch failed | Re-alerts each run while true |
| M2 | `status='running' AND updated_at < now − sync_stale_job_threshold_minutes` | Stuck job the reaper hasn't recovered | Re-alerts each run while true |
| M3 | `status='failed' AND alerted_at IS NULL` | A job failed permanently (includes `gmail_reauth_required` and operator-credential failures); the output shows counts by `error_code` | `alerted_at = now` is set after reporting, so it alerts **once** |
| M4 | `SYNC_DISPATCH_TOKEN_EXPIRES_ON − today ≤ 21 days` | Rotate the dispatch PAT (README procedure) | Re-alerts each run |

- **Public-output rule:** counts, condition IDs, `error_code` values and 8-character
  job-ID prefixes only. Never emails, user IDs, message IDs, subjects or companies. A
  test enforces this.
- The monitor exiting non-zero because the DB is unreachable is also an alert.
- **Workflow:** `on: schedule: "23 * * * *"` (hourly target; GitHub throttles it) +
  `workflow_dispatch`; `concurrency: sync-monitor`; `timeout-minutes: 5`;
  `permissions: contents: read`; env: `PROD_DATABASE_URL` and the other settings the
  app's `Settings` requires, plus `SYNC_DISPATCH_TOKEN_EXPIRES_ON: "2027-09-24"` (a date,
  not a secret).
- **Neon cost:** each run wakes the compute briefly. The throttled hourly target is a
  handful of wakes a day. Checked on Neon's usage page after a week (acceptance §6).
- The migration backfills `alerted_at = now()` for existing `failed` rows, so the first
  run doesn't alert on history.

### 3.7 Rate limiting and dispatch cooldown

- `app/core/ratelimit.py`: a small thread-safe, in-memory fixed-window counter
  (`dict[(bucket, key, window_index)] → count`, pruned on access), used through a FastAPI
  dependency factory `rate_limit(bucket, limit, window_seconds, key=...)`. No new
  dependency. Exceeding a limit → **429** with `Retry-After` and `{"detail": "Too many
  requests — try again in N seconds."}`.

| Endpoint | Key | Limit |
|---|---|---|
| `POST /gmail/sync` | user id | 10 / 60s |
| `GET /gmail/messages` | user id | 5 / 60s |
| `GET /gmail/connect` | user id | 5 / 60s |
| `GET /auth/google/login` | client IP (best-effort) | 20 / 60s |

- The polled endpoints (`GET /gmail/sync/{id}`, `/sync/latest`, `/gmail/status`,
  `/auth/me`), the review queue and application CRUD are **not** limited. `SyncPanel`'s
  polling is unaffected.
- The IP key is best-effort: uvicorn runs with `--forwarded-allow-ips='*'` behind
  Render/Cloudflare, so `X-Forwarded-For` can be spoofed. Documented; it limits only
  casual hammering.
- **Dispatch cooldown:** the 409 re-kick path dispatches at most once per job per 30s
  (in-memory `job_id → last_dispatch_monotonic`). The 409 response is unchanged.
- Documented assumption: a single uvicorn process. Adding `--workers N` would multiply
  the effective limits.

### 3.8 Other security hardening

- **API docs off in production:** `FastAPI(docs_url=None, redoc_url=None,
  openapi_url=None)` when `settings.env == "production"` (Render sets `ENV=production`).
- **Public-log hygiene:** `create_engine(..., hide_parameters=True)`, so SQLAlchemy
  exception text never includes bound values anywhere (Render or GitHub logs).
  Tracebacks otherwise stay, since they're needed for debugging.
- **Actions pinned to full commit SHAs**, with a `# vX.Y.Z` comment, in all four
  workflows. `test_sync_worker_workflow.py` asserts every `uses:` is SHA-pinned.
- **App-side sign-in allowlist, only if CP7 is approved:** `AUTH_ALLOWED_EMAILS`
  (comma-separated). When set, `google_callback` refuses any other verified Google email
  (redirect to the frontend with no cookie). When unset, behavior is unchanged. Must be
  deployed and verified **before** the consent screen leaves Testing.

### 3.9 Static-site security headers (Render dashboard → Static Site → Headers, path `/*`)

What the deployed page actually loads (verified from `dist/index.html`, source and the
live response): same-origin JS and CSS bundles, `/favicon.svg`, the user's avatar from
`picture_url` (Google `*.googleusercontent.com`), and `fetch` to same-origin `/api/*`.
There are no inline scripts, no inline `style` attributes in markup (React sets styles
through the CSSOM, which `style-src` doesn't restrict), no web fonts and no iframes.
Google OAuth is a **top-level navigation** (link → `/api/.../login` → 302 to
`accounts.google.com` → 302 back), which CSP's `connect-src`/`default-src` don't govern,
and there are no form submissions to other origins.

| Header | Value |
|---|---|
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` (the browser default made explicit; not `no-referrer`-level strict in a way that could change the avatar or OAuth behavior) |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=(), usb=()` |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://*.googleusercontent.com; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'` |

Deliberately **omitted**: `form-action` (it gains little here and some browsers apply
it to redirect chains, which could affect the OAuth hop), `upgrade-insecure-requests`
(everything is already HTTPS), and HSTS (already sent).

**Rollout:**
1. Add the CSP as **`Content-Security-Policy-Report-Only`**; the other three are
   enforced right away (they can't affect OAuth).
2. Walk through every flow with DevTools open: login, logout, avatar, Gmail
   connect/reconnect, Sync (all states), review queue approve/edit/reject, application
   create/edit/delete, a hard refresh. Zero CSP reports is the requirement.
3. Switch the header name to enforcing `Content-Security-Policy`, then repeat the walk.
4. **Rollback:** remove the header in the dashboard (it takes effect on the next CDN
   fetch; `s-maxage=300`, so at most about 5 min).

The headers don't reach rewritten `/api/*` responses (JSON and redirects). That's
acceptable, and noted.

### 3.10 Google consent-screen investigation (CP0, read-only)

Produces a short findings section appended to this spec, then **stops for approval**:

1. Record the current console state (user type, publishing status, test users,
   configured scopes, authorized domains, homepage/privacy links). This means the
   maintainer shares screenshots, or Claude walks through them. **No edits.**
2. Confirm from current Google docs: the requirements to set the status to "In
   production" while unverified, the exact user-facing warning for `gmail.readonly`,
   whether the personal-use exemption applies (it's expected to, at fewer than 100
   users), and what happens to refresh tokens issued during Testing.
3. **Options memo:**
   (a) stay in Testing (weekly reconnect, made painless by CP2);
   (b) go to Production **unverified**, personal use: no 7-day expiry, a warning screen
   on Connect Gmail, and the test-user allowlist lost, so CP7's app allowlist ships
   first;
   (c) full verification + annual CASA assessment (disproportionate; recorded for
   completeness).
   Recommendation expected: (b), gated on approval.
4. If (b) is approved, it runs as **CP7**. Otherwise CP7 is dropped and (a) is
   documented as the accepted state.

## 4. Data model — migration `0007`

One additive migration, deployed on its own before any code that uses it:
- `gmail_connections.reauth_required_at TIMESTAMPTZ NULL`
- `sync_jobs.error_code VARCHAR(40) NULL`
- `sync_jobs.alerted_at TIMESTAMPTZ NULL`
- Data step: `UPDATE sync_jobs SET alerted_at = now() WHERE status = 'failed'`.
- Downgrade drops the three columns.

Nullable columns only; no rewrite of existing rows beyond the one backfill. Old code
keeps working against the new schema, which is what makes deploying the migration
first safe.

## 5. Failure modes after Phase 10

| Failure | Result |
|---|---|
| Gmail grant expired or revoked (weekly in Testing) | Job `failed`, `error_code=gmail_reauth_required`, no retries; Reconnect shown; the next sync is incremental; M3 alerts once |
| Encryption key rotated | Same as above (`InvalidToken` → reauth) |
| Client secret wrong or rotated badly | Retried, then `failed` with the generic message; M3 alerts |
| Network blip on one message | Retried once; if still failing, skipped with the status logged; no attempt used |
| Network blip / 5xx on a page list | Existing retry/backoff (uses an attempt), resumes from `page_token` |
| Neon connection drop mid-job | Existing handler: rollback, requeue with an attempt, resume from the checkpoint |
| Initial import longer than one slice | Yields at a page boundary, no attempt used, the lane re-dispatches itself |
| Re-dispatch fails | Cron fallback / Sync click re-kick; M1 alerts after 15 min |
| Runner killed / workflow timeout / cancelled | Next drain in the lane sweeps the orphan after 120s (attempt used) |
| Dispatch PAT expired | Jobs wait for cron; M1 alerts; M4 warned 21 days earlier |
| Monitor can't reach the DB | Monitor run fails → email |
| Sync button hammered | 429 after 10/min; dispatch at most once per 30s per job |

## 6. Acceptance criteria

**Preserved behavior**
- P1. 3 production incremental syncs (warm backend), click → "Synced": median ≤ 90s,
  measured after CP3 and again after CP5.
- P2. The backend suite is green (344 + new tests). `evaluation.compare` output is
  identical to before Phase 10. `tsc -b` is clean and `oxlint` has no new warnings.
- P3. No change under `app/classifier/`, `app/pipeline/matching.py` or
  `app/applications/`, or to the pipeline decision logic (checked with `git diff
  --stat` at the end of the phase).

**Reauthorization (CP2)**
- R1. Tests: refresh `invalid_grant`, a Gmail 401, and `InvalidToken` each → job
  `failed`, `error_code=gmail_reauth_required`, `attempts == 0`,
  `connection.reauth_required_at` set. `invalid_client` → generic path.
- R2. Production drill: remove the app's access in the Google Account settings, click
  Sync → the job fails in one attempt within ~1 min, the UI shows Reconnect, and
  `POST /gmail/sync` returns 403 `gmail_reauth_required` until reconnected.
- R3. After Reconnect: `needs_reconnect=false`, the next job is `incremental` with
  `window_start` = old watermark − 1 day.

**Transient errors & log privacy (CP1)**
- T1. A test injects a network error and a 503 on one message: the job completes,
  `attempts == 0`, the message is retried once, and it's processed when the retry
  succeeds.
- T2. A test forces a SQLAlchemy error in the pre-check query: the captured log output
  contains no raw message ID.
- T3. Scanning the public logs of every Phase 10 production run finds no 16-hex
  message IDs and no `gmail.googleapis.com/.../messages` URLs.

**Slicing & recovery (CP3)**
- S1. Tests: a deadline mid-job → `yielded`, `status=queued`, `attempts` and
  `started_at` unchanged, `page_token` = next page; no yield on the last page; the drain
  stops after a yield and reports `requeued=True`.
- S2. A slice-value change via env is honored (test), and the workflow guard test
  enforces the timeout margin and SHA pins.
- S3. Production drill: an initial import run with `SYNC_INITIAL_SLICE_SECONDS=120`
  (a temporary commit to the lane workflow's `env:`, reverted right after; workflows
  still take no dispatch inputs) → ≥2 dispatched runs, the job `completed`,
  `attempts == 0`, zero duplicate `(user_id, gmail_message_id)` rows, watermark = the
  first claim's `started_at` date.
- S4. Production drill: cancel an initial-lane run mid-job, then dispatch the lane →
  the job is re-claimed within 3 min of the dispatch (not 15).

**Monitoring (CP4)**
- M-a. A test per condition M1–M4 → exit 1. The healthy state → exit 0. M3 alerts once
  (the second run exits 0). The output contains no emails or UUIDs beyond 8-char
  prefixes.
- M-b. Production drill: break `SYNC_DISPATCH_REPOSITORY`, click Sync, wait more than
  15 min, dispatch the monitor → failed run + email received. Restore; the next monitor
  run is green.
- M-c. Over 7 days after CP4: no false-positive monitor failures; Neon's usage page
  shows no meaningful increase in compute hours (reported with numbers).

**Security (CP5)**
- X1. The 11th `POST /gmail/sync` within 60s → 429 with `Retry-After`. Sync polling
  across a full initial import never hits a 429.
- X2. Production `/docs`, `/redoc` and `/openapi.json` → 404. Local dev still serves
  them.
- X3. Every `uses:` in `.github/workflows/*.yml` is pinned to a 40-char SHA (guard
  test).
- X4. `curl -I` on the static site shows `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy` and the enforcing `Content-Security-Policy`. The full UI walk
  (§3.9) shows zero CSP violations in the console, and login, Gmail connect and sync
  all work.

**Consent screen (CP0/CP7)**
- G1. A findings section appended to this spec, with sources and a recommendation.
  **No Google Cloud change made in CP0.**
- G2. (Only if CP7 is approved.) A non-allowlisted Google account can't obtain a
  session. After the switch and one reconnect, a sync ≥8 days later succeeds without
  reauthorization.

## 7. Deployment risks & mitigations

1. **Migration vs worker ordering (highest).** The worker runs `main` immediately;
   Render migrates in its build. → CP2a pushes the migration **alone**. Wait for Render's
   deploy to go live and `/health/ready` 200, then confirm the columns exist. Only then
   push CP2b. No code before CP2b references the new columns.
2. **Wider workflow permissions** (`actions: write` in secret-bearing workflows). →
   Only `main` code runs; the re-dispatch step takes no inputs; the SHA pinning in CP5
   narrows supply-chain risk. CP5 could be done before CP3 if preferred.
3. **Changing timeouts while a run is active.** → Push CP3's workflow changes only when
   no initial-lane run is in progress (`gh run list`).
4. **Frontend/backend skew.** → New fields are optional in the TypeScript types, and
   the 403 reauth response is readable by the old frontend.
5. **CSP breaking the app.** → Report-Only first, the full UI walk, then enforce; a
   one-click rollback.
6. **Alert fatigue.** → Thresholds sit far above normal timings (15 min vs a ~1-min
   sync); M3 deduplicates; the 7-day false-positive check (M-c).
7. **Direct pushes to `main`, no CI gate.** → Full local checks (`uv run pytest`,
   `evaluation.compare`, `tsc -b`, `oxlint`, `npm run build`) before every push; watch CI
   after each push.
8. **Consent-screen switch opens sign-in to any Google account.** → CP7 ships the app
   allowlist first, and the switch only happens after explicit approval.

## 8. Implementation checkpoints

Each checkpoint: TDD → full local checks → commit → push to `main` → CI green →
production verification listed → the next checkpoint. Render dashboard changes happen
only in the checkpoint that names them; **Google Cloud changes only after explicit
approval in CP7.**

| CP | Content | Production verification | Criteria |
|---|---|---|---|
| **CP0** | Google consent-screen investigation (read-only); findings appended here. **Stop for decision.** Can run alongside CP1. | — | G1 |
| **CP1** | §3.1 error classification (types only; the worker's auth handling comes in CP2b), network-error wrapping, per-message single retry + status logging, `hide_parameters=True` | A normal incremental sync; public log scan | T1–T3, P1 (spot) |
| **CP2a** | Migration 0007 only | Render deploy live, `alembic current` = 0007 against prod, columns present | — |
| **CP2b** | §3.2 auth handling, §3.3 reconnect flow (backend + frontend) | Revoke/reconnect drill | R1–R3 |
| **CP3** | §3.4 slicing (configurable), §3.5 orphan sweep, lane workflow changes (`actions: write`, re-dispatch step, timeouts, slice env), frontend "continuing" state | Short-slice drill; cancel drill; 3 incremental timings | S1–S4, P1 |
| **CP4** | §3.6 monitor module + workflow | Broken-dispatch drill; start the 7-day observation | M-a, M-b (M-c at the end of the phase) |
| **CP5** | §3.7 rate limiting + cooldown; §3.8 docs off, SHA pins; §3.9 headers (Render dashboard: Report-Only → walk → enforce) | 429 check; `/docs` 404; header `curl`; full UI walk; 3 incremental timings | X1–X4, P1 |
| **CP6** | Failure-injection matrix (§9); CLAUDE.md "Phase 10 results", README alert runbook (what each M-alert means and what to do), known-gaps update | — | P2, P3, M-c |
| **CP7** *(conditional)* | `AUTH_ALLOWED_EMAILS` allowlist → deploy → verify → **stop for approval** → maintainer changes the consent screen → one reconnect | Non-allowlisted login refused; sync after ≥8 days | G2 |

## 9. Testing plan (failure injection)

All tests are offline: they mock `google_api`/`httpx` and use the real test Postgres
database.

- **Per-commit-point DB failure matrix** (`test_sync_failure_injection.py`,
  parametrized): inject a real Postgres error (`SELECT 1/0`, the Phase 5 technique) at
  each of the counter commit after list, the pre-check query, inside `process_message`,
  the `page_token` commit, the yield commit, the success tail, and the requeue handler
  itself. Assert: `process_job` doesn't raise; the job ends `queued` (attempt used) or is
  recoverable by the sweep/reaper; no duplicate `ProcessedMessage`; a resume continues
  from the last committed `page_token`.
- **Neon-style drop:** raise `sqlalchemy.exc.OperationalError` mid-page → requeue →
  resume.
- **Worker interruption:** simulate a killed run (claim, commit part of a page, abandon
  the session with the job `running`, age `updated_at`) → `sweep_orphans` requeues →
  the next drain completes it with exactly one `ProcessedMessage` per message.
- **Token expiry and revocation:** access-token expiry → refresh path (existing);
  refresh `invalid_grant`; Gmail 401 on list and on get; `InvalidToken` on decrypt;
  `invalid_client`.
- **Retries:** 429/503/network error on list (job-level backoff); on get (single
  in-place retry); retry-within-drain still waits (existing).
- **Slicing:** a deadline in the middle, at the last page, and a zero budget (one page
  still processed); the drain stops after a yield; `GITHUB_OUTPUT` written or absent.
- **Monitor:** M1–M4 true/false, M3 dedup, output privacy, DB-unreachable → non-zero.
- **Rate limit:** window rollover, per-user isolation, 429 + `Retry-After`, unlimited
  polling endpoints, dispatch cooldown.
- **Workflow guards:** SHA pins, `actions: write` only on the lane workflows, timeout vs
  slice margin, the monitor workflow's permissions are `contents: read`.

## 10. What is preserved

The Phase 9 dispatch path (`POST /gmail/sync` → background `request_worker`) and lane
split; the queue, partial unique index, `FOR UPDATE SKIP LOCKED` claiming, backoff
formula, 15-minute reaper and idempotency; the classifier, matching, trust model,
review queue and manual CRUD; the $0 hosting footprint.

## 11. CP0 findings — Google consent screen (2026-09-25)

Read-only. Nothing in Google Cloud was changed.

### 11.1 Current console state (recorded by the maintainer)

| Setting | Value |
|---|---|
| Publishing status | **Testing** |
| User type | **External** |
| OAuth user cap | 1 user (1 test, 0 other) of **100**, counted over the app's whole lifetime |
| Test users | 1 (the maintainer) |
| "Publish app" | **Disabled**: *"To publish your app, you must complete your configuration on the Branding page."* |
| Branding: set | App name "Job Tracker Dev", user support email, developer contact email, authorized domains `job-tracker-lds1.onrender.com` and `job-tracker-1-ldy2.onrender.com` |
| Branding: empty | App logo, **application home page**, **privacy policy link**, **terms of service link** |

### 11.2 What Google's current docs say (fetched 2026-09-25)

- **Testing-mode refresh tokens:** *"issued a refresh token expiring in 7 days, unless the
  only OAuth scopes requested are a subset of name, email address, and user profile."*
  This app requests `gmail.readonly`, so it's the cause of the weekly reconnects.
- **Other reasons a refresh token stops working** (they apply in any publishing status,
  and CP2 handles all of them the same way): *"The user has revoked your app's access"*;
  *"has not been used for six months"*; *"The user changed passwords and the refresh token
  contains Gmail scopes"*; the per-account limit on live refresh tokens.
- `gmail.readonly` is a **restricted** scope. Restricted scopes normally need
  verification **and** an annual security assessment.
- **Exemption:** *"Personal Use apps — If the app is for your personal use (fewer than 100
  users)."* Unverified apps show the "unverified app" screen, and users can click through
  it; the cap is *"100 new users in total"*.
- **Branding links:** *"These links are required for all external production apps."*
  (home page, privacy policy, terms of service). *"The Privacy Policy should be hosted
  within the domain that hosts your homepage"*; the home page *"must be hosted on a
  verified domain you own."*
- **Authorized domains:** `onrender.com` **is on the Public Suffix List**, so each Render
  subdomain is its own "top private domain". That's why the two app subdomains are
  accepted, and a subdomain could be verified in Google Search Console.
- **Not documented:** what happens to refresh tokens issued during Testing when the
  status changes. Assume they keep their 7-day expiry, so plan on one reconnect after
  publishing.

### 11.3 Options

| | (a) Stay in Testing | (b) Publish, unverified, personal use | (c) Full verification |
|---|---|---|---|
| Weekly reconnect | Yes: one click, keeps history (CP2); monitor emails when it happens (CP4) | No (still needed after revocation, password change or 6 months unused) | No |
| Who can sign in | Listed test users only (Google enforces it) | **Any Google account**, so the CP7 app allowlist is mandatory first | Any Google account |
| Warning screen | Testing notice | "Google hasn't verified this app" on Connect Gmail | None |
| New work beyond the approved scope | None | **Privacy-policy and terms pages** on the frontend domain, a **home page** link, possibly **Search Console domain verification** for `job-tracker-1-ldy2.onrender.com`, the CP7 allowlist, and Google Cloud changes | All of (b) + a submission and an **annual third-party security assessment (CASA)** |
| Cost | $0 | $0 (hours of work, plus a policy text to maintain) | Assessment fees, disproportionate for a personal project |
| Risk | None new | Google could still refuse or limit a restricted scope; the allowlist becomes the only sign-in gate | Review and assessment effort |

### 11.4 Recommendation

**(a) for Phase 10, with (b) recorded as a scoped future option.** The approved
Phase 10 scope assumed (b) needed only an app-side allowlist. The console shows that
publishing is **blocked until privacy-policy, terms and home-page links exist on a domain
we control**, which means new frontend pages and possibly domain verification. That's
product/legal content, outside a hardening phase. CP2 already reduces (a)'s cost to one
Reconnect click a week, with no loss of sync history, and CP4 emails the maintainer when
it's needed.

If (b) is chosen instead, CP7 grows to: the app allowlist → privacy-policy and terms
pages on the static site → Branding links filled in → (if the console demands it) Search
Console verification of `job-tracker-1-ldy2.onrender.com` → **stop for approval** →
Publish → one reconnect → a sync after 8 or more days succeeds without reauthorization.

### 11.5 Decision (2026-09-25)

**Option (a): stay in Google OAuth Testing mode for Phase 10.** Publishing,
privacy/terms pages, domain verification and full verification are out of scope.
CP2's reconnect handling and CP4's failure alert stay as planned.

**CP7 is dropped.** Its only content was the `AUTH_ALLOWED_EMAILS` sign-in allowlist.
In Testing mode Google already refuses sign-in to anyone who isn't a listed test user,
before our callback runs, so the allowlist would add only defense-in-depth against a
future configuration change. Criterion G2 no longer applies; G1 is met by this section.

**Future improvement: publish the OAuth app (unverified, personal use).** Requirements
found in CP0, in order:
1. An app-side sign-in allowlist (`AUTH_ALLOWED_EMAILS`, as designed for the former
   CP7), deployed and verified first. Publishing removes Google's test-user gate.
2. A home page, a privacy policy and terms of service, hosted on the app's own domain
   (`job-tracker-1-ldy2.onrender.com`; `onrender.com` is on the Public Suffix List). The
   privacy policy on the home page and on the consent screen must be the same.
3. The Branding page's home page, privacy policy and terms links filled in (this
   un-disables "Publish app").
4. If the console requires it: verify `job-tracker-1-ldy2.onrender.com` in Google Search
   Console.
5. Publish; reconnect Gmail once (Testing-issued tokens may keep their 7-day expiry);
   confirm that a sync 8 or more days later succeeds without reauthorization.
6. Stay under the 100-user lifetime cap; users will see the "unverified app" screen.
   Anything beyond personal use means full verification plus an annual CASA assessment.
