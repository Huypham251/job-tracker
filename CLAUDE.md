# Job Application Tracker — Project Notes

Read this before starting a new phase. It's a snapshot of where the project stands, not
a spec — the specs under `docs/superpowers/specs/` and plans under
`docs/superpowers/plans/` remain the source of truth for what was decided and why.

## Status

Phases 1–9 are complete and merged to `main`, and the app is **deployed to production**
(Render + Neon + GitHub Actions, $0/month — see "Phase 8 results" and "Phase 9 results"
below; production paths verified end-to-end 2026-09-24). Phase 5 was manually verified end-to-end
against a real Gmail account (2026-09-17) — see "Manual testing findings" below — and
Phase 6 was manually verified the same way (2026-09-20) — see "Phase 6 manual testing
findings" below. Backend: 344/344 tests passing (281 after Phase 7; Phase 8 added
health/heartbeat, in-process-worker, drain-entrypoint, workflow-config and
key-rotation tests; Phase 9 added lane, retry-wait, dispatch, re-kick, single-fetch
and log-privacy tests).
Frontend:
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
- Phase 6: classifier/extraction quality — larger, category-labeled evaluation
  dataset (88 examples); preprocessing (greeting/signature stripping, punctuation
  normalization); extraction fixes (greeting-bleed trim, broadened templates,
  subdomain/assessment-platform gaps); status-pattern coverage; measured confidence
  recalibration. See "Phase 6 results" below.
- Phase 7: three targeted classifier extraction fixes (a fourth gap — the meetup false
  positive — was already partly addressed by the same negative-pattern mechanism as the
  newsletter fix, so it shipped as part of that same commit rather than a separate one)
  found during Phase 6 manual testing (apex-domain company resolution for a
  leading-ATS-subdomain sender, pipe-delimited/longer position titles, newsletter/meetup
  false positives), plus three new evaluation-dataset examples covering the gaps. A
  final whole-branch review then found and fixed two further issues the per-task
  reviews had missed — see "Phase 7 results" below.
- Phase 8: production deployment + CI/CD — Render Static Site (frontend) + Render free
  Web Service (API) + Neon Postgres, a GitHub Actions CI gate on `main`, and the sync
  worker running as a **scheduled GitHub Actions workflow** (not in-process, not a
  Render service — see "Phase 8 results" for why the spec's original in-process design
  was abandoned). Six real bugs/constraints found only during live deployment were
  fixed along the way.
- Phase 9: on-demand sync — a Sync click now starts the worker within seconds
  (the API calls GitHub's `workflow_dispatch` right after committing the job, with
  cron kept only as a fallback), and initial imports and incremental syncs run in
  **separate lanes** (`sync-initial.yml` / `sync-incremental.yml`). Also: retries are
  waited for within the same run, each Gmail message is fetched once instead of
  twice, message IDs are kept out of the public worker logs, and the Sync panel
  shows "Starting…" vs "Syncing…" with a Retry. See "Phase 9 results".

**Read `2026-09-15-job-tracker-phase-4-pipeline-design.md` for the pipeline architecture
that's still current (matching, trust model, DB schema, `/pipeline/review*` API,
frontend), `2026-09-15-job-tracker-phase-4b-local-classifier-design.md` for how
classification actually works, and `2026-09-16-job-tracker-phase-5-gmail-sync-design.md`
for the sync job queue/worker.** All three specs carry supersession banners pointing at
whichever of the others changed their original decisions. For production,
`2026-09-21-job-tracker-phase-8-production-deployment-design.md` covers hosting,
secrets, OAuth and CI — but its §3.3 in-process worker design was superseded during
deployment (banner at its top); "Phase 8 results" below is the current truth for
hosting. **For how syncs are triggered and run today, read
`2026-09-24-job-tracker-phase-9-on-demand-sync-design.md` and "Phase 9 results".**

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
│   ├── preprocess.py    Phase 6 — greeting/signature stripping, punctuation
│   │                       normalization, applied before scoring/extraction
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
│   │                            on an already-active job), get_job, get_latest_job,
│   │                            should_rekick (Phase 9)
│   ├── router.py               /api/v1/gmail/sync* (POST /sync, GET /sync/{id},
│   │                            GET /sync/latest); POST /sync schedules a
│   │                            best-effort dispatch as a background task (Phase 9)
│   ├── dispatch.py              Phase 9 — request_worker(job_type): POST GitHub
│   │                            workflow_dispatch for sync-<job_type>.yml; never raises
│   ├── worker.py                 claim_next_job / reap_stale_jobs (FOR UPDATE SKIP
│   │                               LOCKED; optional job_type lane filter), process_job
│   │                               (pagination + retry/backoff), _run_one_tick (shared
│   │                               reap+claim+process), run_forever (local dev, all
│   │                               lanes: `uv run python -m app.sync.worker`),
│   │                               drain_once (drain one lane until idle or its budget;
│   │                               waits for near-term retries), LANE_DRAIN_BUDGET_SECONDS
│   ├── drain.py                    production entrypoint,
│   │                               `python -m app.sync.drain --lane <lane>`, run by the
│   │                               lane workflows; silences httpx request logging
│   └── inprocess.py                Phase 8 — optional worker thread inside the API
│                                   (RUN_WORKER_IN_PROCESS); OFF in production, see
│                                   "Phase 8 results"
├── main.py           /health (liveness), /health/ready (DB ping), /health/worker
│                     (in-process heartbeat — always 503 in production, by design now)
├── db/session.py     engine with pool_pre_ping=True (Phase 8, for Neon)
├── core/privacy.py   Phase 9 — message_ref(id): hashed stand-in for Gmail message IDs in logs
└── core/config.py    Settings — gmail_sync_backfill_days, sync_stale_job_threshold_minutes,
                      run_worker_in_process, sync_dispatch_token/_repository/_ref

