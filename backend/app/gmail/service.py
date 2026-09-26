import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TypeVar
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.gmail import google_api
from app.gmail.crypto import decrypt_token, encrypt_token
from app.gmail.exceptions import GmailNotConnected, GmailReauthRequired
from app.gmail.models import GmailConnection

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Refresh a minute before the stored access token actually expires, rather
# than racing expiry mid-request.
REFRESH_BUFFER = timedelta(seconds=60)


def _revoke_stored_refresh_token(connection: GmailConnection, user_id: UUID, action: str) -> None:
    """Best-effort revoke of the grant a connection row holds, before that row
    is overwritten (reconnect) or deleted (disconnect). Never fatal: a
    lingering grant at Google is harmless, while failing here would leave the
    user unable to reconnect or disconnect at all. The InvalidToken case is
    what rotating GMAIL_TOKEN_ENCRYPTION_KEY produces for every existing row —
    the old token is unreadable, so there's nothing we can revoke; the user
    can still remove the stale grant from their Google account settings."""
    try:
        refresh_token = decrypt_token(connection.refresh_token_encrypted)
    except InvalidToken:
        logger.warning(
            "Stored Gmail token for user %s is unreadable (encryption key rotated?); skipping revoke on %s",
            user_id, action,
        )
        return
    try:
        google_api.revoke_token(refresh_token)
    except google_api.GoogleApiError:
        # A failed revoke leaves a stale grant at Google — logged, not fatal.
        logger.warning("Failed to revoke Gmail token for user %s on %s", user_id, action)


def _decrypt_or_reauth(ciphertext: str) -> str:
    try:
        return decrypt_token(ciphertext)
    except InvalidToken as exc:
        # GMAIL_TOKEN_ENCRYPTION_KEY was rotated since this row was written:
        # the grant is unreadable, and only a reconnect can replace it.
        raise google_api.GmailAuthError("stored Gmail token is unreadable") from exc


def mark_reauth_required(db: Session, connection: GmailConnection) -> None:
    connection.reauth_required_at = datetime.now(timezone.utc)
    db.commit()


def get_connection(db: Session, user_id: UUID) -> GmailConnection | None:
    return db.scalars(
        select(GmailConnection).where(GmailConnection.user_id == user_id)
    ).one_or_none()


def connect(db: Session, user_id: UUID, token: dict) -> GmailConnection:
    access_token = token["access_token"]
    refresh_token = token["refresh_token"]
    expires_in = token["expires_in"]
    scope = token["scope"]

    # gmail.readonly alone is enough to call this endpoint — we don't
    # request the "email"/"openid" scopes just to learn the connected
    # address.
    profile = google_api.get_profile(access_token)

    connection = get_connection(db, user_id)
    if connection is None:
        connection = GmailConnection(user_id=user_id)
        db.add(connection)
    else:
        # Overwriting our row with the new tokens matters more than a lagging
        # revoke upstream.
        _revoke_stored_refresh_token(connection, user_id, "reconnect")

    connection.google_email = profile["emailAddress"]
    connection.access_token_encrypted = encrypt_token(access_token)
    connection.refresh_token_encrypted = encrypt_token(refresh_token)
    connection.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    connection.scope = scope
    connection.reauth_required_at = None

    db.commit()
    db.refresh(connection)
    return connection


def get_valid_access_token(db: Session, connection: GmailConnection) -> str:
    if connection.token_expiry - datetime.now(timezone.utc) > REFRESH_BUFFER:
        return _decrypt_or_reauth(connection.access_token_encrypted)
    return _refresh_access_token(db, connection)


def force_refresh_access_token(db: Session, connection: GmailConnection) -> str:
    """For a Gmail 401 on a token we believed valid (Phase 10): re-read the
    row first — a reconnect may have replaced the grant meanwhile — then
    refresh regardless of the stored expiry. Raises GmailAuthError if the
    row is gone (disconnected) or Google refuses the refresh."""
    try:
        db.refresh(connection)
    except InvalidRequestError as exc:
        raise google_api.GmailAuthError("Gmail connection was removed") from exc
    return _refresh_access_token(db, connection)


def _refresh_access_token(db: Session, connection: GmailConnection) -> str:
    refresh_token = _decrypt_or_reauth(connection.refresh_token_encrypted)
    token = google_api.refresh_access_token(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        refresh_token=refresh_token,
    )
    connection.access_token_encrypted = encrypt_token(token["access_token"])
    connection.token_expiry = datetime.now(timezone.utc) + timedelta(
        seconds=token["expires_in"]
    )
    db.commit()
    db.refresh(connection)
    return token["access_token"]


def call_with_fresh_token(db: Session, connection: GmailConnection, call: Callable[[str], T]) -> T:
    """Runs a Gmail call with a valid access token (checked before every call, not
    once per page: a page of new messages can outlast the token's last minute). A
    401 still gets one forced refresh and retry before it counts as a revoked grant
    — the token may have expired mid-call, or a reconnect may have replaced the
    grant. GmailAuthError out of here means reconnect. (Moved from the sync worker
    in Phase 11 so the evaluation export and the re-evaluation command share it.)"""
    try:
        return call(get_valid_access_token(db, connection))
    except google_api.GmailAuthError as exc:
        if exc.status_code != 401:
            raise
    return call(force_refresh_access_token(db, connection))


def list_recent_messages(db: Session, user_id: UUID, limit: int) -> list[dict]:
    connection = get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    try:
        access_token = get_valid_access_token(db, connection)
        message_ids = google_api.list_message_ids(access_token, limit=limit)
        return [google_api.get_message_summary(access_token, message_id) for message_id in message_ids]
    except google_api.GmailAuthError:
        mark_reauth_required(db, connection)
        raise GmailReauthRequired(user_id) from None


def disconnect(db: Session, user_id: UUID) -> None:
    connection = get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    # We still delete our row even if the revoke can't happen: a disconnect
    # button that doesn't disconnect locally is worse than a lagging revoke
    # upstream.
    _revoke_stored_refresh_token(connection, user_id, "disconnect")

    db.delete(connection)
    db.commit()
