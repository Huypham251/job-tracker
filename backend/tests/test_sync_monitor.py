from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.sync import monitor
from app.sync.models import SyncJob
from app.sync.monitor import evaluate


def _job(user, **values) -> SyncJob:
    defaults = dict(user_id=user.id, job_type="incremental", window_start=date(2026, 9, 1))
    defaults.update(values)
    return SyncJob(**defaults)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evaluate(db_session):
    now = _now()
    return evaluate(db_session, now=now, today=now.date())


def _ids(report) -> list[str]:
    return [alert.split()[0] for alert in report.alerts]


@pytest.fixture(autouse=True)
def _no_token_expiry(monkeypatch):
    monkeypatch.setattr(settings, "sync_dispatch_token_expires_on", None)


def test_healthy_state_has_no_alerts(db_session, user) -> None:
    db_session.add(_job(user, status="completed"))
    db_session.commit()

    report = _evaluate(db_session)

    assert report.alerts == []
    assert report.lines  # still prints a summary


def test_m1_alerts_on_a_job_due_but_unclaimed_for_15_minutes(db_session, user) -> None:
    db_session.add(_job(user, status="queued", next_attempt_at=_now() - timedelta(minutes=16)))
    db_session.commit()

    assert _ids(_evaluate(db_session)) == ["M1"]


def test_m1_ignores_a_just_yielded_job_and_a_backed_off_retry(db_session, user, other_user) -> None:
    db_session.add_all([
        # paused at its slice deadline seconds ago: due now, about to be claimed
        _job(user, status="queued", started_at=_now() - timedelta(hours=1),
             next_attempt_at=_now() - timedelta(seconds=30)),
        # a retry waiting out its backoff isn't due yet
        _job(other_user, status="queued", next_attempt_at=_now() + timedelta(minutes=30)),
    ])
    db_session.commit()

    assert _evaluate(db_session).alerts == []


def test_m2_alerts_on_a_running_job_without_progress(db_session, user) -> None:
    job = _job(user, status="running")
    db_session.add(job)
    db_session.commit()
    db_session.execute(
        text("UPDATE sync_jobs SET updated_at = now() - make_interval(mins => :m) WHERE id = :id"),
        {"m": settings.sync_stale_job_threshold_minutes + 1, "id": job.id},
    )
    db_session.commit()

    assert _ids(_evaluate(db_session)) == ["M2"]


def test_m2_ignores_a_running_job_making_progress(db_session, user) -> None:
    db_session.add(_job(user, status="running"))
    db_session.commit()

    assert _evaluate(db_session).alerts == []


def test_m3_alerts_once_per_failed_job_with_its_error_codes(db_session, user, other_user) -> None:
    db_session.add_all([
        _job(user, status="failed", error_code="gmail_reauth_required"),
        _job(other_user, status="failed"),
    ])
    db_session.commit()

    first = _evaluate(db_session)
    assert _ids(first) == ["M3"]
    assert "gmail_reauth_required=1" in first.alerts[0]
    assert "generic=1" in first.alerts[0]

    assert _evaluate(db_session).alerts == []  # already reported


def test_m3_ignores_failures_reported_before(db_session, user) -> None:
    db_session.add(_job(user, status="failed", alerted_at=_now() - timedelta(days=1)))
    db_session.commit()

    assert _evaluate(db_session).alerts == []


@pytest.mark.parametrize("days_left,alerts", [(21, True), (5, True), (-1, True), (22, False)])
def test_m4_alerts_when_the_dispatch_token_is_about_to_expire(db_session, monkeypatch, days_left, alerts) -> None:
    today = _now().date()
    monkeypatch.setattr(settings, "sync_dispatch_token_expires_on", today + timedelta(days=days_left))

    report = _evaluate(db_session)

    assert (_ids(report) == ["M4"]) is alerts


def test_report_output_never_contains_emails_or_full_ids(db_session, user) -> None:
    job = _job(user, status="failed", error_code="gmail_reauth_required")
    db_session.add(job)
    db_session.commit()

    output = "\n".join(_evaluate(db_session).lines)

    assert user.email not in output
    assert str(user.id) not in output
    assert str(job.id) not in output
    assert str(job.id)[:8] in output


def test_main_exits_nonzero_when_an_alert_fires(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(monitor, "evaluate", lambda db, *, now, today: monitor.MonitorReport(["M1 x"], ["M1 x"]))
    assert monitor.main() == 1


def test_main_exits_zero_when_healthy(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(monitor, "evaluate", lambda db, *, now, today: monitor.MonitorReport([], ["ok"]))
    assert monitor.main() == 0


def test_main_exits_nonzero_when_the_database_is_unreachable(monkeypatch) -> None:
    def unreachable():
        raise RuntimeError("could not connect")

    monkeypatch.setattr(monitor, "SessionLocal", unreachable)
    assert monitor.main() == 1


def test_main_does_not_log_the_error_text_of_a_failed_check(monkeypatch, caplog) -> None:
    import logging

    def unreachable():
        raise RuntimeError('connection to server at "ep-secret-host.neon.tech" failed')

    monkeypatch.setattr(monitor, "SessionLocal", unreachable)
    monkeypatch.setattr(logging.getLogger("app.sync.monitor"), "disabled", False)
    with caplog.at_level("ERROR"):
        monitor.main()
    assert "RuntimeError" in caplog.text
    assert "ep-secret-host" not in caplog.text


def test_main_writes_a_step_summary_in_github_actions(monkeypatch, tmp_path) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(monitor, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(monitor, "evaluate", lambda db, *, now, today: monitor.MonitorReport(["M1 x"], ["M1 x", "ok"]))

    monitor.main()

    assert "M1 x" in summary.read_text()


class _FakeSession:
    def close(self) -> None:
        pass