.github/workflows/
├── ci.yml            Phase 8 — backend pytest + evaluation.compare; frontend tsc/oxlint/
│                     build. Required checks ("backend", "frontend") on `main`
├── sync-incremental.yml  Phase 9 — incremental lane: workflow_dispatch (from the API)
│                         + cron */15 fallback, concurrency "sync-incremental",
│                         timeout 30 min, drain budget 20 min
└── sync-initial.yml      Phase 9 — initial-import lane: same triggers, concurrency
                          "sync-initial", timeout 120 min, budget 100 min. Both use the
                          PROD_* repo secrets and a read-only GITHUB_TOKEN; they
                          replaced Phase 8's single sync-worker.yml

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
  discussion before trusting it. **Production runs at most one worker per lane**
  (Phase 9): each lane workflow's `concurrency` group serializes its drains, and the
  two lanes claim and reap **disjoint row sets** (`job_type`), so no two processes
  ever compete for the same rows. That's covered by a real two-connection test
  (`test_two_lanes_claim_concurrently_without_blocking_or_stealing`) but **was not
  verified in production with two lanes running at once** (it would need a second
  Gmail account doing a real initial import; deliberately not set up).
  `RUN_WORKER_IN_PROCESS` stays `false` on Render — turning it on would add an
  all-lanes worker inside the API (and reintroduce the OOM problem, see
  "Phase 8 results").
- **The old Phase 4 "ReviewQueue only fetches once on mount" staleness gap is fixed.**
  (Noted here only so a search for it in old notes doesn't mislead — `ReviewQueue`'s
  `refreshSignal` prop, bumped by `ApplicationsPage` on sync completion/failure, closes
  it.)
- **Sync duration is bounded by date window, not message count — confirmed real, not
  just theoretical.** See "Manual testing findings" below: a real mailbox's initial sync
  took 1h43m. Noted as a recommendation in Phase 5's final review, not fixed.
