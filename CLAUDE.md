# Job Application Tracker — Project Notes

Read this before starting Phase 5. It's a snapshot of where the project stands after
Phase 4b (2026-09-16), not a spec — the specs under `docs/superpowers/specs/` and plans
under `docs/superpowers/plans/` remain the source of truth for what was decided and why.

## Status

Phases 1–4b are complete, merged to `main`, and manually verified in the browser.
Backend: 187/187 tests passing. Frontend: `tsc -b` clean, `oxlint` clean (0 errors, 3
pre-existing warnings in `AuthContext.tsx`/`useApplications.ts`, unrelated to Phase 4/4b
and not touched by them). Working tree clean, no uncommitted changes.

- Phase 1: manual CRUD for job applications.
- Phase 2: Google OAuth login, multi-user.
- Phase 3: Gmail connection (OAuth, read-only), manual "fetch recent messages" test view.
- Phase 4: classification/extraction pipeline, matching/dedup, trust model, review queue
  — originally built on the Anthropic API, **then fully replaced by Phase 4b**.
- Phase 4b: the Anthropic-backed classifier was swapped for a local, deterministic,
  rule-based one. `app/llm/` and the `anthropic` dependency are gone. No API key, no
  external service, no per-email cost for classification.

**Read `2026-09-15-job-tracker-phase-4-pipeline-design.md` for the pipeline architecture
that's still current (matching, trust model, DB schema, API contract, frontend) and
`2026-09-15-job-tracker-phase-4b-local-classifier-design.md` for how classification
actually works today.** Both specs carry supersession banners pointing at each other.

## Architecture

```
backend/app/
├── applications/    Phase 1 — CRUD, source column ("manual"|"gmail")
├── auth/            Phase 2 — Google OAuth login, JWT cookie sessions
├── gmail/            Phase 3 — Gmail OAuth connection, message fetch (metadata + body)
├── classifier/       Phase 4b — local rule-based classification/extraction
│   ├── text.py        normalization, sender-domain/display-name parsing
│   ├── patterns.py     weighted phrase tables, ATS-domain lists, tunable constants
│   ├── fields.py        company/position regex extraction (ordered fallback tiers)
│   ├── extractor.py      Extractor Protocol, ClassificationError, RuleBasedExtractor
│   └── schemas.py         EmailExtraction (Pydantic contract)
├── pipeline/         Phase 4 — orchestration (provider-agnostic)
│   ├── matching.py     rapidfuzz company/position matching against existing apps
│   ├── service.py        process_inbox / review-queue / approve / reject, trust model
│   ├── models.py          ProcessedMessage (idempotency + audit + review queue)
│   └── router.py           /api/v1/pipeline/*
└── core/config.py    Settings — no Anthropic-related fields anymore

frontend/src/
├── components/ApplicationsPage.tsx   dashboard shell: Gmail panel, Process Inbox,
│                                      ReviewQueue, add/edit form, applications list
├── components/ReviewQueue.tsx          "Needs review" section, Approve/Edit/Reject
└── components/GmailPanel.tsx           Connect Gmail, Fetch recent messages (display-only)
```

**The extractor swap validated the original abstraction**: `app/pipeline/` needed only
import-path changes to move from `AnthropicExtractor` to `RuleBasedExtractor` — the
`Extractor` Protocol (`classify_and_extract(*, subject, sender, date, body) ->
EmailExtraction`) is the entire seam. If a future phase wants a third
provider/implementation, it slots in the same way.

## Key design decisions (why, not just what)

- **Trust model is a single column, `applications.source`.** `"manual"` vs `"gmail"`.
  Set to `"manual"` unconditionally by `update_application` on *any* manual edit — it
  never reverts, including when a human approves a classifier proposal through the
  review queue (`approve_review_item`'s update branch never touches `source`). This is a
  **one-way ratchet by design**: once a human has touched a row, every future classifier
  change to it requires approval, forever. Fail-closed, not a bug.
- **Three independent gates force review, not just ownership**: `_apply_decision` in
  `pipeline/service.py` routes to `pending_review` if the matched application isn't
  `source="gmail"`, **or** the fuzzy match is ambiguous, **or** confidence is below
  `settings.classification_confidence_threshold` (currently 0.85) — any one alone is
  enough, independent of the other two.
- **Fuzzy matching thresholds** (`pipeline/matching.py`, unchanged since Phase 4):
  `STRONG_MATCH_THRESHOLD=85`, `NO_MATCH_THRESHOLD=60`, `AMBIGUOUS_MARGIN=10`. Score
  ≥85 AND ≥10 points clear of the runner-up → confident match. 60–84, or ≥85 with a
  runner-up within 9 points → ambiguous → review. <60 → confident "no match" → create
  (still gated by confidence before it's *auto*-created).
  compared for you.
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
  is classified at most once ever.
- **No pagination on Gmail fetch.** Both "Fetch recent messages" and "Process Inbox" call
  Gmail's list endpoint with only `maxResults` — no `pageToken`, ever. They only ever see
  the top-N most recent messages in the mailbox (N = `pipeline_batch_limit`, default 20).
  A job-related email older than that window is permanently unreachable by the current
  implementation. This is the explicit Phase 4 non-goal ("scheduling/backfill deferred to
  Phase 5"), not an oversight — flagging it here so Phase 5 planning has it in view.
- **Evaluation is now free and always-on.** Unlike the Anthropic version (deferred,
  cost-gated), `evaluation/dataset.jsonl` + `evaluation/run_eval.py` cost nothing per run,
  and `tests/test_evaluation_accuracy.py` gates classification/status/company/position
  accuracy as part of the normal `pytest` run.

## Known gap found during manual testing (not fixed, needs a decision)

`ReviewQueue.tsx` fetches `GET /api/v1/pipeline/review` only once, on mount
(`useEffect(refresh, [])`, empty deps). `ApplicationsPage.tsx`'s `handleProcessInbox`
only calls `refetch()` for the applications list — nothing tells `ReviewQueue` to
re-fetch. So a newly-queued item after clicking "Process Inbox" does **not** appear in
the "Needs review" section until the page is reloaded (or the user approves/rejects some
other already-visible item, which happens to call `refresh()` as a side effect). The
`processed`/`queued_for_review` counts shown by the Process Inbox button are correct —
only the visible queue list is stale. Left as-is per explicit instruction during manual
testing ("don't change anything yet"); worth a one-line fix (pass a `refreshKey` prop, or
have `ApplicationsPage` also trigger the queue's refresh) before/during Phase 5.

## Local dev environment (this machine)

An unrelated project (`~/chasel/chasel-frontend`) occupies ports 5173–5177 on this
machine. This project's frontend is pinned to **5178** (`npm run dev -- --port 5178
--strictPort`), and `backend/.env`'s `CORS_ORIGINS`/`FRONTEND_URL` are set to
`http://localhost:5178` to match — don't "fix" this back to 5173 without checking
whether that conflict still exists. Backend runs on the default `:8000`. Both are
gitignored per-machine `.env` state, not something to commit.

## Testing conventions

Backend: `cd backend && uv run pytest`. Frontend: `cd frontend && npx tsc -b && npx
oxlint`. Every backend test mocks the `Extractor` Protocol or Gmail's `google_api`
module — no test makes a real network call. `evaluation/run_eval.py` is *not* part of
the pytest suite (it's a standalone reporting script); `tests/test_evaluation_accuracy.py`
is the automated subset of it.
