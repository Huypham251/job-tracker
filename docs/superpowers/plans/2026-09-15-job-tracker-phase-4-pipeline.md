# Phase 4: Classification, Extraction & Auto-Tracking Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **⚠️ Superseded (2026-09-16):** The `app/llm/` package and Anthropic SDK described
> here were executed as written, then fully replaced by
> `2026-09-15-job-tracker-phase-4b-local-classifier.md` — `app/llm/` was deleted and the
> `anthropic` dependency removed. This document is kept as the historical record of how
> the pipeline's matching/trust-model/DB/API/frontend layers (still current) were first
> built; for classification/extraction, see the 4b plan instead.

**Goal:** Turn fetched Gmail messages into structured job-application data, match them against existing applications, and create/update applications automatically when confident or via a human-reviewed queue otherwise — without ever silently overwriting a manually-entered fact.

**Architecture:** A new `app/llm/` package wraps the Anthropic SDK behind a small `Extractor` protocol (`client.messages.parse(output_format=EmailExtraction)` for validated structured output); a new `app/pipeline/` package orchestrates fetch → extract → match (`rapidfuzz`) → decide → persist, backed by a new `processed_messages` audit/idempotency/review-queue table. A row-level trust rule — never auto-touch a `source="manual"` application, regardless of confidence — is the entire mechanism for protecting manually-entered data. Everything is triggered by a manual "Process Inbox" action, reusing Phase 3's Gmail-fetch plumbing; no background jobs yet.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, `anthropic` SDK (`client.messages.parse`, Pydantic structured output), `rapidfuzz` (fuzzy matching), React + TypeScript (frontend), pytest + `monkeypatch` (no real Anthropic/Gmail API calls in tests).

**Spec:** `docs/superpowers/specs/2026-09-15-job-tracker-phase-4-pipeline-design.md`

## Global Constraints

- Model: `claude-opus-5`, `output_config={"effort": "low"}`, via `client.messages.parse(output_format=EmailExtraction)` — no hand-rolled JSON parsing anywhere.
- A single overall `confidence` float (0.0-1.0) per extraction — no per-field scores this phase.
- Trust rule (structural, not a convention): any proposed change touching an `Application` with `source == "manual"` always goes to `pending_review`, regardless of confidence. A `source == "gmail"` application can be auto-updated once `confidence >= settings.llm_confidence_threshold` and the match is unambiguous.
- An `other`-status extraction against an application whose current status is already one of `applied`/`oa`/`interview`/`rejected`/`offer` is a no-op (`ignored`) — never regress a specific status to `other`. This check does not apply when the match itself is ambiguous (we don't know it's about that application).
- The email body is fetched (`format=full`), cleaned (HTML/quoted-replies/signatures stripped), and truncated to `BODY_MAX_CHARS = 4000` characters before ever being sent to the LLM. The body itself is never persisted — only the derived structured fields plus the existing Phase-3-approved subject/snippet.
- Matching thresholds: `rapidfuzz.fuzz.ratio` on normalized `"{company} {position}"`; best score ≥ 85 AND no other candidate within 10 points → confident unambiguous match; best score < 60 → confident no-match (create); everything else → ambiguous.
- `pipeline_batch_limit` (default 20) caps messages fetched per `/pipeline/process` call — a per-request safety cap, not Phase 5's rate-limiting/scheduling infrastructure.
- Migration is `0005`, `down_revision = "0004"`. `application_status` gets an additive `other` value (Postgres allows `ALTER TYPE ... ADD VALUE` inside a normal transaction on PG12+, as long as the new value isn't used in that same transaction — confirmed against this project's Postgres 16, no special autocommit handling needed). `withdrawn` is untouched.
- Every `/api/v1/pipeline/*` route requires `Depends(get_current_user)`; a review item id not owned by the caller is `404`, matching the existing `ApplicationNotFound`/`GmailNotConnected` convention (never `403`).
- Follow existing test conventions: monkeypatch the `Extractor` protocol and Gmail/`httpx` calls — never call the real Anthropic or Google APIs from tests; reuse `client`/`auth_client`/`other_auth_client`/`user`/`other_user`/`db_session` fixtures already in `backend/tests/conftest.py`.
- The evaluation dataset itself (`backend/evaluation/dataset.jsonl` content) is intentionally NOT designed in this plan — Task 10 scaffolds the file/script shape only; building the actual labeled set happens afterward via the `claude-api` skill's `build-eval` workflow (see Task 10).

---

## Task 1: Dependencies and configuration

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/core/config.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `settings.anthropic_api_key: str`, `settings.llm_model: str` (default `"claude-opus-5"`), `settings.llm_confidence_threshold: float` (default `0.85`), `settings.pipeline_batch_limit: int` (default `20`) on `app.core.config.settings`.

- [ ] **Step 1: Add `anthropic` and `rapidfuzz` as direct dependencies**

Edit `backend/pyproject.toml`:

```toml
dependencies = [
    "alembic>=1.19.2",
    "anthropic>=1.6.0",
    "authlib>=1.8.0",
    "cryptography>=43.0.0",
    "fastapi>=0.141.1",
    "httpx>=0.28.1",
    "itsdangerous>=2.2.0",
    "psycopg[binary]>=3.3.5",
    "pydantic>=2.13.5",
    "pydantic-settings>=2.15.0",
    "pyjwt>=2.13.0",
    "rapidfuzz>=3.10.0",
    "sqlalchemy>=2.0.52",
    "uvicorn[standard]>=0.52.4",
]
```

Run: `cd backend && uv sync`
Expected: lock file updates, no errors; `uv run python -c "import anthropic, rapidfuzz"` succeeds.

- [ ] **Step 2: Add the new settings**

Edit `backend/app/core/config.py`, adding after `gmail_token_encryption_key: str`:

```python
    gmail_token_encryption_key: str
    anthropic_api_key: str
    llm_model: str = "claude-opus-5"
    llm_confidence_threshold: float = 0.85
    pipeline_batch_limit: int = 20
```

`anthropic_api_key` is required (no default) — same pattern as `google_client_id`/`gmail_token_encryption_key`, so a missing key fails loudly at startup.

- [ ] **Step 3: Add a placeholder key to `backend/.env` and `.env.example`**

`backend/.env` is gitignored and local-only. Unlike the Fernet key in Phase 3, the Anthropic SDK only validates the API key format at actual network-call time, not at client construction — so a placeholder string is sufficient for every test in this plan (none of them make a real network call).

Run:
```bash
cd backend
echo "ANTHROPIC_API_KEY=sk-ant-placeholder-for-local-dev" >> .env
```

Edit `.env.example`, appending after the `GMAIL_TOKEN_ENCRYPTION_KEY` line:

```
# Anthropic API key for the Phase 4 classification/extraction pipeline.
# Get a real one from https://console.anthropic.com/ to actually run
# "Process Inbox" against real email; the placeholder below is enough to
# run the test suite (no test makes a real Anthropic API call).
ANTHROPIC_API_KEY=sk-ant-placeholder-for-local-dev
```

- [ ] **Step 4: Verify the existing suite still passes**

Run: `cd backend && uv run pytest -q`
Expected: all 87 tests still pass (this task only adds settings/dependencies, no behavior change).

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/core/config.py .env.example
git commit -m "feat(backend): add anthropic/rapidfuzz dependencies and Phase 4 config

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: LLM provider abstraction

**Files:**
- Create: `backend/app/llm/__init__.py`
- Create: `backend/app/llm/schemas.py`
- Create: `backend/app/llm/prompts.py`
- Create: `backend/app/llm/client.py`
- Test: `backend/tests/test_llm_client.py`

**Interfaces:**
- Produces: `app.llm.schemas.EmailExtraction` (Pydantic model, fields: `is_job_related: bool`, `confidence: float`, `company: str | None`, `position: str | None`, `status: Literal["applied","oa","interview","rejected","offer","other"] | None`, `status_date: date | None`, `reasoning: str | None`).
- Produces: `app.llm.client.Extractor` (Protocol with `classify_and_extract(*, subject: str, sender: str, date: str, body: str) -> EmailExtraction`), `app.llm.client.AnthropicExtractor` (the implementation), `app.llm.client.LLMExtractionError` (exception).

- [ ] **Step 1: Write the schema**

Create `backend/app/llm/__init__.py` (empty file).

Create `backend/app/llm/schemas.py`:

```python
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class EmailExtraction(BaseModel):
    is_job_related: bool
    confidence: float = Field(ge=0.0, le=1.0)
    company: str | None = None
    position: str | None = None
    status: Literal["applied", "oa", "interview", "rejected", "offer", "other"] | None = None
    status_date: date | None = None
    reasoning: str | None = None
```

- [ ] **Step 2: Write the system prompt**

Create `backend/app/llm/prompts.py`:

