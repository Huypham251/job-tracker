# Job Application Tracker — Phase 11 Design: Classifier Quality on Real Mail

**Status:** proposed (2026-09-25), awaiting maintainer review.
**Builds on:** Phase 4 (pipeline, trust model), Phase 4b (rule-based classifier),
Phase 6/7 (classifier quality), Phase 10 (migration-first rule, public-log privacy).

> **Privacy note for this document.** The repository is public. Evidence below is
> described by *shape* ("company name followed by the user's first name"), not by the
> real employers, recruiters or the maintainer's name. The same rule applies to
> everything Phase 11 commits (§5).

---

## 0. Why

Phase 10 made syncing reliable. What syncing *produces* is still poor: on the
maintainer's real mailbox, almost nothing auto-applies and the production review queue
has grown to ~149 items, many of them false positives or bad extractions (junk company
names such as a sender's subdomain label). Phase 6/7 improved the *synthetic*
evaluation set (auto-apply rate 0.419, precision-at-threshold 1.0) but real mail has
never crossed 0.85 except for hand-written test messages.

Phase 11 improves real-mail classification and extraction accuracy. **It does not lower
`classification_confidence_threshold` (0.85)** and does not weaken any trust-model gate
— it adds one.

## 1. Goal & Non-Goals

### Goals

1. A **private, labeled, real-mail evaluation set** measured through the *same* input
   path production uses, split into dev (tune) and held-out test (score once, at the
   end).
2. A **committed, pseudonymized subset** of real-mail examples so CI gates regressions
   on real email shapes.
3. Fix the **evaluation/production input skew** (§2.1).
4. Fix **sender/domain company derivation** at the root (the "Com"/"Us"/"Recruitment"
   class), not per case.
5. Better **company/position extraction** (subject lines, name bleed via the user's own
   name, more templates).
6. Better **relatedness/status recall and precision** from real misses.
7. A **confidence model that means something**: calibrated on real dev data, so a
   correct, complete extraction can clear 0.85 and an incomplete one can't.
8. **Position required for any automatic action**, as an explicit pipeline gate.
9. A narrow, tested, one-off **re-evaluation of `pending_review` rows** so the existing
   queue benefits.

### Non-Goals

- Lowering the 0.85 threshold, or changing matching thresholds (85/60/10).
- An LLM or any external classification service (Phase 4b decision stands).
- Re-processing rows in any state other than `pending_review`: `approved`, `rejected`,
  `auto_applied` and `ignored` rows are never re-read or rewritten. **Consequence,
  accepted:** real application mail that older code already filed as `ignored` (§2.4)
  is not recovered. Phase 11 helps new mail and the current queue only.
- Modifying any application whose `source` is `"manual"` (the existing ratchet holds).
- New application statuses (e.g. "withdrawn") — needs a schema/UI change; later.
- Frontend changes.
- Company-specific rules. **No pattern, list entry or constant may name a specific
  employer** (platform domains like `greenhouse-mail.io` are fine: they are
  infrastructure, not employers). This is the main guard against overfitting one
  mailbox.

## 2. Findings (investigated 2026-09-25, read-only)

Sources: code reading; the local dev DB (same real mailbox as production, ~6,600
processed messages, 65 `pending_review`); re-running today's classifier on those rows'
stored subject/sender/snippet (bodies are not stored — see §2.6).

### 2.1 Evaluation/production input skew (root cause of several bugs)

`app/gmail/google_api.py::_clean_text` collapses **all** whitespace, newlines included
(and replaces HTML tags with a space), before the classifier sees the body.
`app/classifier/preprocess.py`'s greeting stripper and signature truncator are
line-anchored (`^…$`, `re.MULTILINE`). **In production they never fire.** 20 of the 91
synthetic examples contain newlines, so evaluation exercises code production doesn't
run. This is why company-plus-greeting captures ("<Company> <user's first name>",
"<Company> Hi <first name>") still appear in real queues after Phase 6 "fixed" them.

Also: punctuation normalization (curly apostrophes etc.) is applied to the **body only,
not the subject**, so a subject like "We’ve Received Your Application" (curly `’`)
misses the `received your application` pattern and falls to status `other`.

### 2.2 The confidence formula has structural ceilings

`confidence = base + margin + domain_bonus − max(company_penalty, position_penalty)`:

- **No position extracted → max 0.65** (0.6 + 0.3 + 0.1 − 0.35), however clear the
  email. Most real confirmations state the position only in the subject line, which no
  pattern reads.
- **One strong status phrase, perfect extraction, non-ATS sender → max 0.70**
  (3/4.5·0.6 + 0.3). Real mail typically hits exactly one phrase; 0.85 needs two.
- **Domain-derived company → −0.15** even when the email's own text names the same
  company (no corroboration is ever credited).
- ATS detection is exact-match on the domain, so regional/tenant subdomains of known
  platforms (e.g. `us.<platform-mail>.io`) get no bonus.

Local queue: 65 items, average confidence 0.361, maximum 0.75.

### 2.3 Company extraction failures

| Shape | Mechanism |
|---|---|
| "Com" | `extract_sender_domain` strips a prefix (`mail.`, `jobs.`, `e.`, `oraclecloud.`) unconditionally: `mail.com`, `jobs.com`, `e.com`, `oraclecloud.com` → `com` |
| "Us", "Recruitment", "Email", "Hr" | `_domain_derived_company` takes the **first** DNS label, not the registrable domain's: `us.<platform>.io`, `recruitment.<company>.com`, `email.<company>.com`, `hr.<company>.co.uk` |
| "Gmail" | freemail senders (recruiters writing from personal accounts) are treated as companies |
| "Tenant" | platform tenant subdomains (`tenant.fa.us2.oraclecloud.com`) |
| acronym casing ("ACME" → "Acme") | `label.capitalize()` ignores the casing the email's own text uses |
| "<Platform> <code>", "do-not-reply <company>" | display-name fallback keeps system words |
| "<Company> <first name>" | greeting/name bleed (§2.1) |
| missed "<Company> (Formerly <Other>)" | `_COMPANY_BOUNDARY` doesn't stop at `(`, so the template fails and the domain fallback wins |

### 2.4 Relatedness/status failures

- **False negatives (permanent — each message is classified once):** "Thanks for
  applying to <Co>" (only "thank you for applying" is a pattern), "Thank you for your
  application!", "your application was sent to <Co>" (job-board apply confirmations),
  assessment invitations phrased without "online assessment", "Thank you for your
  interest in <Co>". A keyword prefilter over 6,542 locally `ignored` rows found ~80
  subjects worth labeling, several of them genuine confirmations.
- **False positives:** assessment-platform account mail (password reset, login code,
  guest-account details), a job-site community post quoting an interview, social-network
  invitation digests, an HR-system daily digest, a newsletter, admissions marketing
  ("apply by midnight").
- **Status `other`** for real confirmations whose phrasing isn't in the tables (curly
  apostrophes, "thank you for your application for <req-id>").

