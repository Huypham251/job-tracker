import uuid

import pytest

from app.applications import service
from app.applications.exceptions import ApplicationNotFound
from app.applications.models import ApplicationStatus
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def test_create_persists_with_defaults(db_session) -> None:
    created = service.create_application(
        db_session, ApplicationCreate(company="Acme", position="SWE Intern")
    )
    assert created.id is not None
    assert created.status is ApplicationStatus.applied
    assert created.created_at is not None
    assert created.updated_at is not None


def test_get_missing_raises(db_session) -> None:
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, uuid.uuid4())


def test_list_returns_newest_first(db_session) -> None:
    first = service.create_application(
        db_session, ApplicationCreate(company="A", position="P1")
    )
    second = service.create_application(
        db_session, ApplicationCreate(company="B", position="P2")
    )
    listed = service.list_applications(db_session)
    assert [row.id for row in listed] == [second.id, first.id]


def test_update_applies_only_provided_fields(db_session) -> None:
    created = service.create_application(
        db_session, ApplicationCreate(company="A", position="P")
    )
    updated = service.update_application(
        db_session, created.id, ApplicationUpdate(status="interview")
    )
    assert updated.status is ApplicationStatus.interview
    assert updated.company == "A"
    assert updated.position == "P"


def test_update_missing_raises(db_session) -> None:
    with pytest.raises(ApplicationNotFound):
        service.update_application(
            db_session, uuid.uuid4(), ApplicationUpdate(status="offer")
        )


def test_delete_removes_row(db_session) -> None:
    created = service.create_application(
        db_session, ApplicationCreate(company="A", position="P")
    )
    service.delete_application(db_session, created.id)
    with pytest.raises(ApplicationNotFound):
        service.get_application(db_session, created.id)


def test_delete_missing_raises(db_session) -> None:
    with pytest.raises(ApplicationNotFound):
        service.delete_application(db_session, uuid.uuid4())
