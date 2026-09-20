# Job Application Tracker — Phase 7 Design: Classifier Extraction Fixes

**Date:** 2026-09-20
**Status:** Draft — awaiting approval
**Scope:** Fixes four concrete, real-mail extraction/classification gaps found during
Phase 6's manual testing against a real Gmail account (see CLAUDE.md, "Phase 6 manual
testing findings" and "Known gaps"). This is a **small, targeted fix set**, not a
rewrite — same deterministic, local, rule-based classifier from Phase 4b/6, same
`0.85` auto-apply threshold, same matching/trust-model/review-queue behavior. Only
`app/classifier/text.py`, `app/classifier/fields.py`, `app/classifier/patterns.py`,
and `evaluation/dataset.jsonl` are touched.

## 0. Why

Phase 6's automated evaluation numbers improved substantially (see CLAUDE.md's "Phase 6
results" table), and Phase 6's own manual testing against real Gmail (2026-09-20)
confirmed the headline fix (greeting-contaminated company names) actually works on the
exact real email that originally motivated it. But that same manual-testing pass
surfaced four more real, reproducible gaps — one already fixed on the spot (`hirevue.com`
missing from `ATS_DOMAINS`, see CLAUDE.md), and four still open:

1. A sender-domain-derived company guess picks the wrong label when an ATS/HCM platform
   name appears as a **subdomain of the real employer's own domain**, rather than being
   the domain itself (Verisk: `TalentAcquisition@oraclecloud.verisk.com` → "Oraclecloud"
   instead of "Verisk").
2. Position extraction fails entirely on a real title containing a `|` separator
   (Verisk: "Tech Intern | 2027 Summer Internship Program").
3. A real community-newsletter email crosses the job-relatedness threshold on
   negligible signal and lands in the review queue as noise (DNDA: "DNDA September
   Newsletter", confidence 5%).
4. A real non-recruiting meetup/panel announcement is misclassified as job-related,
   already flagged during Phase 6's own task review but not fixed then (eval dataset's
   `ambiguous`-category "Meetup: Hiring managers panel this Thursday" example).

None of these caused an unsafe auto-apply — the trust-model gate caught every one and
routed it to review — but #1 and #2 are real extraction-quality misses, and #3/#4 are
review-queue noise that erodes the point of having a review queue at all (if enough
junk lands there, genuine items get harder to find).

## 1. Goal & Non-Goals

### Goal

Fix all four gaps above, each backed by real evidence (not speculative hardening), and
extend `evaluation/dataset.jsonl` with a labeled example per gap so a future change
can't silently regress any of them.

### Non-Goals

- Changing `settings.classification_confidence_threshold` (stays `0.85`).
- Changing Phase 4's matching/trust-model logic or the review-queue API/UI.
- Changing Phase 5's sync job queue, worker, retry/backoff, or reaper.
- A general "parse the registrable domain correctly" algorithm (e.g. a public-suffix
  list). Investigated and rejected — see §2.1.
- Loosening the word-count/character constraints on *every* extraction token
  unboundedly — the position-token change (§2.2) is a specific, evidence-bounded
  widening, not a general relaxation.
- Tightening `interview (?:invitation|process)` or the generic `opening`/`opportunity`
  patterns directly — Phase 6 documented these needing to fire alone for
  `recruiter_outreach` classification to work; touching them risks regressing that
  category. §2.3/§2.4 use negative patterns instead.

## 2. Root Causes & Fixes

### 2.1 Compound-subdomain company misattribution

**Root cause, verified directly against current code**: `_domain_derived_company`
(`classifier/fields.py`) takes the first label of the sender's domain after
`extract_sender_domain` (`classifier/text.py`) strips a fixed list of generic prefixes
(`mail.`, `careers.`, `talent.`, etc. — `_SUBDOMAIN_PREFIXES`). Two real sender-domain
shapes both reach `fields.py` as multi-label domains, but need *opposite* handling:

