# Job Application Tracker — Phase 4 Design: Classification, Extraction & Auto-Tracking Pipeline

**Date:** 2026-09-15
**Status:** Approved for implementation planning
**⚠️ Superseded (2026-09-16):** §2 (LLM provider decision), §5 (Classification &
Extraction — the Anthropic API call), and §10 (Evaluation Strategy) were replaced by
`2026-09-15-job-tracker-phase-4b-local-classifier-design.md` — the shipped
implementation uses a local, deterministic, rule-based classifier (`app/classifier/`),
not an LLM API. Every other section here (§4 data model, §6 matching, §7 trust model,
§8 API contract, §9 frontend structure, §11-13 minus the LLM-specific rows) reflects
what's actually running — read this doc for those, and the 4b doc for classification.
**⚠️ Also superseded (2026-09-17):** §2's "Trigger mechanism" row (manual "Process
Inbox" action) was replaced by `2026-09-16-job-tracker-phase-5-gmail-sync-design.md` —
the shipped implementation is a Postgres-backed job queue and background worker, not a
synchronous per-request fetch; `process_inbox` and its endpoint no longer exist. §8's
API contract is accordingly out of date for the pipeline-trigger endpoint specifically
(`POST /pipeline/process` is gone, replaced by `/gmail/sync*`) — read the Phase 5 doc
for the current sync API; §8's `/pipeline/review*` rows are still accurate.
**Scope:** Phase 4 only — turn fetched Gmail messages into structured job-application
information, match them against existing applications, and create/update applications
either automatically (high confidence) or via a human-reviewed queue (low confidence,
or anything touching a manually-created application). Builds directly on Phase 3
(`docs/superpowers/specs/2026-09-11-job-tracker-phase-3-gmail-design.md`), which was
manually verified end-to-end (real Gmail connect, fetch, disconnect) before this phase
began.

## 1. Goal & Non-Goals

### Goal

1. Classify a fetched Gmail message as job-application-related or not.
2. If relevant, extract structured fields: company, position, status, a relevant date,
   and a confidence score — validated before it can touch any application data.
3. Match the extraction against the user's existing applications, or determine it's new.
4. Auto-create or auto-update applications when confidence is high; queue everything
   else (low confidence, ambiguous matches, or anything touching a manually-created
   application) for human review.
5. Let the user approve, edit-then-approve, or reject queued items from a review UI.
6. Never let automation silently overwrite a manually-entered fact.
7. Provide a way to evaluate classification/extraction quality against a labeled set.
8. Minimize what's sent to the LLM provider and what's persisted about email content.

### Non-Goals (explicitly deferred to Phase 5)

