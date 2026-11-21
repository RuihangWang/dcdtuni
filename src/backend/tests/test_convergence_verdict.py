"""Dual convergence verdict (backend.aircooling.runner.convergence, 2026-09-22).

The rule the user set for accepting a dcCart mesh: a steady solve counts as
converged only when the residuals decrease smoothly AND every return-air
sensor's temperature first changes and then plateaus. Both legs, always.
Synthetic logs here; the threshold calibration against the real 2026-09-21
np8 logs (Tri_angle PASS, DAML weld-on PASS, DAML weld-off FAIL, Base604
NOT_CONVERGED) is recorded in the module docstring.
"""
import json
import math
from pathlib import Path

import pytest

from backend.aircooling.runner import convergence as cv


# ---------------------------------------------------------------- log builders
def _step(t: int, res: dict, cumulative: float = 1e-5, bounded: bool = False) -> str:
    lines = [f"Time = {t}", ""]
    for name, val in res.items():
        lines.append(
            f"smoothSolver:  Solving for {name}, Initial residual = {val:.6g}, Final residual = {val / 10:.6g}, No Iterations 2"
        )
    # p_rgh is solved twice per iteration: the second corrector must be ignored
    lines.append(
        f"GAMGPCG:  Solving for p_rgh, Initial residual = {res.get('p_rgh', 1e-3) / 3:.6g}, Final residual = 1e-7, No Iterations 2"
    )
    lines.append(
        f"time step continuity errors : sum local = 1e-6, global = 1e-8, cumulative = {cumulative:.6g}"
    )
    if bounded:
        lines.append("bounding k, min: 1e-15 max: 2000 average: 0.04")
    lines.append("ExecutionTime = 1 s  ClockTime = 1 s")
    lines.append("")
    return "\n".join(lines)


def _decaying(n: int, start: float, end: float, *, wobble: float = 0.05, cumulative: float = 1e-5, k_ratio: float = 1.0):
    """Residuals falling geometrically from ``start`` to ``end`` with a small wobble."""
    out = []
    for i in range(n):
        f = start * (end / start) ** (i / max(1, n - 1))
        w = 1 + wobble * math.sin(i)
        out.append(
            _step(
                i + 1,
                {"Ux": f * w, "Uy": f * w, "Uz": f * w, "T": f * w, "k": f * w * k_ratio, "epsilon": f * w, "p_rgh": 10 * f * w},
                cumulative=cumulative,
            )
        )
    return "\n".join(out)


def _write_log(tmp_path: Path, text: str, prefixed: bool = True) -> Path:
    if prefixed:
        text = "\n".join(
            (f"2026-09-22 00:00:00 | INFO     | {ln}" if ln else ln) for ln in text.splitlines()
        )
    p = tmp_path / "run.log"
    p.write_text(text + "\nEnd\n")
    return p


def _write_probes(run_dir: Path, series: list) -> Path:
    """``series`` is a list of per-step rows (each a list of probe values)."""
    d = run_dir / "postProcessing" / "probes" / "0"
    d.mkdir(parents=True)
    lines = [f"# Probe {i} (0 0 0)" for i in range(len(series[0]))] + ["#       Time"]
    for t, row in enumerate(series, start=1):
        lines.append(f"{t * 10:>12} " + " ".join(f"{v:.4f}" for v in row))
    (d / "T").write_text("\n".join(lines) + "\n")
    return d / "T"


# ---------------------------------------------------------------- parsing
def test_parser_strips_the_loguru_prefix_and_keeps_the_first_p_rgh_solve(tmp_path):
    log = cv.parse_solver_log(_write_log(tmp_path, _step(1, {"Ux": 1e-3, "p_rgh": 5e-2}) + _step(2, {"Ux": 5e-4, "p_rgh": 2e-2}, bounded=True)))
    assert log.iterations == [1.0, 2.0]
    assert log.residuals["p_rgh"] == [(1.0, 5e-2), (2.0, 2e-2)]  # not the /3 second corrector
    assert log.bounding == {"k": [2.0]}
    assert log.continuity[-1][3] == pytest.approx(1e-5)
    assert log.ended and log.converged_at is None


def test_parser_reads_the_raw_openfoam_log_too(tmp_path):
    log = cv.parse_solver_log(_write_log(tmp_path, _step(1, {"Ux": 1e-3}), prefixed=False))
    assert log.residuals["Ux"] == [(1.0, 1e-3)]


