# Job Application Tracker — Phase 6 Design: Classifier & Extraction Quality

**Date:** 2026-09-17
**Status:** Draft — awaiting approval
**Scope:** Improves the accuracy, extraction cleanliness, and confidence calibration of
the local rule-based classifier introduced in
`docs/superpowers/specs/2026-09-15-job-tracker-phase-4b-local-classifier-design.md`
("Phase 4b"), in response to concrete gaps found during Phase 5's real-mailbox manual
test (see CLAUDE.md, "Manual testing findings" and "Known gaps"). This is a **targeted
improvement**, not a rewrite: the classifier stays deterministic, local, and free —
no LLM/paid API of any kind. Phase 5's Gmail sync job queue/worker, and Phase 4's
matching/trust-model/review-queue behavior (including the fixed `0.85` auto-apply
threshold), are unchanged except for one narrow, explicitly-scoped fix inside Phase 5's
`gmail/google_api.py` body-cleaning step (§5.1) — everything else in those areas is out
of scope.

## 0. Why

Phase 5's manual test against a real ~6,500-message mailbox validated the sync
infrastructure but exposed that the classifier, calibrated only against an 18-example
curated dataset, behaves very differently on real mail: confidence averaged ~0.36 on
review-queued items (none crossed the 0.85 auto-apply bar), and some company extraction
picked up greeting/name text (e.g. "Anduril Hi Gia Huy" instead of "Anduril"). The
trust-model gate correctly caught every affected message and routed it to manual review
— nothing wrong was auto-applied — but the classifier is doing far less useful work than
it could. This document proposes a systematic fix: a harder, more representative
evaluation dataset; root-caused preprocessing and extraction fixes; measured (not
guessed) confidence recalibration; and a repeatable old-vs-new comparison methodology,
so future changes to `classifier/patterns.py` can be judged by evidence instead of
intuition.

## 1. Goal & Non-Goals

### Goal

Reduce the gap between curated-dataset performance and real-mailbox performance for the
existing `RuleBasedExtractor`: fewer extraction errors (especially company-name
boundary bleed), and confidence scores that let genuinely clear, correctly-extracted
real emails actually reach the existing `0.85` auto-apply threshold — without lowering
that threshold, and without introducing any paid API, network dependency, or trained ML
model.

### Non-Goals

- Changing `settings.classification_confidence_threshold` (stays `0.85`).
- Changing Phase 4's matching/trust-model logic (`pipeline/matching.py`,
  `pipeline/service.py::_apply_decision`) or the review-queue API/UI.
- Changing Phase 5's sync job queue, worker, retry/backoff, or reaper.
- Any statistical/ML model (Naive Bayes, embeddings, local NER, etc.) — explicitly
  ruled out in favor of better rules and real preprocessing, per direction for this
  phase.
- A stateful/learned sender-trust signal (e.g. "this domain was previously approved") —
  would need new infrastructure (a seen-sender table) beyond this phase's scope.
