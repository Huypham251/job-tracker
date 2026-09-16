# Job Application Tracker — Phase 4b Design: Local Rule-Based Classification & Extraction

**Date:** 2026-09-15
**Status:** Draft — awaiting approval
**Scope:** Replaces the LLM-backed classification/extraction layer from
`docs/superpowers/specs/2026-09-15-job-tracker-phase-4-pipeline-design.md` ("Phase 4")
with a local, deterministic, rule-based implementation that requires no paid API and no
API key at runtime. This is a **targeted supersession**, not a rewrite: every other part
of Phase 4 — Gmail message retrieval, fuzzy matching/dedup, the trust model, the review
queue, the API contract, the frontend, and the database schema — is unchanged and stays
governed by the original Phase 4 spec. Sections below that are unchanged from Phase 4 are
marked **(unchanged, reproduced for completeness)**; sections that replace Phase 4
content are marked **(supersedes Phase 4 §N)**.

## 0. Why

The original design used Anthropic's Claude API for classification/extraction, with an
explicit abstraction (`app/llm/`) so a future phase could add/swap providers. Before that
abstraction was ever exercised with a real API call, the requirement changed: the
production pipeline must not depend on any paid LLM API, and must not require an API key
to run at all — not even a mocked one for development, since the goal is a fully local,
free, deterministic classifier in production. This document proposes what replaces the
`app/llm/` package.

## 1. Goal & Non-Goals

### Goal (unchanged from Phase 4 §1, restated)

Classify a fetched Gmail message as job-application-related or not; if relevant, extract
company/position/status/date/confidence; match against existing applications; auto-apply
when confident, otherwise queue for human review; never silently overwrite manually
entered data; support ongoing quality measurement via an evaluation set.

### New goal for this document

Do all of the above with **zero external API calls, zero API keys, and $0 marginal cost
per email**, using deterministic, explainable, multi-signal scoring — not a single
keyword lookup.

### Non-Goals

Everything Phase 4 already deferred to Phase 5 (Redis, Celery, scheduling, rate
limiting, monitoring) remains deferred. Additionally out of scope for this redesign:
non-English email support, semantic/ML-based classification (e.g. a local embedding
model) — the requirement is specifically a rule-based system, not a smaller model.

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Classification approach | Weighted multi-signal scoring over regex/phrase pattern tables, sender-domain signals, and negative (non-job) signals | Single-keyword matching was explicitly ruled out; a weighted score across independent signal categories is both explainable (each contributing signal is inspectable) and resistant to any one false-positive phrase. |
| Extraction approach | Ordered fallback: template regex over subject/body → sender-domain-derived name (blocklisting known ATS platforms) → sender display-name heuristic → give up (`None`) | Mirrors how a human skims these emails: look for the boilerplate phrase first, then the sender identity, before admitting defeat. Each tier is strictly less certain than the last, and that uncertainty is fed back into the confidence score. |
| Confidence model | Deterministic formula: relatedness strength + status-margin clarity + domain bonus − extraction-uncertainty penalty, each component capped, summed, then clamped to `[0, 1]` | Same one-float contract the rest of the pipeline (trust model, review-queue gating) already depends on — no change needed to `_apply_decision`. Fully reproducible from the same input every time. |
| Package location | New `app/classifier/` package replaces `app/llm/` | `app/pipeline/` depends only on the `Extractor` Protocol (unchanged) and `EmailExtraction` (unchanged shape) — swapping the concrete implementation and its home package is the entire migration surface. |
| Weight/threshold values | Reasoned starting defaults, explicitly tunable | Requirement #8 (build an evaluation dataset to measure and tune) is taken literally: the constants in `patterns.py` are a first cut, not a final answer — §8 below makes that tuning loop concrete and free to run repeatedly. |
| Matching library | `rapidfuzz` **(unchanged from Phase 4 §2)** | Already in use for dedup matching against existing applications; also reused here as a secondary corroboration signal for extracted company/position (§5). |
| Confidence threshold setting name | `classification_confidence_threshold` (renamed from `llm_confidence_threshold`) | The old name is now inaccurate — there is no LLM. Same type/default (`float`, `0.85`), same role in `_apply_decision`. |
| Evaluation dataset | Hand-built now (not deferred), since it costs nothing to run | Phase 4's original plan deferred the eval dataset to `claude-api:build-eval` specifically because each run spent real API money and there was no usage data yet. Neither constraint applies to a local classifier — the dataset can be built immediately and re-run on every CI run as a real regression gate (§8). |

