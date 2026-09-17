from uuid import uuid4

from app.applications.models import Application, ApplicationStatus
from app.classifier.schemas import EmailExtraction
from app.pipeline import service
from app.pipeline.models import ProcessedMessage
from app.sync.models import SyncJob
from datetime import date


def _summary(message_id: str = "m1", subject: str = "App received") -> dict:
    return {"id": message_id, "subject": subject, "from_": "jobs@acme.com", "date": "d", "snippet": "s"}


class _SingleResultExtractor:
    def __init__(self, extraction: EmailExtraction) -> None:
        self._extraction = extraction

    def classify_and_extract(self, *, subject, sender, date, body):
        return self._extraction


def test_process_message_ignores_non_job_email(db_session, user) -> None:
    extractor = _SingleResultExtractor(EmailExtraction(is_job_related=False, confidence=0.99))

    review_status = service.process_message(
        db_session, user.id, extractor,
        sync_job_id=None, message_id="m1", summary=_summary(), body="body",
    )

    assert review_status == "ignored"
    stored = db_session.query(ProcessedMessage).one()
    assert stored.review_status == "ignored"
    assert stored.sync_job_id is None


def test_process_message_auto_creates_a_new_application_when_confident(db_session, user) -> None:
    extractor = _SingleResultExtractor(
        EmailExtraction(is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied")
    )

    review_status = service.process_message(
        db_session, user.id, extractor,
        sync_job_id=None, message_id="m1", summary=_summary(), body="body",
    )

    assert review_status == "auto_applied"
    app = db_session.query(Application).one()
    assert app.company == "Acme"
    assert app.source == "gmail"


def test_process_message_always_queues_when_touching_a_manual_application(db_session, user) -> None:
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.applied, source="manual"
    )
    db_session.add(existing)
    db_session.commit()
    extractor = _SingleResultExtractor(
        EmailExtraction(is_job_related=True, confidence=0.99, company="Acme", position="SWE", status="interview")
    )

    review_status = service.process_message(
        db_session, user.id, extractor,
        sync_job_id=None, message_id="m1", summary=_summary(subject="Interview invite"), body="body",
    )

    assert review_status == "pending_review"
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.applied  # untouched


def test_process_message_records_the_owning_sync_job(db_session, user) -> None:
    job = SyncJob(user_id=user.id, job_type="initial", window_start=date(2026, 1, 1))
    db_session.add(job)
    db_session.commit()
    extractor = _SingleResultExtractor(EmailExtraction(is_job_related=False, confidence=0.99))

    service.process_message(
        db_session, user.id, extractor,
        sync_job_id=job.id, message_id="m1", summary=_summary(), body="body",
    )

    stored = db_session.query(ProcessedMessage).one()
    assert stored.sync_job_id == job.id


def test_process_message_returns_none_and_skips_on_persistence_failure(db_session, user) -> None:
    oversized_subject = "x" * 1000  # ProcessedMessage.subject is String(998)
    extractor = _SingleResultExtractor(
        EmailExtraction(is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied")
    )

    review_status = service.process_message(
        db_session, user.id, extractor,
        sync_job_id=None, message_id="m1", summary=_summary(subject=oversized_subject), body="body",
    )

    assert review_status is None
    assert db_session.query(ProcessedMessage).count() == 0
    assert db_session.query(Application).count() == 0
