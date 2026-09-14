import httpx
import pytest

from app.gmail import google_api


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict) -> None:
        self.status_code = status_code
        self._json_data = json_data

    def json(self) -> dict:
        return self._json_data


def test_refresh_access_token_returns_json_on_success(monkeypatch) -> None:
    def fake_post(url, data, timeout):
        assert url == google_api.TOKEN_URL
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "rtoken"
        return _FakeResponse(200, {"access_token": "new-token", "expires_in": 3600})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = google_api.refresh_access_token(
        client_id="cid", client_secret="csecret", refresh_token="rtoken"
    )
    assert result["access_token"] == "new-token"


def test_refresh_access_token_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.refresh_access_token(client_id="c", client_secret="s", refresh_token="r")


def test_revoke_token_succeeds_on_200(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, {}))
    google_api.revoke_token("token")  # no exception raised


def test_revoke_token_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.revoke_token("token")


def test_get_profile_returns_email(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse(200, {"emailAddress": "a@gmail.com"})
    )
    profile = google_api.get_profile("access-token")
    assert profile["emailAddress"] == "a@gmail.com"


def test_list_message_ids_extracts_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _FakeResponse(200, {"messages": [{"id": "m1"}, {"id": "m2"}]}),
    )
    assert google_api.list_message_ids("token", limit=2) == ["m1", "m2"]


def test_list_message_ids_returns_empty_list_when_no_messages(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {}))
    assert google_api.list_message_ids("token", limit=20) == []


def test_get_message_summary_extracts_headers_and_snippet(monkeypatch) -> None:
    payload = {
        "id": "m1",
        "snippet": "hello there",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Your application"},
                {"name": "From", "value": "jobs@acme.com"},
                {"name": "Date", "value": "Wed, 1 Jan 2026 00:00:00 +0000"},
            ]
        },
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    summary = google_api.get_message_summary("token", "m1")
    assert summary == {
        "id": "m1",
        "subject": "Your application",
        "from_": "jobs@acme.com",
        "date": "Wed, 1 Jan 2026 00:00:00 +0000",
        "snippet": "hello there",
    }
