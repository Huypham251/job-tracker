# Phase 6: Classifier & Extraction Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between the classifier's curated-dataset accuracy and its
real-mailbox behavior — confidence that never crosses the auto-apply threshold, and
company extraction that bleeds into greeting text — with root-caused, measured fixes,
not more ad-hoc regex.

**Architecture:** A larger, category-labeled evaluation dataset (still hand-authored,
synthetic) replaces reliance on the 18-example original; a new `evaluation/compare.py`
diffs any run against a recorded baseline so every change is judged by measured
before/after numbers. Fixes land in dependency order: a new `app/classifier/preprocess.py`
stage (greeting/signature stripping, punctuation normalization) runs first since it
reduces noise for everything downstream; then extraction (`fields.py`, `text.py`)
improvements; then status-pattern coverage (`patterns.py`); then confidence
recalibration (`patterns.py` constants) last, since it depends on everything else being
in its final state. One narrow fix lands in `app/gmail/google_api.py` (Phase 5
territory) because the bug — `<style>`/`<script>` block *contents* surviving HTML tag
stripping — lives there, not in the classifier.

**Tech Stack:** Python 3.12, pytest, stdlib `re` (unchanged — no new dependencies).
`rapidfuzz` (already a dependency via `pipeline/matching.py`) is reused for fuzzy
extraction-accuracy scoring in the eval tooling.

**Spec:** `docs/superpowers/specs/2026-09-17-job-tracker-phase-6-classifier-quality-design.md`

## Global Constraints

- No LLM/paid API, no network calls, no new runtime dependencies. Classification stays
  fully deterministic.
- `settings.classification_confidence_threshold` stays `0.85` — never edit
  `app/core/config.py`'s value for it. Confidence *inputs* are recalibrated so correct,
  clear real emails can legitimately reach it; the bar itself does not move.
- `app/pipeline/` (matching, trust model, `_apply_decision`, review queue) and
  `app/sync/` (job queue, worker, retry/backoff, reaper) are unchanged. The only
  exception is the single, narrow `app/gmail/google_api.py::_clean_text` fix in Task 5
  — nothing else in `app/gmail/` or `app/sync/` is touched.
- `EmailExtraction`'s schema (`app/classifier/schemas.py`) does not change — no new
  fields, no new `status` literal values. Recruiter outreach maps to the existing
  `status="other"`.
- Every new/changed pattern, template, or constant must be justified by a specific,
  named example in `evaluation/dataset.jsonl` — no speculative additions "just in
  case." Each extraction/status/calibration task lists exactly which dataset examples
  motivate its change.
- `evaluation/dataset.jsonl`'s 18 original examples are kept byte-for-byte (only a
  `"category": "clean_template"` key is added to each) — they remain a regression floor.
- Every checkpoint (Tasks 6-10) ends by running `uv run python -m evaluation.compare`
  and confirming no metric regresses against the Task 4 baseline before moving to the
  next task.

---

## File Structure

```
backend/
├── evaluation/
│   ├── dataset.jsonl                    # MODIFIED — category field added to existing
│   │                                        18; 70 new examples appended (Tasks 1-3)
│   ├── run_eval.py                      # MODIFIED — full metrics (precision/recall/F1,
│   │                                        per-status, fuzzy extraction accuracy,
│   │                                        precision-at-threshold, auto-apply rate,
│   │                                        review rate), per-category breakdown,
│   │                                        importable `evaluate(extractor, examples)`
│   │                                        (Task 4)
│   ├── compare.py                       # NEW — runs evaluate() against the current
│   │                                        RuleBasedExtractor, diffs against
│   │                                        baseline_metrics.json, prints a table
│   │                                        (Task 4)
│   ├── baseline_metrics.json            # NEW — today's (pre-Phase-6) extractor's
│   │                                        metrics against the new dataset, recorded
│   │                                        once in Task 4 and never edited again
│   └── inspect_confidence.py            # NEW — diagnostic-only: prints the confidence
│                                            formula's components per example, for
│                                            recalibration work (Task 10)
├── app/
│   ├── gmail/
│   │   └── google_api.py                # MODIFIED — _clean_text strips <style>/
│   │                                        <script> block contents, not just tags
│   │                                        (Task 5)
│   └── classifier/
│       ├── preprocess.py                # NEW — greeting-line stripping,
│       │                                    signature/footer truncation, smart-quote/
│       │                                    dash normalization (Task 6)
│       ├── extractor.py                 # MODIFIED — calls preprocess.preprocess_body
│       │                                    before normalize_text/combine_subject_body
│       │                                    (Task 6)
│       ├── fields.py                    # MODIFIED — trailing-greeting trim on company
│       │                                    capture, broadened templates, new
│       │                                    "X is pleased to offer" / "as our new X"
│       │                                    templates (Tasks 7-8)
│       ├── text.py                      # MODIFIED — _SUBDOMAIN_PREFIXES extended
│       │                                    (Task 7)
│       └── patterns.py                  # MODIFIED — ATS_DOMAINS extended (Task 7);
│                                            new STATUS_PATTERNS/GENERIC_JOB_PATTERNS
│                                            entries (Task 9); recalibrated
│                                            JOB_SIGNAL_NORM/MARGIN_NORM/weights
│                                            (Task 10)
└── tests/
    ├── test_evaluation_run_eval.py        # NEW — unit tests for evaluate()'s scoring
    │                                        arithmetic (Task 4)
    ├── test_gmail_google_api.py          # MODIFIED — new test for style/script
    │                                        stripping (Task 5)
    ├── test_classifier_preprocess.py      # NEW (Task 6)
    ├── test_classifier_fields.py          # MODIFIED (Tasks 7-8)
    ├── test_classifier_text.py            # MODIFIED (Task 7)
    ├── test_classifier_patterns.py        # MODIFIED (Task 9)
    ├── test_classifier_extractor.py       # MODIFIED (Task 6, confidence assertions
    │                                          revisited in Task 10)
    └── test_evaluation_accuracy.py        # MODIFIED — bars rebased on final numbers
                                               (Task 11)

CLAUDE.md                                  # MODIFIED — before/after summary appended
                                               (Task 11)
```

---

## Task 1: Tag the existing 18 dataset examples with `category`

**Files:**
- Modify: `backend/evaluation/dataset.jsonl`
- Test: `backend/tests/test_evaluation_accuracy.py` (existing — run unchanged, must still pass)

**Interfaces:**
- Consumes: nothing new.
- Produces: every existing dataset line now has `"category": "clean_template"`; later
  tasks' new lines carry their own category values. `run_eval.py`/`test_evaluation_accuracy.py`
  are unmodified in this task and must keep passing — proves the new field is
  backward-compatible (both scripts read only the keys they already know about, `json.loads`
  keeps extra keys, silently ignored today).

- [ ] **Step 1: Add `"category": "clean_template"` to each of the 18 existing lines**

Open `backend/evaluation/dataset.jsonl` and insert `"category": "clean_template", ` right
after the opening `{` of every line's JSON object (i.e., as the first key), for example
line 1 becomes:

```json
{"category": "clean_template", "subject": "Your application to Acme Corp", "sender": "careers@acme.com", "date": "Mon, 5 Jan 2026 10:00:00 +0000", "body": "Thank you for applying to Acme Corp. We have received your application for the Software Engineer position and will be in touch.", "expected": {"is_job_related": true, "company": "Acme Corp", "position": "Software Engineer", "status": "applied"}}
```

Do this for all 18 lines — every other field byte-for-byte unchanged.

- [ ] **Step 2: Verify the file still parses and has exactly 18 lines**

Run: `cd backend && uv run python -c "import json; lines=[json.loads(l) for l in open('evaluation/dataset.jsonl') if l.strip()]; print(len(lines)); assert all(l['category']=='clean_template' for l in lines)"`
Expected: prints `18`, no assertion error.

- [ ] **Step 3: Run the existing regression tests to confirm nothing broke**

Run: `cd backend && uv run pytest tests/test_evaluation_accuracy.py -v`
Expected: PASS, same numbers as before (`18/18` classification, etc. — this task changes
no scoring logic).

- [ ] **Step 4: Commit**

```bash
cd backend && git add evaluation/dataset.jsonl
git commit -m "test(evaluation): tag existing dataset examples with category=clean_template"
```

---

## Task 2: Add `html_noise`, `greeting_adjacent`, `signature_footer` dataset examples

**Files:**
- Modify: `backend/evaluation/dataset.jsonl` (append 30 new lines)

**Interfaces:**
- Consumes: nothing new.
- Produces: 30 new dataset lines the rest of the plan measures against. No code under
  test yet — this task only adds data. `run_eval.py`/`test_evaluation_accuracy.py` are
  NOT expected to pass at their current bars after this task (that's the point — Task 4
  measures how much worse the unmodified extractor does on the harder set, and Task 11
  is the only task allowed to touch the bars). Do not edit `test_evaluation_accuracy.py`
  in this task.

Each example below is a **verbatim JSON line** to append to `backend/evaluation/dataset.jsonl`
(one JSON object per line, no line-wrapping in the actual file — wrapped here only for
readability). Append them in this order, immediately after the 18 tagged lines from Task 1.

- [ ] **Step 1: Append the 10 `html_noise` examples**

These simulate realistic leftover nav/button-label fragments that survive HTML→text
conversion even after Task 5's style/script fix (that fix removes CSS/JS block content;
it does not and cannot remove legitimate-looking nav text like "View Application
Status" that Gmail's HTML template literally renders as visible words).

```json
{"category": "html_noise", "subject": "Your application to Highland Robotics", "sender": "careers@highlandrobotics.com", "date": "Sun, 1 Feb 2026 09:00:00 +0000", "body": "Thank you for applying to Highland Robotics. We have received your application for the Structural Engineer position.  View Application Status  Manage Email Preferences  Highland Robotics, 500 Industry Way", "expected": {"is_job_related": true, "company": "Highland Robotics", "position": "Structural Engineer", "status": "applied"}}
{"category": "html_noise", "subject": "Next step: complete your coding challenge", "sender": "noreply@hackerrank.com", "date": "Mon, 2 Feb 2026 09:00:00 +0000", "body": "As part of your application to Driftwood Games, please complete this online coding challenge within 5 days.  Download the App  View in Browser  HackerRank, Inc.", "expected": {"is_job_related": true, "company": "Driftwood Games", "status": "oa"}}
{"category": "html_noise", "subject": "Interview Invitation", "sender": "talent@junctionmobility.com", "date": "Tue, 3 Feb 2026 09:00:00 +0000", "body": "We would like to invite you to interview for the Backend Engineer position at Junction Mobility.  Manage Job Alerts  Update Your Profile  Junction Mobility Careers", "expected": {"is_job_related": true, "company": "Junction Mobility", "position": "Backend Engineer", "status": "interview"}}
{"category": "html_noise", "subject": "Update on your application", "sender": "careers@cinderblockgames.com", "date": "Wed, 4 Feb 2026 09:00:00 +0000", "body": "Thank you for your interest in the Level Designer role at Cinder Block Games. Unfortunately, we have decided to move forward with other candidates for this position.  Manage Notification Settings  Visit Careers Site", "expected": {"is_job_related": true, "company": "Cinder Block Games", "position": "Level Designer", "status": "rejected"}}
{"category": "html_noise", "subject": "Offer of employment - Trellis Health", "sender": "hr@trellishealth.com", "date": "Thu, 5 Feb 2026 09:00:00 +0000", "body": "We are pleased to offer you the Clinical Data Analyst position at Trellis Health. Please find the offer of employment attached.  View Offer Details  Manage Preferences", "expected": {"is_job_related": true, "company": "Trellis Health", "position": "Clinical Data Analyst", "status": "offer"}}
{"category": "html_noise", "subject": "Application Received - Outpost Aerospace", "sender": "talent@outpostaerospace.com", "date": "Fri, 6 Feb 2026 09:00:00 +0000", "body": "We have received your application for the Propulsion Engineer position at Outpost Aerospace. Our recruiting team will review it shortly.  Track Your Application  Update Your Profile", "expected": {"is_job_related": true, "company": "Outpost Aerospace", "position": "Propulsion Engineer", "status": "applied"}}
{"category": "html_noise", "subject": "Complete your technical assessment", "sender": "assessments@codesignal.com", "date": "Sat, 7 Feb 2026 09:00:00 +0000", "body": "Amber Trail Foods has invited you to complete a take-home assignment for the Data Analyst position.  View Assessment  Manage Email Preferences  CodeSignal", "expected": {"is_job_related": true, "company": "Amber Trail Foods", "position": "Data Analyst", "status": "oa"}}
{"category": "html_noise", "subject": "Phone screen with Cedar Grove Foods", "sender": "recruiting@cedargrovefoods.com", "date": "Sun, 8 Feb 2026 09:00:00 +0000", "body": "Let's schedule a phone screen for the Supply Planner role at Cedar Grove Foods.  View Available Times  Manage Notification Settings", "expected": {"is_job_related": true, "company": "Cedar Grove Foods", "position": "Supply Planner", "status": "interview"}}
{"category": "html_noise", "subject": "Your application to Waypoint Analytics", "sender": "careers@waypointanalytics.com", "date": "Mon, 9 Feb 2026 09:00:00 +0000", "body": "We regret to inform you that we will not be moving forward with your application to Waypoint Analytics for the Data Engineer position.  Update Your Profile  Visit Careers Site", "expected": {"is_job_related": true, "company": "Waypoint Analytics", "position": "Data Engineer", "status": "rejected"}}
{"category": "html_noise", "subject": "Your offer from Brightview Energy", "sender": "hr@brightviewenergy.com", "date": "Tue, 10 Feb 2026 09:00:00 +0000", "body": "Brightview Energy is pleased to extend an offer for the Electrical Engineer position. Welcome to the team!  View Offer  Manage Preferences", "expected": {"is_job_related": true, "company": "Brightview Energy", "position": "Electrical Engineer", "status": "offer"}}
```

- [ ] **Step 2: Append the 10 `greeting_adjacent` examples**

These reproduce the real "Anduril Hi Gia Huy" bug mechanism directly: a sentence ends
without terminating punctuation right where a greeting paragraph begins (exactly what
HTML paragraph-to-text conversion without inserted punctuation produces), so a naive
capitalized-word-run capture bleeds into the greeting. `company`/`position` in
`expected` are the correct ground truth regardless of what today's unmodified
extractor produces.

