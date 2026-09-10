import pytest
from pydantic import ValidationError

from app.applications.models import ApplicationStatus
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def test_create_defaults_status_to_applied() -> None:
    schema = ApplicationCreate(company="Acme", position="SWE Intern")
    assert schema.status is ApplicationStatus.applied
    assert schema.applied_at is None


def test_create_trims_whitespace() -> None:
    schema = ApplicationCreate(company="  Acme  ", position="  SWE  ")
    assert schema.company == "Acme"
    assert schema.position == "SWE"


def test_create_rejects_blank_company() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="   ", position="SWE")


def test_create_rejects_overlong_company() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="x" * 256, position="SWE")


def test_create_accepts_valid_status() -> None:
    schema = ApplicationCreate(company="Acme", position="SWE", status="interview")
    assert schema.status is ApplicationStatus.interview


def test_create_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="Acme", position="SWE", status="ghosted")


def test_update_allows_single_field() -> None:
    schema = ApplicationUpdate(status="offer")
    assert schema.model_dump(exclude_unset=True) == {"status": ApplicationStatus.offer}


def test_update_rejects_empty_body() -> None:
    with pytest.raises(ValidationError):
        ApplicationUpdate()


def test_update_trims_company_when_present() -> None:
    schema = ApplicationUpdate(company="  Acme  ")
    assert schema.company == "Acme"
