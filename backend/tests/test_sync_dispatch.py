from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.sync import dispatch
from app.sync.worker import LANE_DRAIN_BUDGET_SECONDS

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
SECRET = "github_pat_test_value_never_logged"


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "sync_dispatch_token", SECRET)
    monkeypatch.setattr(settings, "sync_dispatch_repository", "owner/repo")
    monkeypatch.setattr(settings, "sync_dispatch_ref", "main")


@pytest.fixture
def visible_logs(monkeypatch):
    # conftest.py's Alembic run calls logging.config.fileConfig, which
    # disables loggers that already exist — re-enable this one for caplog.
    import logging

    monkeypatch.setattr(logging.getLogger("app.sync.dispatch"), "disabled", False)


def test_every_lane_dispatches_to_a_workflow_that_exists() -> None:
    for lane in LANE_DRAIN_BUDGET_SECONDS:
        assert (WORKFLOWS_DIR / dispatch.workflow_file(lane)).is_file()


def test_request_worker_is_a_no_op_without_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sync_dispatch_token", None)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not call GitHub"))
    assert dispatch.request_worker("incremental") is False


def test_request_worker_is_a_no_op_without_a_repository(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sync_dispatch_token", SECRET)
    monkeypatch.setattr(settings, "sync_dispatch_repository", None)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not call GitHub"))
    assert dispatch.request_worker("incremental") is False


@pytest.mark.parametrize("status", [200, 204])
def test_request_worker_posts_the_dispatch_and_reports_success(configured, monkeypatch, status) -> None:
    seen = {}

    def fake_post(url, *, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Resp(status)

    monkeypatch.setattr(httpx, "post", fake_post)

    assert dispatch.request_worker("initial") is True
    assert seen["url"] == "https://api.github.com/repos/owner/repo/actions/workflows/sync-initial.yml/dispatches"
    assert seen["json"] == {"ref": "main"}
    assert seen["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert seen["headers"]["Accept"] == "application/vnd.github+json"
    assert seen["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert seen["timeout"] == 5.0


@pytest.mark.parametrize("status", [401, 403, 404, 422, 500])
def test_request_worker_reports_rejection_without_leaking_the_token(
    configured, visible_logs, monkeypatch, caplog, status
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(status))
    with caplog.at_level("WARNING"):
        assert dispatch.request_worker("incremental") is False
    assert f"HTTP {status}" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.ConnectError("c"), RuntimeError(SECRET)])
def test_request_worker_never_raises_on_unexpected_exception(
    configured, visible_logs, monkeypatch, caplog, exc
) -> None:
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(httpx, "post", boom)
    with caplog.at_level("WARNING"):
        assert dispatch.request_worker("incremental") is False
    assert "incremental" in caplog.text
    assert SECRET not in caplog.text
