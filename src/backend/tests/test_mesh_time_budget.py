"""The five-minute mesh time is a soft development target (owner decision
2026-10-20): it is measured and logged, never enforced. A mesh that runs past
it finishes; no step is refused, no process is terminated, dcCart gets no
-timeLimit and its dict carries timeLimitSeconds 0.

`sleep` stands in for a step that runs past the target, without a mesh build.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from backend.aircooling.runner.mesh_budget import MeshBudget
from backend.aircooling.runner.stlgen_runner import (
    DEFAULT_MESH_TIME_LIMIT_S,
    STLGenRunner,
)


def _runner(mesh_config: dict | None = None, tmp_path: Path | None = None) -> STLGenRunner:
    runner = STLGenRunner.__new__(STLGenRunner)
    runner._data = {"meshConfig": mesh_config if mesh_config is not None else {}}
    runner.mesh_dir = tmp_path or Path("/tmp")
    return runner


def test_target_is_five_minutes():
    assert DEFAULT_MESH_TIME_LIMIT_S == 300.0


def test_started_clock_sets_no_deadline(tmp_path):
    r = _runner({"meshEngine": "dcCart", "meshTimeLimitSeconds": 42}, tmp_path)
    r._start_mesh_budget()
    assert r._mesh_time_remaining() is None
    assert r._mesh_timing is True
    r._check_mesh_budget("dcCartMesh")  # never raises


def test_overrun_is_logged_once_and_never_raises(caplog):
    started = time.monotonic() - 10.0
    budget = MeshBudget(started=started, deadline=started + 1.0)
    with caplog.at_level(logging.WARNING):
        budget.check("first step")
        budget.check("second step")
        budget.finish()
    overruns = [r for r in caplog.records if "development target" in r.getMessage()]
    assert len(overruns) == 2  # one overrun warning + the final "took" line
    assert "took" in overruns[-1].getMessage()


def test_watchdog_is_never_armed():
    budget = MeshBudget()
    seen = []
    budget.watch(seen.append)
    assert seen == [None]


def test_step_running_past_the_target_completes(tmp_path):
    started = time.monotonic() - 299.5
    r = _runner({}, tmp_path)
    r._mesh_budget = MeshBudget(started=started, deadline=started + 300.0)
    r._start_mesh_budget()
    t0 = time.monotonic()
    r._run_cmd("sleep 1.5 && echo done", tmp_path)  # passes the target mid-step
    assert time.monotonic() - t0 >= 1.4


def test_budget_is_cleared_before_solving(tmp_path):
    r = _runner({}, tmp_path)
    r._start_mesh_budget()
    r._clear_mesh_budget()
    assert r._mesh_time_remaining() is None
    assert r._mesh_timing is False