## 3. Project Structure — additions, changes, deletions

```
backend/
├── app/
│   ├── core/
│   │   └── config.py                          # MODIFIED — drop anthropic_api_key, llm_model;
│   │                                             rename llm_confidence_threshold ->
│   │                                             classification_confidence_threshold
│   ├── llm/                                    # DELETED — entire package
│   ├── classifier/                              # NEW package — replaces app/llm/
│   │   ├── __init__.py
│   │   ├── schemas.py                          # EmailExtraction (Pydantic) — moved, unchanged shape
│   │   ├── extractor.py                        # Extractor Protocol, ClassificationError, RuleBasedExtractor
│   │   ├── patterns.py                         # status phrase tables, ATS/job-board domain lists,
│   │   │                                         negative-signal patterns, all weights as named constants
│   │   ├── fields.py                           # company/position regex extraction + fallback tiers
│   │   └── text.py                             # normalization + sender-domain parsing helpers
│   └── pipeline/                                # UNCHANGED except import paths
│       ├── service.py                          # MODIFIED — import from app.classifier.*, rename
│       │                                         LLMExtractionError -> ClassificationError; no logic changes
│       └── router.py                           # MODIFIED — instantiate RuleBasedExtractor instead of
│                                                  AnthropicExtractor
├── pyproject.toml                               # MODIFIED — remove `anthropic`; rapidfuzz unchanged
├── evaluation/
│   ├── dataset.jsonl                            # MODIFIED — expanded to realistic examples, all 6
│   │                                             statuses + non-job categories (see §8)
│   └── run_eval.py                              # MODIFIED — calls RuleBasedExtractor instead of
│                                                  AnthropicExtractor; still runnable standalone
└── tests/
    ├── test_llm_client.py                       # DELETED
    ├── test_classifier_extractor.py              # NEW — classification scoring, confidence determinism
    ├── test_classifier_fields.py                  # NEW — regex extraction + each fallback tier
    ├── test_evaluation_accuracy.py                # NEW — runs dataset.jsonl through RuleBasedExtractor,
    │                                                asserts a minimum accuracy; part of the normal
    │                                                pytest run now that it costs nothing
    └── test_pipeline_router.py                    # MODIFIED — monkeypatch target updated

.env.example                                       # MODIFIED — remove ANTHROPIC_API_KEY block
README.md                                          # MODIFIED — rewrite Phase 4 setup section: no key,
                                                     no billing; document the local classifier + eval
```

Everything under `app/gmail/`, `app/pipeline/matching.py|models.py|schemas.py|exceptions.py`,
`app/applications/`, `alembic/versions/0005_add_pipeline_tables.py`, and the entire
`frontend/` tree is **unmodified** by this document — see §9.

## 4. Data Model

**(unchanged from Phase 4 §4 — reproduced for completeness, no changes)**

The `processed_messages` table, the `applications.source` column, the `'other'` status
enum value, and migration `0005` are all provider-agnostic already: `confidence` is a
plain `float`, `extracted_status` is a plain string, and nothing in the schema encodes
"this came from an LLM." No new migration is needed for this document's changes — the
`classification_confidence_threshold` rename is an application-config change only, not a
database change.

## 5. Classification & Extraction (supersedes Phase 4 §5)

### The schema (`app/classifier/schemas.py`) — unchanged shape, moved package

```python
class EmailExtraction(BaseModel):
    is_job_related: bool
    confidence: float = Field(ge=0.0, le=1.0)
    company: str | None = Field(default=None, max_length=255)
    position: str | None = Field(default=None, max_length=255)
    status: Literal["applied", "oa", "interview", "rejected", "offer", "other"] | None = None
    status_date: date | None = None
    reasoning: str | None = None  # populated with a short explanation of which
                                    # signals fired, for debuggability — see §5.4
```

### 5.1 Normalization (`app/classifier/text.py`)

- `normalize_text(subject, body) -> str`: lowercase, collapse whitespace, concatenate
  subject + body for pattern scanning (body already arrives HTML-stripped and truncated
  from `app/gmail/google_api.py`, unchanged).
- `extract_sender_domain(sender: str) -> str`: pull the email address out of a `"Display
  Name <addr@domain.com>"` or bare `addr@domain.com` sender header, lowercase the
  registrable domain (drop common subdomains like `mail.`, `notifications.`, `e.`).
- `extract_sender_display_name(sender: str) -> str | None`: pull the `"Display Name"`
  portion when present.

### 5.2 Classification scoring (`app/classifier/extractor.py` + `patterns.py`)

