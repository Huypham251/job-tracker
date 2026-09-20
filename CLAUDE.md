# Job Application Tracker — Project Notes

Read this before starting Phase 6. It's a snapshot of where the project stands after
Phase 5 (2026-09-17), not a spec — the specs under `docs/superpowers/specs/` and plans
under `docs/superpowers/plans/` remain the source of truth for what was decided and why.

## Status

Phases 1–6 are complete and merged to `main`. Phase 7 (classifier extraction fixes) is
implemented and verified on a feature branch, pending merge. Phase 5 was manually verified end-to-end
against a real Gmail account (2026-09-17) — see "Manual testing findings" below — and
Phase 6 was manually verified the same way (2026-09-20) — see "Phase 6 manual testing
findings" below. Backend: 279/279 tests passing. Frontend:
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
- Phase 7: three targeted classifier extraction fixes found during Phase 6 manual
  testing (apex-domain company resolution for a leading-ATS-subdomain sender,
  pipe-delimited/longer position titles, newsletter/meetup false positives), plus
  three new evaluation-dataset examples covering the gaps. See "Phase 7 results" below.

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
  subdomain+company) before it's safe to add.
- **Position extraction can't capture a title containing `|`.** `_POSITION_TOKEN`
  (`classifier/fields.py`) allows `[\w&'/+\-]` per word — no pipe — so a real title like
  "Tech Intern | 2027 Summer Internship Program" (from the same Verisk email above)
  fails to extract regardless of the subdomain issue; that title is also 7 words, over
  the 6-word cap, so widening the character class alone wouldn't be enough. Found during
  Phase 6 manual testing, not fixed.

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
  non-recruiting business correspondence.
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

Three targeted classifier extraction fixes, found during Phase 6's manual testing
against a real Gmail account (see "Two more real extraction gaps found, not fixed this
session" and the "hiring managers panel" false-positive note above) and formalized in
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
