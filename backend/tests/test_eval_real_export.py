import json

import pytest

from app.applications.models import Application, ApplicationStatus
from app.core.privacy import message_ref
from app.gmail.google_api import GmailAuthError, GoogleApiError
from app.pipeline.models import ProcessedMessage
from evaluation.real import dataset
from evaluation.real.export import export_candidates, select_candidates
from evaluation.real.privacy import FICTIONAL_GIVEN, Identity

IDENTITY = Identity(name="Alice", email="alice@example.com")


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(tmp_path / "eval"))


def _row(db, user, message_id: str, *, status: str, subject: str, matched=None) -> ProcessedMessage:
    row = ProcessedMessage(
        user_id=user.id, gmail_message_id=message_id, subject=subject, sender="x@kestrel.com",
        message_date="Mon, 5 Jan 2026 10:00:00 +0000", snippet="", is_job_related=status != "ignored",
        confidence=0.4, review_status=status, matched_application_id=matched,
    )
    db.add(row)
    db.commit()
    return row


def _fetch(message_id: str):
    return (
        {"subject": "Thanks for applying, Alice", "from_": "Jane Roe <jane.roe@kestrel.com>", "date": "d"},
        "text/html",
        "<p>Hi Alice, visit https://kestrel.com/x</p>",
    )


def test_selects_reviewed_rows_keyword_hits_and_a_seeded_sample(db_session, user) -> None:
    _row(db_session, user, "p1", status="pending_review", subject="Thank you for applying")
    _row(db_session, user, "i1", status="ignored", subject="Your application was received")
    for i in range(5):
        _row(db_session, user, f"n{i}", status="ignored", subject=f"Weekly deals {i}")
    picked = select_candidates(db_session, user.id, ignored_sample=2, seed=11)
    kinds = [(row.gmail_message_id, kind) for row, kind in picked]
    assert kinds[:2] == [("p1", "reviewed"), ("i1", "keyword")]
    assert [k for _, k in kinds[2:]] == ["sample", "sample"]
    assert picked == select_candidates(db_session, user.id, ignored_sample=2, seed=11)


def test_export_writes_scrubbed_records_with_review_values(db_session, user) -> None:
    app = Application(user_id=user.id, company="Kestrel", position="Data Engineer",
                      status=ApplicationStatus.applied, source="gmail")
    db_session.add(app)
    db_session.commit()
    _row(db_session, user, "RAWID-QX", status="approved", subject="s", matched=app.id)

    counts = export_candidates(db_session, user.id, _fetch, IDENTITY, ignored_sample=0, seed=11)

    assert counts == {"exported": 1}
    record = dataset.load_raw(message_ref("RAWID-QX"))
    assert "Alice" not in json.dumps(record)
    assert f"Hi {FICTIONAL_GIVEN}" in record["raw_body"]
    assert "person@kestrel.com" in record["sender"]
    assert record["review"] == {"company": "Kestrel", "position": "Data Engineer", "status": "applied"}
    assert record["origin"] == "approved" and record["selected_by"] == "reviewed"
    assert "RAWID" not in json.dumps(record)  # only the hashed ref, never the raw ID


def test_export_is_resumable_and_counts_fetch_failures(db_session, user) -> None:
    _row(db_session, user, "p1", status="pending_review", subject="s")
    _row(db_session, user, "p2", status="pending_review", subject="s")

    def flaky(message_id: str):
        if message_id == "p2":
            raise GoogleApiError("message fetch failed: HTTP 404", status_code=404)
        return _fetch(message_id)

    assert export_candidates(db_session, user.id, flaky, IDENTITY, ignored_sample=0, seed=11) == {"exported": 1, "fetch_failed": 1}
    assert export_candidates(db_session, user.id, _fetch, IDENTITY, ignored_sample=0, seed=11) == {"already_exported": 1, "exported": 1}


def test_export_stops_when_gmail_needs_reconnecting(db_session, user) -> None:
    _row(db_session, user, "p1", status="pending_review", subject="s")

    def revoked(message_id: str):
        raise GmailAuthError("token refresh failed: invalid_grant")

    with pytest.raises(SystemExit):
        export_candidates(db_session, user.id, revoked, IDENTITY, ignored_sample=0, seed=11)