```json
{"category": "greeting_adjacent", "subject": "Your application to Pinnacle Robotics", "sender": "careers@pinnaclerobotics.com", "date": "Wed, 11 Feb 2026 09:00:00 +0000", "body": "Thank you for applying to Pinnacle Robotics\n\nHi Jordan Lee,\n\nWe have received your application for the Mechanical Engineer position and will be in touch.", "expected": {"is_job_related": true, "company": "Pinnacle Robotics", "position": "Mechanical Engineer", "status": "applied"}}
{"category": "greeting_adjacent", "subject": "Next step: online assessment", "sender": "noreply@hackerrank.com", "date": "Thu, 12 Feb 2026 09:00:00 +0000", "body": "As part of your application to Cobalt Data\n\nHi Taylor Kim,\n\nPlease complete this online coding challenge within 5 days.", "expected": {"is_job_related": true, "company": "Cobalt Data", "status": "oa"}}
{"category": "greeting_adjacent", "subject": "Interview Invitation", "sender": "careers@fernwooddesign.com", "date": "Fri, 13 Feb 2026 09:00:00 +0000", "body": "We would like to invite you to interview for the Product Designer position at Fernwood Design\n\nHi Morgan,\n\nPlease pick a time below that works for you.", "expected": {"is_job_related": true, "company": "Fernwood Design", "position": "Product Designer", "status": "interview"}}
{"category": "greeting_adjacent", "subject": "Update on your application", "sender": "careers@solacesystems.com", "date": "Sat, 14 Feb 2026 09:00:00 +0000", "body": "Thank you for your interest in the Backend Developer role at Solace Systems\n\nHi Avery,\n\nUnfortunately, we have decided to move forward with other candidates for this position.", "expected": {"is_job_related": true, "company": "Solace Systems", "position": "Backend Developer", "status": "rejected"}}
{"category": "greeting_adjacent", "subject": "Offer of employment - Redwood Biotech", "sender": "hr@redwoodbiotech.com", "date": "Sun, 15 Feb 2026 09:00:00 +0000", "body": "We are pleased to offer you the Research Associate position at Redwood Biotech\n\nHi Casey,\n\nPlease find the offer of employment attached.", "expected": {"is_job_related": true, "company": "Redwood Biotech", "position": "Research Associate", "status": "offer"}}
{"category": "greeting_adjacent", "subject": "Application Received - Harborlight Media", "sender": "talent@harborlightmedia.com", "date": "Mon, 16 Feb 2026 09:00:00 +0000", "body": "We have received your application for the Marketing Coordinator position at Harborlight Media\n\nHi Reese,\n\nOur recruiting team will follow up soon.", "expected": {"is_job_related": true, "company": "Harborlight Media", "position": "Marketing Coordinator", "status": "applied"}}
{"category": "greeting_adjacent", "subject": "Phone screen with Ironclad Security", "sender": "recruiting@ironcladsecurity.com", "date": "Tue, 17 Feb 2026 09:00:00 +0000", "body": "Let's schedule a phone screen for the Security Analyst role at Ironclad Security\n\nHi Drew,\n\nWe're looking forward to connecting.", "expected": {"is_job_related": true, "company": "Ironclad Security", "position": "Security Analyst", "status": "interview"}}
{"category": "greeting_adjacent", "subject": "Your offer from Meridian Labs", "sender": "hr@meridianlabs.ai", "date": "Wed, 18 Feb 2026 09:00:00 +0000", "body": "Meridian Labs is pleased to extend an offer for the Data Scientist position\n\nHi Sam,\n\nWelcome to the team!", "expected": {"is_job_related": true, "company": "Meridian Labs", "position": "Data Scientist", "status": "offer"}}
{"category": "greeting_adjacent", "subject": "Your application to Northstar Logistics", "sender": "careers@northstarlogistics.com", "date": "Thu, 19 Feb 2026 09:00:00 +0000", "body": "We regret to inform you that we will not be moving forward with your application to Northstar Logistics for the Supply Chain Analyst position\n\nHi Jamie,\n\nWe wish you the best in your search.", "expected": {"is_job_related": true, "company": "Northstar Logistics", "position": "Supply Chain Analyst", "status": "rejected"}}
{"category": "greeting_adjacent", "subject": "Complete your technical assessment", "sender": "assessments@codesignal.com", "date": "Fri, 20 Feb 2026 09:00:00 +0000", "body": "Cascade Robotics has invited you to complete a take-home assignment for the Firmware Engineer position\n\nHi Alex,\n\nYou'll have one week to finish.", "expected": {"is_job_related": true, "company": "Cascade Robotics", "position": "Firmware Engineer", "status": "oa"}}
```

- [ ] **Step 3: Append the 10 `signature_footer` examples**

```json
{"category": "signature_footer", "subject": "Your application to Vantage Point Consulting", "sender": "careers@vantagepointconsulting.com", "date": "Sat, 21 Feb 2026 09:00:00 +0000", "body": "Thank you for applying to Vantage Point Consulting. We have received your application for the Business Analyst position and will be in touch.\n\n--\nJamie Fox\nTalent Acquisition, Vantage Point Consulting\nThis email and any attachments are confidential.", "expected": {"is_job_related": true, "company": "Vantage Point Consulting", "position": "Business Analyst", "status": "applied"}}
{"category": "signature_footer", "subject": "Next step: online assessment", "sender": "noreply@hackerrank.com", "date": "Sun, 22 Feb 2026 09:00:00 +0000", "body": "As part of your application to Lumen Financial, please complete this online coding challenge within 5 days.\n\nBest,\nThe Lumen Financial Recruiting Team\nUnsubscribe from future assessment reminders here.", "expected": {"is_job_related": true, "company": "Lumen Financial", "status": "oa"}}
{"category": "signature_footer", "subject": "Interview Invitation", "sender": "recruiting@anchorpointstudios.com", "date": "Mon, 23 Feb 2026 09:00:00 +0000", "body": "We would like to invite you to interview for the Game Designer position at Anchor Point Studios. Please pick a time below.\n\nRegards,\ncasey park\nRecruiting Coordinator\nAnchor Point Studios | 200 Creative Way", "expected": {"is_job_related": true, "company": "Anchor Point Studios", "position": "Game Designer", "status": "interview"}}
{"category": "signature_footer", "subject": "Your application to Silverline Cloud", "sender": "careers@silverlinecloud.com", "date": "Tue, 24 Feb 2026 09:00:00 +0000", "body": "We regret to inform you that we will not be moving forward with your application to Silverline Cloud for the Cloud Support Engineer position.\n\nSincerely,\nThe Silverline Cloud Team\nSent from our applicant tracking system. View in browser.", "expected": {"is_job_related": true, "company": "Silverline Cloud", "position": "Cloud Support Engineer", "status": "rejected"}}
{"category": "signature_footer", "subject": "Your offer from Fieldstone Ventures", "sender": "hr@fieldstoneventures.com", "date": "Wed, 25 Feb 2026 09:00:00 +0000", "body": "Fieldstone Ventures is pleased to extend an offer for the Investment Associate position. Welcome to the team!\n\nBest regards,\nJordan Blake\nHead of Talent\nFieldstone Ventures\nConfidentiality notice: this message may contain privileged information.", "expected": {"is_job_related": true, "company": "Fieldstone Ventures", "position": "Investment Associate", "status": "offer"}}
{"category": "signature_footer", "subject": "Application Received - Cedar Grove Foods", "sender": "talent@cedargrovefoods.com", "date": "Thu, 26 Feb 2026 09:00:00 +0000", "body": "We have received your application for the Quality Assurance Specialist position at Cedar Grove Foods. Our recruiting team will follow up soon.\n\nThanks,\nThe Cedar Grove Foods Careers Team\nManage your email preferences here.", "expected": {"is_job_related": true, "company": "Cedar Grove Foods", "position": "Quality Assurance Specialist", "status": "applied"}}
{"category": "signature_footer", "subject": "Phone screen with Brightview Energy", "sender": "recruiting@brightviewenergy.com", "date": "Fri, 27 Feb 2026 09:00:00 +0000", "body": "Let's schedule a phone screen for the Field Technician role at Brightview Energy.\n\nBest,\nRiley Chen\nSenior Recruiter\nBrightview Energy\nThis message was sent to you because you applied to a Brightview Energy opening.", "expected": {"is_job_related": true, "company": "Brightview Energy", "position": "Field Technician", "status": "interview"}}
{"category": "signature_footer", "subject": "Update on your application", "sender": "careers@waypointanalytics.com", "date": "Sat, 28 Feb 2026 09:00:00 +0000", "body": "Thank you for your interest in the Business Intelligence Analyst role at Waypoint Analytics. Unfortunately, we have decided to move forward with other candidates for this position.\n\nWarm regards,\nThe Waypoint Analytics Hiring Team\nUnsubscribe | Update Preferences | Privacy Policy", "expected": {"is_job_related": true, "company": "Waypoint Analytics", "position": "Business Intelligence Analyst", "status": "rejected"}}
{"category": "signature_footer", "subject": "Offer of employment - Outpost Aerospace", "sender": "hr@outpostaerospace.com", "date": "Sun, 1 Mar 2026 09:00:00 +0000", "body": "We are pleased to offer you the Systems Engineer position at Outpost Aerospace. Please find the offer of employment attached.\n\nCongratulations again,\nDana Reyes\nPeople Operations\nOutpost Aerospace, 88 Launch Rd", "expected": {"is_job_related": true, "company": "Outpost Aerospace", "position": "Systems Engineer", "status": "offer"}}
{"category": "signature_footer", "subject": "Application Received - Trellis Health", "sender": "careers@trellishealth.com", "date": "Mon, 2 Mar 2026 09:00:00 +0000", "body": "Thank you for applying to Trellis Health. We have received your application for the Care Coordinator position.\n\n--\nThis is an automated message from Trellis Health's applicant tracking system. Please do not reply directly to this email.", "expected": {"is_job_related": true, "company": "Trellis Health", "position": "Care Coordinator", "status": "applied"}}
```

- [ ] **Step 4: Verify the file parses and has 48 lines**

Run: `cd backend && uv run python -c "import json; lines=[json.loads(l) for l in open('evaluation/dataset.jsonl') if l.strip()]; print(len(lines))"`
Expected: prints `48`.

- [ ] **Step 5: Commit**

```bash
cd backend && git add evaluation/dataset.jsonl
git commit -m "test(evaluation): add html_noise, greeting_adjacent, signature_footer dataset examples"
```

---

## Task 3: Add `recruiter_outreach`, `ambiguous`, `sender_variation`, `messy_phrasing` dataset examples

**Files:**
- Modify: `backend/evaluation/dataset.jsonl` (append 40 new lines)

**Interfaces:**
- Consumes: nothing new.
- Produces: the final 40 dataset lines — 88 total after this task. Same caveat as
  Task 2: `test_evaluation_accuracy.py`'s bars are not touched until Task 11.

Design note carried into later tasks: the `recruiter_outreach` bodies below all use the
literal phrase shape `(?:for|to|in|you|about) the <position> (?:opening|opportunity) at
<company>` deliberately — Task 7 broadens `fields.py`'s company/position template to
accept `opening`/`opportunity` (today it only accepts `position`/`role`) and to accept
`about` as a leading word (today only `for|to|in|you`). Task 9 adds `\bopening\b` and
`\bopportunity\b` to `GENERIC_JOB_PATTERNS` at weight 3 — without that, these examples'
`job_signal` never reaches `JOB_RELATED_THRESHOLD` (3) since none of them contain any
existing `STATUS_PATTERNS`/`GENERIC_JOB_PATTERNS` phrase, and today's classifier would
wrongly score them `is_job_related=false`.

- [ ] **Step 1: Append the 10 `recruiter_outreach` examples**

All `status: "other"` — cold outreach about a potential role, not a status update on an
existing application. `EmailExtraction.status` already accepts `"other"`; no schema
change needed.

```json
{"category": "recruiter_outreach", "subject": "Are you open to new opportunities?", "sender": "recruiter@pinnaclerobotics.com", "date": "Tue, 3 Mar 2026 09:00:00 +0000", "body": "Hi, I think you'd be a great fit for the Senior Mechanical Engineer opening at Pinnacle Robotics. Would you be open to a quick call this week?", "expected": {"is_job_related": true, "company": "Pinnacle Robotics", "position": "Senior Mechanical Engineer", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Cobalt Data - Data Engineer role", "sender": "jane.kim@cobaltdata.io", "date": "Wed, 4 Mar 2026 09:00:00 +0000", "body": "Hi there, I'm reaching out about the Data Engineer opportunity at Cobalt Data. Your background seems like a strong match.", "expected": {"is_job_related": true, "company": "Cobalt Data", "position": "Data Engineer", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Thought of you for a role at Fernwood Design", "sender": "talent@fernwooddesign.com", "date": "Thu, 5 Mar 2026 09:00:00 +0000", "body": "Hello, I wanted to reach out to you about the Product Designer opening at Fernwood Design. Interested in chatting?", "expected": {"is_job_related": true, "company": "Fernwood Design", "position": "Product Designer", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Backend opportunity at Solace Systems", "sender": "recruiting@solacesystems.com", "date": "Fri, 6 Mar 2026 09:00:00 +0000", "body": "Hi, I'm reaching out to you about the Backend Developer opportunity at Solace Systems. Would you be open to a conversation?", "expected": {"is_job_related": true, "company": "Solace Systems", "position": "Backend Developer", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Research role at Redwood Biotech", "sender": "careers@redwoodbiotech.com", "date": "Sat, 7 Mar 2026 09:00:00 +0000", "body": "Hi, I wanted to tell you about the Research Associate opening at Redwood Biotech. Happy to share more details if you're interested.", "expected": {"is_job_related": true, "company": "Redwood Biotech", "position": "Research Associate", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Marketing role that might interest you", "sender": "rchen@harborlightmedia.com", "date": "Sun, 8 Mar 2026 09:00:00 +0000", "body": "Hi, I'm reaching out to you about the Marketing Coordinator opening at Harborlight Media. Worth a quick chat?", "expected": {"is_job_related": true, "company": "Harborlight Media", "position": "Marketing Coordinator", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Security Analyst opportunity", "sender": "talent@ironcladsecurity.com", "date": "Mon, 9 Mar 2026 09:00:00 +0000", "body": "Hello, I wanted to reach out to you about the Security Analyst opening at Ironclad Security. Are you open to hearing more?", "expected": {"is_job_related": true, "company": "Ironclad Security", "position": "Security Analyst", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Data Science opening at Meridian Labs", "sender": "recruiter@meridianlabs.ai", "date": "Tue, 10 Mar 2026 09:00:00 +0000", "body": "Hi, I'm reaching out to you about the Data Scientist opening at Meridian Labs. Let me know if you'd like to connect.", "expected": {"is_job_related": true, "company": "Meridian Labs", "position": "Data Scientist", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Supply chain role at Northstar Logistics", "sender": "careers@northstarlogistics.com", "date": "Wed, 11 Mar 2026 09:00:00 +0000", "body": "Hi, I wanted to reach out to you about the Supply Chain Analyst opening at Northstar Logistics. Open to a brief call this week?", "expected": {"is_job_related": true, "company": "Northstar Logistics", "position": "Supply Chain Analyst", "status": "other"}}
{"category": "recruiter_outreach", "subject": "Firmware role at Cascade Robotics", "sender": "talent@cascaderobotics.com", "date": "Thu, 12 Mar 2026 09:00:00 +0000", "body": "Hello, I'm reaching out to you about the Firmware Engineer opening at Cascade Robotics given your background. Interested in learning more?", "expected": {"is_job_related": true, "company": "Cascade Robotics", "position": "Firmware Engineer", "status": "other"}}
```

- [ ] **Step 2: Append the 10 `ambiguous` examples**

All `is_job_related: false` — job-adjacent vocabulary in a non-application context,
deliberately harder than the original 6 negatives (no "unsubscribe"/"% off"-style
obvious spam markers).