Pattern tables in `patterns.py`, each entry `(compiled_regex, weight)`:

- `STATUS_PATTERNS: dict[Literal["applied","oa","interview","rejected","offer"], list[(Pattern, int)]]`
  — e.g. `applied`: `r"thank you for applying"` (3), `r"application (has been )?received"` (3),
  `r"successfully applied"` (2); `oa`: `r"online assessment"` (3), `r"coding (challenge|test)"` (3),
  `r"hackerrank|codesignal"` (3), `r"take.?home"` (2); `interview`: `r"schedule (a|your) (call|interview)"` (3),
  `r"invite you to interview"` (3), `r"phone screen"` (3), `r"\binterview\b"` alone (1, weak — overlaps
  with `oa`); `rejected`: `r"unfortunately"` (2), `r"will not be moving forward"` (3),
  `r"other candidates"` (2), `r"regret to inform"` (3); `offer`: `r"pleased to offer"` (3),
  `r"offer of employment"` (3), `r"extend an offer"` (3).
- `GENERIC_JOB_PATTERNS: list[(Pattern, int)]` — relatedness boosters that don't imply a
  specific status: `r"your application"` (1), `r"\bposition\b"` (1), `r"\bcandidate\b"` (1),
  `r"recruiting team"` (1), `r"talent (acquisition|team)"` (1).
- `NEGATIVE_PATTERNS: list[(Pattern, int)]` — subtract from relatedness: `r"unsubscribe"` (2),
  `r"view (this|in) browser"` (2), `r"%\s*off"` (2), `r"jobs matching your search"` (3),
  `r"new jobs? for you"` (3), `r"recommended jobs"` (2) — this is what correctly excludes
  LinkedIn/Indeed job-alert digests, which mention "job" and "position" constantly without
  being about an application the user submitted.
- `ATS_DOMAINS: frozenset[str]` — `greenhouse.io`, `lever.co`, `myworkday.com`, `icims.com`,
  `smartrecruiters.com`, `ashbyhq.com`, `workable.com`, `jobvite.com`, `bamboohr.com`,
  `taleo.net`. Used both as a relatedness bonus (§ scoring) and as a **blocklist** for
  domain-derived company extraction (§5.3 — a greenhouse.io sender is the platform, not
  the employer).

Scoring algorithm:

```python
def classify(text: str, sender_domain: str) -> tuple[dict[str, int], int, int]:
    """Returns (status_scores, job_signal, negative_signal)."""
    status_scores = {status: 0 for status in STATUS_PATTERNS}
    job_signal = 0
    for status, patterns in STATUS_PATTERNS.items():
        for pattern, weight in patterns:
            if pattern.search(text):
                status_scores[status] += weight
                job_signal += weight
    for pattern, weight in GENERIC_JOB_PATTERNS:
        if pattern.search(text):
            job_signal += weight
    negative_signal = sum(weight for pattern, weight in NEGATIVE_PATTERNS if pattern.search(text))
    if sender_domain in ATS_DOMAINS:
        job_signal += DOMAIN_RELATEDNESS_BONUS  # = 2
    return status_scores, job_signal, negative_signal
```

```python
JOB_RELATED_THRESHOLD = 3   # net_signal >= this => is_job_related
JOB_SIGNAL_NORM = 6.0       # net_signal at/above this maps to full 0.6 relatedness contribution
MARGIN_NORM = 4.0           # status-score margin at/above this maps to full 0.3 clarity contribution
DOMAIN_CONFIDENCE_BONUS = 0.1

net_signal = job_signal - negative_signal
is_job_related = net_signal >= JOB_RELATED_THRESHOLD

if is_job_related:
    ranked = sorted(status_scores.items(), key=lambda kv: kv[1], reverse=True)
    top_status, top_score = ranked[0]
    runner_up_score = ranked[1][1]
    status = top_status if top_score > 0 else "other"
else:
    status = None
    top_score = runner_up_score = 0
```

### 5.3 Field extraction (`app/classifier/fields.py`)

Only attempted when `is_job_related` is `True`. Ordered tiers, each function returns
`(value, tier)` or `None`:

1. **Template regex** over subject+body (original case preserved, unlike the lowercased
   scoring text) — e.g. `r"applying to ([A-Z][\w&.,'\- ]{1,50})"`,
   `r"application for (?:the )?(.+?) position at ([A-Z][\w&.,'\- ]{1,60})"` (captures both
   company and position in one match), `r"for the (.+?) (?:position|role)"`. Tier =
   `"template"` (no confidence penalty).
