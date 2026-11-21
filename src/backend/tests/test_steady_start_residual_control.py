"""A monitored steady start converges as a monitored steady solve does.

A monitored steady solve writes residualControl 0 and stops on the convergence
verdict (return air changed, then plateaued; residuals are diagnostics). Since
every export monitors each ACU return by default (a9cdaa6f7) a transient run's
steady start was written the same way but still accepted only on SIMPLE's own
residualControl, so it could never be accepted: Shinaide ran 3000 SIMPLE
iterations and was refused with "invalid residualControl tolerance p_rgh=0.0"
(2026-10-26). Its return air had held within 0.03 K for 2000 iterations while
p_rgh's residual stayed near 1.5e-2, a stationary state no residual gate passes.
A monitored start is now stopped by the same watcher and accepted on the same
verdict; an unmonitored one keeps the strict residualControl gate
(test_transient_start).
"""
import re

import pytest

from backend.aircooling.runner import convergence
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests.test_transient_start import TRANSIENT_T, _simple_log, _steady_stage_runner


def _runner():
    runner = object.__new__(STLGenRunner)
    runner._data = {"simulationConfig": {
        "solverType": "steady", "endTime": 500,
        "residualControl": {"p_rgh": 1e-3, "U": 2e-3, "T": 3e-3, "k_epsilon": 4e-3},
        "monitoring": {"acuReturnTemperature": [{"patch": "ACU_1_Top_patch"}]},
    }}
    runner._bridge_registry = {}
    runner._extract_sensor_points = lambda: []
    return runner


def _residual_control(run_dir):
    text = (run_dir / "system" / "fvSolution").read_text()
    block = re.search(r"residualControl\s*\{(.*?)\}", text, re.S).group(1)
    return {name.strip('"'): float(value) for name, value in re.findall(r'(\S+)\s+(\S+);', block)}


def test_a_monitored_steady_solve_stops_on_the_verdict(tmp_path):
    runner = _runner()
    runner._generate_solver_dicts(tmp_path)
    assert set(_residual_control(tmp_path).values()) == {0.0}
    assert runner._steady_stop_thresholds["p_rgh"] == 1e-3


def _monitored(tmp_path, monkeypatch, verdict, iterations, *, parallel=False):
    # The residuals never reach residualControl: no "SIMPLE solution converged".
    runner, calls = _steady_stage_runner(
        tmp_path, monkeypatch, _simple_log(pressure=0.015, second=0.002, marker=False), parallel=parallel)

    def dicts(path):
        runner._steady_stop_thresholds = {"p_rgh": 1e-3, "U": 1e-3, "T": 1e-3, "k_epsilon": 1e-3}
    monkeypatch.setattr(runner, "_generate_solver_dicts", dicts)
    monkeypatch.setattr(runner, "_sensor_names", lambda: [], raising=False)
    seen = []

    def assess(run_dir, **kwargs):
        seen.append(kwargs.get("log_path"))
        return {"verdict": verdict, "rule": "return air changed, then plateaued",
                "residuals": {"iterations": iterations, "finalInitialResidual": {"p_rgh": 0.015}}}
    monkeypatch.setattr(convergence, "assess_run", assess)
    return runner, calls, seen


@pytest.mark.parametrize("parallel", [False, True])
def test_a_monitored_start_is_accepted_on_the_steady_verdict(tmp_path, monkeypatch, parallel):
    runner, _, seen = _monitored(tmp_path, monkeypatch, "CONVERGED", 3000, parallel=parallel)
    report = runner._run_steady_start(tmp_path, parallel, 2 if parallel else 1, True)
    assert report["convergence"]["verdict"] == "STEADY_CONVERGED"
    assert report["convergence"]["completed_iteration"] == 3000
    assert seen[-1] == tmp_path / "log.steady_start"


@pytest.mark.parametrize("verdict,iterations,reason", [
    ("NOT_CONVERGED", 3000, "verdict NOT_CONVERGED"),
    ("CONVERGED", 50, ""),           # too short a run to judge
])
def test_a_monitored_start_without_the_verdict_is_refused(tmp_path, monkeypatch, verdict, iterations, reason):
    runner, _, _ = _monitored(tmp_path, monkeypatch, verdict, iterations)
    with pytest.raises(RuntimeError, match="steady start did not converge.*" + reason):
        runner._run_steady_start(tmp_path, False, 1, True)
    assert (tmp_path / "0" / "T").read_text() == TRANSIENT_T  # rolled back