- Non-English email support (unchanged from Phase 4b's non-goals).

## 2. Current Architecture (as-is, for reference)

`RuleBasedExtractor.classify_and_extract` (`app/classifier/extractor.py`):

1. `text.combine_subject_body` joins subject+body with a space, collapses whitespace;
   `normalize_text` lowercases that for scoring.
2. `classify()` scores the normalized text against weighted regex tables in
   `patterns.py`: `STATUS_PATTERNS` (5 statuses × weighted phrases) produce a per-status
   score and an overall `job_signal`; `GENERIC_JOB_PATTERNS` add generic relatedness;
   `NEGATIVE_PATTERNS` subtract; a sender domain in `ATS_DOMAINS` adds a flat `+2` to
   `job_signal`.
3. `is_job_related = (job_signal - negative_signal) >= JOB_RELATED_THRESHOLD (3)`.
4. Confidence is additive, each component capped, then clamped to `[0, 1]`:
   `base` (≤0.6, from `min(net_signal / JOB_SIGNAL_NORM, 1) * 0.6`) + `margin` (≤0.3,
   from how clearly the top status score beat the runner-up) + `domain_bonus` (flat
   0.1, `ATS_DOMAINS` only) − `extraction_penalty` (0 / 0.15 / 0.15 / 0.35, by which
   tier found company/position — `max` of the two tiers' penalties).
5. Company/position extraction (`fields.py`): two anchored template regexes tried
   first (`"... the X position/role at Y"`, `"applying/application to Y"`); company
   falls back to a domain-derived guess (capitalized domain label, skipping
   `ATS_DOMAINS`) or a sender-display-name-derived guess; position has no fallback
   beyond the two templates.

### 2.1 Root causes of the two observed real-mailbox problems

**Confidence conservatism** (avg ~0.36, no auto-applies): `base` and `margin` only
saturate when a message hits several weighted phrases at once, and `domain_bonus` only
fires for the small `ATS_DOMAINS` allowlist. Most real recruiting mail is sent from a
company's own domain, phrased plainly, hitting one pattern once — capping `base` around
0.3 with no domain bonus available. The formula's ceiling structure was tuned
(Phase 4b Task 8) against template-phrased examples that legitimately saturate it; real
mail structurally can't.

**Company-extraction boundary bleed** (`"Anduril Hi Gia Huy"`): `fields.py`'s
`_COMPANY_TOKEN` (`(?-i:[A-Z][\w&'\-]*(?:\s[A-Z][\w&'\-]*){0,4})`) captures a run of
capitalized words, stopped only by punctuation, a few connector words, or
end-of-string. A greeting name is just as capitalized as a company name — text like
`"...at Anduril\n\nHi Gia Huy,"`, after whitespace collapsing, becomes
`"at Anduril Hi Gia Huy,"` with nothing but a space between "Anduril" and "Hi" for the
boundary lookahead to catch on. This is the same overcapture class the existing
tier-2 fallback logic already guards against for generic sentence continuations
(see the `"Thank you"` example documented in `fields.py`'s own comments) — it just
doesn't generalize to arbitrary greeting names.

**A third, not-yet-observed-but-verified gap**: `gmail/google_api.py`'s body cleaning
(`_clean_text`, lines ~148-155) already strips quoted-reply lines and HTML tags before
the classifier sees the text — this is existing Phase 3/5 infrastructure, not something
missing. But `_HTML_TAG_RE = re.compile(r"<[^>]+>")` strips tags only, not
`<style>`/`<script>` block *contents* — CSS/JS text inside those blocks survives as
visible text after tag-stripping and can pollute both scoring and extraction on any
HTML-body message. Confirmed by reading the regex, not yet observed in production
(the real-mailbox test didn't isolate this), but predictable and worth fixing
proactively since the new evaluation dataset (§4) will include HTML-body examples.

There is also no signature/footer stripping today at all (only quoted-reply lines and
HTML tags are removed) — a trailing "Best regards, Jane Recruiter, TalentCo" block or a
legal/unsubscribe footer can spuriously feed `NEGATIVE_PATTERNS` or
`GENERIC_JOB_PATTERNS`.

## 3. Current Evaluation Dataset — weaknesses

`evaluation/dataset.jsonl` has 18 hand-written examples (12 positive across all 5
statuses, 6 negative). Concretely:

- Every example is one clean paragraph — no HTML, no quoted threads, no
  signatures/footers, no greeting lines.
- The positive examples are phrased almost exactly like the two template regexes that
  extract from them — `fields.py`'s own comments cite "Task 7"/"Task 8" calibration
  against this *same* dataset, so passing it proves the regexes match what they were
  written to match, not that they generalize.
- No recruiter cold-outreach, no genuinely ambiguous items (every negative example is
  unambiguous spam/newsletter/unrelated — nothing borderline).
- No sender/domain diversity beyond the exact `ATS_DOMAINS` list vs. a plain company
  domain; no subdomain edge cases (e.g. `careers.company.com`), no mismatched
  display-name-vs-domain cases.
- No company/position names that stress the boundary logic — all are short
  one/two-word placeholder names, none adjacent to a greeting or containing
  punctuation like "Inc." mid-span.
- n=18 (n=11-12 per field) — `test_evaluation_accuracy.py`'s own comments admit the
  bars are set only to "tolerate one additional miss." Since it's the same dataset the
  constants were tuned against, it cannot detect overfitting to itself.

## 4. New Evaluation Dataset

A new, larger, hand-authored dataset (target: 80-120 examples — exact count driven by
category coverage below, not a fixed quota) replaces the sole reliance on the 18
existing examples. The 18 existing examples are kept as the "clean/template-like"
category (they're valid, just insufficient alone).

**Format**: same JSONL shape as today (`subject`, `sender`, `date`, `body`,
`expected: {is_job_related, company?, position?, status?}`), plus a new `category`
field for per-category metric breakdowns (§6).

**Categories** (each gets multiple examples, covering all 5 statuses where
applicable, plus negatives):

| Category | What it exercises |
|---|---|
| `clean_template` | Today's 18 examples — kept as a baseline-preserving floor. |
| `html_noise` | HTML-body messages, including `<style>`/`<script>` block content, to exercise the §5.1 fix. |
| `greeting_adjacent` | A greeting line immediately before or after a company/position mention, with realistic (not always punctuated) spacing — targets §2.1's boundary bug directly. |
| `signature_footer` | A trailing signature block or legal/unsubscribe footer after the real content. |
| `recruiter_outreach` | Cold outreach about a potential role, not a status update on an existing application — labeled `is_job_related=true, status="other"` (no schema change needed; see §5.4). |
| `ambiguous` | Job-adjacent but not a real status update (e.g. a newsletter that happens to contain update-like phrasing) — labeled negative, tests that `NEGATIVE_PATTERNS`/threshold correctly reject it. |
| `sender_variation` | Subdomains (`careers.`, `jobs.`, `talent.`), mismatched display name vs. domain, non-`ATS_DOMAINS` assessment platforms. |
| `messy_phrasing` | Realistic non-template phrasing that a human would still recognize instantly, but doesn't hit either anchor regex — tests confidence recalibration, not just extraction. |

All examples are synthetic, hand-authored by me — no real mailbox content, no privacy
concerns, per your answer to keep this fully synthetic.

## 5. Proposed Improvements

### 5.1 Preprocessing (new `app/classifier/preprocess.py`)

A new stage applied to subject/body before `normalize_text`/extraction:

- Strip leading greeting lines (`"Hi X,"`, `"Dear X,"`, `"Hello,"`) — the primary fix
  for the boundary-bleed bug, since it removes the greeting text entirely before the
  company/position regexes ever see it, rather than trying to patch the regex to guess
  where a greeting starts.
- Truncate at a detected signature/footer boundary (a line that's just `"--"`, or a
  closing word like `"Best,"/"Regards,"/"Sincerely,"` followed by a short name-like
  line, or a long trailing legal/unsubscribe block).
- Normalize Unicode punctuation variants (smart quotes, non-breaking spaces, em/en
  dashes) to ASCII equivalents — patterns are written against literal ASCII and would
  silently miss variants otherwise.

Plus one narrow, explicitly-scoped fix **inside Phase 5's `gmail/google_api.py`**
(the one exception to "Phase 5 is unchanged"): extend the HTML cleaning in
`_clean_text` to strip `<style>...</style>` and `<script>...</script>` blocks
*including their contents*, not just tags, before the existing tag-stripping regex
runs. This is a mechanical bug in existing MIME-cleaning code that directly corrupts
classifier input — narrow enough to fix here rather than deferring to a hypothetical
future Phase 5 revision.

### 5.2 Company extraction (`fields.py`)

- Add a stopword safety net: reject a captured span if it's composed entirely of known
  greeting/closing words (`hi`, `hello`, `dear`, `thanks`, `thank`, `best`, `regards`,
  `sincerely`, `cheers`) even if the regex matched — belt-and-suspenders on top of
  §5.1's preprocessing, since a greeting could still appear mid-line without a full
  line break.
- Expand anchor templates beyond the current two (`"... position at Y"`,
  `"applying to Y"`) to cover phrasings observed in the new dataset (e.g. "your
  application with Y", "at Y, we...", "the Y team") — driven by dataset misses at
  checkpoint 3 (§7), not speculative additions.
- Extend `text._SUBDOMAIN_PREFIXES` to include `careers.`, `jobs.`, `talent.`,
  `recruiting.` — currently a sender on `careers.company.com` produces "Careers" as
  the domain-derived company guess, a real bug found while reading the code.

### 5.3 Position extraction (`fields.py`)

- Add fallback tiers mirroring company's (currently template-or-`None`), with new
  entries in `EXTRACTION_PENALTY` for each new tier.
- Expand anchor templates the same dataset-miss-driven way as company.

### 5.4 Status classification (`patterns.py`)

- Add `STATUS_PATTERNS`/`GENERIC_JOB_PATTERNS` coverage, but **only entries driven by
  measured misses on the new dataset** (checkpoint 4, §7) — re-run the eval after each
  addition to confirm it helps without introducing new false positives on the
  `ambiguous`/negative categories.
- `recruiter_outreach` examples need no schema change — `EmailExtraction.status`
  already accepts `"other"`; they just need dataset coverage and confirmation that
  `is_job_related` correctly reaches `true` for that phrasing without misclassifying
  a genuine status.

### 5.5 Sender/domain signals

- The `_SUBDOMAIN_PREFIXES` fix above (§5.2) is the only change here.

### 5.6 Confidence calibration (`patterns.py` constants)

After §5.1-5.4 land, re-derive `JOB_SIGNAL_NORM`, `MARGIN_NORM`, and weights using the
same measure → adjust one constant → re-measure method Phase 4b's Task 8 used —
now against the harder dataset, so the recalibrated constants aren't optimistic about
template-perfect phrasing. Since `0.85` is fixed, the calibration goal is specifically:
let genuinely-clear, correctly-extracted real emails legitimately earn `0.85` — not
lower the bar. Every recalibration change must be justified by measured
precision-at-threshold (§6) on the new dataset, not just a higher average confidence.

## 6. Metrics

| Metric | What it tells us |
|---|---|
| Precision / recall / F1 for `is_job_related` (binary) | Recall matters most here — a false negative silently drops a real job email before it ever reaches review. Precision matters less (a false positive just becomes a nuisance review item), but is still tracked. |
| Per-status precision/recall/F1 (5-way, macro + per-class) | Whether the classifier confuses e.g. `rejected` vs. `interview` — invisible in a single blended accuracy number. |
| Company/position exact-match accuracy | Direct correctness. |
| Company/position fuzzy-match accuracy (via `rapidfuzz`, already used in `pipeline/matching.py`) | Separates real extraction errors from formatting noise (trailing punctuation, etc.) so a regression in exact-match alone doesn't look like a real accuracy loss. |
| **Precision at threshold** (of items scoring ≥0.85, what fraction are fully correct — `is_job_related` + status + company + position all correct) | The single most important number: it's what actually guards the auto-apply trust-model gate. Must not regress. |
| Auto-apply rate (fraction of true positives clearing 0.85) | The "conservatism" measure this phase exists to move — but only meaningful reported *alongside* precision-at-threshold; raising this while precision-at-threshold drops is a regression dressed as progress. |
| Review rate (fraction routed to `pending_review`) | Expected to stay high for genuinely ambiguous/messy items — the goal is shifting *clear* items out of review, not eliminating review. |

All metrics reported both overall and broken out per `category` (§4) — a single
blended number would hide exactly the "does badly on real mail" gap this phase exists
to close.

## 7. Comparison Methodology & Checkpoints

**Baseline**: before any classifier code changes, run today's unmodified
`RuleBasedExtractor` against the *new* dataset via `run_eval.py` and record the
resulting metrics as the documented baseline (also validates the new dataset is
actually harder than the old one — expected to score noticeably worse than on the old
18-example set). After each checkpoint below, re-run the same script against the same
dataset and diff against the baseline, so a regression is caught at the checkpoint that
introduced it. `tests/test_evaluation_accuracy.py`'s bar-gating pattern is kept, rebased
on the new dataset's numbers at the final checkpoint.

1. **Dataset + baseline.** Write the new dataset (§4). Run today's extractor against it,
   record baseline metrics. No production code changes.
2. **Preprocessing.** `classifier/preprocess.py` (§5.1) + the `gmail/google_api.py`
   style/script fix. Re-measure, diff against baseline.
3. **Company/position extraction.** §5.2-5.3. Re-measure, diff.
4. **Status pattern coverage.** §5.4, dataset-miss-driven only. Re-measure, diff.
5. **Confidence recalibration.** §5.6, with explicit before/after precision-at-threshold.
   Re-measure, diff.
6. **Final report + regression gate update.** Update `test_evaluation_accuracy.py`
   bars to the new dataset's numbers (same "tolerate one more miss" philosophy). Append
   a before/after metrics summary to CLAUDE.md's Phase 6 section.

Each checkpoint keeps existing unit tests (`tests/test_classifier_*.py`) extended
alongside its change, and the full backend suite green. No frontend changes are
anticipated in any checkpoint.