2. **Sender-domain-derived company** — only if `sender_domain not in ATS_DOMAINS`: strip
   TLD, title-case the remaining label (`acme.com` → `Acme`). Position is not derivable
   this way — stays `None` if template extraction didn't find one. Tier = `"domain"`
   (penalty applies).
3. **Sender display-name heuristic** — strip trailing role words (`Recruiting`, `Talent`,
   `Careers`, `HR`, `Team`) from the parsed display name. Tier = `"display_name"` (penalty
   applies).
4. **Give up** → `None`. Counts as a missing field for the confidence penalty (§5.4).

`find_company(text, sender) -> tuple[str | None, str]` and
`find_position(text, sender) -> tuple[str | None, str]` each return the extracted value
and which tier produced it (or `"none"`), so the confidence calculation and `reasoning`
field can both consume that information without re-deriving it.

### 5.4 Confidence (deterministic, three additive capped components minus a penalty)

```python
EXTRACTION_PENALTY = {"template": 0.0, "domain": 0.15, "display_name": 0.15, "none": 0.35}

base = min(net_signal / JOB_SIGNAL_NORM, 1.0) * 0.6
margin = min((top_score - runner_up_score) / MARGIN_NORM, 1.0) * 0.3 if is_job_related else 0.0
domain_bonus = DOMAIN_CONFIDENCE_BONUS if sender_domain in ATS_DOMAINS else 0.0
penalty = max(EXTRACTION_PENALTY[company_tier], EXTRACTION_PENALTY[position_tier]) if is_job_related else 0.0

confidence = max(0.0, min(1.0, base + margin + domain_bonus - penalty))
```

When `is_job_related` is `False`, `company`/`position`/`status`/`status_date` are all
`None` (matching the original contract), and `confidence` reflects only how clearly
non-job-related the message scored (`base` computed from the negative margin, floored at
0 — a strongly-negative message gets a confident `False` classification, not a
low-confidence guess).

`reasoning` is populated with a short machine-generated string, e.g.
`"matched: applied phrase 'thank you for applying' (+3); company via template; position via template"`
— purely for debuggability (visible in the review queue and logs), not used by any
downstream logic.

**Calibration note:** the weights/norms above are a reasoned first cut, worked through
by hand against a couple of example emails, not a tuned result. A worked example: a
clearly-worded "thank you for applying" email with a template-matched company scores
`base=0.5, margin=0.225, domain_bonus=0, penalty=0` → **confidence ≈ 0.72** — below the
`classification_confidence_threshold` default of `0.85` inherited from Phase 4. That
default was calibrated for an LLM's self-reported confidence semantics, which don't
transfer automatically to this scoring formula. Implementation must not treat `0.85`,
or any of the weights/norms in §5.2/§5.4, as fixed: Task work for this document includes
running the evaluation dataset (§8) through the formula, observing where confident-correct
and uncertain-or-wrong extractions actually fall, and adjusting the weights, norms, and
`classification_confidence_threshold`'s default together so that clearly-worded,
correctly-extracted emails clear the auto-apply bar and genuinely ambiguous ones don't.
The exact final values belong in the implementation plan/PR, not this design doc.

### 5.5 The `Extractor` Protocol and implementation (`app/classifier/extractor.py`)

```python
class Extractor(Protocol):
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction: ...


class ClassificationError(Exception):
    """Raised only on a malformed/unusable input the classifier cannot process."""


class RuleBasedExtractor:
    def classify_and_extract(self, *, subject, sender, date, body) -> EmailExtraction:
        text = normalize_text(subject, body)
        sender_domain = extract_sender_domain(sender)
        status_scores, job_signal, negative_signal = classify(text, sender_domain)
        # ... apply the §5.2 threshold, §5.3 field extraction, §5.4 confidence ...
        return EmailExtraction(...)
```

Same signature as the original `AnthropicExtractor.classify_and_extract` — `app/pipeline/`
needs no interface changes, only an import-path change and renaming the caught exception
from `LLMExtractionError` to `ClassificationError` in `service.py`'s
`except (google_api.GoogleApiError, ClassificationError):` clause.

## 6. Matching (`app/pipeline/matching.py`)

**(unchanged from Phase 4 §6 — reproduced for completeness, no changes)**