Redis, Celery/background jobs, scheduling/polling for new mail automatically, rate
limiting, monitoring, multi-provider LLM support (the abstraction exists so Phase 5+
*can* add a second provider, but only Anthropic is implemented now), per-field
confidence scores (a single overall score is used — see §3), fuzzy-match tuning beyond
a first reasonable threshold (revisit once the evaluation set shows where it's wrong).

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| LLM provider | Anthropic Claude, `claude-opus-5` | Best accuracy for the fuzzy judgment calls this task requires (job-relatedness, field extraction, implicit matching signal). Provider-neutral abstraction (`app/llm/`) so a future phase can add/swap providers without touching the pipeline. |
| Effort level | `low` (`output_config.effort`) | Classification/extraction is not long-horizon reasoning; Anthropic's own guidance calls this workload shape a good fit for low effort, and it keeps per-message cost down. |
| Structured output mechanism | `client.messages.parse(output_format=EmailExtraction)` | SDK-validated Pydantic output — no hand-rolled JSON parsing, no unvalidated dict ever reaches the database. Directly satisfies "LLM output must be structured and validated." |
| Trigger mechanism | Manual "Process Inbox" action, reusing Phase 3's `list_recent_messages` plumbing | Phase 5 (not this phase) adds background jobs/scheduling. A synchronous, user-initiated action needs no new infra and mirrors Phase 3's own "test retrieval" trigger pattern. |
| Confidence handling | Single overall confidence score per extraction; threshold-gated auto-apply vs. review queue | One number is sufficient to gate "trust this or don't" for this phase; splitting into classification/extraction/matching sub-scores is deferred until real eval data shows it's needed (YAGNI). |
| Manual vs. automatic trust model | Any proposed change touching a `source="manual"` application always goes to the review queue, regardless of confidence; `source="gmail"` applications can be auto-updated once confidence clears the threshold | Directly implements "never overwrite reliable existing information with uncertain extracted information" as a simple, row-level rule instead of per-field provenance tracking. |
| Message body handling | Fetch full body (`format=full`) for pipeline processing (not Phase 3's `format=metadata`); clean (strip HTML/quoted replies/signatures) and truncate (~4000 chars) before sending to the LLM; never persist the body | Reliable extraction genuinely needs body content — Phase 3's metadata-only choice was specific to its own limited test-fetch purpose, not a permanent constraint. What's sent onward is minimized; what's stored is only the derived structured result plus the same subject/snippet Phase 3 already treats as acceptable. |
| Status enum change | Add `other` (additive `ALTER TYPE`); keep `withdrawn` | `other` is a pipeline-only outcome ("confidently job-related, can't confidently map to a specific stage"). `withdrawn` stays manual-only — nothing in a typical email states it — and removing it would drop existing functionality for no reason. |
| Matching library | `rapidfuzz` (new dependency) | Lightweight, well-established fuzzy string matching for normalized company/position comparison; no need to hand-roll or reach for a heavier NLP dependency. |
| Idempotency | `processed_messages` table, unique on `(user_id, gmail_message_id)` | A message is classified/extracted at most once ever — re-running "Process Inbox" only processes genuinely new messages, and doubles as the review-queue source and audit trail. |
| Evaluation approach | Deferred to the `claude-api` skill's `build-eval` workflow at implementation time | That workflow runs a structured interview (grading method, data source, cost) and requires explicit sign-off before producing anything — better than hand-designing a dataset now with no usage data yet. |
| Batch cap | `pipeline_batch_limit` setting, default 20 | Bounds worst-case cost of a single "Process Inbox" click (~$0.10-0.20 at Opus rates). A per-request safety cap, not the rate-limiting/scheduling infrastructure explicitly deferred to Phase 5. |

## 3. Project Structure — additions and changes

```
backend/
├── alembic/versions/
│   └── 0005_add_pipeline_tables.py           # NEW
├── app/
│   ├── core/
│   │   └── config.py                          # MODIFIED — anthropic_api_key, llm_model,
│   │                                             llm_confidence_threshold, pipeline_batch_limit
│   ├── llm/                                    # NEW package — provider abstraction
│   │   ├── __init__.py
│   │   ├── schemas.py                          # EmailExtraction (Pydantic)
│   │   ├── prompts.py                          # system prompt text
│   │   └── client.py                           # Extractor protocol + AnthropicExtractor
│   ├── pipeline/                                # NEW package — orchestration
│   │   ├── __init__.py
│   │   ├── models.py                           # ProcessedMessage
│   │   ├── schemas.py                          # ProcessResult, ReviewItem, ReviewDecision
│   │   ├── matching.py                         # normalize + fuzzy-match against existing applications
│   │   ├── service.py                          # process_inbox, list_review_queue, approve, reject
│   │   └── router.py                           # /api/v1/pipeline/*
│   └── applications/
│       ├── models.py                           # MODIFIED — Application.source column
│       └── schemas.py                          # MODIFIED — ApplicationRead exposes `source`
├── pyproject.toml                               # MODIFIED — add `anthropic`, `rapidfuzz`
├── evaluation/                                   # NEW — not part of the pytest suite
│   ├── dataset.jsonl                            # labeled examples (built via claude-api:build-eval)
│   └── run_eval.py                              # calls the same classify_and_extract() as production
└── tests/
    ├── test_llm_client.py                       # NEW
    ├── test_pipeline_matching.py                 # NEW
    ├── test_pipeline_service.py                   # NEW
    └── test_pipeline_router.py                    # NEW

frontend/src/
├── types/
│   └── pipeline.ts                              # NEW — ReviewItem, ProcessResult
├── api/
│   └── pipeline.ts                              # NEW — processInbox(), getReviewQueue(), approve(), reject()
└── components/
    ├── ReviewQueue.tsx                           # NEW
    └── ApplicationsPage.tsx                      # MODIFIED — renders ReviewQueue + "Process Inbox" button
```

## 4. Data Model

### New table: `processed_messages`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | `UUID` | PK, default `uuid4` | |
| `user_id` | `UUID` | FK → `users.id`, not null, `ON DELETE CASCADE`, indexed | |
| `gmail_message_id` | `varchar(255)` | not null | unique together with `user_id` |
| `subject` | `varchar(998)` | not null | RFC 5322 max header length; same field Phase 3 already fetches |
| `sender` | `varchar(998)` | not null | the `From` header |
| `message_date` | `varchar(255)` | not null | the raw `Date` header string (display only, not parsed) |
| `snippet` | `text` | not null | Gmail's own snippet field — not the full body |
| `is_job_related` | `boolean` | not null | raw classification output |
| `confidence` | `float` | not null | raw confidence, 0.0-1.0 |
| `extracted_company` | `varchar(255)` | nullable | |
| `extracted_position` | `varchar(255)` | nullable | |
| `extracted_status` | `varchar(50)` | nullable | one of the `application_status` enum values, stored as text (not a DB enum FK — this is the raw LLM claim, not yet applied) |
| `extracted_status_date` | `date` | nullable | |
| `matched_application_id` | `UUID` | FK → `applications.id`, nullable, `ON DELETE SET NULL` | |
| `proposed_action` | `varchar(10)` | nullable, one of `create`/`update` | null when `is_job_related=False` |
| `review_status` | `varchar(20)` | not null | one of `auto_applied`/`ignored`/`pending_review`/`approved`/`rejected` |
| `created_at`/`updated_at` | `timestamptz` | not null, `TimestampMixin` | |

Unique constraint on `(user_id, gmail_message_id)` — the idempotency guarantee.

### Modified table: `applications`

- Add `source: varchar(10)`, not null, server default `'manual'` — existing rows correctly
  default to `manual` (no backfill logic needed; that's literally what they are).
- Add `'other'` to the `application_status` Postgres enum via `ALTER TYPE ... ADD VALUE`
  (additive — `withdrawn` and every existing value untouched).

### Migration `0005_add_pipeline_tables`

1. `ALTER TYPE application_status ADD VALUE 'other'` (must run in its own transaction
   block outside a `BEGIN` per Postgres's enum-alteration rules — Alembic's
   `op.execute()` with `autocommit_block()` or an equivalent non-transactional step).
2. Add `applications.source` (nullable first, backfill `'manual'`, then `SET NOT NULL` —
   the standard two-step pattern already used in migration `0003`).
3. Create `processed_messages` with its FKs, unique constraint, and indexes.

`downgrade()` reverses in the opposite order. (Postgres does not support removing an
enum value; `downgrade()` documents this limitation rather than attempting it, matching
how irreversible enum additions are commonly handled — this is called out explicitly so
the implementation plan doesn't silently omit it.)

## 5. Classification & Extraction

### The schema (`app/llm/schemas.py`)

```python
class EmailExtraction(BaseModel):
    is_job_related: bool
    confidence: float = Field(ge=0.0, le=1.0)
    company: str | None
    position: str | None
    status: Literal["applied", "oa", "interview", "rejected", "offer", "other"] | None
    status_date: date | None
    reasoning: str | None
```

One LLM call performs classification and extraction together — cheaper than two calls,
and for this use case a single confidence number is a meaningful enough signal to gate
"trust this or don't." `company`/`position`/`status`/`status_date` are `None` when
`is_job_related` is `False`; the system prompt instructs the model not to guess fields
for irrelevant email.

### The call (`app/llm/client.py`)

```python
class Extractor(Protocol):
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction: ...


class AnthropicExtractor:
    def classify_and_extract(self, *, subject, sender, date, body) -> EmailExtraction:
        response = self._client.messages.parse(
            model=settings.llm_model,
            max_tokens=1024,
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            output_format=EmailExtraction,
            messages=[{"role": "user", "content": f"Subject: {subject}\nFrom: {sender}\nDate: {date}\n\n{body}"}],
        )
        return response.parsed_output
```

Everything in `app/pipeline/` depends on the `Extractor` protocol, never on `anthropic`
directly.

### Body handling (`app/gmail/google_api.py` — extended, not replaced)

A new function `get_message_body(access_token, message_id) -> str` requests
`format=full`, decodes the MIME payload's plain-text part (falling back to a simple
HTML-tag strip if only an HTML part exists), strips common quoted-reply markers
(`^>`, "On ... wrote:") and truncates to ~4000 characters before returning. This is the
"send only the minimum necessary" step — the cleaned, truncated text is all that ever
reaches the LLM; the raw MIME structure and any attachments never leave Gmail.

## 6. Matching (`app/pipeline/matching.py`)

```python
def find_candidate(db: Session, user_id: UUID, extraction: EmailExtraction) -> MatchResult:
    # MatchResult: {application: Application | None, action: Literal["create","update"], ambiguous: bool}
```

1. Normalize `company` (lowercase, strip `inc`/`llc`/`corp`/punctuation/whitespace
   variants) and `position` similarly, on both the extraction and every existing
   `Application` for `user_id`.
2. Compute a `rapidfuzz.fuzz.ratio` score (0-100) on the normalized `"{company} {position}"`
   string, against each existing application for that user.
3. **Best score ≥ 85, and no other candidate within 10 points of it** → `action="update"`,
   that application.
4. **Best score < 60** → `action="create"`, no application (confidently nothing matches).
5. **Everything else** (best score 60-84, or a second candidate within 10 points of the
   top one) → `ambiguous=True` — never guessed; folded into the review-queue path (§7)
   exactly like low confidence. These three numbers (85 / 60 / 10) are a reasonable
   starting point, not a tuned result — revisit once the evaluation set (§10) shows
   where they're wrong.

## 7. Decision Logic & the Trust Model

```
extraction = llm.classify_and_extract(...)

if not extraction.is_job_related:
    review_status = "ignored"
elif extraction.status == "other" and matched_application already has a more specific status:
    review_status = "ignored"   # no new information — never regress a specific status to "other"
else:
    match = matching.find_candidate(db, user_id, extraction)
    high_confidence = extraction.confidence >= settings.llm_confidence_threshold
    touches_manual = match.application is not None and match.application.source == "manual"

    if touches_manual:
        review_status = "pending_review"          # ALWAYS — never auto-touch a manual row
    elif match.ambiguous or not high_confidence:
        review_status = "pending_review"
    elif match.action == "create":
        create Application(source="gmail", ...); review_status = "auto_applied"
    else:  # update, source == "gmail", confident, unambiguous
        update the matched Application's fields; review_status = "auto_applied"

persist ProcessedMessage(..., review_status=review_status, proposed_action=..., matched_application_id=...)
```

| Application's provenance | Confident + unambiguous match | Not confident, or ambiguous |
|---|---|---|
| No match (new) | Auto-create, `source="gmail"` | Queue as proposed create |
| Existing, `source="gmail"` | Auto-update | Queue as proposed update |
| Existing, `source="manual"` | **Always queue** | Queue |

A queued item's `approve` endpoint applies the exact proposed change (create or update);
an edit-then-approve request carries the corrected fields in the request body instead;
`reject` marks the row reviewed with no application change. **Manual editing of any
application remains available at all times**, independent of the pipeline — a user edit
simply becomes the new ground truth the next time matching runs.

## 8. API Contract

New router `app/pipeline/router.py`, mounted at `/api/v1/pipeline`, every endpoint behind
`Depends(get_current_user)`:

| Method | Path | Response | Notes |
|---|---|---|---|
| `POST` | `/api/v1/pipeline/process` | `200` `ProcessResult` | `{processed, auto_applied, queued_for_review, ignored}`; fetches up to `pipeline_batch_limit` new messages |
| `GET` | `/api/v1/pipeline/review` | `200` `list[ReviewItem]` | this user's `pending_review` rows only |
| `POST` | `/api/v1/pipeline/review/{id}/approve` | `200` `ApplicationRead` | optional body with edited fields; `404` if not found/not pending/not owned |
| `POST` | `/api/v1/pipeline/review/{id}/reject` | `204` | `404` if not found/not pending/not owned |

`ApplicationRead` (existing schema, modified) gains `source: Literal["manual", "gmail"]`.

## 9. Frontend Structure & Data Flow

```
ReviewQueue.tsx (rendered on the dashboard, below GmailPanel)
   │  on mount + after any action: GET /api/v1/pipeline/review
   │  each item: proposed action (create/update), extracted fields, confidence, subject/snippet for context
   │  Approve -> POST .../approve   Edit -> inline field edits, then Approve   Reject -> POST .../reject
   │
"Process Inbox" button (on ApplicationsPage, near GmailPanel)
   │  POST /api/v1/pipeline/process -> shows {processed, auto_applied, queued_for_review, ignored} summary
   │  then refetches applications + review queue
```

`api/pipeline.ts` follows the exact `parseResponse<T>` pattern established in
`api/auth.ts`/`api/gmail.ts`. `ApplicationRead`'s new `source` field lets the UI badge
auto-created rows (e.g. a small "via Gmail" tag) without changing the manual-edit flow.

## 10. Evaluation Strategy

Deferred to implementation time via the `claude-api` skill's `build-eval` workflow,
which runs a structured interview (what's graded, where labeled examples come from,
grading method, measured cost) and requires explicit sign-off before producing
anything — designing the dataset by hand now, with no real usage data yet, would be
guessing. The resulting shape: `backend/evaluation/dataset.jsonl` (labeled email
examples with expected `EmailExtraction` output) and `backend/evaluation/run_eval.py`,
which calls the *same* `classify_and_extract()` function production uses and reports
classification accuracy/precision/recall plus field-level extraction accuracy. This is
explicitly **not** part of the default `pytest` run — every run spends real API money —
and is invoked manually (`uv run python -m evaluation.run_eval`).

## 11. Security & Privacy Considerations

| Concern | Position |
|---|---|
| Data minimization | Only cleaned, truncated plain-text body (~4000 chars, HTML/quoted-replies/signatures stripped) is sent to Anthropic — never raw MIME, attachments, or more than needed. |
| Retention | The email body is never persisted anywhere — fetched, cleaned, sent, discarded. Only derived structured fields plus the Phase-3-approved subject/snippet live in the database. |
| Structured-output validation | `client.messages.parse(output_format=EmailExtraction)` guarantees a validated Pydantic instance; no unvalidated LLM text ever reaches `matching.py` or the database. |
| Manual-data protection | The trust-model table in §7 is structural, not a convention to remember — a `source="manual"` application is never in the auto-apply branch of the decision logic, full stop. |
| Cross-user isolation | Every `/pipeline/*` route requires `get_current_user`; `ProcessedMessage`/`Application` queries are always scoped to `user_id`; review-item ids are never trusted across users (`404`, not `403`, matching the existing `applications` convention). |
| Cost | `claude-opus-5`, effort `low`, ~1-2K input tokens/message → ~$0.005-0.01/message; `pipeline_batch_limit=20` bounds one "Process Inbox" click to roughly $0.10-0.20. No sustained/scheduled spend yet — Phase 5's concern. |
| Idempotency as a cost control | The unique `(user_id, gmail_message_id)` constraint means re-running "Process Inbox" never re-spends on an already-processed message. |
| Provider trust boundary | Standard Anthropic API terms govern data in transit/processing; no code-level control beyond minimizing what's sent — worth a one-line README note, not a design lever. |

## 12. Testing Plan

Following the project's established conventions: monkeypatch the `Extractor` protocol
(never call the real Anthropic API in tests), reuse `auth_client`/`other_auth_client`/
`user`/`other_user`/`db_session` fixtures.

- `test_llm_client.py`: `AnthropicExtractor.classify_and_extract` correctly maps a
  mocked `messages.parse` response into `EmailExtraction`; a malformed/refused response
  is handled without crashing (raises a typed error the pipeline can catch).
- `test_pipeline_matching.py`: exact match, no match, ambiguous multi-candidate match,
  normalization behavior (case/punctuation-insensitive company matching).
- `test_pipeline_service.py`: each cell of the §7 decision table — auto-create,
  auto-update on a `gmail`-sourced application, forced-review on a `manual`-sourced
  application regardless of confidence, low-confidence queuing, `other`-never-regresses
  behavior, idempotency (processing the same message id twice is a no-op the second
  time), approve/reject mutating `ProcessedMessage.review_status` and (for approve) the
  target `Application` correctly.
- `test_pipeline_router.py`: auth-required on all four endpoints, cross-user isolation
  (one user cannot see or act on another's review items), `/process` respects
  `pipeline_batch_limit`, `/review/{id}/approve` applies edited fields when provided.

## 13. Preserving Phase 1-3 Functionality

Nothing about login, Gmail connection, or existing manual CRUD changes. An
`ApplicationRead` response gains one additive field (`source`); every other field is
unchanged. A user who never clicks "Process Inbox" sees no behavior difference at all.
All Phase 1-3 tests continue to pass unmodified.