```json
{"category": "ambiguous", "subject": "5 Data Scientist jobs matching your search", "sender": "jobalerts-noreply@linkedin.com", "date": "Fri, 13 Mar 2026 09:00:00 +0000", "body": "New positions for you this week: Data Scientist at Meridian Labs, Backend Developer at Solace Systems. See more candidates like you are considering these roles. Unsubscribe from job alerts.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "Your weekly career newsletter", "sender": "newsletter@careerhub.com", "date": "Sat, 14 Mar 2026 09:00:00 +0000", "body": "This week: how to ace your next interview, 10 tips for salary negotiation, and why more candidates are switching industries. Read on for more career advice.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "You're invited: Women in Tech networking event", "sender": "events@techmeetup.com", "date": "Sun, 15 Mar 2026 09:00:00 +0000", "body": "Join us for an evening of networking with recruiters and candidates from top companies. Light refreshments will be served. RSVP below.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "Congrats on your work anniversary!", "sender": "hr-announcements@currentcompany.com", "date": "Mon, 16 Mar 2026 09:00:00 +0000", "body": "Happy 2-year anniversary! Thank you for being a valued member of our team. Your manager will follow up with more details about your recognition award.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "New course: Interview Prep Masterclass", "sender": "noreply@learningplatform.com", "date": "Tue, 17 Mar 2026 09:00:00 +0000", "body": "Enroll now in our new course covering technical interviews, take-home assignments, and offer negotiation. 50% off this week only. Unsubscribe here.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "Your resume was viewed 12 times this week", "sender": "notify@jobboard.com", "date": "Wed, 18 Mar 2026 09:00:00 +0000", "body": "Recruiters are checking out candidates like you. Boost your visibility by upgrading your profile. See who's hiring near you.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "Reminder: complete your benefits enrollment", "sender": "benefits@currentcompany.com", "date": "Thu, 19 Mar 2026 09:00:00 +0000", "body": "This is a reminder to complete your annual benefits enrollment by Friday. Please review your options and submit your selections in the portal.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "Meetup: Hiring managers panel this Thursday", "sender": "community@devmeetup.com", "date": "Fri, 20 Mar 2026 09:00:00 +0000", "body": "Come hear from hiring managers about what they look for in candidates and how the interview process works at their companies. Free pizza provided.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "Your friend Sam is hiring!", "sender": "notify@jobboard.com", "date": "Sat, 21 Mar 2026 09:00:00 +0000", "body": "Sam Rivera just posted a new position: Software Engineer at Cinder Block Games. See more jobs your network is sharing.", "expected": {"is_job_related": false}}
{"category": "ambiguous", "subject": "Panel recap: How we hire at top startups", "sender": "digest@startupnewsletter.com", "date": "Sun, 22 Mar 2026 09:00:00 +0000", "body": "In case you missed it, here's a recap of last week's panel on hiring practices, candidate experience, and interview design at fast-growing startups.", "expected": {"is_job_related": false}}
```

- [ ] **Step 3: Append the 10 `sender_variation` examples**