- **Compound-subdomain ATS platforms still produce a wrong domain-derived company.**
  `ATS_DOMAINS` (`classifier/patterns.py`) blocks a platform when the sender's domain
  *is* the platform (e.g. `hackerrank.com` — and, since Phase 6 manual testing,
  `hirevue.com`, added after it was found extracting company="Hirevue" for a real "
  Interview with Nike, Inc." email). It does not help when the platform name is a
  *subdomain* of the real employer's own domain instead — a real Verisk rejection email
  from `TalentAcquisition@oraclecloud.verisk.com` extracted company="Oraclecloud" (the
  ATS subdomain) instead of "Verisk" (the actual apex domain). `extract_sender_domain`
  (`classifier/text.py`) only strips a fixed list of generic prefixes
  (`mail.`/`careers.`/`talent.`/etc.); `oraclecloud.` isn't one, and adding it wouldn't
  generalize (unlike `careers.`/`talent.`, "oraclecloud" is a specific vendor name, not
  a generic email-infra label). Not fixed — a general fix (e.g., preferring the label
  before the TLD when the domain has 3+ labels) needs verification against real
  multi-label domains that are legitimately the whole company name (not just
  subdomain+company) before it's safe to add. **This specific evidenced shape
  (`oraclecloud.<company>.com`) is now fixed in Phase 7** — see "Phase 7 results" below;
  the broader multi-label-domain question above is still open, and a wider, unrelated
  hazard in the same prefix-stripping mechanism is newly flagged below.
- **Position extraction can't capture a title containing `|`.** `_POSITION_TOKEN`
  (`classifier/fields.py`) allows `[\w&'/+\-]` per word — no pipe — so a real title like
  "Tech Intern | 2027 Summer Internship Program" (from the same Verisk email above)
  fails to extract regardless of the subdomain issue; that title is also 7 words, over
  the 6-word cap, so widening the character class alone wouldn't be enough. Found during
  Phase 6 manual testing, not fixed. **Fixed in Phase 7** (pipe added to the allowed
  characters, cap raised to 8 words, and — found only during Phase 7's own final
  whole-branch review — a capitalized-first-word guard added to stop the wider cap from
  reopening the overcapture bug it otherwise would have) — see "Phase 7 results" below.
- **`_SUBDOMAIN_PREFIXES`'s prefix-stripping has no guard that the remainder is still a
  plausible domain.** `extract_sender_domain` (`classifier/text.py`) strips a matched
  prefix (`mail.`, `jobs.`, `e.`, `oraclecloud.`, etc.) unconditionally, with no check
  that what's left still looks like a real domain. A sender at exactly
  `noreply@oraclecloud.com` (bare, no further subdomain — as opposed to the evidenced
  `oraclecloud.<company>.com` shape Phase 7 fixed) strips down to `"com"`, producing
  company="Com". This hazard class predates Phase 7 (the same thing happens today for
  `mail.`/`jobs.`/`e.` against a sender at exactly `mail.com`/`jobs.com`/`e.com`) —
  Phase 7's `oraclecloud.` addition widens the class of triggering inputs but did not
  introduce the underlying bug. Not fixed here — a proper fix means auditing every
  `_SUBDOMAIN_PREFIXES` entry together (e.g. requiring at least one more label to
  remain after stripping), which is broader than this branch's narrowly-evidenced,
  single-sender-shape scope.

## Manual testing findings (2026-09-17)

Everything above was validated against a real Gmail account (~6,500 messages in the
180-day window), not just the automated suite. Two setup/operational issues surfaced —
neither is a design flaw, but a fresh session should know about them:

- **The worker crashed on startup** the first time it was ever run standalone
  (`python -m app.sync.worker`), with `sqlalchemy.exc.InvalidRequestError: ...
  expression 'User' failed to locate a name`. `GmailConnection.owner` and
  `Application.owner` reference `User` as a string relationship, resolved lazily by
  SQLAlchemy's class registry the first time any mapper configures — which only works
  if `app.users.models` was already imported by then. Neither `gmail/models.py` nor
  `applications/models.py` imports it at runtime (only under `TYPE_CHECKING`), and
  nothing else in the worker's import graph did either. This never surfaced via the
  FastAPI app (`main.py` imports `auth_router`, which imports `app.users.models` early)
  or via pytest (`conftest.py` explicitly imports every model module first) — the
  worker run standalone is the one path that does neither, and it had simply never been
  run standalone before this test session. **Fixed**: `worker.py` now explicitly
  imports `app.users.models.User`. If a future refactor makes that import look
  "unused" and removes it, this will silently come back — it's guarded by
  `tests/test_sync_worker_entrypoint.py`, a subprocess-based regression test (a
  genuinely fresh Python process is required to reproduce this; an in-process test
  would be masked by whatever else already ran first in the same pytest session).
- **The dev database's migrations were behind.** `alembic upgrade head` had never been
  run locally after Phase 5's migration (`0006`, adding `sync_jobs`) merged, so the
  worker failed with `UndefinedTable: relation "sync_jobs" does not exist` until it was
  applied. Not a bug — just a reminder: **after pulling a phase that adds a migration,
  run `cd backend && uv run alembic upgrade head` before starting the API or the
  worker**, the API server can mask this longer than you'd expect since most of its
  endpoints don't touch the new tables.

What real-mailbox testing confirmed working, with concrete numbers: initial sync
correctly paginated (6,533 messages seen, far past the old 20-message cap); incremental
sync correctly bounded to the watermark window (52 seen, only 2 genuinely new, the rest
correctly recognized as already-processed); zero duplicate `gmail_message_id` rows
anywhere across ~6,560 total messages seen; 37 individual per-message fetch failures
were logged and skipped without failing the job; review-queue approve/reject and the
post-sync auto-refresh (applications list + review queue, no page reload) both worked.

**Also observed, Phase 4b (classifier) territory, not Phase 5 — flagged, not fixed**: on
this real mailbox, confidence scores were uniformly lower than on the curated
evaluation dataset (avg ~0.36 for review-queued items; none crossed the 0.85 auto-apply
bar), and some company-name extraction picked up greeting text (e.g. "Anduril Hi Gia
Huy" instead of "Anduril"). Every affected message still correctly routed to manual
review rather than auto-applying something wrong — the trust-model gate did its job —
but it's real signal that `classifier/patterns.py`'s constants could use a re-tuning
pass against real-world mail, not just the evaluation dataset, whenever that's prioritized.

## Phase 6 results (2026-09-17)

Evaluation dataset grew from 18 to 88 examples (the original 18 kept as
`clean_template`; 70 added across `html_noise`, `greeting_adjacent`,
`signature_footer`, `recruiter_outreach`, `ambiguous`, `sender_variation`, and
`messy_phrasing` — see `docs/superpowers/specs/2026-09-17-job-tracker-phase-6-classifier-quality-design.md`
and the accompanying plan for the category rationale). Measured with
`evaluation/baseline_metrics.json` (today's classifier, before any Phase 6 code
change, against the new dataset) as the "before" column and
`uv run python -m evaluation.compare`'s final output as "after":

| metric | before | after |
|---|---|---|
| classification_accuracy | 0.761 | 0.989 |
| status_accuracy | 0.722 | 1.0 |
| company_exact_accuracy | 0.444 | 0.861 |
| position_exact_accuracy | 0.692 | 0.954 |
| precision_at_threshold | 0.828 | 1.0 |
| auto_apply_rate | 0.403 | 0.431 |

`JOB_SIGNAL_NORM`/`MARGIN_NORM` recalibrated from `6.0`/`4.0` to `4.5`/`3.0` (Task
10) — the first-pass values held on the first attempt, with no fallback to `5.0`/`3.5`
needed (Task 10's acceptance criteria — `precision_at_threshold` not regressing from
the Task 9 checkpoint, `auto_apply_rate` visibly increasing from it — both passed
immediately). `settings.classification_confidence_threshold` (`0.85`) was not changed,
per the Phase 6 spec's explicit scope decision.

**Cross-task bug found and fixed during final whole-branch review, not a planned
task**: `auto_apply_rate` initially landed at 0.375 (Task 10's checkpoint above,
`precision_at_threshold` 0.963) — below the original pre-Phase-6 baseline of 0.403, even
though `precision_at_threshold` had improved substantially. The final whole-branch
review traced this to a cross-task interaction, not the confidence formula: Task 6's
`preprocess.py::_strip_greeting_lines` deleted a matched greeting line outright, which
collapsed the sentence boundary company extraction (Task 7) depended on — a body like
"...at Solace Systems\n\nHi Avery,\n\nUnfortunately, we..." lost its paragraph break
entirely once whitespace collapsed, so the capitalized-word-run company capture ran
straight through "Systems" into the next sentence's capitalized first word
("Unfortunately"), bypassing Task 7's trailing-greeting trim (which only strips an
actual greeting word, not an arbitrary next-sentence word). Fixing that (substituting a
sentence-boundary marker instead of empty string, and widening the company-boundary
lookahead to match it) closed the gap the recalibration alone hadn't:
`auto_apply_rate` now exceeds the original baseline (0.431 > 0.403),
`company_exact_accuracy` improved further (0.764 → 0.861), and `precision_at_threshold`
reached 1.0. See the classifier module for the fix; it's covered by regression tests at
the `classify_and_extract` seam, not just isolated `find_company`/`preprocess_body`
calls, so a future regression in either function alone or in their interaction would be
caught.

**Two more known gaps, found during Task 9's pre-flight verification and task review,
not fixed this phase**:
- One `ambiguous`-category dataset example (a "hiring managers panel" meetup
  announcement) is a false positive, driven entirely by original, pre-Phase-6 patterns
  (`interview (?:invitation|process)` + `\bcandidates?\b`) that this phase didn't touch
  — real signal that those two original patterns are too loosely scoped for some real
  non-recruiting business correspondence. **Fixed in Phase 7** (a `\bmeetup\b` negative
  pattern, not a change to the two loosely-scoped positive patterns themselves — see
  "Phase 7 results" below).
- The new `\bopening\b`/`\bopportunity\b` `GENERIC_JOB_PATTERNS` entries (weight 3 each,
  added in Task 9) can alone cross the job-relatedness threshold with zero corroboration
  — a deliberate, dataset-verified trade-off (needed for `recruiter_outreach`
  classification to work at all), but real mail beyond this evaluation dataset
  (marketing "grand opening" emails, generic biz-dev "opportunity" outreach) could
  trigger false positives these 88 examples don't exercise.

## Phase 6 manual testing findings (2026-09-20)

Validated the Phase 6 classifier changes against the same real Gmail account used for
Phase 5's manual testing (backend, frontend, and worker all restarted fresh on today's
code; migrations confirmed at head first). Went in specifically to check whether the
real-mailbox weaknesses that motivated Phase 6 (see Phase 5's "Also observed" note
above) actually improved, not just the curated evaluation dataset.

