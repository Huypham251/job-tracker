from fastapi.testclient import TestClient

from app.auth.dependencies import COOKIE_NAME

from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from app.auth.jwt import create_access_token
from app.core.config import settings


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


def test_expired_token_returns_401(client: TestClient, user) -> None:
    expired_payload = {
        "sub": str(user.id),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    token = pyjwt.encode(expired_payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    client.cookies.set(COOKIE_NAME, token)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_forged_token_returns_401(client: TestClient, user) -> None:
    payload = {
        "sub": str(user.id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    forged_token = pyjwt.encode(payload, "wrong-secret", algorithm=settings.jwt_algorithm)
    client.cookies.set(COOKIE_NAME, forged_token)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_token_for_deleted_user_returns_401(
    client: TestClient, db_session, user
) -> None:
    token = create_access_token(user.id)
    db_session.delete(user)
    db_session.commit()
    client.cookies.set(COOKIE_NAME, token)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_google_callback_oauth_error_redirects_to_frontend(
    client: TestClient, monkeypatch
) -> None:
    from authlib.integrations.base_client import OAuthError

    from app.auth.oauth import oauth
    from app.core.config import settings

    async def fake_authorize_access_token(request):
        raise OAuthError(description="access_denied")

    monkeypatch.setattr(oauth.google, "authorize_access_token", fake_authorize_access_token)
    response = client.get(
        "/api/v1/auth/google/callback?state=x&code=y", follow_redirects=False
    )
    assert response.status_code in (302, 307)
    assert response.headers["location"] == settings.frontend_url


def test_google_login_redirect_uri_uses_frontend_url_not_request_host(
    client: TestClient, monkeypatch
) -> None:
    captured = {}

    async def fake_authorize_redirect(request, redirect_uri):
        captured["redirect_uri"] = redirect_uri
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="https://accounts.google.com/fake")

    from app.auth.oauth import oauth

    monkeypatch.setattr(oauth.google, "authorize_redirect", fake_authorize_redirect)

    client.get("/api/v1/auth/google/login", follow_redirects=False)

    assert captured["redirect_uri"] == f"{settings.frontend_url}/api/v1/auth/google/callback"


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