`find_candidate` and its 85/60/10 thresholds are reused exactly as-is: normalize company
+ position, `rapidfuzz.fuzz.ratio` against every existing application for the user,
`create` / `update` / `ambiguous` outcome. This also serves, informally, as a
corroboration signal for extraction quality — a `RuleBasedExtractor` guess that happens
to fuzzy-match an existing application strongly is more likely a correct extraction than
a coincidental regex hit, though this document does not feed that signal back into the
confidence score (kept as a documented future refinement, not implemented now — see §7
weaknesses).

## 7. Decision Logic & the Trust Model

**(unchanged from Phase 4 §7 — reproduced for completeness, no changes)**

`_apply_decision` in `app/pipeline/service.py` is untouched: the same
`source == "gmail"` auto-mutable rule, the same high-confidence gate (now checking
`settings.classification_confidence_threshold` instead of
`settings.llm_confidence_threshold`), the same ambiguous-match → review routing, and the
same never-regress-a-specific-status-to-"other" guard. None of this logic reasons about
*how* `EmailExtraction` was produced — only about its output shape and confidence float —
which is exactly why this redesign doesn't need to touch it.

## 8. Evaluation Strategy (supersedes Phase 4 §10)

Phase 4 deferred the evaluation dataset because every run spent real Anthropic API money
and no usage data existed yet to design against. Neither constraint applies to a local
classifier: it costs nothing to run, so the dataset gets built now and wired into the
normal test suite as a real regression gate — this directly satisfies requirement #8
("build an evaluation dataset ... tune rules/thresholds based on evidence").

