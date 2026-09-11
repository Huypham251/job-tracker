from fastapi.testclient import TestClient

from app.auth.dependencies import COOKIE_NAME


def test_me_without_cookie_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_with_garbage_cookie_returns_401(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, "not-a-real-jwt")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_with_valid_cookie_returns_user(auth_client: TestClient, user) -> None:
    response = auth_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user.id)
    assert body["email"] == user.email
    assert body["name"] == user.name


def test_logout_clears_session(auth_client: TestClient) -> None:
    assert auth_client.get("/api/v1/auth/me").status_code == 200
    logout_response = auth_client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204
    set_cookie_header = logout_response.headers.get("set-cookie", "")
    assert COOKIE_NAME in set_cookie_header
    assert "max-age=0" in set_cookie_header.lower()


def test_applications_endpoint_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/applications")
    assert response.status_code == 401