**The original motivating bug is fixed, confirmed against the exact real email that
found it.** The Phase 5 finding was company extraction contaminated with greeting text
("Anduril Hi Gia Huy" instead of "Anduril") on a real Anduril rejection email, still
sitting in this mailbox's processed-message history. Re-running that exact
subject/body/sender through today's classifier gives company="Anduril" (tier
"template") and position="2027 Software Engineer Intern" (tier "template") — both
clean. This is the direct, real-world confirmation the curated-dataset eval numbers
alone couldn't provide.

**New real bug found and fixed this session**: a real "Interview with Nike, Inc." email
sent via HireVue extracted company="Hirevue" (the interview platform) instead of "Nike,
Inc." (the actual employer) — the exact failure category Phase 6 already blocklisted
`hackerrank.com`/`codesignal.com`/`testgorilla.com`/`codility.com` for, just missing
`hirevue.com` itself. Fixed by adding it to `ATS_DOMAINS`
(`classifier/patterns.py`) — one line, mirrors the existing pattern exactly. Verified:
company now resolves to "Nike, Inc." via the display-name fallback; 274/274 backend
tests pass (added a regression test,
`test_find_company_falls_back_to_display_name_for_hirevue_interview_invites`); the
Phase 6 evaluation baseline is unchanged (no dataset example touches `hirevue.com`).

**Two more real extraction gaps found, not fixed this session** (a Verisk rejection
email, sender `TalentAcquisition@oraclecloud.verisk.com`): company extracted as
"Oraclecloud" instead of "Verisk", and position not extracted at all despite the body
stating it explicitly ("Tech Intern | 2027 Summer Internship Program"). Root-caused,
not just observed unusual — see the two new entries under "Known gaps" above for the
mechanism in each case. Both are safe (confidence 55%, correctly routed to review, no
auto-apply), just extraction-quality gaps for a future pass.

**Confirmed still true from Phase 5's mailbox on Phase 6 code**: real-mail confidence
has never crossed the 0.85 auto-apply threshold across the full ~6,560-message history
(max observed 0.75, average ~0.37 on pending-review items) — the Phase 6 recalibration
improved the curated dataset's `auto_apply_rate` (0.403 → 0.431) but real mail is still
systematically lower-signal than the dataset. To directly exercise the high-confidence
auto-apply path under today's code (not just the pre-Phase-6 fixture already in the
DB), sent a fresh clean test email ("Thank you for applying to the Software Engineer
position at Acme Corp"-style, company "NovaByte Technologies") to the connected
mailbox: extracted cleanly (company/position/status all correct), confidence 0.90,
correctly auto-created via the "create" path. Confirms the auto-apply mechanism itself
is intact under Phase 6's changes; the low-real-world-confidence issue remains an
accepted, already-documented gap (Phase 6 spec's scope explicitly excluded touching
`classification_confidence_threshold`).

**Status detection**: real examples exist in this mailbox's history for Applied (37),
Interview (3), OA (6), and Rejected (6), all correctly classified. No real Offer email
exists in this mailbox, so that path wasn't exercised — not fixed or worked around, per
the decision to use real emails where practical rather than manufacture every case.

**Housekeeping note, not a classifier finding**: the review queue accumulates across
every testing session since Phase 4 (64 pending items at the start of this session,
predating Phase 6). `ProcessedMessage` rows are never re-classified once written
(idempotency by design — see "Idempotency" above), so an old row's `extracted_company`
etc. can reflect a *previous* classifier version, not current code. One review-queue
item (a TikTok email) showed company="Careers" from an old run; re-running its exact
subject/sender through current code gives "Tiktok" correctly — already fixed, just
stale data. Don't infer a live bug from an old queue row without re-running it through
current code first.

## Phase 7 results (2026-09-20)

Three targeted classifier extraction fixes (the design spec frames this as four
concrete gaps, §2.1–§2.4 — the newsletter and meetup false positives, §2.3/§2.4, share
one negative-pattern mechanism and landed as a single commit, so four gaps map to three
code fixes), found during Phase 6's manual testing against a real Gmail account (see
"Two more real extraction gaps found, not fixed this session" and the "hiring managers
panel" false-positive note above) and formalized in
`docs/superpowers/specs/2026-09-20-job-tracker-phase-7-classifier-extraction-fixes-design.md`
and the accompanying plan. Evaluation dataset grew from 88 to 91 examples (three new
cases added up front, each verified to reproduce its bug before the corresponding fix
landed). Measured with `evaluation/baseline_metrics.json` (Phase 6's frozen baseline,
unchanged) as the "before" column and `uv run python -m evaluation.compare`'s final
output as "after":

| metric | before | after |
|---|---|---|
| classification_accuracy | 0.761 | 1.0 |
| status_accuracy | 0.722 | 1.0 |
| company_exact_accuracy | 0.444 | 0.865 |
| position_exact_accuracy | 0.692 | 0.955 |
| precision_at_threshold | 0.828 | 1.0 |
| auto_apply_rate | 0.403 | 0.419 |

(This table's `auto_apply_rate` baseline, 0.403, is the frozen Phase 6 baseline
(`evaluation/baseline_metrics.json`, measured before any Phase 6 code change) — it is
not the same number as the 0.431 the "Phase 6 results" section above reports, which was
measured after Phase 6's own fixes landed, at a different point in the project's
history. The two new job-related dataset examples Phase 7 added (Verisk/oraclecloud and
Solstice Robotics) both score well under the 0.85 auto-apply confidence threshold, so
neither could have moved `auto_apply_rate` on its own in either direction; the 0.419
here reflects zero pre-existing auto-applies lost and a small net gain from Phase 7's
extraction fixes correcting a few previously-review-queued items enough to clear the
threshold.)

Re-running `evaluation.compare` after this whole-branch review's own two fixes (the
`_POSITION_TOKEN` capitalized-first-word guard and the anchored `\bnewsletter\b`
pattern, see below) reproduced every number in the table above unchanged — neither fix
touches any of the 91 dataset examples' actual classification, since the dataset has no
example matching Finding 1's filler-clause shape or Finding 2's footer-newsletter shape.
`MIN_AUTO_APPLY_RATE` in `tests/test_evaluation_accuracy.py` therefore stays at `0.40`
(comment: "currently 31/74=0.419"), unchanged — see that file for the "tolerates one
more miss" rule its value follows.

