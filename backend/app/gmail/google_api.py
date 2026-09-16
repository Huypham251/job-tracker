import base64
import html
import re

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

_TIMEOUT = 10.0

BODY_MAX_CHARS = 4000

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_QUOTE_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)
_ON_WROTE_RE = re.compile(r"^On .+ wrote:\s*$", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")


class GoogleApiError(Exception):
    """A Google/Gmail HTTP call returned a non-2xx response."""


def refresh_access_token(*, client_id: str, client_secret: str, refresh_token: str) -> dict:
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"token refresh failed: {response.status_code}")
    return response.json()


def revoke_token(token: str) -> None:
    response = httpx.post(REVOKE_URL, data={"token": token}, timeout=_TIMEOUT)
    if response.status_code != 200:
        raise GoogleApiError(f"token revoke failed: {response.status_code}")


def get_profile(access_token: str) -> dict:
    response = httpx.get(
        f"{GMAIL_API_BASE}/profile",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"profile fetch failed: {response.status_code}")
    return response.json()


def list_message_ids(access_token: str, *, limit: int) -> list[str]:
    response = httpx.get(
        f"{GMAIL_API_BASE}/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"maxResults": limit},
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"message list failed: {response.status_code}")
    return [item["id"] for item in response.json().get("messages", [])]


def get_message_summary(access_token: str, message_id: str) -> dict:
    response = httpx.get(
        f"{GMAIL_API_BASE}/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        # format=metadata + an explicit header allowlist — this function only
        # needs headers/snippet for display purposes (the Gmail test-fetch
        # endpoint, and ProcessedMessage's subject/sender/snippet fields).
        # get_message_body() below fetches the full body separately, only
        # when the pipeline needs it for classification (see the Phase 4
        # spec §11, data minimization — the body itself is never persisted).
        params={
            "format": "metadata",
            "metadataHeaders": ["Subject", "From", "Date"],
        },
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        raise GoogleApiError(f"message fetch failed: {response.status_code}")
    payload = response.json()
    headers = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
    return {
        "id": payload["id"],
        "subject": headers.get("Subject", ""),
        "from_": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": payload.get("snippet", ""),
    }


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
    # needs body content. What's sent onward to the classifier is still minimized:
    # cleaned plain text only, truncated, never the raw MIME structure or attachments.
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
