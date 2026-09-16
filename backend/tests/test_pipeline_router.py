from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.applications.models import Application, ApplicationStatus
from app.gmail import google_api
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.classifier import extractor as classifier_extractor
from app.classifier.schemas import EmailExtraction
from app.pipeline.models import ProcessedMessage

BASE = "/api/v1/pipeline"


@pytest.fixture
def connected_gmail(db_session, user) -> GmailConnection:
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


@pytest.fixture
def pending_item(db_session, user) -> ProcessedMessage:
    item = ProcessedMessage(
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
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/process"),
        ("get", "/review"),
        ("post", "/review/00000000-0000-0000-0000-000000000000/approve"),
        ("post", "/review/00000000-0000-0000-0000-000000000000/reject"),
    ],
)
def test_pipeline_endpoints_require_authentication(client: TestClient, method, path) -> None:
    response = getattr(client, method)(f"{BASE}{path}")
    assert response.status_code == 401


def test_process_endpoint_returns_summary(
    auth_client: TestClient, connected_gmail, monkeypatch
) -> None:
    monkeypatch.setattr(google_api, "list_message_ids", lambda token, limit: ["m1"])
    monkeypatch.setattr(
        google_api,
        "get_message_summary",
        lambda token, mid: {"id": mid, "subject": "App received", "from_": "jobs@acme.com", "date": "d", "snippet": "s"},
    )
    monkeypatch.setattr(google_api, "get_message_body", lambda token, mid: "body")
    monkeypatch.setattr(
        classifier_extractor.RuleBasedExtractor,
        "classify_and_extract",
        lambda self, **kwargs: EmailExtraction(
            is_job_related=True, confidence=0.95, company="Acme", position="SWE", status="applied"
        ),
    )

    response = auth_client.post(f"{BASE}/process")

    assert response.status_code == 200
    body = response.json()
    assert body == {"processed": 1, "auto_applied": 1, "queued_for_review": 0, "ignored": 0}


def test_process_endpoint_requires_gmail_connection(auth_client: TestClient) -> None:
    response = auth_client.post(f"{BASE}/process")
    assert response.status_code == 404


def test_review_list_returns_pending_items(auth_client: TestClient, pending_item) -> None:
    response = auth_client.get(f"{BASE}/review")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(pending_item.id)
    assert body[0]["extracted_company"] == "Acme"


def test_review_approve_creates_application(auth_client: TestClient, pending_item) -> None:
    response = auth_client.post(f"{BASE}/review/{pending_item.id}/approve", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["company"] == "Acme"
    assert body["source"] == "gmail"


def test_review_approve_with_edits_overrides_extraction(auth_client: TestClient, pending_item) -> None:
    response = auth_client.post(
        f"{BASE}/review/{pending_item.id}/approve", json={"company": "Acme Corp"}
    )

    assert response.status_code == 200
    assert response.json()["company"] == "Acme Corp"


def test_review_reject_returns_204(auth_client: TestClient, pending_item) -> None:
    response = auth_client.post(f"{BASE}/review/{pending_item.id}/reject")
    assert response.status_code == 204


def test_review_approve_404_for_unknown_item(auth_client: TestClient) -> None:
    response = auth_client.post(
        f"{BASE}/review/00000000-0000-0000-0000-000000000000/approve", json={}
    )
    assert response.status_code == 404


def test_cross_user_isolation(auth_client: TestClient, other_auth_client: TestClient, pending_item) -> None:
    other_list = other_auth_client.get(f"{BASE}/review")
    assert other_list.json() == []

    other_approve = other_auth_client.post(f"{BASE}/review/{pending_item.id}/approve", json={})
    assert other_approve.status_code == 404
