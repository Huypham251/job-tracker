from fastapi.testclient import TestClient

BASE = "/api/v1/applications"
MISSING_ID = "00000000-0000-0000-0000-000000000000"


def test_create_returns_201_with_body(auth_client: TestClient) -> None:
    response = auth_client.post(BASE, json={"company": "Acme", "position": "SWE Intern"})
    assert response.status_code == 201
    body = response.json()
    assert body["company"] == "Acme"
    assert body["position"] == "SWE Intern"
    assert body["status"] == "applied"
    assert body["applied_at"] is None
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]
    assert "user_id" not in body


def test_create_accepts_status_and_applied_at(auth_client: TestClient) -> None:
    response = auth_client.post(
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


def test_create_rejects_blank_company_with_422(auth_client: TestClient) -> None:
    response = auth_client.post(BASE, json={"company": "   ", "position": "SWE"})
    assert response.status_code == 422


def test_list_returns_newest_first(auth_client: TestClient) -> None:
    auth_client.post(BASE, json={"company": "A", "position": "P1"})
    auth_client.post(BASE, json={"company": "B", "position": "P2"})
    response = auth_client.get(BASE)
    assert response.status_code == 200
    assert [row["company"] for row in response.json()] == ["B", "A"]


def test_patch_updates_only_status(auth_client: TestClient) -> None:
    created = auth_client.post(BASE, json={"company": "A", "position": "P"}).json()
    response = auth_client.patch(f"{BASE}/{created['id']}", json={"status": "offer"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    assert body["company"] == "A"
    assert body["updated_at"] > created["updated_at"]


def test_patch_empty_body_returns_422(auth_client: TestClient) -> None:
    created = auth_client.post(BASE, json={"company": "A", "position": "P"}).json()
    response = auth_client.patch(f"{BASE}/{created['id']}", json={})
    assert response.status_code == 422


def test_patch_missing_returns_404(auth_client: TestClient) -> None:
    response = auth_client.patch(f"{BASE}/{MISSING_ID}", json={"status": "offer"})
    assert response.status_code == 404
    assert MISSING_ID in response.json()["detail"]


def test_patch_clears_applied_at_with_null(auth_client: TestClient) -> None:
    created = auth_client.post(
        BASE, json={"company": "A", "position": "P", "applied_at": "2026-09-01"}
    ).json()
    assert created["applied_at"] == "2026-09-01"
    response = auth_client.patch(f"{BASE}/{created['id']}", json={"applied_at": None})
    assert response.status_code == 200
    assert response.json()["applied_at"] is None


def test_patch_omitting_applied_at_leaves_it_unchanged(auth_client: TestClient) -> None:
    created = auth_client.post(
        BASE, json={"company": "A", "position": "P", "applied_at": "2026-09-01"}
    ).json()
    response = auth_client.patch(f"{BASE}/{created['id']}", json={"status": "offer"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    assert body["applied_at"] == "2026-09-01"


def test_delete_returns_204_then_get_404(auth_client: TestClient) -> None:
    created = auth_client.post(BASE, json={"company": "A", "position": "P"}).json()
    assert auth_client.delete(f"{BASE}/{created['id']}").status_code == 204
    assert auth_client.get(f"{BASE}/{created['id']}").status_code == 404


def test_delete_missing_returns_404(auth_client: TestClient) -> None:
    assert auth_client.delete(f"{BASE}/{MISSING_ID}").status_code == 404


def test_get_missing_returns_404(auth_client: TestClient) -> None:
    response = auth_client.get(f"{BASE}/{MISSING_ID}")
    assert response.status_code == 404


def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    assert client.get(BASE).status_code == 401
    assert client.post(BASE, json={"company": "A", "position": "P"}).status_code == 401


def test_list_only_returns_own_applications(
    auth_client: TestClient, other_auth_client: TestClient
) -> None:
    auth_client.post(BASE, json={"company": "Mine", "position": "P"})
    other_auth_client.post(BASE, json={"company": "Theirs", "position": "P"})
    response = auth_client.get(BASE)
    assert [row["company"] for row in response.json()] == ["Mine"]


def test_get_other_users_application_returns_404(
    auth_client: TestClient, other_auth_client: TestClient
) -> None:
    theirs = other_auth_client.post(BASE, json={"company": "Theirs", "position": "P"}).json()
    response = auth_client.get(f"{BASE}/{theirs['id']}")
    assert response.status_code == 404


def test_patch_other_users_application_returns_404(
    auth_client: TestClient, other_auth_client: TestClient
) -> None:
    theirs = other_auth_client.post(BASE, json={"company": "Theirs", "position": "P"}).json()
    response = auth_client.patch(f"{BASE}/{theirs['id']}", json={"status": "offer"})
    assert response.status_code == 404


def test_delete_other_users_application_returns_404(
    auth_client: TestClient, other_auth_client: TestClient
) -> None:
    theirs = other_auth_client.post(BASE, json={"company": "Theirs", "position": "P"}).json()
    response = auth_client.delete(f"{BASE}/{theirs['id']}")
    assert response.status_code == 404
    assert other_auth_client.get(f"{BASE}/{theirs['id']}").status_code == 200
