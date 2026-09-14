import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

_TIMEOUT = 10.0


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
        # format=metadata + an explicit header allowlist — we deliberately
        # never fetch the message body, even though gmail.readonly permits
        # it (see spec §10, data minimization).
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