# ---------------------------------------------------------------- residual leg
def test_smooth_decay_passes(tmp_path):
    log = cv.parse_solver_log(_write_log(tmp_path, _decaying(400, 1e-2, 1e-5)))
    v = cv.residual_verdict(log)
    assert v["passed"], v["reasons"]
    assert set(v["fields"]) == {"Ux", "Uy", "Uz", "T", "k", "epsilon", "p_rgh"}
    assert v["window"]["iterations"] == 80


def test_residual_control_reached_passes_outright(tmp_path):
    text = _decaying(60, 1e-2, 1e-3) + "\nSIMPLE solution converged in 60 iterations\n"
    v = cv.residual_verdict(cv.parse_solver_log(_write_log(tmp_path, text)))
    assert v["passed"] and v["converged_at"] == 60


def test_rising_residuals_fail(tmp_path):
    text = _decaying(300, 1e-2, 1e-4) + "\n" + "\n".join(
        _step(301 + i, {"Ux": 1e-4 * 1.01 ** i, "p_rgh": 1e-3}) for i in range(150)
    )
    v = cv.residual_verdict(cv.parse_solver_log(_write_log(tmp_path, text)))
    assert not v["passed"]
    assert any("rising" in r for r in v["reasons"]), v["reasons"]


def test_bounded_turbulence_in_the_window_fails(tmp_path):
    text = _decaying(300, 1e-2, 1e-5) + "\n" + "\n".join(
        _step(301 + i, {"Ux": 1e-5, "k": 1e-5, "p_rgh": 1e-4}, bounded=(i % 5 == 0)) for i in range(100)
    )
    v = cv.residual_verdict(cv.parse_solver_log(_write_log(tmp_path, text)))
    assert not v["passed"]
    # the window is the last 80 iterations (321..400): i = 20, 25, ... 95 -> 16 bounded steps
    assert v["bounded_in_window"]["k"] == 16


def test_collapsed_k_fails_even_when_flat_and_tiny(tmp_path):
    # DAML weld-off signature: k residual 1e-7 while momentum sits at 1e-3
    log = cv.parse_solver_log(_write_log(tmp_path, _decaying(400, 1e-2, 1e-3, k_ratio=1e-4)))
    v = cv.residual_verdict(log)
    assert not v["passed"]
    assert any("collapsed" in r for r in v["reasons"]), v["reasons"]


def test_cumulative_continuity_blow_up_fails(tmp_path):
    log = cv.parse_solver_log(_write_log(tmp_path, _decaying(400, 1e-2, 1e-5, cumulative=7.3)))
    v = cv.residual_verdict(log)
    assert not v["passed"]
    assert any("continuity" in r for r in v["reasons"])


def test_scattered_residuals_fail(tmp_path):
    log = cv.parse_solver_log(_write_log(tmp_path, _decaying(400, 1e-2, 1e-5, wobble=0.95)))
    v = cv.residual_verdict(log)
    assert not v["passed"]
    assert any("scatters" in r for r in v["reasons"])


def test_scatter_is_forgiven_once_the_residual_is_tiny(tmp_path):
    # DAML with the first-layer floor: epsilon at 1e-6 wanders 0.6 decades (p_rgh is 10x f, so end at 1e-7)
    log = cv.parse_solver_log(_write_log(tmp_path, _decaying(400, 1e-2, 1e-7, wobble=0.95)))
    v = cv.residual_verdict(log)
    assert v["passed"], v["reasons"]


def test_empty_log_fails_with_a_reason(tmp_path):
    v = cv.residual_verdict(cv.parse_solver_log(_write_log(tmp_path, "")))
    assert not v["passed"] and v["reasons"] == ["no residual lines in the log"]


# ---------------------------------------------------------------- sensor leg
def test_sensors_change_then_plateau_pass(tmp_path):
    rows = [[293.15 + 5 * (1 - math.exp(-i / 10)), 295.0 + 2 * (1 - math.exp(-i / 8))] for i in range(100)]
    f = _write_probes(tmp_path, rows)
    # 100 rows written every 10th iteration: a 1000-iteration run, long enough to judge
    v = cv.sensor_verdict(f, names=["ret_A", "ret_B"], iterations=1000)
    assert v["passed"] and v["status"] == "OK"
    assert set(v["probes"]) == {"ret_A", "ret_B"}


def test_sensor_that_never_moved_fails(tmp_path):
    rows = [[293.15, 295.0 + 2 * (1 - math.exp(-i / 8))] for i in range(100)]
    v = cv.sensor_verdict(_write_probes(tmp_path, rows), iterations=1000)
    assert not v["passed"]
    assert "probe_0" in v["reasons"][0] and "moved only" in v["reasons"][0]