### 2.5 Position failures

Positions are overwhelmingly in **subject lines** in shapes no template reads:
`"<Co> Careers: Application for <Position>"`, `"Thank you for applying to <Co> - <Position>"`,
`"... job application - <Position>"`, `"<Co> Application: <req-id> - <Position>"`,
`"Your recent job application for <Position>"`. One employer with three applications
and no extracted position is exactly the merge hazard the position gate (§3.6) exists
for.

### 2.6 Stale rows

`ProcessedMessage` rows are never re-classified (idempotency by design), and nothing
records which classifier version produced a row, so the queue mixes outputs of every
classifier since Phase 4. Bodies are not stored, so re-classification requires a Gmail
re-fetch.

## 3. Architecture

### 3.1 Real-mail evaluation set (`backend/evaluation/real/`, CP0)

**Private data lives outside the repository**: default `~/.job-tracker-eval/`,
overridable by `JOBTRACKER_REAL_EVAL_DIR`. `.gitignore` additionally ignores
`backend/evaluation/private/` as a backstop. Code in `evaluation/real/` is committed;
data never is.

**Export** (`python -m evaluation.real.export`, run locally against the local dev DB
and the locally connected Gmail account, after a local incremental sync brings the
local DB up to date with the mailbox):

- Candidates: every `pending_review`, `approved`, `rejected` and `auto_applied` row;
  every `ignored` row whose subject matches a job-keyword prefilter; plus a seeded
  random sample of 150 other `ignored` rows (to measure precision on negatives and
  estimate recall).
