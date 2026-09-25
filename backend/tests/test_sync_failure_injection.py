"""Phase 10 failure-injection matrix for the sync worker.

Every failure here is real: a genuine Postgres error (`SELECT 1/0`, the Phase 5
technique — a plain Python exception doesn't poison the session and can hide
bugs), a SQLAlchemy OperationalError shaped like a Neon connection drop, or a
Gmail/token failure at the google_api seam. The invariants checked after each:
process_job never raises, the job ends in a recoverable state, a retry resumes
from the last committed page, and no message is ever stored twice.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.classifier.schemas import EmailExtraction
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.crypto import encrypt_token
from app.gmail.models import GmailConnection
from app.pipeline.models import ProcessedMessage
from app.sync.models import SyncJob
from app.sync.worker import process_job, sweep_orphans

PAGES = {None: (["m1"], "page-2"), "page-2": (["m2"], None)}
# Commits in a clean two-page run: per page — messages_seen, the message's
# ProcessedMessage (inside process_message), the job counters, page_token —
# then the success tail.
CLEAN_RUN_COMMITS = 9
# The success tail (status=completed + watermark) sits outside process_job's
# error handler by design (Phase 5): a failure there leaves the job "running"
# for the orphan sweep / stale reaper, and resuming is cheap because every
# page is checkpointed and every message is stored.
SUCCESS_TAIL_COMMIT = CLEAN_RUN_COMMITS


class _Extractor:
    def classify_and_extract(self, *, subject, sender, date, body):
        return EmailExtraction(is_job_related=False, confidence=0.99)


@pytest.fixture
def gmail(db_session, user, monkeypatch) -> GmailConnection:
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
    monkeypatch.setattr(
        google_api, "list_message_ids_page",
        lambda token, *, query, page_token, max_results: PAGES[page_token],
    )
    monkeypatch.setattr(
        google_api, "get_message",
        lambda token, mid: ({"id": mid, "subject": mid, "from_": "x@y.com", "date": "d", "snippet": "s"}, "b"),
    )
    return connection


def _claimed_job(db_session, user) -> SyncJob:
    job = SyncJob(
        user_id=user.id, job_type="initial", window_start=date(2026, 1, 1),
        status="running", started_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    return job


def _poison_commits(db_session, monkeypatch, failing: set[int]) -> list[int]:
    """Makes the n-th commit (1-based) for each n in `failing` hit a real
    Postgres error instead of committing."""
    real_commit = db_session.commit
    calls = [0]

    def commit():
        calls[0] += 1
        if calls[0] in failing:
            db_session.execute(text("SELECT 1/0"))
        return real_commit()

    monkeypatch.setattr(db_session, "commit", commit)
    return calls


def _resume(db_session, job) -> str:
    """What the next claim does: mark it running again and process it."""
    job.status = "running"
    job.next_attempt_at = datetime.now(timezone.utc)
    db_session.commit()
    return process_job(db_session, job, _Extractor())


def _stored(db_session) -> dict[str, int]:
    rows = db_session.execute(
        text("select gmail_message_id, count(*) from processed_messages group by 1")
    ).all()
    return dict(rows)


def test_a_clean_run_makes_the_expected_number_of_commits(db_session, user, gmail, monkeypatch) -> None:
    job = _claimed_job(db_session, user)
    calls = _poison_commits(db_session, monkeypatch, set())

    assert process_job(db_session, job, _Extractor()) == "completed"
    assert calls[0] == CLEAN_RUN_COMMITS  # keeps the matrix below honest


@pytest.mark.parametrize("failing_commit", range(1, SUCCESS_TAIL_COMMIT))
def test_a_db_failure_at_any_commit_point_is_recoverable(
    db_session, user, gmail, monkeypatch, failing_commit
) -> None:
    job = _claimed_job(db_session, user)
    calls = _poison_commits(db_session, monkeypatch, {failing_commit})

    first = process_job(db_session, job, _Extractor())  # must not raise

    db_session.refresh(job)
    assert first in ("completed", "retrying")
    if first == "retrying":
        assert job.status == "queued" and job.attempts == 1
        assert _resume(db_session, job) == "completed"
    db_session.refresh(job)
    assert job.status == "completed"
    stored = _stored(db_session)
    assert all(count == 1 for count in stored.values())  # never stored twice
    # Every message is accounted for: stored, or counted as a failed message
    # (a failed ProcessedMessage write is skipped, not retried — Phase 4).
    assert len(stored) + job.failed_count == 2
    assert calls[0] > failing_commit


@pytest.mark.parametrize(
    "failing",
    [
        pytest.param({1, 2}, id="error-handler-commit"),  # commit 1 fails, then the handler's requeue commit
        pytest.param({SUCCESS_TAIL_COMMIT}, id="success-tail-commit"),
    ],
)
def test_a_failure_process_job_cannot_record_leaves_a_job_the_sweep_recovers(
    db_session, user, gmail, monkeypatch, failing
) -> None:
    job = _claimed_job(db_session, user)
    _poison_commits(db_session, monkeypatch, failing)

    with pytest.raises(Exception):
        process_job(db_session, job, _Extractor())

    db_session.rollback()
    db_session.refresh(job)
    assert job.status == "running"  # stuck: exactly what sweep_orphans is for
    db_session.execute(
        text("UPDATE sync_jobs SET updated_at = now() - interval '5 minutes' WHERE id = :id"), {"id": job.id}
    )
    db_session.commit()

    assert sweep_orphans(db_session, "initial") == 1
    db_session.refresh(job)
    assert job.status == "queued"
    assert _resume(db_session, job) == "completed"
    assert _stored(db_session) == {"m1": 1, "m2": 1}


def test_a_neon_style_connection_drop_mid_job_resumes_from_the_checkpoint(
    db_session, user, gmail, monkeypatch
) -> None:
    job = _claimed_job(db_session, user)

    def drop_on_page_two(token, *, query, page_token, max_results):
        if page_token == "page-2":
            raise OperationalError("SELECT 1", {}, Exception("SSL connection has been closed unexpectedly"))
        return PAGES[page_token]

    monkeypatch.setattr(google_api, "list_message_ids_page", drop_on_page_two)
    assert process_job(db_session, job, _Extractor()) == "retrying"
    db_session.refresh(job)
    assert job.page_token == "page-2"

    seen = []
    monkeypatch.setattr(
        google_api, "list_message_ids_page",
        lambda token, *, query, page_token, max_results: seen.append(page_token) or PAGES[page_token],
    )
    assert _resume(db_session, job) == "completed"
    assert seen == ["page-2"]
    assert _stored(db_session) == {"m1": 1, "m2": 1}


def _raise(exc):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


@pytest.mark.parametrize(
    "case, outcome, error_code",
    [
        ("refresh_invalid_grant", "failed", "gmail_reauth_required"),
        ("list_401", "failed", "gmail_reauth_required"),
        ("message_401", "failed", "gmail_reauth_required"),
        ("unreadable_stored_token", "failed", "gmail_reauth_required"),
        ("refresh_invalid_client", "retrying", None),
        ("list_503", "retrying", None),
        ("list_network_error", "retrying", None),
    ],
)
def test_token_and_gmail_failures_end_in_the_right_state(
    db_session, user, gmail, monkeypatch, case, outcome, error_code
) -> None:
    job = _claimed_job(db_session, user)
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    if case.startswith("refresh"):
        gmail.token_expiry = expired
        db_session.commit()
        body = {"error": "invalid_grant"} if case == "refresh_invalid_grant" else {"error": "invalid_client"}
        status = 400 if case == "refresh_invalid_grant" else 401

        class _Response:
            status_code = status

            def json(self):
                return body

        monkeypatch.setattr(google_api.httpx, "post", lambda *a, **k: _Response())
    elif case == "unreadable_stored_token":
        gmail.access_token_encrypted = "not-a-fernet-token"
        db_session.commit()
    elif case in ("list_401", "message_401"):
        # A forced refresh succeeds, but Gmail still answers 401.
        monkeypatch.setattr(
            google_api, "refresh_access_token", lambda **kw: {"access_token": "fresh", "expires_in": 3600}
        )
    if case == "list_401":
        monkeypatch.setattr(google_api, "list_message_ids_page", _raise(google_api.GmailAuthError("x", status_code=401)))
    elif case == "message_401":
        monkeypatch.setattr(google_api, "get_message", _raise(google_api.GmailAuthError("x", status_code=401)))
    elif case == "list_503":
        monkeypatch.setattr(google_api, "list_message_ids_page", _raise(google_api.GoogleApiError("x", status_code=503)))
    elif case == "list_network_error":
        monkeypatch.setattr(google_api, "list_message_ids_page", _raise(google_api.GoogleApiError("x")))

    assert process_job(db_session, job, _Extractor()) == outcome

    db_session.refresh(job)
    db_session.refresh(gmail)
    assert job.error_code == error_code
    assert (gmail.reauth_required_at is not None) is (error_code is not None)
    assert job.attempts == (0 if error_code else 1)


def test_an_expiring_access_token_is_refreshed_not_treated_as_a_failure(
    db_session, user, gmail, monkeypatch
) -> None:
    user_id = user.id  # process_job expunges the session between pages
    gmail.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=30)  # inside the refresh buffer
    db_session.commit()
    refreshed = []
    monkeypatch.setattr(
        google_api, "refresh_access_token",
        lambda **kw: refreshed.append(1) or {"access_token": "new", "expires_in": 3600},
    )
    job = _claimed_job(db_session, user)

    assert process_job(db_session, job, _Extractor()) == "completed"
    assert refreshed  # refreshed once per page as needed, never failed
    assert gmail_service.get_connection(db_session, user_id).reauth_required_at is None