```python
SYSTEM_PROMPT = """You are a careful email classifier for a job application tracker.

Given the subject, sender, date, and body of one email, determine:

1. Whether this email is related to a job application the recipient personally
   submitted (e.g. application confirmations, online assessment / OA invitations,
   interview scheduling, rejections, offers). Marketing emails, job board digests,
   newsletters, and emails about applications for OTHER people are NOT
   job-application-related.
2. If it is job-related, extract:
   - company: the hiring company's name, as written in the email.
   - position: the job title/role, as written in the email.
   - status: one of "applied", "oa", "interview", "rejected", "offer", or "other" if
     the email is clearly job-related but doesn't clearly indicate a specific stage.
   - status_date: the date most relevant to this email's content (e.g. an interview
     date, or the email's own date if no other date is mentioned).
3. Provide an overall confidence score (0.0-1.0) reflecting how sure you are about
   both the relevance determination and the extracted fields together. Use a LOW
   confidence score whenever the company or position is ambiguous, abbreviated, or
   only implied rather than stated plainly.

If the email is not job-related, leave company/position/status/status_date unset —
do not guess at values for irrelevant email.
"""
```

- [ ] **Step 3: Write the failing test**

Create `backend/tests/test_llm_client.py`:

```python
import anthropic
import httpx2

from app.core.config import settings
from app.llm.client import AnthropicExtractor, LLMExtractionError
from app.llm.schemas import EmailExtraction


class _FakeMessages:
    def __init__(self, *, parsed_output=None, raise_error=None):
        self._parsed_output = parsed_output
        self._raise_error = raise_error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_error is not None:
            raise self._raise_error
        return type("FakeResponse", (), {"parsed_output": self._parsed_output})()


def _make_extractor(fake_messages: _FakeMessages) -> AnthropicExtractor:
    extractor = AnthropicExtractor()
    extractor._client.messages = fake_messages
    return extractor


def test_classify_and_extract_returns_parsed_output() -> None:
    expected = EmailExtraction(
        is_job_related=True,
        confidence=0.9,
        company="Acme",
        position="SWE",
        status="applied",
        status_date=None,
        reasoning=None,
    )
    extractor = _make_extractor(_FakeMessages(parsed_output=expected))

    result = extractor.classify_and_extract(subject="s", sender="f", date="d", body="b")

    assert result == expected


def test_classify_and_extract_sends_correct_request_shape() -> None:
    expected = EmailExtraction(is_job_related=False, confidence=0.99)
    fake = _FakeMessages(parsed_output=expected)
    extractor = _make_extractor(fake)

    extractor.classify_and_extract(
        subject="Newsletter", sender="news@x.com", date="Mon", body="Check out our sale"
    )

    call = fake.calls[0]
    assert call["output_format"] is EmailExtraction
    assert call["model"] == settings.llm_model
    content = call["messages"][0]["content"]
    assert "Newsletter" in content
    assert "news@x.com" in content
    assert "Check out our sale" in content


def test_classify_and_extract_raises_when_parsed_output_is_none() -> None:
    extractor = _make_extractor(_FakeMessages(parsed_output=None))

    try:
        extractor.classify_and_extract(subject="s", sender="f", date="d", body="b")
        assert False, "expected LLMExtractionError"
    except LLMExtractionError:
        pass


def test_classify_and_extract_wraps_api_errors() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(message="boom", request=request)
    extractor = _make_extractor(_FakeMessages(raise_error=error))

    try:
        extractor.classify_and_extract(subject="s", sender="f", date="d", body="b")
        assert False, "expected LLMExtractionError"
    except LLMExtractionError:
        pass
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_llm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.client'`

- [ ] **Step 5: Write the client**

Create `backend/app/llm/client.py`:

