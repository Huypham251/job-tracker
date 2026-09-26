# Phase 11 — Classifier Quality on Real Mail — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the rule-based classifier accurate on the maintainer's real Gmail —
measured on a private, labeled real-mail set — without lowering the 0.85 threshold, and
re-evaluate the existing review queue once with the improved classifier.

**Architecture:** A private evaluation set (outside the repo) is exported from the
local dev DB + Gmail, scrubbed, labeled and split dev/test; the harness runs it through
the production body-cleaning path. Classifier fixes land checkpoint by checkpoint
(ingestion parity → sender resolution → extraction → patterns → confidence), each
measured on the dev split. A narrow `reevaluate` command rewrites only `pending_review`
rows through the unchanged pipeline gates plus a new position gate.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres (Neon in prod),
pytest, rapidfuzz, **tldextract** (new, offline mode), GitHub Actions, uv.

**Spec:** `docs/superpowers/specs/2026-09-25-job-tracker-phase-11-real-mail-classifier-quality-design.md`
(read it first; section numbers below — "spec §3.4" — refer to it).

## Global Constraints

- `classification_confidence_threshold` stays **0.85**; matching thresholds stay 85/60/10.
- **No employer-specific literal** in `backend/app/classifier/` (no real company name, no
  real company domain). Platform/job-board/freemail domains are infrastructure and allowed.
- **Private data never enters the repo**: default private dir `~/.job-tracker-eval/`
  (env `JOBTRACKER_REAL_EVAL_DIR`), refused if inside the repo. Real-derived examples
  are committed only via `check_leaks` **and** maintainer review.
- Commit messages, docs, tests and comments describe real evidence **by shape**, never
  by real employer or person. Test fixtures use fictional names only.
- Public logs (GitHub Actions) carry **counts only** — no subjects, companies or raw
  Gmail message IDs (`message_ref()` if a message must be referenced).
- **Migration-first:** `0008` ships alone and is verified in production before code
  that reads or writes its columns.
- No network access at classification time (`tldextract` offline, no cache dir).
- Rows in `approved`, `rejected`, `auto_applied`, `ignored` are never rewritten; a
  `source="manual"` application is never modified by automation.
- Automatic actions require a non-empty position (new pipeline gate, Task 15).
- Existing synthetic bars in `tests/test_evaluation_accuracy.py` must not be lowered.
- Checks before every push (maintainer merges straight to `main`, CI does not gate
  deploy): `cd backend && uv run pytest && uv run python -m evaluation.compare`, and
  `cd frontend && npx tsc -b && npx oxlint`.
- Fictional candidate used everywhere real text is stored or evaluated:
  **"Quinlan Ellery" / `quinlan.ellery@example.com`** (absent from the current dataset).

## Review Focus

1. **Malformed `From` headers** (`""`, `<>`, `undisclosed-recipients:;`, a bare name,
   `a@`, `x@localhost`, upper-case domains) → no exception, no invented company. Pinned
   in Task 9 (`test_parse_sender_never_raises_on_malformed_from_headers`).
2. **Unusual user names** (single token, empty, non-ASCII, hyphenated, one-letter
   initials) → name-bleed trimming works on whole tokens, never empties a company, never
   raises. Pinned in Task 13 (`test_recipient_name_trim_handles_unusual_names`).
3. **A pending row whose Gmail message was deleted, or whose matched application was
   deleted** → re-evaluation leaves the row untouched (404) or reclassifies it normally
   (no dangling FK). Pinned in Task 19 (`test_deleted_message_is_left_untouched`,
   `test_row_whose_matched_application_was_deleted_is_reclassified`).
4. **HTML that is only layout** (tables of `&nbsp;`, zero-width characters, soft
   hyphens) → cleaned body has no runaway blank lines or invisible characters. Pinned in
   Task 8 (`test_clean_body_drops_invisible_characters_and_layout_only_html`).
5. **Running `reevaluate --apply` twice** → the original pre-Phase-11 snapshot is kept.
   Pinned in Task 19 (`test_second_apply_keeps_the_original_snapshot`).

---

## File Structure

**Create**
- `backend/evaluation/real/__init__.py` — package marker.
- `backend/evaluation/real/privacy.py` — private dir resolution, repo guard, scrubber, fictional identity.
- `backend/evaluation/real/dataset.py` — raw/label storage, validation, stratified split, `load_real_examples`, CLI (`--freeze-split`, `--stats`, `--list-dev`).
- `backend/evaluation/real/export.py` — select candidate rows, fetch raw parts, scrub, write.
- `backend/evaluation/real/label.py` — interactive labeling CLI.
- `backend/evaluation/real/pseudonymize.py` — real→fictional mapping, preview, append.
- `backend/evaluation/real/check_leaks.py` — scan committed real-derived lines for private terms.
- `backend/evaluation/bars.py` — suggest "tolerates one more miss" bars.
- `backend/app/classifier/sender.py` — `SenderInfo`, `parse_sender` (Public Suffix List).
- `backend/app/pipeline/reevaluate.py` — pending-row re-evaluation + CLI.
- `backend/alembic/versions/0008_add_classifier_version_and_reevaluation.py`
- `.github/workflows/reevaluate.yml`
- Tests: `test_eval_real_privacy.py`, `test_eval_real_dataset.py`, `test_eval_real_export.py`,
  `test_eval_real_label.py`, `test_eval_real_pseudonymize.py`, `test_evaluation_bars.py`,
  `test_classifier_sender.py`, `test_migrations_0008.py`, `test_pipeline_reevaluate.py`,
  `test_reevaluate_workflow.py`, `test_reevaluate_entrypoint.py`.

**Modify**
- `backend/app/gmail/google_api.py` — `get_message_raw`, `clean_body`, line-preserving cleaning.
- `backend/app/gmail/service.py` — `call_with_fresh_token` (moved from the worker).
- `backend/app/sync/worker.py` — use `gmail_service.call_with_fresh_token`.
- `backend/app/classifier/{schemas,text,patterns,preprocess,fields,extractor}.py`
- `backend/app/pipeline/{service,models}.py`
- `backend/evaluation/{run_eval,compare,inspect_confidence}.py`, `backend/evaluation/dataset.jsonl` (append only)
- `backend/tests/` — fakes and assertions touched by interface changes (listed per task).
- `backend/pyproject.toml`, `backend/uv.lock`, `.gitignore`, `README.md`, `CLAUDE.md`, the spec's §9.

---

# CP0 — Real evaluation set

### Task 1: Private-data rules (`evaluation/real/privacy.py`)

**Files:**
- Create: `backend/evaluation/real/__init__.py` (empty), `backend/evaluation/real/privacy.py`
- Modify: `.gitignore`
- Test: `backend/tests/test_eval_real_privacy.py`

**Interfaces:**
- Produces: `REPO_ROOT: Path`, `FICTIONAL_NAME/GIVEN/FAMILY/EMAIL: str`, `SCRUBBED_URL`,
  `ensure_outside_repo(path: Path) -> Path` (raises `SystemExit`), `private_dir() -> Path`,
  `Identity(name: str, email: str, extra_terms: tuple[str, ...] = ())`,
  `scrub(text: str, identity: Identity) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_eval_real_privacy.py
import pytest

from evaluation.real.privacy import (
    FICTIONAL_EMAIL,
    FICTIONAL_FAMILY,
    FICTIONAL_GIVEN,
    FICTIONAL_NAME,
    REPO_ROOT,
    SCRUBBED_URL,
    Identity,
    ensure_outside_repo,
    private_dir,
    scrub,
)

IDENTITY = Identity(name="Mira Tan Okoro", email="mira.okoro@example.org", extra_terms=("12 Elm Row",))


def test_ensure_outside_repo_refuses_the_repo_and_anything_inside_it() -> None:
    with pytest.raises(SystemExit):
        ensure_outside_repo(REPO_ROOT)
    with pytest.raises(SystemExit):
        ensure_outside_repo(REPO_ROOT / "backend" / "evaluation" / "private")


def test_private_dir_honors_the_env_var_and_creates_it(tmp_path, monkeypatch) -> None:
    target = tmp_path / "eval"
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(target))
    assert private_dir() == target.resolve()
    assert target.is_dir()


def test_private_dir_refuses_an_env_var_pointing_into_the_repo(monkeypatch) -> None:
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(REPO_ROOT / "tmp-eval"))
    with pytest.raises(SystemExit):
        private_dir()


def test_scrub_replaces_every_form_of_the_users_name() -> None:
    text = "Hi Mira Tan, thanks Mira Tan Okoro. OKORO MIRA TAN applied. Dear Mira, bye Okoro"
    result = scrub(text, IDENTITY)
    assert "mira" not in result.lower() and "okoro" not in result.lower() and "Tan" not in result
    assert f"Hi {FICTIONAL_GIVEN}," in result
    assert f"thanks {FICTIONAL_NAME}." in result
    assert f"bye {FICTIONAL_FAMILY}" in result


def test_scrub_replaces_emails_keeping_only_the_domain_of_others() -> None:
    result = scrub("to mira.okoro@example.org from jane.doe@halvorsen.com", IDENTITY)
    assert FICTIONAL_EMAIL in result
    assert "person@halvorsen.com" in result
    assert "jane.doe" not in result


def test_scrub_replaces_urls_phones_ids_and_extra_terms() -> None:
    text = (
        'Visit https://careers.halvorsen.com/apply?id=abc or <a href="http://x.io/t">x</a>. '
        "Call (206) 555-0142. Req JR2026505093 / 2026-90891. Class of 2027. Lives at 12 Elm Row."
    )
    result = scrub(text, IDENTITY)
    assert "halvorsen.com/apply" not in result and SCRUBBED_URL in result
    assert "555-0142" not in result
    assert "JR0000000000" in result and "0000-00000" in result
    assert "2027" in result  # a year is not an ID
    assert "12 Elm Row" not in result and "[redacted]" in result


def test_scrub_tolerates_empty_text_and_a_single_token_name() -> None:
    assert scrub("", IDENTITY) == ""
    single = Identity(name="Mira", email="m@example.org")
    assert scrub("Hi Mira,", single) == f"Hi {FICTIONAL_GIVEN},"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_eval_real_privacy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evaluation.real'`.

- [ ] **Step 3: Implement**

```python
# backend/evaluation/real/privacy.py
"""Private-data rules for the real-mail evaluation set (Phase 11 spec §3.1, §5).

Real email content never enters the repository. It lives in private_dir(), outside
the repo, and is scrubbed before it is written even there (defense in depth — the
directory is private, but a scrubbed copy is what we'd want if it ever leaked)."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRIVATE_DIR = Path.home() / ".job-tracker-eval"

# Stands in for the maintainer everywhere real-derived text is stored or evaluated.
# Chosen because neither word appears in evaluation/dataset.jsonl's synthetic examples.
FICTIONAL_NAME = "Quinlan Ellery"
FICTIONAL_GIVEN = "Quinlan"
FICTIONAL_FAMILY = "Ellery"
FICTIONAL_EMAIL = "quinlan.ellery@example.com"
SCRUBBED_URL = "https://example.invalid/"


def ensure_outside_repo(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit(
            f"Refusing to use {resolved}: private evaluation data must live outside the "
            f"repository ({REPO_ROOT})."
        )
    return resolved


def private_dir() -> Path:
    path = ensure_outside_repo(Path(os.environ.get("JOBTRACKER_REAL_EVAL_DIR", str(DEFAULT_PRIVATE_DIR))))
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Identity:
    name: str
    email: str
    extra_terms: tuple[str, ...] = ()


_EMAIL_RE = re.compile(r"[\w.+-]+@((?:[\w-]+\.)+[\w-]+)")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)")
# Requisition/candidate IDs: an optional short letter prefix, then 6+ digits (hyphens
# allowed inside). Digits become 0 so the SHAPE survives — position extraction has to
# learn to strip these. A year ("2027") is too short to match.
_LONG_ID_RE = re.compile(r"\b([A-Za-z]{0,4})(\d[\d-]{4,}\d)\b")


def _whole_word(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def _name_replacements(name: str) -> list[tuple[re.Pattern[str], str]]:
    tokens = [t for t in name.split() if len(t) >= 2]
    if not tokens:
        return []
    if len(tokens) == 1:
        variants = [(tokens[0], FICTIONAL_GIVEN)]
    else:
        given, family = " ".join(tokens[:-1]), tokens[-1]
        variants = [
            (" ".join(tokens), FICTIONAL_NAME),
            (f"{family} {given}", FICTIONAL_NAME),
            (given, FICTIONAL_GIVEN),
            (family, FICTIONAL_FAMILY),
            *((token, FICTIONAL_GIVEN) for token in tokens[:-1]),
        ]
    # Longest first, so the full name is replaced before its parts are.
    variants.sort(key=lambda pair: len(pair[0]), reverse=True)
    return [(_whole_word(source), replacement) for source, replacement in variants]


def scrub(text: str, identity: Identity) -> str:
    if not text:
        return text
    text = _URL_RE.sub(SCRUBBED_URL, text)
    own_email = identity.email.strip().lower()

    def _email(match: re.Match[str]) -> str:
        return FICTIONAL_EMAIL if match.group(0).lower() == own_email else f"person@{match.group(1)}"

    text = _EMAIL_RE.sub(_email, text)
    for pattern, replacement in _name_replacements(identity.name):
        text = pattern.sub(replacement, text)
    for term in identity.extra_terms:
        if term.strip():
            text = _whole_word(term.strip()).sub("[redacted]", text)
    text = _PHONE_RE.sub("000-000-0000", text)
    return _LONG_ID_RE.sub(lambda m: m.group(1) + re.sub(r"\d", "0", m.group(2)), text)
```

Append to `.gitignore` (repo root):

```gitignore

# Phase 11 — private real-mail evaluation data lives OUTSIDE the repo
# (~/.job-tracker-eval by default); this is only a backstop.
backend/evaluation/private/
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_eval_real_privacy.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add .gitignore backend/evaluation/real/__init__.py backend/evaluation/real/privacy.py backend/tests/test_eval_real_privacy.py
git commit -m "feat(eval): add private-data rules and scrubber for the real-mail evaluation set"
```

---

### Task 2: Raw Gmail parts, a public `clean_body`, and a shared fresh-token helper

Behavior-preserving `app/` changes that the export and re-evaluation need.

**Files:**
- Modify: `backend/app/gmail/google_api.py`, `backend/app/gmail/service.py`, `backend/app/sync/worker.py`
- Test: `backend/tests/test_gmail_google_api.py`, `backend/tests/test_gmail_service.py`

