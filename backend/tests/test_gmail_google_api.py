import base64

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


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_get_message_extracts_body_extracts_plain_text_part(monkeypatch) -> None:
    payload = {
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Thanks for applying to Acme.")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>Thanks for applying to Acme.</p>")}},
            ],
        }
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message("token", "m1")[1] == "Thanks for applying to Acme."


def test_get_message_extracts_body_falls_back_to_html_and_strips_tags(monkeypatch) -> None:
    payload = {
        "payload": {"mimeType": "text/html", "body": {"data": _b64("<p>Hello <b>World</b></p>")}}
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message("token", "m1")[1] == "Hello World"


def test_get_message_extracts_body_strips_style_and_script_block_contents(monkeypatch) -> None:
    raw = (
        "<html><head>"
        "<style>.unsubscribe-link { color: blue; font-weight: bold; }</style>"
        "<script>function trackClick() { return true; }</script>"
        "</head><body><p>Thanks for applying to Acme.</p></body></html>"
    )
    payload = {"payload": {"mimeType": "text/html", "body": {"data": _b64(raw)}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    result = google_api.get_message("token", "m1")[1]
    assert result == "Thanks for applying to Acme."
    assert "unsubscribe-link" not in result
    assert "trackClick" not in result


def test_get_message_extracts_body_strips_quoted_replies(monkeypatch) -> None:
    raw = (
        "Please see below.\n\n"
        "On Mon, Jan 1, 2026 at 1:00 PM wrote:\n"
        "> old message\n"
        "> more old"
    )
    payload = {"payload": {"mimeType": "text/plain", "body": {"data": _b64(raw)}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message("token", "m1")[1] == "Please see below."


def test_get_message_extracts_body_truncates_long_bodies(monkeypatch) -> None:
    long_text = "a" * 5000
    payload = {"payload": {"mimeType": "text/plain", "body": {"data": _b64(long_text)}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    result = google_api.get_message("token", "m1")[1]
    assert len(result) == google_api.BODY_MAX_CHARS


def test_get_message_extracts_body_returns_empty_string_when_no_text_part(monkeypatch) -> None:
    payload = {"payload": {"mimeType": "image/png", "body": {"data": _b64("binarydata")}}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message("token", "m1")[1] == ""


def test_get_message_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.get_message("token", "m1")[1]


def test_list_message_ids_page_returns_ids_and_next_page_token(monkeypatch) -> None:
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured["params"] = params
        return _FakeResponse(200, {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "next-tok"})

    monkeypatch.setattr(httpx, "get", fake_get)

    ids, next_page_token = google_api.list_message_ids_page(
        "token", query="after:2026/01/01", page_token="prev-tok", max_results=100
    )

    assert ids == ["m1", "m2"]
    assert next_page_token == "next-tok"
    assert captured["params"]["q"] == "after:2026/01/01"
    assert captured["params"]["pageToken"] == "prev-tok"
    assert captured["params"]["maxResults"] == 100


def test_list_message_ids_page_omits_page_token_param_when_none(monkeypatch) -> None:
    captured = {}

    def fake_get(url, headers, params, timeout):
        captured["params"] = params
        return _FakeResponse(200, {"messages": []})

    monkeypatch.setattr(httpx, "get", fake_get)

    ids, next_page_token = google_api.list_message_ids_page(
        "token", query="after:2026/01/01", page_token=None, max_results=100
    )

    assert ids == []
    assert next_page_token is None
    assert "pageToken" not in captured["params"]


def test_list_message_ids_page_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(400, {}))
    with pytest.raises(google_api.GoogleApiError):
        google_api.list_message_ids_page(
            "token", query="after:2026/01/01", page_token=None, max_results=100
        )


def test_get_message_makes_one_full_fetch_and_returns_summary_and_body(monkeypatch) -> None:
    calls = []
    payload = {
        "id": "m1",
        "snippet": "Thanks for applying",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Your application"},
                {"name": "From", "value": "jobs@acme.com"},
                {"name": "Date", "value": "Mon, 1 Sep 2026 10:00:00 +0000"},
                {"name": "X-Other", "value": "ignored"},
            ],
            "body": {"data": _b64("Hello there")},
        },
    }

    def fake_get(url, headers, params, timeout):
        calls.append((url, params))
        return _FakeResponse(200, payload)

    monkeypatch.setattr(httpx, "get", fake_get)

    summary, body = google_api.get_message("token", "m1")

    assert calls == [(f"{google_api.GMAIL_API_BASE}/messages/m1", {"format": "full"})]
    assert summary == {
        "id": "m1",
        "subject": "Your application",
        "from_": "jobs@acme.com",
        "date": "Mon, 1 Sep 2026 10:00:00 +0000",
        "snippet": "Thanks for applying",
    }
    assert body == "Hello there"


def test_get_message_summary_matches_get_message_for_the_same_payload(monkeypatch) -> None:
    payload = {
        "id": "m1",
        "snippet": "s",
        "payload": {"headers": [
            {"name": "Subject", "value": "S"},
            {"name": "From", "value": "F"},
            {"name": "Date", "value": "D"},
        ]},
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))
    assert google_api.get_message_summary("t", "m1") == google_api.get_message("t", "m1")[0]


def test_get_message_uses_the_requested_id_when_the_payload_has_none(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {"payload": {}}))
    summary, body = google_api.get_message("t", "m9")
    assert summary["id"] == "m9"
    assert summary["subject"] == "" and summary["snippet"] == ""
    assert body == ""
