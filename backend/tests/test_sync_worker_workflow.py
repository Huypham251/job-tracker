from pathlib import Path

import pytest
import yaml

from app.sync.worker import LANE_DRAIN_BUDGET_SECONDS

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
EXPECTED_TIMEOUT_MINUTES = {"incremental": 30, "initial": 120}
# GitHub's own default job timeout — a timeout-minutes at or above this adds
# no protection at all.
GITHUB_DEFAULT_TIMEOUT_MINUTES = 360
LANES = sorted(EXPECTED_TIMEOUT_MINUTES)


def _load(lane: str) -> dict:
    return yaml.safe_load((WORKFLOWS_DIR / f"sync-{lane}.yml").read_text())


def _triggers(workflow: dict) -> dict:
    # PyYAML (YAML 1.1) parses the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def test_every_lane_has_a_workflow_and_the_old_single_worker_is_gone() -> None:
    assert set(EXPECTED_TIMEOUT_MINUTES) == set(LANE_DRAIN_BUDGET_SECONDS)
    for lane in LANES:
        assert (WORKFLOWS_DIR / f"sync-{lane}.yml").is_file()
    assert not (WORKFLOWS_DIR / "sync-worker.yml").exists()


@pytest.mark.parametrize("lane", LANES)
def test_lane_workflow_is_dispatchable_with_a_cron_fallback(lane) -> None:
    triggers = _triggers(_load(lane))
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"][0]["cron"]


@pytest.mark.parametrize("lane", LANES)
def test_lane_workflow_runs_one_drain_at_a_time_and_never_cancels_one(lane) -> None:
    """At most one running (+ one pending) drain per lane — production has
    only ever been verified with one worker per job set. cancel-in-progress
    must stay false: cancelling a drain mid-job leaves that job "running"
    until a later run's reaper requeues it with attempts += 1, and three
    such cancellations permanently fail it."""
    concurrency = _load(lane)["concurrency"]
    assert concurrency["group"] == f"sync-{lane}"
    assert concurrency["cancel-in-progress"] is False


@pytest.mark.parametrize("lane", LANES)
def test_lane_workflow_timeout_sits_above_its_drain_budget(lane) -> None:
    """drain_once's budget is only checked between jobs, so the job timeout
    must sit above it (killing a run mid-job costs that job an attempt) but
    below GitHub's 360-minute default, or it protects against nothing."""
    timeout = _load(lane)["jobs"]["drain"]["timeout-minutes"]
    assert timeout == EXPECTED_TIMEOUT_MINUTES[lane]
    assert LANE_DRAIN_BUDGET_SECONDS[lane] < timeout * 60 < GITHUB_DEFAULT_TIMEOUT_MINUTES * 60


@pytest.mark.parametrize("lane", LANES)
def test_lane_workflow_drains_only_its_own_lane(lane) -> None:
    steps = _load(lane)["jobs"]["drain"]["steps"]
    assert f"uv run python -m app.sync.drain --lane {lane}" in [step.get("run", "") for step in steps]


@pytest.mark.parametrize("lane", LANES)
def test_lane_workflow_token_is_read_only(lane) -> None:
    assert _load(lane)["permissions"] == {"contents": "read"}
