from uuid import uuid4

import pytest

from app.applications.models import Application, ApplicationStatus
from app.pipeline import service
from app.pipeline.exceptions import ReviewItemNotFound
from app.pipeline.models import ProcessedMessage
from app.pipeline.schemas import ReviewDecision


def _make_pending_item(db_session, user, **overrides) -> ProcessedMessage:
    defaults = dict(
        user_id=user.id,
        gmail_message_id="m1",
        subject="Interview invite",
        sender="jobs@acme.com",
        message_date="d",
        snippet="s",
        is_job_related=True,
        confidence=0.5,
        extracted_company="Acme",
        extracted_position="SWE",
        extracted_status="interview",
        proposed_action="create",
        review_status="pending_review",
    )
    defaults.update(overrides)
    item = ProcessedMessage(**defaults)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def test_list_review_queue_returns_only_pending_items_for_this_user(db_session, user, other_user) -> None:
    pending = _make_pending_item(db_session, user)
    _make_pending_item(db_session, user, gmail_message_id="m2", review_status="approved")
    _make_pending_item(db_session, other_user, gmail_message_id="m3")

    items = service.list_review_queue(db_session, user.id)

    assert [item.id for item in items] == [pending.id]


def test_approve_creates_application_from_proposed_create(db_session, user) -> None:
    item = _make_pending_item(db_session, user)

    application = service.approve_review_item(db_session, user.id, item.id, ReviewDecision())

    assert application.company == "Acme"
    assert application.status == ApplicationStatus.interview
    assert application.source == "gmail"
    db_session.refresh(item)
    assert item.review_status == "approved"
    assert item.matched_application_id == application.id


def test_approve_applies_edited_fields_over_the_extraction(db_session, user) -> None:
    item = _make_pending_item(db_session, user)

    application = service.approve_review_item(
        db_session, user.id, item.id, ReviewDecision(company="Acme Corp")
    )

    assert application.company == "Acme Corp"


def test_approve_updates_an_existing_matched_application(db_session, user) -> None:
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.applied, source="manual"
    )
    db_session.add(existing)
    db_session.commit()
    item = _make_pending_item(
        db_session, user, proposed_action="update", matched_application_id=existing.id
    )

    application = service.approve_review_item(db_session, user.id, item.id, ReviewDecision())

    assert application.id == existing.id
    assert application.status == ApplicationStatus.interview
    assert application.source == "manual"  # approving an update never changes provenance


def test_reject_marks_item_rejected_without_touching_applications(db_session, user) -> None:
    item = _make_pending_item(db_session, user)

    service.reject_review_item(db_session, user.id, item.id)

    db_session.refresh(item)
    assert item.review_status == "rejected"
    assert db_session.query(Application).count() == 0


def test_approve_raises_for_unknown_item(db_session, user) -> None:
    with pytest.raises(ReviewItemNotFound):
        service.approve_review_item(db_session, user.id, uuid4(), ReviewDecision())


def test_reject_raises_for_unknown_item(db_session, user) -> None:
    with pytest.raises(ReviewItemNotFound):
        service.reject_review_item(db_session, user.id, uuid4())


def test_approve_raises_when_item_belongs_to_another_user(db_session, user, other_user) -> None:
    item = _make_pending_item(db_session, other_user)

    with pytest.raises(ReviewItemNotFound):
        service.approve_review_item(db_session, user.id, item.id, ReviewDecision())


def test_approve_raises_for_already_reviewed_item(db_session, user) -> None:
    item = _make_pending_item(db_session, user, review_status="approved")

    with pytest.raises(ReviewItemNotFound):
        service.approve_review_item(db_session, user.id, item.id, ReviewDecision())