def test_sensor_still_drifting_fails(tmp_path):
    rows = [[293.15 + 0.1 * i] for i in range(100)]  # 10 K over the run, 1 K over the last window
    v = cv.sensor_verdict(_write_probes(tmp_path, rows), iterations=1000)
    assert not v["passed"]
    assert "drifts" in v["reasons"][0]


def test_no_probe_file_is_no_sensors(tmp_path):
    v = cv.sensor_verdict(None)
    assert v["status"] == "NO_SENSORS" and not v["passed"]


def test_find_probe_file_prefers_the_longest_series(tmp_path):
    rows = [[1.0], [2.0], [3.0]]
    _write_probes(tmp_path, rows)
    d = tmp_path / "postProcessing" / "probes" / "500"
    d.mkdir()
    (d / "T").write_text("# Probe 0\n 500 1.0\n")
    assert cv.find_probe_file(tmp_path) == tmp_path / "postProcessing" / "probes" / "0" / "T"


# ---------------------------------------------------------------- both legs
def test_assess_run_requires_both_legs(tmp_path):
    # 1000 iterations: the shortest run the return-air window may judge
    _write_log(tmp_path, _decaying(1000, 1e-2, 1e-5))
    rows = [[293.15 + 5 * (1 - math.exp(-i / 10))] for i in range(100)]
    _write_probes(tmp_path, rows)
    out = cv.assess_run(tmp_path, sensor_names=["ret_A"])
    assert out["verdict"] == "CONVERGED"
    written = json.loads((tmp_path / "convergence.json").read_text())
    assert written["verdict"] == "CONVERGED" and "ret_A" in written["sensors"]["probes"]


def test_assess_run_without_sensors_is_inconclusive_not_converged(tmp_path):
    _write_log(tmp_path, _decaying(400, 1e-2, 1e-5))
    out = cv.assess_run(tmp_path, write=False)
    assert out["verdict"] == "INCONCLUSIVE"
    assert out["residuals"]["passed"] and out["sensors"]["status"] == "NO_SENSORS"


def test_assess_run_steady_return_air_converges_whatever_the_residuals(tmp_path):
    # owner rule 2026-10-20: steady return-air temperatures decide; the
    # residual leg is reported but does not gate the verdict
    _write_log(tmp_path, _decaying(1000, 1e-2, 1e-5, cumulative=7.3))
    _write_probes(tmp_path, [[293.15 + 5 * (1 - math.exp(-i / 10))] for i in range(100)])
    out = cv.assess_run(tmp_path, write=False)
    assert out["verdict"] == "CONVERGED"
    assert not out["residuals"]["passed"]


def test_return_air_steady_within_half_a_kelvin(tmp_path):
    # last window (200 samples of 1000) wanders 0.4 K: steady; 0.7 K: not
    base = [[293.15 + 5 * (1 - math.exp(-i / 100))] for i in range(800)]
    def wander(a):
        return base + [[base[-1][0] + a * math.sin(i / 3)] for i in range(200)]
    ok = cv.temperature_series_verdict(wander(0.2))
    bad = cv.temperature_series_verdict(wander(0.35))
    assert ok["passed"] and not bad["passed"]


def test_live_manifest_assesses_return_monitors_before_the_receipt(tmp_path):
    # a running solve has no execution receipt yet: without live mode the
    # return-air route is skipped entirely
    import json as _json
    run = tmp_path / "log" / "run"
    (run / "system").mkdir(parents=True)
    (run / "model").mkdir()
    manifest = {
        "schemaVersion": 4,
        "simulationConfig": {"monitoring": {"acuReturnTemperature": [{"patch": "AC_Top"}]}},
        "boundaryBindings": {"b": {"patchName": "AC_Top", "solidName": "AC_Top", "bcType": "acu_return",
                                   "role": "acu_return", "objectPath": "constructions.acus.m_1"}},
    }
    mpath = run / "model" / "x.cfdrun"
    mpath.write_text(_json.dumps(manifest))
    assert cv.configured_return_patch_verdict(run, final_iteration=10.0) is None
    out = cv.configured_return_patch_verdict(run, final_iteration=10.0, expected_iterations=[1.0],
                                             live_manifest=mpath)
    # the route is taken (it fails here only on the missing monitor files)
    assert out is not None and out["status"] == "NO_SENSORS" and out["reasons"]