- For each, re-fetch via `google_api.get_message` and store the **raw decoded part and
  its MIME type** (not the cleaned text), so evaluation runs the production cleaning
  function end-to-end and ingestion changes (CP1) are measured.
- **Scrub before writing**, even though the data is private (defense in depth): the
  user's name variants and email address, other email addresses (keeping the sender's
  domain), URLs → `https://example.invalid/`, phone numbers, long numeric/candidate IDs
  (shape-preserving: `JR1234567890` → `JR0000000000`), street addresses.
- Records are keyed by `message_ref(message_id)` (Phase 9's hash), never the raw ID.

**Labeling** (`python -m evaluation.real.label`): an interactive CLI that shows one
scrubbed example at a time, pre-filled with today's classifier output and — where it
exists — the maintainer's own review decision (approved values, or rejected →
`is_job_related` suggestion). Keys: accept / edit field / skip / quit; resumable.
Output: `labels.jsonl` in the private dir.

**Labeling guidelines** (recorded in the CLI's help text):

- *Job-related* = about the user's own application/candidacy with a specific employer:
  confirmation (including job-board "application sent to"), assessment invite/receipt,
  interview, rejection, offer, status update, and reminders about an assessment for a
  submitted application.
- *Not job-related*: job alerts/recommendations, "complete/start your application"
  reminders for applications never submitted, platform account mail
  (passwords, login codes), community posts, newsletters, marketing, admissions.
- `company`: the employer as the email names it. `position`: the title as stated, or
  `null` if the email doesn't state one (so "position found when stated" is measurable).
- `status`: one of the existing six; withdrawn-type mail is `other`.

**Split:** deterministic by `message_ref` hash, 70% dev / 30% test, stratified by
(job-related, status). The test split is scored **only** at CP7; during tuning only dev
reports are run. `run_eval --real` refuses `--split test` unless `--final` is passed.

**Harness changes** (`evaluation/run_eval.py`): `evaluate()` accepts examples carrying
either a clean `body` (synthetic) or `raw_body` + `mime_type` (real, cleaned via the
production function). New metrics alongside the existing ones (§4). The existing
synthetic gates and `baseline_metrics.json` are unchanged; a new private
`real_baseline.json` is frozen at CP0.

### 3.2 Committed pseudonymized subset (CP2)

`python -m evaluation.real.pseudonymize` turns selected **dev-split** examples into
synthetic-looking records appended to `evaluation/dataset.jsonl` under new
`real_*` categories, chosen from the label (`real_applied`, `real_assessment`,
`real_interview_offer`, `real_rejection`, `real_other`, `real_negative`):

- Each real company is replaced with a consistent fictional name across subject, body,
  sender domain and display name (`<Co>` → "Halvorsen Robotics" / `halvorsen.com`);
  the mapping stays in the private dir. Platform domains (ATS, assessment, job boards)
  are kept — they are the shape being tested.
- Recruiter/person names → fictional names; the user's name → a fixed fictional
  candidate name; all §3.1 scrubbing applies.
- Bodies are the cleaned text (with the CP1 line structure). The `Date` header is
  replaced with a fixed date, so no example reveals when a real application happened.
- **Gate before commit:** `python -m evaluation.real.check_leaks` scans the committed
  dataset for every string in the private mapping (real company names and domains, the
  user's name/email, recruiter names) and fails on any hit; then the maintainer reviews
  the diff by eye. No real-derived example is committed without both.

Target ~40–60 committed examples. `tests/test_evaluation_accuracy.py` bars are set at
CP2 from the measured numbers (the same "tolerates one more miss" rule) and raised as later
checkpoints land.

### 3.3 Ingestion parity (CP1)

`google_api._clean_text` preserves line structure: `br` becomes `\n`; block-level HTML
tags (`p`, `div`, `tr`, `li`, `h1–h6`, `table`) become `\n\n`; horizontal whitespace
collapses to one space; 3+ newlines collapse to 2. `BODY_MAX_CHARS` is unchanged.
Preprocessing (line-anchored) then works as designed. After it runs, **paragraph
breaks** (a blank line) become sentence boundaries (`. `) and remaining single newlines
become spaces — single newlines are *not* boundaries, because hard-wrapped plain-text
mail breaks lines mid-sentence. So extraction never runs across a paragraph, and
wrapped sentences stay whole. The subject gets the same punctuation normalization as
the body.

The Phase 3 "Fetch recent messages" test view shows bodies; it will now show line
breaks — acceptable (it's a test view).

### 3.4 Sender/domain resolution (CP2) — the "Com" fix

New `app/classifier/sender.py`, replacing `extract_sender_domain`'s prefix list and
`_domain_derived_company`:

```
SenderInfo(address_domain, registrable_domain, org_label, kind, display_name)
kind ∈ {"employer", "platform", "job_board", "freemail", "unknown"}
```

- **Registrable domain via the Public Suffix List**, using `tldextract` in offline mode
  (bundled snapshot, `suffix_list_urls=()`, no cache dir — no network at runtime,
  asserted by a test). `org_label` = the label left of the public suffix
  (`recruitment.<co>.com` → `<co>`; `hr.<co>.co.uk` → `<co>`). A bare suffix
  (`com`) can never be an org label.
- **Platform suffix matching**: `PLATFORM_DOMAINS` (ATS + assessment platforms, e.g.
  `greenhouse.io`, `greenhouse-mail.io`, `myworkday.com`, `icims.com`, `oraclecloud.com`,
  `hackerrank.com`, …) matched against the registrable domain, so every regional or
  tenant subdomain is covered. `JOB_BOARD_DOMAINS` (e.g. `linkedin.com`, `indeed.com`,
  `glassdoor.com`) and `FREEMAIL_DOMAINS` (e.g. `gmail.com`, `outlook.com`) likewise.
  None of these kinds ever yields a domain-derived company. `ATS_DOMAINS`'s relatedness
  bonus applies to `kind == "platform"`.
- **Platform tenants**: a tenant label left of a platform domain (`<co>.wd5.myworkday.com`)
  is a *weak* company candidate only if it is alphabetic, ≥3 chars and not in
  `GENERIC_LABELS` (`mail`, `email`, `us`, `eu`, `wd1`–`wd12`, `fa`, `hr`, `jobs`, …).
- **Display name cleaning**: strips system words (`no-reply`, `do-not-reply`,
  `notifications`, `careers`, `recruiting`, `talent`, `team`, `hr`, platform names) and
  rejects what's left if it is empty, one character, or contains digits/codes only.
- **Casing from the text**: a domain-derived name is matched against capitalized runs in
  the subject/body with spaces/punctuation removed (`acmerobotics` ↔ "Acme
  Robotics"); if found, the text's form is used *and* the candidate counts as
  corroborated (§3.7). Otherwise `capitalize()` as today.

Invariant, property-tested: a derived company is never a public suffix, a
`GENERIC_LABELS` entry, a platform/job-board/freemail name, or shorter than 2 chars.
The Phase 7 `oraclecloud.<co>.com` behavior falls out of this generally (`<co>` is the
registrable label) and its existing tests must keep passing.

### 3.5 Extraction (CP3)

**Interface change — the extractor receives the recipient's identity.**

```python
@dataclass(frozen=True)
class Recipient:
    name: str | None      # User.name
    email: str | None     # User.email

class Extractor(Protocol):
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str, recipient: Recipient
    ) -> EmailExtraction: ...
```

Required (no default), so no call site can silently skip it. `process_message` gets the
`User` (already one query away via `job.user_id`); the worker loads it once per job.
Evaluation passes the fixed fictional candidate's `Recipient`.

Uses:
- **Name bleed**: after capture, a company span is cut at the first token that is part
  of the recipient's name (case-insensitive, whole tokens; given-name prefixes
  included), in addition to the existing greeting-word trim.
- Senders equal to the recipient's own address are not a company source.

**Subject-line templates** (new, tried before body templates for position; each a
bounded token pattern like today's):
- `<Co> Careers: Application for <Pos>` / `Application for <Pos>`
- `applying to <Co> - <Pos>` / `application - <Pos>`
- `<Co> Application: <req-id> - <Pos>` (req-id = token containing digits)
- `(your )?(recent )?(job )?application for <Pos>`
- `Your <Co> application`, `Update on your <Co> application`, `Interview with <Co>`,
  `your application was sent to <Co>`, `Thanks for applying to <Co>`

Trailing noise stripped from positions: `(Spring|Summer|Fall 20xx)` suffixes are kept
(they distinguish applications), req-ids and trailing ` - <digits>` are removed.
`_COMPANY_BOUNDARY` also stops at `(`.

Each field returns a **provenance** used by §3.7: `subject_template`, `body_template`,
`domain`, `display_name`, `tenant`, `none`.

### 3.6 Relatedness and status (CP4)

Pattern-table changes driven only by dev-split misses, each backed by a regression
test:
- Applied: `thanks? (you )?for applying`, `thank you for your application`,
  `application was sent to`, `successfully submitted`, `thank you for your interest in`
  (weak, 2).
- OA: assessment invitations/receipts (`invit\w* .{0,40}assessment`,
  `assessment (completed|invitation)`, `complete .{0,30}assessment`).
- Negatives: `password reset`, `(login|verification) code`, `guest account`,
  `daily digest`, `new invitations`, admissions phrasing (`apply to .{0,20}(college|university)`,
  `application deadline` + `admission`). Weighted, like today, to clear the evidenced
  cases without touching positive patterns.
- `ATS_DOMAINS` bonus is replaced by the `kind == "platform"` check (§3.4).

### 3.7 Confidence model (CP5)

Replace "sum minus the largest penalty" with a **weakest-link** model over three
components, each in [0, 1]:

- `relatedness` — from net signal, saturating: one strong (weight-3) status phrase from
  a platform sender, or two independent phrases, is enough for a high score.
- `status_clarity` — from the top-vs-runner-up margin; `other` scores low.
- `company` — by provenance and corroboration:
  - template (subject or body) **and** corroborated by the sender's org label or cleaned
    display name → highest;
  - template alone, or domain/display-name corroborated by a text mention → high;
  - domain/display-name/tenant alone → medium (can never clear 0.85 alone);
  - none → 0.

`confidence = min(relatedness, status_clarity, company)`. The component values are
constants in `patterns.py`, set by measurement on the **real dev split** (one change at
a time, re-measure — the Phase 6 procedure) so that the [0.85, 1.0] band is ≥95%
correct. `evaluation/inspect_confidence.py` prints the three components per example.

**Position is not a confidence component; it is a pipeline gate.** A fourth independent
gate in `pipeline/service.py::_apply_decision`: if `extraction.position` is empty, the
decision is `pending_review`, whatever the confidence. This makes the
"no merge without a position" safeguard explicit and testable instead of an emergent
effect of a penalty constant, and lets the displayed confidence honestly describe
classification quality. The other three gates are unchanged.

### 3.8 Re-evaluating the existing queue (CP6)

**Data model — migration `0008`**, shipped alone first (Phase 10 migration-first rule):
`processed_messages` gains three nullable columns:
- `classifier_version` (string) — written on every new row from CP6b on
  (`CLASSIFIER_VERSION` constant in `app/classifier/`); `NULL` = pre-Phase-11.
- `reevaluated_at` (timestamptz).
- `previous_classification` (JSONB) — the row's prior `is_job_related`, `confidence`,
  extracted fields, `proposed_action`, `matched_application_id`, `review_status`,
  `classifier_version`, captured at re-evaluation for audit and manual rollback.

**Command:** `python -m app.pipeline.reevaluate [--apply] [--limit N]`.

- **Selection:** `review_status = 'pending_review'` only. Nothing else is read for
  rewriting.
- **Preconditions:** skips (and counts) any user with a `queued`/`running` sync job or
  `reauth_required_at` set, and **re-checks for an active sync job before every row**,
  stopping that user's run if one appeared. (A sync job row exists the moment Sync is
  clicked; the worker needs a GitHub runner to boot — 10s or more — before it can write
  anything, so the per-row check always sees the job first.)
- **Per row:** re-fetch the message (`google_api.get_message`, with the worker's
  fresh-token handling), classify with the current extractor and the user's
  `Recipient`. Then, in one transaction: `SELECT … FOR UPDATE` the row; if it is **no
  longer `pending_review`** (the user acted meanwhile), leave it untouched; otherwise
  lock the user's applications (`FOR UPDATE`, so a concurrent manual edit can't slip
  between the source check and the write), run the **same** decision function a sync
  uses (renamed `apply_decision`, all four gates), snapshot the row into
  `previous_classification` **only if it has no snapshot yet** (a second run keeps the
  original), and update the classification fields, `review_status`,
  `matched_application_id`, `proposed_action`, `classifier_version`, `reevaluated_at`.
- **Review actions take the same row lock:** approve/reject (`_get_pending_item`) select
  the row `FOR UPDATE`, so a click racing a re-evaluation waits for it and then sees the
  row's new state (a 404 if it left the queue) instead of acting on a stale read.
- **Outcomes:** a row may stay `pending_review` with better fields, become `ignored`
  (now judged not job-related — leaves the queue), or become `auto_applied` (only
  through the unchanged gates, so never touching a `source="manual"` application and
  never without a position).
- **Failures:** a 404 (message deleted) or per-message fetch error leaves the row
  untouched and is counted; a `GmailAuthError` stops that user's run.
- **Dry run by default:** computes and prints outcomes without writing.
- **Logs are aggregate counts only** (per outcome and transition, e.g.
  `pending_review→ignored: 41`) — no subjects, companies or message IDs, because it runs
  in a public GitHub Actions log.

**Where it runs:** `.github/workflows/reevaluate.yml`, `workflow_dispatch` only, input
`apply` (boolean, default false), its **own** `concurrency: reevaluate` group (not the
incremental lane's: GitHub keeps only one *pending* run per group, so sharing it would
let a Sync click's dispatch cancel a pending re-evaluation or vice versa — the per-row
active-sync check above is what keeps it away from syncs), SHA-pinned actions,
`persist-credentials: false` on checkout, `permissions: contents: read`, same secrets
as the lane workflows.
Sequence in production: dry run → maintainer reviews the counts → `apply` run.

## 4. Metrics

Reported for the synthetic set (existing report, unchanged) and the real dev/test split:

| Metric | Definition | Why |
|---|---|---|
| relatedness precision / **recall** | per example | recall first: a miss is dropped forever |
| status accuracy + confusion matrix | true positives only | |
| company accuracy (normalized) | `matching.normalize` equality | what matching actually uses |
| **junk-company rate** | extracted company that is a §3.4 invariant violation or matches none of the labeled company's tokens | the "Com" class |
| position found-when-stated | position non-null where the label has one | position gate coverage |
| position accuracy (fuzzy ≥90) | where labeled | |
| **precision at threshold** | among cleared-and-positioned predictions: related, status, company (normalized), position (fuzzy) all correct | the safety guard |
| **auto-apply rate** | true positives with a stated position that clear 0.85 | the goal |
| calibration table | accuracy per confidence band (0.1 wide) | 0.85 must mean ≥95% |
| queue false-positive share | queue candidates labeled not-job-related | review burden |

## 5. Privacy & safety rules

1. Real email content (raw or scrubbed), labels, the pseudonym mapping and real-set
   reports stay in the private dir. Never in git, CI, artifacts or logs.
2. Committed real-derived examples pass `check_leaks` *and* maintainer review.
3. The spec, plan, commit messages and CLAUDE.md describe real evidence by shape, not
   by employer or person.
4. Production logs from `reevaluate` are counts only.
5. No company-specific literals in classifier code (§1 Non-Goals).

## 6. Acceptance criteria

Scored at CP7 on the **held-out real test split** unless noted. Targets are provisional:
they may be revised **once**, at the CP0 review, with the baseline in hand, and the
revision is recorded in §9.

1. Precision at threshold **≥ 0.97**, over at least 15 cleared predictions; **zero**
   cleared predictions with a junk company.
2. Relatedness recall **≥ 0.95**; precision **≥ 0.90**.
3. Company normalized accuracy **≥ 0.90**; junk-company rate **0** (also on dev).
4. Position found-when-stated **≥ 0.80**.
5. Auto-apply rate **≥ 0.50** (baseline: ~0).
6. Calibration: the [0.85, 1.0] band is ≥ 95% correct on dev+test combined.
7. Synthetic set: no existing `test_evaluation_accuracy.py` bar regresses. If a
   synthetic example turns out to encode behavior real mail contradicts, it may be
   relabeled only with a written reason in the plan.
8. Unit tests: the §3.4 invariant (property-style over generated senders, including
   `mail.com`, `jobs.com`, `e.com`, `oraclecloud.com`, `us.<platform>.io`, multi-part
   public suffixes, freemail); the position gate; the recipient name-bleed trim; the
   no-network `tldextract` configuration.
9. Re-evaluation tests (real DB): only `pending_review` rows selected; `approved`/
   `rejected`/`auto_applied`/`ignored` rows byte-identical after `--apply`; a row the
   user approves between fetch and write is left untouched; a `source="manual"`
   application is never modified; a missing position never auto-applies; dry run
   writes nothing; logs contain no subject/company/message-id; `previous_classification`
   round-trips.
10. Production: `0008` applied before code that writes it; dry run then apply of the
    re-evaluation; queue size before/after recorded (counts only); an incremental sync
    stays ~1 min or less.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Overfitting one mailbox (mostly internship ATS mail) | held-out test split; no employer-specific rules; mechanisms (registrable domain, subject templates) over phrases |
| Private data leaking into the public repo | outside-repo default dir, `.gitignore` backstop, `check_leaks`, maintainer review, shape-only docs |
| CP1 changes every extraction's input | measured on real dev + synthetic before/after; synthetic gates in CI |
| Labeling effort (~400 examples) | pre-filled labels, review decisions seed them, resumable CLI; mostly "accept" keystrokes |
| `tldextract` snapshot ages | PSL changes rarely matter here; a test pins that it never goes to the network |
| Re-evaluation racing a user action or a sync | row lock + state re-check (also taken by approve/reject); applications locked per row; active-sync check before every row |
| Re-evaluation moving good items out of the queue | dry run first; `previous_classification` snapshot allows manual rollback |
| Weekly Gmail reconnect (Testing consent) blocks export/re-eval | reconnect before running; `GmailAuthError` stops cleanly |

## 8. Implementation checkpoints

Each checkpoint: dev-split report before/after, full local checks
(`uv run pytest`, `evaluation.compare`, frontend `tsc -b`/`oxlint`), pushed separately,
CI green. Maintainer-blocking steps are marked **(M)**.

- **CP0 — Real evaluation set.** Local incremental sync to catch up; `evaluation/real/`
  (export, scrub, label CLI, split, `run_eval --real`); **(M)** labeling; freeze private
  `real_baseline.json`. Maintainer confirms or revises §6 targets. The only `app/`
  changes are behavior-preserving: `google_api.get_message_raw`/`clean_body` and moving
  the worker's fresh-token helper to `gmail_service.call_with_fresh_token`.
- **CP1 — Ingestion parity.** `_clean_text` line structure; paragraph break → sentence
  boundary after preprocessing; subject normalization.
- **CP2 — Sender resolution + committed subset.** `sender.py`, `tldextract` dependency
  (offline), platform/job-board/freemail/generic tables, casing from text, invariant
  tests. Then pseudonymize + `check_leaks` + **(M)** review, and commit the `real_*`
  examples with bars. (Moved here from CP0 during planning: pseudonymizing needs
  `sender.py` to find the domain labels to replace, and committed bodies should have
  CP1's line structure so they match what production feeds the classifier.)
- **CP3 — Extraction.** `Recipient` interface change through pipeline, worker and
  evaluation; name-bleed trim; subject-line templates; provenance.
- **CP4 — Relatedness & status.** Pattern-table changes from dev misses.
- **CP5 — Confidence & position gate.** Weakest-link model, calibration on dev,
  position gate in `_apply_decision`, `inspect_confidence.py` update.
- **CP6a — Migration `0008` alone**, deployed and verified in production
  (`alembic_version`, columns present) before anything writes it.
- **CP6b — Re-evaluation.** `CLASSIFIER_VERSION` on new rows, `reevaluate` command and
  workflow, tests; **(M)** production dry run → apply.
- **CP7 — Final.** Score the held-out test split once; record results; update README,
  CLAUDE.md ("Phase 11 results", resolved known gaps) and this spec's §9.

## 9. Results / decisions log

- 2026-09-25 — Maintainer decisions before this spec: keep requiring a position for
  automatic actions (→ §3.7 gate); two-tier evaluation set with only pseudonymized data
  committed (→ §3.1–3.2); pass the user's identity into the extractor, interface change
  accepted (→ §3.5); re-evaluate `pending_review` rows only, narrowly and tested
  (→ §3.8).