Exercises subdomain stripping (`careers.`/`jobs.`/`talent.`/`recruiting.` — today only
`mail.`/`notifications.`/`e.`/`no-reply.`/`noreply.` are stripped), and non-`ATS_DOMAINS`
assessment platforms (`testgorilla.com`, `codility.com`) that today incorrectly become
the domain-derived company guess instead of falling through. Where an example's
`expected.company` is a single un-spaced capitalized word (e.g. `"Pinnaclerobotics"`),
that's intentional — it's the best a domain-label fallback can produce (same accepted
imperfection as the original dataset's `"Eta"` vs. real `"Eta Ltd"`), not a typo.

**Correction found during Task 7 pre-flight verification** (before Task 7 was
dispatched, simulating its planned regex against the full dataset): the "Offer
details"/`recruiting.trellishealth.com` example below was designed to exercise the
`recruiting.` subdomain-prefix fix via the domain-fallback path, but its body text
("Trellis Health is pleased to extend an offer...") also matches Task 7's new
`_COMPANY_PLEASED_TO_OFFER_RE` template — which correctly wins (template tier beats
domain-fallback tier), extracting the fuller, more accurate `"Trellis Health"` instead
of the domain-derived `"Trellishealth"`. This is a genuine improvement, not a bug — the
template match makes the domain-fallback path unreachable for this one example. Its
`expected.company` below is corrected to `"Trellis Health"` to match. The `recruiting.`
prefix-stripping behavior itself stays fully covered independently — Task 7 adds a
direct unit test on `extract_sender_domain("recruiting@recruiting.trellishealth.com")`
in `test_classifier_text.py` — so there's no coverage gap, just a corrected expected
value in this one dataset line. (The other three subdomain examples — `careers.`,
`jobs.`, `talent.` — have bodies with no "is/are pleased to offer" phrasing, so they
still correctly exercise the domain-fallback path end-to-end.)

```json
{"category": "sender_variation", "subject": "Following up on your application", "sender": "Talent Team <talent@careers.pinnaclerobotics.com>", "date": "Mon, 23 Mar 2026 09:00:00 +0000", "body": "Hi, thanks for your patience. We have received your application and are currently reviewing candidates for this position.", "expected": {"is_job_related": true, "company": "Pinnaclerobotics", "status": "applied"}}
{"category": "sender_variation", "subject": "Interview scheduling", "sender": "Jobs at Cobalt <jobs@jobs.cobaltdata.io>", "date": "Tue, 24 Mar 2026 09:00:00 +0000", "body": "We would like to invite you to interview. Please pick a time that works best for you from the calendar below.", "expected": {"is_job_related": true, "company": "Cobaltdata", "status": "interview"}}
{"category": "sender_variation", "subject": "Assessment reminder", "sender": "Talent Acquisition <talent@talent.fernwooddesign.com>", "date": "Wed, 25 Mar 2026 09:00:00 +0000", "body": "This is a reminder to complete your online assessment before the deadline. Let us know if you have any questions.", "expected": {"is_job_related": true, "company": "Fernwooddesign", "status": "oa"}}
{"category": "sender_variation", "subject": "Application confirmation", "sender": "No Reply <donotreply@bamboohr.com>", "date": "Thu, 26 Mar 2026 09:00:00 +0000", "body": "Thank you for applying to Vantage Point Consulting through our careers portal. We have received your application for the Business Analyst position.", "expected": {"is_job_related": true, "company": "Vantage Point Consulting", "position": "Business Analyst", "status": "applied"}}
{"category": "sender_variation", "subject": "Complete your assessment for Ironclad Security", "sender": "Assessments <noreply@testgorilla.com>", "date": "Fri, 27 Mar 2026 09:00:00 +0000", "body": "Ironclad Security has invited you to complete a take-home assignment for the Security Analyst position via TestGorilla.", "expected": {"is_job_related": true, "company": "Ironclad Security", "position": "Security Analyst", "status": "oa"}}
{"category": "sender_variation", "subject": "Offer details", "sender": "Recruiting <recruiting@recruiting.trellishealth.com>", "date": "Sat, 28 Mar 2026 09:00:00 +0000", "body": "Trellis Health is pleased to extend an offer for the Care Coordinator position. Welcome aboard!", "expected": {"is_job_related": true, "company": "Trellis Health", "position": "Care Coordinator", "status": "offer"}}
{"category": "sender_variation", "subject": "Coding test for Cascade Robotics", "sender": "codility <no-reply@codility.com>", "date": "Sun, 29 Mar 2026 09:00:00 +0000", "body": "Cascade Robotics has invited you to complete a coding challenge for the Firmware Engineer position.", "expected": {"is_job_related": true, "company": "Cascade Robotics", "position": "Firmware Engineer", "status": "oa"}}
{"category": "sender_variation", "subject": "Your application to Meridian Labs", "sender": "Meridian Labs <careers@jobs.meridianlabs.ai>", "date": "Mon, 30 Mar 2026 09:00:00 +0000", "body": "Thank you for applying to Meridian Labs. We have received your application for the Machine Learning Engineer position.", "expected": {"is_job_related": true, "company": "Meridian Labs", "position": "Machine Learning Engineer", "status": "applied"}}
{"category": "sender_variation", "subject": "Update on your application", "sender": "HR Team <hr@northstarlogistics.com>", "date": "Tue, 31 Mar 2026 09:00:00 +0000", "body": "Thank you for your interest in the Supply Chain Analyst role at Northstar Logistics. Unfortunately, we have decided to move forward with other candidates for this position.", "expected": {"is_job_related": true, "company": "Northstar Logistics", "position": "Supply Chain Analyst", "status": "rejected"}}
{"category": "sender_variation", "subject": "Interview invitation", "sender": "careers@careers.outpostaerospace.com", "date": "Wed, 1 Apr 2026 09:00:00 +0000", "body": "We would like to invite you to interview for the Systems Engineer position at Outpost Aerospace.", "expected": {"is_job_related": true, "company": "Outpost Aerospace", "position": "Systems Engineer", "status": "interview"}}
```

- [ ] **Step 4: Append the 10 `messy_phrasing` examples**

Each is deliberately designed around one specific, new `STATUS_PATTERNS`/
`GENERIC_JOB_PATTERNS` entry that Task 9 adds — see that task for the exact regex
each one motivates. Two examples (`Cedar Grove Foods` and `Waypoint Analytics`, marked
below) use curly apostrophes/dashes on purpose, to verify Task 6's punctuation
normalization: today, `’` (curly apostrophe) makes the existing `received your
application` pattern silently fail to match (verified: `we've` matches, `we’ve`
does not).

```json
{"category": "messy_phrasing", "subject": "Your application to Vantage Point Consulting", "sender": "careers@vantagepointconsulting.com", "date": "Thu, 2 Apr 2026 09:00:00 +0000", "body": "Your application is now in our system and our team will be reviewing it shortly for the Operations Associate role at Vantage Point Consulting.", "expected": {"is_job_related": true, "company": "Vantage Point Consulting", "position": "Operations Associate", "status": "applied"}}
{"category": "messy_phrasing", "subject": "Skills assessment - Lumen Financial", "sender": "assessments@lumenfinancial.com", "date": "Fri, 3 Apr 2026 09:00:00 +0000", "body": "You've been asked to complete a skills assessment for the Financial Analyst role at Lumen Financial. Please finish it within the week.", "expected": {"is_job_related": true, "company": "Lumen Financial", "position": "Financial Analyst", "status": "oa"}}
{"category": "messy_phrasing", "subject": "Quick chat about Anchor Point Studios?", "sender": "recruiting@anchorpointstudios.com", "date": "Sat, 4 Apr 2026 09:00:00 +0000", "body": "We'd love to set up a time to chat about the Game Designer opening at Anchor Point Studios — are you free later this week?", "expected": {"is_job_related": true, "company": "Anchor Point Studios", "position": "Game Designer", "status": "interview"}}
{"category": "messy_phrasing", "subject": "Your application to Cedar Grove Foods", "sender": "careers@cedargrovefoods.com", "date": "Sun, 5 Apr 2026 09:00:00 +0000", "body": "We’ve received your application for the Logistics Coordinator position at Cedar Grove Foods.", "expected": {"is_job_related": true, "company": "Cedar Grove Foods", "position": "Logistics Coordinator", "status": "applied"}}
{"category": "messy_phrasing", "subject": "Update on your application", "sender": "careers@waypointanalytics.com", "date": "Mon, 6 Apr 2026 09:00:00 +0000", "body": "After careful consideration, we won’t be moving forward with your candidacy for the Data Engineer role at Waypoint Analytics.", "expected": {"is_job_related": true, "company": "Waypoint Analytics", "position": "Data Engineer", "status": "rejected"}}
{"category": "messy_phrasing", "subject": "Great news from Fieldstone Ventures", "sender": "hr@fieldstoneventures.com", "date": "Tue, 7 Apr 2026 09:00:00 +0000", "body": "Great news — Fieldstone Ventures would like to extend you an offer for the Investment Associate role. Congratulations!", "expected": {"is_job_related": true, "company": "Fieldstoneventures", "position": "Investment Associate", "status": "offer"}}
{"category": "messy_phrasing", "subject": "Your application to Brightview Energy", "sender": "careers@brightviewenergy.com", "date": "Wed, 8 Apr 2026 09:00:00 +0000", "body": "Just a note to say we've got your application for the Field Technician role at Brightview Energy and it's in good hands.", "expected": {"is_job_related": true, "company": "Brightview Energy", "position": "Field Technician", "status": "applied"}}
{"category": "messy_phrasing", "subject": "Ironclad Security - quick question", "sender": "talent@ironcladsecurity.com", "date": "Thu, 9 Apr 2026 09:00:00 +0000", "body": "Would you be available for a chat about the Security Analyst position at Ironclad Security sometime this week?", "expected": {"is_job_related": true, "company": "Ironclad Security", "position": "Security Analyst", "status": "interview"}}
{"category": "messy_phrasing", "subject": "Welcome to Outpost Aerospace", "sender": "hr@outpostaerospace.com", "date": "Fri, 10 Apr 2026 09:00:00 +0000", "body": "Outpost Aerospace is thrilled to bring you on board as our new Systems Engineer. Formal offer details are attached.", "expected": {"is_job_related": true, "company": "Outpostaerospace", "position": "Systems Engineer", "status": "offer"}}
{"category": "messy_phrasing", "subject": "Update on your application", "sender": "careers@cinderblockgames.com", "date": "Sat, 11 Apr 2026 09:00:00 +0000", "body": "We wanted to personally let you know that we’ve chosen to move in a different direction for the Level Designer role at Cinder Block Games.", "expected": {"is_job_related": true, "company": "Cinder Block Games", "position": "Level Designer", "status": "rejected"}}
```

- [ ] **Step 5: Verify the file parses and has 88 lines**

Run: `cd backend && uv run python -c "import json; lines=[json.loads(l) for l in open('evaluation/dataset.jsonl') if l.strip()]; print(len(lines)); from collections import Counter; print(Counter(l['category'] for l in lines))"`
Expected: prints `88`, and a category count of `clean_template: 18` plus `10` for each
of the other 7 categories.

- [ ] **Step 6: Commit**

```bash
cd backend && git add evaluation/dataset.jsonl
git commit -m "test(evaluation): add recruiter_outreach, ambiguous, sender_variation, messy_phrasing dataset examples"
```

---

## Task 4: Rebuild `run_eval.py` into a full metrics harness, add `compare.py`, record the baseline

**Files:**
- Modify: `backend/evaluation/run_eval.py`
- Create: `backend/evaluation/compare.py`
- Create: `backend/evaluation/baseline_metrics.json` (generated by Step 5, not hand-written)
- Create: `backend/tests/test_evaluation_run_eval.py`
- Modify: `backend/tests/test_evaluation_accuracy.py` (add one sync-check test only —
  bars are untouched until Task 11)

**Interfaces:**
- Consumes: `app.classifier.extractor.RuleBasedExtractor` (unmodified in this task),
  `app.classifier.schemas.EmailExtraction`.
- Produces: `evaluate(extractor, examples: list[dict]) -> dict` — the function every
  later task's checkpoint re-run calls (via `compare.py`). Return shape:
  `{"overall": <metrics>, "by_category": {category: <metrics>}}` where `<metrics>` is
  `{"n", "classification_accuracy", "is_job_related": {"precision","recall","f1","tp",
  "fp","fn","tn"}, "per_status": {status: {"precision","recall","f1"}}, "status_accuracy",
  "company_exact_accuracy", "company_fuzzy_accuracy", "position_exact_accuracy",
  "position_fuzzy_accuracy", "precision_at_threshold", "auto_apply_rate",
  "review_rate"}`. Also `load_dataset(path=DATASET_PATH) -> list[dict]` and the module
  constant `CONFIDENCE_THRESHOLD = 0.85`.

Note on `precision_at_threshold`/`auto_apply_rate`/`review_rate`: this harness only has
the classifier's output, not the pipeline's matching/trust-model state (no existing
applications to match against), so "cleared threshold" here means "would be an
auto-apply *candidate*" — `is_job_related` and `confidence >= CONFIDENCE_THRESHOLD` —
not a full replay of `pipeline/service.py::_apply_decision`'s three gates. That's an
intentional, honest scope: it measures exactly what the classifier controls.

- [ ] **Step 1: Write `backend/evaluation/run_eval.py`**

```python
# backend/evaluation/run_eval.py
"""Run the classification/extraction pipeline against a labeled dataset and report the
full Phase 6 metrics set (precision/recall/F1, per-status breakdown, exact and fuzzy
extraction accuracy, precision-at-threshold, auto-apply rate, review rate), both
overall and per dataset `category`.

Local classification has no external dependency and costs nothing to run — safe to run
as often as you like. Run manually:

    cd backend
    uv run python -m evaluation.run_eval

evaluate() is also imported directly by evaluation/compare.py (checkpoint diffing) and
by tests/test_evaluation_run_eval.py.
"""

from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from app.classifier.extractor import RuleBasedExtractor

DATASET_PATH = Path(__file__).parent / "dataset.jsonl"

# Mirrors settings.classification_confidence_threshold (app/core/config.py). Duplicated
# rather than imported: Settings() requires .env-backed fields (database_url, Google
# OAuth credentials, the Gmail token encryption key, ...) that this standalone,
# zero-setup evaluation script must not depend on just to compute a metrics report.
# tests/test_evaluation_accuracy.py::test_confidence_threshold_constant_matches_settings
# keeps the two from silently drifting apart.
CONFIDENCE_THRESHOLD = 0.85

STATUSES = ["applied", "oa", "interview", "rejected", "offer", "other"]

# rapidfuzz token_sort_ratio threshold for "close enough to call the same extraction"
# (trailing punctuation, minor casing/spacing differences) — a couple points stricter
# than pipeline/matching.py's STRONG_MATCH_THRESHOLD=85, since that threshold answers a
# different question ("is this likely the same application") than this one ("did the
# classifier basically get the right string").
FUZZY_MATCH_THRESHOLD = 90


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _fuzzy_match(actual: str | None, expected: str | None) -> bool:
    if actual is None or expected is None:
        return actual == expected
    return fuzz.token_sort_ratio(actual, expected) >= FUZZY_MATCH_THRESHOLD


def _new_bucket() -> dict:
    return {
        "n": 0,
        "tp": 0, "fp": 0, "fn": 0, "tn": 0,
        "status_tp": defaultdict(int), "status_fp": defaultdict(int), "status_fn": defaultdict(int),
        "status_total": 0, "status_correct": 0,
        "company_total": 0, "company_exact": 0, "company_fuzzy": 0,
        "position_total": 0, "position_exact": 0, "position_fuzzy": 0,
        "cleared_threshold": 0,
        "cleared_threshold_correct": 0,
        "true_positive_total": 0,
        "true_positive_cleared": 0,
        "review_candidates": 0,
    }


def _score_one(bucket: dict, result, expected: dict) -> None:
    bucket["n"] += 1
    predicted_related = result.is_job_related
    expected_related = expected["is_job_related"]

    if predicted_related and expected_related:
        bucket["tp"] += 1
    elif predicted_related and not expected_related:
        bucket["fp"] += 1
    elif not predicted_related and expected_related:
        bucket["fn"] += 1
    else:
        bucket["tn"] += 1

    fully_correct = predicted_related == expected_related

    if expected_related:
        bucket["true_positive_total"] += 1

        expected_status = expected.get("status")
        if expected_status:
            # Scoped to true positives only (expected_related=True): a false-positive
            # item's status guess is deliberately not counted here, since the metric
            # this feeds ("does the classifier confuse rejected vs. interview") is
            # about confusion among genuinely job-related mail, not spam.
            bucket["status_total"] += 1
            status_correct = result.status == expected_status
            bucket["status_correct"] += int(status_correct)
            if status_correct:
                bucket["status_tp"][expected_status] += 1
            else:
                bucket["status_fn"][expected_status] += 1
                if result.status:
                    bucket["status_fp"][result.status] += 1
            fully_correct = fully_correct and status_correct

        if "company" in expected:
            bucket["company_total"] += 1
            expected_company = expected["company"]
            exact = result.company == expected_company
            bucket["company_exact"] += int(exact)
            bucket["company_fuzzy"] += int(exact or _fuzzy_match(result.company, expected_company))
            fully_correct = fully_correct and exact

        if "position" in expected:
            bucket["position_total"] += 1
            expected_position = expected["position"]
            exact = result.position == expected_position
            bucket["position_exact"] += int(exact)
            bucket["position_fuzzy"] += int(exact or _fuzzy_match(result.position, expected_position))
            fully_correct = fully_correct and exact

    cleared = predicted_related and result.confidence >= CONFIDENCE_THRESHOLD
    if cleared:
        bucket["cleared_threshold"] += 1
        bucket["cleared_threshold_correct"] += int(fully_correct)
    elif predicted_related:
        bucket["review_candidates"] += 1

    if expected_related and cleared:
        bucket["true_positive_cleared"] += 1


def _safe_div(n: int, d: int) -> float:
    return n / d if d else 0.0


def _finalize(bucket: dict) -> dict:
    tp, fp, fn, tn = bucket["tp"], bucket["fp"], bucket["fn"], bucket["tn"]
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall) if (precision + recall) else 0.0

    per_status = {}
    for status in STATUSES:
        s_tp, s_fp, s_fn = bucket["status_tp"][status], bucket["status_fp"][status], bucket["status_fn"][status]
        if not (s_tp or s_fp or s_fn):
            continue
        s_precision = _safe_div(s_tp, s_tp + s_fp)
        s_recall = _safe_div(s_tp, s_tp + s_fn)
        s_f1 = _safe_div(2 * s_precision * s_recall, s_precision + s_recall) if (s_precision + s_recall) else 0.0
        per_status[status] = {"precision": round(s_precision, 3), "recall": round(s_recall, 3), "f1": round(s_f1, 3)}

    return {
        "n": bucket["n"],
        "classification_accuracy": round(_safe_div(tp + tn, bucket["n"]), 3),
        "is_job_related": {
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        },
        "per_status": per_status,
        "status_accuracy": round(_safe_div(bucket["status_correct"], bucket["status_total"]), 3),
        "company_exact_accuracy": round(_safe_div(bucket["company_exact"], bucket["company_total"]), 3),
        "company_fuzzy_accuracy": round(_safe_div(bucket["company_fuzzy"], bucket["company_total"]), 3),
        "position_exact_accuracy": round(_safe_div(bucket["position_exact"], bucket["position_total"]), 3),
        "position_fuzzy_accuracy": round(_safe_div(bucket["position_fuzzy"], bucket["position_total"]), 3),
        "precision_at_threshold": round(_safe_div(bucket["cleared_threshold_correct"], bucket["cleared_threshold"]), 3),
        "auto_apply_rate": round(_safe_div(bucket["true_positive_cleared"], bucket["true_positive_total"]), 3),
        "review_rate": round(_safe_div(bucket["review_candidates"], bucket["true_positive_total"]), 3),
    }


def evaluate(extractor, examples: list[dict]) -> dict[str, Any]:
    """Run `extractor` over `examples` and compute the full metrics set, both overall
    and broken out per dataset `category`. Returns a plain, JSON-serializable dict."""
    overall = _new_bucket()
    by_category: dict[str, dict] = defaultdict(_new_bucket)

    for example in examples:
        result = extractor.classify_and_extract(
            subject=example["subject"],
            sender=example["sender"],
            date=example["date"],
            body=example["body"],
        )
        category = example.get("category", "uncategorized")
        for bucket in (overall, by_category[category]):
            _score_one(bucket, result, example["expected"])

    return {
        "overall": _finalize(overall),
        "by_category": {cat: _finalize(b) for cat, b in by_category.items()},
    }


def _print_report(report: dict) -> None:
    overall = report["overall"]
    rel = overall["is_job_related"]
    print(f"n={overall['n']}")
    print(f"is_job_related: precision={rel['precision']} recall={rel['recall']} f1={rel['f1']} "
          f"(tp={rel['tp']} fp={rel['fp']} fn={rel['fn']} tn={rel['tn']})")
    for status, m in overall["per_status"].items():
        print(f"  status={status}: precision={m['precision']} recall={m['recall']} f1={m['f1']}")
    print(f"company: exact={overall['company_exact_accuracy']} fuzzy={overall['company_fuzzy_accuracy']}")
    print(f"position: exact={overall['position_exact_accuracy']} fuzzy={overall['position_fuzzy_accuracy']}")
    print(f"precision_at_threshold={overall['precision_at_threshold']} "
          f"auto_apply_rate={overall['auto_apply_rate']} review_rate={overall['review_rate']}")
    print()
    print("By category:")
    for category, m in sorted(report["by_category"].items()):
        print(
            f"  {category} (n={m['n']}): is_job_related_f1={m['is_job_related']['f1']} "
            f"company_fuzzy={m['company_fuzzy_accuracy']} position_fuzzy={m['position_fuzzy_accuracy']} "
            f"precision_at_threshold={m['precision_at_threshold']} auto_apply_rate={m['auto_apply_rate']}"
        )


def main() -> None:
    report = evaluate(RuleBasedExtractor(), load_dataset())
    _print_report(report)


if __name__ == "__main__":
    import json
    main()
```

Note: `import json` is used by `load_dataset` at module scope — move `import json` to
the top of the file with the other imports (it's written inside the `if __name__ ==
"__main__":` guard above only to keep this diff readable line-by-line; the actual file
must import it normally at the top, alongside `from collections import defaultdict`
etc.).

- [ ] **Step 2: Run it once against the (still-unmodified) `RuleBasedExtractor`**

Run: `cd backend && uv run python -m evaluation.run_eval`
Expected: prints a full report with no exceptions. Numbers will look considerably worse
than the old 18-example run's `18/18` — that's expected and exactly what Task 7 §7 of
the spec predicted (the new dataset is deliberately harder).

- [ ] **Step 3: Write `backend/evaluation/compare.py`**

```python
# backend/evaluation/compare.py
"""Diff the current RuleBasedExtractor's metrics against the recorded Phase 6 baseline
(evaluation/baseline_metrics.json, written once by Task 4 before any classifier code
changed). Run after every checkpoint:

    cd backend
    uv run python -m evaluation.compare
"""

import json
from pathlib import Path

from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import evaluate, load_dataset

BASELINE_PATH = Path(__file__).parent / "baseline_metrics.json"

_TRACKED_METRICS: list[tuple[str, ...]] = [
    ("is_job_related", "precision"),
    ("is_job_related", "recall"),
    ("is_job_related", "f1"),
    ("company_exact_accuracy",),
    ("company_fuzzy_accuracy",),
    ("position_exact_accuracy",),
    ("position_fuzzy_accuracy",),
    ("precision_at_threshold",),
    ("auto_apply_rate",),
    ("review_rate",),
]


def _get(section: dict, path: tuple[str, ...]) -> float:
    value: Any = section
    for key in path:
        value = value[key]
    return value


def main() -> None:
    if not BASELINE_PATH.exists():
        raise SystemExit(
            f"{BASELINE_PATH} does not exist yet. Task 4 records it once, before any "
            "classifier code changes, by running today's extractor and saving its "
            "evaluate() output there. Nothing to compare against."
        )
    baseline = json.loads(BASELINE_PATH.read_text())
    current = evaluate(RuleBasedExtractor(), load_dataset())

    print(f"{'metric':<28} {'baseline':>10} {'current':>10} {'delta':>10}")
    for path in _TRACKED_METRICS:
        label = ".".join(path)
        b, c = _get(baseline["overall"], path), _get(current["overall"], path)
        delta = c - b
        marker = "  REGRESSION" if delta < -0.001 else ""
        print(f"{label:<28} {b:>10.3f} {c:>10.3f} {delta:>+10.3f}{marker}")

    print()
    header = f"{'category':<20} {'is_job_related_f1':>20} {'auto_apply_rate':>18} {'precision_at_threshold':>24}"
    print(header)
    for category in sorted(current["by_category"]):
        cur = current["by_category"][category]
        base = baseline["by_category"].get(category, {})
        base_f1 = base.get("is_job_related", {}).get("f1", "n/a")
        base_auto = base.get("auto_apply_rate", "n/a")
        base_prec = base.get("precision_at_threshold", "n/a")
        print(
            f"{category:<20} "
            f"{cur['is_job_related']['f1']:>10.3f} (was {base_f1})  "
            f"{cur['auto_apply_rate']:>8.3f} (was {base_auto})  "
            f"{cur['precision_at_threshold']:>10.3f} (was {base_prec})"
        )


if __name__ == "__main__":
    from typing import Any

    main()
```

Note: same as Step 1 — move `import json`, `from typing import Any`, and the `Path`
import to the top of the file in normal import style; they're shown split out above
only for diff readability.

- [ ] **Step 4: Write `backend/tests/test_evaluation_run_eval.py`**

```python
# backend/tests/test_evaluation_run_eval.py
from app.classifier.schemas import EmailExtraction
from evaluation.run_eval import evaluate


class _StubExtractor:
    """Returns pre-built EmailExtraction results in order, ignoring its inputs —
    lets these tests assert evaluate()'s arithmetic against known ground truth without
    depending on RuleBasedExtractor's actual behavior."""

    def __init__(self, results: list[EmailExtraction]) -> None:
        self._results = iter(results)

    def classify_and_extract(self, *, subject, sender, date, body) -> EmailExtraction:
        return next(self._results)


def _example(expected: dict) -> dict:
    return {
        "subject": "s", "sender": "a@a.com", "date": "Mon, 1 Jan 2026 00:00:00 +0000",
        "body": "b", "expected": expected,
    }


def test_evaluate_computes_precision_recall_f1_for_is_job_related() -> None:
    examples = [
        _example({"is_job_related": True}),
        _example({"is_job_related": True}),
        _example({"is_job_related": False}),
        _example({"is_job_related": False}),
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.9),   # TP
        EmailExtraction(is_job_related=False, confidence=0.1),  # FN
        EmailExtraction(is_job_related=True, confidence=0.9),   # FP
        EmailExtraction(is_job_related=False, confidence=0.1),  # TN
    ]
    report = evaluate(_StubExtractor(results), examples)
    assert report["overall"]["is_job_related"] == {
        "precision": 0.5, "recall": 0.5, "f1": 0.5, "tp": 1, "fp": 1, "fn": 1, "tn": 1,
    }


def test_evaluate_fuzzy_company_match_tolerates_trailing_punctuation() -> None:
    examples = [_example({"is_job_related": True, "company": "Acme Corp", "status": "applied"})]
    results = [EmailExtraction(is_job_related=True, confidence=0.9, company="Acme Corp.", status="applied")]
    report = evaluate(_StubExtractor(results), examples)
    overall = report["overall"]
    assert overall["company_exact_accuracy"] == 0.0
    assert overall["company_fuzzy_accuracy"] == 1.0


def test_evaluate_computes_threshold_and_rate_metrics() -> None:
    examples = [
        _example({"is_job_related": True, "status": "applied"}),
        _example({"is_job_related": True, "status": "applied"}),
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.9, status="applied"),  # clears, correct
        EmailExtraction(is_job_related=True, confidence=0.5, status="applied"),  # below threshold
    ]
    report = evaluate(_StubExtractor(results), examples)
    overall = report["overall"]
    assert overall["precision_at_threshold"] == 1.0
    assert overall["auto_apply_rate"] == 0.5
    assert overall["review_rate"] == 0.5


def test_evaluate_breaks_out_metrics_by_category() -> None:
    examples = [
        {**_example({"is_job_related": True}), "category": "cat_a"},
        {**_example({"is_job_related": False}), "category": "cat_b"},
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.9),
        EmailExtraction(is_job_related=False, confidence=0.1),
    ]
    report = evaluate(_StubExtractor(results), examples)
    assert set(report["by_category"]) == {"cat_a", "cat_b"}
    assert report["by_category"]["cat_a"]["is_job_related"]["tp"] == 1
    assert report["by_category"]["cat_b"]["is_job_related"]["tn"] == 1