Each fix is covered by both a dataset example and a direct regression test, not just
the aggregate numbers above:
- **`fix(classifier): resolve the apex domain when an ATS platform name is a leading
  subdomain`** — covered by the new `Verisk`/`oraclecloud.verisk.com` dataset example
  and by `test_find_company_prefers_the_apex_domain_over_an_oraclecloud_subdomain` in
  `tests/test_classifier_fields.py` (alongside a companion test confirming the existing
  Workday-tenant-subdomain behavior was left intact).
- **`fix(classifier): allow pipe-delimited, up-to-8-word position titles`** — covered by
  the new Solstice Robotics dataset example (`"Mechanical Engineer | 2027 Summer
  Internship Program"`) and by `test_find_position_captures_a_pipe_delimited_title` in
  `tests/test_classifier_fields.py`.
- **`fix(classifier): suppress newsletter and meetup false positives`** — covered by the
  new Riverside Neighbors newsletter dataset example plus the pre-existing "Meetup:
  Hiring managers panel this Thursday" example (already in the dataset, previously
  mismatched), and by two direct regression tests in `tests/test_classifier_patterns.py`:
  `test_negative_patterns_suppress_a_newsletter_with_incidental_job_language` and
  `test_negative_patterns_suppress_a_meetup_style_false_positive`.

