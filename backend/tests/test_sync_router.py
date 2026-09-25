from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gmail.crypto import encrypt_token
from app.core.config import settings
from app.gmail.models import GmailConnection
from app.sync import dispatch
from app.sync.models import SyncJob

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


@pytest.fixture
def dispatched(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(dispatch, "request_worker", lambda job_type: calls.append(job_type) or True)
    return calls


def test_start_sync_dispatches_the_worker_for_the_new_jobs_lane(
    auth_client: TestClient, connected_gmail, dispatched
) -> None:
    response = auth_client.post(f"{BASE}/sync")
    assert response.status_code == 202
    assert dispatched == ["initial"]


def test_start_sync_still_returns_202_when_dispatch_fails(
    auth_client: TestClient, connected_gmail, db_session, monkeypatch
) -> None:
    # The real request_worker, configured, with GitHub unreachable: the job
    # must still be created, committed and reported as queued.
    monkeypatch.setattr(settings, "sync_dispatch_token", "t")
    monkeypatch.setattr(settings, "sync_dispatch_repository", "owner/repo")

    def unreachable(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", unreachable)

    response = auth_client.post(f"{BASE}/sync")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    job = db_session.get(SyncJob, response.json()["id"])
    assert job is not None and job.status == "queued"


def test_start_sync_rekicks_the_worker_for_an_already_queued_job(
    auth_client: TestClient, connected_gmail, dispatched
) -> None:
    first = auth_client.post(f"{BASE}/sync")
    second = auth_client.post(f"{BASE}/sync")

    assert second.status_code == 409
    assert second.json()["id"] == first.json()["id"]
    assert dispatched == ["initial", "initial"]


def test_start_sync_does_not_rekick_a_healthy_running_job(
    auth_client: TestClient, connected_gmail, db_session, dispatched
) -> None:
    job_id = auth_client.post(f"{BASE}/sync").json()["id"]
    job = db_session.get(SyncJob, job_id)
    job.status = "running"
    db_session.commit()

    second = auth_client.post(f"{BASE}/sync")

    assert second.status_code == 409
    assert second.json()["status"] == "running"
    assert dispatched == ["initial"]


def test_start_sync_rekicks_a_stale_running_job(
    auth_client: TestClient, connected_gmail, db_session, dispatched
) -> None:
    job_id = auth_client.post(f"{BASE}/sync").json()["id"]
    db_session.execute(
        SyncJob.__table__.update()
        .where(SyncJob.__table__.c.id == job_id)
        .values(status="running", updated_at=datetime.now(timezone.utc) - timedelta(minutes=30))
    )
    db_session.commit()

    second = auth_client.post(f"{BASE}/sync")

    assert second.status_code == 409
    assert dispatched == ["initial", "initial"]


def test_start_sync_returns_403_with_a_reconnect_code_and_does_not_dispatch(
    auth_client: TestClient, db_session, connected_gmail, monkeypatch
) -> None:
    connected_gmail.reauth_required_at = datetime.now(timezone.utc)
    db_session.commit()
    dispatched = []
    monkeypatch.setattr(dispatch, "request_worker", lambda job_type: dispatched.append(job_type))

    response = auth_client.post(f"{BASE}/sync")

    assert response.status_code == 403
    assert response.json()["code"] == "gmail_reauth_required"
    assert dispatched == []


def test_get_sync_exposes_the_error_code(auth_client: TestClient, db_session, connected_gmail, user) -> None:
    job = SyncJob(
        user_id=user.id, job_type="incremental", window_start=datetime.now(timezone.utc).date(),
        status="failed", error_code="gmail_reauth_required", error_message="Reconnect Gmail",
    )
    db_session.add(job)
    db_session.commit()

    body = auth_client.get(f"{BASE}/sync/{job.id}").json()

    assert body["error_code"] == "gmail_reauth_required"


def test_start_sync_is_limited_to_10_requests_a_minute_per_user(
    auth_client: TestClient, other_auth_client: TestClient, connected_gmail, dispatched
) -> None:
    statuses = [auth_client.post(f"{BASE}/sync").status_code for _ in range(10)]
    assert 429 not in statuses

    limited = auth_client.post(f"{BASE}/sync")
    assert limited.status_code == 429
    assert 0 < int(limited.headers["Retry-After"]) <= 60
    assert "Too many requests" in limited.json()["detail"]
    # Another user has their own allowance (404 here: no Gmail connection).
    assert other_auth_client.post(f"{BASE}/sync").status_code == 404


def test_polling_endpoints_are_never_rate_limited(auth_client: TestClient, connected_gmail, dispatched) -> None:
    job_id = auth_client.post(f"{BASE}/sync").json()["id"]
    for _ in range(100):
        assert auth_client.get(f"{BASE}/sync/{job_id}").status_code == 200
        assert auth_client.get(f"{BASE}/sync/latest").status_code == 200
        assert auth_client.get(f"{BASE}/status").status_code == 200


def test_rekick_dispatches_at_most_once_per_30_seconds_per_job(
    auth_client: TestClient, connected_gmail, dispatched, monkeypatch
) -> None:
    from app.sync import router as sync_router

    clock = [5000.0]
    monkeypatch.setattr(sync_router.time, "monotonic", lambda: clock[0])
    first = auth_client.post(f"{BASE}/sync")
    auth_client.post(f"{BASE}/sync")  # re-kick
    third = auth_client.post(f"{BASE}/sync")  # within 30s: no second re-kick

    assert third.status_code == 409
    assert third.json()["id"] == first.json()["id"]
    assert dispatched == ["initial", "initial"]

    clock[0] += 31
    auth_client.post(f"{BASE}/sync")
    assert dispatched == ["initial", "initial", "initial"]