- `backend/evaluation/dataset.jsonl` expands to realistic, synthetic (not real-user)
  examples: at least 3-4 per status (`applied`, `oa`, `interview`, `rejected`, `offer`),
  several `other` (clearly job-related, no clear stage — e.g. "we've received your
  application and will review it" with no status verb), and a non-job set covering the
  categories that matter most: a marketing newsletter, a LinkedIn/Indeed job-alert digest
  ("5 new jobs matching your search"), an unrelated personal email, an order/shipping
  receipt, and a third-party notice ("your friend applied to ..."). Each entry keeps the
  existing shape: `{subject, sender, date, body, expected: {is_job_related, company,
  position, status}}`.
- `backend/evaluation/run_eval.py` calls `RuleBasedExtractor().classify_and_extract()` —
  the same function production uses — and reports classification accuracy (is_job_related
  + status match) and field-level accuracy (company/position match, normalized). Still
  runnable standalone: `uv run python -m evaluation.run_eval`.
- **New:** `backend/tests/test_evaluation_accuracy.py` runs the same dataset through the
  same extractor inside the normal `pytest` run and asserts a minimum accuracy threshold
  (e.g. ≥ 0.8 on the initial hand-built set — the exact bar is set once the dataset
  exists and the first run's actual numbers are known, at implementation time). This
  becomes the concrete mechanism for "tune rules/thresholds based on evidence": a
  weight/threshold change in `patterns.py` that regresses accuracy fails CI immediately.

## 9. Migration Plan — what's deleted, what's untouched

**Deleted:** `app/llm/__init__.py`, `client.py`, `prompts.py`, `schemas.py`;
`tests/test_llm_client.py`; the `anthropic` dependency from `pyproject.toml`;
`ANTHROPIC_API_KEY` from `.env.example` and `backend/.env`; `settings.anthropic_api_key`
and `settings.llm_model` from `app/core/config.py`.

**Added:** `app/classifier/` (5 files), `tests/test_classifier_extractor.py`,
`tests/test_classifier_fields.py`, `tests/test_evaluation_accuracy.py`.

**Modified (import/wiring only, no logic change):** `app/pipeline/service.py` (import
paths, exception rename), `app/pipeline/router.py` (instantiate `RuleBasedExtractor`),
`app/core/config.py` (drop two fields, rename one), `pyproject.toml`, `.env.example`,
`README.md`, `evaluation/run_eval.py`, `evaluation/dataset.jsonl`,
`tests/test_pipeline_router.py` (monkeypatch target).

**Entirely untouched:** `app/gmail/` (all of Phase 3 plus `get_message_body`/
`get_message_summary` from Phase 4), `app/pipeline/matching.py|models.py|schemas.py|exceptions.py`,
`app/applications/`, `alembic/versions/0005_add_pipeline_tables.py`, every file under
`frontend/`.

**Sequencing:** build and test the new `app/classifier/` package fully in isolation
first (it has no dependency on `app/pipeline/` or `app/llm/`), then switch
`service.py`/`router.py` imports over in one small task, verify the full test suite is
green, and only then delete `app/llm/` and the `anthropic` dependency — so there is never
a window where the pipeline is broken.

## 10. Test Migration

| Existing test file | Disposition |
|---|---|
| `test_llm_client.py` | Deleted — replaced by `test_classifier_extractor.py` + `test_classifier_fields.py` |
| `test_pipeline_matching.py` | Unchanged — no dependency on the extractor implementation |
| `test_pipeline_models.py` | Unchanged |
| `test_pipeline_service.py` | Unchanged — already exercises `_apply_decision` via a fake/stub `Extractor` per the Protocol, never the real Anthropic client |
| `test_pipeline_review.py` | Unchanged |
| `test_applications_source.py` | Unchanged |
| `test_pipeline_router.py` | One-line change: monkeypatch `app.classifier.extractor.RuleBasedExtractor` instead of `app.llm.client.AnthropicExtractor` |

New: `test_classifier_extractor.py` (per-status scoring cases, the digest/newsletter
negative-signal cases, confidence determinism — same input always produces the same
output), `test_classifier_fields.py` (each extraction tier exercised independently, plus
the ATS-domain blocklist behavior), `test_evaluation_accuracy.py` (§8).

## 11. Security & Privacy Considerations (supersedes Phase 4 §11 where noted)

| Concern | Position |
|---|---|
| Data minimization | **Stronger than Phase 4**: email body text never leaves the process — no external network call happens for classification at all. `app/gmail/google_api.py`'s cleaning/truncation still applies (defense in depth, unchanged), but there is no third-party data-processing agreement to reason about for this step. |
| Retention | Unchanged from Phase 4 §11 — body never persisted; only derived fields + subject/snippet. |
| Structured-output validation | Unchanged in spirit, different mechanism: `RuleBasedExtractor` constructs `EmailExtraction(...)` directly (Pydantic validates on construction) rather than via SDK-parsed output — same guarantee, no unvalidated data reaches `matching.py` or the database. |
| Manual-data protection | Unchanged from Phase 4 §11 — structural, in `_apply_decision`, independent of extraction method. |
| Cross-user isolation | Unchanged from Phase 4 §11. |
| Cost | **$0 marginal cost per message, no API key, no billing setup, no rate limits to worry about.** `pipeline_batch_limit` is retained as a sane UX cap (avoid one click processing thousands of messages), not a cost control. |
| Provider trust boundary | **Eliminated** — no third-party API is in the data path for classification/extraction. |

## 12. Global Constraints for the implementation plan

(For the forthcoming plan document — collected here so both documents agree on exact
values.)

- Config field rename: `llm_confidence_threshold` → `classification_confidence_threshold`,
  same type (`float`). **Its default is not fixed at `0.85`** — see the §5.4 calibration
  note; the implementation task that builds the eval dataset must set the final default
  from evidence and document why.
- `settings.anthropic_api_key` and `settings.llm_model` are removed entirely — no
  placeholder, no optional field. Starting the backend must require zero API keys beyond
  what Phases 1-3 already need (Google OAuth, Gmail encryption key, JWT secret).
- `JOB_RELATED_THRESHOLD = 3`, `JOB_SIGNAL_NORM = 6.0`, `MARGIN_NORM = 4.0`,
  `DOMAIN_CONFIDENCE_BONUS = 0.1`, `DOMAIN_RELATEDNESS_BONUS = 2` — starting constants,
  named and centralized in `app/classifier/patterns.py`, not hardcoded inline. Same
  calibration caveat as the confidence threshold above: adjust against the eval dataset,
  don't ship these untested.
- `EXTRACTION_PENALTY` tiers: `template=0.0`, `domain=0.15`, `display_name=0.15`, `none=0.35` — same caveat.
- `ATS_DOMAINS` list (§5.2) is the authoritative blocklist for domain-derived company
  extraction and the allowlist for the relatedness/confidence domain bonus — one list,
  two uses.
- `Extractor` Protocol signature is unchanged: `classify_and_extract(self, *, subject: str, sender: str, date: str, body: str) -> EmailExtraction`.
- No database migration is part of this document's implementation.

## 13. Preserving Phase 1-4 Functionality

Nothing about login, Gmail connection, existing manual CRUD, the review queue UI, the
API contract, or the database schema changes. A user who already has `ProcessedMessage`
rows or applications with `source="gmail"` sees no data loss or behavior change — the
same decision table in §7 governs them, now driven by locally-computed `EmailExtraction`
values instead of Anthropic-returned ones. All Phase 1-4 tests other than the ones listed
in §10 continue to pass unmodified.
