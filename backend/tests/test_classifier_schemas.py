from datetime import date

import pytest
from pydantic import ValidationError

from app.classifier.schemas import EmailExtraction


def test_email_extraction_accepts_full_job_related_payload() -> None:
    extraction = EmailExtraction(
        is_job_related=True,
        confidence=0.9,
        company="Acme Corp",
        position="Backend Engineer",
        status="applied",
        status_date=date(2026, 1, 5),
        reasoning="matched applied phrase",
    )
    assert extraction.company == "Acme Corp"
    assert extraction.status == "applied"


def test_email_extraction_defaults_optional_fields_to_none() -> None:
    extraction = EmailExtraction(is_job_related=False, confidence=0.1)
    assert extraction.company is None
    assert extraction.position is None
    assert extraction.status is None
    assert extraction.status_date is None
    assert extraction.reasoning is None


def test_email_extraction_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        EmailExtraction(is_job_related=True, confidence=1.5)


def test_email_extraction_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        EmailExtraction(is_job_related=True, confidence=0.9, status="withdrawn")


def test_email_extraction_rejects_overlong_company() -> None:
    with pytest.raises(ValidationError):
        EmailExtraction(is_job_related=True, confidence=0.9, company="x" * 256)
