from datetime import date

from app.pipeline.models import ProcessedMessage


def _make_message(user, **overrides) -> ProcessedMessage:
    defaults = dict(
        user_id=user.id,
        gmail_message_id="msg-1",
        subject="Your application to Acme",
        sender="jobs@acme.com",
        message_date="Wed, 1 Jan 2026 00:00:00 +0000",
        snippet="Thanks for applying",
        is_job_related=True,
        confidence=0.9,
        review_status="auto_applied",
    )
    defaults.update(overrides)
    return ProcessedMessage(**defaults)


def test_create_and_load_processed_message(db_session, user) -> None:
    message = _make_message(user)
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    assert message.id is not None
    assert message.extracted_company is None
    assert message.created_at is not None


def test_gmail_message_id_unique_per_user_but_not_globally(db_session, user, other_user) -> None:
    db_session.add(_make_message(user, gmail_message_id="shared-id"))
    db_session.commit()
    # Same gmail_message_id, different user — allowed (unique constraint is composite)
    db_session.add(_make_message(other_user, gmail_message_id="shared-id"))
    db_session.commit()


def test_duplicate_gmail_message_id_for_same_user_raises(db_session, user) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    db_session.add(_make_message(user, gmail_message_id="dup-id"))
    db_session.commit()

    db_session.add(_make_message(user, gmail_message_id="dup-id"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
