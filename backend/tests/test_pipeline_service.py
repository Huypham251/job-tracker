from uuid import uuid4

import pytest

from app.applications.models import Application, ApplicationStatus
from app.core.config import settings
from app.gmail import google_api
from app.gmail.crypto import encrypt_token
from app.gmail.exceptions import GmailNotConnected
from app.gmail.models import GmailConnection
from app.llm.schemas import EmailExtraction
from app.pipeline import service
from app.pipeline.models import ProcessedMessage
from datetime import datetime, timedelta, timezone


class _FakeExtractor:
    def __init__(self, results: dict[str, EmailExtraction]) -> None:
        self._results = results
        self.calls: list[str] = []

    def classify_and_extract(self, *, subject, sender, date, body):
        self.calls.append(subject)
        return self._results[subject]


def _connect_gmail(db_session, user) -> GmailConnection:
    connection = GmailConnection(
        user_id=user.id,
        google_email="alice@gmail.com",
        access_token_encrypted=encrypt_token("access"),
        refresh_token_encrypted=encrypt_token("refresh"),
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def _make_summary(message_id: str, subject: str) -> dict:
    return {"id": message_id, "subject": subject, "from_": "jobs@acme.com", "date": "d", "snippet": "s"}


def test_process_inbox_raises_when_not_connected(db_session, user) -> None:
    fake_extractor = _FakeExtractor({})
    with pytest.raises(GmailNotConnected):
        service.process_inbox(db_session, user.id, fake_extractor)


def test_process_inbox_ignores_non_job_email(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Newsletter"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor({"Newsletter": EmailExtraction(is_job_related=False, confidence=0.99)})

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.processed == 1
    assert result.ignored == 1
    assert result.auto_applied == 0
    assert result.queued_for_review == 0
    stored = db_session.query(ProcessedMessage).one()
    assert stored.review_status == "ignored"


def test_process_inbox_auto_creates_a_new_application_when_confident(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "App received"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "App received": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.auto_applied == 1
    app = db_session.query(Application).one()
    assert app.company == "Acme"
    assert app.source == "gmail"
    stored = db_session.query(ProcessedMessage).one()
    assert stored.review_status == "auto_applied"
    assert stored.matched_application_id == app.id


def test_process_inbox_auto_updates_a_gmail_sourced_application(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.applied, source="gmail"
    )
    db_session.add(existing)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Interview invite"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Interview invite": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="interview"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.auto_applied == 1
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.interview


def test_process_inbox_always_queues_when_touching_a_manual_application(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.applied, source="manual"
    )
    db_session.add(existing)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Interview invite"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Interview invite": EmailExtraction(
                is_job_related=True, confidence=0.99, company="Acme", position="SWE", status="interview"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.queued_for_review == 1
    assert result.auto_applied == 0
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.applied  # untouched


def test_process_inbox_queues_low_confidence_extractions(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Maybe a job email"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Maybe a job email": EmailExtraction(
                is_job_related=True, confidence=0.4, company="Acme", position="SWE", status="applied"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.queued_for_review == 1
    assert db_session.query(Application).count() == 0


def test_process_inbox_never_regresses_a_specific_status_to_other(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    existing = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.interview, source="gmail"
    )
    db_session.add(existing)
    db_session.commit()

    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "Some update"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "Some update": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="other"
            )
        }
    )

    result = service.process_inbox(db_session, user.id, extractor)

    assert result.ignored == 1
    db_session.refresh(existing)
    assert existing.status == ApplicationStatus.interview  # untouched


def test_process_inbox_respects_the_batch_limit(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    captured_limits = []

    def fake_list_message_ids(token, limit):
        captured_limits.append(limit)
        return []

    monkeypatch.setattr(google_api, "list_message_ids", fake_list_message_ids)
    extractor = _FakeExtractor({})

    service.process_inbox(db_session, user.id, extractor)

    assert captured_limits == [settings.pipeline_batch_limit]


def test_process_inbox_is_idempotent(db_session, user, monkeypatch) -> None:
    _connect_gmail(db_session, user)
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(google_api, "get_message_summary", lambda token, mid: _make_summary(mid, "App received"))
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    extractor = _FakeExtractor(
        {
            "App received": EmailExtraction(
                is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
            )
        }
    )

    service.process_inbox(db_session, user.id, extractor)
    second_result = service.process_inbox(db_session, user.id, extractor)

    assert second_result.processed == 0
    assert len(extractor.calls) == 1  # never re-extracted
    assert db_session.query(Application).count() == 1
