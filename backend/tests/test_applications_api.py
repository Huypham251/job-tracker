from fastapi.testclient import TestClient

BASE = "/api/v1/applications"
MISSING_ID = "00000000-0000-0000-0000-000000000000"


def test_create_returns_201_with_body(client: TestClient) -> None:
    response = client.post(BASE, json={"company": "Acme", "position": "SWE Intern"})
    assert response.status_code == 201
    body = response.json()
    assert body["company"] == "Acme"
    assert body["position"] == "SWE Intern"
    assert body["status"] == "applied"
    assert body["applied_at"] is None
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


def test_create_accepts_status_and_applied_at(client: TestClient) -> None:
    response = client.post(
        BASE,
        json={
            "company": "Acme",
            "position": "SWE",
            "status": "interview",
            "applied_at": "2026-09-01",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "interview"
    assert body["applied_at"] == "2026-09-01"


def test_create_rejects_blank_company_with_422(client: TestClient) -> None:
    response = client.post(BASE, json={"company": "   ", "position": "SWE"})
    assert response.status_code == 422


def test_list_returns_newest_first(client: TestClient) -> None:
    client.post(BASE, json={"company": "A", "position": "P1"})
    client.post(BASE, json={"company": "B", "position": "P2"})
    response = client.get(BASE)
    assert response.status_code == 200
    assert [row["company"] for row in response.json()] == ["B", "A"]


def test_patch_updates_only_status(client: TestClient) -> None:
    created = client.post(BASE, json={"company": "A", "position": "P"}).json()
    response = client.patch(f"{BASE}/{created['id']}", json={"status": "offer"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    assert body["company"] == "A"
    assert body["updated_at"] > created["updated_at"]


def test_patch_clears_applied_at_with_null(client: TestClient) -> None:
    created = client.post(
        BASE, json={"company": "A", "position": "P", "applied_at": "2026-09-01"}
    ).json()
    assert created["applied_at"] == "2026-09-01"
    response = client.patch(f"{BASE}/{created['id']}", json={"applied_at": None})
    assert response.status_code == 200
    assert response.json()["applied_at"] is None


def test_patch_omitting_applied_at_leaves_it_unchanged(client: TestClient) -> None:
    created = client.post(
        BASE, json={"company": "A", "position": "P", "applied_at": "2026-09-01"}
    ).json()
    response = client.patch(f"{BASE}/{created['id']}", json={"status": "offer"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    assert body["applied_at"] == "2026-09-01"


def test_patch_empty_body_returns_422(client: TestClient) -> None:
    created = client.post(BASE, json={"company": "A", "position": "P"}).json()
    response = client.patch(f"{BASE}/{created['id']}", json={})
    assert response.status_code == 422


def test_patch_missing_returns_404(client: TestClient) -> None:
    response = client.patch(f"{BASE}/{MISSING_ID}", json={"status": "offer"})
    assert response.status_code == 404
    assert MISSING_ID in response.json()["detail"]


def test_delete_returns_204_then_get_404(client: TestClient) -> None:
    created = client.post(BASE, json={"company": "A", "position": "P"}).json()
    assert client.delete(f"{BASE}/{created['id']}").status_code == 204
    assert client.get(f"{BASE}/{created['id']}").status_code == 404


def test_delete_missing_returns_404(client: TestClient) -> None:
    assert client.delete(f"{BASE}/{MISSING_ID}").status_code == 404


def test_get_missing_returns_404(client: TestClient) -> None:
    response = client.get(f"{BASE}/{MISSING_ID}")
    assert response.status_code == 404