- **Tenant-subdomain pattern** (works correctly today): `acme.myworkday.com` → the
  first label (`acme`) is the real company; the platform is the base domain. Verified:
  `find_company("...", "notify@acme.myworkday.com")` → `("Acme", "domain")`, correct.
- **Platform-subdomain pattern** (broken today): `oraclecloud.verisk.com` → the first
  label (`oraclecloud`) is the *platform*; the real company (`verisk`) is the base
  domain. Verified: currently returns `("Oraclecloud", "domain")`, wrong.

A generic "always prefer the label before the TLD" rule would fix the second case but
silently break the first (`acme.myworkday.com` → would wrongly extract `"Myworkday"`).
Since both shapes are indistinguishable by position alone, the fix must be
label-specific, not positional — this rules out a general algorithm (a non-goal above).

**Fix**: add `"oraclecloud."` to `_SUBDOMAIN_PREFIXES` in `classifier/text.py`. This is
the exact same mechanism already used for `careers.`/`talent.`/etc. — no new code path,
just one more evidenced label. `oraclecloud.verisk.com` → strip `oraclecloud.` → 
`verisk.com` → `"Verisk"`, correct. `acme.myworkday.com` is untouched (doesn't start
with `oraclecloud.`), so the tenant-subdomain case keeps working. This is deliberately a
narrow, per-vendor allowlist — like `ATS_DOMAINS` itself, it will need future additions
as new vendor patterns are found in real mail, not a one-time complete fix.

### 2.2 Pipe-character position titles

**Root cause, verified directly against current code**: the real title `"Tech Intern |
2027 Summer Internship Program"` fails `_POSITION_TOKEN`
(`classifier/fields.py`) for two independent reasons: `|` is not in the allowed
character class (`[\w&'/+\-]`), and the title is 7 space-separated words against the
current 6-word cap (`{0,5}` additional words after the first). Fixing only one of the
two still fails to extract this real title.

**Fix**: add `|` to `_POSITION_TOKEN`'s character class, and raise the cap from 6 to 8
words. The cap exists specifically to stop the position regex from swallowing an entire
sentence (documented in the file's own comments, with a past real bug as the reason it
was added) — so this change is guarded, not casual: after implementing, re-run
`evaluation/compare.py` and confirm `position_exact_accuracy` and
`precision_at_threshold` don't regress on any existing category before considering this
done.

### 2.3 Newsletter false positive (DNDA)

**Root cause, verified against the real email**: "DNDA September Newsletter: A New
Destination" crosses `JOB_RELATED_THRESHOLD` on marginal signal (confidence 5%,
never auto-applies) but still lands in the review queue as noise. Tightening the
generic `opening`/`opportunity` patterns that likely contribute here is out of scope
(non-goal above) since Phase 6 documented them needing to fire alone for
`recruiter_outreach` emails.

**Fix**: add a `NEGATIVE_PATTERNS` entry for `\bnewsletter\b` (same mechanism as the
existing `unsubscribe`/`view in browser` entries) — the real email's subject contains
this word literally. This suppresses the specific evidenced case without touching any
positive pattern, so it can't regress `recruiter_outreach` detection.

### 2.4 Meetup/panel false positive

