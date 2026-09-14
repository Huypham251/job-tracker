from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.gmail.models import GmailConnection
from app.users.models import User


def _make_connection(user: User) -> GmailConnection:
    return GmailConnection(
        user_id=user.id,
        google_email=user.email,
        access_token_encrypted="enc-access",
        refresh_token_encrypted="enc-refresh",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )


def test_create_and_load_gmail_connection(db_session, user) -> None:
    connection = _make_connection(user)
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)

    assert connection.id is not None
    assert connection.google_email == user.email
    assert connection.created_at is not None


def test_user_id_must_be_unique(db_session, user) -> None:
    db_session.add(_make_connection(user))
    db_session.commit()

    db_session.add(_make_connection(user))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_deleting_user_cascades_to_gmail_connection(db_session, user) -> None:
    connection = _make_connection(user)
    db_session.add(connection)
    db_session.commit()
    connection_id = connection.id

    db_session.delete(user)
    db_session.commit()

    assert db_session.get(GmailConnection, connection_id) is None