**Deliberate deviation from the written spec**: the spec's speculative `\bpanel\b`
negative pattern (design §2.4) was not added. Direct verification against the actual
dataset before implementing the newsletter/meetup fix showed only `\bmeetup\b` was
needed to fix the one real failing case, and adding `\bpanel\b` would risk suppressing a
legitimate "panel interview" scheduling email that a real candidate might receive — an
evidence-based narrowing of the spec, not an oversight, documented at the point it
happened (the plan's Task 4).

**Two Important issues found and fixed during final whole-branch review, not caught by
any per-task review** (each task above passed its own review individually; these only
became visible once the branch was read as a whole):
- **Finding 1 — position-token overcapture reopened.** The pipe-delimited-title fix
  above widened `_POSITION_TOKEN`'s word cap from 6 to 8 to fit a real title, but the
  word cap alone only bounds an overcapture's *length*, not whether one happens — an
  8-word span starting mid-filler-clause ("time you took to apply for the Analyst") is
  exactly the failure mode the file's own pre-Phase-7 comments already warn about, just
  longer. Fixed the same way `_COMPANY_TOKEN` already guards against it: the token's
  first character must now be an uppercase letter or digit (only the first word, unlike
  `_COMPANY_TOKEN` which requires every word capitalized — position titles routinely
  have lowercase words after the first, e.g. "Software Engineer II"). Covered by
  `test_find_position_does_not_capture_a_lowercase_filler_clause` in
  `tests/test_classifier_fields.py`; the pipe-delimited Solstice Robotics case above
  still passes unchanged.
- **Finding 2 — `\bnewsletter\b` too broad.** The newsletter negative pattern stacked
  additively with the pre-existing `unsubscribe` pattern (2+2=4), so a genuine
  job-related email whose footer happened to say "unsubscribe from our newsletter"
  could flip to a false negative it wasn't before — strictly worse than the
  review-queue noise the pattern was meant to fix, since this app's idempotency model
  (`ProcessedMessage`, unique per message) means a false negative is silently dropped
  forever, not just re-seen and re-scored later. Fixed by anchoring the pattern to the
  first ~60 characters of the combined text (`^.{0,60}\bnewsletter\b`) rather than
  matching anywhere: `text.combine_subject_body` always puts the subject first, and
  every real evidenced newsletter (the dataset's "Riverside Neighbors September
  Newsletter" example, the pre-existing "Your weekly career newsletter" example, and a
  real "DNDA September Newsletter: A New Destination" subject the reviewer verified by
  hand, not itself in the dataset) names itself within the first few words of its own
  subject, while a footer mention is typically hundreds of characters in.
  Covered by `test_negative_patterns_do_not_suppress_a_job_email_with_a_newsletter_footer_mention`
  in `tests/test_classifier_patterns.py`, alongside the pre-existing
  `test_negative_patterns_suppress_a_newsletter_with_incidental_job_language` (still
  passes unchanged).

Both fixes reproduce every number in the table above exactly — see the note under it —
since neither touches any example already in `evaluation/dataset.jsonl`; their value is
closing failure modes the dataset doesn't yet exercise, evidenced instead by direct
regression tests against the specific text shapes the whole-branch review constructed.

Also addressed in the same review pass, Minor severity: a comment on the
`oraclecloud.` subdomain-prefix entry (`classifier/text.py`) was reworded to scope its
claim to the one evidenced sender shape (`oraclecloud.<company>.com`) rather than
reading as though it handled Oracle Fusion HCM tenant subdomains generally (e.g.
`tenant.fa.us2.oraclecloud.com`, a different, more common shape that still resolves
incorrectly and is not fixed by this branch). See the new Known gap below on
`_SUBDOMAIN_PREFIXES` for a related, wider hazard this same review surfaced but did not
fix.

## Phase 8 results — production deployment (2026-09-21 → 2026-09-24)

Spec/plan: `docs/superpowers/specs/2026-09-21-job-tracker-phase-8-production-deployment-design.md`
and `docs/superpowers/plans/2026-09-21-job-tracker-phase-8-production-deployment.md`.
Deployment/setup steps and secret-rotation procedures live in `README.md`'s
"Production deployment" section — not duplicated here.

### Production architecture ($0/month; sync trigger superseded by Phase 9)

> Phase 9 replaced `sync-worker.yml` with two dispatchable lane workflows, started by
> the API on every Sync click. Everything else below still holds.

```
Browser ──> Render Static Site  https://job-tracker-1-ldy2.onrender.com
              (frontend/dist, CDN, no spin-down)
              │  rewrite rule: /api/*  ->  https://job-tracker-lds1.onrender.com/api/*
              v  (browser sees same-origin; no VITE_API_URL, no CORS in the happy path)
            Render free Web Service  https://job-tracker-lds1.onrender.com
              (FastAPI/uvicorn; migrations run in the Build Command;
               RUN_WORKER_IN_PROCESS=false — the API never runs sync jobs)
              │
              v
            Neon Postgres (free, autosuspends)  <──┐
                                                   │ PROD_DATABASE_URL etc. (repo secrets)
GitHub Actions ── ci.yml: required checks on main  │
              └── sync-worker.yml (cron */5, really ~every 3–5h) ──> python -m app.sync.drain
                                                   └──> Gmail API
```

Deploy flow: push to `main` → Render auto-deploys both services; `ci.yml` runs on every
push/PR. `backend`/`frontend` are required checks for PR merges, but as of 2026-09-24
changes are pushed straight to `main` (maintainer's choice; admins bypass protection),
so CI no longer gates the deploy — run the full local checks before every push. The scheduled worker always runs `main`'s
code at the moment it fires, so it picks up merged changes with no deploy step.

### What changed from the spec, discovered only during live deployment

Each was found against real Render/Neon/Google, not locally or in CI:

1. **Render's Pre-Deploy Command is paid-only** (`4cdeed6`) → migrations run as the
   tail of the Build Command (`uv sync && uv run alembic upgrade head`). A failed
   migration still aborts the deploy; it just looks like any other build failure.
2. **OAuth login silently failed behind the rewrite** (`50dba5b`) — Render's rewrite
   forwards the backend's own Host header, so `request.url_for` built a callback on the
   backend's domain and the session cookie landed on the wrong origin. `redirect_uri`
   is now built from `settings.frontend_url`, so Google's callback also goes through
   the rewrite. **Google Console redirect URIs must therefore be the *frontend*
   domain** (`https://job-tracker-1-ldy2.onrender.com/api/v1/...`) — the plan's Task 7
   text (backend domain) predates this fix.
3. **Neon drops idle connections server-side** (`cbd83be`) → `pool_pre_ping=True`.
4. **`/health/worker` reported "stale" during a long job** (`861b8f1`) → heartbeat now
   updates per message, not only per outer loop tick.
5. **Render free tier OOM-restarted the API while the in-process worker synced**
   (`1ebafef`) → `process_job` expunges the session identity map every page. A second
   OOM after that fix, with no memory metrics on the free tier to debug with, led to
   (`91d1d5d`) moving the worker out of the API entirely: `_run_one_tick` +
   `drain_once` + `app/sync/drain.py`, originally for a Render Cron Job.
6. **Render Cron Jobs have no free tier** (`f3d46e5`) → the same entrypoint runs from
   a scheduled GitHub Actions workflow instead (public repo = free minutes). PR #4
   later added `concurrency` (one drain at a time, never cancelled mid-job) and
   `timeout-minutes: 120`, guarded by `tests/test_sync_worker_workflow.py`.

Net effect on the spec: §3.3 (in-process worker) is superseded; `app/sync/inprocess.py`
and the `RUN_WORKER_IN_PROCESS` setting remain in the code, **off** in production, and
`/health/worker` therefore always returns `503 not_running` there — expected, not an
outage. It's only meaningful if the in-process mode is ever deliberately re-enabled.

Also found while preparing the production secret rotation, before rotating anything:
**reconnect and disconnect returned a 500 after `GMAIL_TOKEN_ENCRYPTION_KEY` was
rotated.** Both decrypted the old refresh token so they could revoke it, and only
caught `GoogleApiError`, not `InvalidToken`, so a user could never recover without a
manual DB delete. Fixed in PR #5: the revoke is best-effort and skipped when the
stored token is unreadable.

### Production verification (2026-09-24)

- `/health` 200, `/health/ready` 200 (Neon reachable), `/health/worker` 503
  `not_running` (confirms in-process worker is off). `/api/*` through the frontend
  rewrite returns 401 unauthenticated (proxied correctly). A foreign `Origin` gets no
  `Access-Control-Allow-Origin` header (CORS is not a wildcard).
- **Scheduled worker, end-to-end, no manual trigger**: a Sync clicked in the deployed UI
  created job `13693c38…` (incremental, `queued`, 21:38:25Z); scheduled run
  `36062738758` (`event: schedule`) claimed it at 21:38:29Z, paged 122 messages
  (77 new, 1 queued for review, 74 ignored, 2 per-message fetch failures skipped
  non-fatally), completed at 21:39:24Z and logged `Drained 1 job(s)`. The pickup was
  seconds, not hours, only because the click landed just after a scheduled run started.
- Review queue approve, edit-then-approve and reject; application create, edit and
  delete — all passed in production, each re-checked after a page reload.
- Branch protection on `main` requires `backend` and `frontend`; CI green on `main`.

### Production secret rotation (2026-09-24)

All four production secrets were rotated one at a time, after being exposed outside
git during setup. Git history was scanned first and is clean: no `.env` was ever
committed, and every committed value is a placeholder. Each new value went from its
source straight into GitHub (`gh secret set` from stdin) and the clipboard for Render,
never printed. Each was verified on both sides:

- `SECRET_KEY`: old sessions rejected, sign-in works.
- `GOOGLE_CLIENT_SECRET`: new secret added alongside the old one; worker token refresh
  returned 200 with GitHub's copy; sign-in worked after the old secret was disabled;
  old secret deleted.
- `DATABASE_URL` (Neon role password reset, pooler host): new URL connected locally
  before being deployed; `/health/ready` 200; worker connected.
- `GMAIL_TOKEN_ENCRYPTION_KEY`: Disconnect (the PR #5 fix, now exercised in production),
  old grant removed at Google, reconnected, worker sync succeeded.

**Found during rotation:** `GmailConnection` holds the sync watermark
(`last_synced_message_date`), so **any Disconnect resets it**. The first sync after
reconnecting is a full 180-day `initial` sync. That's harmless thanks to
`ProcessedMessage` idempotency: the run listed ~6,600 messages across 66 pages,
fetched only the 36 new ones, and took ~90s. Not changed; documented in README's
rotation table.

### Free-tier limitations (accepted, not bugs)

- **Sync latency was hours, not minutes (fixed in Phase 9).** GitHub treats cron as
  best-effort and heavily throttles it: `*/5` produced ~11 runs/day (gaps of ~2–5h)
  over 2026-09-23/24. Phase 9 made `workflow_dispatch` the primary trigger; cron is
  now only a fallback.
- **GitHub disables a public repo's scheduled workflows after 60 days without a
  commit.** Re-enable from the Actions tab (or push a commit) after a quiet period.
- **Render Web Service spins down after 15 min idle**; first request takes ~30–60s.
  The Static Site never sleeps, so the page loads instantly but the first API call
  is slow.
- **Neon autosuspends** when idle; first query after a pause is slower
  (`pool_pre_ping` handles the dropped connections).
- **512 MB Render instance, no memory metrics** — why the worker lives outside the API.
- **Google OAuth consent screen is in "Testing" mode**: only listed test users can
  sign in. Google also expires refresh tokens issued to Testing-mode apps after about
  7 days, so expect to reconnect Gmail roughly weekly until the app is verified —
  a sync failing with the generic Gmail error after a quiet week most likely means
  this (see the `_safe_error_message` gap above).

### Known gaps added by Phase 8 (not fixed)

- **`process_job` has no time budget of its own** (still true after Phase 9, which
  deferred the fix — see "Phase 9 results"). A lane's drain budget is checked only
  between jobs. A job that outlives the 120-minute workflow timeout (or
  a runner that dies) is left `running`; the *next* scheduled run's reaper requeues
  it with `attempts += 1`, and three such losses fail it permanently. `page_token` is
  checkpointed per page, so no work is lost. Proper fix: have `process_job` stop
  between pages at a deadline and requeue without consuming an attempt.
- **No monitoring of the scheduled worker** beyond the Actions run history (GitHub
  emails the repo owner on a failed run). `/health/worker` can't see it.
- **Branch protection doesn't apply to admins** (`enforce_admins: false`) — `f3d46e5`
  was pushed straight to `main`. Harmless for a single maintainer; turn it on if that
  matters.
- **No rate limiting** on any endpoint, including `POST /gmail/sync` (spec §11).

## Phase 9 results — on-demand sync with lanes (2026-09-24)

Spec/plan: `docs/superpowers/specs/2026-09-24-job-tracker-phase-9-on-demand-sync-design.md`
and `docs/superpowers/plans/2026-09-24-job-tracker-phase-9-on-demand-sync.md`.
Setup, sync behavior and the dispatch-token rotation procedure are in `README.md`'s
"Production deployment" section.

**The problem:** Phase 8's sync engine worked, but it only ran when GitHub's throttled
cron fired (every 2–5 hours in practice), so a Sync click could wait hours for a job
that then took under a minute. A single worker lane also meant a long initial import
could hold up everyone's incremental syncs.

**What changed** (each checkpoint was reviewed and pushed separately, with CI green):
1. **Lanes** (`9d2092f`) — `claim_next_job` / `reap_stale_jobs` / `drain_once` take an
   optional `job_type`; `python -m app.sync.drain --lane incremental|initial`. No
   migration: `job_type` already existed. `drain_once` also **waits for a queued
   retry** that falls due within its budget (backoff is 60s/120s), instead of leaving
   it for a later run, but exits immediately if a due job is locked by another worker.
   Public-log privacy: `httpx`/`httpcore` are set to WARNING in the drain entrypoint, and
   `message_ref(id)` replaces raw Gmail message IDs in worker/pipeline warnings.
2. **Two lane workflows** (`b8063c9`) — `sync-incremental.yml` (30 min) and
   `sync-initial.yml` (120 min) replaced `sync-worker.yml`.
3. **Dispatch + re-kick** (`4812bd7`) — after `POST /gmail/sync` commits a job, a
   background task calls GitHub's workflow-dispatch API for that lane. It's
   best-effort: 5s timeout, **never raises**, logs lane + HTTP status only. A click
   that hits the user's already-active job (the existing 409) re-dispatches if that
   job is still `queued`, or `running` but stale.
4. **One Gmail call per message** (`cfb7f61`) — `google_api.get_message()` does a
   single `format=full` fetch returning `(summary, body)`; `get_message_body` is gone.
5. **Frontend** (`d1e1686`) — "Starting sync…" (queued) vs "Syncing — N of M"
   (running); an initial-import note; a Retry link after 2 min in the queue; the Sync
   panel is hidden until Gmail is connected.

**Dispatch token (production config, never in git):** a fine-grained PAT named
`job-tracker-sync-dispatch`, repository access **only `Huypham251/job-tracker`**,
repository permissions **Actions: Read and write** + mandatory **Metadata: Read-only**,
no user permissions. Created **2026-09-24**; **expires 2027-09-24.** It lives only in the
Render backend's `SYNC_DISPATCH_TOKEN` (plus `SYNC_DISPATCH_REPOSITORY=Huypham251/job-tracker`,
`SYNC_DISPATCH_REF=main`). **Regenerate it and update Render before 2027-09-24** —
reminder target 2027-09-10. The procedure is in README's rotation section. If it lapses,
nothing breaks, but syncs silently fall back to cron latency and Render logs `dispatch …
rejected: HTTP 401`.

**Observed, not explained:** probing the token with deliberately invalid requests
showed GitHub accepting its `issues=write` check on `job-tracker` (an empty issue got a
422 validation error rather than a 403), even though the token's settings page lists
only Actions + Metadata. On a public repo the token *wasn't* given, the same request
is refused (403). Pull requests, contents, secrets and repo settings are all refused
(403). Accepted as GitHub-side behavior: at worst a leaked token could open issues or
comments on this public repo, which any GitHub account can already do. (A first,
non-invalid probe accidentally created a test issue, #6, which was deleted right away.)

### Production verification (2026-09-24, times UTC on 2026-09-25)

- **Warm backend:** Sync clicked 06:33 → `Sync Worker (incremental)` run
  `36103421214`, `event: workflow_dispatch`, created 06:33:47, drain started 06:33:57,
  finished 06:34:16 with `Drained 1 job(s) from the incremental lane`. **About 1 minute
  from click to "Synced"** in the UI, with "Starting sync…", "Syncing — N of M" and
  "Synced: …" all seen in order. (Before Phase 9: up to ~5 hours.)
- **Cold backend** (API idle ~32 min, 06:42→07:14): the page loaded in under a minute
  while Render woke the API. Sync clicked 07:14 → run `36106612260`
  (`workflow_dispatch`) created 07:14:06, `Drained 1 job(s) from the incremental lane`
  at 07:14:25, finished 07:14:29. **Under 1 minute from click to "Synced"**, so the
  wake-up cost landed on the page load, not on the sync.
- **Failure drill:** `SYNC_DISPATCH_REPOSITORY` set to a nonexistent repo → Sync click
  returned normally, the job stayed `queued` ("Starting sync…"), Render logged
  `Sync worker dispatch for the incremental lane was rejected: HTTP 404`, and **no run
  started** (06:34→06:41). Repository restored → the **Retry** link appeared after 2 min
  → click → run `36104019837` (`workflow_dispatch`, 06:41:41) drained the same job.
- **Cron fallback:** a `schedule` run of the new incremental lane ran at 06:23:22.
- **Both lanes valid on GitHub:** manual dispatches of each (runs `36081651533`,
  `36081654353`) drained only their own lane (`Drained 0 job(s) from the initial lane`).
  That's the only production evidence of lane separation.
- **Public logs:** no `gmail.googleapis.com/…/messages` URLs and no raw 16-hex message
  IDs in any Phase 9 run log.
- **Not verified in production (by decision):** an initial import and an incremental
  sync running **at the same time** in the two lanes. That would need a second real
  Gmail account. It rests on the automated two-connection test (see the multi-worker
  note in "Known gaps").

### Known gaps / future work after Phase 9

- **Time-sliced initial imports (deferred by decision).** One initial job still runs to
  completion inside one run, bounded by the lane's 120-minute timeout (roughly 10,000
  messages at the measured rate). The deferred design is in the Phase 9 spec §10:
  stop between pages at a deadline, re-queue without using an attempt, and have the run
  dispatch its own lane again.
- **The Retry hint can show for a legitimately waiting initial import.** If one user's
  initial import queues behind another user's in the same lane, "The sync worker
  hasn't started yet" appears after 2 min. Retry is harmless there (the pending run is
  simply re-requested).
- **The cron fallback is still GitHub-throttled** and is disabled after 60 quiet days
  in a public repo. It only matters when dispatch fails.
- **No rate limiting** (unchanged): re-kicks are bounded only by one active job per
  user, and repeated dispatches collapse to one pending run per lane.

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

**Before starting any of these for the first time after pulling new changes**, run
`cd backend && uv run alembic upgrade head` — see "Manual testing findings" above for
what happens if you skip this (the worker fails opaquely; the API server can mask it
longer since most endpoints don't touch the newer tables).

## Testing conventions

Backend: `cd backend && uv run pytest`. Frontend: `cd frontend && npx tsc -b && npx
oxlint`. Every backend test mocks the `Extractor` Protocol or Gmail's `google_api`
module — no test makes a real network call. `evaluation/run_eval.py` is *not* part of
the pytest suite (it's a standalone reporting script); `tests/test_evaluation_accuracy.py`
is the automated subset of it. `evaluation/compare.py` diffs the current classifier
against the Phase 6 baseline (`evaluation/baseline_metrics.json`, frozen at Task 4 and
never edited again) — run it after any future `app/classifier/` change to see the
before/after impact directly. `evaluation/inspect_confidence.py` prints the
confidence-formula components per example, for recalibration work.

Three non-obvious test techniques introduced in Phase 5, in `backend/tests/test_sync_worker.py`
(the third in its own file), worth knowing about before extending either:
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
- **Subprocess-based standalone-import test**
  (`test_worker_module_can_configure_orm_mappers_standalone`, in the separate file
  `tests/test_sync_worker_entrypoint.py`) runs `python -c "import app.sync.worker; ...
  configure_mappers()"` in a genuinely fresh process rather than importing in-process —
  needed because `conftest.py` explicitly pre-imports every model module for the whole
  suite, which would silently mask the exact import-ordering bug this test exists to
  catch (see "Manual testing findings" above).
