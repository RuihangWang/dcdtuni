"""Transient verdict and solver-stage parsing (transient design §6 T4 item 7, plan T5c).

A transient (PIMPLE) air run is not held to the steady rule — residuals do not
decay to a plateau and the return-air sensors are meant to keep moving. It is
TRANSIENT_COMPLETED when the solver reached ``endTime`` and ended without a
fatal error or a non-finite residual; everything else is reported as
diagnostics, without invented thresholds. Only the LAST solver stage of a job
log is assessed: a steady-converged start runs SIMPLE then PIMPLE in one job,
and utilities after the solver (reconstructPar, writeExplicitBC) print their
own ``Time =`` and ``End`` lines.
"""
from pathlib import Path

from backend.aircooling.runner import convergence as cv

PREFIX = "2026-09-30 12:00:00 | INFO     | "


def _banner(app: str) -> list:
    return ["/*---------------------------------------------------------------------------*\\",
            "Build  : _v2412", f"Exec   : {app} -parallel", "Date   : Sep 30 2026", ""]


def _pimple_step(t: float, outers: list, courant: float = 0.8) -> list:
    """One PIMPLE time step; ``outers`` = per outer corrector {field: initial residual}."""
    out = [f"Courant Number mean: 0.03 max: {courant}", "deltaT = 0.05", f"Time = {t:g}", ""]
    for k, res in enumerate(outers, start=1):
        out.append(f"PIMPLE: iteration {k}")
        for name, val in res.items():
            out.append(f"DILUPBiCGStab:  Solving for {name}, Initial residual = {val}, Final residual = 1e-9, No Iterations 1")
        out.append("GAMGPCG:  Solving for p_rgh, Initial residual = 0.001, Final residual = 1e-9, No Iterations 3")
        out.append("time step continuity errors : sum local = 1e-9, global = 1e-11, cumulative = 1e-8")
    out.append("ExecutionTime = 1 s  ClockTime = 1 s")
    return out


def _simple_step(i: int) -> list:
    return [f"Time = {i}", "",
            "smoothSolver:  Solving for Ux, Initial residual = 0.01, Final residual = 0.001, No Iterations 2",
            "GAMGPCG:  Solving for p_rgh, Initial residual = 0.02, Final residual = 1e-5, No Iterations 3",
            "ExecutionTime = 1 s  ClockTime = 1 s"]


def _pimple_run(times, *, end=True, courant=0.8) -> list:
    lines = _banner("buoyantBoussinesqPimpleFoam") + ["Starting time loop", ""]
    for t in times:
        lines += _pimple_step(t, [{"T": 0.05, "p_rgh": 0.3}, {"T": 0.002, "p_rgh": 0.04}], courant=courant)
    if end:
        lines += ["End", ""]
    return lines


def _run_dir(tmp_path: Path, lines: list, end_time: float = 1.0, max_co: float = 5.0) -> Path:
    run = tmp_path / "run"
    (run / "system").mkdir(parents=True)
    (run / "system" / "controlDict").write_text(
        f"application     buoyantBoussinesqPimpleFoam;\nendTime         {end_time};\nmaxCo           {max_co};\n")
    (run / "job.log").write_text("\n".join(PREFIX + ln if ln else ln for ln in lines) + "\n")
    return run


def test_a_pimple_run_that_reached_end_time_is_transient_completed(tmp_path):
    run = _run_dir(tmp_path, _pimple_run([0.5, 1.0], courant=4.2))
    out = cv.assess_run(run, log_path=run / "job.log")
    assert out["verdict"] == "TRANSIENT_COMPLETED"
    tr = out["transient"]
    assert tr["application"] == "buoyantBoussinesqPimpleFoam"
    assert tr["steps"] == 2 and tr["final_time"] == 1.0 and tr["end_time"] == 1.0
    assert tr["courant_max"] == 4.2 and tr["max_co"] == 5.0
    # each step's LAST outer corrector, not the first
    assert tr["last_outer_initial_residual"]["T"]["max"] == 0.002
    assert tr["last_outer_initial_residual"]["p_rgh"]["median"] == 0.04
    assert (run / "convergence.json").is_file()
    assert cv.summary_line(out).startswith("convergence: TRANSIENT_COMPLETED")


