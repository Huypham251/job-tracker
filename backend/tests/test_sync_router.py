from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection

BASE = "/api/v1/gmail"


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


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/sync"),
        ("get", "/sync/latest"),
        ("get", "/sync/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_sync_endpoints_require_authentication(client: TestClient, method, path) -> None:
    response = getattr(client, method)(f"{BASE}{path}")
    assert response.status_code == 401


def test_start_sync_returns_202_and_initial_job(auth_client: TestClient, connected_gmail) -> None:
    response = auth_client.post(f"{BASE}/sync")

    assert response.status_code == 202
    body = response.json()
    assert body["job_type"] == "initial"
    assert body["status"] == "queued"


def test_start_sync_requires_gmail_connection(auth_client: TestClient) -> None:
    response = auth_client.post(f"{BASE}/sync")
    assert response.status_code == 404


def test_start_sync_returns_409_with_existing_job_when_already_running(
    auth_client: TestClient, connected_gmail
) -> None:
    first = auth_client.post(f"{BASE}/sync")
    assert first.status_code == 202

    second = auth_client.post(f"{BASE}/sync")

    assert second.status_code == 409
    assert second.json()["id"] == first.json()["id"]


def test_get_sync_returns_the_job(auth_client: TestClient, connected_gmail) -> None:
    started = auth_client.post(f"{BASE}/sync").json()

    response = auth_client.get(f"{BASE}/sync/{started['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == started["id"]


def test_get_sync_404_for_unknown_job(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/sync/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_sync_latest_returns_null_when_no_jobs(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/sync/latest")
    assert response.status_code == 200
    assert response.json() is None


def test_get_sync_latest_returns_most_recent_job(auth_client: TestClient, connected_gmail) -> None:
    started = auth_client.post(f"{BASE}/sync").json()

    response = auth_client.get(f"{BASE}/sync/latest")

    assert response.status_code == 200
    assert response.json()["id"] == started["id"]


def test_cross_user_isolation(
    auth_client: TestClient, other_auth_client: TestClient, connected_gmail
) -> None:
    started = auth_client.post(f"{BASE}/sync").json()

    assert other_auth_client.get(f"{BASE}/sync/{started['id']}").status_code == 404
    assert other_auth_client.get(f"{BASE}/sync/latest").json() is None
