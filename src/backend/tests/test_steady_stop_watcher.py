"""A monitored steady solve stops on the convergence verdict, not on
residualControl alone (Slab stopped at iteration 369 while its return-air
temperatures still moved 0.2 K per window). Since the 2026-10-20 owner rule
the verdict is steady return air; residual thresholds no longer gate the
stop, but the run must hold the verdict's window, convergence.MIN_WINDOW_SAMPLES
iterations (a fixed window with no further minimum since 2026-10-27), the
minimum the final verdict applies too (test_convergence_run_length.py)."""
import time
from types import SimpleNamespace

import backend.aircooling.runner.convergence as convergence
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _runner(tmp_path, thresholds):
    runner = object.__new__(STLGenRunner)
    runner._steady_stop_thresholds = thresholds
    runner._sensor_names = lambda: []
    runner._data = {"simulationConfig": {}}
    runner._STEADY_STOP_POLL_SECONDS = 0.02
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "controlDict").write_text("application x;\nstopAt          endTime;\nendTime 5000;\n")
    log = tmp_path / "run.log"
    log.write_text("")
    return runner, log


def _run(runner, tmp_path, log, verdict, final, iterations=1200):
    convergence_assess = convergence.assess_run
    convergence_parse = convergence.parse_solver_log
    convergence.assess_run = lambda *a, **k: {
        "verdict": verdict, "residuals": {"iterations": iterations}, "sensors": {}}
    convergence.parse_solver_log = lambda _p: SimpleNamespace(
        residuals={"Ux": [(400, final)], "p_rgh": [(400, final)], "T": [(400, final)]})
    try:
        with runner._watch_steady_stop(tmp_path, log):
            time.sleep(0.3)
    finally:
        convergence.assess_run = convergence_assess
        convergence.parse_solver_log = convergence_parse
    return (tmp_path / "system" / "controlDict").read_text()


def test_steady_return_air_writes_stop_now_whatever_the_residuals(tmp_path):
    runner, log = _runner(tmp_path, {"p_rgh": 1e-3, "U": 1e-3, "T": 1e-3, "k_epsilon": 1e-3})
    assert "stopAt          writeNow;" in _run(runner, tmp_path, log, "CONVERGED", 1e-4)
    other = tmp_path / "residuals_above_thresholds"
    other.mkdir()
    runner, log = _runner(other, {"p_rgh": 1e-3, "U": 1e-3, "T": 1e-3, "k_epsilon": 1e-3})
    assert "stopAt          writeNow;" in _run(runner, other, log, "CONVERGED", 5e-3)


def test_unsteady_return_air_or_short_window_keeps_running(tmp_path):
    runner, log = _runner(tmp_path, {"p_rgh": 1e-3, "U": 1e-3, "T": 1e-3, "k_epsilon": 1e-3})
    assert "stopAt          endTime;" in _run(runner, tmp_path, log, "NOT_CONVERGED", 1e-4)
    # 50 iterations: shorter than the window, too short to call a plateau
    assert "stopAt          endTime;" in _run(runner, tmp_path, log, "CONVERGED", 1e-4, iterations=50)


def test_no_thresholds_means_no_watcher(tmp_path):
    runner, log = _runner(tmp_path, None)
    assert "stopAt          endTime;" in _run(runner, tmp_path, log, "CONVERGED", 1e-6)