def test_point_probes_are_diagnostics_beside_the_return_air():
    # owner rule 2026-10-20: steady return air decides. CMI-China: every
    # return within 0.5 K, two cold-aisle points swinging 0.7-0.8 K.
    returns = {"passed": True, "status": "OK", "window_samples": 60, "reasons": [],
               "probes": {"ACU1": {"passed": True}}}
    points = {"passed": False, "status": "OK", "window_samples": 60,
              "reasons": ["cold_east: last window spans 0.712"], "probes": {"cold_east": {"passed": False}}}
    v = cv._temperature_streams_verdict(points, returns)
    assert v["passed"] and v["status"] == "OK" and v["reasons"] == []
    assert v["diagnostics"] == ["point: cold_east: last window spans 0.712"]
    assert v["streams"]["pointProbes"]["diagnostic"] and set(v["probes"]) == {"return:ACU1", "point:cold_east"}
    failing = cv._temperature_streams_verdict({**points, "passed": True, "reasons": []},
                                              {**returns, "passed": False, "reasons": ["ACU1 drifts"]})
    assert not failing["passed"] and failing["reasons"] == ["return: ACU1 drifts"]


def test_a_large_executed_manifest_is_within_its_read_bound(tmp_path):
    # Base604's executed cfdrun is 12 MB; the 8 MiB evidence bound left its
    # final verdict INCONCLUSIVE after the live watcher had stopped it steady
    big = tmp_path / "model.cfdrun"
    big.write_bytes(b" " * (12 * 1024 * 1024))
    assert len(cv._monitor_bytes(big, tmp_path, cv._MANIFEST_READ_BOUND)) == 12 * 1024 * 1024
    with pytest.raises(ValueError, match="read bound"):
        cv._monitor_bytes(big, tmp_path)


def _live_run(tmp_path, rows_by_patch, tail_by_patch=None, n_log=None):
    """A running steady solve: no receipt, two ACU return monitors written row by row."""
    import json as _json
    run = tmp_path / "log" / "run"
    (run / "system").mkdir(parents=True)
    (run / "model").mkdir()
    patches = list(rows_by_patch)
    manifest = {
        "schemaVersion": 4,
        "simulationConfig": {"monitoring": {"acuReturnTemperature": [{"patch": p} for p in patches]}},
        "boundaryBindings": {p: {"patchName": p, "solidName": p, "bcType": "acu_return", "role": "acu_return",
                                 "objectPath": f"constructions.acus.m_{i}"} for i, p in enumerate(patches)},
    }
    mpath = run / "model" / "x.cfdrun"
    mpath.write_text(_json.dumps(manifest))
    blocks = "".join(
        f"    avgT_{p}\n    {{\n        type surfaceFieldValue;\n        enabled true;\n        writeControl timeStep;\n"
        f"        writeInterval 1;\n        regionType patch;\n        name {p};\n        operation areaAverage;\n"
        f"        fields (T);\n    }}\n" for p in patches)
    (run / "system" / "controlDict").write_text(f"application x;\nfunctions\n{{\n{blocks}}}\n")
    for p, rows in rows_by_patch.items():
        d = run / "postProcessing" / f"avgT_{p}" / "0"
        d.mkdir(parents=True)
        body = "".join(f"{i}\t{t:.6e}\n" for i, t in enumerate(rows, start=1))
        (d / "surfaceFieldValue.dat").write_text(
            f"# Region type : patch {p}\n# Faces       : 16\n# Area        : 1.0\n# Scale factor: 1.0\n"
            f"# Time        \tareaAverage(T)\n" + body + (tail_by_patch or {}).get(p, ""))
    n = n_log or max(len(r) for r in rows_by_patch.values())
    return run, mpath, [float(i) for i in range(1, n + 1)]


def test_live_return_monitors_written_unevenly_are_judged_on_their_common_prefix(tmp_path):
    # 2026-10-27, DAML on 8 ranks: the watcher read the monitors while the solver
    # wrote them; one return a row ahead of another ("do not share a complete
    # time axis") or a file growing during the read ("changed while reading")
    # made the poll INCONCLUSIVE, so the solve stopped at iteration 1501 although
    # its returns were flat within 0.3 K from iteration 899.
    rise = [300.0 + 5.0 * min(i, 100) / 100 for i in range(400)]
    run, mpath, axis = _live_run(tmp_path, {"AC1_Top": rise, "AC2_Top": rise[:398]},
                                 tail_by_patch={"AC2_Top": "399\t3.05"})  # a half-written row
    out = cv.configured_return_patch_verdict(run, final_iteration=axis[-1], expected_iterations=axis,
                                             live_manifest=mpath)
    assert out["status"] == "OK" and out["passed"], out["reasons"]
    assert out["samples"] == 398


def test_the_live_reader_keeps_only_complete_lines(tmp_path):
    f = tmp_path / "m.dat"
    f.write_text("# h\n1\t3.0e+02\n2\t3.0")
    assert cv._growing_monitor_bytes(f, tmp_path) == b"# h\n1\t3.0e+02\n"
