import subprocess
import sys
from pathlib import Path

import pytest

from app.sync.worker import DrainResult

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_drain_module_can_configure_orm_mappers_standalone() -> None:
    """Same class of bug test_sync_worker_entrypoint.py guards against for
    `python -m app.sync.worker`, for this new entrypoint: a genuinely fresh
    process importing only app.sync.drain must not crash configuring
    SQLAlchemy's mappers (GmailConnection.owner / Application.owner resolve
    "User" as a string relationship, lazily, the first time any mapper
    configures — this only works if app.users.models was already imported).
    app.sync.drain imports app.sync.worker, which already imports
    app.users.models.User for exactly this reason — this test proves that
    protection extends to the new entrypoint too, not just the old one.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.sync.drain; from sqlalchemy.orm import configure_mappers; configure_mappers()",
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_drain_main_passes_the_lane_and_its_budget(monkeypatch) -> None:
    import app.sync.drain as drain_module

    calls = []
    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: calls.append(kw) or DrainResult(2, False))

    assert drain_module.main(["--lane", "incremental"]) == DrainResult(2, False)
    assert calls == [{"max_runtime_seconds": 1200.0, "job_type": "incremental", "sweep": True}]


def test_drain_main_without_a_lane_keeps_the_legacy_behavior(monkeypatch) -> None:
    import app.sync.drain as drain_module

    calls = []
    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: calls.append(kw) or DrainResult(0, False))

    drain_module.main([])
    assert calls == [{}]


def test_drain_main_silences_per_request_http_logging(monkeypatch) -> None:
    import logging

    import app.sync.drain as drain_module

    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: DrainResult(0, False))
    drain_module.main(["--lane", "initial"])
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_drain_main_uses_the_configured_slice_for_the_lane(monkeypatch) -> None:
    import app.sync.drain as drain_module
    from app.core.config import settings

    calls = []
    monkeypatch.setattr(settings, "sync_initial_slice_seconds", 120)
    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: calls.append(kw) or DrainResult(0, False))

    drain_module.main(["--lane", "initial"])
    assert calls[0]["max_runtime_seconds"] == 120.0


@pytest.mark.parametrize("requeued,expected", [(True, "requeued=true\n"), (False, "requeued=false\n")])
def test_drain_main_reports_a_paused_job_to_github_actions(monkeypatch, tmp_path, requeued, expected) -> None:
    import app.sync.drain as drain_module

    output = tmp_path / "github_output"
    output.write_text("earlier=1\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: DrainResult(1, requeued))

    drain_module.main(["--lane", "initial"])

    assert output.read_text() == "earlier=1\n" + expected


def test_drain_main_writes_nothing_outside_github_actions(monkeypatch, tmp_path) -> None:
    import app.sync.drain as drain_module

    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(drain_module, "drain_once", lambda **kw: DrainResult(1, True))

    drain_module.main(["--lane", "initial"])

    assert list(tmp_path.iterdir()) == []
