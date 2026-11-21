"""Transient steps converge (E8c, user ruling 2026-10-01).

The E7 gate runs used a fixed 2 PIMPLE outer correctors with no residual control
(p_rgh started each step's last corrector near 0.15). OpenFOAM v2412 converges each
time step's outer loop with ``residualControl`` INSIDE ``PIMPLE`` (probed on the
hotRoom tutorial: "PIMPLE: converged in 12 iterations", then the next step; at the
limit "PIMPLE: not converged within N iterations" and it carries on). The per-step
thresholds are their own set, ``transientResidualControl``, separate from the steady
stop thresholds; ``nOuterCorrectors`` is the per-step maximum.
"""
import copy
import json
import re
from pathlib import Path

import pytest

from backend.aircooling.runner import convergence as cv
from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.derived_config import derive_sim_config
from backend.aircooling.runner.openfoam_case import TRANSIENT_RESIDUAL_DEFAULTS, generate_fv_solution
from backend.tests.test_cfdrun_reader import _minimal_manifest
from backend.tests.test_convergence_transient import PREFIX, _banner, _run_dir


def pimple_block(text: str) -> str:
    return re.search(r"^PIMPLE\n\{.*?^\}", text, re.S | re.M).group(0)


def test_the_pimple_block_carries_the_per_step_thresholds(tmp_path):
    generate_fv_solution(tmp_path, solver_type="transient", n_outer_correctors=20,
                         transient_residual_control={"p_rgh": 2e-3, "U": 1e-4, "T": 5e-5, "k_epsilon": 1e-4})
    block = pimple_block((tmp_path / "fvSolution").read_text())
    assert "nOuterCorrectors    20;" in block
    assert re.search(r"p_rgh\s*\{\s*tolerance\s+0\.002;\s*relTol\s+0;\s*\}", block)
    assert re.search(r"T\s*\{\s*tolerance\s+5e-05;\s*relTol\s+0;\s*\}", block)
    assert re.search(r'"\(k\|epsilon\)"\s*\{\s*tolerance\s+0\.0001;\s*relTol\s+0;\s*\}', block)
    assert "SIMPLE" not in (tmp_path / "fvSolution").read_text()


def test_absent_thresholds_take_the_defaults_and_one_corrector_has_none(tmp_path):
    generate_fv_solution(tmp_path / "a", solver_type="transient", n_outer_correctors=20)
    block = pimple_block((tmp_path / "a" / "fvSolution").read_text())
    assert f"tolerance {TRANSIENT_RESIDUAL_DEFAULTS['p_rgh']};" in block.replace("  ", " ")
    generate_fv_solution(tmp_path / "b", solver_type="transient", n_outer_correctors=1)
    assert "residualControl" not in (tmp_path / "b" / "fvSolution").read_text()


def test_the_steady_block_keeps_its_own_thresholds(tmp_path):
    generate_fv_solution(tmp_path, solver_type="steady", residual_control={"p_rgh": 3e-3},
                         transient_residual_control={"p_rgh": 9e-9})
    text = (tmp_path / "fvSolution").read_text()
    assert "0.003" in text and "9e-09" not in text and "PIMPLE" not in text


def test_the_root_config_carries_the_set_and_derive_passes_it_on():
    m = _minimal_manifest(schemaVersion=4)
    m.setdefault("simulationConfig", {}).update(
        roomTemperatureC=22, transientResidualControl={"p_rgh": 1e-3, "U": 1e-4, "T": 1e-4, "k_epsilon": 1e-4})
    assert CfdrunV2.model_validate(m).simulationConfig.transientResidualControl.p_rgh == 1e-3
    bad = copy.deepcopy(m)
    bad["simulationConfig"]["transientResidualControl"]["omega"] = 1
    with pytest.raises(ValueError):
        CfdrunV2.model_validate(bad)
    derived = derive_sim_config(m["simulationConfig"], room_temperature=22, monitoring={})
    assert derived["transientResidualControl"] == m["simulationConfig"]["transientResidualControl"]
    assert "transientResidualControl" not in derive_sim_config({}, room_temperature=22, monitoring={})


def outer_run(outcomes) -> list:
    """A PIMPLE log whose steps converge in N or hit the limit (None)."""
    lines = _banner("buoyantBoussinesqPimpleFoam") + ["PIMPLE: max iterations = 20", ""]
    for i, n in enumerate(outcomes, start=1):
        lines += ["Courant Number mean: 0.03 max: 1.2", f"Time = {0.5 * i:g}", ""]
        for k in range(1, (n or 20) + 1):
            lines += [f"PIMPLE: iteration {k}",
                      "DILUPBiCGStab:  Solving for T, Initial residual = 0.001, Final residual = 1e-9, No Iterations 1"]
        lines.append(f"PIMPLE: converged in {n} iterations" if n else "PIMPLE: not converged within 20 iterations")
        lines.append("ExecutionTime = 1 s  ClockTime = 1 s")
    return lines + ["End", ""]


def test_the_verdict_counts_steps_that_hit_the_outer_limit(tmp_path):
    run = _run_dir(tmp_path, outer_run([12, 11, None, 9]), end_time=2.0)
    out = cv.assess_run(run, log_path=run / "job.log", write=False)
    assert out["verdict"] == "TRANSIENT_COMPLETED"
    loop = out["transient"]["outer_loop"]
    assert loop == {"steps_converged": 3, "steps_hit_limit": 1, "iterations_median": 11.5, "iterations_max": 20}
    assert "1 of 4 steps hit the outer-corrector limit" in cv.summary_line(out)


def test_no_outer_control_reports_no_outer_loop(tmp_path):
    from backend.tests.test_convergence_transient import _pimple_run
    run = _run_dir(tmp_path, _pimple_run([0.5, 1.0]))
    assert cv.assess_run(run, log_path=run / "job.log", write=False)["transient"]["outer_loop"] is None
