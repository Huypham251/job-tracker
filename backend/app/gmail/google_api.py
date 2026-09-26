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
_HTML_STYLE_SCRIPT_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_QUOTE_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)
_ON_WROTE_RE = re.compile(r"^On .+ wrote:\s*$", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")


class GoogleApiError(Exception):
    """A Google/Gmail call failed: a non-2xx response (status_code set) or a
    network-level failure (status_code None). The message carries only the
    operation and the status or error class — never a URL, token or message
    ID, since worker tracebacks end up in public GitHub Actions logs."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GmailAuthError(GoogleApiError):
    """The user's Gmail grant is no longer usable (revoked, expired, or the
    stored token is unreadable) — retrying can't help, only a reconnect can."""


def _send(method: str, url: str, operation: str, **kwargs) -> httpx.Response:
    try:
        return getattr(httpx, method)(url, **kwargs)
    except httpx.TransportError as exc:
        raise GoogleApiError(f"{operation} failed: network error ({type(exc).__name__})") from exc


def _raise_for_status(response: httpx.Response, operation: str) -> None:
    if response.status_code == 200:
        return
    if response.status_code == 401:
        # The access token was just refreshed or is still within its
        # lifetime, so a 401 from Gmail means the grant itself is gone.
        raise GmailAuthError(f"{operation} failed: 401", status_code=401)
    raise GoogleApiError(f"{operation} failed: {response.status_code}", status_code=response.status_code)


def _oauth_error(response: httpx.Response) -> str | None:
    try:
        return response.json().get("error")
    except Exception:
        return None


def refresh_access_token(*, client_id: str, client_secret: str, refresh_token: str) -> dict:
    response = _send(
        "post",
        TOKEN_URL,
        "token refresh",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=_TIMEOUT,
    )
    if response.status_code != 200:
        # invalid_grant = the refresh token was revoked or expired (weekly in
        # Google's Testing mode). invalid_client and friends are our own
        # misconfiguration, not the user's grant, so they stay generic —
        # which is also why this doesn't go through _raise_for_status.
        if response.status_code == 400 and _oauth_error(response) == "invalid_grant":
            raise GmailAuthError("token refresh failed: invalid_grant", status_code=400)
        raise GoogleApiError(f"token refresh failed: {response.status_code}", status_code=response.status_code)
    return response.json()


def revoke_token(token: str) -> None:
    response = _send("post", REVOKE_URL, "token revoke", data={"token": token}, timeout=_TIMEOUT)
    _raise_for_status(response, "token revoke")


def get_profile(access_token: str) -> dict:
    response = _send(
        "get",
        f"{GMAIL_API_BASE}/profile",
        "profile fetch",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_TIMEOUT,
    )
    _raise_for_status(response, "profile fetch")
    return response.json()


def list_message_ids(access_token: str, *, limit: int) -> list[str]:
    response = _send(
        "get",
        f"{GMAIL_API_BASE}/messages",
        "message list",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"maxResults": limit},
        timeout=_TIMEOUT,
    )
    _raise_for_status(response, "message list")
    return [item["id"] for item in response.json().get("messages", [])]


def list_message_ids_page(
    access_token: str, *, query: str, page_token: str | None, max_results: int
) -> tuple[list[str], str | None]:
    params: dict[str, str | int] = {"maxResults": max_results, "q": query}
    if page_token is not None:
        params["pageToken"] = page_token

    response = _send(
        "get",
        f"{GMAIL_API_BASE}/messages",
        "message list",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=_TIMEOUT,
    )
    _raise_for_status(response, "message list")
    payload = response.json()
    ids = [item["id"] for item in payload.get("messages", [])]
    return ids, payload.get("nextPageToken")


def _summary_from_payload(payload: dict, message_id: str) -> dict:
    headers = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
    return {
        "id": payload.get("id", message_id),
        "subject": headers.get("Subject", ""),
        "from_": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": payload.get("snippet", ""),
    }


def get_message_summary(access_token: str, message_id: str) -> dict:
    response = _send(
        "get",
        f"{GMAIL_API_BASE}/messages/{message_id}",
        "message fetch",
        headers={"Authorization": f"Bearer {access_token}"},
        # format=metadata + an explicit header allowlist — used only by the
        # Phase 3 display endpoint ("Fetch recent messages"), which never
        # needs a body. The sync worker uses get_message() below instead.
        params={
            "format": "metadata",
            "metadataHeaders": ["Subject", "From", "Date"],
        },
        timeout=_TIMEOUT,
    )
    _raise_for_status(response, "message fetch")
    return _summary_from_payload(response.json(), message_id)


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
        text = _HTML_STYLE_SCRIPT_RE.sub(" ", text)
        text = _HTML_TAG_RE.sub(" ", text)
        text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:BODY_MAX_CHARS]


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
    """One format=full fetch for the sync worker, returning (summary, body).
    The headers and snippet format=metadata would give are in the same
    payload, so fetching them separately doubled the Gmail round-trips per
    message (Phase 9). format=full because reliable extraction genuinely
    needs the body — but what's sent onward to the classifier is still
    minimized: cleaned plain text only, truncated, never the raw MIME
    structure or attachments, and the body itself is never persisted
    (Phase 4 spec §11)."""
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
