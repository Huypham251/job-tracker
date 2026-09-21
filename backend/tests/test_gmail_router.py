from datetime import datetime, timedelta, timezone

import pytest
from authlib.integrations.base_client import OAuthError
from fastapi.testclient import TestClient

from app.gmail import google_api
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.gmail.oauth import oauth

BASE = "/api/v1/gmail"

FAKE_TOKEN = {
    "access_token": "fake-access",
    "refresh_token": "fake-refresh",
    "expires_in": 3600,
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
}


@pytest.fixture
def connected_gmail(db_session, user) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("current-access"),
        refresh_token_encrypted=encrypt_token("current-refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/connect"),
        ("get", "/status"),
        ("get", "/messages"),
        ("post", "/disconnect"),
        ("get", "/callback?state=x&code=y"),
    ],
)
def test_gmail_endpoints_require_authentication(client: TestClient, method, path) -> None:
    response = getattr(client, method)(f"{BASE}{path}")
    assert response.status_code == 401


def test_status_when_not_connected(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/status")
    assert response.status_code == 200
    assert response.json() == {"connected": False, "email": None, "connected_at": None}


def test_status_when_connected(auth_client: TestClient, connected_gmail) -> None:
    response = auth_client.get(f"{BASE}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["email"] == "alice@gmail.com"


def test_callback_success_creates_connection(auth_client: TestClient, monkeypatch) -> None:
    async def fake_authorize_access_token(request):
        return FAKE_TOKEN

    monkeypatch.setattr(oauth.google_gmail, "authorize_access_token", fake_authorize_access_token)
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})

    response = auth_client.get(f"{BASE}/callback?state=x&code=y", follow_redirects=False)
    assert response.status_code in (302, 307)

    status_response = auth_client.get(f"{BASE}/status")
    assert status_response.json()["connected"] is True


def test_callback_oauth_error_redirects_without_creating_connection(
    auth_client: TestClient, monkeypatch
) -> None:
    async def fake_authorize_access_token(request):
        raise OAuthError(description="access_denied")

    monkeypatch.setattr(oauth.google_gmail, "authorize_access_token", fake_authorize_access_token)

    response = auth_client.get(f"{BASE}/callback?state=x&code=y", follow_redirects=False)
    assert response.status_code in (302, 307)

    status_response = auth_client.get(f"{BASE}/status")
    assert status_response.json()["connected"] is False


def test_gmail_connect_redirect_uri_uses_frontend_url_not_request_host(
    auth_client: TestClient, monkeypatch
) -> None:
    captured = {}

    async def fake_authorize_redirect(request, redirect_uri):
        captured["redirect_uri"] = redirect_uri
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="https://accounts.google.com/fake")

    monkeypatch.setattr(oauth.google_gmail, "authorize_redirect", fake_authorize_redirect)

    auth_client.get(f"{BASE}/connect", follow_redirects=False)

    from app.core.config import settings

    assert captured["redirect_uri"] == f"{settings.frontend_url}/api/v1/gmail/callback"


def test_messages_endpoint_returns_summaries(
    auth_client: TestClient, connected_gmail, monkeypatch
) -> None:
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(
        google_api,
        "get_message_summary",
        lambda token, message_id: {
            "id": message_id, "subject": "Hi", "from_": "a@b.com", "date": "d", "snippet": "s"
        },
    )

    response = auth_client.get(f"{BASE}/messages?limit=1")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "m1", "subject": "Hi", "from_": "a@b.com", "date": "d", "snippet": "s"}
    ]


def test_messages_endpoint_404_when_not_connected(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/messages")
    assert response.status_code == 404


def test_messages_endpoint_502_when_google_api_fails(
    auth_client: TestClient, connected_gmail, monkeypatch
) -> None:
    def failing_list_message_ids(token, limit):
        raise google_api.GoogleApiError("boom")

    monkeypatch.setattr(google_api, "list_message_ids", failing_list_message_ids)

    response = auth_client.get(f"{BASE}/messages")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Gmail request failed. Try reconnecting your Gmail account."
    }


def test_messages_endpoint_persists_no_new_rows(
    db_session, auth_client: TestClient, connected_gmail, monkeypatch
) -> None:
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(
        google_api,
        "get_message_summary",
        lambda token, message_id: {
            "id": message_id, "subject": "Hi", "from_": "a@b.com", "date": "d", "snippet": "s"
        },
    )
    count_before = db_session.query(GmailConnection).count()

    response = auth_client.get(f"{BASE}/messages?limit=1")

    assert response.status_code == 200
    count_after = db_session.query(GmailConnection).count()
    assert count_after == count_before


def test_disconnect_endpoint_deletes_connection(
    auth_client: TestClient, connected_gmail, monkeypatch
) -> None:
    monkeypatch.setattr(google_api, "revoke_token", lambda token: None)

    response = auth_client.post(f"{BASE}/disconnect")

    assert response.status_code == 204
    status_response = auth_client.get(f"{BASE}/status")
    assert status_response.json()["connected"] is False


def test_disconnect_endpoint_404_when_not_connected(auth_client: TestClient) -> None:
    response = auth_client.post(f"{BASE}/disconnect")
    assert response.status_code == 404


def test_cross_user_isolation(
    auth_client: TestClient, other_auth_client: TestClient, connected_gmail
) -> None:
    other_status = other_auth_client.get(f"{BASE}/status")
    assert other_status.json()["connected"] is False

    other_disconnect = other_auth_client.post(f"{BASE}/disconnect")
    assert other_disconnect.status_code == 404

    own_status = auth_client.get(f"{BASE}/status")
    assert own_status.json()["connected"] is True
