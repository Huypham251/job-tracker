from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from app.gmail import google_api, service
from app.gmail.crypto import decrypt_token, encrypt_token
from app.gmail.exceptions import GmailNotConnected, GmailReauthRequired
from app.gmail.models import GmailConnection

FAKE_TOKEN = {
    "access_token": "fake-access",
    "refresh_token": "fake-refresh",
    "expires_in": 3600,
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
}


def _make_connection(db_session, user, *, expires_in_seconds: int) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("current-access"),
        refresh_token_encrypted=encrypt_token("current-refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


def test_connect_creates_new_connection_with_encrypted_tokens(db_session, user, monkeypatch) -> None:
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})

    connection = service.connect(db_session, user.id, FAKE_TOKEN)

    assert connection.google_email == "alice@gmail.com"
    assert connection.access_token_encrypted != FAKE_TOKEN["access_token"]
    assert decrypt_token(connection.access_token_encrypted) == FAKE_TOKEN["access_token"]
    assert decrypt_token(connection.refresh_token_encrypted) == FAKE_TOKEN["refresh_token"]


def test_connect_updates_existing_connection_on_reconnect(db_session, user, monkeypatch) -> None:
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})
    service.connect(db_session, user.id, FAKE_TOKEN)

    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice.new@gmail.com"})
    new_token = {**FAKE_TOKEN, "access_token": "new-access"}
    connection = service.connect(db_session, user.id, new_token)

    assert connection.google_email == "alice.new@gmail.com"
    assert decrypt_token(connection.access_token_encrypted) == "new-access"
    count = (
        db_session.query(GmailConnection).filter_by(user_id=user.id).count()
    )
    assert count == 1


def test_connect_revokes_old_refresh_token_on_reconnect(db_session, user, monkeypatch) -> None:
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})
    service.connect(db_session, user.id, FAKE_TOKEN)

    revoked = []
    monkeypatch.setattr(google_api, "revoke_token", lambda token: revoked.append(token))
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice.new@gmail.com"})
    new_token = {**FAKE_TOKEN, "refresh_token": "new-refresh", "access_token": "new-access"}

    service.connect(db_session, user.id, new_token)

    assert revoked == [FAKE_TOKEN["refresh_token"]]


def test_connect_reconnect_succeeds_even_if_revoke_raises(db_session, user, monkeypatch) -> None:
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})
    service.connect(db_session, user.id, FAKE_TOKEN)

    def failing_revoke(token):
        raise google_api.GoogleApiError("boom")

    monkeypatch.setattr(google_api, "revoke_token", failing_revoke)
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice.new@gmail.com"})
    new_token = {**FAKE_TOKEN, "refresh_token": "new-refresh", "access_token": "new-access"}

    connection = service.connect(db_session, user.id, new_token)

    assert connection.google_email == "alice.new@gmail.com"
    assert decrypt_token(connection.access_token_encrypted) == "new-access"
    assert decrypt_token(connection.refresh_token_encrypted) == "new-refresh"
    count = db_session.query(GmailConnection).filter_by(user_id=user.id).count()
    assert count == 1


def test_get_valid_access_token_returns_cached_token_when_not_expiring_soon(
    db_session, user, monkeypatch
) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)
    calls = []
    monkeypatch.setattr(google_api, "refresh_access_token", lambda **kw: calls.append(kw))

    token = service.get_valid_access_token(db_session, connection)

    assert token == "current-access"
    assert calls == []


def test_get_valid_access_token_refreshes_when_expiring_soon(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=30)
    monkeypatch.setattr(
        google_api,
        "refresh_access_token",
        lambda **kw: {"access_token": "refreshed-access", "expires_in": 3600},
    )

    token = service.get_valid_access_token(db_session, connection)

    assert token == "refreshed-access"
    assert decrypt_token(connection.access_token_encrypted) == "refreshed-access"
    assert connection.token_expiry > datetime.now(timezone.utc) + timedelta(minutes=30)


def test_list_recent_messages_raises_when_not_connected(db_session, user) -> None:
    with pytest.raises(GmailNotConnected):
        service.list_recent_messages(db_session, user.id, 20)


def test_list_recent_messages_returns_summaries(db_session, user, monkeypatch) -> None:
    _make_connection(db_session, user, expires_in_seconds=3600)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1", "m2"])
    monkeypatch.setattr(
        google_api,
        "get_message_summary",
        lambda token, message_id: {
            "id": message_id, "subject": "s", "from_": "f", "date": "d", "snippet": "sn"
        },
    )

    messages = service.list_recent_messages(db_session, user.id, 2)

    assert [m["id"] for m in messages] == ["m1", "m2"]