```python
from typing import Protocol

import anthropic

from app.core.config import settings
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.schemas import EmailExtraction


class Extractor(Protocol):
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction: ...


class LLMExtractionError(Exception):
    """The LLM call failed, or returned no usable structured output."""


class AnthropicExtractor:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction:
        try:
            response = self._client.messages.parse(
                model=settings.llm_model,
                max_tokens=1024,
                output_config={"effort": "low"},
                system=SYSTEM_PROMPT,
                output_format=EmailExtraction,
                messages=[
                    {
                        "role": "user",
                        "content": f"Subject: {subject}\nFrom: {sender}\nDate: {date}\n\n{body}",
                    }
                ],
            )
        except anthropic.APIError as exc:
            raise LLMExtractionError(f"Anthropic API call failed: {exc}") from exc

        if response.parsed_output is None:
            raise LLMExtractionError("Anthropic response did not include structured output")

        return response.parsed_output
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_llm_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass (87 existing + 4 new = 91).

- [ ] **Step 8: Commit**

```bash
git add backend/app/llm/ backend/tests/test_llm_client.py
git commit -m "feat(backend): Anthropic-backed classify_and_extract behind an Extractor protocol

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Gmail message body fetching (extends Phase 3's `google_api.py`)

**Files:**
- Modify: `backend/app/gmail/google_api.py`
- Test: `backend/tests/test_gmail_google_api.py`

**Interfaces:**
- Produces: `app.gmail.google_api.get_message_body(access_token: str, message_id: str) -> str` (raises `GoogleApiError` on a non-200 response), `app.gmail.google_api.BODY_MAX_CHARS` (constant, `4000`).
- Consumes: nothing new — reuses this file's existing `GMAIL_API_BASE`, `_TIMEOUT`, `GoogleApiError`.

This function is additive — none of Phase 3's existing functions (`get_message_summary`, `list_message_ids`, etc.) change.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_gmail_google_api.py` (append — do not remove existing tests):

```python
import base64


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_get_message_body_extracts_plain_text_part(monkeypatch) -> None:
    payload = {
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Thanks for applying to Acme.")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>Thanks for applying to Acme.</p>")}},
            ],
        }
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message_body("token", "m1") == "Thanks for applying to Acme."


def test_get_message_body_falls_back_to_html_and_strips_tags(monkeypatch) -> None:
    payload = {
        "payload": {"mimeType": "text/html", "body": {"data": _b64("<p>Hello <b>World</b></p>")}}
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message_body("token", "m1") == "Hello World"


def test_get_message_body_strips_quoted_replies(monkeypatch) -> None:
    raw = (
        "Please see below.\n\n"
        "On Mon, Jan 1, 2026 at 1:00 PM wrote:\n"
        "> old message\n"
        "> more old"
    )
    payload = {"payload": {"mimeType": "text/plain", "body": {"data": _b64(raw)}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message_body("token", "m1") == "Please see below."


def test_get_message_body_truncates_long_bodies(monkeypatch) -> None:
    long_text = "a" * 5000
    payload = {"payload": {"mimeType": "text/plain", "body": {"data": _b64(long_text)}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    result = google_api.get_message_body("token", "m1")
    assert len(result) == google_api.BODY_MAX_CHARS


def test_get_message_body_returns_empty_string_when_no_text_part(monkeypatch) -> None:
    payload = {"payload": {"mimeType": "image/png", "body": {"data": _b64("binarydata")}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message_body("token", "m1") == ""


def test_get_message_body_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.get_message_body("token", "m1")
```

This appends to the existing test file, which already has `import httpx`, `import pytest`, `from app.gmail import google_api`, and the `_FakeResponse` class from Task 4 of the Phase 3 plan — reuse them, don't redefine.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py -v -k get_message_body`
Expected: FAIL — `AttributeError: module 'app.gmail.google_api' has no attribute 'get_message_body'`

- [ ] **Step 3: Implement it**

Edit `backend/app/gmail/google_api.py`, adding these imports at the top (alongside the existing `import httpx`):

```python
import base64
import html
import re

import httpx
```

Add after the existing constants (`_TIMEOUT = 10.0`):

```python
BODY_MAX_CHARS = 4000

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_QUOTE_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)
_ON_WROTE_RE = re.compile(r"^On .+ wrote:\s*$", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")
```

Add at the end of the file:

```python
def _decode_part(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _find_text_part(payload: dict) -> tuple[str, str] | None:
    """Return (mime_type, decoded_text) for the first text/plain part found in this
    payload (recursing into multipart `parts`), falling back to text/html if no plain
    part exists anywhere. None if there's no text body at all."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if mime_type == "text/plain" and body_data:
        return ("text/plain", _decode_part(body_data))

    html_fallback = ("text/html", _decode_part(body_data)) if mime_type == "text/html" and body_data else None

    for part in payload.get("parts", []):
        found = _find_text_part(part)
        if found is None:
            continue
        if found[0] == "text/plain":
            return found
        if html_fallback is None:
            html_fallback = found

    return html_fallback


def _clean_text(raw: str, *, is_html: bool) -> str:
    text = _ON_WROTE_RE.sub("", raw)
    text = _QUOTE_LINE_RE.sub("", text)
    if is_html:
        text = _HTML_TAG_RE.sub(" ", text)
        text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:BODY_MAX_CHARS]


def get_message_body(access_token: str, message_id: str) -> str:
    # format=full (not Phase 3's format=metadata) — reliable extraction genuinely
    # needs body content. What's sent onward to the LLM is still minimized: cleaned
    # plain text only, truncated, never the raw MIME structure or attachments.
    response = httpx.get(
        f"{GMAIL_API_BASE}/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "full"},
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"message body fetch failed: {response.status_code}")

    found = _find_text_part(response.json().get("payload", {}))
    if found is None:
        return ""
    mime_type, text = found
    return _clean_text(text, is_html=(mime_type == "text/html"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_gmail_google_api.py -v`
Expected: PASS (14 tests — 8 existing + 6 new)

- [ ] **Step 5: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass (91 + 6 = 97).

- [ ] **Step 6: Commit**

```bash
git add backend/app/gmail/google_api.py backend/tests/test_gmail_google_api.py
git commit -m "feat(backend): fetch and clean Gmail message bodies for the pipeline

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Data model — `processed_messages` table, `Application.source`, `other` status

**Files:**
- Create: `backend/app/pipeline/__init__.py`
- Create: `backend/app/pipeline/models.py`
- Create: `backend/alembic/versions/0005_add_pipeline_tables.py`
- Modify: `backend/app/applications/models.py`
- Modify: `backend/app/applications/schemas.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_pipeline_models.py`
- Test: `backend/tests/test_applications_source.py`

**Interfaces:**
- Produces: `app.pipeline.models.ProcessedMessage` (columns: `id`, `user_id`, `gmail_message_id`, `subject`, `sender`, `message_date`, `snippet`, `is_job_related`, `confidence`, `extracted_company`, `extracted_position`, `extracted_status`, `extracted_status_date`, `matched_application_id`, `proposed_action`, `review_status`, `created_at`, `updated_at`).
- Produces: `app.applications.models.Application.source: str` (`"manual"` or `"gmail"`), `ApplicationStatus.other`.
- Produces: `app.applications.schemas.ApplicationRead.source: Literal["manual", "gmail"]`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_applications_source.py`:

```python
from app.applications.models import Application, ApplicationStatus


def test_new_application_defaults_to_manual_source(db_session, user) -> None:
    application = Application(user_id=user.id, company="Acme", position="SWE")
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    assert application.source == "manual"


def test_application_source_can_be_set_to_gmail(db_session, user) -> None:
    application = Application(
        user_id=user.id, company="Acme", position="SWE", source="gmail"
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    assert application.source == "gmail"


def test_other_is_a_valid_status(db_session, user) -> None:
    application = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.other
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    assert application.status == ApplicationStatus.other
```

Create `backend/tests/test_pipeline_models.py`:

```python
from datetime import date

from app.pipeline.models import ProcessedMessage


def _make_message(user, **overrides) -> ProcessedMessage:
    defaults = dict(
        user_id=user.id,
        gmail_message_id="msg-1",
        subject="Your application to Acme",
        sender="jobs@acme.com",
        message_date="Wed, 1 Jan 2026 00:00:00 +0000",
        snippet="Thanks for applying",
        is_job_related=True,
        confidence=0.9,
        review_status="auto_applied",
    )
    defaults.update(overrides)
    return ProcessedMessage(**defaults)


def test_create_and_load_processed_message(db_session, user) -> None:
    message = _make_message(user)
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    assert message.id is not None
    assert message.extracted_company is None
    assert message.created_at is not None


def test_gmail_message_id_unique_per_user_but_not_globally(db_session, user, other_user) -> None:
    db_session.add(_make_message(user, gmail_message_id="shared-id"))
    db_session.commit()
    # Same gmail_message_id, different user — allowed (unique constraint is composite)
    db_session.add(_make_message(other_user, gmail_message_id="shared-id"))
    db_session.commit()


def test_duplicate_gmail_message_id_for_same_user_raises(db_session, user) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    db_session.add(_make_message(user, gmail_message_id="dup-id"))
    db_session.commit()

    db_session.add(_make_message(user, gmail_message_id="dup-id"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_applications_source.py tests/test_pipeline_models.py -v`
Expected: FAIL — `AttributeError`/`ModuleNotFoundError` (no `source` column, no `ApplicationStatus.other`, no `app.pipeline.models`)

- [ ] **Step 3: Update the `Application` model**

Edit `backend/app/applications/models.py`:

```python
class ApplicationStatus(enum.StrEnum):
    applied = "applied"
    oa = "oa"
    interview = "interview"
    rejected = "rejected"
    offer = "offer"
    withdrawn = "withdrawn"
    other = "other"
```

```python
    applied_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")

    owner: Mapped["User"] = relationship(back_populates="applications")
```

- [ ] **Step 4: Update `ApplicationRead`**

Edit `backend/app/applications/schemas.py` — add the import and the field:

```python
from datetime import date, datetime
from typing import Literal
from uuid import UUID
```

```python
class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company: str
    position: str
    status: ApplicationStatus
    applied_at: date | None
    source: Literal["manual", "gmail"]
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 5: Create the `ProcessedMessage` model**

Create `backend/app/pipeline/__init__.py` (empty file).

Create `backend/app/pipeline/models.py`:

```python
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class ProcessedMessage(TimestampMixin, Base):
    __tablename__ = "processed_messages"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "gmail_message_id", name="uq_processed_messages_user_id_gmail_message_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gmail_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    sender: Mapped[str] = mapped_column(String(998), nullable=False)
    message_date: Mapped[str] = mapped_column(String(255), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    is_job_related: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extracted_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extracted_position: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extracted_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extracted_status_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    matched_application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    proposed_action: Mapped[str | None] = mapped_column(String(10), nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False)
```

(No ORM relationships to `User`/`Application` — this codebase's established pattern, seen in `app/gmail/service.py`, is explicit `select()`/`db.get()` lookups rather than relationship traversal for cross-package references.)

- [ ] **Step 6: Write the migration**

Create `backend/alembic/versions/0005_add_pipeline_tables.py`:

```python
"""add pipeline tables and application source/other-status

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE application_status ADD VALUE IF NOT EXISTS 'other'")

    op.add_column(
        "applications",
        sa.Column("source", sa.String(length=10), nullable=False, server_default="manual"),
    )

    op.create_table(
        "processed_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("sender", sa.String(length=998), nullable=False),
        sa.Column("message_date", sa.String(length=255), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("is_job_related", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extracted_company", sa.String(length=255), nullable=True),
        sa.Column("extracted_position", sa.String(length=255), nullable=True),
        sa.Column("extracted_status", sa.String(length=50), nullable=True),
        sa.Column("extracted_status_date", sa.Date(), nullable=True),
        sa.Column("matched_application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposed_action", sa.String(length=10), nullable=True),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index("ix_processed_messages_user_id", "processed_messages", ["user_id"])
    op.create_unique_constraint(
        "uq_processed_messages_user_id_gmail_message_id",
        "processed_messages",
        ["user_id", "gmail_message_id"],
    )
    op.create_foreign_key(
        "fk_processed_messages_user_id_users",
        "processed_messages",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_processed_messages_matched_application_id_applications",
        "processed_messages",
        "applications",
        ["matched_application_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_table("processed_messages")
    op.drop_column("applications", "source")
    # Postgres does not support removing a value from an enum type — the 'other'
    # value added by upgrade() is intentionally left in place.
```

Apply it to your local dev database: `cd backend && uv run alembic upgrade head`

- [ ] **Step 7: Register the model for mapper configuration**

Edit `backend/tests/conftest.py`:

```python
# Models must be imported so their tables are registered on Base.metadata.
from app.applications import models  # noqa: F401
from app.gmail import models as gmail_models  # noqa: F401
from app.pipeline import models as pipeline_models  # noqa: F401
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_applications_source.py tests/test_pipeline_models.py -v`
Expected: PASS (6 tests)

- [ ] **Step 9: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass (97 + 6 = 103) — this also proves migration `0005` applies cleanly on top of `0001`-`0004`, and that the existing `application_status`-dependent tests (Phase 1 CRUD) still work with the added enum value.

- [ ] **Step 10: Commit**

```bash
git add backend/app/pipeline/__init__.py backend/app/pipeline/models.py \
        backend/alembic/versions/0005_add_pipeline_tables.py \
        backend/app/applications/models.py backend/app/applications/schemas.py \
        backend/tests/conftest.py backend/tests/test_applications_source.py \
        backend/tests/test_pipeline_models.py
git commit -m "feat(backend): ProcessedMessage model, Application.source, 'other' status

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Matching

**Files:**
- Create: `backend/app/pipeline/matching.py`
- Test: `backend/tests/test_pipeline_matching.py`

**Interfaces:**
- Consumes: `app.applications.models.Application` (existing).
- Produces: `app.pipeline.matching.normalize(text: str) -> str`, `app.pipeline.matching.MatchResult` (dataclass: `application: Application | None`, `action: Literal["create","update"]`, `ambiguous: bool`), `app.pipeline.matching.find_candidate(db, user_id, company: str, position: str) -> MatchResult`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pipeline_matching.py`:

```python
from app.applications.models import Application, ApplicationStatus
from app.pipeline import matching


def _make_app(db_session, user, *, company, position, source="manual") -> Application:
    application = Application(
        user_id=user.id,
        company=company,
        position=position,
        status=ApplicationStatus.applied,
        source=source,
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)
    return application


def test_normalize_strips_suffixes_and_punctuation() -> None:
    assert matching.normalize("Acme, Inc.") == matching.normalize("ACME")


def test_find_candidate_returns_create_when_no_applications_exist(db_session, user) -> None:
    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")
    assert result.action == "create"
    assert result.application is None
    assert result.ambiguous is False


def test_find_candidate_returns_update_for_strong_unambiguous_match(db_session, user) -> None:
    existing = _make_app(db_session, user, company="Acme Inc", position="Software Engineer")
    _make_app(db_session, user, company="Globex", position="Marketing Manager")

    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")

    assert result.action == "update"
    assert result.application.id == existing.id
    assert result.ambiguous is False


def test_find_candidate_flags_ambiguous_for_a_mid_range_score(db_session, user) -> None:
    _make_app(db_session, user, company="Acme Corp", position="Backend Developer")

    result = matching.find_candidate(db_session, user.id, "Acme", "Backend Engineer")

    assert result.ambiguous is True


def test_find_candidate_flags_ambiguous_when_two_candidates_are_close(db_session, user) -> None:
    _make_app(db_session, user, company="Acme Inc", position="Software Engineer I")
    _make_app(db_session, user, company="Acme Inc", position="Software Engineer II")

    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")

    assert result.ambiguous is True


def test_find_candidate_returns_create_when_scores_are_all_low(db_session, user) -> None:
    _make_app(db_session, user, company="Totally Different Co", position="Marketing Manager")

    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")

    assert result.action == "create"
    assert result.ambiguous is False


def test_find_candidate_scopes_to_the_given_user(db_session, user, other_user) -> None:
    _make_app(db_session, other_user, company="Acme Inc", position="Software Engineer")

    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")

    assert result.action == "create"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_pipeline_matching.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.matching'`

- [ ] **Step 3: Implement matching**

Create `backend/app/pipeline/matching.py`:

```python
import re
from dataclasses import dataclass
from uuid import UUID

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application

STRONG_MATCH_THRESHOLD = 85
NO_MATCH_THRESHOLD = 60
AMBIGUOUS_MARGIN = 10

_SUFFIX_PATTERN = re.compile(r"\b(inc|llc|corp|corporation|ltd|co)\.?\b", re.IGNORECASE)
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = _SUFFIX_PATTERN.sub("", text)
    text = _PUNCTUATION_PATTERN.sub("", text)
    text = _WHITESPACE_PATTERN.sub(" ", text)
    return text.strip().lower()


@dataclass
class MatchResult:
    application: Application | None
    action: str
    ambiguous: bool


def find_candidate(db: Session, user_id: UUID, company: str, position: str) -> MatchResult:
    target = normalize(f"{company} {position}")
    candidates = list(db.scalars(select(Application).where(Application.user_id == user_id)))

    scored = sorted(
        (
            (fuzz.ratio(target, normalize(f"{app.company} {app.position}")), app)
            for app in candidates
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    if not scored or scored[0][0] < NO_MATCH_THRESHOLD:
        return MatchResult(application=None, action="create", ambiguous=False)

    top_score, top_app = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else 0

    if top_score >= STRONG_MATCH_THRESHOLD and (top_score - runner_up_score) >= AMBIGUOUS_MARGIN:
        return MatchResult(application=top_app, action="update", ambiguous=False)

    return MatchResult(application=top_app, action="update", ambiguous=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_pipeline_matching.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass (103 + 7 = 110).

- [ ] **Step 6: Commit**

```bash
git add backend/app/pipeline/matching.py backend/tests/test_pipeline_matching.py
git commit -m "feat(backend): fuzzy matching for job-email-to-application linking

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Pipeline service — `process_inbox` and the decision logic

**Files:**
- Create: `backend/app/pipeline/exceptions.py`
- Create: `backend/app/pipeline/schemas.py`
- Create: `backend/app/pipeline/service.py`
- Test: `backend/tests/test_pipeline_service.py`

**Interfaces:**
- Consumes: `app.gmail.service.get_connection`/`get_valid_access_token` (existing), `app.gmail.google_api.list_message_ids`/`get_message_summary`/`get_message_body`/`GoogleApiError` (existing + Task 3), `app.gmail.exceptions.GmailNotConnected` (existing), `app.llm.client.Extractor`/`LLMExtractionError` (Task 2), `app.llm.schemas.EmailExtraction` (Task 2), `app.pipeline.matching.find_candidate` (Task 5), `app.pipeline.models.ProcessedMessage` (Task 4), `app.applications.models.Application`/`ApplicationStatus` (existing + Task 4).
- Produces: `app.pipeline.exceptions.ReviewItemNotFound(item_id)`, `app.pipeline.schemas.ProcessResult` (`processed`, `auto_applied`, `queued_for_review`, `ignored`: all `int`), `app.pipeline.service.process_inbox(db, user_id, extractor: Extractor) -> ProcessResult` (raises `GmailNotConnected`). Task 7 adds `list_review_queue`/`approve_review_item`/`reject_review_item` to this same `service.py` and `schemas.py`.

- [ ] **Step 1: Write the exception and schema**

Create `backend/app/pipeline/exceptions.py`:

```python
from uuid import UUID


class ReviewItemNotFound(Exception):
    def __init__(self, item_id: UUID) -> None:
        self.item_id = item_id
        super().__init__(f"Review item {item_id} not found")
```

Create `backend/app/pipeline/schemas.py`:

```python
from datetime import date

from pydantic import BaseModel


class ProcessResult(BaseModel):
    processed: int
    auto_applied: int
    queued_for_review: int
    ignored: int
```

(Task 7 appends `ReviewItem` and `ReviewDecision` to this file.)

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_pipeline_service.py`:

```python
from uuid import uuid4

import pytest

from app.applications.models import Application, ApplicationStatus
from app.core.config import settings
from app.gmail import google_api
from app.gmail.crypto import encrypt_token
from app.gmail.exceptions import GmailNotConnected
from app.gmail.models import GmailConnection
from app.llm.schemas import EmailExtraction
from app.pipeline import service
from app.pipeline.models import ProcessedMessage
from datetime import datetime, timedelta, timezone


class _FakeExtractor:
    def __init__(self, results: dict[str, EmailExtraction]) -> None:
        self._results = results
        self.calls: list[str] = []

    def classify_and_extract(self, *, subject, sender, date, body):
        self.calls.append(subject)
        return self._results[subject]


def _connect_gmail(db_session, user) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("access"),
        refresh_token_encrypted=encrypt_token("refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def _make_summary(message_id: str, subject: str) -> dict:
    return {"id": message_id, "subject": subject, "from_": "jobs@acme.com", "date": "d", "snippet": "s"}


def test_process_inbox_raises_when_not_connected(db_session, user) -> None:
    fake_extractor = _FakeExtractor({})
    with pytest.raises(GmailNotConnected):
        service.process_inbox(db_session, user.id, fake_extractor)


def test_process_inbox_ignores_non_job_email(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Newsletter"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor({"Newsletter": EmailExtraction(is_job_related=False, confidence=0.99)})

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.processed == 1
    assert result.ignored == 1
    assert result.auto_applied == 0
    assert result.queued_for_review == 0
    stored = db_session.query(ProcessedMessage).one()
    assert stored.review_status == "ignored"


def test_process_inbox_auto_creates_a_new_application_when_confident(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "App received"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "App received": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.auto_applied == 1
    app = db_session.query(Application).one()
    assert app.company == "Acme"
    assert app.source == "gmail"
    stored = db_session.query(ProcessedMessage).one()
    assert stored.review_status == "auto_applied"
    assert stored.matched_application_id == app.id


def test_process_inbox_auto_updates_a_gmail_sourced_application(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.applied, source="gmail"
    )
    db_session.add(existing)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Interview invite"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Interview invite": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="interview"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.auto_applied == 1
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.interview


def test_process_inbox_always_queues_when_touching_a_manual_application(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.applied, source="manual"
    )
    db_session.add(existing)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Interview invite"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Interview invite": EmailExtraction(
                is_job_related=True, confidence=0.99, company="Acme", position="SWE", status="interview"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.queued_for_review == 1
    assert result.auto_applied == 0
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.applied  # untouched


def test_process_inbox_queues_low_confidence_extractions(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Maybe a job email"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Maybe a job email": EmailExtraction(
                is_job_related=True, confidence=0.4, company="Acme", position="SWE", status="applied"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.queued_for_review == 1
    assert db_session.query(Application).count() == 0


def test_process_inbox_never_regresses_a_specific_status_to_other(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.interview, source="gmail"
    )
    db_session.add(existing)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Some update"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Some update": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="other"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.ignored == 1
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.interview  # untouched


def test_process_inbox_respects_the_batch_limit(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    captured_limits = []

    def fake_list_message_ids(token, limit):
        captured_limits.append(limit)
        return []

    monkeypatch.setattr(google_api, "list_message_ids", fake_list_message_ids)
    extractor = _FakeExtractor({})

    service.process_inbox(db_session, user.id, extractor)

    assert captured_limits == [settings.pipeline_batch_limit]


def test_process_inbox_is_idempotent(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "App received"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "App received": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
            )
        }
    )

    service.process_inbox(db_session, user.id, extractor)
    second_result = service.process_inbox(db_session, user.id, extractor)

    assert second_result.processed == 0
    assert len(extractor.calls) == 1  # never re-extracted
    assert db_session.query(Application).count() == 1
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_pipeline_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.service'`

- [ ] **Step 4: Implement the service**

Create `backend/app/pipeline/service.py`:

```python
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application, ApplicationStatus
from app.core.config import settings
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.exceptions import GmailNotConnected
from app.llm.client import Extractor, LLMExtractionError
from app.llm.schemas import EmailExtraction
from app.pipeline import matching
from app.pipeline.models import ProcessedMessage
from app.pipeline.schemas import ProcessResult

logger = logging.getLogger(__name__)

_MORE_SPECIFIC_STATUSES = {"applied", "oa", "interview", "rejected", "offer"}


def process_inbox(db: Session, user_id: UUID, extractor: Extractor) -> ProcessResult:
    connection = gmail_service.get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    access_token = gmail_service.get_valid_access_token(db, connection)
    message_ids = google_api.list_message_ids(access_token, limit=settings.pipeline_batch_limit)

    already_processed = set(
        db.scalars(
            select(ProcessedMessage.gmail_message_id).where(
                ProcessedMessage.user_id == user_id,
                ProcessedMessage.gmail_message_id.in_(message_ids),
            )
        )
    )
    new_message_ids = [mid for mid in message_ids if mid not in already_processed]

    auto_applied = 0
    queued_for_review = 0
    ignored = 0

    for message_id in new_message_ids:
        summary = google_api.get_message_summary(access_token, message_id)
        try:
            body = google_api.get_message_body(access_token, message_id)
            extraction = extractor.classify_and_extract(
                subject=summary["subject"],
                sender=summary["from_"],
                date=summary["date"],
                body=body,
            )
        except (google_api.GoogleApiError, LLMExtractionError):
            logger.warning("Skipping message %s: fetch or extraction failed", message_id)
            continue

        review_status, matched_application_id, proposed_action = _apply_decision(
            db, user_id, extraction
        )

        db.add(
            ProcessedMessage(
                user_id=user_id,
                gmail_message_id=message_id,
                subject=summary["subject"],
                sender=summary["from_"],
                message_date=summary["date"],
                snippet=summary["snippet"],
                is_job_related=extraction.is_job_related,
                confidence=extraction.confidence,
                extracted_company=extraction.company,
                extracted_position=extraction.position,
                extracted_status=extraction.status,
                extracted_status_date=extraction.status_date,
                matched_application_id=matched_application_id,
                proposed_action=proposed_action,
                review_status=review_status,
            )
        )
        db.commit()

        if review_status == "auto_applied":
            auto_applied += 1
        elif review_status == "pending_review":
            queued_for_review += 1
        else:
            ignored += 1

    return ProcessResult(
        processed=len(new_message_ids),
        auto_applied=auto_applied,
        queued_for_review=queued_for_review,
        ignored=ignored,
    )


def _apply_decision(
    db: Session, user_id: UUID, extraction: EmailExtraction
) -> tuple[str, UUID | None, str | None]:
    if not extraction.is_job_related:
        return "ignored", None, None

    match = matching.find_candidate(db, user_id, extraction.company or "", extraction.position or "")

    if (
        match.application is not None
        and not match.ambiguous
        and extraction.status == "other"
        and match.application.status.value in _MORE_SPECIFIC_STATUSES
    ):
        return "ignored", match.application.id, None

    high_confidence = extraction.confidence >= settings.llm_confidence_threshold
    touches_manual = match.application is not None and match.application.source == "manual"

    if touches_manual or match.ambiguous or not high_confidence:
        return (
            "pending_review",
            match.application.id if match.application else None,
            match.action,
        )

    if match.action == "create":
        new_application = Application(
            user_id=user_id,
            company=extraction.company or "",
            position=extraction.position or "",
            status=ApplicationStatus(extraction.status or "applied"),
            applied_at=extraction.status_date,
            source="gmail",
        )
        db.add(new_application)
        db.flush()
        return "auto_applied", new_application.id, "create"

    application = match.application
    if extraction.status is not None:
        application.status = ApplicationStatus(extraction.status)
    if extraction.status_date is not None:
        application.applied_at = extraction.status_date
    return "auto_applied", application.id, "update"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_pipeline_service.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass (110 + 9 = 119).

- [ ] **Step 7: Commit**

```bash
git add backend/app/pipeline/exceptions.py backend/app/pipeline/schemas.py \
        backend/app/pipeline/service.py backend/tests/test_pipeline_service.py
git commit -m "feat(backend): process_inbox orchestration and the manual/gmail trust-model decision logic

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Pipeline service — review queue (list, approve, reject)

**Files:**
- Modify: `backend/app/pipeline/schemas.py`
- Modify: `backend/app/pipeline/service.py`
- Test: `backend/tests/test_pipeline_review.py`

**Interfaces:**
- Consumes: `app.pipeline.models.ProcessedMessage`, `app.pipeline.exceptions.ReviewItemNotFound`, `app.applications.models.Application`/`ApplicationStatus` (all existing from Tasks 4/6).
- Produces: `app.pipeline.schemas.ReviewItem` (from-attributes model: `id`, `subject`, `sender`, `snippet`, `confidence`, `proposed_action`, `matched_application_id`, `extracted_company`, `extracted_position`, `extracted_status`, `extracted_status_date`, `created_at`), `app.pipeline.schemas.ReviewDecision` (`company`, `position`, `status`, `status_date`: all optional), `app.pipeline.service.list_review_queue(db, user_id) -> list[ProcessedMessage]`, `app.pipeline.service.approve_review_item(db, user_id, item_id, decision: ReviewDecision) -> Application` (raises `ReviewItemNotFound`), `app.pipeline.service.reject_review_item(db, user_id, item_id) -> None` (raises `ReviewItemNotFound`).

- [ ] **Step 1: Add the schemas**

Edit `backend/app/pipeline/schemas.py`, adding:

```python
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProcessResult(BaseModel):
    processed: int
    auto_applied: int
    queued_for_review: int
    ignored: int


class ReviewItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject: str
    sender: str
    snippet: str
    confidence: float
    proposed_action: str | None
    matched_application_id: UUID | None
    extracted_company: str | None
    extracted_position: str | None
    extracted_status: str | None
    extracted_status_date: date | None
    created_at: datetime


class ReviewDecision(BaseModel):
    company: str | None = None
    position: str | None = None
    status: str | None = None
    status_date: date | None = None
```

(This replaces the file's current top-of-file imports — keep `ProcessResult` exactly as Task 6 defined it.)

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_pipeline_review.py`:

```python
from uuid import uuid4

import pytest

from app.applications.models import Application, ApplicationStatus
from app.pipeline import service
from app.pipeline.exceptions import ReviewItemNotFound
from app.pipeline.models import ProcessedMessage
from app.pipeline.schemas import ReviewDecision


def _make_pending_item(db_session, user, **overrides) -> ProcessedMessage:
    defaults = dict(
        user_id=user.id,
        gmail_message_id="m1",
        subject="Interview invite",
        sender="jobs@acme.com",
        message_date="d",
        snippet="s",
        is_job_related=True,
        confidence=0.5,
        extracted_company="Acme",
        extracted_position="SWE",
        extracted_status="interview",
        proposed_action="create",
        review_status="pending_review",
    )
    defaults.update(overrides)
    item = ProcessedMessage(**defaults)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def test_list_review_queue_returns_only_pending_items_for_this_user(db_session, user, other_user) -> None:
    pending = _make_pending_item(db_session, user)
    _make_pending_item(db_session, user, gmail_message_id="m2", review_status="approved")
    _make_pending_item(db_session, other_user, gmail_message_id="m3")

    items = service.list_review_queue(db_session, user.id)

    assert [item.id for item in items] == [pending.id]


def test_approve_creates_application_from_proposed_create(db_session, user) -> None:
    item = _make_pending_item(db_session, user)

    application = service.approve_review_item(db_session, user.id, item.id, ReviewDecision())

    assert application.company == "Acme"
    assert application.status == ApplicationStatus.interview
    assert application.source == "gmail"
    db_session.refresh(item)
    assert item.review_status == "approved"
    assert item.matched_application_id == application.id


def test_approve_applies_edited_fields_over_the_extraction(db_session, user) -> None:
    item = _make_pending_item(db_session, user)

    application = service.approve_review_item(
        db_session, user.id, item.id, ReviewDecision(company="Acme Corp")
    )

    assert application.company == "Acme Corp"


def test_approve_updates_an_existing_matched_application(db_session, user) -> None:
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.applied, source="manual"
    )
    db_session.add(existing)
    db_session.commit()
    item = _make_pending_item(
        db_session, user, proposed_action="update", matched_application_id=existing.id
    )

    application = service.approve_review_item(db_session, user.id, item.id, ReviewDecision())

    assert application.id == existing.id
    assert application.status == ApplicationStatus.interview
    assert application.source == "manual"  # approving an update never changes provenance


def test_reject_marks_item_rejected_without_touching_applications(db_session, user) -> None:
    item = _make_pending_item(db_session, user)

    service.reject_review_item(db_session, user.id, item.id)

    db_session.refresh(item)
    assert item.review_status == "rejected"
    assert db_session.query(Application).count() == 0


def test_approve_raises_for_unknown_item(db_session, user) -> None:
    with pytest.raises(ReviewItemNotFound):
        service.approve_review_item(db_session, user.id, uuid4(), ReviewDecision())


def test_reject_raises_for_unknown_item(db_session, user) -> None:
    with pytest.raises(ReviewItemNotFound):
        service.reject_review_item(db_session, user.id, uuid4())


def test_approve_raises_when_item_belongs_to_another_user(db_session, user, other_user) -> None:
    item = _make_pending_item(db_session, other_user)

    with pytest.raises(ReviewItemNotFound):
        service.approve_review_item(db_session, user.id, item.id, ReviewDecision())


def test_approve_raises_for_already_reviewed_item(db_session, user) -> None:
    item = _make_pending_item(db_session, user, review_status="approved")

    with pytest.raises(ReviewItemNotFound):
        service.approve_review_item(db_session, user.id, item.id, ReviewDecision())
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_pipeline_review.py -v`
Expected: FAIL — `AttributeError: module 'app.pipeline.service' has no attribute 'list_review_queue'`

- [ ] **Step 4: Implement the review queue functions**

Edit `backend/app/pipeline/service.py` — add a new import line, and extend the existing `app.pipeline.schemas` import line (do not add a second, duplicate import of `ProcessResult`):

```python
from app.pipeline.exceptions import ReviewItemNotFound
```

Change the existing line `from app.pipeline.schemas import ProcessResult` to:

```python
from app.pipeline.schemas import ProcessResult, ReviewDecision
```

Append to the end of the file:

```python
def list_review_queue(db: Session, user_id: UUID) -> list[ProcessedMessage]:
    return list(
        db.scalars(
            select(ProcessedMessage)
            .where(
                ProcessedMessage.user_id == user_id,
                ProcessedMessage.review_status == "pending_review",
            )
            .order_by(ProcessedMessage.created_at.desc())
        )
    )


def _get_pending_item(db: Session, user_id: UUID, item_id: UUID) -> ProcessedMessage:
    item = db.scalars(
        select(ProcessedMessage).where(
            ProcessedMessage.id == item_id,
            ProcessedMessage.user_id == user_id,
            ProcessedMessage.review_status == "pending_review",
        )
    ).one_or_none()
    if item is None:
        raise ReviewItemNotFound(item_id)
    return item


def approve_review_item(
    db: Session, user_id: UUID, item_id: UUID, decision: ReviewDecision
) -> Application:
    item = _get_pending_item(db, user_id, item_id)

    company = decision.company or item.extracted_company or ""
    position = decision.position or item.extracted_position or ""
    status_value = decision.status or item.extracted_status or "applied"
    status_date = decision.status_date or item.extracted_status_date

    if item.proposed_action == "update" and item.matched_application_id is not None:
        application = db.get(Application, item.matched_application_id)
        if application is None:
            raise ReviewItemNotFound(item_id)
        if company:
            application.company = company
        if position:
            application.position = position
        application.status = ApplicationStatus(status_value)
        if status_date is not None:
            application.applied_at = status_date
    else:
        application = Application(
            user_id=user_id,
            company=company,
            position=position,
            status=ApplicationStatus(status_value),
            applied_at=status_date,
            source="gmail",
        )
        db.add(application)
        db.flush()

    item.review_status = "approved"
    item.matched_application_id = application.id
    db.commit()
    db.refresh(application)
    return application


def reject_review_item(db: Session, user_id: UUID, item_id: UUID) -> None:
    item = _get_pending_item(db, user_id, item_id)
    item.review_status = "rejected"
    db.commit()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_pipeline_review.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass (119 + 9 = 128).

- [ ] **Step 7: Commit**

```bash
git add backend/app/pipeline/schemas.py backend/app/pipeline/service.py \
        backend/tests/test_pipeline_review.py
git commit -m "feat(backend): review queue list/approve/reject

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Router and app wiring

**Files:**
- Create: `backend/app/pipeline/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_pipeline_router.py`

**Interfaces:**
- Consumes: `app.pipeline.service.*` (Tasks 6/7), `app.pipeline.exceptions.ReviewItemNotFound` (Task 6), `app.llm.client.AnthropicExtractor` (Task 2), `app.auth.dependencies.get_current_user`, `app.applications.schemas.ApplicationRead` (existing, modified in Task 4).
- Produces: `GET/POST /api/v1/pipeline/{process, review, review/{id}/approve, review/{id}/reject}` per spec §8.

- [ ] **Step 1: Write the router**

Create `backend/app/pipeline/router.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.applications.schemas import ApplicationRead
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.llm.client import AnthropicExtractor
from app.pipeline import service
from app.pipeline.schemas import ProcessResult, ReviewDecision, ReviewItem
from app.users.models import User

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/process", response_model=ProcessResult)
def pipeline_process(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> ProcessResult:
    return service.process_inbox(db, current_user.id, AnthropicExtractor())


@router.get("/review", response_model=list[ReviewItem])
def pipeline_review_list(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list:
    return service.list_review_queue(db, current_user.id)


@router.post("/review/{item_id}/approve", response_model=ApplicationRead)
def pipeline_review_approve(
    item_id: UUID,
    decision: ReviewDecision = ReviewDecision(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.approve_review_item(db, current_user.id, item_id, decision)


@router.post("/review/{item_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def pipeline_review_reject(
    item_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    service.reject_review_item(db, current_user.id, item_id)
```

- [ ] **Step 2: Write the failing router tests**

Create `backend/tests/test_pipeline_router.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.applications.models import Application, ApplicationStatus
from app.gmail import google_api
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.llm import client as llm_client
from app.llm.schemas import EmailExtraction
from app.pipeline.models import ProcessedMessage

BASE = "/api/v1/pipeline"


@pytest.fixture
def connected_gmail(db_session, user) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("access"),
        refresh_token_encrypted=encrypt_token("refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    return connection


@pytest.fixture
def pending_item(db_session, user) -> ProcessedMessage:
    item = ProcessedMessage(
        user_id=user.id,
        gmail_message_id="m1",
        subject="Interview invite",
        sender="jobs@acme.com",
        message_date="d",
        snippet="s",
        is_job_related=True,
        confidence=0.5,
        extracted_company="Acme",
        extracted_position="SWE",
        extracted_status="interview",
        proposed_action="create",
        review_status="pending_review",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/process"),
        ("get", "/review"),
        ("post", "/review/00000000-0000-0000-0000-000000000000/approve"),
        ("post", "/review/00000000-0000-0000-0000-000000000000/reject"),
    ],
)
def test_pipeline_endpoints_require_authentication(client: TestClient, method, path) -> None:
    response = getattr(client, method)(f"{BASE}{path}")
    assert response.status_code == 401


def test_process_endpoint_returns_summary(
    auth_client: TestClient, connected_gmail, monkeypatch
) -> None:
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(
        google_api,
        "get_message_summary",
        lambda token, mid: {"id": mid, "subject": "App received", "from_": "jobs@acme.com", "date": "d", "snippet": "s"},
    )
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    monkeypatch.setattr(
        llm_client.AnthropicExtractor,
        "classify_and_extract",
        lambda self, **kwargs: EmailExtraction(
            is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
        ),
    )

    response = auth_client.post(f"{BASE}/process")

    assert response.status_code == 200
    body = response.json()
    assert body == {"processed": 1, "auto_applied": 1, "queued_for_review": 0, "ignored": 0}


def test_process_endpoint_requires_gmail_connection(auth_client: TestClient) -> None:
    response = auth_client.post(f"{BASE}/process")
    assert response.status_code == 404


def test_review_list_returns_pending_items(auth_client: TestClient, pending_item) -> None:
    response = auth_client.get(f"{BASE}/review")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(pending_item.id)
    assert body[0]["extracted_company"] == "Acme"


def test_review_approve_creates_application(auth_client: TestClient, pending_item) -> None:
    response = auth_client.post(f"{BASE}/review/{pending_item.id}/approve", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["company"] == "Acme"
    assert body["source"] == "gmail"


def test_review_approve_with_edits_overrides_extraction(auth_client: TestClient, pending_item) -> None:
    response = auth_client.post(
        f"{BASE}/review/{pending_item.id}/approve", json={"company": "Acme Corp"}
    )

    assert response.status_code == 200
    assert response.json()["company"] == "Acme Corp"


def test_review_reject_returns_204(auth_client: TestClient, pending_item) -> None:
    response = auth_client.post(f"{BASE}/review/{pending_item.id}/reject")
    assert response.status_code == 204


def test_review_approve_404_for_unknown_item(auth_client: TestClient) -> None:
    response = auth_client.post(
        f"{BASE}/review/00000000-0000-0000-0000-000000000000/approve", json={}
    )
    assert response.status_code == 404


def test_cross_user_isolation(auth_client: TestClient, other_auth_client: TestClient, pending_item) -> None:
    other_list = other_auth_client.get(f"{BASE}/review")
    assert other_list.json() == []

    other_approve = other_auth_client.post(f"{BASE}/review/{pending_item.id}/approve", json={})
    assert other_approve.status_code == 404
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_pipeline_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.router'`

- [ ] **Step 4: Wire the router and exception handler into `main.py`**

Edit `backend/app/main.py`:

```python
from app.applications.exceptions import ApplicationNotFound
from app.applications.router import router as applications_router
from app.auth.router import router as auth_router
from app.core.config import settings
from app.gmail.exceptions import GmailNotConnected
from app.gmail.google_api import GoogleApiError
from app.gmail.router import router as gmail_router
from app.pipeline.exceptions import ReviewItemNotFound
from app.pipeline.router import router as pipeline_router
```

```python
    @app.exception_handler(GoogleApiError)
    async def handle_google_api_error(
        request: Request, exc: GoogleApiError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": "Gmail request failed. Try reconnecting your Gmail account."},
        )

    @app.exception_handler(ReviewItemNotFound)
    async def handle_review_item_not_found(
        request: Request, exc: ReviewItemNotFound
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
```

```python
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(applications_router, prefix="/api/v1")
    app.include_router(gmail_router, prefix="/api/v1")
    app.include_router(pipeline_router, prefix="/api/v1")
```

- [ ] **Step 5: Run the router tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_pipeline_router.py -v`
Expected: PASS (12 tests, counting the 4 parametrized auth cases)

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: every test passes (128 + 12 = 140) — Phase 1-3 tests unaffected.

- [ ] **Step 7: Commit**

```bash
git add backend/app/pipeline/router.py backend/app/main.py backend/tests/test_pipeline_router.py
git commit -m "feat(backend): mount pipeline process/review endpoints

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: Frontend — Review Queue and "Process Inbox"

**Files:**
- Create: `frontend/src/types/pipeline.ts`
- Create: `frontend/src/api/pipeline.ts`
- Create: `frontend/src/components/ReviewQueue.tsx`
- Modify: `frontend/src/types/application.ts`
- Modify: `frontend/src/constants.ts`
- Modify: `frontend/src/components/ApplicationsPage.tsx`

**Interfaces:**
- Consumes: `../api/http`'s `parseResponse<T>` (existing), `useApplications()`'s existing `refetch: () => Promise<void>` (from `frontend/src/hooks/useApplications.ts` — already returned today, just not currently destructured by `ApplicationsPage`).
- Produces: `ProcessResult`, `ReviewItem`, `ReviewDecision` types; `processInbox(): Promise<ProcessResult>`, `getReviewQueue(): Promise<ReviewItem[]>`, `approveReviewItem(id: string, edits?: ReviewDecision): Promise<Application>`, `rejectReviewItem(id: string): Promise<void>`; `<ReviewQueue onApplicationsChanged={() => void} />` component.

No frontend test framework exists in this repo (consistent with Phase 1-3 precedent) — this task is verified via `npm run build`/`npm run lint` plus manual verification, same as Phase 3's frontend task.

- [ ] **Step 1: Add `source` to `Application` and `'other'` to the status type**

The backend's `ApplicationStatus` enum (Task 4) gained `other`, and `ApplicationRead` gained `source`. The frontend's `Application` type and its two `Record<ApplicationStatus, ...>` lookup tables in `constants.ts` must stay in sync, or `tsc -b` fails on an incomplete `Record`.

Edit `frontend/src/types/application.ts`:

```typescript
export const APPLICATION_STATUSES = [
  'applied',
  'oa',
  'interview',
  'rejected',
  'offer',
  'withdrawn',
  'other',
] as const

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number]

export interface Application {
  id: string
  company: string
  position: string
  status: ApplicationStatus
  applied_at: string | null
  source: 'manual' | 'gmail'
  created_at: string
  updated_at: string
}
```

(`ApplicationCreate`/`ApplicationUpdate` are unchanged — `source` is never client-settable, matching the backend's `ApplicationCreate` schema, which has no such field either.)

Edit `frontend/src/constants.ts`, adding the `other` entry to both records:

```typescript
export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  applied: 'Applied',
  oa: 'OA',
  interview: 'Interview',
  rejected: 'Rejected',
  offer: 'Offer',
  withdrawn: 'Withdrawn',
  other: 'Other',
}

export const STATUS_STYLES: Record<ApplicationStatus, string> = {
  applied: 'bg-blue-100 text-blue-800',
  oa: 'bg-purple-100 text-purple-800',
  interview: 'bg-amber-100 text-amber-800',
  rejected: 'bg-red-100 text-red-800',
  offer: 'bg-green-100 text-green-800',
  withdrawn: 'bg-gray-100 text-gray-700',
  other: 'bg-gray-100 text-gray-700',
}
```

(`other` becomes selectable in the existing manual `ApplicationForm` dropdown too, since it reads `APPLICATION_STATUSES` directly — a minor, harmless side effect: a user can deliberately mark something "Other" by hand if they want to, which is a reasonable option to have even though the pipeline is `other`'s primary source.)

- [ ] **Step 2: Add pipeline types**

Create `frontend/src/types/pipeline.ts`:

```typescript
export interface ProcessResult {
  processed: number
  auto_applied: number
  queued_for_review: number
  ignored: number
}

export interface ReviewItem {
  id: string
  subject: string
  sender: string
  snippet: string
  confidence: number
  proposed_action: 'create' | 'update' | null
  matched_application_id: string | null
  extracted_company: string | null
  extracted_position: string | null
  extracted_status: string | null
  extracted_status_date: string | null
  created_at: string
}

export interface ReviewDecision {
  company?: string
  position?: string
  status?: string
  status_date?: string
}
```

- [ ] **Step 3: Add the pipeline API client**

Create `frontend/src/api/pipeline.ts`:

```typescript
import { parseResponse } from './http'
import type { Application } from '../types/application'
import type { ProcessResult, ReviewDecision, ReviewItem } from '../types/pipeline'

const BASE = '/api/v1/pipeline'

export function processInbox(): Promise<ProcessResult> {
  return fetch(`${BASE}/process`, { method: 'POST' }).then((r) => parseResponse<ProcessResult>(r))
}

export function getReviewQueue(): Promise<ReviewItem[]> {
  return fetch(`${BASE}/review`).then((r) => parseResponse<ReviewItem[]>(r))
}

export function approveReviewItem(id: string, edits: ReviewDecision = {}): Promise<Application> {
  return fetch(`${BASE}/review/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(edits),
  }).then((r) => parseResponse<Application>(r))
}

export function rejectReviewItem(id: string): Promise<void> {
  return fetch(`${BASE}/review/${id}/reject`, { method: 'POST' }).then((r) => parseResponse<void>(r))
}
```

- [ ] **Step 4: Build the Review Queue component**

Create `frontend/src/components/ReviewQueue.tsx`:

```tsx
import { useEffect, useState } from 'react'

import { approveReviewItem, getReviewQueue, rejectReviewItem } from '../api/pipeline'
import type { ReviewItem } from '../types/pipeline'

interface Props {
  onApplicationsChanged: () => void
}

export function ReviewQueue({ onApplicationsChanged }: Props) {
  const [items, setItems] = useState<ReviewItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editedCompany, setEditedCompany] = useState('')

  const refresh = () => {
    getReviewQueue()
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load review queue'))
  }

  useEffect(refresh, [])

  const handleApprove = async (item: ReviewItem) => {
    setError(null)
    try {
      const edits = editingId === item.id && editedCompany ? { company: editedCompany } : {}
      await approveReviewItem(item.id, edits)
      setEditingId(null)
      refresh()
      onApplicationsChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve item')
    }
  }

  const handleReject = async (item: ReviewItem) => {
    setError(null)
    try {
      await rejectReviewItem(item.id)
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reject item')
    }
  }

  if (items.length === 0) return null

  return (
    <section className="space-y-3 rounded border border-amber-200 bg-amber-50 p-4">
      <h2 className="text-sm font-semibold text-gray-900">
        Needs review ({items.length})
      </h2>

      {error && (
        <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>
      )}

      <ul className="space-y-3">
        {items.map((item) => (
          <li key={item.id} className="rounded border border-gray-200 bg-white p-3 text-sm">
            <p className="font-medium text-gray-900">{item.subject}</p>
            <p className="text-gray-500">{item.sender}</p>
            <p className="mt-1 text-gray-600">{item.snippet}</p>
            <p className="mt-1 text-gray-700">
              {item.proposed_action === 'update' ? 'Update' : 'Create'}: {item.extracted_company ?? '?'} —{' '}
              {item.extracted_position ?? '?'} ({item.extracted_status ?? 'unknown status'}) — confidence{' '}
              {Math.round(item.confidence * 100)}%
            </p>
            {editingId === item.id && (
              <input
                className="mt-2 w-full rounded border border-gray-300 px-2 py-1 text-sm"
                placeholder="Correct company name"
                value={editedCompany}
                onChange={(e) => setEditedCompany(e.target.value)}
              />
            )}
            <div className="mt-2 flex gap-2">
              <button
                onClick={() => handleApprove(item)}
                className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
              >
                Approve
              </button>
              <button
                onClick={() => {
                  setEditingId(item.id)
                  setEditedCompany(item.extracted_company ?? '')
                }}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Edit
              </button>
              <button
                onClick={() => handleReject(item)}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Reject
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
```

- [ ] **Step 5: Wire "Process Inbox" and the Review Queue into the dashboard**

`useApplications()` (`frontend/src/hooks/useApplications.ts`) already returns `refetch: () => Promise<void>` — `ApplicationsPage` just isn't destructuring it today. Replace the whole file:

```tsx
import { useState } from 'react'

import { processInbox } from '../api/pipeline'
import { useApplications } from '../hooks/useApplications'
import type { Application } from '../types/application'
import type { User } from '../types/user'
import type { ProcessResult } from '../types/pipeline'
import { ApplicationForm } from './ApplicationForm'
import { ApplicationList } from './ApplicationList'
import { GmailPanel } from './GmailPanel'
import { ReviewQueue } from './ReviewQueue'
import { UserMenu } from './UserMenu'

interface Props {
  user: User
  onLogout: () => void
}

export function ApplicationsPage({ user, onLogout }: Props) {
  const { applications, loading, error, refetch, create, update, remove } = useApplications()
  const [editing, setEditing] = useState<Application | null>(null)
  const [processResult, setProcessResult] = useState<ProcessResult | null>(null)
  const [processError, setProcessError] = useState<string | null>(null)
  const [processing, setProcessing] = useState(false)

  const handleProcessInbox = async () => {
    setProcessing(true)
    setProcessError(null)
    try {
      const result = await processInbox()
      setProcessResult(result)
      await refetch()
    } catch (err) {
      setProcessError(err instanceof Error ? err.message : 'Failed to process inbox')
    } finally {
      setProcessing(false)
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 sm:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Job Application Tracker</h1>
          <p className="text-sm text-gray-500">
            {applications.length} application{applications.length === 1 ? '' : 's'}
          </p>
        </div>
        <UserMenu user={user} onLogout={onLogout} />
      </header>

      <GmailPanel />

      <section className="space-y-2 rounded border border-gray-200 bg-white p-4">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-gray-900">Pipeline</h2>
          <button
            onClick={handleProcessInbox}
            disabled={processing}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {processing ? 'Processing…' : 'Process Inbox'}
          </button>
        </div>
        {processError && <p className="text-sm text-red-700">{processError}</p>}
        {processResult && (
          <p className="text-sm text-gray-600">
            Processed {processResult.processed}: {processResult.auto_applied} auto-applied,{' '}
            {processResult.queued_for_review} queued for review, {processResult.ignored} ignored.
          </p>
        )}
      </section>

      <ReviewQueue onApplicationsChanged={() => void refetch()} />

      {editing ? (
        <ApplicationForm
          key={editing.id}
          initial={editing}
          submitLabel="Save changes"
          onSubmit={async (data) => {
            await update(editing.id, data)
            setEditing(null)
          }}
          onCancel={() => setEditing(null)}
        />
      ) : (
        <ApplicationForm submitLabel="Add application" onSubmit={create} />
      )}

      {error && (
        <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <ApplicationList
          applications={applications}
          onEdit={setEditing}
          onDelete={(id) => {
            void remove(id)
          }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 6: Type-check and lint**

Run: `cd frontend && npm run build`
Expected: `tsc -b` and the Vite build both succeed with no type errors.

Run: `cd frontend && npm run lint`
Expected: no new lint errors from files this task added/touched.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/pipeline.ts frontend/src/types/application.ts \
        frontend/src/constants.ts frontend/src/api/pipeline.ts \
        frontend/src/components/ReviewQueue.tsx frontend/src/components/ApplicationsPage.tsx
git commit -m "feat(frontend): Process Inbox action and review queue UI

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 10: Evaluation scaffold

**Files:**
- Create: `backend/evaluation/__init__.py`
- Create: `backend/evaluation/dataset.jsonl`
- Create: `backend/evaluation/run_eval.py`

**Interfaces:** None consumed beyond `app.llm.client.AnthropicExtractor`/`Extractor` and `app.llm.schemas.EmailExtraction` (Task 2). Produces a runnable script; produces no importable interface other tasks depend on.

This task scaffolds the *shape* only. Per the spec (§10) and this plan's Global Constraints, the actual labeled dataset is deliberately not designed here — building it properly means invoking the `claude-api` skill's `build-eval` workflow, which runs an interview (what's graded, where examples come from, grading method, cost) and requires your explicit sign-off. That happens as a separate, interactive follow-up after this plan's tasks are done — not as a delegated, non-interactive step here.

- [ ] **Step 1: Create the package and a single example row**

Create `backend/evaluation/__init__.py` (empty file).

Create `backend/evaluation/dataset.jsonl` with one illustrative example (the real dataset is built later via `claude-api:build-eval`):

```json
{"subject": "Your application to Acme Corp", "sender": "careers@acme.com", "date": "Mon, 5 Jan 2026 10:00:00 +0000", "body": "Thank you for applying to the Software Engineer position at Acme Corp. We have received your application and will be in touch.", "expected": {"is_job_related": true, "company": "Acme Corp", "position": "Software Engineer", "status": "applied"}}
```

- [ ] **Step 2: Write the eval runner**

Create `backend/evaluation/run_eval.py`:

```python
"""Run the classification/extraction pipeline against a labeled dataset.

This is NOT part of the pytest suite — every run spends real Anthropic API
money. Run manually:

    cd backend
    uv run python -m evaluation.run_eval

The dataset (evaluation/dataset.jsonl) currently has one illustrative example.
Building a real labeled set is a separate, interactive step — see the
`claude-api` skill's `build-eval` workflow.
"""

import json
from pathlib import Path

from app.llm.client import AnthropicExtractor

DATASET_PATH = Path(__file__).parent / "dataset.jsonl"


def _load_dataset() -> list[dict]:
    with DATASET_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    extractor = AnthropicExtractor()
    examples = _load_dataset()

    classification_correct = 0
    field_matches = {"company": 0, "position": 0, "status": 0}
    field_totals = {"company": 0, "position": 0, "status": 0}

    for example in examples:
        result = extractor.classify_and_extract(
            subject=example["subject"],
            sender=example["sender"],
            date=example["date"],
            body=example["body"],
        )
        expected = example["expected"]

        if result.is_job_related == expected["is_job_related"]:
            classification_correct += 1

        if expected["is_job_related"]:
            for field in ("company", "position", "status"):
                if field in expected:
                    field_totals[field] += 1
                    if getattr(result, field) == expected[field]:
                        field_matches[field] += 1

    print(f"Classification accuracy: {classification_correct}/{len(examples)}")
    for field, total in field_totals.items():
        if total:
            print(f"{field} accuracy: {field_matches[field]}/{total}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify it's excluded from the default test run**

Run: `cd backend && uv run pytest --collect-only -q 2>&1 | grep -i evaluation`
Expected: no output — pytest doesn't collect anything under `evaluation/` (no `test_*.py` files there), confirming it never runs as part of `uv run pytest`.

- [ ] **Step 4: Commit**

```bash
git add backend/evaluation/
git commit -m "feat(backend): scaffold the evaluation runner (dataset to be built via claude-api build-eval)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 11: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add a "Phase 4 pipeline setup" section**

Edit `README.md`, adding a new section after "Gmail integration setup (Phase 3, optional)":

```markdown
## Classification & extraction pipeline setup (Phase 4, optional)

Requires Gmail to already be connected (Phase 3, above).

1. Get an Anthropic API key from <https://console.anthropic.com/> and set it in
   `backend/.env`:
   ```
   ANTHROPIC_API_KEY=<your real key>
   ```
   (The placeholder value that ships by default is enough to run the test suite,
   but every real "Process Inbox" call needs a real key.)
2. Restart the backend.
3. On the dashboard, click **Process Inbox**. This fetches your recent Gmail
   messages (up to `PIPELINE_BATCH_LIMIT`, default 20), classifies each one, and
   either auto-creates/updates an application, ignores it, or adds it to the
   **Needs review** queue below the Gmail panel.
4. For anything in the review queue, **Approve** (optionally editing a field
   first) or **Reject**. Automation never touches an application you created by
   hand — those always go through this queue, regardless of how confident the
   extraction was.
5. Building a real evaluation set for classification/extraction quality is a
   separate step — see `backend/evaluation/run_eval.py` and invoke the
   `claude-api` skill's `build-eval` workflow when you're ready to do that.

Full design: `docs/superpowers/specs/2026-09-15-job-tracker-phase-4-pipeline-design.md`
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: Phase 4 pipeline setup instructions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
