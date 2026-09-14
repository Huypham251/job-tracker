import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.gmail import google_api
from app.gmail.crypto import decrypt_token, encrypt_token
from app.gmail.exceptions import GmailNotConnected
from app.gmail.models import GmailConnection

logger = logging.getLogger(__name__)

# Refresh a minute before the stored access token actually expires, rather
# than racing expiry mid-request.
REFRESH_BUFFER = timedelta(seconds=60)


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
        old_refresh_token = decrypt_token(connection.refresh_token_encrypted)
        try:
            google_api.revoke_token(old_refresh_token)
        except google_api.GoogleApiError:
            # A failed revoke leaves a stale grant at Google — logged, not
            # fatal. We still proceed with the reconnect: overwriting our
            # row with the new tokens matters more than a lagging revoke
            # upstream.
            logger.warning("Failed to revoke Gmail token for user %s on reconnect", user_id)

    connection.google_email = profile["emailAddress"]
    connection.access_token_encrypted = encrypt_token(access_token)
    connection.refresh_token_encrypted = encrypt_token(refresh_token)
    connection.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    connection.scope = scope

    db.commit()
    db.refresh(connection)
    return connection


def get_valid_access_token(db: Session, connection: GmailConnection) -> str:
    if connection.token_expiry - datetime.now(timezone.utc) > REFRESH_BUFFER:
        return decrypt_token(connection.access_token_encrypted)

    refresh_token = decrypt_token(connection.refresh_token_encrypted)
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


def list_recent_messages(db: Session, user_id: UUID, limit: int) -> list[dict]:
    connection = get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    access_token = get_valid_access_token(db, connection)
    message_ids = google_api.list_message_ids(access_token, limit=limit)
    return [google_api.get_message_summary(access_token, message_id) for message_id in message_ids]


def disconnect(db: Session, user_id: UUID) -> None:
    connection = get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    refresh_token = decrypt_token(connection.refresh_token_encrypted)
    try:
        google_api.revoke_token(refresh_token)
    except google_api.GoogleApiError:
        # A failed revoke leaves a stale grant at Google — logged, not
        # fatal. We still delete our row: a disconnect button that doesn't
        # disconnect locally is worse than a lagging revoke upstream.
        logger.warning("Failed to revoke Gmail token for user %s", user_id)

    db.delete(connection)
    db.commit()