def test_evaluate_computes_overall_classification_and_status_accuracy() -> None:
    examples = [
        _example({"is_job_related": True, "status": "applied"}),
        _example({"is_job_related": True, "status": "rejected"}),
        _example({"is_job_related": False}),
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.9, status="applied"),   # correct
        EmailExtraction(is_job_related=True, confidence=0.9, status="interview"),  # wrong status
        EmailExtraction(is_job_related=False, confidence=0.1),                     # correct
    ]
    report = evaluate(_StubExtractor(results), examples)
    overall = report["overall"]
    assert overall["classification_accuracy"] == 1.0  # all 3 is_job_related predictions correct
    assert overall["status_accuracy"] == 0.5           # 1 of 2 true-positive statuses correct
```

- [ ] **Step 5: Run the new tests**

Run: `cd backend && uv run pytest tests/test_evaluation_run_eval.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 6: Record the baseline**

Run:

```bash
cd backend && uv run python -c "
import json
from pathlib import Path
from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import evaluate, load_dataset

report = evaluate(RuleBasedExtractor(), load_dataset())
Path('evaluation/baseline_metrics.json').write_text(json.dumps(report, indent=2) + chr(10))
print('baseline recorded:', report['overall'])
"
```

Expected: prints the overall baseline metrics dict; `evaluation/baseline_metrics.json`
now exists. This is the one and only time this file is written by hand-run command —
every later task only *reads* it (via `compare.py`), never overwrites it.

- [ ] **Step 7: Add the confidence-threshold sync-check test**

Add to `backend/tests/test_evaluation_accuracy.py` (new test, appended at the end of
the file — do not touch the existing `MIN_*` bars or `test_classification_accuracy_meets_minimum_bar`
in this task):

```python
def test_confidence_threshold_constant_matches_settings() -> None:
    """evaluation/run_eval.py.CONFIDENCE_THRESHOLD is a deliberate duplicate of
    settings.classification_confidence_threshold (see run_eval.py's comment for why it
    isn't imported directly). This test is what keeps the two from silently drifting
    apart if one is ever changed without the other."""
    from app.core.config import settings
    from evaluation.run_eval import CONFIDENCE_THRESHOLD

    assert CONFIDENCE_THRESHOLD == settings.classification_confidence_threshold
```

- [ ] **Step 8: Run the full backend test suite**

