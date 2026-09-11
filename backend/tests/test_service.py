import uuid

import pytest

from app.applications import service
from app.applications.exceptions import ApplicationNotFound
from app.applications.models import ApplicationStatus
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def test_create_persists_with_defaults(db_session, user) -> None:
    created = service.create_application(
        db_session, user.id, ApplicationCreate(company="Acme", position="SWE Intern")
    )
    assert created.id is not None
    assert created.user_id == user.id
    assert created.status is ApplicationStatus.applied
    assert created.created_at is not None
    assert created.updated_at is not None


def test_get_missing_raises(db_session, user) -> None:
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, user.id, uuid.uuid4())


def test_list_returns_newest_first(db_session, user) -> None:
    first = service.create_application(
        db_session, user.id, ApplicationCreate(company="A", position="P1")
    )
    second = service.create_application(
        db_session, user.id, ApplicationCreate(company="B", position="P2")
    )
    listed = service.list_applications(db_session, user.id)
    assert [row.id for row in listed] == [second.id, first.id]


def test_update_applies_only_provided_fields(db_session, user) -> None:
    created = service.create_application(
        db_session, user.id, ApplicationCreate(company="A", position="P")
    )
    updated = service.update_application(
        db_session, user.id, created.id, ApplicationUpdate(status="interview")
    )
    assert updated.status is ApplicationStatus.interview
    assert updated.company == "A"
    assert updated.position == "P"


def test_update_missing_raises(db_session, user) -> None:
    with pytest.raises(ApplicationNotFound):
        service.update_application(
            db_session, user.id, uuid.uuid4(), ApplicationUpdate(status="offer")
        )


def test_delete_removes_row(db_session, user) -> None:
    created = service.create_application(
        db_session, user.id, ApplicationCreate(company="A", position="P")
    )
    service.delete_application(db_session, user.id, created.id)
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, user.id, created.id)


def test_delete_missing_raises(db_session, user) -> None:
    with pytest.raises(ApplicationNotFound):
        service.delete_application(db_session, user.id, uuid.uuid4())


def test_list_excludes_other_users_applications(db_session, user, other_user) -> None:
    service.create_application(
        db_session, user.id, ApplicationCreate(company="Mine", position="P")
    )
    service.create_application(
        db_session, other_user.id, ApplicationCreate(company="Theirs", position="P")
    )
    listed = service.list_applications(db_session, user.id)
    assert [row.company for row in listed] == ["Mine"]


def test_get_other_users_application_raises(db_session, user, other_user) -> None:
    theirs = service.create_application(
        db_session, other_user.id, ApplicationCreate(company="Theirs", position="P")
    )
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, user.id, theirs.id)
