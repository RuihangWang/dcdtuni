"""The energy leg of the steady verdict (owner rule 2026-10-30): the heat the
ACUs remove must close on the declared heat, or the run is not converged."""
import json

from backend.aircooling.runner.convergence import declared_air_heat, energy_balance_verdict
from backend.aircooling.utils.const import air_specific_heat, rho_air

RHO_CP = rho_air * air_specific_heat


def _monitor(run, name, patch, column, values):
    path = run / "postProcessing" / name / "0" / "surfaceFieldValue.dat"
    path.parent.mkdir(parents=True)
    rows = "".join(f"{i + 1}\t{v!r}\n" for i, v in enumerate(values))
    path.write_text(f"# Region type : patch {patch}\n# Faces       : 10\n# Area        : 1.0\n"
                    f"# Scale factor: 1.0\n# Weight field: phi\n# Time\t{column}\n{rows}")


def _run(tmp_path, return_temps, *, power=1000.0, extra_bindings=None, tolerance=None):
    run = tmp_path / "run"
    (run / "system").mkdir(parents=True)
    (run / "system" / "controlDict").write_text("functions { heatT_S {} phi_S {} heatT_R {} phi_R {} }\n")
    n = len(return_temps)
    _monitor(run, "heatT_S", "S", "weightedSum(T)", [-1.0 * 295.0] * n)
    _monitor(run, "phi_S", "S", "sum(phi)", [-1.0] * n)
    _monitor(run, "heatT_R", "R", "weightedSum(T)", list(return_temps))
    _monitor(run, "phi_R", "R", "sum(phi)", [1.0] * n)
    bindings = {"s": {"bcType": "acu_supply", "patchName": "S"},
                "r": {"bcType": "acu_return", "patchName": "R"},
                "f": {"bcType": "fan_outlet", "patchName": "F", "parameters": {"powerW": power}},
                **(extra_bindings or {})}
    config = {} if tolerance is None else {"energyBalanceTolerance": tolerance}
    manifest = run / "live.cfdrun"
    manifest.write_text(json.dumps({"schemaVersion": 4, "simulationConfig": config,
                                    "boundaryBindings": bindings}))
    return energy_balance_verdict(run, live_manifest=manifest, min_window=20)


def test_closed_balance_passes(tmp_path):
    t = 295.0 + 1000.0 / RHO_CP
    out = _run(tmp_path, [t] * 30)
    assert out["passed"] and out["status"] == "CLOSED"
    assert abs(out["acuHeatRemovedW"] - 1000.0) < 1e-6


def test_return_air_barely_warmer_than_supply_is_not_converged(tmp_path):
    # Alibaba-sige at iteration 204: flat returns, the ACUs removing 2 % of the heat
    out = _run(tmp_path, [295.0 + 20.0 / RHO_CP] * 30)
    assert not out["passed"] and out["status"] == "OPEN"
    assert out["reasons"][0].startswith("energy balance open")


def test_one_iteration_outside_the_tolerance_fails_the_window(tmp_path):
    t = 295.0 + 1000.0 / RHO_CP
    temps = [t] * 30
    temps[-5] = 295.0 + 1030.0 / RHO_CP
    assert not _run(tmp_path, temps)["passed"]
    assert _run(tmp_path / "loose", temps, tolerance=0.05)["passed"]


def test_incomplete_declared_heat_falls_back_to_steadiness(tmp_path):
    fixed = {"w": {"bcType": "nonfan_fixed_temp", "patchName": "W"}}
    steady = _run(tmp_path, [295.0 + 700.0 / RHO_CP] * 30, extra_bindings=fixed)
    assert steady["passed"] and steady["status"] == "STEADY" and steady["closureUnavailable"] == "1 x nonfan_fixed_temp"
    assert steady["notCounted"] == ["W (nonfan_fixed_temp)"]
    rising = _run(tmp_path / "rising", [295.0 + (300.0 + 20 * k) / RHO_CP for k in range(30)], extra_bindings=fixed)
    assert not rising["passed"] and rising["status"] == "DRIFTING"


def test_too_short_and_unmonitored_runs(tmp_path):
    assert _run(tmp_path, [296.0] * 5)["status"] == "TOO_SHORT"
    bare = tmp_path / "bare"
    (bare / "system").mkdir(parents=True)
    (bare / "system" / "controlDict").write_text("functions { avgT_R {} }\n")
    assert energy_balance_verdict(bare) is None


def test_declared_heat_counts_fans_and_flux_surfaces():
    manifest = {"geometryExport": {"solids": {"a": {"patchName": "H", "areaM2": 2.0}}},
                "boundaryBindings": {
                    "f": {"bcType": "fan_outlet", "patchName": "F", "parameters": {"powerW": 500}},
                    "h": {"bcType": "nonfan_heat_flux", "patchName": "H", "parameters": {"heatFluxWm2": 100}},
                    "w": {"bcType": "wall", "patchName": "W"}}}
    assert declared_air_heat(manifest) == (700.0, [])
    manifest["boundaryBindings"]["x"] = {"bcType": "nonfan_heat_flux", "patchName": "X",
                                         "parameters": {"heatFluxWm2": 5}}
    assert declared_air_heat(manifest)[1] == ["X (nonfan_heat_flux without an exported area)"]