Run: `cd backend && uv run pytest`
Expected: all tests PASS (the existing `test_evaluation_accuracy.py` bars still apply
to the now-88-example dataset and are not touched yet — if any of the original `MIN_*`
bars unexpectedly fail here, stop and re-check Tasks 1-3's dataset edits before
proceeding; the 18 `clean_template` examples should behave identically to before, but
the bars are computed over the *whole* file, not just that subset, so this is a real
possible failure and worth a moment's sanity check, not an automatic pass-through).

- [ ] **Step 9: Commit**

```bash
cd backend && git add evaluation/run_eval.py evaluation/compare.py evaluation/baseline_metrics.json \
  tests/test_evaluation_run_eval.py tests/test_evaluation_accuracy.py
git commit -m "feat(evaluation): full metrics harness, compare.py, record Phase 6 baseline"
```

---

## Task 5: Fix `gmail/google_api.py` — strip `<style>`/`<script>` block contents

**Files:**
- Modify: `backend/app/gmail/google_api.py`
- Test: `backend/tests/test_gmail_google_api.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_clean_text` (private, unchanged signature) no longer leaks CSS/JS block
  text into the cleaned body it returns from `get_message_body`.

This is the one change in this plan outside `app/classifier/`. It's a mechanical bug in
existing Phase 3/5 code (`_HTML_TAG_RE = re.compile(r"<[^>]+>")` strips tags but not
`<style>`/`<script>` *contents*), verified by reading the code, not by the evaluation
dataset — this fix isn't exercised by `evaluation/dataset.jsonl` at all (that dataset
feeds the classifier directly, bypassing this Gmail-layer cleaning entirely), so it
gets its own direct unit test here instead.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_gmail_google_api.py`, right after the existing
`test_get_message_body_falls_back_to_html_and_strips_tags` (uses the same `_b64`/
`_FakeResponse` helpers already in this file):

```python
def test_get_message_body_strips_style_and_script_block_contents(monkeypatch) -> None:
    raw = (
        "<html><head>"
        "<style>.unsubscribe-link { color: blue; font-weight: bold; }</style>"
        "<script>function trackClick() { return true; }</script>"
        "</head><body><p>Thanks for applying to Acme.</p></body></html>"
    )
    payload = {"payload": {"mimeType": "text/html", "body": {"data": _b64(raw)}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    result = google_api.get_message_body("token", "m1")
    assert result == "Thanks for applying to Acme."
    assert "unsubscribe-link" not in result
    assert "trackClick" not in result
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py::test_get_message_body_strips_style_and_script_block_contents -v`
Expected: FAIL — the assertion `result == "Thanks for applying to Acme."` fails because
`result` also contains the CSS/JS text (e.g. `".unsubscribe-link { color: blue;..."`).

- [ ] **Step 3: Fix `_clean_text`**

In `backend/app/gmail/google_api.py`, add a new compiled pattern next to the existing
`_HTML_TAG_RE`/`_QUOTE_LINE_RE`/`_ON_WROTE_RE`/`_WHITESPACE_RE` (around line 15-18):

```python
_HTML_STYLE_SCRIPT_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
```

Then update `_clean_text` (currently lines 148-155) to strip those blocks — contents
included — before the existing tag-only stripping runs:

```python
def _clean_text(raw: str, *, is_html: bool) -> str:
    text = _ON_WROTE_RE.sub("", raw)
    text = _QUOTE_LINE_RE.sub("", text)
    if is_html:
        text = _HTML_STYLE_SCRIPT_RE.sub(" ", text)
        text = _HTML_TAG_RE.sub(" ", text)
        text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:BODY_MAX_CHARS]
```

- [ ] **Step 4: Run the test again to verify it passes**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py::test_get_message_body_strips_style_and_script_block_contents -v`
Expected: PASS.

- [ ] **Step 5: Run the full gmail test file to confirm no regression**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py -v`
Expected: all PASS, including the pre-existing
`test_get_message_body_falls_back_to_html_and_strips_tags` (plain tag stripping is
unaffected by this change).

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/gmail/google_api.py tests/test_gmail_google_api.py
git commit -m "fix(gmail): strip <style>/<script> block contents, not just tags, from HTML message bodies"
```

---

## Task 6: Add `classifier/preprocess.py` (greeting/signature stripping, punctuation normalization)

**Files:**
- Create: `backend/app/classifier/preprocess.py`
- Modify: `backend/app/classifier/extractor.py`
- Create: `backend/tests/test_classifier_preprocess.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `preprocess_body(body: str) -> str` — called from
  `RuleBasedExtractor.classify_and_extract` as the very first thing done to `body`,
  before `normalize_text`/`combine_subject_body` see it. Every downstream consumer of
  `body` in `extractor.py` automatically gets the preprocessed version since it's the
  same local variable, reassigned once at the top of the method.

Targets the `greeting_adjacent` and `signature_footer` dataset categories (Task 2), and
the two curly-apostrophe `messy_phrasing` examples (Task 3) that verifiably break the
existing `received your application`/`won't be moving forward`-style patterns today.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_classifier_preprocess.py`:

```python
from app.classifier.preprocess import preprocess_body


def test_strips_a_standalone_greeting_line() -> None:
    body = "Thank you for applying to Acme Corp\n\nHi Jordan Lee,\n\nWe have received your application."
    result = preprocess_body(body)
    assert "Hi Jordan Lee" not in result
    assert "Thank you for applying to Acme Corp" in result
    assert "We have received your application." in result


def test_truncates_at_a_dashes_signature_block() -> None:
    body = "Thank you for applying to Acme Corp.\n\n--\nJamie Fox\nTalent Acquisition"
    result = preprocess_body(body)
    assert "Jamie Fox" not in result
    assert "Thank you for applying to Acme Corp." in result


def test_truncates_at_a_regards_signoff() -> None:
    body = "We would like to invite you to interview.\n\nBest regards,\nJordan Blake\nHead of Talent"
    result = preprocess_body(body)
    assert "Jordan Blake" not in result
    assert "We would like to invite you to interview." in result


def test_normalizes_smart_quotes_and_dashes() -> None:
    body = "We’ve received your application — thanks!"
    assert preprocess_body(body) == "We've received your application - thanks!"


def test_leaves_ordinary_content_untouched() -> None:
    body = "We have received your application for the Software Engineer position and will be in touch."
    assert preprocess_body(body) == body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_preprocess.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.classifier.preprocess'`.

- [ ] **Step 3: Write `backend/app/classifier/preprocess.py`**

```python
import re

# A line that is ONLY a greeting (optionally with a short name/title after it) — not a
# sentence that merely starts with one of these words. Requires the line to end (after
# optional trailing [,:] and whitespace) within 60 chars of the greeting word, so a real
# sentence like "Hi-tech companies are hiring fast." (ends in a period, more than a
# trailing comma/colon) is correctly left alone.
_GREETING_LINE_RE = re.compile(
    r"^[ \t]*(?:hi|hello|hey|dear|greetings)\b[^\n]{0,60}[,:]?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# A line that is just a sign-off word/phrase (optionally with trailing punctuation) —
# everything from that line to the end of the body is a signature/footer block and is
# dropped. "--" alone is the conventional plain-text signature delimiter.
_SIGNATURE_START_RE = re.compile(
    r"^[ \t]*(?:--[ \t]*$|(?:best regards|warm regards|kind regards|many thanks|"
    r"congratulations(?: again)?|best|regards|sincerely|thanks|thank you)[,.]?[ \t]*$)",
    re.IGNORECASE | re.MULTILINE,
)

# Smart quotes/dashes that real email clients commonly introduce, which the literal-
# ASCII patterns in patterns.py (e.g. "received your application", built with a plain
# apostrophe) otherwise silently fail to match. Verified: "we've received your
# application" matches that pattern; "we’ve received your application" (curly
# apostrophe) does not.
_PUNCTUATION_NORMALIZE = {
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "—": "-", "–": "-",
}
_PUNCTUATION_NORMALIZE_RE = re.compile("|".join(re.escape(c) for c in _PUNCTUATION_NORMALIZE))


def _strip_greeting_lines(body: str) -> str:
    return _GREETING_LINE_RE.sub("", body)


def _truncate_at_signature(body: str) -> str:
    match = _SIGNATURE_START_RE.search(body)
    return body[: match.start()] if match else body


def _normalize_punctuation(body: str) -> str:
    return _PUNCTUATION_NORMALIZE_RE.sub(lambda m: _PUNCTUATION_NORMALIZE[m.group(0)], body)


def preprocess_body(body: str) -> str:
    """Applied to the message body before any scoring or extraction. Order matters:
    greeting lines are stripped first (a greeting can itself contain a word the
    signature truncator would misfire on, e.g. "Hi Best,"), then the signature/footer
    is truncated, then punctuation is normalized last so it doesn't interfere with
    either line-anchored regex above."""
    body = _strip_greeting_lines(body)
    body = _truncate_at_signature(body)
    body = _normalize_punctuation(body)
    return body
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_classifier_preprocess.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Wire `preprocess_body` into `RuleBasedExtractor`**

In `backend/app/classifier/extractor.py`, add the import next to the existing
`app.classifier.*` imports:

```python
from app.classifier.preprocess import preprocess_body
```

Then in `RuleBasedExtractor.classify_and_extract`, add one line at the very top of the
method body, before the existing `text = normalize_text(subject, body)`:

```python
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction:
        body = preprocess_body(body)
        text = normalize_text(subject, body)
        sender_domain = extract_sender_domain(sender)
        ...  # everything else unchanged — `raw_text = combine_subject_body(subject, body)`
             # further down already picks up the preprocessed `body` for free
```

- [ ] **Step 6: Run the existing classifier test suite to confirm no regression**

Run: `cd backend && uv run pytest tests/test_classifier_extractor.py tests/test_classifier_fields.py tests/test_classifier_patterns.py tests/test_classifier_text.py -v`
Expected: all PASS — every existing example is a `clean_template`-style plain
paragraph with no greeting/signature/curly-punctuation, so `preprocess_body` is a
no-op on all of them (this is exactly what
`test_leaves_ordinary_content_untouched` from Step 1 already verifies in isolation).

- [ ] **Step 7: Re-run the evaluation comparison**

Run: `cd backend && uv run python -m evaluation.compare`
Expected: `is_job_related` recall, `company_fuzzy_accuracy`, and `position_fuzzy_accuracy`
improve (no `REGRESSION` marker on any tracked metric) — driven by the
`greeting_adjacent`, `signature_footer`, and the two curly-punctuation `messy_phrasing`
examples now being classified/extracted more accurately. Full auto-apply/precision-at-threshold
gains are expected mainly from Task 10 (confidence recalibration), not this task — a
flat or small movement there is fine as long as nothing regresses.

- [ ] **Step 8: Commit**

```bash
cd backend && git add app/classifier/preprocess.py app/classifier/extractor.py tests/test_classifier_preprocess.py
git commit -m "feat(classifier): add preprocessing stage (greeting/signature stripping, punctuation normalization)"
```

---

## Task 7: Company extraction improvements

**Files:**
- Modify: `backend/app/classifier/fields.py`
- Modify: `backend/app/classifier/text.py`
- Modify: `backend/app/classifier/patterns.py`
- Modify: `backend/tests/test_classifier_fields.py`
- Modify: `backend/tests/test_classifier_text.py`

**Interfaces:**
- Consumes: `app.classifier.patterns.ATS_DOMAINS` (extended in this task).
- Produces: `find_company`'s public signature is unchanged
  (`find_company(text: str, sender: str) -> tuple[str | None, str]`); its behavior
  changes as described below. `text.extract_sender_domain`'s signature is unchanged.

Four independent, dataset-motivated fixes:

1. **Trailing-greeting trim** — targets `greeting_adjacent` (Task 2): even with Task
   6's greeting-line stripping, a greeting that survives on the same line as a
   preceding sentence (paragraph-join artifacts without a clean line break) still
   bleeds into the capitalized-run company capture. Fix at the source: trim the
   captured span at the first greeting word, rather than trying to prevent the capture
   from ever reaching it.
2. **Template keyword broadening** — targets `recruiter_outreach` (Task 3): today's
   template only recognizes `position`/`role` before `at Company`; real outreach says
   "opening"/"opportunity", and uses "about the X ... at Y" as often as "for the X ...
   at Y".
3. **New "X is pleased to offer" template** — targets the `Brightview Energy` (Task 2
   `html_noise` #10) and `Meridian Labs` (Task 2 `greeting_adjacent` #8) examples: when
   the company is the sentence's subject rather than reached via "at Y", the two
   existing templates never fire at all.
4. **Subdomain prefixes + assessment-platform blocklist** — targets `sender_variation`
   (Task 3): `careers.`/`jobs.`/`talent.`/`recruiting.` subdomains today leak into the
   domain-derived company guess as literal words ("Careers"), and `testgorilla.com`/
   `codility.com` (real assessment platforms, not currently in `ATS_DOMAINS`) get
   wrongly treated as the hiring company's domain.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_classifier_fields.py`:

```python
def test_find_company_trims_a_trailing_greeting_after_applying_to_template() -> None:
    text = "Thank you for applying to Pinnacle Robotics Hi Jordan Lee, we have received your application."
    company, tier = find_company(text, "careers@pinnaclerobotics.com")
    assert company == "Pinnacle Robotics"
    assert tier == "template"


def test_find_company_trims_a_trailing_greeting_after_position_at_company_template() -> None:
    text = "We would like to invite you to interview for the Product Designer position at Fernwood Design Hi Morgan, please pick a time."
    company, tier = find_company(text, "careers@fernwooddesign.com")
    assert company == "Fernwood Design"


def test_find_company_matches_opening_and_opportunity_keywords() -> None:
    text = "I think you'd be a great fit for the Senior Mechanical Engineer opening at Pinnacle Robotics."
    company, tier = find_company(text, "recruiter@pinnaclerobotics.com")
    assert company == "Pinnacle Robotics"
    assert tier == "template"


def test_find_company_matches_about_the_leading_word() -> None:
    text = "I'm reaching out about the Data Engineer opportunity at Cobalt Data."
    company, tier = find_company(text, "jane.kim@cobaltdata.io")
    assert company == "Cobalt Data"


def test_find_company_matches_is_pleased_to_offer_template() -> None:
    text = "Brightview Energy is pleased to extend an offer for the Electrical Engineer position."
    company, tier = find_company(text, "hr@brightviewenergy.com")
    assert company == "Brightview Energy"
    assert tier == "template"


def test_find_company_dedupes_when_subject_and_body_both_mention_the_company_adjacently() -> None:
    # combine_subject_body joins "Your offer from Brightview Energy" (subject) and
    # "Brightview Energy is pleased to..." (body) with a single space, producing
    # "...Brightview Energy Brightview Energy is pleased..." — without deduping, the
    # capitalized-run capture swallows both mentions as one company name.
    text = "Your offer from Brightview Energy Brightview Energy is pleased to extend an offer for the Electrical Engineer position."
    company, tier = find_company(text, "hr@brightviewenergy.com")
    assert company == "Brightview Energy"
    assert tier == "template"


def test_find_company_strips_careers_and_talent_subdomain_prefixes() -> None:
    company, tier = find_company("no template match here", "talent@careers.pinnaclerobotics.com")
    assert company == "Pinnaclerobotics"
    assert tier == "domain"


def test_find_company_does_not_treat_non_ats_assessment_platform_as_the_company() -> None:
    company, tier = find_company("no template match here", "noreply@testgorilla.com")
    assert company is None
    assert tier == "none"
```

Add to `backend/tests/test_classifier_text.py`:

```python
def test_extract_sender_domain_strips_careers_jobs_talent_recruiting_subdomains() -> None:
    assert extract_sender_domain("talent@careers.pinnaclerobotics.com") == "pinnaclerobotics.com"
    assert extract_sender_domain("jobs@jobs.cobaltdata.io") == "cobaltdata.io"
    assert extract_sender_domain("talent@talent.fernwooddesign.com") == "fernwooddesign.com"
    assert extract_sender_domain("recruiting@recruiting.trellishealth.com") == "trellishealth.com"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py tests/test_classifier_text.py -v -k "greeting or opening or opportunity or about_the or pleased_to_offer or subdomain or assessment_platform"`
Expected: FAIL (new tests reference behavior that doesn't exist yet; `test_find_company_does_not_treat_non_ats_assessment_platform_as_the_company`
currently gets `company == "Testgorilla"`, not `None`).

- [ ] **Step 3: Extend `_SUBDOMAIN_PREFIXES` in `text.py`**

In `backend/app/classifier/text.py`, change:

```python
_SUBDOMAIN_PREFIXES = ("mail.", "notifications.", "e.", "no-reply.", "noreply.")
```

to:

```python
_SUBDOMAIN_PREFIXES = (
    "mail.", "notifications.", "e.", "no-reply.", "noreply.",
    "careers.", "jobs.", "talent.", "recruiting.",
)
```

- [ ] **Step 4: Extend `ATS_DOMAINS` in `patterns.py`**

In `backend/app/classifier/patterns.py`, add to the `ATS_DOMAINS` frozenset (in the
"Assessment platforms" comment block, alongside the existing `hackerrank.com`/
`codesignal.com`):

```python
        "hackerrank.com",
        "codesignal.com",
        "testgorilla.com",
        "codility.com",
```

- [ ] **Step 5: Update `fields.py`'s templates and add the trailing-greeting trim**

Replace the template regex block (currently the `_POSITION_AT_COMPANY_RE`/
`_APPLICATION_TO_COMPANY_RE`/`_POSITION_ROLE_RE` definitions) with:

```python
_POSITION_AT_COMPANY_RE = re.compile(
    rf"(?:for|to|in|you|about) the (?P<position>{_POSITION_TOKEN}) "
    rf"(?:position|role|opening|opportunity) at (?P<company>{_COMPANY_TOKEN}){_COMPANY_BOUNDARY}",
    re.IGNORECASE,
)
_APPLICATION_TO_COMPANY_RE = re.compile(
    rf"appl(?:ying|ication) to (?P<company>{_COMPANY_TOKEN}){_COMPANY_BOUNDARY}",
    re.IGNORECASE,
)
_POSITION_ROLE_RE = re.compile(
    rf"for the (?P<position>{_POSITION_TOKEN}) (?:position|role)\b",
    re.IGNORECASE,
)
# Handles a company stated as the sentence's subject rather than reached via "at Y",
# e.g. "Brightview Energy is pleased to extend an offer for the Electrical Engineer
# position." — neither template above fires here since there's no "at <company>".
_COMPANY_PLEASED_TO_OFFER_RE = re.compile(
    rf"(?P<company>{_COMPANY_TOKEN}) (?:is|are) pleased to (?:offer|extend an offer)",
    re.IGNORECASE,
)

# Words that mark the start of a greeting that immediately follows a company mention
# with no intervening punctuation (a common HTML-paragraph-to-text-conversion
# artifact — see the Phase 6 spec's "Anduril Hi Gia Huy" root-cause analysis). Task 6's
# preprocess.py already strips *standalone* greeting lines before this code ever runs;
# this is the second, narrower line of defense for a greeting that survives on the same
# line as real content.
_GREETING_STOPWORDS = {"hi", "hello", "hey", "dear", "greetings"}


def _trim_trailing_greeting(span: str) -> str:
    words = span.split()
    for i, word in enumerate(words):
        if word.lower().strip(",.!") in _GREETING_STOPWORDS:
            return " ".join(words[:i]).strip()
    return span


# When a subject line ends with the company name and the body's next sentence starts
# with it again (e.g. subject "Your offer from Brightview Energy" + body "Brightview
# Energy is pleased to..."), combine_subject_body's single-space join puts the two
# mentions directly adjacent with nothing but a space between them — _COMPANY_TOKEN's
# capitalized-word-run capture (used by every template above) has no way to tell that's
# two mentions of one company rather than one long name, and captures both:
# "Brightview Energy Brightview Energy". This collapses an exact repeated half back
# down to one — safe because a genuine company name being a literal word-for-word
# self-repeat ("Design Design") essentially never happens in practice.
def _dedupe_repeated_span(span: str) -> str:
    words = span.split()
    n = len(words)
    if n > 0 and n % 2 == 0:
        half = n // 2
        if [w.lower() for w in words[:half]] == [w.lower() for w in words[half:]]:
            return " ".join(words[:half])
    return span
```

Then update `find_company` to try the new template, and trim + dedupe every template result:

```python
def find_company(text: str, sender: str) -> tuple[str | None, str]:
    match = _POSITION_AT_COMPANY_RE.search(text)
    if match:
        return _dedupe_repeated_span(_trim_trailing_greeting(match.group("company").strip(" .,"))), "template"

    match = _APPLICATION_TO_COMPANY_RE.search(text)
    if match:
        return _dedupe_repeated_span(_trim_trailing_greeting(match.group("company").strip(" .,"))), "template"

    match = _COMPANY_PLEASED_TO_OFFER_RE.search(text)
    if match:
        return _dedupe_repeated_span(_trim_trailing_greeting(match.group("company").strip(" .,"))), "template"

    domain_company = _domain_derived_company(sender)
    if domain_company:
        return domain_company, "domain"

    display_name_company = _display_name_derived_company(sender)
    if display_name_company:
        return display_name_company, "display_name"

    return None, "none"
```

`find_position` is unchanged in this task (Task 8 handles position).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py tests/test_classifier_text.py -v`
Expected: all PASS, including every pre-existing test in both files (the trim function
is a no-op unless a greeting stopword is actually present, and the broadened
alternations are strict supersets of the old ones).

- [ ] **Step 7: Re-run the evaluation comparison**

Run: `cd backend && uv run python -m evaluation.compare`
Expected: `company_exact_accuracy`/`company_fuzzy_accuracy` improve, particularly in
the `greeting_adjacent`, `recruiter_outreach`, and `sender_variation` category
breakdowns.

One understood, accepted exception if it shows up: the `clean_template` category's
`company_exact_accuracy` may show a single-example dip. The original (protected,
byte-for-byte) dataset's "Your offer from Eta Ltd" example has `expected.company ==
"Eta"` — a known, already-documented Phase 4b compromise (today's code can't match this
phrasing via any template, so it fell back to the domain-derived guess). Task 7's new
`_COMPANY_PLEASED_TO_OFFER_RE` template *does* match this example's body ("Eta Ltd is
pleased to extend an offer..."), correctly extracting the fuller `"Eta Ltd"` — which is
actually the *more* correct answer, just not equal to the frozen `"Eta"` ground truth.
Since the original 18 examples must stay byte-for-byte unchanged (Global Constraints),
do not edit this example's expected value — accept the metric showing `"Eta Ltd" !=
"Eta"` as a exact-match miss here. This is a one-example, direction-correct wobble, not
a real regression; do not attempt to "fix" it by weakening the new template.

- [ ] **Step 8: Commit**

```bash
cd backend && git add app/classifier/fields.py app/classifier/text.py app/classifier/patterns.py \
  tests/test_classifier_fields.py tests/test_classifier_text.py
git commit -m "feat(classifier): trim greeting bleed from company capture, broaden templates, fix subdomain/assessment-platform gaps"
```

---

## Task 8: Position extraction — "as (our|your) new X" template

**Files:**
- Modify: `backend/app/classifier/fields.py`
- Modify: `backend/tests/test_classifier_fields.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `find_position`'s signature is unchanged.

Targets the `Outpost Aerospace` example (Task 3 `messy_phrasing` #9): "is thrilled to
bring you on board as our new Systems Engineer" has no "position"/"role"/"opening"/
"opportunity" keyword at all, so neither existing position template fires — the only
signal is the "as our/your new X" construction itself. Scoping note: this is a single
added template, not a new non-template fallback tier (unlike company, position has no
good non-regex signal — no domain or display name reliably encodes a job title — so
`EXTRACTION_PENALTY`'s existing `"template"`/`"none"` tiers for position are unchanged;
no new dict entries needed).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_classifier_fields.py`:

```python
def test_find_position_matches_as_our_new_template() -> None:
    text = "Outpost Aerospace is thrilled to bring you on board as our new Systems Engineer."
    position, tier = find_position(text, "hr@outpostaerospace.com")
    assert position == "Systems Engineer"
    assert tier == "template"


def test_find_position_matches_as_your_new_template() -> None:
    text = "We're excited to welcome you as your new Machine Learning Engineer."
    position, tier = find_position(text, "hr@meridianlabs.ai")
    assert position == "Machine Learning Engineer"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py -v -k as_our_new_template or as_your_new_template`
Expected: FAIL — both currently return `(None, "none")`.

- [ ] **Step 3: Add the template and wire it into `find_position`**

In `backend/app/classifier/fields.py`, add near the other template regexes:

```python
# "X is thrilled to bring you on board as our new Y" / "as your new Y" — no
# position/role/opening/opportunity keyword present, so neither existing template
# fires; "as (our|your) new" is the only extractable signal.
_POSITION_AS_NEW_RE = re.compile(
    rf"as (?:our|your) new (?P<position>{_POSITION_TOKEN})\b",
    re.IGNORECASE,
)
```

Update `find_position`:

```python
def find_position(text: str, sender: str) -> tuple[str | None, str]:
    match = _POSITION_AT_COMPANY_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    match = _POSITION_ROLE_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    match = _POSITION_AS_NEW_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    return None, "none"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py -v`
Expected: all PASS, including every pre-existing test in the file.

- [ ] **Step 5: Re-run the evaluation comparison**

Run: `cd backend && uv run python -m evaluation.compare`
Expected: `position_exact_accuracy`/`position_fuzzy_accuracy` improve slightly (one
dataset example moves from a miss to a hit); no `REGRESSION` marker anywhere.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/classifier/fields.py tests/test_classifier_fields.py
git commit -m "feat(classifier): add 'as our/your new X' position template"
```

---

## Task 9: Status pattern coverage

**Files:**
- Modify: `backend/app/classifier/patterns.py`
- Modify: `backend/tests/test_classifier_patterns.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `STATUS_PATTERNS`/`GENERIC_JOB_PATTERNS` dict/list shapes are unchanged
  (`classify()` in `extractor.py` is not touched).

Every addition below is motivated by one specific `messy_phrasing` or
`recruiter_outreach` example from Task 2/3 — not speculative. Each is checked against
the exact dataset text it targets (verified during planning; Task 9's own eval re-run
in Step 5 is the real confirmation).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_classifier_patterns.py`:

```python
def test_status_patterns_match_application_is_in_our_system() -> None:
    text = "your application is now in our system and our team will be reviewing it shortly"
    scores, job_signal, _ = classify(text, "")
    assert scores["applied"] > 0


def test_status_patterns_match_got_your_application() -> None:
    text = "we've got your application for the field technician role"
    scores, job_signal, _ = classify(text, "")
    assert scores["applied"] > 0


def test_status_patterns_match_skills_assessment() -> None:
    text = "you've been asked to complete a skills assessment for the financial analyst role"
    scores, job_signal, _ = classify(text, "")
    assert scores["oa"] > 0


def test_status_patterns_match_set_up_a_time_to_chat() -> None:
    text = "we'd love to set up a time to chat about the game designer opening"
    scores, job_signal, _ = classify(text, "")
    assert scores["interview"] > 0


def test_status_patterns_match_available_for_a_chat_about() -> None:
    text = "would you be available for a chat about the security analyst position"
    scores, job_signal, _ = classify(text, "")
    assert scores["interview"] > 0


def test_status_patterns_match_wont_be_moving_forward() -> None:
    text = "we won't be moving forward with your candidacy for the data engineer role"
    scores, job_signal, _ = classify(text, "")
    assert scores["rejected"] > 0


def test_status_patterns_match_different_direction() -> None:
    text = "we've chosen to move in a different direction for the level designer role"
    scores, job_signal, _ = classify(text, "")
    assert scores["rejected"] > 0


def test_status_patterns_match_extend_you_an_offer() -> None:
    text = "fieldstone ventures would like to extend you an offer for the investment associate role"
    scores, job_signal, _ = classify(text, "")
    assert scores["offer"] > 0


def test_status_patterns_match_thrilled_to_bring_you_on_board() -> None:
    text = "outpost aerospace is thrilled to bring you on board as our new systems engineer"
    scores, job_signal, _ = classify(text, "")
    assert scores["offer"] > 0


def test_generic_job_patterns_match_opening_and_opportunity() -> None:
    text = "i think you'd be a great fit for the senior mechanical engineer opening at pinnacle robotics"
    _, job_signal, _ = classify(text, "")
    assert job_signal >= JOB_RELATED_THRESHOLD
```

(The last test imports `JOB_RELATED_THRESHOLD` alongside whatever this test file
already imports from `app.classifier.patterns` — add it to the existing import line if
it isn't already there.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_patterns.py -v -k "applied_is_in_our_system or got_your_application or skills_assessment or set_up_a_time or available_for_a_chat or wont_be_moving_forward or different_direction or extend_you_an_offer or thrilled_to_bring or opening_and_opportunity"`
Expected: FAIL — none of these phrases score anything against today's patterns (all
assert a score of `0`, which fails `> 0` / `>= JOB_RELATED_THRESHOLD`).

- [ ] **Step 3: Add the new patterns**

In `backend/app/classifier/patterns.py`, update `STATUS_PATTERNS`:

```python
STATUS_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "applied": [
        (r"thank you for applying", 3),
        (r"application (?:has been )?received", 3),
        (r"(?:we|i)(?:'ve| have)? received your application", 3),
        (r"successfully applied", 2),
        (r"application is (?:now )?(?:in our system|being reviewed)", 3),
        (r"(?:we|i)(?:'ve| have)? got your application", 2),
    ],
    "oa": [
        (r"online assessment", 3),
        (r"coding (?:challenge|test)", 3),
        (r"hackerrank|codesignal", 3),
        (r"take.?home (?:assignment|test|challenge)", 2),
        (r"technical assessment", 2),
        (r"skills assessment", 3),
    ],
    "interview": [
        (r"invite you to interview", 3),
        (r"schedule (?:a|your) (?:call|interview)", 3),
        (r"phone screen", 3),
        (r"interview (?:invitation|process)", 2),
        (r"\binterview\b", 1),
        (r"set up a time to (?:chat|talk)", 2),
        (r"available for a (?:chat|call) about", 2),
    ],
    "rejected": [
        (r"regret to inform", 3),
        (r"will not be moving forward", 3),
        (r"decided (?:not )?to (?:proceed|move forward) with other candidates", 3),
        (r"other candidates", 2),
        (r"unfortunately", 2),
        (r"won'?t be moving forward", 3),
        (r"move(?:d|ing)? in a different direction", 3),
    ],
    "offer": [
        (r"pleased to offer", 3),
        (r"offer of employment", 3),
        (r"extend(?:ing)? (?:you )?an offer", 3),
        (r"job offer", 2),
        (r"(?:thrilled|excited) to (?:offer|welcome you|bring you on board)", 3),
    ],
}
```

(Only the `extend(?:ing)? an offer` entry is *changed* in place, to
`extend(?:ing)? (?:you )?an offer` — every other line above is either unchanged or new,
shown in full so the file's exact final state is unambiguous.)

**Weight correction found during Task 9 pre-flight verification** (before Task 9 was
dispatched, simulating the full classify()/threshold pipeline — not just isolated regex
matches — against the real 88-example dataset): `skills assessment` and `(?:thrilled|
excited) to (?:offer|welcome you|bring you on board)` are weighted `3`, not `2` as an
earlier draft of this plan had them. Both are each the *only* signal in their target
example (Lumen Financial's "skills assessment for the Financial Analyst **role**" has
no `\bposition\b`/`your application`/`\bcandidates?\b` GENERIC hit since it says "role,"
not "position"; Outpost Aerospace's "thrilled to bring you on board" has no other
GENERIC hit either) — at weight 2 alone, `job_signal` only reaches 2, one short of
`JOB_RELATED_THRESHOLD` (3), so both examples would still wrongly classify as
`is_job_related=false` even with the pattern "matching." Weight 3 matches the existing
convention every other unambiguous, single-signal-sufficient phrase in this table already
uses (`thank you for applying`, `invite you to interview`, `regret to inform`, `pleased
to offer`, etc. are all `3`) — `2` is reserved for genuinely weaker/corroborating
signals that were always expected to need company. Verified by re-running the full
simulation at weight 3: both examples now correctly classify, with the correct status.

Update `GENERIC_JOB_PATTERNS`:

```python
GENERIC_JOB_PATTERNS: list[tuple[str, int]] = [
    (r"your application", 1),
    (r"\bposition\b", 1),
    (r"\bcandidates?\b", 1),
    (r"recruiting team", 1),
    (r"talent (?:acquisition|team)", 1),
    (r"\bopening\b", 3),
    (r"\bopportunity\b", 3),
]
```

`\bopening\b`/`\bopportunity\b` are weighted higher than the other generic patterns
(3, not 1) because — unlike "your application"/"\bposition\b", which are weak
corroborating signals — a recruiter outreach email's *only* job-relatedness signal is
often exactly one of these two words (see `recruiter_outreach`, Task 3): without a
strong-enough weight, `job_signal` never reaches `JOB_RELATED_THRESHOLD` (3) and those
10 examples would wrongly classify as `is_job_related=false`. Checked against the
`ambiguous` category (Task 3): none of those 10 negative examples contain "opening" or
"opportunity", so this doesn't introduce a false positive there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_classifier_patterns.py -v`
Expected: all PASS, including every pre-existing test in the file.

- [ ] **Step 5: Run the full classifier suite and the evaluation comparison**

Run: `cd backend && uv run pytest tests/test_classifier_extractor.py tests/test_classifier_fields.py tests/test_classifier_patterns.py tests/test_classifier_text.py tests/test_classifier_preprocess.py -v`
Expected: all PASS.

Run: `cd backend && uv run python -m evaluation.compare`
Expected: per-status precision/recall/F1 improve, especially `applied`/`interview`/
`rejected`/`offer`; `recruiter_outreach`'s category `is_job_related` F1 goes from ~0 to
~1.0 (this is the category whose examples were entirely unclassifiable before this
task, per the `\bopening\b`/`\bopportunity\b` weighting above); `messy_phrasing`'s
category `is_job_related` F1 also improves (was partial — some of its 10 examples
needed this task's other new patterns too). No `REGRESSION` marker on any tracked
metric.

Two things NOT to chase here, both understood and out of this task's scope:

1. The `ambiguous` category's `is_job_related` precision/recall/F1 will read as `0.0`
   both before and after this task, regardless of classifier quality — it has zero
   expected-positive examples by design (every one of its 10 examples is
   `is_job_related: false`), so precision/recall/F1 for the *positive* class are always
   `0`-by-convention when there's nothing positive to measure against (a `_safe_div`
   `0/0` case). The real signal for this category is `classification_accuracy`, not
   `is_job_related` precision/recall/F1 — check that instead if you want to confirm the
   new `\bopening\b`/`\bopportunity\b` patterns didn't introduce a new false positive
   (neither phrase appears in any `ambiguous` example's body, so they shouldn't).
2. One `ambiguous` example (sender `community@devmeetup.com`, "Meetup: Hiring managers
   panel...") is *already* a false positive (`is_job_related=true` when it should be
   `false`) before this task ever runs — driven entirely by the pre-existing, unchanged
   `interview (?:invitation|process)` pattern matching "interview process" plus the
   pre-existing `\bcandidates?\b` GENERIC pattern matching "candidates," neither of
   which this task touches. This is a real, known gap in the *original* Phase 4b
   patterns, exposed by this dataset's deliberately-harder `ambiguous` examples working
   as intended — not something this task introduced or is scoped to fix (fixing it
   would mean reweighting or adding a negative pattern for an *existing*, unrelated
   phrase, which no specific new dataset example in this task motivates). It's noted
   here so it isn't mistaken for a regression this task caused; Task 11's final report
   records it as a known, accepted gap.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/classifier/patterns.py tests/test_classifier_patterns.py
git commit -m "feat(classifier): add status/generic pattern coverage for messy real-world phrasing"
```

---

## Task 10: Confidence recalibration

**Files:**
- Create: `backend/evaluation/inspect_confidence.py`
- Modify: `backend/app/classifier/patterns.py`
- Modify: `backend/tests/test_classifier_extractor.py`

**Interfaces:**
- Consumes: `app.classifier.extractor.classify`, `app.classifier.fields.find_company`/
  `find_position`, `app.classifier.text.*` (all unmodified — the diagnostic script only
  calls them, it doesn't change their behavior).
- Produces: new values for `JOB_SIGNAL_NORM`/`MARGIN_NORM` in `patterns.py`. No other
  constant changes (`DOMAIN_CONFIDENCE_BONUS`, `DOMAIN_RELATEDNESS_BONUS`,
  `EXTRACTION_PENALTY`, `JOB_RELATED_THRESHOLD` are untouched — the spec's root-cause
  analysis (§2.1) is specifically that `base`/`margin` cap too early for realistically-
  phrased mail, not that the other components are wrong).

This is a measured procedure, following the same "measure → adjust one constant →
re-measure" method the codebase's own Task 8 (Phase 4b) precedent used — see
`app/core/config.py`'s `classification_confidence_threshold` comment for that
precedent's own documented reasoning. `settings.classification_confidence_threshold`
itself (0.85) is never touched (Global Constraints) — this task only changes what feeds
into the confidence a message *earns*.

- [ ] **Step 1: Write the diagnostic script**

```python
# backend/evaluation/inspect_confidence.py
"""Diagnostic tool for Task 10 (confidence recalibration): prints the confidence-
formula's components for every job-related dataset example, so it's visible where
JOB_SIGNAL_NORM/MARGIN_NORM are leaving real signal on the table for realistically-
phrased (non-template) mail. Not a regression gate — evaluation/compare.py is that.
Duplicates extractor.py's confidence formula deliberately (a diagnostic script, not
production code) — if RuleBasedExtractor's formula structure ever changes (not just its
constants), this script's formula must be updated to match or its output is meaningless.

Run: cd backend && uv run python -m evaluation.inspect_confidence
"""

from app.classifier.extractor import classify
from app.classifier.fields import find_company, find_position
from app.classifier.patterns import (
    ATS_DOMAINS,
    DOMAIN_CONFIDENCE_BONUS,
    EXTRACTION_PENALTY,
    JOB_SIGNAL_NORM,
    MARGIN_NORM,
)
from app.classifier.text import combine_subject_body, extract_sender_domain, normalize_text
from evaluation.run_eval import load_dataset


def main() -> None:
    for example in load_dataset():
        expected = example["expected"]
        if not expected.get("is_job_related"):
            continue

        subject, sender, body = example["subject"], example["sender"], example["body"]
        text = normalize_text(subject, body)
        sender_domain = extract_sender_domain(sender)
        status_scores, job_signal, negative_signal = classify(text, sender_domain)
        net_signal = job_signal - negative_signal

        ranked = sorted(status_scores.items(), key=lambda kv: kv[1], reverse=True)
        top_score, runner_up_score = ranked[0][1], ranked[1][1]

        raw_text = combine_subject_body(subject, body)
        _, company_tier = find_company(raw_text, sender)
        _, position_tier = find_position(raw_text, sender)

        base = min(max(net_signal, 0) / JOB_SIGNAL_NORM, 1.0) * 0.6
        margin = min((top_score - runner_up_score) / MARGIN_NORM, 1.0) * 0.3
        domain_bonus = DOMAIN_CONFIDENCE_BONUS if sender_domain in ATS_DOMAINS else 0.0
        penalty = max(EXTRACTION_PENALTY[company_tier], EXTRACTION_PENALTY[position_tier])
        confidence = max(0.0, min(1.0, base + margin + domain_bonus - penalty))

        print(
            f"{example.get('category', '?'):<20} net_signal={net_signal:>3} "
            f"base={base:.3f} margin={margin:.3f} domain_bonus={domain_bonus:.2f} "
            f"penalty={penalty:.2f} -> confidence={confidence:.3f}"
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and observe today's distribution**

Run: `cd backend && uv run python -m evaluation.inspect_confidence`
Expected: for the non-`clean_template` categories especially, most `base` values sit
well below the 0.6 cap (a single 3-weight status pattern match with no other signal
gives `net_signal=3`, `base=3/6*0.6=0.3`) and most `margin` values sit below the 0.3
cap too — confirming the spec's §2.1 diagnosis directly, with this dataset's actual
numbers instead of the ~0.36-average finding from the real mailbox.

- [ ] **Step 3: Apply the first-pass recalibration**

In `backend/app/classifier/patterns.py`, change:

```python
JOB_RELATED_THRESHOLD = 3
JOB_SIGNAL_NORM = 6.0
MARGIN_NORM = 4.0
DOMAIN_CONFIDENCE_BONUS = 0.1
DOMAIN_RELATEDNESS_BONUS = 2
```

to:

```python
JOB_RELATED_THRESHOLD = 3
# Lowered from 6.0/4.0 (Phase 4b) in Phase 6 Task 10: those values were calibrated
# against evaluation/dataset.jsonl's original 18 template-phrased examples, where a
# genuinely job-related message routinely hits several weighted patterns at once. Real
# mail (per Phase 5's manual test — see CLAUDE.md) usually hits exactly one 3-weight
# status pattern and nothing else, giving net_signal=3 — under the old norm that's
# base=3/6*0.6=0.3, capped well below where a human would call the signal "clear."
# 4.5/3.0 let a single strong, unambiguous status match reach base=0.4 and a clean
# top-vs-runner-up margin still saturate at 0.3 — see evaluation/inspect_confidence.py's
# output and evaluation/compare.py's precision_at_threshold before/after for the
# evidence this was checked against, not guessed.
JOB_SIGNAL_NORM = 4.5
MARGIN_NORM = 3.0
DOMAIN_CONFIDENCE_BONUS = 0.1
DOMAIN_RELATEDNESS_BONUS = 2
```

- [ ] **Step 4: Update the two existing hardcoded confidence assertions**

Two of `test_classifier_extractor.py`'s existing tests hand-trace their expected
`confidence` value in a comment; both change under the new norms (verify with
`cd backend && uv run python -c "print(min(3/4.5,1)*0.6 + min(3/3.0,1)*0.3 - 0.35)"` →
`0.35`, and `uv run python -c "print(min(8/4.5,1)*0.6 + min(3/3.0,1)*0.3 + 0.1 - 0.35)"`
→ `0.65`). A third (`test_classify_and_extract_high_confidence_interview_with_both_fields`,
currently `0.9`) is unaffected — its underlying `base`/`margin` were already saturated
under the *old*, larger norms, so they're still saturated under the new, smaller ones;
leave that test exactly as-is.

In `test_classify_and_extract_applied_email_with_company_but_no_position` (around line
40-42), replace:

```python
    # base=0.3 (net_signal=3, capped at 3/6*0.6) + margin=0.225 (3/4*0.3) + domain=0.0
    # - penalty=0.35 (position tier "none") = 0.175
    assert result.confidence == pytest.approx(0.175)
```

with:

```python
    # base=0.4 (net_signal=3, min(3/4.5,1)*0.6) + margin=0.3 (min(3/3.0,1)*0.3, capped)
    # + domain=0.0 - penalty=0.35 (position tier "none") = 0.35
    assert result.confidence == pytest.approx(0.35)
```

In `test_classify_and_extract_ats_domain_blocked_from_company_but_boosts_confidence`
(around line 78-88), replace:

```python
    # base = min(max(8, 0) / 6, 1.0) * 0.6 = min(1.333, 1.0) * 0.6 = 0.6
    # margin = 0.225
    # domain_bonus = 0.1 (sender domain greenhouse.io is in ATS_DOMAINS)
    # penalty = max(EXTRACTION_PENALTY["none"], EXTRACTION_PENALTY["template"])
    #         = max(0.35, 0.0) = 0.35 (company tier "none"; position tier
    #           "template" via find_position's "for the ... position" match)
    # confidence = 0.6 + 0.225 + 0.1 - 0.35 = 0.575
    assert result.status == "applied"
    assert result.company is None  # greenhouse.io is blocklisted; no display name to fall back to
    assert result.position == "Backend Engineer"
    assert result.confidence == pytest.approx(0.575)
```

with:

```python
    # base = min(max(8, 0) / 4.5, 1.0) * 0.6 = min(1.778, 1.0) * 0.6 = 0.6 (still capped)
    # margin = min(3 / 3.0, 1.0) * 0.3 = 0.3 (now also capped, was 0.225 under the old
    #   MARGIN_NORM=4.0 — Task 10 lowered it to 3.0)
    # domain_bonus = 0.1 (sender domain greenhouse.io is in ATS_DOMAINS)
    # penalty = max(EXTRACTION_PENALTY["none"], EXTRACTION_PENALTY["template"])
    #         = max(0.35, 0.0) = 0.35 (company tier "none"; position tier
    #           "template" via find_position's "for the ... position" match)
    # confidence = 0.6 + 0.3 + 0.1 - 0.35 = 0.65
    assert result.status == "applied"
    assert result.company is None  # greenhouse.io is blocklisted; no display name to fall back to
    assert result.position == "Backend Engineer"
    assert result.confidence == pytest.approx(0.65)
```

- [ ] **Step 5: Run the classifier test suite**

Run: `cd backend && uv run pytest tests/test_classifier_extractor.py tests/test_classifier_fields.py tests/test_classifier_patterns.py tests/test_classifier_text.py tests/test_classifier_preprocess.py -v`
Expected: all PASS.

- [ ] **Step 6: Re-run the evaluation comparison and check the acceptance bounds**

Run: `cd backend && uv run python -m evaluation.compare`

Check two things explicitly, in this order:

1. **`precision_at_threshold` (overall) must not drop below its Task 9 checkpoint
   value.** This is the non-negotiable one — it's what guards real auto-applies. If it
   dropped, the recalibration was too aggressive: revert to `JOB_SIGNAL_NORM = 5.0`,
   `MARGIN_NORM = 3.5` (a more conservative second attempt) and re-run this step. If
   *that* still regresses `precision_at_threshold`, stop lowering the norms further —
   revert to the original `6.0`/`4.0` and document in Task 11's report that this
   dataset's real-phrasing examples don't yet clear 0.85 even post-recalibration
   (an honest negative result is a valid Phase 6 outcome, not a failure to hide).
2. **`auto_apply_rate` (overall) should visibly increase** from the Task 9 checkpoint
   value — this is the metric the whole task exists to move. Report the before/after
   numbers from `compare.py`'s output in the commit message (Step 7).

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/classifier/patterns.py tests/test_classifier_extractor.py evaluation/inspect_confidence.py
git commit -m "feat(classifier): recalibrate JOB_SIGNAL_NORM/MARGIN_NORM against the harder Phase 6 dataset

precision_at_threshold: <baseline value> -> <new value>
auto_apply_rate: <baseline value> -> <new value>
(fill in from evaluation/compare.py's Step 6 output before committing)"
```

---

## Task 11: Final comparison report, regression gate rebase, CLAUDE.md update

**Files:**
- Modify: `backend/tests/test_evaluation_accuracy.py` (rewrite — rebase bars on the new
  88-example dataset; refactor to use `evaluate()` instead of duplicating its scoring
  logic)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `evaluation.run_eval.evaluate`, `evaluation.run_eval.load_dataset`,
  `evaluation.run_eval.CONFIDENCE_THRESHOLD`, `app.classifier.extractor.RuleBasedExtractor`
  (all from Task 4, unmodified here).
- Produces: nothing new is consumed by later code — this is the plan's last task.

This task cannot be fully written with final numeric values in advance — the bars
depend on the actual measured output of every prior task's changes, which only exists
once Tasks 1-10 have actually run. That's not a placeholder: Step 1 gives the exact
command to run and the exact, mechanical rule for turning its output into bars (the
same "tolerate one additional miss, not zero" rule `test_evaluation_accuracy.py`
already documents and used from Phase 4b) — the only thing deferred to execution time is
plugging in real numbers, not deciding the method.

- [ ] **Step 1: Get the final numbers**

Run: `cd backend && uv run python -m evaluation.run_eval`

Read the printed `overall` numbers (classification, per-status, company, position,
precision_at_threshold, auto_apply_rate). For each of the six metrics below, the bar is
set so that the *current* score passes, one-more-miss-than-current still passes, and
two-more-misses fails — mechanically: if `C` out of `T` are currently correct, the bar
is any value in the open interval `((C-2)/T, (C-1)/T]`; pick a clean two-decimal number
in that range (this is exactly the method the existing `MIN_CLASSIFICATION_ACCURACY`
etc. comments already document — Task 11 continues it, not replaces it).

- [ ] **Step 2: Rewrite `backend/tests/test_evaluation_accuracy.py`**

```python
"""Runs the classifier against evaluation/dataset.jsonl as part of the normal test
suite. Local classification costs nothing, so this is a real, always-on regression
gate: a weight/pattern/threshold change in app/classifier/ that regresses accuracy
fails this test immediately.

Uses evaluate() from evaluation/run_eval.py (the same function evaluation/compare.py
and evaluation/inspect_confidence.py rely on) instead of re-implementing scoring here —
Phase 6 added enough metrics (precision_at_threshold, auto_apply_rate, fuzzy matching,
per-category breakdowns) that a second hand-rolled copy would drift.

Every bar below is set to genuinely tolerate one additional miss beyond the dataset's
current numbers (not just barely clear them) — see evaluate()'s docstring in
run_eval.py for the metric definitions, and docs/superpowers/plans/
2026-09-17-job-tracker-phase-6-classifier-quality.md Task 11 for the exact procedure
used to pick these values from a real `uv run python -m evaluation.run_eval` run
against the 88-example dataset (18 original clean_template examples + 70 added across
html_noise, greeting_adjacent, signature_footer, recruiter_outreach, ambiguous,
sender_variation, and messy_phrasing).
"""

from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import CONFIDENCE_THRESHOLD, evaluate, load_dataset

# <FILL IN from Step 1's `uv run python -m evaluation.run_eval` output — replace every
# <VALUE> below with a real two-decimal number and delete this comment line once done.>
MIN_CLASSIFICATION_ACCURACY = <VALUE>
MIN_STATUS_ACCURACY = <VALUE>
MIN_COMPANY_ACCURACY = <VALUE>          # exact-match, same as Phase 4b (not fuzzy)
MIN_POSITION_ACCURACY = <VALUE>         # exact-match, same as Phase 4b (not fuzzy)
MIN_PRECISION_AT_THRESHOLD = <VALUE>    # guards real auto-applies — the most important bar
MIN_AUTO_APPLY_RATE = <VALUE>           # a floor: confirms Task 10 didn't silently regress


def test_classification_accuracy_meets_minimum_bar() -> None:
    report = evaluate(RuleBasedExtractor(), load_dataset())["overall"]

    assert report["classification_accuracy"] >= MIN_CLASSIFICATION_ACCURACY, (
        f"classification accuracy {report['classification_accuracy']:.2f} fell below "
        f"the {MIN_CLASSIFICATION_ACCURACY} bar"
    )
    assert report["status_accuracy"] >= MIN_STATUS_ACCURACY, (
        f"status accuracy {report['status_accuracy']:.2f} fell below the {MIN_STATUS_ACCURACY} bar"
    )
    assert report["company_exact_accuracy"] >= MIN_COMPANY_ACCURACY, (
        f"company accuracy {report['company_exact_accuracy']:.2f} fell below the {MIN_COMPANY_ACCURACY} bar"
    )
    assert report["position_exact_accuracy"] >= MIN_POSITION_ACCURACY, (
        f"position accuracy {report['position_exact_accuracy']:.2f} fell below the {MIN_POSITION_ACCURACY} bar"
    )


def test_precision_at_threshold_and_auto_apply_rate_meet_minimum_bar() -> None:
    """The Phase 6 metrics that guard and measure the auto-apply trust-model gate
    directly (spec §6) — kept as a separate test from classification/extraction
    accuracy above so a failure message immediately says which kind of regression
    happened."""
    report = evaluate(RuleBasedExtractor(), load_dataset())["overall"]

    assert report["precision_at_threshold"] >= MIN_PRECISION_AT_THRESHOLD, (
        f"precision_at_threshold {report['precision_at_threshold']:.2f} fell below "
        f"the {MIN_PRECISION_AT_THRESHOLD} bar — this guards real auto-applies, do not "
        f"lower it without new evidence"
    )
    assert report["auto_apply_rate"] >= MIN_AUTO_APPLY_RATE, (
        f"auto_apply_rate {report['auto_apply_rate']:.2f} fell below the {MIN_AUTO_APPLY_RATE} bar"
    )


def test_confidence_threshold_constant_matches_settings() -> None:
    """evaluation/run_eval.py's CONFIDENCE_THRESHOLD is a deliberate duplicate of
    settings.classification_confidence_threshold (see run_eval.py's comment for why it
    isn't imported directly). This test is what keeps the two from silently drifting
    apart if one is ever changed without the other."""
    from app.core.config import settings

    assert CONFIDENCE_THRESHOLD == settings.classification_confidence_threshold
```

(This replaces the entire previous file content, including the Task 4 Step 7 version of
`test_confidence_threshold_constant_matches_settings` — folded in here verbatim, not
duplicated.)

- [ ] **Step 3: Fill in the real bar values and run the test**

Replace every `<VALUE>` above using Step 1's numbers and the interval rule. Run:

Run: `cd backend && uv run pytest tests/test_evaluation_accuracy.py -v`
Expected: both tests PASS.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && uv run pytest`
Expected: all tests PASS.

- [ ] **Step 5: Confirm the frontend is untouched**

Run: `cd frontend && npx tsc -b && npx oxlint`
Expected: both clean, identical to the Phase 5 baseline in CLAUDE.md — this plan makes
no frontend changes, so this step is a sanity check, not something expected to need a
fix.

- [ ] **Step 6: Update CLAUDE.md**

Six edits, all inside the existing file (no restructuring beyond what's needed):

1. In the opening paragraph, change:
   `Phases 1–5 are complete, merged to main, and manually verified end-to-end against a
   real Gmail account (2026-09-17)` to `Phases 1–6 are complete, merged to main` and
   update the "Backend: 233/233 tests passing" figure to the actual count from Step 4's
   `pytest` output.

2. In the "Status" phase list, add a new bullet after the Phase 5 one:
   ```
   - Phase 6: classifier/extraction quality — larger, category-labeled evaluation
     dataset (88 examples); preprocessing (greeting/signature stripping, punctuation
     normalization); extraction fixes (greeting-bleed trim, broadened templates,
     subdomain/assessment-platform gaps); status-pattern coverage; measured confidence
     recalibration. See "Phase 6 results" below.
   ```

3. In the `backend/app/classifier/` section of the Architecture tree, add
   `preprocess.py` to the file list:
   ```
   │   ├── preprocess.py    Phase 6 — greeting/signature stripping, punctuation
   │   │                       normalization, applied before scoring/extraction
   ```

4. Add a new section after "Manual testing findings (2026-09-17)" (before "Local dev
   environment"):

   ```markdown
   ## Phase 6 results (<FILL IN DATE>)

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
   | classification_accuracy | <FILL IN> | <FILL IN> |
   | status_accuracy | <FILL IN> | <FILL IN> |
   | company_exact_accuracy | <FILL IN> | <FILL IN> |
   | position_exact_accuracy | <FILL IN> | <FILL IN> |
   | precision_at_threshold | <FILL IN> | <FILL IN> |
   | auto_apply_rate | <FILL IN> | <FILL IN> |

   `JOB_SIGNAL_NORM`/`MARGIN_NORM` recalibrated from `6.0`/`4.0` to `4.5`/`3.0` (Task
   10) — <FILL IN: one sentence on whether the first-pass values held or needed the
   documented fallback to `5.0`/`3.5` or a revert>. `settings.classification_confidence_threshold`
   (`0.85`) was not changed, per the Phase 6 spec's explicit scope decision.

   **Known residual gap, not fixed this phase**: <FILL IN if `auto_apply_rate` is still
   low after recalibration — name which categories still don't clear 0.85 and why (e.g.
   "`messy_phrasing` and `sender_variation` still average below 0.85 because their
   company/position extraction relies on the domain-fallback tier, which always carries
   a 0.15 confidence penalty") — or write "None found — auto_apply_rate improved
   without any category regressing" if that's what the numbers actually show.>
   ```

5. In "Testing conventions", add one sentence after the existing
   `evaluation/run_eval.py is *not* part of the pytest suite...` line:
   ```
   `evaluation/compare.py` diffs the current classifier against the Phase 6 baseline
   (`evaluation/baseline_metrics.json`, frozen at Task 4 and never edited again) —
   run it after any future `app/classifier/` change to see the before/after impact
   directly. `evaluation/inspect_confidence.py` prints the confidence-formula
   components per example, for recalibration work.
   ```

6. Do not modify "Known gaps" or "Manual testing findings" — those are historical
   records of Phase 5, not Phase 6's to rewrite.

- [ ] **Step 7: Commit**

```bash
cd backend && git add tests/test_evaluation_accuracy.py
cd /Users/itshuy/Documents/Projects/Job-tracker && git add CLAUDE.md
git commit -m "$(cat <<'EOF'
test(evaluation): rebase regression bars on the Phase 6 dataset; document results

Rewrites tests/test_evaluation_accuracy.py to use evaluate() from run_eval.py
instead of duplicating its scoring logic, and rebases every MIN_* bar on the
new 88-example dataset's measured numbers (same "tolerate one more miss"
method as the original Phase 4b bars). Adds two new bars
(precision_at_threshold, auto_apply_rate) for the metrics that directly guard
the auto-apply trust-model gate. Appends a before/after results summary to
CLAUDE.md.
EOF
)"
```

---

## Self-Review Notes

**Spec coverage**: every section of the Phase 6 spec has a task — §2/§2.1 (architecture,
root causes) informed Tasks 6-8 directly; §3 (dataset weaknesses) → Tasks 1-3; §4 (new
dataset) → Tasks 1-3; §5.1 (preprocessing) → Task 6, §5.1's gmail fix → Task 5; §5.2-5.3
(extraction) → Tasks 7-8; §5.4 (status) → Task 9; §5.6 (calibration) → Task 10; §6
(metrics) → Task 4's `evaluate()`; §7 (comparison methodology + checkpoints) → Task 4's
`compare.py`/baseline plus the re-run step at the end of every task from 6 onward; the
checkpoint list itself maps 1:1 onto Tasks 4-11 (Task 1 in the spec's checkpoint list
covers this plan's Tasks 1-4; the spec's remaining checkpoints 2-6 map to this plan's
Tasks 5/6, 7/8, 9, 10, 11 respectively — split finer here because Task Right-Sizing
called for separable, independently-reviewable deliverables where the spec's checkpoints
bundled multiple files/failure-modes together).

**Placeholder scan**: the only bracketed `<...>` placeholders remaining are in Task 11
(final bar values, CLAUDE.md's results table, and the commit message's before/after
numbers in Task 10) — each is accompanied by an exact command and an exact rule for
computing the real value, not a vague "figure this out" instruction; they cannot be
filled in earlier because they depend on Tasks 1-10's actual measured output, which
doesn't exist until those tasks run.

**Type/interface consistency**: `find_company`/`find_position` signatures
(`(text: str, sender: str) -> tuple[str | None, str]`) are unchanged across Tasks 7-8;
`evaluate`/`load_dataset`/`CONFIDENCE_THRESHOLD` (Task 4) are consumed with matching
names and shapes in Tasks 10 and 11; `preprocess_body` (Task 6) is a single new
function consumed exactly once (Task 6 Step 5, `extractor.py`); the `EXTRACTION_PENALTY`
dict's four existing tier keys (`template`/`domain`/`display_name`/`none`) are read but
never modified by Tasks 7-8, consistent with Task 8's explicit scoping note that
position gets a new template, not a new tier.