def test_a_run_that_stopped_short_is_incomplete(tmp_path):
    run = _run_dir(tmp_path, _pimple_run([0.5], end=False))
    out = cv.assess_run(run, log_path=run / "job.log", write=False)
    assert out["verdict"] == "TRANSIENT_INCOMPLETE"
    assert any("0.5 of 1" in r for r in out["transient"]["reasons"])


def test_a_non_finite_residual_or_a_fatal_error_is_incomplete(tmp_path):
    lines = _pimple_run([0.5, 1.0])
    lines.insert(lines.index("PIMPLE: iteration 2") + 1,
                 "DILUPBiCGStab:  Solving for T, Initial residual = nan, Final residual = nan, No Iterations 1000")
    run = _run_dir(tmp_path, lines)
    out = cv.assess_run(run, log_path=run / "job.log", write=False)
    assert out["verdict"] == "TRANSIENT_INCOMPLETE"
    assert any("non-finite" in r for r in out["transient"]["reasons"])
    fatal = _pimple_run([0.5], end=False) + ["--> FOAM FATAL ERROR: (openfoam-2412)", "Maximum number of iterations exceeded"]
    run2 = _run_dir(tmp_path / "b", fatal)
    assert "FOAM FATAL" in " ".join(cv.assess_run(run2, log_path=run2 / "job.log", write=False)["transient"]["reasons"])


def test_only_the_last_solver_stage_of_a_steady_start_chain_is_assessed(tmp_path):
    lines = _banner("buoyantBoussinesqSimpleFoam_new")
    for i in range(1, 6):
        lines += _simple_step(i)
    lines += ["SIMPLE solution converged in 5 iterations", "End", ""]
    lines += _banner("reconstructPar") + ["Time = 5", "Reconstructing FV fields", "End", ""]
    lines += _pimple_run([0.5, 1.0])
    lines += _banner("writeExplicitBC") + ["Time = 1", "End", ""]
    run = _run_dir(tmp_path, lines)
    log = cv.parse_solver_log(run / "job.log")
    assert log.application == "buoyantBoussinesqPimpleFoam"
    assert log.iterations == [0.5, 1.0]            # not SIMPLE's 1..5, not the utilities' Time lines
    assert log.converged_at is None and "Ux" not in log.residuals
    out = cv.assess_run(run, log_path=run / "job.log", write=False)
    assert out["verdict"] == "TRANSIENT_COMPLETED" and out["transient"]["steps"] == 2


def test_a_steady_log_ignores_the_utilities_after_the_solver(tmp_path):
    lines = _banner("buoyantBoussinesqSimpleFoam_new")
    for i in range(1, 4):
        lines += _simple_step(i)
    lines += ["End", ""] + _banner("reconstructPar") + ["Time = 3", "End"]
    run = _run_dir(tmp_path, lines)
    log = cv.parse_solver_log(run / "job.log")
    assert log.iterations == [1.0, 2.0, 3.0] and log.application == "buoyantBoussinesqSimpleFoam_new"
    assert cv.assess_run(run, log_path=run / "job.log", write=False)["verdict"] in ("NOT_CONVERGED", "INCONCLUSIVE")


def test_a_log_without_banners_is_read_whole_as_before(tmp_path):
    lines = []
    for i in range(1, 4):
        lines += _simple_step(i)
    run = _run_dir(tmp_path, lines)
    log = cv.parse_solver_log(run / "job.log")
    assert log.iterations == [1.0, 2.0, 3.0] and log.application is None


def test_the_runner_logs_a_completed_transient_run_as_info_not_a_warning(tmp_path):
    """The runner's own call site (``_write_convergence_verdict``) takes the transient shape."""
    from loguru import logger
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    run = _run_dir(tmp_path, _pimple_run([0.5, 1.0]))
    runner = STLGenRunner.__new__(STLGenRunner)
    runner._data = {}
    records = []
    sink = logger.add(lambda message: records.append(message.record), level="DEBUG")
    try:
        result = runner._write_convergence_verdict(run, run / "job.log")
    finally:
        logger.remove(sink)
    assert result["verdict"] == "TRANSIENT_COMPLETED"
    verdict_lines = [r for r in records if r["message"].startswith("convergence:")]
    assert [r["level"].name for r in verdict_lines] == ["INFO"]
    assert (run / "convergence.json").is_file()