def test_disconnect_revokes_and_deletes_row(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)
    revoked = []
    monkeypatch.setattr(google_api, "revoke_token", lambda token: revoked.append(token))

    service.disconnect(db_session, user.id)

    assert revoked == ["current-refresh"]
    assert db_session.get(GmailConnection, connection.id) is None


def test_disconnect_deletes_row_even_if_revoke_raises(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)

    def failing_revoke(token):
        raise google_api.GoogleApiError("boom")

    monkeypatch.setattr(google_api, "revoke_token", failing_revoke)

    service.disconnect(db_session, user.id)

    assert db_session.get(GmailConnection, connection.id) is None


def _make_connection_encrypted_with_a_rotated_away_key(db_session, user) -> GmailConnection:
    """A row written before GMAIL_TOKEN_ENCRYPTION_KEY was rotated: its tokens
    were encrypted with a key the app no longer has, so decrypt_token raises
    InvalidToken on them."""
    old_key = Fernet(Fernet.generate_key())
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=old_key.encrypt(b"old-access").decode(),
        refresh_token_encrypted=old_key.encrypt(b"old-refresh").decode(),
        token_expiry=datetime.now(timezone.utc) + timedelta(seconds=3600),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


def test_connect_reconnect_succeeds_after_encryption_key_rotation(db_session, user, monkeypatch) -> None:
    _make_connection_encrypted_with_a_rotated_away_key(db_session, user)
    revoked = []
    monkeypatch.setattr(google_api, "revoke_token", lambda token: revoked.append(token))
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})
    new_token = {**FAKE_TOKEN, "refresh_token": "new-refresh", "access_token": "new-access"}

    connection = service.connect(db_session, user.id, new_token)

    assert revoked == []  # the old token is unreadable, so there's nothing to revoke
    assert decrypt_token(connection.access_token_encrypted) == "new-access"
    assert decrypt_token(connection.refresh_token_encrypted) == "new-refresh"
    assert db_session.query(GmailConnection).filter_by(user_id=user.id).count() == 1


def test_disconnect_deletes_row_after_encryption_key_rotation(db_session, user, monkeypatch) -> None:
    connection = _make_connection_encrypted_with_a_rotated_away_key(db_session, user)
    revoked = []
    monkeypatch.setattr(google_api, "revoke_token", lambda token: revoked.append(token))

    service.disconnect(db_session, user.id)

    assert revoked == []
    assert db_session.get(GmailConnection, connection.id) is None


def test_disconnect_raises_when_not_connected(db_session, user) -> None:
    with pytest.raises(GmailNotConnected):
        service.disconnect(db_session, user.id)


def test_get_valid_access_token_raises_auth_error_when_the_stored_token_is_unreadable(
    db_session, user
) -> None:
    # GMAIL_TOKEN_ENCRYPTION_KEY rotated: only a reconnect can fix it, so it's
    # an auth error (no retries), not a generic crash.
    connection = _make_connection_encrypted_with_a_rotated_away_key(db_session, user)
    with pytest.raises(google_api.GmailAuthError):
        service.get_valid_access_token(db_session, connection)


def test_get_valid_access_token_raises_auth_error_when_an_expired_tokens_refresh_token_is_unreadable(
    db_session, user
) -> None:
    connection = _make_connection_encrypted_with_a_rotated_away_key(db_session, user)
    connection.token_expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    with pytest.raises(google_api.GmailAuthError):
        service.get_valid_access_token(db_session, connection)


def test_connect_clears_reauth_and_keeps_the_watermark(db_session, user, monkeypatch) -> None:
    # Reconnecting (not disconnect + connect) must keep sync history, so the
    # next sync is incremental rather than a full 180-day import.
    connection = _make_connection(db_session, user, expires_in_seconds=3600)
    connection.reauth_required_at = datetime.now(timezone.utc)
    connection.last_synced_message_date = date(2026, 9, 1)
    db_session.commit()
    monkeypatch.setattr(google_api, "get_profile", lambda token: {"emailAddress": "alice@gmail.com"})
    monkeypatch.setattr(google_api, "revoke_token", lambda token: None)

    service.connect(db_session, user.id, FAKE_TOKEN)

    db_session.refresh(connection)
    assert connection.reauth_required_at is None
    assert connection.last_synced_message_date == date(2026, 9, 1)


def test_list_recent_messages_flags_reauth_and_raises(db_session, user, monkeypatch) -> None:
    connection = _make_connection(db_session, user, expires_in_seconds=3600)

    def revoked(token, limit):
        raise google_api.GmailAuthError("message list failed: 401", status_code=401)

    monkeypatch.setattr(google_api, "list_message_ids", revoked)

    with pytest.raises(GmailReauthRequired):
        service.list_recent_messages(db_session, user.id, 5)
    db_session.refresh(connection)
    assert connection.reauth_required_at is not None
