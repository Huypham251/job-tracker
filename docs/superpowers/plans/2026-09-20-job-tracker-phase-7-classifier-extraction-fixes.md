# Job Tracker Phase 7: Classifier Extraction Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four concrete, real-mail extraction/classification gaps found during
Phase 6 manual testing (2026-09-20): compound-subdomain company misattribution,
pipe-character position titles, and two review-queue false positives (a community
newsletter, a meetup/panel announcement).

**Architecture:** No new modules. Four small, independent edits to the existing local
rule-based classifier (`app/classifier/text.py`, `app/classifier/fields.py`,
`app/classifier/patterns.py`), each backed by a labeled `evaluation/dataset.jsonl`
example that fails today and passes after its fix, plus a direct unit/regression test
at the function seam (`find_company`/`find_position`/`classify`). Every fix is verified
against the full dataset via `evaluation/compare.py`, not just its own example, to
catch collateral regressions.

**Tech Stack:** Python 3.14, pytest, rapidfuzz (already a dependency, used by
`evaluation/run_eval.py`'s fuzzy-match scoring — untouched by this plan).

**Spec:** `docs/superpowers/specs/2026-09-20-job-tracker-phase-7-classifier-extraction-fixes-design.md`

## Global Constraints

- `settings.classification_confidence_threshold` stays `0.85` — never change it.
- Do not touch `pipeline/matching.py`, `pipeline/service.py::_apply_decision`, the
  review-queue API/UI, or anything in `app/sync/`.
- Do not tighten `interview (?:invitation|process)`, `\bopening\b`, or `\bopportunity\b`
  directly — Phase 6 documented these needing to fire alone for `recruiter_outreach`
  classification to work; the false-positive fixes here use new `NEGATIVE_PATTERNS`
  entries instead (see spec §2.3/§2.4, and Task 4's plan-time refinement below).
- Every dataset addition must be verified to currently fail (red) before its fix lands,
  using the exact commands in each task — this is what proves each gap is real, not
  theoretical.
- After every code change, run `cd backend && uv run python -m evaluation.compare` and
  confirm no metric outside the intended target regresses.
- Full backend suite (`cd backend && uv run pytest`) must be green before each commit.

---

## File Structure

No new files. Files touched:

- `backend/evaluation/dataset.jsonl` — three new labeled examples appended (Task 1).
  The fourth gap (meetup false positive) already has two examples in this file
  (`"Meetup: Hiring managers panel this Thursday"` at line 66, currently mismatched;
  `"Panel recap: How we hire at top startups"` at line 68, already correct) — no new
  example needed for it.
- `backend/app/classifier/text.py` — `_SUBDOMAIN_PREFIXES` tuple gets one new entry
  (Task 2).
- `backend/app/classifier/fields.py` — `_POSITION_TOKEN` character class and word cap
  widened (Task 3).
- `backend/app/classifier/patterns.py` — `NEGATIVE_PATTERNS` gets two new entries
  (Task 4).
- `backend/tests/test_classifier_fields.py` — regression tests for Tasks 2 and 3.
- `backend/tests/test_classifier_patterns.py` — regression tests for Task 4.
- `backend/tests/test_evaluation_accuracy.py` — bars rebased on the final dataset
  (Task 5).
- `CLAUDE.md` — new "Phase 7 results" section (Task 5).

---

### Task 1: Add evaluation dataset examples for the three new gaps, confirm they fail today

**Files:**
- Modify: `backend/evaluation/dataset.jsonl` (append 3 lines at the end, after line 88)

**Interfaces:**
- Consumes: `app.classifier.extractor.RuleBasedExtractor.classify_and_extract(subject, sender, date, body) -> EmailExtraction` (existing, unchanged).
- Produces: three new dataset rows later tasks' fixes are measured against. No code
  interfaces change in this task.

This task only adds data — no production code changes. Its "test" is running the
classifier against the three new examples and confirming each currently produces the
wrong result, proving the gap is real before any fix lands.

- [ ] **Step 1: Append the three new examples to `backend/evaluation/dataset.jsonl`**

Open the file and add these three lines at the end (after the existing line 88, each on
its own line, valid JSON per line — matching the file's existing format exactly):

```json
{"category": "sender_variation", "subject": "Application Update", "sender": "TalentAcquisition@oraclecloud.verisk.com", "date": "Mon, 6 Apr 2026 09:00:00 +0000", "body": "Thank you for the time and effort you put into applying for the QA Analyst position. Unfortunately, we have decided to move forward with other candidates.", "expected": {"is_job_related": true, "company": "Verisk", "position": "QA Analyst", "status": "rejected"}}
{"category": "messy_phrasing", "subject": "Your application to Solstice Robotics", "sender": "careers@solsticerobotics.com", "date": "Tue, 7 Apr 2026 09:00:00 +0000", "body": "Thank you for applying to Solstice Robotics. We would like you to complete an online assessment for the Mechanical Engineer | 2027 Summer Internship Program role before the deadline.", "expected": {"is_job_related": true, "company": "Solstice Robotics", "position": "Mechanical Engineer | 2027 Summer Internship Program"}}
{"category": "ambiguous", "subject": "Riverside Neighbors September Newsletter", "sender": "communications@riversideneighbors.org", "date": "Wed, 8 Apr 2026 09:00:00 +0000", "body": "Plus: our fall cleanup day, a new mural unveiling, and how to join the tenant council. Now Hiring: Community Outreach Coordinator. This position will support neighborhood events. Also, join us for an opportunity to volunteer at the community garden this weekend.", "expected": {"is_job_related": false}}
```

- [ ] **Step 2: Confirm all three currently fail (red)**

Run:

```bash
cd backend && uv run python -c "
from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import load_dataset

e = RuleBasedExtractor()
dataset = load_dataset()
targets = {
    'Application Update': 'company',
    'Your application to Solstice Robotics': 'position',
    'Riverside Neighbors September Newsletter': 'is_job_related',
}
for ex in dataset:
    if ex['subject'] not in targets:
        continue
    r = e.classify_and_extract(subject=ex['subject'], sender=ex['sender'], date=ex['date'], body=ex['body'])
    field = targets[ex['subject']]
    actual = getattr(r, field)
    expected = ex['expected'].get(field)
    print(ex['subject'], '| field:', field, '| actual:', actual, '| expected:', expected, '| MATCH' if actual == expected else 'MISMATCH (expected, proves the gap)')
"
```

Expected output: all three lines print `MISMATCH (expected, proves the gap)` —
`company` prints `Oraclecloud` (not `Verisk`), `position` prints `None` (not the
pipe-delimited title), `is_job_related` prints `True` (not `False`).

- [ ] **Step 3: Commit**

```bash
cd backend && git add evaluation/dataset.jsonl
git commit -m "$(cat <<'EOF'
test(evaluation): add three dataset examples for Phase 7 known gaps

Subdomain company misattribution (Verisk/oraclecloud), pipe-character
position title, and a newsletter false positive — each confirmed to
fail against current code before their fixes land in later commits.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Fix compound-subdomain company misattribution

**Files:**
- Modify: `backend/app/classifier/text.py:7-10` (`_SUBDOMAIN_PREFIXES`)
- Test: `backend/tests/test_classifier_fields.py`

**Interfaces:**
- Consumes: `app.classifier.fields.find_company(text: str, sender: str) -> tuple[str | None, str]` (existing, unchanged signature).
- Produces: `extract_sender_domain` (existing, `app/classifier/text.py`) now also
  strips a leading `oraclecloud.` label — no signature change, only behavior for that
  one label.

- [ ] **Step 1: Write the failing regression tests**

Add to `backend/tests/test_classifier_fields.py` (after
`test_find_company_does_not_treat_non_ats_assessment_platform_as_the_company`, which
ends at line 168):

```python
def test_find_company_prefers_the_apex_domain_over_an_oraclecloud_subdomain() -> None:
    # Real Verisk rejection email found during Phase 6 manual testing: the sender
    # domain is "oraclecloud.verisk.com" — Verisk's own domain, verisk.com, fronted by
    # an "oraclecloud" ATS subdomain label. Before this fix, the first label
    # ("oraclecloud", the platform) was extracted instead of the real employer.
    company, tier = find_company("no template match here", "TalentAcquisition@oraclecloud.verisk.com")
    assert company == "Verisk"
    assert tier == "domain"


def test_find_company_still_treats_workday_tenant_subdomain_as_the_company() -> None:
    # Guards against a general "always prefer the label before the TLD" fix, which
    # would break this opposite, already-correct pattern: here the FIRST label
    # ("acme") is the real company and the platform is the base domain — the reverse
    # of the oraclecloud.verisk.com shape above. The fix must stay a specific,
    # evidenced label addition (oraclecloud.), not a positional rule.
    company, tier = find_company("no template match here", "notify@acme.myworkday.com")
    assert company == "Acme"
    assert tier == "domain"
```

- [ ] **Step 2: Run the tests to verify the first one fails, the second already passes**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py -k "oraclecloud or workday_tenant" -v`

Expected: `test_find_company_prefers_the_apex_domain_over_an_oraclecloud_subdomain`
FAILS (`assert 'Oraclecloud' == 'Verisk'`);
`test_find_company_still_treats_workday_tenant_subdomain_as_the_company` PASSES
(this one locks in existing correct behavior — it isn't expected to fail, it's a
safety net for the next step).

- [ ] **Step 3: Implement the fix**

In `backend/app/classifier/text.py`, change:

```python
_SUBDOMAIN_PREFIXES = (
    "mail.", "notifications.", "e.", "no-reply.", "noreply.",
    "careers.", "jobs.", "talent.", "recruiting.",
)
```

to:

```python
_SUBDOMAIN_PREFIXES = (
    "mail.", "notifications.", "e.", "no-reply.", "noreply.",
    "careers.", "jobs.", "talent.", "recruiting.",
    # ATS/HCM platform labels that appear as a LEADING subdomain of the real
    # employer's own domain (the opposite shape from a tenant subdomain like
    # acme.myworkday.com, where the first label IS the company — see
    # test_find_company_still_treats_workday_tenant_subdomain_as_the_company).
    # Evidenced by a real Verisk email during Phase 6 manual testing:
    # oraclecloud.verisk.com must resolve to "verisk", not "oraclecloud".
    "oraclecloud.",
)
```

- [ ] **Step 4: Run the tests to verify both pass**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py -k "oraclecloud or workday_tenant" -v`

Expected: both PASS.

- [ ] **Step 5: Confirm the dataset example now passes and check for regressions**

Run: `cd backend && uv run python -m evaluation.compare`

Expected: `company_exact_accuracy` and `company_fuzzy_accuracy` move up (the new
`sender_variation` example now passes); no other metric drops relative to the Phase 6
baseline.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest -q`

Expected: all tests pass (the `test_evaluation_accuracy.py` bars still hold — this fix
only improves company accuracy, it doesn't regress anything the current bars guard).

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/classifier/text.py tests/test_classifier_fields.py
git commit -m "$(cat <<'EOF'
fix(classifier): resolve the apex domain when an ATS platform name is
a leading subdomain

oraclecloud.verisk.com was extracting company="Oraclecloud" (the ATS
platform) instead of "Verisk" (the real employer) — found on a real
rejection email during Phase 6 manual testing. Extends the existing
_SUBDOMAIN_PREFIXES mechanism (already used for careers./talent./etc.)
with this one evidenced label, rather than a general "prefer the label
before the TLD" rule, which would have broken the opposite and already-
correct acme.myworkday.com tenant-subdomain pattern.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Fix pipe-character position titles

**Files:**
- Modify: `backend/app/classifier/fields.py:53` (`_POSITION_TOKEN`)
- Test: `backend/tests/test_classifier_fields.py`

**Interfaces:**
- Consumes: `app.classifier.fields.find_position(text: str, sender: str) -> tuple[str | None, str]` (existing, unchanged signature).
- Produces: `_POSITION_TOKEN` (existing regex fragment used by
  `_POSITION_AT_COMPANY_RE`, `_POSITION_ROLE_RE`, `_POSITION_AS_NEW_RE`) now allows `|`
  and up to 8 words instead of 6 — every position template benefits, not just the one
  fixed here.

- [ ] **Step 1: Write the failing regression test**

Add to `backend/tests/test_classifier_fields.py` (after the two tests added in Task 2):

```python
def test_find_position_captures_a_pipe_delimited_title() -> None:
    # Real Verisk email found during Phase 6 manual testing: "Tech Intern | 2027
    # Summer Internship Program" fails today for two independent reasons — "|" isn't
    # in _POSITION_TOKEN's allowed characters, and the title is 7 words against the
    # then-6-word cap. Uses a different (Solstice Robotics) example here to isolate
    # position extraction from the company-extraction fix in Task 2.
    text = (
        "Thank you for applying to Solstice Robotics. We would like you to complete "
        "an online assessment for the Mechanical Engineer | 2027 Summer Internship "
        "Program role before the deadline."
    )
    position, tier = find_position(text, "careers@solsticerobotics.com")
    assert position == "Mechanical Engineer | 2027 Summer Internship Program"
    assert tier == "template"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py -k pipe_delimited -v`

Expected: FAILS (`assert None == 'Mechanical Engineer | 2027 Summer Internship Program'`).

- [ ] **Step 3: Implement the fix**

In `backend/app/classifier/fields.py`, change line 53 from:

```python
_POSITION_TOKEN = r"[\w&'/+\-]+(?:\s[\w&'/+\-]+){0,5}"
```

to:

```python
_POSITION_TOKEN = r"[\w&'/+|\-]+(?:\s[\w&'/+|\-]+){0,7}"
```

(adds `|` to the allowed character class; raises the cap from 6 words — `{0,5}`
additional after the first — to 8 words — `{0,7}` additional after the first. A real
title with a pipe-delimited internship-program suffix, e.g. "Tech Intern | 2027 Summer
Internship Program", is 7 words; 8 leaves one word of headroom, following the project's
established "tolerate one more" calibration philosophy rather than fitting the cap
exactly to the one known example.)

Also update the comment block immediately above `_POSITION_TOKEN` (currently ending
`"# 'Full-Stack/Backend' use them."`) by appending one sentence:

```python
# `|` is also included (Phase 7): real titles like "Tech Intern | 2027 Summer
# Internship Program" use it as a separator, and the word cap was raised from 6 to 8
# to fit such titles — still bounded, not unbounded, so the overcapture risk this
# comment describes above doesn't reopen.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py -k pipe_delimited -v`

Expected: PASSES.

- [ ] **Step 5: Confirm the dataset example now passes and check for regressions**

Run: `cd backend && uv run python -m evaluation.compare`

Expected: `position_exact_accuracy` and `position_fuzzy_accuracy` move up (the new
`messy_phrasing` example now passes); `precision_at_threshold` does not drop (this is
the check that guards against the widened cap reintroducing the overcapture bug
documented in `fields.py`'s comments — if it drops, inspect which example regressed
with `uv run python -m evaluation.inspect_confidence` before proceeding).

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/classifier/fields.py tests/test_classifier_fields.py
git commit -m "$(cat <<'EOF'
fix(classifier): allow pipe-delimited, up-to-8-word position titles

"Tech Intern | 2027 Summer Internship Program" (a real title found
during Phase 6 manual testing) failed to extract for two reasons: "|"
wasn't in _POSITION_TOKEN's character class, and the title is 7 words
against the prior 6-word cap. Both needed fixing together. The cap
stays bounded (now 8, not unbounded) to preserve the overcapture
protection documented in this file's existing comments.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Fix newsletter and meetup false positives

**Files:**
- Modify: `backend/app/classifier/patterns.py:60-67` (`NEGATIVE_PATTERNS`)
- Test: `backend/tests/test_classifier_patterns.py`

**Interfaces:**
- Consumes: `app.classifier.extractor.classify(text: str, sender_domain: str) -> tuple[dict[str, int], int, int]` (existing, unchanged signature — returns `(status_scores, job_signal, negative_signal)`).
- Produces: `NEGATIVE_PATTERNS` (existing list, `app/classifier/patterns.py`) gains two
  entries — no signature change.

**Plan-time refinement from the spec**: the spec (§2.4) speculated adding both
`\bmeetup\b` and `\bpanel\b`. Verified against the actual dataset before writing this
task: only the "Meetup: Hiring managers panel this Thursday" example currently fails
(it's the dataset's one pre-existing tolerated miss); the sibling "Panel recap: How we
hire at top startups" example already classifies correctly today (job_signal=2, already
below `JOB_RELATED_THRESHOLD=3`). Adding `\bpanel\b` is unnecessary to fix the one real
failure and would risk suppressing a legitimate "panel interview" scheduling email that
doesn't exist in the dataset to catch such a regression — so this task adds only
`\bmeetup\b`, per the Global Constraints' YAGNI direction. `\bnewsletter\b` (§2.3) is
added as originally specified.

- [ ] **Step 1: Write the failing regression tests**

Add to `backend/tests/test_classifier_patterns.py` (after
`test_generic_job_patterns_match_opening_and_opportunity`, the file's last test):

```python
def test_negative_patterns_suppress_a_newsletter_with_incidental_job_language() -> None:
    # Real DNDA community-newsletter email found during Phase 6 manual testing: a
    # multi-topic digest with an unrelated "position"/"opportunity" mention (a
    # volunteer opportunity, not a job) crosses JOB_RELATED_THRESHOLD on generic
    # patterns alone. The subject line contains "Newsletter" literally.
    text = (
        "riverside neighbors september newsletter plus: our fall cleanup day, a new "
        "mural unveiling, and how to join the tenant council. now hiring: community "
        "outreach coordinator. this position will support neighborhood events. also, "
        "join us for an opportunity to volunteer at the community garden this weekend."
    )
    _, job_signal, negative_signal = classify(text, "riversideneighbors.org")
    assert job_signal - negative_signal < JOB_RELATED_THRESHOLD


def test_negative_patterns_suppress_a_meetup_style_false_positive() -> None:
    # Real gap already present in evaluation/dataset.jsonl (the "Meetup: Hiring
    # managers panel this Thursday" example) — third-person, broadcast-style framing
    # about hiring as a topic, not a candidate-directed message, trips
    # "interview (?:invitation|process)" plus the generic "\bcandidates?\b" booster.
    text = (
        "meetup: hiring managers panel this thursday come hear from hiring managers "
        "about what they look for in candidates and how the interview process works "
        "at their companies. free pizza provided."
    )
    _, job_signal, negative_signal = classify(text, "devmeetup.com")
    assert job_signal - negative_signal < JOB_RELATED_THRESHOLD
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_patterns.py -k "newsletter or meetup_style" -v`

Expected: both FAIL (`job_signal - negative_signal` is `4 - 0 = 4` and `4 - 0 = 4`
respectively, not `< 3`).

- [ ] **Step 3: Implement the fix**

In `backend/app/classifier/patterns.py`, change:

```python
NEGATIVE_PATTERNS: list[tuple[str, int]] = [
    (r"jobs matching your search", 3),
    (r"new jobs? for you", 3),
    (r"recommended jobs", 2),
    (r"unsubscribe", 2),
    (r"view (?:this|in) browser", 2),
    (r"%\s*off", 2),
]
```

to:

```python
NEGATIVE_PATTERNS: list[tuple[str, int]] = [
    (r"jobs matching your search", 3),
    (r"new jobs? for you", 3),
    (r"recommended jobs", 2),
    (r"unsubscribe", 2),
    (r"view (?:this|in) browser", 2),
    (r"%\s*off", 2),
    # Both found via real Phase 6 manual-testing/eval-dataset false positives, both
    # weighted to just clear the two known real cases (job_signal=4 in each) without
    # touching the interview/opening/opportunity positive patterns those cases also
    # trip — see the Phase 7 spec §2.3/§2.4 for why the positive patterns themselves
    # are out of scope.
    (r"\bnewsletter\b", 2),
    (r"\bmeetup\b", 2),
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_classifier_patterns.py -k "newsletter or meetup_style" -v`

Expected: both PASS.

- [ ] **Step 5: Confirm the dataset examples now pass and check for regressions across the full dataset**

Run: `cd backend && uv run python -m evaluation.compare`

Expected: `classification_accuracy` and the `ambiguous` category's `is_job_related_f1`
move up (both the new `ambiguous` newsletter example and the pre-existing "Meetup:"
example now classify correctly); `is_job_related` recall does not drop for any other
category — a drop would mean `\bnewsletter\b` or `\bmeetup\b` incidentally appears in a
genuinely job-related example elsewhere in the dataset (already checked manually before
writing this task — neither word appears in any other example — but re-verify here
since this step runs against the dataset as it exists at execution time, which may
differ if Tasks 1-3 changed anything nearby).

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/classifier/patterns.py tests/test_classifier_patterns.py
git commit -m "$(cat <<'EOF'
fix(classifier): suppress newsletter and meetup false positives

A real DNDA community newsletter (incidental "position"/"opportunity"
mentions unrelated to any job) and the dataset's existing "Meetup:
Hiring managers panel" example both cross the job-relatedness
threshold on generic signal alone. Adds two narrow NEGATIVE_PATTERNS
entries (newsletter, meetup) rather than tightening the
interview/opening/opportunity positive patterns those cases also trip,
since Phase 6 documented those needing to fire alone for
recruiter_outreach classification to work.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Final verification, regression-gate rebase, CLAUDE.md update

**Files:**
- Modify: `backend/tests/test_evaluation_accuracy.py` (rebase the `MIN_*` bars on the
  final, post-fix dataset numbers)
- Modify: `CLAUDE.md` (new "Phase 7 results" section)

**Interfaces:**
- Consumes: `evaluation.run_eval.evaluate`, `evaluation.run_eval.load_dataset`,
  `evaluation.run_eval.CONFIDENCE_THRESHOLD`, `app.classifier.extractor.RuleBasedExtractor`
  (all unmodified by this plan).
- Produces: nothing new is consumed by later code — this is the plan's last task.

This task's exact bar values cannot be written in advance — they depend on the actual
measured output of Tasks 1-4, which only exists once those have run. That's not a
placeholder: Step 1 gives the exact command and the exact mechanical rule for turning
its output into bars (the same rule the current file's comments already document, and
the same one Phase 6's plan Task 11 used) — only the numbers are deferred to execution
time, not the method.

- [ ] **Step 1: Get the final numbers**

Run: `cd backend && uv run python -m evaluation.run_eval`

Read the printed `overall` numbers. For each of the six bars below, the rule is: if `C`
out of `T` are currently correct, pick a clean two-decimal bar in the open interval
`((C-2)/T, (C-1)/T]` — passes at the current score, tolerates one more miss, fails at
two more.

- [ ] **Step 2: Update `backend/tests/test_evaluation_accuracy.py`**

Replace the six `MIN_*` constants (keep everything else in the file — the two test
functions and the docstring structure — unchanged except updating the docstring's
dataset description if the total example count changed):

```python
MIN_CLASSIFICATION_ACCURACY = <VALUE>  # from Step 1 — see comment convention below
MIN_STATUS_ACCURACY = <VALUE>
MIN_COMPANY_ACCURACY = <VALUE>          # exact-match, same as Phase 4b/6 (not fuzzy)
MIN_POSITION_ACCURACY = <VALUE>         # exact-match, same as Phase 4b/6 (not fuzzy)
MIN_PRECISION_AT_THRESHOLD = <VALUE>    # guards real auto-applies — the most important bar
MIN_AUTO_APPLY_RATE = <VALUE>           # a floor: confirms Phase 7's fixes didn't silently regress this
```

For each, write the actual computed value and a trailing comment in the existing file's
style, e.g. `MIN_CLASSIFICATION_ACCURACY = 0.98  # currently 90/91=0.989; tolerates 89/91=0.978; fails at 88/91=0.967`
— using the real `C`/`T` from Step 1's output, not these example numbers.

Also update the module docstring's dataset description (currently "against the
88-example dataset... 18 original clean_template examples + 70 added across...") to
reflect the new total (91, after Task 1's three additions) and reference this plan
file alongside the Phase 6 one.

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && uv run pytest -q`

Expected: all tests pass, including the rebased `test_evaluation_accuracy.py`.

- [ ] **Step 4: Run the evaluation comparison one more time for the final report**

Run: `cd backend && uv run python -m evaluation.compare`

Record the full before/after table (before = Phase 6's `baseline_metrics.json`, after =
current) — this is what goes into CLAUDE.md's new section in Step 5.

- [ ] **Step 5: Run frontend checks**

Run: `cd frontend && npx tsc -b && npx oxlint`

Expected: `tsc -b` clean; `oxlint` clean with the same 3 pre-existing warnings noted in
CLAUDE.md (this plan touches no frontend code, so this step is a sanity check, not
expected to find anything new).

- [ ] **Step 6: Add a "Phase 7 results" section to CLAUDE.md**

Insert a new `## Phase 7 results (<today's date>)` section after the existing
"## Phase 6 manual testing findings (2026-09-20)" section and before
"## Local dev environment (this machine)". Include:
- The before/after metrics table from Step 4 (same format as the existing "Phase 6
  results" table).
- One sentence per fix confirming it's covered by both a dataset example and a direct
  regression test (reference the four commits from Tasks 2-4 by summary, not hash).
- A note that the `\bpanel\b` pattern from the spec's speculative §2.4 was not added
  after verification showed only `\bmeetup\b` was needed to fix the one real failing
  case (cross-reference Task 4's "Plan-time refinement" note above) — this is worth
  recording since it's a deliberate, evidence-based deviation from the written spec.

Also update the "## Status" section's test count (currently "274/274") to the new
total from Step 3.

- [ ] **Step 7: Commit**

```bash
cd backend && git add tests/test_evaluation_accuracy.py
cd .. && git add CLAUDE.md
git commit -m "$(cat <<'EOF'
test(evaluation): rebase regression bars on the Phase 7 dataset; document results

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Confirm a clean working tree**

Run: `cd /Users/itshuy/Documents/Projects/Job-tracker && git status --short`

Expected: no output (clean).

---

## Self-Review Notes

- **Spec coverage**: §2.1 (subdomain) → Task 2. §2.2 (position token) → Task 3. §2.3
  (newsletter) and §2.4 (meetup) → Task 4. §3 (dataset additions) → Task 1 (three new
  examples) plus Task 4's note that the fourth gap's examples already exist. §5
  (checkpoints/final report) → Task 5. All spec sections have a task.
- **Placeholder scan**: the only `<VALUE>` placeholders are in Task 5, which explicitly
  documents why they can't be filled in advance (same convention as the Phase 6 plan's
  Task 11) and gives the exact mechanical procedure to fill them at execution time.
  No other placeholders.
- **Type/interface consistency**: `find_company`/`find_position` signatures
  (`(text: str, sender: str) -> tuple[str | None, str]`) and `classify`'s signature
  (`(text: str, sender_domain: str) -> tuple[dict[str, int], int, int]`) are used
  identically across Tasks 2-4 and match the actual current source read before writing
  this plan.
- **Plan-time deviation from spec, documented at the point it happens**: Task 4 drops
  the spec's speculative `\bpanel\b` pattern after direct verification against the
  dataset showed it's unnecessary (only one real case fails, and `\bmeetup\b` alone
  fixes it) — flagged inline in Task 4 and again in Task 5's CLAUDE.md update step, so
  it's not silently lost.
