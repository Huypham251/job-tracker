from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_returns_ok_when_db_is_reachable(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_worker_returns_503_when_worker_has_never_polled(
    client: TestClient, monkeypatch
) -> None:
    import app.sync.worker as worker_module

    monkeypatch.setattr(worker_module, "_last_poll_at", None)
    response = client.get("/health/worker")
    assert response.status_code == 503
    assert response.json()["status"] == "not_running"


def test_health_worker_returns_ok_when_poll_is_recent(
    client: TestClient, monkeypatch
) -> None:
    import app.sync.worker as worker_module

    monkeypatch.setattr(worker_module, "_last_poll_at", datetime.now(timezone.utc))
    response = client.get("/health/worker")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_worker_returns_503_when_poll_is_stale(
    client: TestClient, monkeypatch
) -> None:
    import app.sync.worker as worker_module

    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    monkeypatch.setattr(worker_module, "_last_poll_at", stale)
    response = client.get("/health/worker")
    assert response.status_code == 503
    assert response.json()["status"] == "stale"