**Interfaces:**
- Produces: `google_api.get_message_raw(access_token: str, message_id: str) -> tuple[dict, str | None, str]`
  (summary, MIME type of the chosen text part or `None`, undecoded-by-cleaning text or `""`);
  `google_api.clean_body(raw: str, mime_type: str | None) -> str`;
  `gmail_service.call_with_fresh_token(db: Session, connection: GmailConnection, call: Callable[[str], T]) -> T`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_gmail_google_api.py` (it already has `_FakeResponse` and `_b64`):

```python
def test_get_message_raw_returns_the_uncleaned_part_and_its_mime_type(monkeypatch) -> None:
    raw = "<p>Thanks for applying to Acme.</p>"
    payload = {
        "payload": {
            "mimeType": "text/html",
            "body": {"data": _b64(raw)},
            "headers": [{"name": "Subject", "value": "Hello"}],
        }
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    summary, mime_type, text = google_api.get_message_raw("token", "m1")
    assert (summary["subject"], mime_type, text) == ("Hello", "text/html", raw)


def test_clean_body_matches_what_get_message_returns(monkeypatch) -> None:
    raw = "<html><body><p>Hello <b>World</b></p><p>Second</p></body></html>"
    payload = {"payload": {"mimeType": "text/html", "body": {"data": _b64(raw)}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    _, mime_type, text = google_api.get_message_raw("token", "m1")
    assert google_api.clean_body(text, mime_type) == google_api.get_message("token", "m1")[1]


def test_get_message_raw_without_a_text_part(monkeypatch) -> None:
    payload = {"payload": {"mimeType": "image/png", "body": {"data": _b64("binary")}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    _, mime_type, text = google_api.get_message_raw("token", "m1")
    assert (mime_type, text) == (None, "")
    assert google_api.clean_body(text, mime_type) == ""
```

Append to `backend/tests/test_gmail_service.py` (add imports at the top if missing:
`from app.gmail import service as gmail_service`, `from app.gmail.google_api import GmailAuthError`, `import pytest`):

```python
def test_call_with_fresh_token_forces_one_refresh_on_a_401(monkeypatch) -> None:
    monkeypatch.setattr(gmail_service, "get_valid_access_token", lambda db, c: "stale")
    monkeypatch.setattr(gmail_service, "force_refresh_access_token", lambda db, c: "fresh")
    tokens: list[str] = []

    def call(token: str) -> str:
        tokens.append(token)
        if token == "stale":
            raise GmailAuthError("message fetch failed: HTTP 401", status_code=401)
        return "ok"

    assert gmail_service.call_with_fresh_token(None, None, call) == "ok"
    assert tokens == ["stale", "fresh"]


def test_call_with_fresh_token_does_not_retry_a_revoked_grant(monkeypatch) -> None:
    monkeypatch.setattr(gmail_service, "get_valid_access_token", lambda db, c: "t")

    def call(token: str) -> str:
        raise GmailAuthError("token refresh failed: invalid_grant")

    with pytest.raises(GmailAuthError):
        gmail_service.call_with_fresh_token(None, None, call)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py tests/test_gmail_service.py -v -k "raw or clean_body or fresh_token"`
Expected: FAIL — `AttributeError: module 'app.gmail.google_api' has no attribute 'get_message_raw'` / `call_with_fresh_token`.

- [ ] **Step 3: Implement**

In `backend/app/gmail/google_api.py`, replace `_body_from_payload` and `get_message` with:

```python
def clean_body(raw: str, mime_type: str | None) -> str:
    """The text the classifier sees, from a decoded text part. Public so the Phase 11
    evaluation harness cleans private real examples exactly as production does."""
    if not raw:
        return ""
    return _clean_text(raw, is_html=(mime_type == "text/html"))


def _body_from_payload(payload: dict) -> str:
    found = _find_text_part(payload.get("payload", {}))
    if found is None:
        return ""
    mime_type, text = found
    return clean_body(text, mime_type)


def _fetch_full(access_token: str, message_id: str) -> dict:
    response = _send(
        "get",
        f"{GMAIL_API_BASE}/messages/{message_id}",
        "message fetch",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "full"},
        timeout=_TIMEOUT,
    )
    _raise_for_status(response, "message fetch")
    return response.json()


def get_message(access_token: str, message_id: str) -> tuple[dict, str]:
    """(keep the existing docstring unchanged)"""
    payload = _fetch_full(access_token, message_id)
    return _summary_from_payload(payload, message_id), _body_from_payload(payload)


def get_message_raw(access_token: str, message_id: str) -> tuple[dict, str | None, str]:
    """Local Phase 11 evaluation export only: the chosen text part BEFORE cleaning,
    with its MIME type, so the evaluation set re-runs whatever clean_body() does at
    the time. Never used by the sync worker or the API."""
    payload = _fetch_full(access_token, message_id)
    found = _find_text_part(payload.get("payload", {}))
    mime_type, raw = found if found is not None else (None, "")
    return _summary_from_payload(payload, message_id), mime_type, raw
```

In `backend/app/gmail/service.py` (it already has `from app.gmail import google_api`;
add `from collections.abc import Callable` and `from typing import TypeVar`), add:

```python
T = TypeVar("T")


def call_with_fresh_token(db: Session, connection: GmailConnection, call: Callable[[str], T]) -> T:
    """Runs a Gmail call with a valid access token (checked before every call, not
    once per page: a page of new messages can outlast the token's last minute). A
    401 still gets one forced refresh and retry before it counts as a revoked grant
    — the token may have expired mid-call, or a reconnect may have replaced the
    grant. GmailAuthError out of here means reconnect. (Moved from the sync worker
    in Phase 11 so the evaluation export and the re-evaluation command share it.)"""
    try:
        return call(get_valid_access_token(db, connection))
    except google_api.GmailAuthError as exc:
        if exc.status_code != 401:
            raise
    return call(force_refresh_access_token(db, connection))
```

In `backend/app/sync/worker.py`: delete `_call_with_fresh_token` and replace its three
call sites (`list_message_ids_page` call and the two in `_fetch_message`) with
`gmail_service.call_with_fresh_token(...)` — same arguments.

- [ ] **Step 4: Run the Gmail, worker and failure-injection suites**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py tests/test_gmail_service.py tests/test_sync_worker.py tests/test_sync_failure_injection.py -q`
Expected: all PASS (existing worker tests prove the move changed nothing).

- [ ] **Step 5: Commit**

```bash
git add backend/app/gmail/google_api.py backend/app/gmail/service.py backend/app/sync/worker.py backend/tests/test_gmail_google_api.py backend/tests/test_gmail_service.py
git commit -m "refactor(gmail): expose raw message parts and clean_body; share the fresh-token helper"
```

---

### Task 3: Evaluation harness — real examples, new metrics, CLI

**Files:**
- Modify: `backend/evaluation/run_eval.py` (full replacement below), `backend/evaluation/compare.py` (full replacement below), `backend/tests/test_evaluation_accuracy.py`
- Test: `backend/tests/test_evaluation_run_eval.py`

**Interfaces:**
- Consumes: `google_api.clean_body` (Task 2), `app.pipeline.matching.normalize`.
- Produces: `evaluate(extractor, examples, *, misses: list | None = None) -> dict`;
  `example_body(example) -> str`; `is_junk_company(actual, expected) -> bool`;
  `synthetic_examples(examples)`, `real_derived_examples(examples)`, `REAL_CATEGORY_PREFIX = "real_"`;
  finalized report keys (new): `company_norm_accuracy`, `company_junk_rate`,
  `position_found_rate`, `status_confusion` (`{"applied->other": n}`), `cleared`, `auto_apply_precision`, `auto_apply_rate_positioned`,
  `queue_false_positive_share`, `calibration` (`{"0.8": {"n", "accuracy"}}`),
  `counts` (`{"classification"|"status"|"company_exact"|"company_norm"|"position_exact"|"auto_apply_precision": [correct, total]}`).
  **"Cleared" now also requires a non-empty position** (mirrors Task 15's gate; with
  today's formula no position-less prediction can reach 0.85, so synthetic numbers are
  unchanged — Step 4 verifies).
- `evaluation.real.dataset.load_real_examples` is imported lazily (created in Task 4).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_evaluation_run_eval.py`, update the existing
`test_evaluate_computes_threshold_and_rate_metrics`: the first result becomes
`EmailExtraction(is_job_related=True, confidence=0.9, status="applied", position="SWE")`.
Then append:

```python
import pytest

from evaluation.run_eval import is_junk_company, main


class _RecordingExtractor:
    def __init__(self, result: EmailExtraction) -> None:
        self.result = result
        self.bodies: list[str] = []

    def classify_and_extract(self, *, subject, sender, date, body) -> EmailExtraction:
        self.bodies.append(body)
        return self.result


def test_real_examples_are_cleaned_by_the_production_function() -> None:
    extractor = _RecordingExtractor(EmailExtraction(is_job_related=False, confidence=0.0))
    example = {
        "subject": "s", "sender": "a@a.com", "date": "Mon, 1 Jan 2026 00:00:00 +0000",
        "raw_body": "<p>Hello <b>World</b></p>", "mime_type": "text/html",
        "expected": {"is_job_related": False},
    }
    evaluate(extractor, [example])
    assert extractor.bodies == ["Hello World"]


@pytest.mark.parametrize(
    "actual, expected, junk",
    [
        ("Us", "Kestrel IQ", True),
        ("Com", None, True),
        ("Kestrel IQ Inc", "Kestrel IQ", False),
        ("Kestrel Quinlan", "Kestrel", False),  # imperfect, not junk
        (None, "Kestrel", False),
    ],
)
def test_is_junk_company(actual, expected, junk) -> None:
    assert is_junk_company(actual, expected) is junk


def test_normalized_company_accuracy_and_junk_rate() -> None:
    examples = [
        _example({"is_job_related": True, "status": "applied", "company": "Acme"}),
        _example({"is_job_related": True, "status": "applied", "company": "Kestrel IQ"}),
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.5, status="applied", company="Acme, Inc."),
        EmailExtraction(is_job_related=True, confidence=0.5, status="applied", company="Us"),
    ]
    overall = evaluate(_StubExtractor(results), examples)["overall"]
    assert overall["company_exact_accuracy"] == 0.0
    assert overall["company_norm_accuracy"] == 0.5
    assert overall["company_junk_rate"] == 0.5


def test_a_confident_prediction_without_a_position_is_not_cleared() -> None:
    examples = [_example({"is_job_related": True, "status": "applied", "company": "Acme", "position": "SWE"})]
    results = [EmailExtraction(is_job_related=True, confidence=0.95, status="applied", company="Acme")]
    overall = evaluate(_StubExtractor(results), examples)["overall"]
    assert overall["cleared"] == 0
    assert overall["review_rate"] == 1.0
    assert overall["position_found_rate"] == 0.0
    assert overall["auto_apply_rate_positioned"] == 0.0


def test_auto_apply_precision_is_lenient_about_company_suffixes_and_position_punctuation() -> None:
    examples = [_example({"is_job_related": True, "status": "applied", "company": "Acme", "position": "Software Engineer"})]
    results = [EmailExtraction(is_job_related=True, confidence=0.9, status="applied",
                               company="Acme Inc.", position="Software Engineer.")]
    overall = evaluate(_StubExtractor(results), examples)["overall"]
    assert overall["precision_at_threshold"] == 0.0  # exact definition, unchanged
    assert overall["auto_apply_precision"] == 1.0
    assert overall["counts"]["auto_apply_precision"] == [1, 1]
    assert overall["calibration"] == {"0.9": {"n": 1, "accuracy": 1.0}}
    assert overall["status_confusion"] == {"applied->applied": 1}


def test_queue_false_positive_share() -> None:
    examples = [_example({"is_job_related": False}), _example({"is_job_related": True, "status": "applied"})]
    results = [EmailExtraction(is_job_related=True, confidence=0.4, status="applied"),
               EmailExtraction(is_job_related=True, confidence=0.4, status="applied")]
    assert evaluate(_StubExtractor(results), examples)["overall"]["queue_false_positive_share"] == 0.5


def test_misses_collects_only_wrong_predictions() -> None:
    examples = [_example({"is_job_related": True}), _example({"is_job_related": False})]
    results = [EmailExtraction(is_job_related=True, confidence=0.9),
               EmailExtraction(is_job_related=True, confidence=0.9)]
    misses: list[dict] = []
    evaluate(_StubExtractor(results), examples, misses=misses)
    assert len(misses) == 1 and misses[0]["expected"] == {"is_job_related": False}


def test_cli_refuses_the_held_out_split_without_final() -> None:
    with pytest.raises(SystemExit):
        main(["--real", "--split", "test"])
```

In `backend/tests/test_evaluation_accuracy.py`: import `synthetic_examples` from
`evaluation.run_eval` and replace every `load_dataset()` call with
`synthetic_examples(load_dataset())` (the existing bars were calibrated on synthetic
examples only; real-derived ones get their own bars in Task 11).

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_evaluation_run_eval.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_junk_company'`.

- [ ] **Step 3: Implement — replace `backend/evaluation/run_eval.py`**

```python
# backend/evaluation/run_eval.py
"""Run the classifier against a labeled dataset and report metrics: precision/recall/F1,
per-status breakdown, exact/fuzzy/normalized extraction accuracy, junk-company rate,
precision-at-threshold, auto-apply rate, calibration — overall and per `category`.

Two datasets:
- evaluation/dataset.jsonl (committed): synthetic examples plus, from Phase 11,
  pseudonymized real-derived `real_*` examples;
- the private real-mail set (Phase 11, never in the repo): `--real`, dev split by
  default. The held-out test split is scored once, at Phase 11's end, with
  `--split test --final`.

    cd backend
    uv run python -m evaluation.run_eval
    uv run python -m evaluation.run_eval --real [--misses] [--json PATH]

Local classification costs nothing — run as often as you like. evaluate() is also used
by evaluation/compare.py, evaluation/bars.py and the tests.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from app.classifier.extractor import RuleBasedExtractor
from app.gmail.google_api import clean_body
from app.pipeline.matching import normalize as normalize_company

DATASET_PATH = Path(__file__).parent / "dataset.jsonl"

# Mirrors settings.classification_confidence_threshold (app/core/config.py). Duplicated
# rather than imported: Settings() requires .env-backed fields this standalone script
# must not depend on. tests/test_evaluation_accuracy.py keeps the two in sync.
CONFIDENCE_THRESHOLD = 0.85

STATUSES = ["applied", "oa", "interview", "rejected", "offer", "other"]

# rapidfuzz token_sort_ratio threshold for "close enough to call the same extraction"
# (trailing punctuation, minor casing/spacing differences).
FUZZY_MATCH_THRESHOLD = 90

REAL_CATEGORY_PREFIX = "real_"


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def is_real_derived(example: dict) -> bool:
    return example.get("category", "").startswith(REAL_CATEGORY_PREFIX)


def synthetic_examples(examples: list[dict]) -> list[dict]:
    """The hand-written examples the Phase 4b–7 bars were calibrated on, kept apart from
    real-derived ones so adding real examples never moves those bars."""
    return [e for e in examples if not is_real_derived(e)]


def real_derived_examples(examples: list[dict]) -> list[dict]:
    return [e for e in examples if is_real_derived(e)]


def example_body(example: dict) -> str:
    """Private real examples carry the raw decoded MIME part, cleaned here by the same
    function the sync worker uses, so ingestion changes are measured too (Phase 11
    spec §3.1). Committed examples carry an already-clean `body`."""
    if "raw_body" in example:
        return clean_body(example["raw_body"], example.get("mime_type"))
    return example["body"]


def _fuzzy_match(actual: str | None, expected: str | None) -> bool:
    if actual is None or expected is None:
        return actual == expected
    return fuzz.token_sort_ratio(actual, expected) >= FUZZY_MATCH_THRESHOLD


def _company_tokens(value: str) -> set[str]:
    return {token for token in normalize_company(value).split() if len(token) >= 2}


def is_junk_company(actual: str | None, expected: str | None) -> bool:
    """An extracted company sharing no word with the labeled one — or any company when
    the email names none. That's the "Com"/"Us" class; a merely imperfect capture
    ("Kestrel Quinlan" for "Kestrel") is wrong but not junk."""
    if not actual:
        return False
    if not expected:
        return True
    return not (_company_tokens(actual) & _company_tokens(expected))


def _has_position(result) -> bool:
    return bool(result.position and result.position.strip())


def _new_bucket() -> dict:
    return {
        "n": 0,
        "tp": 0, "fp": 0, "fn": 0, "tn": 0,
        "status_tp": defaultdict(int), "status_fp": defaultdict(int), "status_fn": defaultdict(int),
        "status_total": 0, "status_correct": 0, "status_confusion": defaultdict(int),
        "company_total": 0, "company_exact": 0, "company_fuzzy": 0, "company_norm": 0, "company_junk": 0,
        "position_total": 0, "position_exact": 0, "position_fuzzy": 0,
        "position_stated": 0, "position_found": 0,
        "cleared_threshold": 0,
        "cleared_threshold_correct": 0,
        "cleared_lenient_correct": 0,
        "true_positive_total": 0,
        "true_positive_cleared": 0,
        "positioned_total": 0,
        "positioned_cleared": 0,
        "review_candidates": 0,
        "review_candidates_not_related": 0,
        "calibration": defaultdict(lambda: [0, 0]),
    }


def _score_one(bucket: dict, result, expected: dict) -> dict[str, bool]:
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

    # fully_correct: the original exact-match definition (synthetic bars use it).
    # lenient_correct: Phase 11's — company compared the way matching compares it,
    # position fuzzily — used for auto_apply_precision and calibration.
    fully_correct = lenient_correct = predicted_related == expected_related

    if expected_related:
        bucket["true_positive_total"] += 1

        expected_status = expected.get("status")
        if expected_status:
            bucket["status_total"] += 1
            status_correct = result.status == expected_status
            bucket["status_correct"] += int(status_correct)
            bucket["status_confusion"][f"{expected_status}->{result.status}"] += 1
            if status_correct:
                bucket["status_tp"][expected_status] += 1
            else:
                bucket["status_fn"][expected_status] += 1
                if result.status:
                    bucket["status_fp"][result.status] += 1
            fully_correct = fully_correct and status_correct
            lenient_correct = lenient_correct and status_correct

        if "company" in expected:
            bucket["company_total"] += 1
            expected_company = expected["company"]
            exact = result.company == expected_company
            normalized = normalize_company(result.company or "") == normalize_company(expected_company or "")
            bucket["company_exact"] += int(exact)
            bucket["company_fuzzy"] += int(exact or _fuzzy_match(result.company, expected_company))
            bucket["company_norm"] += int(normalized)
            bucket["company_junk"] += int(is_junk_company(result.company, expected_company))
            fully_correct = fully_correct and exact
            lenient_correct = lenient_correct and normalized

        if "position" in expected:
            bucket["position_total"] += 1
            expected_position = expected["position"]
            exact = result.position == expected_position
            fuzzy = exact or _fuzzy_match(result.position, expected_position)
            bucket["position_exact"] += int(exact)
            bucket["position_fuzzy"] += int(fuzzy)
            if expected_position is not None:
                bucket["position_stated"] += 1
                bucket["position_found"] += int(_has_position(result))
            fully_correct = fully_correct and exact
            lenient_correct = lenient_correct and fuzzy

    # Mirrors the pipeline: auto-applied only above the threshold AND with a position.
    cleared = predicted_related and result.confidence >= CONFIDENCE_THRESHOLD and _has_position(result)
    if cleared:
        bucket["cleared_threshold"] += 1
        bucket["cleared_threshold_correct"] += int(fully_correct)
        bucket["cleared_lenient_correct"] += int(lenient_correct)
    elif predicted_related:
        bucket["review_candidates"] += 1
        bucket["review_candidates_not_related"] += int(not expected_related)

    if predicted_related:
        band = f"{min(int(result.confidence * 10), 9) / 10:.1f}"
        bucket["calibration"][band][0] += 1
        bucket["calibration"][band][1] += int(lenient_correct)

    if expected_related and cleared:
        bucket["true_positive_cleared"] += 1
    if expected_related and expected.get("position"):
        bucket["positioned_total"] += 1
        bucket["positioned_cleared"] += int(cleared)

    return {"fully_correct": fully_correct, "lenient_correct": lenient_correct, "cleared": cleared}


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
        "status_confusion": dict(sorted(bucket["status_confusion"].items())),  # "expected->predicted": count
        "company_exact_accuracy": round(_safe_div(bucket["company_exact"], bucket["company_total"]), 3),
        "company_fuzzy_accuracy": round(_safe_div(bucket["company_fuzzy"], bucket["company_total"]), 3),
        "company_norm_accuracy": round(_safe_div(bucket["company_norm"], bucket["company_total"]), 3),
        "company_junk_rate": round(_safe_div(bucket["company_junk"], bucket["company_total"]), 3),
        "position_exact_accuracy": round(_safe_div(bucket["position_exact"], bucket["position_total"]), 3),
        "position_fuzzy_accuracy": round(_safe_div(bucket["position_fuzzy"], bucket["position_total"]), 3),
        "position_found_rate": round(_safe_div(bucket["position_found"], bucket["position_stated"]), 3),
        "cleared": bucket["cleared_threshold"],
        "precision_at_threshold": round(_safe_div(bucket["cleared_threshold_correct"], bucket["cleared_threshold"]), 3),
        "auto_apply_precision": round(_safe_div(bucket["cleared_lenient_correct"], bucket["cleared_threshold"]), 3),
        "auto_apply_rate": round(_safe_div(bucket["true_positive_cleared"], bucket["true_positive_total"]), 3),
        "auto_apply_rate_positioned": round(_safe_div(bucket["positioned_cleared"], bucket["positioned_total"]), 3),
        "review_rate": round(_safe_div(bucket["review_candidates"], bucket["true_positive_total"]), 3),
        "queue_false_positive_share": round(
            _safe_div(bucket["review_candidates_not_related"], bucket["review_candidates"]), 3
        ),
        "calibration": {
            band: {"n": n, "accuracy": round(_safe_div(ok, n), 3)}
            for band, (n, ok) in sorted(bucket["calibration"].items())
        },
        "counts": {
            "classification": [tp + tn, bucket["n"]],
            "status": [bucket["status_correct"], bucket["status_total"]],
            "company_exact": [bucket["company_exact"], bucket["company_total"]],
            "company_norm": [bucket["company_norm"], bucket["company_total"]],
            "position_exact": [bucket["position_exact"], bucket["position_total"]],
            "auto_apply_precision": [bucket["cleared_lenient_correct"], bucket["cleared_threshold"]],
        },
    }


def evaluate(extractor, examples: list[dict], *, misses: list | None = None) -> dict[str, Any]:
    """Run `extractor` over `examples`; return overall and per-category metrics as a
    plain, JSON-serializable dict. With `misses`, append one record per example the
    lenient definition gets wrong (local diagnostics — real subjects included, so
    never print these anywhere public)."""
    overall = _new_bucket()
    by_category: dict[str, dict] = defaultdict(_new_bucket)

    for example in examples:
        result = extractor.classify_and_extract(
            subject=example["subject"],
            sender=example["sender"],
            date=example["date"],
            body=example_body(example),
        )
        category = example.get("category", "uncategorized")
        outcome = _score_one(overall, result, example["expected"])
        _score_one(by_category[category], result, example["expected"])
        if misses is not None and not outcome["lenient_correct"]:
            misses.append({
                "ref": example.get("ref"),
                "category": category,
                "subject": example["subject"],
                "sender": example["sender"],
                "expected": example["expected"],
                "predicted": {
                    "is_job_related": result.is_job_related, "status": result.status,
                    "company": result.company, "position": result.position,
                    "confidence": round(result.confidence, 3),
                },
                "reasoning": result.reasoning,
            })

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
    confused = {k: v for k, v in overall["status_confusion"].items() if k.split("->")[0] != k.split("->")[1]}
    print(f"  status confusions (expected->predicted): {confused or 'none'}")
    print(f"company: exact={overall['company_exact_accuracy']} fuzzy={overall['company_fuzzy_accuracy']} "
          f"normalized={overall['company_norm_accuracy']} junk_rate={overall['company_junk_rate']}")
    print(f"position: exact={overall['position_exact_accuracy']} fuzzy={overall['position_fuzzy_accuracy']} "
          f"found_when_stated={overall['position_found_rate']}")
    print(f"cleared={overall['cleared']} precision_at_threshold={overall['precision_at_threshold']} "
          f"auto_apply_precision={overall['auto_apply_precision']}")
    print(f"auto_apply_rate={overall['auto_apply_rate']} auto_apply_rate_positioned={overall['auto_apply_rate_positioned']} "
          f"review_rate={overall['review_rate']} queue_false_positive_share={overall['queue_false_positive_share']}")
    print("calibration (band: n, accuracy): " + ", ".join(
        f"{band}: {m['n']}, {m['accuracy']}" for band, m in overall["calibration"].items()
    ))
    print()
    print("By category:")
    for category, m in sorted(report["by_category"].items()):
        print(
            f"  {category} (n={m['n']}): is_job_related_f1={m['is_job_related']['f1']} "
            f"company_norm={m['company_norm_accuracy']} position_fuzzy={m['position_fuzzy_accuracy']} "
            f"auto_apply_precision={m['auto_apply_precision']} auto_apply_rate={m['auto_apply_rate']}"
        )


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Evaluate the classifier.")
    parser.add_argument("--real", action="store_true", help="the private real-mail set (Phase 11)")
    parser.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    parser.add_argument("--final", action="store_true", help="required to score the held-out test split")
    parser.add_argument("--misses", action="store_true", help="print every wrong prediction (local only)")
    parser.add_argument("--json", type=Path, help="also write the report here")
    args = parser.parse_args(argv)

    if args.real:
        if args.split != "dev" and not args.final:
            parser.error("the held-out test split is scored once, at the end of Phase 11 — pass --final")
        from evaluation.real.dataset import load_real_examples
        from evaluation.real.privacy import ensure_outside_repo

        if args.json:
            ensure_outside_repo(args.json)
        examples = load_real_examples(args.split)
    else:
        examples = load_dataset()

    misses: list[dict] | None = [] if args.misses else None
    report = evaluate(RuleBasedExtractor(), examples, misses=misses)
    _print_report(report)
    if misses:
        print("\nMisses:")
        for miss in misses:
            print(json.dumps(miss, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Replace `backend/evaluation/compare.py`**

```python
# backend/evaluation/compare.py
"""Diff current metrics against a frozen baseline.

    cd backend
    uv run python -m evaluation.compare           # synthetic examples vs evaluation/baseline_metrics.json (Phase 6)
    uv run python -m evaluation.compare --real    # private real dev split vs <private dir>/real_baseline.json (Phase 11 CP0)
"""

import argparse
import json
from pathlib import Path
from typing import Any

from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import evaluate, load_dataset, synthetic_examples

BASELINE_PATH = Path(__file__).parent / "baseline_metrics.json"

_TRACKED_METRICS: list[tuple[str, ...]] = [
    ("is_job_related", "precision"),
    ("is_job_related", "recall"),
    ("is_job_related", "f1"),
    ("status_accuracy",),
    ("company_exact_accuracy",),
    ("company_fuzzy_accuracy",),
    ("company_norm_accuracy",),
    ("company_junk_rate",),
    ("position_exact_accuracy",),
    ("position_fuzzy_accuracy",),
    ("position_found_rate",),
    ("precision_at_threshold",),
    ("auto_apply_precision",),
    ("auto_apply_rate",),
    ("auto_apply_rate_positioned",),
    ("review_rate",),
    ("queue_false_positive_share",),
]
_LOWER_IS_BETTER = {"company_junk_rate", "review_rate", "queue_false_positive_share"}


def _get(section: dict, path: tuple[str, ...]) -> Any:
    value: Any = section
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _fmt(value: Any) -> str:
    return f"{value:>10.3f}" if isinstance(value, (int, float)) else f"{'n/a':>10}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare metrics against a frozen baseline.")
    parser.add_argument("--real", action="store_true", help="private real dev split (Phase 11)")
    args = parser.parse_args(argv)

    if args.real:
        from evaluation.real.dataset import load_real_examples
        from evaluation.real.privacy import private_dir

        baseline_path = private_dir() / "real_baseline.json"
        examples = load_real_examples("dev")
    else:
        baseline_path = BASELINE_PATH
        examples = synthetic_examples(load_dataset())

    if not baseline_path.exists():
        raise SystemExit(f"{baseline_path} does not exist yet — nothing to compare against.")
    baseline = json.loads(baseline_path.read_text())
    current = evaluate(RuleBasedExtractor(), examples)

    print(f"{'metric':<30} {'baseline':>10} {'current':>10} {'delta':>10}")
    for path in _TRACKED_METRICS:
        label = ".".join(path)
        b, c = _get(baseline["overall"], path), _get(current["overall"], path)
        if isinstance(b, (int, float)) and isinstance(c, (int, float)):
            delta = c - b
            worse = delta > 0.001 if path[-1] in _LOWER_IS_BETTER else delta < -0.001
            print(f"{label:<30} {_fmt(b)} {_fmt(c)} {delta:>+10.3f}{'  REGRESSION' if worse else ''}")
        else:
            print(f"{label:<30} {_fmt(b)} {_fmt(c)} {'':>10}")

    print()
    print(f"{'category':<24} {'is_job_related_f1':>20} {'auto_apply_rate':>18} {'precision_at_threshold':>24}")
    for category in sorted(current["by_category"]):
        cur = current["by_category"][category]
        base = baseline["by_category"].get(category, {})
        print(
            f"{category:<24} "
            f"{cur['is_job_related']['f1']:>10.3f} (was {base.get('is_job_related', {}).get('f1', 'n/a')})  "
            f"{cur['auto_apply_rate']:>8.3f} (was {base.get('auto_apply_rate', 'n/a')})  "
            f"{cur['precision_at_threshold']:>10.3f} (was {base.get('precision_at_threshold', 'n/a')})"
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run everything evaluation-related and confirm synthetic numbers are unchanged**

Run: `cd backend && uv run pytest tests/test_evaluation_run_eval.py tests/test_evaluation_accuracy.py -v && uv run python -m evaluation.compare`
Expected: all PASS; every pre-existing tracked metric shows delta `+0.000` (new metrics show `n/a` baseline).

- [ ] **Step 6: Commit**

```bash
git add backend/evaluation/run_eval.py backend/evaluation/compare.py backend/tests/test_evaluation_run_eval.py backend/tests/test_evaluation_accuracy.py
git commit -m "feat(eval): score real examples through production cleaning and add Phase 11 metrics"
```

---

### Task 4: Private dataset storage, labels and split (`evaluation/real/dataset.py`)

**Files:**
- Create: `backend/evaluation/real/dataset.py`
- Test: `backend/tests/test_eval_real_dataset.py`

**Interfaces:**
- Consumes: `private_dir()` (Task 1).
- Produces: `STATUSES`, `raw_path(ref) -> Path`, `write_raw(record: dict) -> None`,
  `load_raw(ref) -> dict`, `iter_raw() -> list[dict]`, `validate_label(label: dict) -> None`
  (raises `ValueError`), `append_label(label: dict) -> None`, `load_labels() -> dict[str, dict]`
  (last line per ref wins), `stratified_split(labels: dict[str, dict]) -> dict[str, str]`,
  `freeze_split() -> dict[str, str]`, `load_frozen_split() -> dict[str, str]`,
  `to_example(raw: dict, label: dict) -> dict`, `load_real_examples(split: str) -> list[dict]`
  (`split` ∈ `dev|test|all`), `main(argv)`.
- Raw record shape (written by Task 5): `{"ref", "origin", "selected_by", "subject", "sender",
  "date", "mime_type", "raw_body", "stored": {...}, "review": {...} | None}`.
- Label shape: `{"ref", "is_job_related", "status", "company", "position"}` or `{"ref", "skip": true}`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_eval_real_dataset.py
import json

import pytest

from evaluation.real import dataset


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(tmp_path / "eval"))


def _raw(ref: str) -> dict:
    return {
        "ref": ref, "origin": "pending_review", "selected_by": "reviewed",
        "subject": f"subject {ref}", "sender": "Kestrel <person@kestrel.com>",
        "date": "Mon, 5 Jan 2026 10:00:00 +0000", "mime_type": "text/plain",
        "raw_body": "Thank you for applying.", "stored": {}, "review": None,
    }


def _related(ref: str, status: str = "applied") -> dict:
    return {"ref": ref, "is_job_related": True, "status": status, "company": "Kestrel", "position": None}


def _unrelated(ref: str) -> dict:
    return {"ref": ref, "is_job_related": False, "status": None, "company": None, "position": None}


@pytest.mark.parametrize(
    "label",
    [
        {"is_job_related": True, "status": "applied"},  # no ref
        {"ref": "r", "is_job_related": "yes"},
        {"ref": "r", "is_job_related": True, "status": "hired"},
        {"ref": "r", "is_job_related": False, "status": "applied", "company": None, "position": None},
    ],
)
def test_validate_label_rejects_malformed_labels(label) -> None:
    with pytest.raises(ValueError):
        dataset.validate_label(label)


def test_raw_round_trip_and_labels_last_line_wins() -> None:
    dataset.write_raw(_raw("msg-a"))
    assert dataset.load_raw("msg-a")["subject"] == "subject msg-a"
    dataset.append_label(_related("msg-a"))
    dataset.append_label({**_related("msg-a"), "company": "Kestrel Labs"})
    assert dataset.load_labels()["msg-a"]["company"] == "Kestrel Labs"


def test_stratified_split_is_deterministic_and_keeps_each_bucket_on_both_sides() -> None:
    labels = {f"msg-{i}": _related(f"msg-{i}") for i in range(10)}
    labels |= {f"neg-{i}": _unrelated(f"neg-{i}") for i in range(4)}
    split = dataset.stratified_split(labels)
    assert split == dataset.stratified_split(labels)
    applied = [split[f"msg-{i}"] for i in range(10)]
    assert applied.count("dev") == 7 and applied.count("test") == 3
    negatives = [split[f"neg-{i}"] for i in range(4)]
    assert "dev" in negatives and "test" in negatives


def test_freeze_split_never_moves_an_already_frozen_example() -> None:
    for i in range(5):
        dataset.write_raw(_raw(f"msg-{i}"))
        dataset.append_label(_related(f"msg-{i}"))
    first = dataset.freeze_split()
    for i in range(5, 12):
        dataset.write_raw(_raw(f"msg-{i}"))
        dataset.append_label(_related(f"msg-{i}"))
    second = dataset.freeze_split()
    assert all(second[ref] == side for ref, side in first.items())
    assert len(second) == 12


def test_load_real_examples_builds_evaluation_examples_and_skips_skipped() -> None:
    dataset.write_raw(_raw("msg-a"))
    dataset.write_raw(_raw("msg-b"))
    dataset.append_label(_related("msg-a"))
    dataset.append_label({"ref": "msg-b", "skip": True})
    dataset.freeze_split()
    examples = dataset.load_real_examples("all")
    assert [e["ref"] for e in examples] == ["msg-a"]
    example = examples[0]
    assert example["raw_body"] == "Thank you for applying." and example["mime_type"] == "text/plain"
    assert example["expected"] == {"is_job_related": True, "status": "applied", "company": "Kestrel", "position": None}
    assert example["category"] == "real_pending_review"


def test_load_real_examples_requires_a_frozen_split() -> None:
    with pytest.raises(SystemExit):
        dataset.load_real_examples("dev")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_eval_real_dataset.py -v`
Expected: FAIL — `ImportError: cannot import name 'dataset'`.

- [ ] **Step 3: Implement**

```python
# backend/evaluation/real/dataset.py
"""Storage for the private real-mail evaluation set (Phase 11 spec §3.1): one scrubbed
JSON file per exported message, a labels file, and a frozen dev/test split — all in
private_dir(), never in the repo.

    cd backend
    uv run python -m evaluation.real.dataset --stats
    uv run python -m evaluation.real.dataset --freeze-split     # after labeling
    uv run python -m evaluation.real.dataset --list-dev         # refs to pick for pseudonymizing
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from evaluation.real.privacy import private_dir

STATUSES = ("applied", "oa", "interview", "rejected", "offer", "other")
RAW_DIR = "raw"
LABELS_FILE = "labels.jsonl"
SPLIT_FILE = "split.json"
DEV_FRACTION = 0.7


def _raw_dir() -> Path:
    path = private_dir() / RAW_DIR
    path.mkdir(exist_ok=True)
    return path


def raw_path(ref: str) -> Path:
    return _raw_dir() / f"{ref}.json"


def write_raw(record: dict) -> None:
    raw_path(record["ref"]).write_text(json.dumps(record, ensure_ascii=False, indent=1))


def load_raw(ref: str) -> dict:
    return json.loads(raw_path(ref).read_text())


def iter_raw() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(_raw_dir().glob("*.json"))]


def validate_label(label: dict) -> None:
    if not isinstance(label.get("ref"), str) or not label["ref"]:
        raise ValueError("a label needs a ref")
    if label.get("skip") is True:
        return
    if not isinstance(label.get("is_job_related"), bool):
        raise ValueError("is_job_related must be true or false")
    if label["is_job_related"]:
        if label.get("status") not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    elif any(label.get(key) is not None for key in ("status", "company", "position")):
        raise ValueError("a not-job-related label has no status, company or position")


def append_label(label: dict) -> None:
    validate_label(label)
    with (private_dir() / LABELS_FILE).open("a") as f:
        f.write(json.dumps(label, ensure_ascii=False) + "\n")


def load_labels() -> dict[str, dict]:
    path = private_dir() / LABELS_FILE
    if not path.exists():
        return {}
    labels: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            label = json.loads(line)
            labels[label["ref"]] = label  # a later line (a correction) wins
    return labels


def _hash_key(ref: str) -> str:
    return hashlib.sha256(f"phase11-split:{ref}".encode()).hexdigest()


def stratified_split(labels: dict[str, dict]) -> dict[str, str]:
    """70/30 within each (is_job_related, status) bucket, ordered by a hash of the ref —
    deterministic, and every bucket with 2+ examples lands on both sides."""
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for ref, label in labels.items():
        if not label.get("skip"):
            buckets[(label["is_job_related"], label.get("status"))].append(ref)
    split: dict[str, str] = {}
    for refs in buckets.values():
        refs.sort(key=_hash_key)
        n_dev = round(len(refs) * DEV_FRACTION)
        if len(refs) >= 2:
            n_dev = min(max(n_dev, 1), len(refs) - 1)
        for i, ref in enumerate(refs):
            split[ref] = "dev" if i < n_dev else "test"
    return split


def load_frozen_split() -> dict[str, str]:
    path = private_dir() / SPLIT_FILE
    if not path.exists():
        raise SystemExit("No frozen split yet — run `python -m evaluation.real.dataset --freeze-split` after labeling.")
    return json.loads(path.read_text())


def freeze_split() -> dict[str, str]:
    """Adds newly labeled refs to the frozen split; never moves an existing one (so a
    test example can't leak into dev because more labels arrived)."""
    path = private_dir() / SPLIT_FILE
    frozen = json.loads(path.read_text()) if path.exists() else {}
    new = {ref: label for ref, label in load_labels().items() if ref not in frozen}
    frozen.update(stratified_split(new))
    path.write_text(json.dumps(frozen, indent=1, sort_keys=True))
    return frozen


def to_example(raw: dict, label: dict) -> dict:
    expected: dict = {"is_job_related": label["is_job_related"]}
    if label["is_job_related"]:
        expected.update(status=label["status"], company=label.get("company"), position=label.get("position"))
    return {
        "ref": raw["ref"],
        "category": f"real_{raw['origin']}",
        "subject": raw["subject"],
        "sender": raw["sender"],
        "date": raw["date"],
        "raw_body": raw["raw_body"],
        "mime_type": raw["mime_type"],
        "expected": expected,
    }


def load_real_examples(split: str) -> list[dict]:
    frozen = load_frozen_split()
    examples = []
    for ref, label in sorted(load_labels().items()):
        if label.get("skip") or ref not in frozen:
            continue
        if split != "all" and frozen[ref] != split:
            continue
        examples.append(to_example(load_raw(ref), label))
    return examples


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Private real-mail evaluation set.")
    parser.add_argument("--freeze-split", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--list-dev", action="store_true", help="dev refs with label bucket and subject (local only)")
    args = parser.parse_args(argv)

    if args.freeze_split:
        frozen = freeze_split()
        print(f"Frozen split: {Counter(frozen.values())}")
    if args.stats:
        labels = load_labels()
        raws = iter_raw()
        print(f"exported={len(raws)} labeled={sum(1 for l in labels.values() if not l.get('skip'))} "
              f"skipped={sum(1 for l in labels.values() if l.get('skip'))} unlabeled={len(raws) - len(labels)}")
        buckets = Counter((l["is_job_related"], l.get("status")) for l in labels.values() if not l.get("skip"))
        for bucket, count in sorted(buckets.items(), key=str):
            print(f"  {bucket}: {count}")
    if args.list_dev:
        frozen = load_frozen_split()
        labels = load_labels()
        for ref, side in sorted(frozen.items()):
            if side == "dev":
                label = labels[ref]
                print(f"{ref}  related={label['is_job_related']} status={label.get('status')}  {load_raw(ref)['subject']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_eval_real_dataset.py tests/test_evaluation_run_eval.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/evaluation/real/dataset.py backend/tests/test_eval_real_dataset.py
git commit -m "feat(eval): store, label and split the private real-mail set"
```

---

### Task 5: Export real messages (`evaluation/real/export.py`)

**Files:**
- Create: `backend/evaluation/real/export.py`
- Test: `backend/tests/test_eval_real_export.py`

**Interfaces:**
- Consumes: `Identity`, `scrub`, `private_dir` (Task 1); `raw_path`, `write_raw` (Task 4);
  `google_api.get_message_raw`, `gmail_service.call_with_fresh_token` (Task 2); `message_ref`.
- Produces: `JOB_KEYWORDS: re.Pattern`, `select_candidates(db, user_id, *, ignored_sample: int, seed: int) -> list[tuple[ProcessedMessage, str]]`
  (row, `selected_by` ∈ `reviewed|keyword|sample`),
  `export_candidates(db, user_id, fetch: Callable[[str], tuple[dict, str | None, str]], identity: Identity, *, ignored_sample: int, seed: int) -> Counter`,
  `main(argv)`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_eval_real_export.py
import json

import pytest

from app.applications.models import Application, ApplicationStatus
from app.core.privacy import message_ref
from app.gmail.google_api import GmailAuthError, GoogleApiError
from app.pipeline.models import ProcessedMessage
from evaluation.real import dataset
from evaluation.real.export import export_candidates, select_candidates
from evaluation.real.privacy import FICTIONAL_GIVEN, Identity

IDENTITY = Identity(name="Alice", email="alice@example.com")


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(tmp_path / "eval"))


def _row(db, user, message_id: str, *, status: str, subject: str, matched=None) -> ProcessedMessage:
    row = ProcessedMessage(
        user_id=user.id, gmail_message_id=message_id, subject=subject, sender="x@kestrel.com",
        message_date="Mon, 5 Jan 2026 10:00:00 +0000", snippet="", is_job_related=status != "ignored",
        confidence=0.4, review_status=status, matched_application_id=matched,
    )
    db.add(row)
    db.commit()
    return row


def _fetch(message_id: str):
    return (
        {"subject": "Thanks for applying, Alice", "from_": "Jane Roe <jane.roe@kestrel.com>", "date": "d"},
        "text/html",
        "<p>Hi Alice, visit https://kestrel.com/x</p>",
    )


def test_selects_reviewed_rows_keyword_hits_and_a_seeded_sample(db_session, user) -> None:
    _row(db_session, user, "p1", status="pending_review", subject="Thank you for applying")
    _row(db_session, user, "i1", status="ignored", subject="Your application was received")
    for i in range(5):
        _row(db_session, user, f"n{i}", status="ignored", subject=f"Weekly deals {i}")
    picked = select_candidates(db_session, user.id, ignored_sample=2, seed=11)
    kinds = [(row.gmail_message_id, kind) for row, kind in picked]
    assert kinds[:2] == [("p1", "reviewed"), ("i1", "keyword")]
    assert [k for _, k in kinds[2:]] == ["sample", "sample"]
    assert picked == select_candidates(db_session, user.id, ignored_sample=2, seed=11)


def test_export_writes_scrubbed_records_with_review_values(db_session, user) -> None:
    app = Application(user_id=user.id, company="Kestrel", position="Data Engineer",
                      status=ApplicationStatus.applied, source="gmail")
    db_session.add(app)
    db_session.commit()
    _row(db_session, user, "RAWID-QX", status="approved", subject="s", matched=app.id)

    counts = export_candidates(db_session, user.id, _fetch, IDENTITY, ignored_sample=0, seed=11)

    assert counts == {"exported": 1}
    record = dataset.load_raw(message_ref("RAWID-QX"))
    assert "Alice" not in json.dumps(record)
    assert f"Hi {FICTIONAL_GIVEN}" in record["raw_body"]
    assert "person@kestrel.com" in record["sender"]
    assert record["review"] == {"company": "Kestrel", "position": "Data Engineer", "status": "applied"}
    assert record["origin"] == "approved" and record["selected_by"] == "reviewed"
    assert "RAWID" not in json.dumps(record)  # only the hashed ref, never the raw ID


def test_export_is_resumable_and_counts_fetch_failures(db_session, user) -> None:
    _row(db_session, user, "p1", status="pending_review", subject="s")
    _row(db_session, user, "p2", status="pending_review", subject="s")

    def flaky(message_id: str):
        if message_id == "p2":
            raise GoogleApiError("message fetch failed: HTTP 404", status_code=404)
        return _fetch(message_id)

    assert export_candidates(db_session, user.id, flaky, IDENTITY, ignored_sample=0, seed=11) == {"exported": 1, "fetch_failed": 1}
    assert export_candidates(db_session, user.id, _fetch, IDENTITY, ignored_sample=0, seed=11) == {"already_exported": 1, "exported": 1}


def test_export_stops_when_gmail_needs_reconnecting(db_session, user) -> None:
    _row(db_session, user, "p1", status="pending_review", subject="s")

    def revoked(message_id: str):
        raise GmailAuthError("token refresh failed: invalid_grant")

    with pytest.raises(SystemExit):
        export_candidates(db_session, user.id, revoked, IDENTITY, ignored_sample=0, seed=11)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_eval_real_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evaluation.real.export'`.

- [ ] **Step 3: Implement**

```python
# backend/evaluation/real/export.py
"""Export real messages for the private evaluation set (Phase 11 spec §3.1).

LOCAL ONLY: reads the local dev database and the locally connected Gmail account.
First let a local sync catch up with the mailbox (see README "Real-mail evaluation"):

    cd backend
    uv run python -m evaluation.real.export --user-email you@example.com [--extra-term "12 Elm Row"]

Writes one scrubbed JSON file per message into <private dir>/raw/ (re-runs skip files
that already exist) and <private dir>/identity.json (used by check_leaks). Prints
counts only."""

import argparse
import json
import random
import re
from collections import Counter
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application
from app.core.privacy import message_ref
from app.gmail.google_api import GmailAuthError, GoogleApiError
from app.pipeline.models import ProcessedMessage
from evaluation.real.dataset import raw_path, write_raw
from evaluation.real.privacy import Identity, private_dir, scrub

JOB_KEYWORDS = re.compile(
    r"appl(?:y|ied|ying|ication)|interview|assessment|offer|candida|next steps|"
    r"hackerrank|codesignal|recruit|hiring|position|thank you for your interest",
    re.IGNORECASE,
)
REVIEWED_STATES = ("pending_review", "approved", "rejected", "auto_applied")
DEFAULT_IGNORED_SAMPLE = 150
DEFAULT_SEED = 11

FetchRaw = Callable[[str], tuple[dict, str | None, str]]


def select_candidates(
    db: Session, user_id: UUID, *, ignored_sample: int, seed: int
) -> list[tuple[ProcessedMessage, str]]:
    reviewed = list(db.scalars(
        select(ProcessedMessage)
        .where(ProcessedMessage.user_id == user_id, ProcessedMessage.review_status.in_(REVIEWED_STATES))
        .order_by(ProcessedMessage.created_at, ProcessedMessage.gmail_message_id)
    ))
    ignored = list(db.scalars(
        select(ProcessedMessage)
        .where(ProcessedMessage.user_id == user_id, ProcessedMessage.review_status == "ignored")
        .order_by(ProcessedMessage.gmail_message_id)
    ))
    keyword_hits = [row for row in ignored if JOB_KEYWORDS.search(row.subject)]
    others = [row for row in ignored if not JOB_KEYWORDS.search(row.subject)]
    sample = random.Random(seed).sample(others, min(ignored_sample, len(others)))
    return (
        [(row, "reviewed") for row in reviewed]
        + [(row, "keyword") for row in keyword_hits]
        + [(row, "sample") for row in sample]
    )


def _review_values(db: Session, row: ProcessedMessage) -> dict | None:
    if row.review_status != "approved" or row.matched_application_id is None:
        return None
    application = db.get(Application, row.matched_application_id)
    if application is None:
        return None
    return {"company": application.company or None, "position": application.position or None,
            "status": application.status.value}


def _scrub_values(values: dict | None, identity: Identity) -> dict | None:
    if values is None:
        return None
    return {key: scrub(value, identity) if isinstance(value, str) else value for key, value in values.items()}


def export_candidates(
    db: Session, user_id: UUID, fetch: FetchRaw, identity: Identity, *, ignored_sample: int, seed: int
) -> Counter:
    counts: Counter = Counter()
    for row, selected_by in select_candidates(db, user_id, ignored_sample=ignored_sample, seed=seed):
        ref = message_ref(row.gmail_message_id)
        if raw_path(ref).exists():
            counts["already_exported"] += 1
            continue
        try:
            summary, mime_type, raw = fetch(row.gmail_message_id)
        except GmailAuthError:
            raise SystemExit("Gmail access needs reconnecting in the local app before exporting.")
        except GoogleApiError:
            counts["fetch_failed"] += 1
            continue
        write_raw({
            "ref": ref,
            "origin": row.review_status,
            "selected_by": selected_by,
            "subject": scrub(summary["subject"], identity),
            "sender": scrub(summary["from_"], identity),
            "date": summary["date"],
            "mime_type": mime_type,
            "raw_body": scrub(raw, identity),
            "stored": _scrub_values({
                "is_job_related": row.is_job_related, "confidence": row.confidence,
                "company": row.extracted_company, "position": row.extracted_position,
                "status": row.extracted_status,
            }, identity),
            "review": _scrub_values(_review_values(db, row), identity),
        })
        counts["exported"] += 1
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export real messages for the private evaluation set.")
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--extra-term", action="append", default=[],
                        help="another personal string to redact (phone, street, school); repeatable")
    parser.add_argument("--ignored-sample", type=int, default=DEFAULT_IGNORED_SAMPLE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    from app.gmail import google_api
    from app.gmail import service as gmail_service
    from app.users.models import User

    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == args.user_email)).one_or_none()
        if user is None:
            raise SystemExit("No local user with that email.")
        connection = gmail_service.get_connection(db, user.id)
        if connection is None:
            raise SystemExit("That user has no local Gmail connection — connect Gmail in the local app first.")
        extra = list(args.extra_term)
        if connection.google_email.lower() != user.email.lower():
            extra.append(connection.google_email)
        identity = Identity(name=user.name, email=user.email, extra_terms=tuple(extra))
        (private_dir() / "identity.json").write_text(json.dumps(
            {"name": identity.name, "email": identity.email, "extra_terms": list(identity.extra_terms)}
        ))

        def fetch(message_id: str):
            return gmail_service.call_with_fresh_token(
                db, connection, lambda token: google_api.get_message_raw(token, message_id)
            )

        counts = export_candidates(db, user.id, fetch, identity, ignored_sample=args.ignored_sample, seed=args.seed)
    for key, value in sorted(counts.items()):
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_eval_real_export.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/evaluation/real/export.py backend/tests/test_eval_real_export.py
git commit -m "feat(eval): export scrubbed real messages from the local dev database"
```

---

### Task 6: Labeling CLI (`evaluation/real/label.py`)

**Files:**
- Create: `backend/evaluation/real/label.py`
- Test: `backend/tests/test_eval_real_label.py`

**Interfaces:**
- Consumes: `iter_raw`, `load_labels`, `append_label`, `STATUSES` (Task 4); `clean_body` (Task 2).
- Produces: `GUIDELINES: str`, `prefill(raw: dict, extractor) -> dict`,
  `run(*, prompt=input, out=print, extractor=None) -> int` (number of labels written), `main()`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_eval_real_label.py
import pytest

from app.classifier.schemas import EmailExtraction
from evaluation.real import dataset
from evaluation.real.label import prefill, run


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(tmp_path / "eval"))


class _Fixed:
    def __init__(self, result: EmailExtraction) -> None:
        self.result = result

    def classify_and_extract(self, **kwargs) -> EmailExtraction:
        return self.result


GUESS = _Fixed(EmailExtraction(is_job_related=True, confidence=0.5, status="applied", company="Kestrel"))


def _raw(ref: str, review=None, origin="pending_review") -> dict:
    return {"ref": ref, "origin": origin, "selected_by": "reviewed", "subject": "Thanks for applying",
            "sender": "person@kestrel.com", "date": "d", "mime_type": "text/plain",
            "raw_body": "Thank you for applying to Kestrel.", "stored": {}, "review": review}


def _script(*answers: str):
    answers_iter = iter(answers)
    return lambda _prompt="": next(answers_iter)


def test_prefill_prefers_the_maintainers_own_review_decision() -> None:
    raw = _raw("msg-a", review={"company": "Kestrel Labs", "position": "Analyst", "status": "interview"}, origin="approved")
    assert prefill(raw, GUESS) == {"is_job_related": True, "status": "interview", "company": "Kestrel Labs", "position": "Analyst"}


def test_accept_not_related_skip_and_quit() -> None:
    for ref in ("msg-a", "msg-b", "msg-c", "msg-d"):
        dataset.write_raw(_raw(ref))
    written = run(prompt=_script("a", "n", "s", "q"), out=lambda *_: None, extractor=GUESS)
    labels = dataset.load_labels()
    assert written == 3
    assert labels["msg-a"] == {"ref": "msg-a", "is_job_related": True, "status": "applied", "company": "Kestrel", "position": None}
    assert labels["msg-b"]["is_job_related"] is False
    assert labels["msg-c"] == {"ref": "msg-c", "skip": True}
    assert "msg-d" not in labels


def test_edit_keeps_defaults_on_enter_and_dash_clears_a_field() -> None:
    dataset.write_raw(_raw("msg-a"))
    # e(dit): job-related [y] ⏎, status: "bogus" (rejected) then "oa", company ⏎ (keep), position "-" (none)
    run(prompt=_script("e", "", "bogus", "oa", "", "-"), out=lambda *_: None, extractor=GUESS)
    assert dataset.load_labels()["msg-a"] == {"ref": "msg-a", "is_job_related": True, "status": "oa", "company": "Kestrel", "position": None}


def test_is_resumable() -> None:
    dataset.write_raw(_raw("msg-a"))
    dataset.write_raw(_raw("msg-b"))
    run(prompt=_script("a", "q"), out=lambda *_: None, extractor=GUESS)
    assert run(prompt=_script("a"), out=lambda *_: None, extractor=GUESS) == 1
    assert set(dataset.load_labels()) == {"msg-a", "msg-b"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_eval_real_label.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evaluation.real.label'`.

- [ ] **Step 3: Implement**

```python
# backend/evaluation/real/label.py
"""Label the exported real messages (Phase 11 spec §3.1). Resumable — quit any time.

    cd backend
    uv run python -m evaluation.real.label
"""

from app.classifier.extractor import RuleBasedExtractor
from app.gmail.google_api import clean_body
from evaluation.real.dataset import STATUSES, append_label, iter_raw, load_labels

GUIDELINES = """\
Job-related = about YOUR application/candidacy with a specific employer: confirmation
  (including job-board "application sent to <Co>"), assessment invite or receipt,
  interview, rejection, offer, status update, reminder about an assessment for an
  application you submitted.
Not job-related: job alerts/recommendations, "complete/start your application"
  reminders for applications never submitted, account mail (passwords, login codes),
  community posts, newsletters, marketing, admissions.
company  = the employer as the email names it.
position = the title as stated; '-' if the email doesn't state one.
status   = applied | oa | interview | rejected | offer | other (withdrawn-type mail = other).
Keys: a=accept  n=not job-related  e=edit  s=skip  q=quit  ?=these guidelines"""

_NOT_RELATED = {"is_job_related": False, "status": None, "company": None, "position": None}


def prefill(raw: dict, extractor) -> dict:
    review = raw.get("review")
    if review:
        return {"is_job_related": True, "status": review.get("status"),
                "company": review.get("company"), "position": review.get("position")}
    result = extractor.classify_and_extract(
        subject=raw["subject"], sender=raw["sender"], date=raw["date"],
        body=clean_body(raw["raw_body"], raw["mime_type"]),
    )
    if not result.is_job_related:
        return dict(_NOT_RELATED)
    return {"is_job_related": True, "status": result.status, "company": result.company, "position": result.position}


def _show(raw: dict, suggestion: dict, out) -> None:
    body = clean_body(raw["raw_body"], raw["mime_type"])
    out("\n" + "=" * 78)
    out(f"[{raw['origin']} / {raw['selected_by']}] {raw['subject']}")
    out(f"from: {raw['sender']}   date: {raw['date']}")
    out("-" * 78)
    out(body[:1500] + (" …" if len(body) > 1500 else ""))
    out("-" * 78)
    if raw["origin"] == "rejected":
        out("(you rejected this in the review queue — not job-related, or just a bad extraction?)")
    out(f"suggested: {suggestion}")


def _ask(prompt, out, field: str, default, choices=None):
    while True:
        answer = prompt(f"  {field} [{'-' if default is None else default}]: ").strip()
        value = default if answer == "" else (None if answer == "-" else answer)
        if choices is None or value in choices:
            return value
        out(f"  must be one of: {', '.join(choices)}")


def _edit(suggestion: dict, prompt, out) -> dict:
    related = _ask(prompt, out, "job-related (y/n)", "y" if suggestion["is_job_related"] else "n", choices=("y", "n"))
    if related == "n":
        return dict(_NOT_RELATED)
    return {
        "is_job_related": True,
        "status": _ask(prompt, out, "status", suggestion["status"] or "applied", choices=STATUSES),
        "company": _ask(prompt, out, "company", suggestion["company"]),
        "position": _ask(prompt, out, "position ('-' = not stated)", suggestion["position"]),
    }


def run(*, prompt=input, out=print, extractor=None) -> int:
    extractor = extractor or RuleBasedExtractor()
    labeled = load_labels()
    pending = [raw for raw in iter_raw() if raw["ref"] not in labeled]
    out(f"{len(pending)} to label, {len(labeled)} already labeled.  ? = guidelines")
    written = 0
    for raw in pending:
        suggestion = prefill(raw, extractor)
        _show(raw, suggestion, out)
        while True:
            key = prompt("> ").strip().lower()
            if key == "q":
                return written
            if key == "?":
                out(GUIDELINES)
                continue
            if key == "a":
                label = {"ref": raw["ref"], **suggestion}
            elif key == "n":
                label = {"ref": raw["ref"], **_NOT_RELATED}
            elif key == "s":
                label = {"ref": raw["ref"], "skip": True}
            elif key == "e":
                label = {"ref": raw["ref"], **_edit(suggestion, prompt, out)}
            else:
                out("a / n / e / s / q / ?")
                continue
            try:
                append_label(label)
            except ValueError as exc:
                out(f"Not saved: {exc} — press e to edit.")
                continue
            written += 1
            break
    return written


def main() -> None:
    print(f"Labeled {run()} message(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_eval_real_label.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/evaluation/real/label.py backend/tests/test_eval_real_label.py
git commit -m "feat(eval): add a resumable labeling CLI for the private real-mail set"
```

---

### Task 7 (M): Build the private set, freeze the baseline, push CP0

Maintainer-blocking. The implementer prepares commands and waits.

- [ ] **Step 1: Full local checks, then push CP0 code**

```bash
cd backend && uv run pytest && uv run python -m evaluation.compare
cd ../frontend && npx tsc -b && npx oxlint
cd .. && git push origin main && gh run watch
```
Expected: all green; CI green.

- [ ] **Step 2 (M): Catch the local DB up with the mailbox**

```bash
cd backend && uv run alembic upgrade head
uv run uvicorn app.main:app --reload            # terminal 1
uv run python -m app.sync.worker                # terminal 2
cd ../frontend && npm run dev -- --port 5178 --strictPort   # terminal 3
```
In the local app: Reconnect Gmail if shown, click **Sync Gmail**, wait for "Synced".

- [ ] **Step 3 (M): Export, label, freeze**

```bash
cd backend
uv run python -m evaluation.real.export --user-email <your login email> [--extra-term "<phone>"] [--extra-term "<street>"]
uv run python -m evaluation.real.label          # resumable; ~400 items, mostly 'a'
uv run python -m evaluation.real.dataset --freeze-split --stats
```
Expected: `exported=` ≈ pending + reviewed + keyword hits + 150; everything labeled or skipped.

- [ ] **Step 4: Freeze the real baseline (dev split)**

```bash
uv run python -m evaluation.run_eval --real --json ~/.job-tracker-eval/real_baseline.json
```
(If `JOBTRACKER_REAL_EVAL_DIR` points elsewhere, write `real_baseline.json` there —
`compare --real` reads it from the private dir.)
Expected: a report prints; the JSON file exists outside the repo.

- [ ] **Step 5 (M): Record the baseline and confirm targets**

Append to the spec's §9 **aggregate numbers only** (n, relatedness precision/recall,
company normalized accuracy, junk rate, position found rate, auto_apply_precision,
auto_apply_rate_positioned, cleared count). The maintainer confirms the §6 targets or
revises them once, recorded in the same entry. Commit:

```bash
git add docs/superpowers/specs/2026-09-25-job-tracker-phase-11-real-mail-classifier-quality-design.md
git commit -m "docs: record the Phase 11 real-mail baseline and confirmed targets"
git push origin main
```

---

# CP1 — Ingestion parity

### Task 8: Keep line structure through cleaning; flatten after preprocessing; normalize subjects

**Files:**
- Modify: `backend/app/gmail/google_api.py`, `backend/app/classifier/preprocess.py`, `backend/app/classifier/extractor.py`
- Test: `backend/tests/test_gmail_google_api.py`, `backend/tests/test_classifier_preprocess.py`, `backend/tests/test_classifier_extractor.py`

**Interfaces:**
- Produces: `clean_body` output now keeps `\n` (lines) and `\n\n` (paragraphs);
  `preprocess_body` output is single-line, paragraph breaks turned into sentence ends;
  `preprocess.preprocess_subject(subject: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_gmail_google_api.py`:

```python
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("<p>Hi Quinlan,</p><p>Thanks for applying to Acme.</p>", "Hi Quinlan,\n\nThanks for applying to Acme."),
        ("Line one<br>Line two<br/>Line three", "Line one\nLine two\nLine three"),
        ("<p>Thank you for\n\n   applying</p>", "Thank you for applying"),  # HTML source newlines are not structure
        ("<div>A</div>\n\n\n<div>B</div>", "A\n\nB"),
    ],
)
def test_clean_body_keeps_html_line_structure(raw, expected) -> None:
    assert google_api.clean_body(raw, "text/html") == expected


def test_clean_body_keeps_plain_text_paragraphs_but_collapses_extra_blank_lines() -> None:
    assert google_api.clean_body("Hi,\n\n\n\nBody   text\nwrapped", "text/plain") == "Hi,\n\nBody text\nwrapped"


def test_clean_body_drops_invisible_characters_and_layout_only_html() -> None:
    assert google_api.clean_body("<p>Acme&nbsp;Corp​­</p>", "text/html") == "Acme Corp"
    layout = "<table><tr><td>&nbsp;</td></tr><tr><td>͏‌</td></tr></table>"
    assert google_api.clean_body(layout, "text/html") == ""
```

Append to `backend/tests/test_classifier_preprocess.py` (import `preprocess_subject` too):

```python
def test_a_paragraph_break_becomes_a_sentence_end_and_a_wrapped_line_does_not() -> None:
    body = "Thank you for applying to Acme Robotics\n\nUnfortunately we will not\nbe moving forward."
    assert preprocess_body(body) == "Thank you for applying to Acme Robotics. Unfortunately we will not be moving forward."


def test_a_stripped_greeting_between_paragraphs_leaves_one_boundary() -> None:
    body = "Your application at Solace Systems\n\nHi Avery,\n\nUnfortunately, no."
    assert preprocess_body(body) == "Your application at Solace Systems. Unfortunately, no."


def test_preprocess_subject_normalizes_punctuation_and_whitespace() -> None:
    assert preprocess_subject("  We’ve Received  Your Application — Acme ") == "We've Received Your Application - Acme"
```

Append to `backend/tests/test_classifier_extractor.py`:

```python
def test_a_curly_apostrophe_in_the_subject_still_matches_status_phrases() -> None:
    result = RuleBasedExtractor().classify_and_extract(
        subject="Great News! We’ve Received Your Application",
        sender="noreply@greenhouse.io",
        date="Mon, 5 Jan 2026 10:00:00 +0000",
        body="",
    )
    assert result.status == "applied"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py tests/test_classifier_preprocess.py tests/test_classifier_extractor.py -v`
Expected: the new tests FAIL (newlines collapsed; `preprocess_subject` missing; status `other`).

- [ ] **Step 3: Implement cleaning in `google_api.py`**

Add the regexes next to the existing ones and replace `_clean_text`:

```python
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|tr|li|ul|ol|table|tbody|thead|h[1-6]|blockquote|section|article|header|footer)\b[^>]*>",
    re.IGNORECASE,
)
# Zero-width characters, soft hyphens and the combining grapheme joiner that
# marketing HTML pads previews with.
_INVISIBLE_RE = re.compile("[­͏​-‏⁠﻿]")
_HORIZONTAL_SPACE_RE = re.compile(r"[^\S\n]+")
_EXTRA_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _clean_text(raw: str, *, is_html: bool) -> str:
    """Line structure survives (Phase 11): `br` → line break, block elements →
    paragraph break, so the classifier's line-anchored preprocessing (greeting and
    signature stripping) works on real mail the way it does in evaluation. HTML
    source newlines carry no meaning and are collapsed first."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _ON_WROTE_RE.sub("", text)
    text = _QUOTE_LINE_RE.sub("", text)
    if is_html:
        text = _HTML_STYLE_SCRIPT_RE.sub(" ", text)
        text = _WHITESPACE_RE.sub(" ", text)
        text = _HTML_BR_RE.sub("\n", text)
        text = _HTML_BLOCK_TAG_RE.sub("\n\n", text)
        text = _HTML_TAG_RE.sub(" ", text)
        text = html.unescape(text)
    text = _INVISIBLE_RE.sub("", text)
    text = _HORIZONTAL_SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _EXTRA_BLANK_LINES_RE.sub("\n\n", text).strip()
    return text[:BODY_MAX_CHARS]
```

- [ ] **Step 4: Implement flattening and subject normalization in `preprocess.py`**

```python
_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n\s*")
_LINE_BREAK_RE = re.compile(r"[ \t]*\n[ \t]*")
_SENTENCE_END = ".!?:;"
_SPACES_RE = re.compile(r"\s+")


def _flatten_paragraphs(body: str) -> str:
    """Runs last: the steps above need line structure, scoring and extraction want one
    line. A blank line ends a sentence, so a company capture can't run into the next
    paragraph; a single newline doesn't, because plain-text mail is hard-wrapped
    mid-sentence (Phase 11 spec §3.3)."""
    paragraphs: list[str] = []
    for paragraph in _PARAGRAPH_BREAK_RE.split(body):
        paragraph = _LINE_BREAK_RE.sub(" ", paragraph).strip()
        if paragraph.strip(" ."):
            paragraphs.append(paragraph)
        elif paragraphs and paragraphs[-1][-1] not in _SENTENCE_END:
            paragraphs[-1] += "."  # a stripped greeting's "." marker: keep the boundary
    closed = [p if p[-1] in _SENTENCE_END else p + "." for p in paragraphs[:-1]]
    return " ".join(closed + paragraphs[-1:])


def preprocess_subject(subject: str) -> str:
    """Subjects get the same punctuation normalization as bodies (a curly apostrophe
    in "We’ve Received Your Application" otherwise misses every status phrase)."""
    return _SPACES_RE.sub(" ", _normalize_punctuation(subject)).strip()
```

and make `preprocess_body` end with the flatten step (update its docstring's order note):

```python
def preprocess_body(body: str) -> str:
    """... then punctuation is normalized, and finally paragraphs are flattened into
    one line with paragraph breaks as sentence ends."""
    body = _strip_greeting_lines(body)
    body = _truncate_at_signature(body)
    body = _normalize_punctuation(body)
    return _flatten_paragraphs(body)
```

In `extractor.py`, import `preprocess_subject` and make the first line of
`classify_and_extract` `subject = preprocess_subject(subject)` (before `preprocess_body`).

- [ ] **Step 5: Run the classifier/Gmail suites and measure**

Run: `cd backend && uv run pytest -q && uv run python -m evaluation.compare && uv run python -m evaluation.compare --real`
Expected: pytest all PASS (synthetic bars hold); synthetic compare shows no REGRESSION;
the real compare is recorded in the commit body (improvements expected in
`company_norm_accuracy`/`company_junk_rate`; if any real metric regresses, run
`uv run python -m evaluation.run_eval --real --misses`, find the cause, fix before committing).

- [ ] **Step 6: Commit and push CP1**

```bash
git add backend/app/gmail/google_api.py backend/app/classifier/preprocess.py backend/app/classifier/extractor.py backend/tests/test_gmail_google_api.py backend/tests/test_classifier_preprocess.py backend/tests/test_classifier_extractor.py
git commit -m "fix(classifier): keep email line structure so preprocessing works on real mail

<paste the aggregate before/after numbers from compare --real here — counts and rates only>"
cd frontend && npx tsc -b && npx oxlint && cd .. && git push origin main && gh run watch
```

---

# CP2 — Sender resolution and the committed real-derived subset

### Task 9: `sender.py` with the Public Suffix List (the "Com" fix)

**Files:**
- Create: `backend/app/classifier/sender.py`
- Modify: `backend/pyproject.toml` + `backend/uv.lock` (`uv add tldextract`), `backend/app/classifier/patterns.py`, `backend/app/classifier/text.py`, `backend/app/classifier/fields.py`, `backend/app/classifier/extractor.py`, `backend/evaluation/inspect_confidence.py`
- Test: create `backend/tests/test_classifier_sender.py`; modify `backend/tests/test_classifier_text.py`, `backend/tests/test_classifier_fields.py`, `backend/tests/test_classifier_patterns.py`

**Interfaces:**
- Produces: `sender.SenderKind = Literal["employer","platform","job_board","freemail","self","unknown"]`;
  `sender.SenderInfo(address, domain, registrable_domain, kind, org_label, tenant_label, display_name)`;
  `sender.parse_sender(sender: str, *, self_address: str | None = None) -> SenderInfo`;
  `sender.plausible_label(label: str | None) -> bool`;
  `text.compact(value: str) -> str`; `text.find_text_form(target: str, text: str) -> str | None`;
  patterns: `PLATFORM_DOMAINS` (replaces `ATS_DOMAINS`), `JOB_BOARD_DOMAINS`, `FREEMAIL_DOMAINS`,
  `GENERIC_LABELS`, `GENERIC_LABEL_RE`, `DISPLAY_NAME_NOISE_WORDS`, `DISPLAY_NAME_NOISE_RE`;
  `extractor.classify(text: str, *, is_platform_sender: bool)`;
  `fields.find_company` tiers: `"template"`, `"domain"`, `"display_name"`, `"tenant"`, `"none"`.
- Removed: `text.extract_sender_domain`, `text.extract_sender_display_name`, `text._SUBDOMAIN_PREFIXES`, `patterns.ATS_DOMAINS`.

- [ ] **Step 1: Add the dependency**

Run: `cd backend && uv add tldextract`
Expected: `pyproject.toml` gains `tldextract>=5.3`; `uv.lock` updated.

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_classifier_sender.py
import importlib
import socket

import pytest

from app.classifier.fields import find_company
from app.classifier.patterns import FREEMAIL_DOMAINS, GENERIC_LABELS, JOB_BOARD_DOMAINS, PLATFORM_DOMAINS
from app.classifier.sender import parse_sender
from app.classifier.text import compact, find_text_form


@pytest.mark.parametrize(
    "sender, registrable, kind, org_label",
    [
        ('"Acme Careers" <careers@acme.com>', "acme.com", "employer", "acme"),
        ("x@recruitment.acme.com", "acme.com", "employer", "acme"),
        ("x@hr.acme.co.uk", "acme.co.uk", "employer", "acme"),
        ("x@mail.acme.jobs", "acme.jobs", "employer", "acme"),
        ("X@ACME.COM", "acme.com", "employer", "acme"),
        ("x@oraclecloud.acme.com", "acme.com", "employer", "acme"),
        ("x@us.greenhouse-mail.io", "greenhouse-mail.io", "platform", None),
        ("x@acme.wd5.myworkday.com", "myworkday.com", "platform", None),
        ("x@oraclecloud.com", "oraclecloud.com", "platform", None),
        ("x@gmail.com", "gmail.com", "freemail", None),
        ("x@mail.com", "mail.com", "freemail", None),
        ("x@linkedin.com", "linkedin.com", "job_board", None),
        ("x@jobs.com", "jobs.com", "employer", None),
        ("x@e.com", "e.com", "employer", None),
    ],
)
def test_parse_sender_resolves_the_registrable_domain(sender, registrable, kind, org_label) -> None:
    info = parse_sender(sender)
    assert (info.registrable_domain, info.kind, info.org_label) == (registrable, kind, org_label)


def test_platform_tenant_label_is_kept_only_when_plausible() -> None:
    assert parse_sender("x@acme.wd5.myworkday.com").tenant_label == "acme"
    assert parse_sender("x@wd5.myworkday.com").tenant_label is None
    assert parse_sender("x@us.greenhouse-mail.io").tenant_label is None


@pytest.mark.parametrize(
    "sender, display",
    [
        ('"do-not-reply Acme" <x@myworkday.com>', "Acme"),
        ('"Acme Recruiting Team" <x@greenhouse.io>', "Acme"),
        ('"No Reply" <x@acme.com>', None),
        ("x@acme.com", None),
    ],
)
def test_display_name_loses_system_words(sender, display) -> None:
    assert parse_sender(sender).display_name == display


def test_the_recipients_own_address_is_its_own_kind() -> None:
    assert parse_sender("Q <Q.Ellery@Example.com>", self_address="q.ellery@example.com").kind == "self"


@pytest.mark.parametrize("sender", ["", "not-an-email", "<>", "undisclosed-recipients:;", '"Only A Name"', "a@", "x@localhost", "a@b@c"])
def test_parse_sender_never_raises_on_malformed_from_headers(sender) -> None:
    info = parse_sender(sender)
    assert info.org_label is None
    assert find_company("Update.", sender) == (None, "none")


def test_public_suffix_lookup_never_touches_the_network(monkeypatch) -> None:
    def _refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    import app.classifier.sender as sender_module

    fresh = importlib.reload(sender_module)  # a new extractor: its first lookup happens under the patch
    assert fresh.parse_sender("x@hr.acme.co.uk").org_label == "acme"


_PREFIXES = ["", "mail.", "e.", "jobs.", "careers.", "us.", "hr.", "email.", "recruitment.", "noreply.", "notifications.", "talent."]
_BASES = ["com", "mail.com", "jobs.com", "e.com", "careers.com", "oraclecloud.com", "greenhouse-mail.io",
          "greenhouse.io", "myworkday.com", "gmail.com", "co.uk", "linkedin.com", "indeed.com"]
_FORBIDDEN = (
    {"com", "io", "uk", "co", "jobs", "org", "net"}
    | set(GENERIC_LABELS)
    | {domain.split(".")[0] for domain in PLATFORM_DOMAINS | JOB_BOARD_DOMAINS | FREEMAIL_DOMAINS}
)


@pytest.mark.parametrize("prefix", _PREFIXES)
@pytest.mark.parametrize("base", _BASES)
def test_a_derived_company_is_never_a_suffix_generic_label_or_platform(prefix, base) -> None:
    company, _ = find_company("Update.", f"noreply@{prefix}{base}")
    if company is not None:
        assert len(company) >= 2
        assert compact(company) not in _FORBIDDEN


@pytest.mark.parametrize(
    "target, text, form",
    [
        ("acmerobotics", "Thanks from Acme Robotics today", "Acme Robotics"),
        ("globexinc", "Interview with Globex, Inc. today", "Globex, Inc."),
        ("acme", "Thanks for applying to Acme.", "Acme"),
        ("acme", "Your ACME application", "ACME"),
        ("acme", "we use acme tools", None),
        ("", "Acme", None),
    ],
)
def test_find_text_form(target, text, form) -> None:
    assert find_text_form(target, text) == form
```

In `backend/tests/test_classifier_fields.py`:
- `test_find_company_still_treats_workday_tenant_subdomain_as_the_company`: expect tier
  `"tenant"` instead of `"domain"` (and update its comment: the tenant label is now read
  from the Public Suffix List split, not a positional rule).
- Add:

```python
def test_find_company_takes_the_casing_the_email_itself_uses() -> None:
    assert find_company("Your ACME application was received.", "x@acme.com") == ("ACME", "domain")


def test_find_company_never_uses_freemail_or_job_board_senders() -> None:
    assert find_company("Update.", '"Jane Roe" <jane.roe@gmail.com>') == (None, "none")
    assert find_company("Update.", '"Acme via Board" <x@linkedin.com>') == (None, "none")


def test_find_company_rejects_a_code_like_display_name_the_text_never_mentions() -> None:
    assert find_company("Update.", '"Workday HRXQZ" <x@myworkday.com>') == (None, "none")
```

In `backend/tests/test_classifier_text.py`: delete the six `extract_sender_domain` /
`extract_sender_display_name` tests and their imports (their cases are covered by
`test_classifier_sender.py` above).

In `backend/tests/test_classifier_patterns.py`: `ATS_DOMAINS` → `PLATFORM_DOMAINS`.

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_sender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.classifier.sender'`.

- [ ] **Step 4: Implement patterns**

In `backend/app/classifier/patterns.py`, rename `ATS_DOMAINS` → `PLATFORM_DOMAINS`, add
`"greenhouse-mail.io"` and `"oraclecloud.com"` to it (update its comment: matched
against the **registrable** domain, so every regional/tenant subdomain is covered), and
add:

```python
# Phase 11 — sender kinds that never name the hiring company themselves. All matched
# against the registrable domain (sender.parse_sender). Infrastructure, not employers.
JOB_BOARD_DOMAINS: frozenset[str] = frozenset({"linkedin.com", "indeed.com", "glassdoor.com"})
FREEMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "mail.com", "gmx.com",
})
# Labels that are email infrastructure, never an organization's name — "jobs.com"
# must not become company "Jobs", a tenant label "us" must not become "Us".
GENERIC_LABELS: frozenset[str] = frozenset({
    "mail", "email", "e", "em", "jobs", "job", "careers", "career", "talent", "recruiting",
    "recruitment", "hr", "noreply", "no-reply", "donotreply", "notifications", "notification",
    "notify", "info", "news", "us", "eu", "team", "people", "apply", "hiring", "official",
    "digital", "reply", "alerts",
})
# Regional/instance codes on platform tenants (wd5, us2, fa, ...).
GENERIC_LABEL_RE = re.compile(r"^(?:wd|us|eu|na|ap|fa|ca|uk)\d*$")
DISPLAY_NAME_NOISE_RE = re.compile(r"\b(?:do[\s-]*not[\s-]*reply|no[\s-]*reply)\b", re.IGNORECASE)
DISPLAY_NAME_NOISE_WORDS: frozenset[str] = frozenset({
    "notifications", "notification", "careers", "career", "recruiting", "recruitment", "talent",
    "acquisition", "team", "hr", "jobs", "via", "mailer", "system", "automated",
    "workday", "greenhouse", "lever", "icims", "ashby", "smartrecruiters", "jobvite", "taleo",
})
```
(add `import re` at the top of `patterns.py`).

- [ ] **Step 5: Implement `text.py` helpers**

Remove `_HEADER_ADDR_RE`, `_DISPLAY_NAME_RE`, `_SUBDOMAIN_PREFIXES`, `extract_sender_domain`,
`extract_sender_display_name`. Add:

```python
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_WORD_RE = re.compile(r"[A-Za-z0-9&][\w&'.-]*")
_CORPORATE_ABBREVIATIONS = frozenset({"inc.", "co.", "corp.", "ltd.", "llc.", "plc."})
MAX_FORM_WORDS = 5


def compact(value: str) -> str:
    """Lowercase letters and digits only: how a name looks as a domain label."""
    return _NON_ALNUM_RE.sub("", value.lower())


def find_text_form(target: str, text: str) -> str | None:
    """The text's own spelling of `target` (a compacted name such as a domain label):
    the first run of up to five words, starting with an uppercase letter or digit,
    whose compacted letters equal it — "acmerobotics" → "Acme Robotics",
    "acme" → "ACME". A trailing period is dropped unless it ends an abbreviation."""
    if not target:
        return None
    words = list(_WORD_RE.finditer(text))
    for i, first in enumerate(words):
        if not (first.group()[0].isupper() or first.group()[0].isdigit()):
            continue
        accumulated = ""
        for j in range(i, min(i + MAX_FORM_WORDS, len(words))):
            accumulated += compact(words[j].group())
            if accumulated == target:
                form = text[first.start(): words[j].end()]
                if form.endswith(".") and words[j].group().lower() not in _CORPORATE_ABBREVIATIONS:
                    form = form[:-1]
                return form
            if not target.startswith(accumulated):
                break
    return None
```

- [ ] **Step 6: Implement `sender.py`**

```python
# backend/app/classifier/sender.py
"""Who sent a message, as far as company extraction is concerned (Phase 11 spec §3.4).

Replaces text.extract_sender_domain's fixed prefix list, which turned mail.com /
jobs.com / e.com into "com" and took the FIRST DNS label of every other domain ("us"
for us.<platform>.io, "recruitment" for recruitment.<co>.com). The organization is now
the label left of the registrable domain's public suffix, per the Public Suffix List.
"""

import re
from dataclasses import dataclass
from typing import Literal

import tldextract

from app.classifier.patterns import (
    DISPLAY_NAME_NOISE_RE,
    DISPLAY_NAME_NOISE_WORDS,
    FREEMAIL_DOMAINS,
    GENERIC_LABEL_RE,
    GENERIC_LABELS,
    JOB_BOARD_DOMAINS,
    PLATFORM_DOMAINS,
)

SenderKind = Literal["employer", "platform", "job_board", "freemail", "self", "unknown"]

# suffix_list_urls=() → only the Public Suffix List snapshot bundled with the package,
# never fetched; cache_dir=None → no cache file written. No network, no filesystem side
# effects at classification time (pinned by tests/test_classifier_sender.py).
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

_ADDRESS_RE = re.compile(r"<([^<>]*)>")
_DISPLAY_NAME_RE = re.compile(r'^\s*"?([^"<]*?)"?\s*<')
_LABEL_RE = re.compile(r"^(?=[a-z0-9-]*[a-z])[a-z0-9-]{2,63}$")


@dataclass(frozen=True)
class SenderInfo:
    address: str
    domain: str
    registrable_domain: str
    kind: SenderKind
    org_label: str | None  # an employer's own label; None when it can't name one
    tenant_label: str | None  # a platform tenant's label (acme.wd5.myworkday.com → acme)
    display_name: str | None  # with system words (no-reply, careers, team, ...) removed


def plausible_label(label: str | None) -> bool:
    return bool(label) and _LABEL_RE.match(label) is not None and label not in GENERIC_LABELS \
        and GENERIC_LABEL_RE.match(label) is None


def _address(sender: str) -> str:
    match = _ADDRESS_RE.search(sender)
    candidate = (match.group(1) if match else sender).strip().lower()
    if candidate.count("@") != 1 or " " in candidate:
        return ""
    return candidate


def _display_name(sender: str) -> str | None:
    match = _DISPLAY_NAME_RE.match(sender)
    if not match:
        return None
    name = DISPLAY_NAME_NOISE_RE.sub(" ", match.group(1))
    words = [w for w in name.split() if w.lower().strip(",.:;-") not in DISPLAY_NAME_NOISE_WORDS]
    cleaned = " ".join(words).strip(" ,.:;-|")
    return cleaned if len(cleaned) >= 2 else None


def _kind(registrable: str, address: str, self_address: str | None) -> SenderKind:
    if self_address and address and address == self_address.strip().lower():
        return "self"
    if not registrable:
        return "unknown"
    if registrable in PLATFORM_DOMAINS:
        return "platform"
    if registrable in JOB_BOARD_DOMAINS:
        return "job_board"
    if registrable in FREEMAIL_DOMAINS:
        return "freemail"
    return "employer"


def parse_sender(sender: str, *, self_address: str | None = None) -> SenderInfo:
    address = _address(sender)
    domain = address.rsplit("@", 1)[1].strip(" .") if address else ""
    parts = _EXTRACT(domain) if domain else None
    registrable = f"{parts.domain}.{parts.suffix}" if parts and parts.domain and parts.suffix else ""
    kind = _kind(registrable, address, self_address)
    org_label = parts.domain if kind == "employer" and plausible_label(parts.domain) else None
    tenant_label = None
    if kind == "platform" and parts.subdomain:
        first = parts.subdomain.split(".")[0]
        tenant_label = first if plausible_label(first) else None
    return SenderInfo(
        address=address, domain=domain, registrable_domain=registrable, kind=kind,
        org_label=org_label, tenant_label=tenant_label, display_name=_display_name(sender),
    )
```

- [ ] **Step 7: Use it in `fields.py` and `extractor.py`**

In `fields.py`: import `parse_sender`, `SenderInfo` from `app.classifier.sender` and
`compact`, `find_text_form` from `app.classifier.text`; drop the `ATS_DOMAINS` and
`extract_sender_*` imports and `_ROLE_SUFFIX_WORDS`; replace `_domain_derived_company` and
`_display_name_derived_company` with:

```python
_PLAIN_NAME_WORD_RE = re.compile(r"[A-Z][a-z][\w&'-]*")


def _display_name_company(display_name: str | None, text: str) -> str | None:
    """A display name counts if the email's text mentions it (then in the text's own
    spelling), or if it is plain Title-Case words — "Acme" yes, a tenant code like
    "HRXQZ" the text never mentions, no."""
    if not display_name:
        return None
    form = find_text_form(compact(display_name), text)
    if form:
        return form
    words = display_name.split()
    if len(words) <= 4 and all(_PLAIN_NAME_WORD_RE.fullmatch(word) for word in words):
        return display_name
    return None


def _fallback_company(text: str, sender: SenderInfo) -> tuple[str | None, str]:
    if sender.kind in ("freemail", "job_board", "self"):
        return None, "none"
    if sender.org_label:
        return find_text_form(sender.org_label, text) or sender.org_label.capitalize(), "domain"
    display = _display_name_company(sender.display_name, text)
    if display:
        return display, "display_name"
    if sender.tenant_label:
        return find_text_form(sender.tenant_label, text) or sender.tenant_label.capitalize(), "tenant"
    return None, "none"
```

and make `find_company`'s tail `return _fallback_company(text, parse_sender(sender))`
(replacing the domain/display-name blocks).

In `extractor.py`: `classify(text: str, *, is_platform_sender: bool)` — replace
`if sender_domain in ATS_DOMAINS:` with `if is_platform_sender:`. In `classify_and_extract`:
`sender_info = parse_sender(sender)`, call `classify(text, is_platform_sender=sender_info.kind == "platform")`,
and `domain_bonus = DOMAIN_CONFIDENCE_BONUS if sender_info.kind == "platform" else 0.0`.
Remove the `extract_sender_domain`/`ATS_DOMAINS` imports. In `patterns.py`, add
`"tenant": 0.15` to `EXTRACTION_PENALTY` (the new tier; the whole formula is replaced in Task 16).

In `evaluation/inspect_confidence.py`: the same two substitutions (`parse_sender(sender)`,
`classify(text, is_platform_sender=...)`, `kind == "platform"` for the bonus).

- [ ] **Step 8: Run all classifier tests and measure**

Run: `cd backend && uv run pytest -q && uv run python -m evaluation.compare && uv run python -m evaluation.compare --real`
Expected: all PASS; no synthetic REGRESSION; real `company_junk_rate` down (record numbers).
If a synthetic example changes company, inspect it with
`uv run python -m evaluation.run_eval --misses` and decide per acceptance criterion #7.

- [ ] **Step 9: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/classifier backend/evaluation/inspect_confidence.py backend/tests/test_classifier_sender.py backend/tests/test_classifier_text.py backend/tests/test_classifier_fields.py backend/tests/test_classifier_patterns.py
git commit -m "fix(classifier): derive companies from the registrable domain, never a suffix or generic label

<aggregate before/after from compare --real>"
```

---

### Task 10: Pseudonymize, leak-check and bar tooling

**Files:**
- Create: `backend/evaluation/real/pseudonymize.py`, `backend/evaluation/real/check_leaks.py`, `backend/evaluation/bars.py`
- Test: `backend/tests/test_eval_real_pseudonymize.py`, `backend/tests/test_evaluation_bars.py`

**Interfaces:**
- Consumes: `parse_sender`, `find_text_form`, `compact` (Task 9); `clean_body`; dataset/privacy helpers; `DATASET_PATH`, `REAL_CATEGORY_PREFIX` (Task 3).
- Produces: `pseudonymize.Mapping` (`companies`, `labels`, `people` dicts; `.company(real)`, `.person(real)`, `.label(real_label, real_company, fake_company)`, `.load()`, `.save()`),
  `pseudonymize.pseudonymize_example(raw, label, mapping) -> dict`, `category_for(label) -> str`,
  `FIXED_DATE`, `PREVIEW_FILE`, `append_preview() -> int`;
  `check_leaks.leak_terms() -> list[str]`, `check_leaks.find_leaks(lines: list[str], terms: list[str]) -> list[tuple[int, str]]`;
  `bars.suggest_bar(correct: int, total: int) -> float | None`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_eval_real_pseudonymize.py
import json

import pytest

from evaluation.real.check_leaks import find_leaks
from evaluation.real.pseudonymize import FIXED_DATE, Mapping, category_for, pseudonymize_example


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(tmp_path / "eval"))


def _raw(sender: str, subject: str, body: str) -> dict:
    return {"ref": "msg-a", "origin": "approved", "selected_by": "reviewed", "subject": subject,
            "sender": sender, "date": "Tue, 3 Mar 2026 08:12:00 +0000", "mime_type": "text/plain",
            "raw_body": body, "stored": {}, "review": None}


LABEL = {"ref": "msg-a", "is_job_related": True, "status": "applied",
         "company": "Zentrix Robotics", "position": "Zentrix Robotics Intern"}


def test_company_domain_person_and_date_are_replaced_consistently() -> None:
    raw = _raw('"Dana Kowal" <person@zentrixrobotics.com>',
               "Thanks for applying to Zentrix Robotics!",
               "Thanks for applying to ZENTRIX ROBOTICS.\n\nDana Kowal")
    mapping = Mapping()
    example = pseudonymize_example(raw, LABEL, mapping)
    blob = json.dumps(example).lower()
    assert "zentrix" not in blob and "dana" not in blob and "kowal" not in blob
    fake = mapping.companies["Zentrix Robotics"]
    assert example["subject"] == f"Thanks for applying to {fake}!"
    assert fake.upper() in example["body"]  # all-caps stays all-caps
    assert example["sender"].endswith(f"@{mapping.labels['zentrixrobotics']}.com>")
    assert mapping.labels["zentrixrobotics"] == "".join(fake.lower().split())  # label mirrors the real relationship
    assert example["expected"] == {"is_job_related": True, "status": "applied", "company": fake, "position": f"{fake} Intern"}
    assert example["date"] == FIXED_DATE
    assert example["category"] == "real_applied"
    assert pseudonymize_example(raw, LABEL, mapping)["subject"] == example["subject"]  # stable across runs


def test_platform_domains_are_kept() -> None:
    raw = _raw("Zentrix <person@us.greenhouse-mail.io>", "Thanks for applying to Zentrix Robotics", "Body.")
    example = pseudonymize_example(raw, LABEL, Mapping())
    assert "greenhouse-mail.io" in example["sender"]


@pytest.mark.parametrize(
    "label, category",
    [
        ({"is_job_related": False}, "real_negative"),
        ({"is_job_related": True, "status": "oa"}, "real_assessment"),
        ({"is_job_related": True, "status": "offer"}, "real_interview_offer"),
        ({"is_job_related": True, "status": "rejected"}, "real_rejection"),
    ],
)
def test_category_for(label, category) -> None:
    assert category_for(label) == category


def test_find_leaks_scans_only_real_derived_lines_as_decoded_text() -> None:
    lines = [
        json.dumps({"category": "clean_template", "subject": "Zentrix"}),
        json.dumps({"category": "real_applied", "subject": "Hi Nguyễn"}),
        json.dumps({"category": "real_applied", "subject": "Hi Quinlan"}),
    ]
    assert find_leaks(lines, ["Zentrix", "Nguyễn"]) == [(2, "Nguyễn")]
```

```python
# backend/tests/test_evaluation_bars.py
import pytest

from evaluation.bars import suggest_bar


@pytest.mark.parametrize(
    "correct, total, bar",
    [
        (64, 74, 0.85),   # tolerates 63/74=0.851, fails at 62/74=0.838 — Phase 7's company bar
        (91, 91, 0.98),   # tolerates 90/91=0.989, fails at 89/91=0.978
        (1, 1, None),     # too few to set a meaningful bar
    ],
)
def test_suggest_bar_tolerates_exactly_one_more_miss(correct, total, bar) -> None:
    assert suggest_bar(correct, total) == bar
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_eval_real_pseudonymize.py tests/test_evaluation_bars.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement `evaluation/bars.py`**

```python
# backend/evaluation/bars.py
"""Suggest regression bars that tolerate exactly one more miss than today and fail at
two — the convention tests/test_evaluation_accuracy.py documents for every bar.

    cd backend
    uv run python -m evaluation.bars            # real-derived (real_*) committed examples
    uv run python -m evaluation.bars --synthetic
"""

import argparse
import math

from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import evaluate, load_dataset, real_derived_examples, synthetic_examples


def suggest_bar(correct: int, total: int) -> float | None:
    if total < 2 or correct < 2:
        return None
    tolerated = (correct - 1) / total
    failing = (correct - 2) / total
    bar = math.floor(tolerated * 100) / 100
    return bar if bar > failing else None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args(argv)
    pick = synthetic_examples if args.synthetic else real_derived_examples
    overall = evaluate(RuleBasedExtractor(), pick(load_dataset()))["overall"]
    for metric, (correct, total) in overall["counts"].items():
        print(f"{metric:<24} {correct}/{total}  suggested bar: {suggest_bar(correct, total)}")
    print(f"company_junk_rate: {overall['company_junk_rate']} (bar: must stay 0.0)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement `evaluation/real/pseudonymize.py`**

```python
# backend/evaluation/real/pseudonymize.py
"""Turn selected real DEV-split examples into pseudonymized examples for the committed
dataset (Phase 11 spec §3.2).

    cd backend
    uv run python -m evaluation.real.dataset --list-dev > ~/refs.txt   # then keep ~40-60 lines, first column
    uv run python -m evaluation.real.pseudonymize --refs-file ~/refs.txt
    # read EVERY line of <private dir>/pseudonymized_preview.jsonl; hand-edit if needed
    uv run python -m evaluation.real.pseudonymize --append
    uv run python -m evaluation.real.check_leaks

The real→fictional mapping lives in <private dir>/pseudonyms.json, so a company keeps
the same fictional name across examples and runs."""

import argparse
import json
import re
from dataclasses import asdict, dataclass, field

from app.classifier.sender import parse_sender
from app.classifier.text import compact, find_text_form
from app.gmail.google_api import clean_body
from app.pipeline.matching import normalize as normalize_company
from evaluation.real.check_leaks import find_leaks, leak_terms
from evaluation.real.dataset import load_frozen_split, load_labels, load_raw
from evaluation.real.privacy import private_dir
from evaluation.run_eval import DATASET_PATH

MAPPING_FILE = "pseudonyms.json"
PREVIEW_FILE = "pseudonymized_preview.jsonl"
FIXED_DATE = "Thu, 1 Jan 2026 09:00:00 +0000"

FICTIONAL_COMPANIES = (
    "Halvorsen Robotics", "Brightwater Labs", "Kestrel Analytics", "Tamsin Systems",
    "Orrin Aerospace", "Calloway Health", "Merrow Energy", "Quillon Software",
    "Selwyn Dynamics", "Tovey Financial", "Arden Motion", "Blakemore Foods",
    "Corvel Networks", "Dunmore Games", "Elstree Media", "Farrow Biotech",
    "Gilder Logistics", "Hensley Retail", "Ivers Semiconductor", "Juniper Lane Studios",
    "Kilbride Insurance", "Lanark Materials", "Morrow Cloud", "Norcott Devices",
    "Ossory Pharma", "Pellam Mobility", "Quarry Street Capital", "Rathmore Telecom",
    "Stanwick Security", "Thorne Valley Foods",
)
FICTIONAL_PEOPLE = (
    "Sam Lee", "Priya Natarajan", "Alex Moreno", "Chris Okafor", "Dana Whitfield",
    "Evan Marsh", "Farah Haddad", "Grace Lindqvist", "Hugo Brandt", "Ines Duarte",
    "Jonah Pike", "Kara Voss",
)
_CATEGORY_BY_STATUS = {
    "applied": "real_applied", "oa": "real_assessment", "interview": "real_interview_offer",
    "offer": "real_interview_offer", "rejected": "real_rejection", "other": "real_other",
}
_TITLE_WORD_RE = re.compile(r"[A-Z][a-z]+")


@dataclass
class Mapping:
    companies: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    people: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Mapping":
        path = private_dir() / MAPPING_FILE
        return cls(**json.loads(path.read_text())) if path.exists() else cls()

    def save(self) -> None:
        (private_dir() / MAPPING_FILE).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))

    def company(self, real: str) -> str:
        key = real.strip()
        for existing, fake in self.companies.items():
            if normalize_company(existing) == normalize_company(key):
                self.companies.setdefault(key, fake)
                return fake
        fake = next((c for c in FICTIONAL_COMPANIES if c not in self.companies.values()), None)
        if fake is None:
            raise SystemExit("Out of fictional company names — add more to FICTIONAL_COMPANIES.")
        self.companies[key] = fake
        return fake

    def person(self, real: str) -> str:
        if real not in self.people:
            fake = next((p for p in FICTIONAL_PEOPLE if p not in self.people.values()), None)
            if fake is None:
                raise SystemExit("Out of fictional people — add more to FICTIONAL_PEOPLE.")
            self.people[real] = fake
        return self.people[real]

    def label(self, real_label: str, real_company: str | None, fake_company: str) -> str:
        """A fake domain label with the same relationship to the fake name as the real
        label had to the real name (whole name compacted, or just its first word)."""
        if real_label not in self.labels:
            whole_name = real_company and compact(real_company) == real_label
            self.labels[real_label] = compact(fake_company) if whole_name else compact(fake_company.split()[0])
        return self.labels[real_label]


def category_for(label: dict) -> str:
    if not label["is_job_related"]:
        return "real_negative"
    return _CATEGORY_BY_STATUS[label["status"]]


def _replace_all(text: str, pairs: list[tuple[str, str]]) -> str:
    for real, fake in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
        if len(real.strip()) < 2:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(real)}(?!\w)", re.IGNORECASE)
        text = pattern.sub(lambda m: fake.upper() if m.group(0).isupper() else fake, text)
    return text


def _personal_name(display_name: str | None, company: str | None) -> str | None:
    if not display_name:
        return None
    words = display_name.split()
    company_words = {w.lower() for w in (company or "").split()}
    if 2 <= len(words) <= 3 and all(_TITLE_WORD_RE.fullmatch(w) for w in words) \
            and not company_words & {w.lower() for w in words}:
        return display_name
    return None


def pseudonymize_example(raw: dict, label: dict, mapping: Mapping) -> dict:
    info = parse_sender(raw["sender"])
    subject = raw["subject"]
    body = clean_body(raw["raw_body"], raw["mime_type"])
    real_company = label.get("company")
    fake_company = mapping.company(real_company) if real_company else None

    pairs: list[tuple[str, str]] = []
    if real_company:
        pairs.append((real_company, fake_company))
    domain_pairs: dict[str, str] = {}
    for real_label in filter(None, (info.org_label, info.tenant_label)):
        form = find_text_form(real_label, f"{subject}\n{body}")
        target = fake_company or mapping.company(form or real_label.capitalize())
        domain_pairs[real_label] = mapping.label(real_label, real_company, target)
        if form:
            pairs.append((form, target))
    person = _personal_name(info.display_name, real_company)
    if person:
        pairs.append((person, mapping.person(person)))
    pairs.extend(mapping.companies.items())  # another example's employer may be mentioned here
    pairs.extend(mapping.people.items())

    sender = re.sub(
        r"@([\w.-]+)",
        lambda m: "@" + ".".join(domain_pairs.get(part, part) for part in m.group(1).split(".")),
        raw["sender"],
    )
    expected: dict = {"is_job_related": label["is_job_related"]}
    if label["is_job_related"]:
        position = label.get("position")
        expected.update(
            status=label["status"],
            company=_replace_all(real_company, pairs) if real_company else None,
            position=_replace_all(position, pairs) if position else None,
        )
    return {
        "category": category_for(label),
        "subject": _replace_all(subject, pairs),
        "sender": _replace_all(sender, pairs),
        "date": FIXED_DATE,
        "body": _replace_all(body, pairs),
        "expected": expected,
    }


def append_preview() -> int:
    lines = [line for line in (private_dir() / PREVIEW_FILE).read_text().splitlines() if line.strip()]
    leaks = find_leaks(lines, leak_terms())
    if leaks:
        for number, term in leaks:
            print(f"preview line {number}: contains private term {term!r}")
        raise SystemExit(1)
    with DATASET_PATH.open("a") as f:
        for line in lines:
            f.write(json.dumps(json.loads(line), ensure_ascii=False) + "\n")
    return len(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pseudonymize real dev examples for the committed dataset.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--refs-file", type=str)
    group.add_argument("--append", action="store_true")
    args = parser.parse_args(argv)

    if args.append:
        print(f"Appended {append_preview()} example(s). Now run: uv run python -m evaluation.real.check_leaks")
        return

    with open(args.refs_file) as f:
        refs = [line.split()[0] for line in f if line.strip() and not line.startswith("#")]
    split = load_frozen_split()
    not_dev = [ref for ref in refs if split.get(ref) != "dev"]
    if not_dev:
        raise SystemExit(f"Only dev-split examples may be committed; not dev: {len(not_dev)} ref(s).")
    labels = load_labels()
    mapping = Mapping.load()
    examples = [pseudonymize_example(load_raw(ref), labels[ref], mapping) for ref in refs]
    mapping.save()
    (private_dir() / PREVIEW_FILE).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in examples))
    print(f"Wrote {len(examples)} example(s) to the preview file in the private dir. Read every line before --append.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Implement `evaluation/real/check_leaks.py`**

```python
# backend/evaluation/real/check_leaks.py
"""Fail if any committed real-derived example (category real_*) contains a private
term: a real company, domain label or person from the pseudonym mapping, or the
maintainer's own name/email/extra terms (Phase 11 spec §3.2, §5). LOCAL ONLY — it
needs the private dir.

    cd backend
    uv run python -m evaluation.real.check_leaks
"""

import json
import re
import sys

from evaluation.real.privacy import private_dir
from evaluation.run_eval import DATASET_PATH, REAL_CATEGORY_PREFIX


def leak_terms() -> list[str]:
    directory = private_dir()
    terms: set[str] = set()
    mapping_path = directory / "pseudonyms.json"
    if mapping_path.exists():
        mapping = json.loads(mapping_path.read_text())
        for section in ("companies", "labels", "people"):
            terms |= set(mapping.get(section, {}))
    identity_path = directory / "identity.json"
    if identity_path.exists():
        identity = json.loads(identity_path.read_text())
        name = identity.get("name") or ""
        email = identity.get("email") or ""
        terms |= {name, email, email.split("@")[0], *name.split(), *identity.get("extra_terms", [])}
    return sorted(term for term in terms if len(term.strip()) >= 2)


def find_leaks(lines: list[str], terms: list[str]) -> list[tuple[int, str]]:
    patterns = [(term, re.compile(rf"(?<![\w]){re.escape(term.lower())}(?![\w])")) for term in terms]
    leaks: list[tuple[int, str]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not record.get("category", "").startswith(REAL_CATEGORY_PREFIX):
            continue
        text = json.dumps(record, ensure_ascii=False).lower()  # decoded, so non-ASCII names match
        leaks.extend((number, term) for term, pattern in patterns if pattern.search(text))
    return leaks


def main() -> None:
    lines = DATASET_PATH.read_text().splitlines()
    leaks = find_leaks(lines, leak_terms())
    for number, term in leaks:
        print(f"dataset.jsonl line {number}: contains private term {term!r}")
    real = sum(1 for line in lines if line.strip() and json.loads(line).get("category", "").startswith(REAL_CATEGORY_PREFIX))
    print(f"{'LEAKS FOUND' if leaks else 'No leaks'} in {real} real-derived example(s).")
    sys.exit(1 if leaks else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run to verify they pass; confirm fictional names are unused**

Run: `cd backend && uv run pytest tests/test_eval_real_pseudonymize.py tests/test_evaluation_bars.py -v`
Expected: all PASS.
Run: `cd backend && grep -ciE "halvorsen|brightwater|kestrel|tamsin|orrin|calloway|merrow|quillon|selwyn|tovey|quinlan|ellery" evaluation/dataset.jsonl`
Expected: `0` (fictional names must not collide with existing synthetic examples; if not 0, replace the colliding names in `FICTIONAL_COMPANIES`).

- [ ] **Step 7: Commit**

```bash
git add backend/evaluation/real/pseudonymize.py backend/evaluation/real/check_leaks.py backend/evaluation/bars.py backend/tests/test_eval_real_pseudonymize.py backend/tests/test_evaluation_bars.py
git commit -m "feat(eval): pseudonymize real dev examples, leak-check them, suggest bars"
```

---

### Task 11 (M): Commit the pseudonymized subset with bars; push CP2

- [ ] **Step 1 (M): Pick, pseudonymize, review, append**

```bash
cd backend
uv run python -m evaluation.real.dataset --list-dev > ~/refs.txt
# keep ~40-60 lines covering: platform confirmations, direct confirmations, assessments,
# rejections, interviews, and ~10 negatives (account mail, digests, alerts, newsletters)
uv run python -m evaluation.real.pseudonymize --refs-file ~/refs.txt
# maintainer reads every line of ~/.job-tracker-eval/pseudonymized_preview.jsonl
uv run python -m evaluation.real.pseudonymize --append
uv run python -m evaluation.real.check_leaks
git diff backend/evaluation/dataset.jsonl     # maintainer reviews the diff by eye
```
Expected: `No leaks in N real-derived example(s).`

- [ ] **Step 2: Measure and write the real-derived bars**

Run: `cd backend && uv run python -m evaluation.bars`
Append to `backend/tests/test_evaluation_accuracy.py` (fill the constants from the
`suggested bar` column; a `None` suggestion means leave that metric unasserted and say so
in the comment):

```python
from evaluation.run_eval import real_derived_examples

# Phase 11 — bars for the pseudonymized real-derived examples (categories real_*),
# set from `uv run python -m evaluation.bars` on <date> with the same rule as above:
# tolerate exactly one more miss, fail at two. Raised (never lowered) as later
# Phase 11 checkpoints improve them.
MIN_REAL_CLASSIFICATION_ACCURACY = ...  # currently k/n
MIN_REAL_STATUS_ACCURACY = ...          # currently k/n
MIN_REAL_COMPANY_NORM_ACCURACY = ...    # currently k/n
MAX_REAL_COMPANY_JUNK_RATE = 0.0
MIN_REAL_AUTO_APPLY_PRECISION = 0.96    # asserted only once anything clears


def test_real_derived_examples_meet_minimum_bar() -> None:
    report = evaluate(RuleBasedExtractor(), real_derived_examples(load_dataset()))["overall"]
    assert report["n"] > 0, "no real_* examples in evaluation/dataset.jsonl"
    assert report["classification_accuracy"] >= MIN_REAL_CLASSIFICATION_ACCURACY
    assert report["status_accuracy"] >= MIN_REAL_STATUS_ACCURACY
    assert report["company_norm_accuracy"] >= MIN_REAL_COMPANY_NORM_ACCURACY
    assert report["company_junk_rate"] <= MAX_REAL_COMPANY_JUNK_RATE
    if report["cleared"]:
        assert report["auto_apply_precision"] >= MIN_REAL_AUTO_APPLY_PRECISION
```

If `company_junk_rate` is not 0.0 on the committed subset, do **not** loosen the bar:
find the example with `uv run python -m evaluation.run_eval --misses`, fix it in Task 9's
mechanism, and re-run.

- [ ] **Step 3: Full checks, commit, push CP2**

```bash
cd backend && uv run pytest && uv run python -m evaluation.compare && uv run python -m evaluation.real.check_leaks
cd ../frontend && npx tsc -b && npx oxlint && cd ..
git add backend/evaluation/dataset.jsonl backend/tests/test_evaluation_accuracy.py
git commit -m "test(eval): add pseudonymized real-derived examples with regression bars"
git push origin main && gh run watch
```

---

# CP3 — Extraction

### Task 12: The extractor receives the recipient (`Recipient`)

**Files:**
- Modify: `backend/app/classifier/schemas.py`, `backend/app/classifier/extractor.py`, `backend/app/classifier/fields.py`, `backend/app/pipeline/service.py`, `backend/evaluation/run_eval.py`, `backend/evaluation/real/label.py`
- Test: `backend/tests/test_pipeline_service.py`, `backend/tests/test_classifier_extractor.py`, `backend/tests/test_sync_worker.py`, `backend/tests/test_sync_failure_injection.py`, `backend/tests/test_evaluation_run_eval.py`, `backend/tests/test_eval_real_label.py`

**Interfaces:**
- Produces: `schemas.Recipient(name: str | None, email: str | None)` (frozen dataclass),
  `schemas.NO_RECIPIENT`; `Extractor.classify_and_extract(*, subject, sender, date, body, recipient: Recipient) -> EmailExtraction`
  (`recipient` **required**); `fields.find_company(text, sender, *, subject: str = "", recipient: Recipient = NO_RECIPIENT)`
  (this task: only forwards `recipient.email` as `self_address`); `run_eval.EVAL_RECIPIENT`.
- `process_message` signature unchanged: it loads the `User` by `user_id` and builds the `Recipient`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_pipeline_service.py`:

```python
class _RecordingExtractor:
    def __init__(self) -> None:
        self.recipients = []

    def classify_and_extract(self, *, subject, sender, date, body, recipient):
        self.recipients.append(recipient)
        return EmailExtraction(is_job_related=False, confidence=0.0)


def test_process_message_passes_the_users_identity_to_the_extractor(db_session, user) -> None:
    extractor = _RecordingExtractor()
    service.process_message(db_session, user.id, extractor, sync_job_id=None, message_id="m1", summary=_summary(), body="b")
    assert extractor.recipients == [Recipient(name="Alice", email="alice@example.com")]
```
(import `Recipient` from `app.classifier.schemas` in both test files).

Append to `backend/tests/test_classifier_fields.py`:

```python
def test_find_company_never_uses_the_recipients_own_address() -> None:
    me = Recipient(name="Quinlan Ellery", email="quinlan.ellery@example.com")
    assert find_company("Update.", "Quinlan <Quinlan.Ellery@example.com>", recipient=me) == (None, "none")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_pipeline_service.py tests/test_classifier_fields.py -v -k "identity or own_address"`
Expected: FAIL — `ImportError: cannot import name 'Recipient'`.

- [ ] **Step 3: Implement**

`backend/app/classifier/schemas.py` — add:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Recipient:
    """Whose mailbox is being classified (Phase 11 spec §3.5): the user's own name
    bleeds into captures after greetings, and their own address is never a company."""

    name: str | None
    email: str | None


NO_RECIPIENT = Recipient(name=None, email=None)
```

`extractor.py`: `Extractor.classify_and_extract` and `RuleBasedExtractor.classify_and_extract`
gain `recipient: Recipient` as the last keyword-only parameter (no default).
`sender_info = parse_sender(sender, self_address=recipient.email)`, and
`find_company(raw_text, sender, subject=subject, recipient=recipient)`.

`fields.py`: `def find_company(text: str, sender: str, *, subject: str = "", recipient: Recipient = NO_RECIPIENT)`
and its tail becomes `return _fallback_company(text, parse_sender(sender, self_address=recipient.email))`
(`subject` is used in Task 13).

`pipeline/service.py`: import `Recipient`, `NO_RECIPIENT` and `User`; at the top of
`process_message`:

```python
    user = db.get(User, user_id)
    recipient = Recipient(name=user.name, email=user.email) if user is not None else NO_RECIPIENT
```
and pass `recipient=recipient` to `classify_and_extract`.

`evaluation/run_eval.py`: add

```python
from app.classifier.schemas import Recipient
from evaluation.real.privacy import FICTIONAL_EMAIL, FICTIONAL_NAME

# Real-derived text has the maintainer replaced by this fictional candidate (Phase 11),
# so the classifier is evaluated as if it were their mailbox.
EVAL_RECIPIENT = Recipient(name=FICTIONAL_NAME, email=FICTIONAL_EMAIL)
```
and pass `recipient=EVAL_RECIPIENT` in `evaluate()`. In `evaluation/real/label.py`'s
`prefill`, pass `recipient=EVAL_RECIPIENT` (import it from `evaluation.run_eval`).

Tests — add a `recipient` keyword parameter to every fake extractor:
`_SingleResultExtractor` (`test_pipeline_service.py`), `_FakeExtractor` (`test_sync_worker.py`),
the fake in `test_sync_failure_injection.py`, `_StubExtractor` and `_RecordingExtractor`
(`test_evaluation_run_eval.py`); `_Fixed` in `test_eval_real_label.py` already takes `**kwargs`.
Add `recipient=NO_RECIPIENT` (imported from `app.classifier.schemas`) to all eight
`classify_and_extract(...)` calls in `test_classifier_extractor.py`, and change its monkeypatched
`lambda text, sender: (oversized, "template")` to `lambda text, sender, **kwargs: (oversized, "template")`.
Find them with: `grep -rn "def classify_and_extract\|classify_and_extract(" backend/tests backend/evaluation`.

- [ ] **Step 4: Run the full suite**

Run: `cd backend && uv run pytest -q && uv run python -m evaluation.compare`
Expected: all PASS, no REGRESSION (no behavior change yet except self-address senders).

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/evaluation backend/tests
git commit -m "refactor(classifier): pass the recipient's identity into the extractor"
```

---

### Task 13: Subject-line templates, name-bleed trimming, company cleanup

**Files:**
- Modify: `backend/app/classifier/fields.py`, `backend/app/classifier/patterns.py`, `backend/app/classifier/extractor.py`
- Test: `backend/tests/test_classifier_fields.py`

**Interfaces:**
- Consumes: `Recipient`, `NO_RECIPIENT` (Task 12); `parse_sender`, `compact`, `find_text_form` (Task 9).
- Produces: `find_company(text, sender, *, subject="", recipient=NO_RECIPIENT) -> tuple[str | None, str]`
  (new tier `"subject"`); `find_position(text, sender, *, subject="") -> tuple[str | None, str]`
  (new tier `"subject"`); `patterns.POSITION_ROLE_WORDS: frozenset[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_classifier_fields.py` (imports: `pytest`, `Recipient`, `NO_RECIPIENT`):

```python
PLATFORM = "noreply@greenhouse.io"


@pytest.mark.parametrize(
    "subject, company",
    [
        ("Thanks for applying to Halvorsen Robotics!", "Halvorsen Robotics"),
        ("\U0001F44B Thank you for applying to Brightwater!", "Brightwater"),
        ("Thank you for your application to Kestrel IQ (Formerly Kestrel Labs)", "Kestrel IQ"),
        ("Your Tamsin Application: Next Steps", "Tamsin"),
        ("[Update] Your Orrin Job Application", "Orrin"),
        ("Update on your Calloway application", "Calloway"),
        ("Interview with Merrow Energy, Inc.", "Merrow Energy"),
        ("Quillon Careers: Application for Summer 2027 Intern - Software Engineering", "Quillon"),
        ("Selwyn Application: 0000-00000 - 2027 Software Engineer Intern/Co-op", "Selwyn"),
        ("Quinlan, your application was sent to Arden", "Arden"),
        ("Your Application to Blakemore", "Blakemore"),
        ("Thank you for your interest in CORVEL", "CORVEL"),
        ("Thank You For Applying at Dunmore", "Dunmore"),
    ],
)
def test_find_company_reads_the_subject_line(subject, company) -> None:
    assert find_company("", PLATFORM, subject=subject) == (company, "subject")


@pytest.mark.parametrize(
    "subject",
    ["Great News! We've Received Your Application", "Action Required: Assessments for completion", "Your Job Application"],
)
def test_find_company_ignores_subjects_without_a_company(subject) -> None:
    assert find_company("", PLATFORM, subject=subject) == (None, "none")


@pytest.mark.parametrize(
    "subject, position",
    [
        ("Quillon Careers: Application for Summer 2027 Intern - Software Engineering", "Summer 2027 Intern - Software Engineering"),
        ("Thank you for applying to Dunmore - Software Engineering Intern (Spring 2027)", "Software Engineering Intern (Spring 2027)"),
        ("Selwyn Application: 0000-00000 - 2027 Software Engineer Intern/Co-op", "2027 Software Engineer Intern/Co-op"),
        ("You have successfully submitted your Elstree job application - Software Developer", "Software Developer"),
        ("Your recent job application for Tech Intern | 2027 Summer Internship Program - 400000", "Tech Intern | 2027 Summer Internship Program"),
        ("Great News! We've Received Your Application for the Summer 2027 Intern - Software Engineer", "Summer 2027 Intern - Software Engineer"),
        ("Thank you for your application for JR0000000000 Farrow Summer 2027 Data Analyst Intern", "Farrow Summer 2027 Data Analyst Intern"),
    ],
)
def test_find_position_reads_the_subject_line(subject, position) -> None:
    assert find_position("", PLATFORM, subject=subject) == (position, "subject")


def test_find_position_needs_a_role_word_in_a_subject_capture() -> None:
    assert find_position("", PLATFORM, subject="Your Tamsin Application: Next Steps") == (None, "none")


def test_find_company_trims_the_recipients_name_after_a_company() -> None:
    me = Recipient(name="Quinlan Ellery", email=None)
    text = "Thank you for applying to Halvorsen Quinlan Ellery, we will review it."
    assert find_company(text, "x@halvorsen.com", recipient=me) == ("Halvorsen", "template")


@pytest.mark.parametrize(
    "name, text, company",
    [
        ("Quinlan", "Thank you for applying to Halvorsen Quinlan, we will be in touch.", "Halvorsen"),
        ("", "Thank you for applying to Halvorsen Robotics, thanks.", "Halvorsen Robotics"),
        (None, "Thank you for applying to Halvorsen Robotics, thanks.", "Halvorsen Robotics"),
        ("Nguyễn Văn An", "Thank you for applying to Halvorsen Nguyễn, thanks.", "Halvorsen"),
        ("Anne-Marie Lee", "Thank you for applying to Halvorsen Anne-Marie, thanks.", "Halvorsen"),
        ("Q Ellery", "Thank you for applying to Halvorsen Ellery, thanks.", "Halvorsen"),  # one-letter token 'Q' ignored
        ("Halvorsen Quinlan", "Thank you for applying to Halvorsen Robotics, thanks.", "Halvorsen Robotics"),  # never trims the first word
    ],
)
def test_recipient_name_trim_handles_unusual_names(name, text, company) -> None:
    assert find_company(text, "x@halvorsen.com", recipient=Recipient(name=name, email=None))[0] == company
```

(`_COMPANY_TOKEN`'s first letter is ASCII `[A-Z]` but the rest is `\w`, so "Nguyễn" is
captured and then trimmed as a name token — the test pins no crash and no empty company.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_fields.py -v`
Expected: the new subject/name tests FAIL.

- [ ] **Step 3: Implement**

`patterns.py` — add:

```python
# Phase 11 — a subject-line position capture must contain one of these, so "Next Steps"
# or "Status Update" after "Application:" is never taken for a job title.
POSITION_ROLE_WORDS: frozenset[str] = frozenset({
    "intern", "internship", "engineer", "engineering", "developer", "development", "analyst",
    "scientist", "science", "manager", "designer", "associate", "coop", "co", "program",
    "specialist", "consultant", "researcher", "research", "technician", "architect", "lead",
    "administrator", "coordinator", "representative", "assistant", "officer", "director",
    "trainee", "apprentice", "fellow", "fellowship", "programmer", "tester", "sde", "swe",
})
```

`fields.py` — keep every existing template and comment; change `_COMPANY_BOUNDARY` to
stop at `(` too:

```python
_COMPANY_BOUNDARY = r"(?=\s*[.,!(]|\s+(?:for|and|regarding|about|which|who)\b|\s*$)"
```

and add, after `_COMPANY_PLEASED_TO_OFFER_RE`:

```python
# Phase 11 — subject-line templates, matched against the SUBJECT ALONE. Real
# confirmations state the company and (above all) the position in the subject, in
# shapes the body templates never see; and a subject has a natural end, so a capture
# can't run into the body the way combine_subject_body's single-space join allows.
_SUBJECT_COMPANY_END = r"(?=\s*(?:[!.,:;(|]|\s[-–]\s|$))"
_SUBJECT_COMPANY_RES = (
    re.compile(
        rf"\b(?:thanks?|thank you) for (?:applying|your application|your interest)(?: (?:to|at|in|with))? "
        rf"(?P<company>{_COMPANY_TOKEN}){_SUBJECT_COMPANY_END}",
        re.IGNORECASE,
    ),
    re.compile(rf"\bapplication (?:was )?sent to (?P<company>{_COMPANY_TOKEN}){_SUBJECT_COMPANY_END}", re.IGNORECASE),
    re.compile(rf"\binterview with (?P<company>{_COMPANY_TOKEN}){_SUBJECT_COMPANY_END}", re.IGNORECASE),
    re.compile(rf"\byour (?P<company>{_COMPANY_TOKEN}) (?:job )?application\b", re.IGNORECASE),
    re.compile(rf"^\W*(?P<company>{_COMPANY_TOKEN})\s*[:|\-–]?\s*(?:your )?(?:job )?application\b", re.IGNORECASE),
    re.compile(rf"\b(?:applying|application) to (?P<company>{_COMPANY_TOKEN}){_SUBJECT_COMPANY_END}", re.IGNORECASE),
)
# Anything to the end of the subject, starting with a capital, digit or "[".
_SUBJECT_POSITION = r"(?-i:[A-Z0-9\[])[^\n]{0,150}?"
_SUBJECT_POSITION_RES = (
    re.compile(rf"\bapplication for (?:the )?(?P<position>{_SUBJECT_POSITION})\s*$", re.IGNORECASE),
    re.compile(rf"\bapplying to {_COMPANY_TOKEN}\s+[-–|:]\s+(?P<position>{_SUBJECT_POSITION})\s*$", re.IGNORECASE),
    re.compile(rf"\bapplication\s*[-–|:]\s*(?P<position>{_SUBJECT_POSITION})\s*$", re.IGNORECASE),
)
# Requisition IDs around a subject title ("0000-00000 - Title", "Title - 400000").
_LEADING_REQ_ID_RE = re.compile(r"^[A-Z]{0,4}\d[\d-]{4,}\s*(?:[-–|:]\s*)?")
_TRAILING_REQ_ID_RE = re.compile(r"\s*[-–|:]\s*[A-Z]{0,4}\d[\d-]{4,}\s*$")
_WORD_SPLIT_RE = re.compile(r"[^\w]+")
MAX_SUBJECT_POSITION_WORDS = 14

# Words that end a company capture without being part of the name ("Orrin Job
# Application" → "Orrin", "Quillon Careers:" → "Quillon"), and captures that are not a
# company at all ("Your Application" → "Your").
_TRAILING_NOISE_WORDS = frozenset({
    "careers", "career", "jobs", "job", "application", "applications", "recruiting",
    "recruitment", "talent", "team", "hr",
})
_NOT_A_COMPANY_WORDS = frozenset({
    "your", "our", "the", "we", "you", "re", "fwd", "fw", "update", "reminder", "important",
    "thank", "thanks", "great", "news", "action", "required", "welcome", "hello", "hi", "dear",
})
```

Replace the helpers section's company cleanup with (keep `_trim_trailing_greeting` and
`_dedupe_repeated_span` as they are):

```python
def _name_tokens(name: str | None) -> set[str]:
    return {token.lower() for token in re.split(r"\s+", name or "") if len(token) >= 2}


def _trim_recipient_name(span: str, recipient_name: str | None) -> str:
    """Cuts a capture at the first word (after the first) that belongs to the
    recipient's own name — "<Company> Quinlan Ellery" after a greeting-less line join.
    The first word is never cut, so a company can share a word with the user's name."""
    tokens = _name_tokens(recipient_name)
    if not tokens:
        return span
    words = span.split()
    for i, word in enumerate(words):
        if i > 0 and word.lower().strip(",.!") in tokens:
            return " ".join(words[:i])
    return span


def _trim_trailing_noise(span: str) -> str:
    words = span.split()
    while words and words[-1].lower().strip(",.:;") in _TRAILING_NOISE_WORDS:
        words.pop()
    return " ".join(words)


def _clean_company(span: str, recipient: Recipient) -> str | None:
    span = span.strip(" .,")
    span = _trim_trailing_greeting(span)
    span = _trim_recipient_name(span, recipient.name)
    span = _dedupe_repeated_span(span)
    span = _trim_trailing_noise(span).strip(" .,:;-")
    if not span or all(word.lower().strip(",.!:") in _NOT_A_COMPANY_WORDS for word in span.split()):
        return None
    return span


def _clean_subject_position(span: str) -> str | None:
    span = span.strip(" .,!")
    span = _LEADING_REQ_ID_RE.sub("", span)
    span = _TRAILING_REQ_ID_RE.sub("", span)
    span = span.strip(" .,!-–|:")
    words = span.split()
    if not words or len(words) > MAX_SUBJECT_POSITION_WORDS:
        return None
    if not POSITION_ROLE_WORDS & set(_WORD_SPLIT_RE.split(span.lower())):
        return None
    return span
```

Replace `find_company` and `find_position`:

```python
def find_company(
    text: str, sender: str, *, subject: str = "", recipient: Recipient = NO_RECIPIENT
) -> tuple[str | None, str]:
    for pattern in _SUBJECT_COMPANY_RES:
        match = pattern.search(subject)
        if match:
            company = _clean_company(match.group("company"), recipient)
            if company:
                return company, "subject"

    for pattern in (_POSITION_AT_COMPANY_RE, _APPLICATION_TO_COMPANY_RE, _COMPANY_PLEASED_TO_OFFER_RE):
        match = pattern.search(text)
        if match:
            company = _clean_company(match.group("company"), recipient)
            if company:
                return company, "template"

    return _fallback_company(text, parse_sender(sender, self_address=recipient.email))


# `sender` is intentionally unused here — kept only for signature symmetry
# with find_company (which does use it), not a bug.
def find_position(text: str, sender: str, *, subject: str = "") -> tuple[str | None, str]:
    match = _POSITION_AT_COMPANY_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    for pattern in _SUBJECT_POSITION_RES:
        match = pattern.search(subject)
        if match:
            position = _clean_subject_position(match.group("position"))
            if position:
                return position, "subject"

    for pattern in (_POSITION_ROLE_RE, _POSITION_AS_NEW_RE):
        match = pattern.search(text)
        if match:
            return match.group("position").strip(" .,"), "template"

    return None, "none"
```

`extractor.py`: `find_position(raw_text, sender, subject=subject)`; add
`"subject": 0.0` to `EXTRACTION_PENALTY` (the formula is replaced in Task 16; this keeps
it working meanwhile).

- [ ] **Step 4: Run and measure**

Run: `cd backend && uv run pytest -q && uv run python -m evaluation.compare && uv run python -m evaluation.compare --real`
Expected: all PASS; no synthetic REGRESSION; real `position_found_rate` and
`company_norm_accuracy` up. Raise any real-derived bar in `test_evaluation_accuracy.py`
that `uv run python -m evaluation.bars` now suggests higher.

- [ ] **Step 5: Commit and push CP3**

```bash
git add backend/app/classifier backend/tests/test_classifier_fields.py backend/tests/test_evaluation_accuracy.py
git commit -m "feat(classifier): read company and position from subject lines; trim the recipient's name

<aggregate before/after from compare --real>"
cd frontend && npx tsc -b && npx oxlint && cd .. && git push origin main && gh run watch
```

---

# CP4 — Relatedness and status

### Task 14: Pattern coverage from real dev misses

**Files:**
- Modify: `backend/app/classifier/patterns.py`
- Test: `backend/tests/test_classifier_patterns.py`

**Interfaces:**
- Consumes/Produces: `STATUS_PATTERNS`, `NEGATIVE_PATTERNS` (same shapes as today).

Rule for every pattern added here: it comes from a real dev-split miss, is backed by a
test using **fictional** text, and names no employer.

- [ ] **Step 1: Write the failing tests (the evidenced shapes from spec §2.4)**

Append to `backend/tests/test_classifier_patterns.py` (it imports `JOB_RELATED_THRESHOLD`;
add `from app.classifier.extractor import classify`):

```python
def _score(text: str, platform: bool = False):
    return classify(text.lower(), is_platform_sender=platform)


@pytest.mark.parametrize(
    "text, status",
    [
        ("Thanks for applying to Halvorsen", "applied"),
        ("Thank you for your application! We will review it.", "applied"),
        ("Quinlan, your application was sent to Arden", "applied"),
        ("You have successfully submitted your application", "applied"),
        ("You're invited! Assessment for Software Engineer Intern", "oa"),
        ("Assessment completed: Kestrel General Coding Assessment", "oa"),
        ("Reminder: please complete your assessment for the Data Analyst position", "oa"),
    ],
)
def test_real_confirmation_and_assessment_phrasings_are_job_related(text, status) -> None:
    scores, job_signal, negative = _score(text)
    assert job_signal - negative >= JOB_RELATED_THRESHOLD
    assert max(scores, key=scores.get) == status


@pytest.mark.parametrize(
    "text",
    [
        "HackerRank password reset instructions. Reset your password here.",
        "Your login code to the assessment portal is 000000",
        "Your guest account information for the assessment platform",
        "Workday Inbox - Your Daily Digest: 3 new tasks",
        "You have 7 new invitations",
    ],
)
def test_account_and_digest_mail_is_not_job_related(text) -> None:
    _, job_signal, negative = _score(text, platform=True)
    assert job_signal - negative < JOB_RELATED_THRESHOLD
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_patterns.py -v`
Expected: the new cases FAIL.

- [ ] **Step 3: Implement the evidenced patterns**

In `STATUS_PATTERNS["applied"]`: replace `(r"thank you for applying", 3)` with
`(r"thank(?:s| you) for applying", 3)` and add:

```python
        (r"thank you for your application", 3),
        (r"application was sent to", 3),
        (r"successfully submitted", 2),
        (r"thank you for your interest in", 2),
```
In `STATUS_PATTERNS["oa"]` add:

```python
        (r"invit\w*[^.]{0,40}\bassessments?\b", 3),
        (r"assessments? (?:completed|invitation|expires?)", 3),
        (r"complete [^.]{0,30}\bassessments?\b", 2),
```
In `NEGATIVE_PATTERNS` add (comment: Phase 11, each from a real false positive — account
mail from assessment platforms and HR-system digests; weighted to cancel a platform
sender's +2 plus one incidental phrase):

```python
    (r"password reset|reset your password", 4),
    (r"(?:login|verification|one-time) code", 4),
    (r"guest account", 4),
    (r"daily digest", 4),
    (r"\bnew invitations?\b", 2),
```

- [ ] **Step 4: Run, measure, then iterate on the remaining dev misses**

Run: `cd backend && uv run pytest -q && uv run python -m evaluation.compare && uv run python -m evaluation.run_eval --real --misses`
For each remaining **relatedness or status** miss in the dev report that shares a shape
with at least one other miss (or is an evidenced false positive): add one test case in
the style above (fictional text), add one pattern, re-run. Stop when the remaining
misses are one-offs. Do not add a pattern for a single message.
Expected at the end: no synthetic REGRESSION; real relatedness recall and
`queue_false_positive_share` improved (record numbers).

- [ ] **Step 5: Commit and push CP4**

```bash
git add backend/app/classifier/patterns.py backend/tests/test_classifier_patterns.py backend/tests/test_evaluation_accuracy.py
git commit -m "feat(classifier): cover real confirmation/assessment phrasings; suppress account and digest mail

<aggregate before/after from compare --real>"
cd frontend && npx tsc -b && npx oxlint && cd .. && git push origin main && gh run watch
```

---

# CP5 — Confidence and the position gate

### Task 15: Position gate in the pipeline (`apply_decision`)

**Files:**
- Modify: `backend/app/pipeline/service.py`
- Test: `backend/tests/test_pipeline_service.py`

**Interfaces:**
- Produces: `pipeline_service.apply_decision(db, user_id, extraction) -> tuple[str, UUID | None, str | None]`
  (renamed from `_apply_decision`, now public — Task 19 uses it). New gate: empty/blank
  position → `"pending_review"`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("position", [None, "", "   "])
def test_process_message_never_auto_applies_without_a_position(db_session, user, position) -> None:
    extractor = _SingleResultExtractor(
        EmailExtraction(is_job_related=True, confidence=0.99, company="Acme", position=position, status="applied")
    )
    review_status = service.process_message(
        db_session, user.id, extractor, sync_job_id=None, message_id="m1", summary=_summary(), body="b",
    )
    assert review_status == "pending_review"
    assert db_session.query(Application).count() == 0


def test_process_message_never_auto_updates_without_a_position(db_session, user) -> None:
    existing = Application(user_id=user.id, company="Acme", position="", status=ApplicationStatus.applied, source="gmail")
    db_session.add(existing)
    db_session.commit()
    extractor = _SingleResultExtractor(
        EmailExtraction(is_job_related=True, confidence=0.99, company="Acme", position=None, status="rejected")
    )
    assert service.process_message(
        db_session, user.id, extractor, sync_job_id=None, message_id="m1", summary=_summary(), body="b",
    ) == "pending_review"
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.applied
```
(add `import pytest` if missing).

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_pipeline_service.py -v -k position`
Expected: FAIL — `auto_applied` returned.

- [ ] **Step 3: Implement**

Rename `_apply_decision` → `apply_decision` (definition and its call in `process_message`).
In it, after `touches_existing_application = ...`:

```python
    # Fourth gate (Phase 11): no automatic create or update without a position. With
    # an empty position, matching compares on company alone, so two different
    # applications at one employer would be merged into one row.
    has_position = bool(extraction.position and extraction.position.strip())

    if (
        (touches_existing_application and not is_auto_managed)
        or match.ambiguous
        or not high_confidence
        or not has_position
    ):
```
Update the module's trust-model docstring/comment that lists the gates (three → four).

- [ ] **Step 4: Run**

Run: `cd backend && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline/service.py backend/tests/test_pipeline_service.py
git commit -m "feat(pipeline): never act automatically on an email without a position"
```

---

### Task 16: Weakest-link confidence, calibrated on the real dev split

**Files:**
- Modify: `backend/app/classifier/patterns.py`, `backend/app/classifier/fields.py`, `backend/app/classifier/extractor.py`, `backend/evaluation/inspect_confidence.py` (full replacement)
- Test: `backend/tests/test_classifier_extractor.py`

**Interfaces:**
- Produces: `extractor.ScoreBreakdown(net_signal, relatedness, status_clarity, company_confidence, company_provenance, company_corroborated, position_provenance)` with `.confidence` property (= min of the three);
  `RuleBasedExtractor.analyze(*, subject, sender, date, body, recipient) -> tuple[EmailExtraction, ScoreBreakdown]`;
  `fields.company_corroborated(company: str, provenance: str, sender: SenderInfo, text: str) -> bool`;
  patterns: `RELATEDNESS_NORM`, `STATUS_MARGIN_NORM`, `OTHER_STATUS_CLARITY`, `COMPANY_CONFIDENCE: dict[tuple[str, bool], float]`.
- Removed: `JOB_SIGNAL_NORM`, `MARGIN_NORM`, `DOMAIN_CONFIDENCE_BONUS`, `EXTRACTION_PENALTY`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_classifier_extractor.py`, replace the numeric confidence
assertions in the three existing tests that hand-compute the old formula:
- `..._applied_email_with_company_but_no_position`: replace the comment and the
  `pytest.approx(0.35)` line with `assert result.confidence < 0.85` (single phrase →
  relatedness below 1).
- `..._ats_domain_blocked_from_company_but_boosts_confidence`: rename to
  `test_classify_and_extract_takes_the_company_from_a_platform_emails_subject`; replace
  the long comment and the last three assertions with
  `assert result.company == "Acme Corp"`, `assert result.position == "Backend Engineer"`,
  `assert result.confidence >= 0.85`.
- `..._high_confidence_interview_with_both_fields`: `assert result.confidence == pytest.approx(1.0)`.

Append:

```python
from app.classifier.extractor import ScoreBreakdown
from app.classifier.patterns import COMPANY_CONFIDENCE
from evaluation.run_eval import CONFIDENCE_THRESHOLD


def _analyze(**kwargs) -> tuple:
    defaults = {"date": "Mon, 5 Jan 2026 10:00:00 +0000", "recipient": NO_RECIPIENT, "body": ""}
    return RuleBasedExtractor().analyze(**(defaults | kwargs))


def test_confidence_is_the_weakest_of_its_three_components() -> None:
    extraction, b = _analyze(subject="Interview Invitation", sender="careers@acme.com",
                             body="We would like to invite you to interview for the Backend Engineer position at Acme Corp.")
    assert isinstance(b, ScoreBreakdown)
    assert extraction.confidence == pytest.approx(min(b.relatedness, b.status_clarity, b.company_confidence))


def test_an_uncorroborated_guess_can_never_clear_the_threshold_on_its_own() -> None:
    for (provenance, corroborated), value in COMPANY_CONFIDENCE.items():
        if provenance in ("domain", "display_name", "tenant") and not corroborated:
            assert value < CONFIDENCE_THRESHOLD, provenance


def test_no_company_means_zero_confidence() -> None:
    extraction, b = _analyze(subject="Update", sender="noreply@greenhouse.io",
                             body="We received your application for the Backend Engineer position.")
    assert extraction.company is None
    assert extraction.confidence == 0.0 and b.company_provenance == "none"


def test_a_corroborated_subject_company_with_a_clear_phrase_and_position_clears() -> None:
    extraction, b = _analyze(subject="Thank you for applying to Halvorsen Robotics",
                             sender="careers@halvorsenrobotics.com",
                             body="We have received your application for the Robotics Engineer position.")
    assert b.company_corroborated is True
    assert extraction.position == "Robotics Engineer"
    assert extraction.confidence >= CONFIDENCE_THRESHOLD


def test_a_single_phrase_from_an_employer_is_limited_by_relatedness() -> None:
    extraction, b = _analyze(subject="Thank you for applying to Halvorsen", sender="x@halvorsen.com")
    assert b.relatedness < 1.0
    assert extraction.confidence == pytest.approx(b.relatedness)


def test_status_other_is_never_clear() -> None:
    extraction, b = _analyze(subject="Your Halvorsen application", sender="x@halvorsen.com",
                             body="Regarding your application for the Data Analyst position. Talent team")
    assert extraction.status == "other"
    assert extraction.confidence < CONFIDENCE_THRESHOLD
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_classifier_extractor.py -v`
Expected: FAIL — `ImportError: cannot import name 'ScoreBreakdown'`.

- [ ] **Step 3: Implement constants (`patterns.py`)**

Remove `JOB_SIGNAL_NORM`, `MARGIN_NORM`, `DOMAIN_CONFIDENCE_BONUS`, `EXTRACTION_PENALTY`
and their comment; add:

```python
# Phase 11 weakest-link confidence (spec §3.7): confidence = min(relatedness,
# status_clarity, company). Starting values below; calibrated on the private real dev
# split (Task 16 Step 5) so the [0.85, 1.0] band is >= 95% correct. Re-tune the same
# way: one constant at a time, re-measure.
RELATEDNESS_NORM = 5.0  # net signal at which relatedness saturates (two phrases, or one + platform sender)
STATUS_MARGIN_NORM = 3.0  # top-vs-runner-up status score margin at which clarity saturates
OTHER_STATUS_CLARITY = 0.3  # job-related but no status phrase: never clear
COMPANY_CONFIDENCE: dict[tuple[str, bool], float] = {
    # (provenance, corroborated) -> confidence. Corroborated = a text capture agrees
    # with the sender's own domain label or display name, or a sender-derived guess is
    # mentioned in the text. An uncorroborated sender-derived guess never clears alone.
    ("subject", True): 1.0,
    ("template", True): 1.0,
    ("subject", False): 0.9,
    ("template", False): 0.9,
    ("domain", True): 0.9,
    ("display_name", True): 0.9,
    ("tenant", True): 0.9,
    ("domain", False): 0.6,
    ("display_name", False): 0.6,
    ("tenant", False): 0.5,
}
```

- [ ] **Step 4: Implement corroboration (`fields.py`) and the model (`extractor.py`)**

`fields.py`:

```python
def company_corroborated(company: str, provenance: str, sender: SenderInfo, text: str) -> bool:
    """A text capture is corroborated when the sender's own label or display name
    agrees with it ("Halvorsen Robotics" from halvorsen.com or halvorsenrobotics.com);
    a sender-derived guess is corroborated when the text mentions it."""
    if provenance in ("subject", "template"):
        known = {label for label in (sender.org_label, sender.tenant_label) if label}
        if sender.display_name:
            known.add(compact(sender.display_name))
        words = company.split()
        prefixes = {compact(" ".join(words[: i + 1])) for i in range(len(words))}
        return bool(known & prefixes)
    return find_text_form(compact(company), text) is not None
```

`extractor.py` — replace the constants imports with `COMPANY_CONFIDENCE, GENERIC_JOB_PATTERNS,
JOB_RELATED_THRESHOLD, NEGATIVE_PATTERNS, OTHER_STATUS_CLARITY, RELATEDNESS_NORM,
STATUS_MARGIN_NORM, STATUS_PATTERNS`; import `company_corroborated`; add
`from dataclasses import dataclass`; replace `RuleBasedExtractor`:

```python
@dataclass(frozen=True)
class ScoreBreakdown:
    net_signal: int
    relatedness: float
    status_clarity: float
    company_confidence: float
    company_provenance: str
    company_corroborated: bool
    position_provenance: str

    @property
    def confidence(self) -> float:
        return min(self.relatedness, self.status_clarity, self.company_confidence)


class RuleBasedExtractor:
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str, recipient: Recipient
    ) -> EmailExtraction:
        return self.analyze(subject=subject, sender=sender, date=date, body=body, recipient=recipient)[0]

    def analyze(
        self, *, subject: str, sender: str, date: str, body: str, recipient: Recipient
    ) -> tuple[EmailExtraction, ScoreBreakdown]:
        """classify_and_extract plus the confidence components (Phase 11 spec §3.7),
        for evaluation/inspect_confidence.py — one formula, not a diagnostic copy."""
        subject = preprocess_subject(subject)
        body = preprocess_body(body)
        text = normalize_text(subject, body)
        sender_info = parse_sender(sender, self_address=recipient.email)
        status_scores, job_signal, negative_signal = classify(text, is_platform_sender=sender_info.kind == "platform")
        net_signal = job_signal - negative_signal
        relatedness = min(max(net_signal, 0) / RELATEDNESS_NORM, 1.0)

        if net_signal < JOB_RELATED_THRESHOLD:
            breakdown = ScoreBreakdown(net_signal, relatedness, 0.0, 0.0, "none", False, "none")
            return _build_extraction(is_job_related=False, confidence=relatedness), breakdown

        ranked = sorted(status_scores.items(), key=lambda kv: kv[1], reverse=True)
        top_status, top_score = ranked[0]
        runner_up_score = ranked[1][1]
        status = top_status if top_score > 0 else "other"
        status_clarity = (
            OTHER_STATUS_CLARITY if status == "other"
            else min((top_score - runner_up_score) / STATUS_MARGIN_NORM, 1.0)
        )

        raw_text = combine_subject_body(subject, body)
        company, company_provenance = find_company(raw_text, sender, subject=subject, recipient=recipient)
        position, position_provenance = find_position(raw_text, sender, subject=subject)
        corroborated = bool(company) and company_corroborated(company, company_provenance, sender_info, raw_text)
        company_confidence = COMPANY_CONFIDENCE.get((company_provenance, corroborated), 0.0) if company else 0.0

        breakdown = ScoreBreakdown(
            net_signal, relatedness, status_clarity, company_confidence,
            company_provenance, corroborated, position_provenance,
        )
        reasoning = (
            f"status={status} (score={top_score}, runner_up={runner_up_score}); "
            f"relatedness={relatedness:.2f} clarity={status_clarity:.2f} company={company_confidence:.2f} "
            f"via {company_provenance}{' (corroborated)' if corroborated else ''}; position via {position_provenance}"
        )
        extraction = _build_extraction(
            is_job_related=True,
            confidence=max(0.0, min(1.0, breakdown.confidence)),
            company=company,
            position=position,
            status=status,
            status_date=parse_email_date(date),
            reasoning=reasoning,
        )
        return extraction, breakdown
```

Replace `backend/evaluation/inspect_confidence.py`:

```python
# backend/evaluation/inspect_confidence.py
"""Print the Phase 11 confidence components for every example the classifier calls
job-related, and which one is the weakest link. Uses RuleBasedExtractor.analyze — the
production formula, not a copy. For recalibration work; not a regression gate.

    cd backend
    uv run python -m evaluation.inspect_confidence [--real]
"""

import argparse

from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import EVAL_RECIPIENT, example_body, load_dataset, synthetic_examples


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="private real dev split (local only)")
    args = parser.parse_args(argv)
    if args.real:
        from evaluation.real.dataset import load_real_examples

        examples = load_real_examples("dev")
    else:
        examples = synthetic_examples(load_dataset())

    extractor = RuleBasedExtractor()
    for example in examples:
        extraction, b = extractor.analyze(
            subject=example["subject"], sender=example["sender"], date=example["date"],
            body=example_body(example), recipient=EVAL_RECIPIENT,
        )
        if not extraction.is_job_related:
            continue
        weakest = min(
            (("relatedness", b.relatedness), ("status_clarity", b.status_clarity), ("company", b.company_confidence)),
            key=lambda pair: pair[1],
        )[0]
        print(
            f"{example.get('category', '?'):<24} conf={b.confidence:.2f} rel={b.relatedness:.2f} "
            f"clarity={b.status_clarity:.2f} company={b.company_confidence:.2f} "
            f"({b.company_provenance}{'+' if b.company_corroborated else ''}) "
            f"position={b.position_provenance} weakest={weakest}"
        )


if __name__ == "__main__":
    main()
```

Remove any now-unused imports from `extractor.py` (the old `ATS`/bonus/penalty names).

- [ ] **Step 5: Run, then calibrate on the real dev split**

Run: `cd backend && uv run pytest -q && uv run python -m evaluation.run_eval --real && uv run python -m evaluation.inspect_confidence --real`
Calibration loop (one constant per iteration, re-measure each time, dev split only):
1. If the `0.8`/`0.9` calibration bands are < 95% accurate or `auto_apply_precision` < 0.97:
   look at which component let the wrong ones through (`inspect_confidence --real`) and
   lower that constant (e.g. `COMPANY_CONFIDENCE[("template", False)]` 0.9 → 0.85, or
   `RELATEDNESS_NORM` 5.0 → 6.0).
2. If precision is safe but `auto_apply_rate_positioned` is low and the weakest link on
   correct items is consistently `relatedness`, try `RELATEDNESS_NORM` 5.0 → 4.0.
3. Stop when both hold, or when the next change would break precision.
Then: `uv run python -m evaluation.compare` (no synthetic bar may fail; if the synthetic
`auto_apply_rate` bar fails, see acceptance criterion #7 — explain in the commit, never
silently lower it) and `uv run python -m evaluation.bars` to raise real-derived bars.

- [ ] **Step 6: Commit and push CP5**

```bash
git add backend/app/classifier backend/app/pipeline backend/evaluation/inspect_confidence.py backend/tests
git commit -m "feat(classifier): weakest-link confidence calibrated on real mail

<final constants, calibration table and aggregate before/after from compare --real>"
cd frontend && npx tsc -b && npx oxlint && cd .. && git push origin main && gh run watch
```

---

# CP6a — Migration `0008` alone

### Task 17: Add the audit columns

**Files:**
- Create: `backend/alembic/versions/0008_add_classifier_version_and_reevaluation.py`
- Test: `backend/tests/test_migrations_0008.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_migrations_0008.py
from sqlalchemy import inspect


def test_migration_0008_adds_the_phase_11_columns(engine) -> None:
    # Phase 11 CP6a ships this migration ALONE (no model maps these columns yet), so
    # this test — against the real migrated schema — is its only coverage until CP6b.
    columns = {c["name"]: c for c in inspect(engine).get_columns("processed_messages")}
    for name in ("classifier_version", "reevaluated_at", "previous_classification"):
        assert columns[name]["nullable"] is True
    assert columns["previous_classification"]["type"].__class__.__name__ == "JSONB"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_migrations_0008.py -v`
Expected: FAIL — `KeyError: 'classifier_version'`.

- [ ] **Step 3: Implement**

```python
# backend/alembic/versions/0008_add_classifier_version_and_reevaluation.py
"""add classifier version and re-evaluation audit columns to processed_messages

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25

Deployed ALONE, before any code that reads or writes these columns (Phase 10's
migration-first rule; Phase 11 spec §3.8). All nullable: NULL classifier_version means
"classified before Phase 11".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processed_messages", sa.Column("classifier_version", sa.String(length=20), nullable=True))
    op.add_column("processed_messages", sa.Column("reevaluated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("processed_messages", sa.Column("previous_classification", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("processed_messages", "previous_classification")
    op.drop_column("processed_messages", "reevaluated_at")
    op.drop_column("processed_messages", "classifier_version")
```

- [ ] **Step 4: Run, migrate the dev DB, commit, push, verify production (M)**

```bash
cd backend && uv run pytest -q && uv run alembic upgrade head
git add backend/alembic/versions/0008_add_classifier_version_and_reevaluation.py backend/tests/test_migrations_0008.py
git commit -m "feat(db): add classifier version and re-evaluation audit columns (migration only)"
cd frontend && npx tsc -b && npx oxlint && cd .. && git push origin main && gh run watch
```
(M) After Render's deploy finishes: in the Neon SQL editor run
`SELECT version_num FROM alembic_version;` → `0008`, and
`SELECT column_name FROM information_schema.columns WHERE table_name='processed_messages' AND column_name IN ('classifier_version','reevaluated_at','previous_classification');`
→ 3 rows. `/health/ready` → 200. **Do not start Task 18 until this is confirmed.**

---

# CP6b — Re-evaluation

### Task 18: Map the columns; stamp every new row with the classifier version

**Files:**
- Modify: `backend/app/pipeline/models.py`, `backend/app/classifier/extractor.py`, `backend/app/pipeline/service.py`
- Test: `backend/tests/test_pipeline_service.py` (+ fakes in `test_sync_worker.py`, `test_sync_failure_injection.py`, `test_evaluation_run_eval.py`)

**Interfaces:**
- Produces: `ProcessedMessage.classifier_version: str | None`, `.reevaluated_at: datetime | None`,
  `.previous_classification: dict | None`; `extractor.CLASSIFIER_VERSION = "11.0"`;
  `Extractor.version: str` (Protocol attribute); `RuleBasedExtractor.version = CLASSIFIER_VERSION`.

- [ ] **Step 1: Write the failing test**

```python
def test_process_message_records_the_classifier_version(db_session, user) -> None:
    extractor = _SingleResultExtractor(EmailExtraction(is_job_related=False, confidence=0.0))
    service.process_message(db_session, user.id, extractor, sync_job_id=None, message_id="m1", summary=_summary(), body="b")
    assert db_session.query(ProcessedMessage).one().classifier_version == "test-extractor"
```
and give `_SingleResultExtractor` a class attribute `version = "test-extractor"`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_pipeline_service.py -v -k version`
Expected: FAIL — `AttributeError: 'ProcessedMessage' object has no attribute 'classifier_version'`.

- [ ] **Step 3: Implement**

`pipeline/models.py` (imports: `DateTime`, `from sqlalchemy.dialects.postgresql import JSONB`, `datetime`):

```python
    # Phase 11 (migration 0008): which classifier produced this row (NULL = before
    # Phase 11), and — only for rows the one-off re-evaluation rewrote — when, and what
    # the row said before (kept from the FIRST re-evaluation, for audit/manual rollback).
    classifier_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reevaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    previous_classification: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

`extractor.py`:

```python
# Bump whenever classification behavior changes, so a ProcessedMessage row says which
# classifier produced it (Phase 11 — old rows were never re-run and looked like live bugs).
CLASSIFIER_VERSION = "11.0"
```
`Extractor` Protocol gains `version: str`; `RuleBasedExtractor` gains `version = CLASSIFIER_VERSION`.

`pipeline/service.py`: in `process_message`'s `ProcessedMessage(...)`, add
`classifier_version=extractor.version,`.

Add `version = "test"` to the fake extractors in `test_sync_worker.py`,
`test_sync_failure_injection.py`, `test_evaluation_run_eval.py` (`_StubExtractor`,
`_RecordingExtractor`), and `_RecordingExtractor` in `test_pipeline_service.py`.

- [ ] **Step 4: Run**

Run: `cd backend && uv run pytest -q`
Expected: all PASS (`test_a_clean_run_makes_the_expected_number_of_commits` unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat(pipeline): record which classifier version produced each processed message"
```

---

### Task 19: `reevaluate` core — pending rows only, through the same gates

**Files:**
- Create: `backend/app/pipeline/reevaluate.py` (core in this task; `main` in Task 20)
- Modify: `backend/app/pipeline/service.py` (`_get_pending_item` takes a row lock)
- Test: `backend/tests/test_pipeline_reevaluate.py`

**Interfaces:**
- Consumes: `apply_decision` (Task 15), `call_with_fresh_token` (Task 2), `Recipient` (Task 12), columns (Task 18).
- Produces: `ReevaluationReport(apply: bool, users: Counter, rows: Counter)` with `.lines() -> list[str]`;
  `gmail_fetcher(db, connection) -> Callable[[str], tuple[dict, str]]`;
  `reevaluate_all(db, extractor, *, apply: bool, limit: int | None = None, fetch_factory=gmail_fetcher) -> ReevaluationReport`.
  Row outcome keys: `"pending_review->pending_review"`, `"pending_review->ignored"`,
  `"pending_review->auto_applied"`, `"left_alone_user_acted"`, `"fetch_failed"`,
  `"classification_failed"`. User keys: `"processed"`, `"skipped_no_gmail_connection"`,
  `"skipped_reconnect_needed"`, `"skipped_sync_in_progress"`, `"stopped_sync_started"`,
  `"stopped_reconnect_needed"`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_pipeline_reevaluate.py
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.applications.models import Application, ApplicationStatus
from app.classifier.extractor import ClassificationError
from app.classifier.schemas import EmailExtraction, Recipient
from app.gmail.crypto import encrypt_token
from app.gmail.google_api import GmailAuthError, GoogleApiError
from app.gmail.models import GmailConnection
from app.pipeline import service as pipeline_service
from app.pipeline.models import ProcessedMessage
from app.pipeline.reevaluate import reevaluate_all
from app.sync.models import SyncJob

SUBJECT = "Thanks for applying to Halvorsen Robotics"


class _Extractor:
    version = "11.0"

    def __init__(self, extraction: EmailExtraction) -> None:
        self.extraction = extraction
        self.recipients: list[Recipient] = []

    def classify_and_extract(self, *, subject, sender, date, body, recipient):
        self.recipients.append(recipient)
        return self.extraction


CONFIDENT = EmailExtraction(is_job_related=True, confidence=0.99, company="Halvorsen Robotics",
                            position="Robotics Engineer", status="applied")
NOT_RELATED = EmailExtraction(is_job_related=False, confidence=0.0)


def _fetch_factory(fetch=None):
    def default(message_id: str):
        return ({"subject": SUBJECT, "from_": "x@halvorsen.com", "date": "Mon, 5 Jan 2026 10:00:00 +0000"}, "body")

    return lambda db, connection: fetch or default


def _connect(db: Session, user, **overrides) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id, google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("a"), refresh_token_encrypted=encrypt_token("r"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly", **overrides,
    )
    db.add(connection)
    db.commit()
    return connection


def _row(db: Session, user, message_id: str, status: str = "pending_review", **fields) -> ProcessedMessage:
    row = ProcessedMessage(
        user_id=user.id, gmail_message_id=message_id, subject="old subject", sender="x@halvorsen.com",
        message_date="d", snippet="", is_job_related=True, confidence=0.3, extracted_company="Com",
        review_status=status, **fields,
    )
    db.add(row)
    db.commit()
    return row


def _columns(row: ProcessedMessage) -> dict:
    return {c.name: getattr(row, c.key) for c in ProcessedMessage.__table__.columns}


def test_only_pending_rows_are_selected_and_every_other_row_is_untouched(db_session, user) -> None:
    _connect(db_session, user)
    others = [_row(db_session, user, f"m-{s}", status=s) for s in ("approved", "rejected", "auto_applied", "ignored")]
    before = [_columns(r) for r in others]
    pending = _row(db_session, user, "m-pending")

    report = reevaluate_all(db_session, _Extractor(NOT_RELATED), apply=True, fetch_factory=_fetch_factory())

    for row, snapshot in zip(others, before):
        db_session.refresh(row)
        assert _columns(row) == snapshot
    db_session.refresh(pending)
    assert pending.review_status == "ignored"
    assert report.rows == {"pending_review->ignored": 1}


def test_dry_run_writes_nothing(db_session, user) -> None:
    _connect(db_session, user)
    row = _row(db_session, user, "m1")
    before = _columns(row)

    report = reevaluate_all(db_session, _Extractor(CONFIDENT), apply=False, fetch_factory=_fetch_factory())

    db_session.refresh(row)
    assert _columns(row) == before
    assert db_session.query(Application).count() == 0
    assert report.rows == {"pending_review->auto_applied": 1}


def test_apply_rewrites_the_row_and_keeps_a_snapshot(db_session, user) -> None:
    _connect(db_session, user)
    row = _row(db_session, user, "m1")
    extractor = _Extractor(CONFIDENT)

    reevaluate_all(db_session, extractor, apply=True, fetch_factory=_fetch_factory())

    db_session.refresh(row)
    assert (row.review_status, row.extracted_company, row.classifier_version) == ("auto_applied", "Halvorsen Robotics", "11.0")
    assert row.reevaluated_at is not None
    assert row.previous_classification["extracted_company"] == "Com"
    assert row.previous_classification["review_status"] == "pending_review"
    app = db_session.get(Application, row.matched_application_id)
    assert (app.company, app.source) == ("Halvorsen Robotics", "gmail")
    assert extractor.recipients == [Recipient(name="Alice", email="alice@example.com")]


def test_second_apply_keeps_the_original_snapshot(db_session, user) -> None:
    _connect(db_session, user)
    row = _row(db_session, user, "m1")
    pending_again = EmailExtraction(is_job_related=True, confidence=0.5, company="Halvorsen", status="applied")
    reevaluate_all(db_session, _Extractor(pending_again), apply=True, fetch_factory=_fetch_factory())
    reevaluate_all(db_session, _Extractor(pending_again), apply=True, fetch_factory=_fetch_factory())
    db_session.refresh(row)
    assert row.previous_classification["extracted_company"] == "Com"


def test_a_manual_application_is_never_modified(db_session, user) -> None:
    _connect(db_session, user)
    manual = Application(user_id=user.id, company="Halvorsen Robotics", position="Robotics Engineer",
                         status=ApplicationStatus.applied, source="manual")
    db_session.add(manual)
    db_session.commit()
    row = _row(db_session, user, "m1")
    rejected = CONFIDENT.model_copy(update={"status": "rejected"})

    reevaluate_all(db_session, _Extractor(rejected), apply=True, fetch_factory=_fetch_factory())

    db_session.refresh(manual)
    db_session.refresh(row)
    assert manual.status == ApplicationStatus.applied and manual.source == "manual"
    assert row.review_status == "pending_review"


def test_a_missing_position_never_auto_applies(db_session, user) -> None:
    _connect(db_session, user)
    row = _row(db_session, user, "m1")
    no_position = CONFIDENT.model_copy(update={"position": None})
    reevaluate_all(db_session, _Extractor(no_position), apply=True, fetch_factory=_fetch_factory())
    db_session.refresh(row)
    assert row.review_status == "pending_review"
    assert db_session.query(Application).count() == 0


def test_a_row_the_user_approved_meanwhile_is_left_alone(db_session, user) -> None:
    _connect(db_session, user)
    row = _row(db_session, user, "m1")

    def fetch_then_user_approves(message_id: str):
        db_session.execute(text("UPDATE processed_messages SET review_status='approved' WHERE id=:id"), {"id": row.id})
        db_session.commit()
        return ({"subject": SUBJECT, "from_": "x@halvorsen.com", "date": "d"}, "body")

    report = reevaluate_all(db_session, _Extractor(NOT_RELATED), apply=True,
                            fetch_factory=_fetch_factory(fetch_then_user_approves))
    db_session.refresh(row)
    assert row.review_status == "approved" and row.previous_classification is None
    assert report.rows == {"left_alone_user_acted": 1}


def test_deleted_message_is_left_untouched(db_session, user) -> None:
    _connect(db_session, user)
    row = _row(db_session, user, "m1")
    before = _columns(row)

    def gone(message_id: str):
        raise GoogleApiError("message fetch failed: HTTP 404", status_code=404)

    report = reevaluate_all(db_session, _Extractor(CONFIDENT), apply=True, fetch_factory=_fetch_factory(gone))
    db_session.refresh(row)
    assert _columns(row) == before
    assert report.rows == {"fetch_failed": 1}


def test_row_whose_matched_application_was_deleted_is_reclassified(db_session, user) -> None:
    _connect(db_session, user)
    app = Application(user_id=user.id, company="X", position="Y", status=ApplicationStatus.applied, source="gmail")
    db_session.add(app)
    db_session.commit()
    row = _row(db_session, user, "m1", matched_application_id=app.id, proposed_action="update")
    db_session.delete(app)
    db_session.commit()
    reevaluate_all(db_session, _Extractor(NOT_RELATED), apply=True, fetch_factory=_fetch_factory())
    db_session.refresh(row)
    assert row.review_status == "ignored" and row.matched_application_id is None


def test_classification_errors_are_counted_and_skipped(db_session, user) -> None:
    _connect(db_session, user)
    _row(db_session, user, "m1")

    class _Broken(_Extractor):
        def classify_and_extract(self, **kwargs):
            raise ClassificationError("bad")

    report = reevaluate_all(db_session, _Broken(CONFIDENT), apply=True, fetch_factory=_fetch_factory())
    assert report.rows == {"classification_failed": 1}


def test_users_needing_reconnect_or_with_an_active_sync_are_skipped(db_session, user, other_user) -> None:
    _connect(db_session, user, reauth_required_at=datetime.now(timezone.utc))
    _connect(db_session, other_user)
    db_session.add(SyncJob(user_id=other_user.id, job_type="incremental", window_start=datetime.now().date()))
    db_session.commit()
    _row(db_session, user, "m1")
    _row(db_session, other_user, "m2")
    report = reevaluate_all(db_session, _Extractor(CONFIDENT), apply=True, fetch_factory=_fetch_factory())
    assert report.users == {"skipped_reconnect_needed": 1, "skipped_sync_in_progress": 1}
    assert report.rows == {}


def test_a_sync_starting_mid_run_stops_that_user(db_session, user) -> None:
    _connect(db_session, user)
    _row(db_session, user, "m1")
    _row(db_session, user, "m2")

    def fetch_and_click_sync(message_id: str):
        db_session.add(SyncJob(user_id=user.id, job_type="incremental", window_start=datetime.now().date()))
        db_session.commit()
        return ({"subject": SUBJECT, "from_": "x@halvorsen.com", "date": "d"}, "body")

    report = reevaluate_all(db_session, _Extractor(NOT_RELATED), apply=True,
                            fetch_factory=_fetch_factory(fetch_and_click_sync))
    assert report.users == {"stopped_sync_started": 1}
    assert report.rows == {"pending_review->ignored": 1}


def test_a_revoked_grant_stops_that_user(db_session, user) -> None:
    _connect(db_session, user)
    _row(db_session, user, "m1")

    def revoked(message_id: str):
        raise GmailAuthError("token refresh failed: invalid_grant")

    report = reevaluate_all(db_session, _Extractor(CONFIDENT), apply=True, fetch_factory=_fetch_factory(revoked))
    assert report.users == {"stopped_reconnect_needed": 1}


def test_report_lines_carry_counts_only(db_session, user) -> None:
    _connect(db_session, user)
    _row(db_session, user, "gmail-id-123")
    report = reevaluate_all(db_session, _Extractor(CONFIDENT), apply=False, fetch_factory=_fetch_factory())
    output = "\n".join(report.lines())
    for private in ("gmail-id-123", "Halvorsen", SUBJECT, "old subject", "alice"):
        assert private not in output
    assert "DRY RUN" in output


def test_approve_waits_for_a_row_being_reevaluated(engine) -> None:
    """approve/reject take the same row lock, so a click racing a re-evaluation can't
    act on a stale read. Two real connections (Phase 5 technique)."""
    from app.users.models import User

    holder = Session(bind=engine)
    other = Session(bind=engine)
    owner = User(google_sub="lock-test", email="lock@example.com", name="Lock")
    holder.add(owner)
    holder.commit()
    row = ProcessedMessage(user_id=owner.id, gmail_message_id="lock-1", subject="s", sender="x@y.com",
                           message_date="d", snippet="", is_job_related=True, confidence=0.3,
                           review_status="pending_review")
    holder.add(row)
    holder.commit()
    try:
        holder.execute(text("SELECT id FROM processed_messages WHERE id=:id FOR UPDATE"), {"id": row.id})
        other.execute(text("SET LOCAL lock_timeout = '300ms'"))
        with pytest.raises(OperationalError):
            pipeline_service._get_pending_item(other, owner.id, row.id)
    finally:
        other.rollback()
        holder.rollback()
        holder.execute(text("DELETE FROM processed_messages WHERE id=:id"), {"id": row.id})
        holder.execute(text("DELETE FROM users WHERE id=:id"), {"id": owner.id})
        holder.commit()
        holder.close()
        other.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_pipeline_reevaluate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.reevaluate'`.

- [ ] **Step 3: Implement the row lock in `pipeline/service.py`**

In `_get_pending_item`, add `.with_for_update()` to the `select(ProcessedMessage)...` and a
comment: "Locked (Phase 11): a review click racing the one-off re-evaluation waits for
it, then sees the row's new state instead of acting on a stale read."

- [ ] **Step 4: Implement `backend/app/pipeline/reevaluate.py`**

```python
# backend/app/pipeline/reevaluate.py
"""One-off re-evaluation of the review queue with the current classifier (Phase 11
spec §3.8).

    cd backend
    uv run python -m app.pipeline.reevaluate            # dry run: counts only, writes nothing
    uv run python -m app.pipeline.reevaluate --apply

Production runs it from .github/workflows/reevaluate.yml (manual dispatch). This is the
one deliberate exception to "a message is classified at most once", and it is narrow:
only rows still `pending_review` are re-read and rewritten — never approved, rejected,
auto_applied or ignored rows — and every decision goes through the same apply_decision
gates a sync uses, so a manually edited application is never modified and nothing
without a position is auto-applied. Logs are public (GitHub Actions): counts only.
"""

import argparse
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application
from app.classifier.extractor import ClassificationError, Extractor, RuleBasedExtractor
from app.classifier.schemas import EmailExtraction, Recipient
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.google_api import GmailAuthError, GoogleApiError
from app.gmail.models import GmailConnection
from app.pipeline import service as pipeline_service
from app.pipeline.models import ProcessedMessage
from app.sync.models import SyncJob
from app.users.models import User  # noqa: F401 — standalone mapper configuration, see test_reevaluate_entrypoint.py

logger = logging.getLogger(__name__)

FetchMessage = Callable[[str], tuple[dict, str]]
FetchFactory = Callable[[Session, GmailConnection], FetchMessage]

_SNAPSHOT_FIELDS = (
    "is_job_related", "confidence", "extracted_company", "extracted_position", "extracted_status",
    "extracted_status_date", "matched_application_id", "proposed_action", "review_status",
    "classifier_version",
)


@dataclass
class ReevaluationReport:
    apply: bool
    users: Counter = field(default_factory=Counter)
    rows: Counter = field(default_factory=Counter)

    def lines(self) -> list[str]:
        """Counts only — this goes to a public log."""
        mode = "APPLY" if self.apply else "DRY RUN (nothing written)"
        return (
            [f"Re-evaluation of pending review items — {mode}"]
            + [f"users {key}: {value}" for key, value in sorted(self.users.items())]
            + [f"rows {key}: {value}" for key, value in sorted(self.rows.items())]
        )


def gmail_fetcher(db: Session, connection: GmailConnection) -> FetchMessage:
    def fetch(message_id: str) -> tuple[dict, str]:
        return gmail_service.call_with_fresh_token(
            db, connection, lambda token: google_api.get_message(token, message_id)
        )

    return fetch


def _has_active_sync(db: Session, user_id: UUID) -> bool:
    return db.scalar(
        select(SyncJob.id).where(SyncJob.user_id == user_id, SyncJob.status.in_(("queued", "running"))).limit(1)
    ) is not None


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _reclassify_row(
    db: Session, user_id: UUID, item_id: UUID, extraction: EmailExtraction, version: str, *, apply: bool
) -> str:
    row = db.scalars(select(ProcessedMessage).where(ProcessedMessage.id == item_id).with_for_update()).one_or_none()
    if row is None or row.review_status != "pending_review":
        db.rollback()
        return "left_alone_user_acted"
    # Hold this user's applications for this short transaction, so a concurrent manual
    # edit (which flips source to "manual") can't slip between apply_decision's source
    # check and its write.
    db.execute(select(Application.id).where(Application.user_id == user_id).with_for_update())
    review_status, matched_application_id, proposed_action = pipeline_service.apply_decision(db, user_id, extraction)
    transition = f"pending_review->{review_status}"
    if not apply:
        db.rollback()
        return transition
    if row.previous_classification is None:  # keep the ORIGINAL across repeated runs
        row.previous_classification = {name: _jsonable(getattr(row, name)) for name in _SNAPSHOT_FIELDS}
    row.is_job_related = extraction.is_job_related
    row.confidence = extraction.confidence
    row.extracted_company = extraction.company
    row.extracted_position = extraction.position
    row.extracted_status = extraction.status
    row.extracted_status_date = extraction.status_date
    row.matched_application_id = matched_application_id
    row.proposed_action = proposed_action
    row.review_status = review_status
    row.classifier_version = version
    row.reevaluated_at = datetime.now(timezone.utc)
    db.commit()
    return transition


def _reevaluate_user(
    db: Session, user: User, extractor: Extractor, fetch_factory: FetchFactory,
    report: ReevaluationReport, *, apply: bool, limit: int | None,
) -> None:
    connection = gmail_service.get_connection(db, user.id)
    if connection is None:
        report.users["skipped_no_gmail_connection"] += 1
        return
    if connection.reauth_required_at is not None:
        report.users["skipped_reconnect_needed"] += 1
        return
    if _has_active_sync(db, user.id):
        report.users["skipped_sync_in_progress"] += 1
        return

    items = db.execute(
        select(ProcessedMessage.id, ProcessedMessage.gmail_message_id)
        .where(ProcessedMessage.user_id == user.id, ProcessedMessage.review_status == "pending_review")
        .order_by(ProcessedMessage.created_at, ProcessedMessage.id)
    ).all()
    if limit is not None:
        items = items[:limit]
    recipient = Recipient(name=user.name, email=user.email)
    fetch = fetch_factory(db, connection)

    for item_id, message_id in items:
        # A sync job row exists the moment Sync is clicked; its worker needs a GitHub
        # runner to boot (10s+) before writing anything — so checking before every row
        # always sees it first (spec §3.8).
        if _has_active_sync(db, user.id):
            report.users["stopped_sync_started"] += 1
            return
        try:
            summary, body = fetch(message_id)
        except GmailAuthError:
            report.users["stopped_reconnect_needed"] += 1
            return
        except GoogleApiError:
            report.rows["fetch_failed"] += 1
            continue
        try:
            extraction = extractor.classify_and_extract(
                subject=summary["subject"], sender=summary["from_"], date=summary["date"],
                body=body, recipient=recipient,
            )
        except ClassificationError:
            report.rows["classification_failed"] += 1
            continue
        report.rows[_reclassify_row(db, user.id, item_id, extraction, extractor.version, apply=apply)] += 1
    report.users["processed"] += 1


def reevaluate_all(
    db: Session, extractor: Extractor, *, apply: bool, limit: int | None = None,
    fetch_factory: FetchFactory = gmail_fetcher,
) -> ReevaluationReport:
    report = ReevaluationReport(apply=apply)
    users = list(db.scalars(
        select(User).join(GmailConnection, GmailConnection.user_id == User.id).order_by(User.id)
    ))
    for user in users:
        _reevaluate_user(db, user, extractor, fetch_factory, report, apply=apply, limit=limit)
    return report
```

- [ ] **Step 5: Run**

Run: `cd backend && uv run pytest tests/test_pipeline_reevaluate.py tests/test_pipeline_review.py tests/test_pipeline_router.py -v`
Expected: all PASS (review endpoints unaffected by the lock).

- [ ] **Step 6: Commit**

```bash
git add backend/app/pipeline/reevaluate.py backend/app/pipeline/service.py backend/tests/test_pipeline_reevaluate.py
git commit -m "feat(pipeline): re-evaluate pending review items through the unchanged gates"
```

---

### Task 20: `reevaluate` CLI and the manual workflow

**Files:**
- Modify: `backend/app/pipeline/reevaluate.py` (add `main`)
- Create: `.github/workflows/reevaluate.yml`
- Test: `backend/tests/test_reevaluate_workflow.py`, `backend/tests/test_reevaluate_entrypoint.py`

**Interfaces:**
- Produces: `reevaluate.main(argv: list[str] | None = None) -> int` (1 if any user run
  stopped, else 0); workflow `Re-evaluate review queue` with boolean input `apply`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_reevaluate_workflow.py
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "reevaluate.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))  # PyYAML parses bare `on` as True


def test_reevaluation_is_manual_only_and_dry_run_by_default() -> None:
    triggers = _triggers(_load())
    assert set(triggers) == {"workflow_dispatch"}
    apply = triggers["workflow_dispatch"]["inputs"]["apply"]
    assert apply["type"] == "boolean" and apply["default"] is False


def test_reevaluation_has_its_own_concurrency_group_and_read_only_token() -> None:
    workflow = _load()
    # Not the incremental lane's group: GitHub keeps one PENDING run per group, so
    # sharing it would let a Sync dispatch cancel a pending re-evaluation (or the
    # reverse). The per-row active-sync check keeps it away from syncs instead.
    assert workflow["concurrency"] == {"group": "reevaluate", "cancel-in-progress": False}
    assert workflow["permissions"] == {"contents": "read"}


def test_reevaluation_checkout_does_not_persist_credentials_and_passes_apply_safely() -> None:
    steps = _load()["jobs"]["reevaluate"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    run = next(s for s in steps if "app.pipeline.reevaluate" in s.get("run", ""))
    assert run["env"]["APPLY"] == "${{ inputs.apply }}"
    assert "${{" not in run["run"]  # the input reaches the shell only via env
```

```python
# backend/tests/test_reevaluate_entrypoint.py
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_reevaluate_module_can_configure_orm_mappers_standalone() -> None:
    """Same trap as test_sync_worker_entrypoint.py: `python -m app.pipeline.reevaluate`
    runs in a fresh process, where the string relationship to "User" only resolves if
    app.users.models was imported."""
    result = subprocess.run(
        [sys.executable, "-c", "import app.pipeline.reevaluate; from sqlalchemy.orm import configure_mappers; configure_mappers()"],
        cwd=BACKEND_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_reevaluate_workflow.py tests/test_reevaluate_entrypoint.py -v`
Expected: workflow tests FAIL (`FileNotFoundError`); the entrypoint test passes already
(the `User` import is in Task 19) — keep it as the regression guard.

- [ ] **Step 3: Implement `main`**

Append to `backend/app/pipeline/reevaluate.py`:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-evaluate pending review items with the current classifier.")
    parser.add_argument("--apply", action="store_true", help="write the results (default: dry run)")
    parser.add_argument("--limit", type=int, help="at most this many rows per user")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    # Public log: httpx logs every request URL at INFO, and Gmail URLs contain message IDs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        report = reevaluate_all(db, RuleBasedExtractor(), apply=args.apply, limit=args.limit)
    for line in report.lines():
        logger.info(line)
    return 1 if any(key.startswith("stopped_") for key in report.users) else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Create `.github/workflows/reevaluate.yml`**

```yaml
name: Re-evaluate review queue

# Phase 11 — manual, one-off: re-classifies pending_review items with the current
# classifier. Dry run unless "apply" is ticked. Logs are public: counts only.
on:
  workflow_dispatch:
    inputs:
      apply:
        description: "Write the new classifications (unticked = dry run)"
        type: boolean
        default: false

permissions:
  contents: read

# Its own group — not the incremental lane's. GitHub keeps only one pending run per
# group, so sharing it would let a Sync click's dispatch cancel a pending
# re-evaluation or vice versa. The command itself checks for an active sync job
# before every row and stops if one appears.
concurrency:
  group: reevaluate
  cancel-in-progress: false

jobs:
  reevaluate:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      DATABASE_URL: ${{ secrets.PROD_DATABASE_URL }}
      GOOGLE_CLIENT_ID: ${{ secrets.PROD_GOOGLE_CLIENT_ID }}
      GOOGLE_CLIENT_SECRET: ${{ secrets.PROD_GOOGLE_CLIENT_SECRET }}
      GMAIL_TOKEN_ENCRYPTION_KEY: ${{ secrets.PROD_GMAIL_TOKEN_ENCRYPTION_KEY }}
      SECRET_KEY: ${{ secrets.PROD_SECRET_KEY }}
      CORS_ORIGINS: https://job-tracker-1-ldy2.onrender.com
      FRONTEND_URL: https://job-tracker-1-ldy2.onrender.com
      ENV: production
      COOKIE_SECURE: "true"
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          persist-credentials: false

      - name: Install uv
        uses: astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4 # v10.1.0
        with:
          enable-cache: true
          python-version: "3.12"

      - name: Install dependencies
        working-directory: backend
        run: uv sync --locked

      - name: Re-evaluate pending review items
        working-directory: backend
        env:
          APPLY: ${{ inputs.apply }}
        run: |
          if [ "$APPLY" = "true" ]; then
            uv run python -m app.pipeline.reevaluate --apply
          else
            uv run python -m app.pipeline.reevaluate
          fi
```

- [ ] **Step 5: Run everything, commit, push CP6b**

Run: `cd backend && uv run pytest -q` (includes `test_every_workflow_action_is_pinned_to_a_full_commit_sha`)
Expected: all PASS.

```bash
git add backend/app/pipeline/reevaluate.py .github/workflows/reevaluate.yml backend/tests/test_reevaluate_workflow.py backend/tests/test_reevaluate_entrypoint.py
git commit -m "feat(pipeline): add the manual re-evaluation command and workflow"
cd frontend && npx tsc -b && npx oxlint && cd .. && git push origin main && gh run watch
```

---

### Task 21 (M): Re-evaluate production's queue

- [ ] **Step 1 (M): Preconditions**

In the deployed app: Gmail connected (Reconnect if the amber notice shows), no sync
running. Note the review-queue count shown in the UI.

- [ ] **Step 2 (M): Dry run**

GitHub → Actions → **Re-evaluate review queue** → Run workflow (apply unticked). Read the
counts in the log (`rows pending_review->…`). Sanity checks: no `stopped_*`;
`fetch_failed` small; transitions plausible for the queue the maintainer knows.

- [ ] **Step 3 (M): Apply**

Run it again with **apply** ticked. Expect the same counts as the dry run (± rows the
maintainer acted on in between).

- [ ] **Step 4 (M): Verify**

UI: queue count dropped by `ignored + auto_applied`; spot-check a few remaining items
(better company/position) and any new auto-applied applications. Neon SQL editor:

```sql
SELECT review_status, count(*) FROM processed_messages WHERE reevaluated_at IS NOT NULL GROUP BY 1;
SELECT count(*) FROM processed_messages WHERE reevaluated_at IS NOT NULL AND previous_classification IS NULL;  -- expect 0
```
Record queue size before/after and the transition counts (numbers only) for Task 22.
Then click Sync once and confirm it still completes in about a minute.

---

# CP7 — Final measurement and docs

### Task 22: Score the held-out test split once; document Phase 11

**Files:**
- Modify: the spec (§9), `README.md`, `CLAUDE.md`

- [ ] **Step 1: Score the test split (once)**

```bash
cd backend
uv run python -m evaluation.run_eval --real --split test --final --json ~/.job-tracker-eval/final_test.json
uv run python -m evaluation.run_eval --real --split all --final   # calibration over dev+test (criterion 6)
```
Compare against spec §6 (as confirmed at CP0). Do **not** tune anything after this run;
a missed target is recorded as a finding, not fixed by re-tuning on the test split.

- [ ] **Step 2: Record results in the spec's §9**

Aggregate numbers only: test-split metrics vs targets (met / missed), calibration table,
synthetic metrics from `evaluation.compare`, production re-evaluation counts and queue
size before/after, final constants, and deviations from the spec (committed subset
moved to CP2; re-evaluation concurrency group; any others found during execution).

- [ ] **Step 3: README**

Add under "Production deployment" a **"Re-evaluating the review queue"** runbook (Task 21
steps, the rollback query below) and a top-level **"Real-mail evaluation (local)"**
section (Task 7 commands; privacy rules from spec §5; `check_leaks` before committing
real-derived examples; `CLASSIFIER_VERSION` bump rule). Rollback query:

```sql
-- Restores one re-evaluated row's classification (review_status and extracted fields).
UPDATE processed_messages SET
  review_status = previous_classification->>'review_status',
  extracted_company = previous_classification->>'extracted_company',
  extracted_position = previous_classification->>'extracted_position',
  extracted_status = previous_classification->>'extracted_status',
  confidence = (previous_classification->>'confidence')::float,
  is_job_related = (previous_classification->>'is_job_related')::boolean,
  proposed_action = previous_classification->>'proposed_action',
  matched_application_id = (previous_classification->>'matched_application_id')::uuid
WHERE id = '<row id>';
```

- [ ] **Step 4: CLAUDE.md**

Status line (Phases 1–11, test count from `uv run pytest -q`), a Phase 11 bullet, the
architecture tree (`classifier/sender.py`, `pipeline/reevaluate.py`, `evaluation/real/`,
`reevaluate.yml`), key-design-decision updates (four gates incl. position; weakest-link
confidence replaces the additive formula; sender resolution via the Public Suffix List;
classify-once exception for the one-off re-evaluation; `CLASSIFIER_VERSION`), a
"Phase 11 results" section (numbers, by shape only), and mark resolved known gaps:
`_SUBDOMAIN_PREFIXES` hazard (fixed), compound-subdomain question (answered by the
registrable domain), real-mail confidence never clearing 0.85 (addressed — with the
measured auto-apply rate). Add remaining gaps found during execution.

- [ ] **Step 5: Final checks, commit, push**

```bash
cd backend && uv run pytest && uv run python -m evaluation.compare && uv run python -m evaluation.real.check_leaks
cd ../frontend && npx tsc -b && npx oxlint && cd ..
git add README.md CLAUDE.md docs/superpowers/specs/2026-09-25-job-tracker-phase-11-real-mail-classifier-quality-design.md
git commit -m "docs: document Phase 11 results, re-evaluation runbook and local real-mail evaluation"
git push origin main && gh run watch
```
