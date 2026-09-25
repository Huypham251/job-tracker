import re
from pathlib import Path

import pytest
import yaml

from app.sync.worker import LANES as WORKER_LANES

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
EXPECTED_TIMEOUT_MINUTES = {"incremental": 30, "initial": 40}
# Phase 10: the drain's per-job slice is set in each lane workflow's env, right
# next to its timeout, so the value governing production is visible there.
SLICE_ENV = {"incremental": "SYNC_INCREMENTAL_SLICE_SECONDS", "initial": "SYNC_INITIAL_SLICE_SECONDS"}
EXPECTED_SLICE_SECONDS = {"incremental": 1200, "initial": 1500}
# A slice can overrun its deadline by up to one page (~100 messages), plus
# checkout/uv setup before the drain starts.
SLICE_MARGIN_SECONDS = 600
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
    assert set(EXPECTED_TIMEOUT_MINUTES) == set(WORKER_LANES)
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
def test_lane_timeout_leaves_margin_above_its_configured_slice(lane) -> None:
    """A job yields at its slice deadline (checked between pages), so the
    job timeout only has to cover one slice plus one page and setup — and
    must stay below GitHub's 360-minute default, or it protects nothing."""
    job = _load(lane)["jobs"]["drain"]
    # A repo variable of the same name may override the slice (production
    # drills) without a commit; the default the file falls back to is what
    # this margin is checked against.
    match = re.fullmatch(r"\$\{\{ vars\.(\w+) \|\| '(\d+)' \}\}", job["env"][SLICE_ENV[lane]])
    assert match is not None and match.group(1) == SLICE_ENV[lane]
    slice_seconds = int(match.group(2))
    assert slice_seconds == EXPECTED_SLICE_SECONDS[lane]
    assert job["timeout-minutes"] == EXPECTED_TIMEOUT_MINUTES[lane]
    assert slice_seconds + SLICE_MARGIN_SECONDS <= job["timeout-minutes"] * 60 < GITHUB_DEFAULT_TIMEOUT_MINUTES * 60


@pytest.mark.parametrize("lane", LANES)
def test_lane_redispatches_itself_only_when_a_job_was_paused(lane) -> None:
    steps = _load(lane)["jobs"]["drain"]["steps"]
    drain = next(step for step in steps if step.get("id") == "drain")
    assert drain["run"] == f"uv run python -m app.sync.drain --lane {lane}"
    redispatch = steps[-1]
    assert redispatch["if"] == "steps.drain.outputs.requeued == 'true'"
    assert redispatch["run"].strip() == f"gh workflow run sync-{lane}.yml --ref main --repo ${{{{ github.repository }}}}"
    assert redispatch["env"] == {"GH_TOKEN": "${{ github.token }}"}


@pytest.mark.parametrize("lane", LANES)
def test_lane_workflow_drains_only_its_own_lane(lane) -> None:
    steps = _load(lane)["jobs"]["drain"]["steps"]
    assert f"uv run python -m app.sync.drain --lane {lane}" in [step.get("run", "") for step in steps]


@pytest.mark.parametrize("lane", LANES)
def test_lane_workflow_token_can_only_read_code_and_start_workflows(lane) -> None:
    # actions: write is needed only for the self re-dispatch step (Phase 10).
    assert _load(lane)["permissions"] == {"contents": "read", "actions": "write"}