**Root cause, verified against the existing eval dataset** (`ambiguous` category,
already flagged during Phase 6's own task review but not fixed then): "Meetup: Hiring
managers panel this Thursday" / "...how the interview process works at their
companies... what they look for in candidates..." trips `interview
(?:invitation|process)` plus the generic `\bcandidates?\b` booster. This is
third-person, broadcast-style framing ("hear from hiring managers about..."), not a
candidate-directed message — but tightening `interview (?:invitation|process)` itself
risks regressing genuine candidate-directed interview emails that use the same phrase
(non-goal above).

**Fix**: add `NEGATIVE_PATTERNS` entries for the broadcast-style markers actually
present in this example and the DNDA example's sibling case in the dataset (`"Panel
recap: How we hire at top startups"`): `\bmeetup\b` and `\bpanel\b`. Validate both new
negative patterns against the *full* dataset (not just these two examples) before
finalizing wording, since `panel` in particular could plausibly appear in a legitimate
"panel interview" scheduling email — if that collision shows up in the dataset run,
narrow the pattern (e.g. require `panel` near `recap`/`hear from` rather than bare
`\bpanel\b`) rather than accepting the regression.

## 3. Evaluation Dataset Additions

Add one new labeled example per gap to `evaluation/dataset.jsonl`, under existing or a
new `category` value consistent with Phase 6's categorization scheme:

| Gap | Category | Expected |
|---|---|---|
| Subdomain company misattribution | `sender_variation` (existing category) | `company: "Verisk"` |
| Pipe-character position | `messy_phrasing` (existing category) | `position: "Tech Intern \| 2027 Summer Internship Program"` |
| Newsletter false positive | `ambiguous` (existing category) | `is_job_related: false` |
| Meetup false positive | `ambiguous` (existing category) — the dataset already has this exact example; confirm it's present and correctly labeled rather than adding a duplicate | `is_job_related: false` |

Each new example is a synthetic, hand-authored reconstruction of the real case (subject
line, sender domain, and the relevant body phrasing), not the literal real email
content — consistent with Phase 6's dataset being fully synthetic.

## 4. Metrics

Same metrics as Phase 6 (`evaluation/compare.py`'s existing output — no new metrics
needed): `is_job_related` precision/recall/F1, company/position exact + fuzzy accuracy,
`precision_at_threshold`, `auto_apply_rate`, `review_rate`, all broken out by category.
`precision_at_threshold` and the `sender_variation`/`messy_phrasing`/`ambiguous`
category rows are the ones this phase's fixes should move; everything else should stay
flat.

## 5. Comparison Methodology & Checkpoints

Same measure → change → re-measure discipline as Phase 6, against the *current*
`evaluation/baseline_metrics.json` (Phase 6's frozen baseline — unchanged by this
phase, since these are additive fixes, not a recalibration).

1. **Dataset additions (§3).** Add the four new examples. Run `evaluation/run_eval.py`
   against current (pre-fix) code to confirm each new example actually fails today —
   proves the gap is real, not just theoretical.
2. **§2.1 subdomain fix.** Add `"oraclecloud."` to `_SUBDOMAIN_PREFIXES`. Add a direct
   regression test (`find_company` with the Verisk-shaped sender, and a test locking in
   the `acme.myworkday.com` tenant-subdomain case stays correct). Re-run
   `evaluation/compare.py`, confirm the new `sender_variation` example now passes and
   nothing else moves.
3. **§2.2 position-token fix.** Widen `_POSITION_TOKEN`. Add a direct regression test
   for the pipe-delimited title. Re-run `evaluation/compare.py`, confirm the new
   `messy_phrasing` example passes and `position_exact_accuracy`/`precision_at_threshold`
   don't regress on any category.
4. **§2.3/§2.4 negative-pattern fixes.** Add both `NEGATIVE_PATTERNS` entries together
   (both are the same class of fix). Add direct `classify()` regression tests. Re-run
   `evaluation/compare.py` against the *full* dataset, confirm both new `ambiguous`
   examples now correctly classify as not-job-related and no other category's
   `is_job_related` recall drops (a real risk if either new pattern is too broad).
5. **Final report.** Run the full backend test suite, `evaluation/compare.py`, and
   frontend checks (`tsc -b`, `oxlint`). Append a before/after summary to CLAUDE.md's
   Phase 6 section area (a new "Phase 7 results" section) with the same table format
   Phase 6 used. No frontend changes are anticipated in any checkpoint.

Each checkpoint keeps the full backend suite green before moving to the next.
