"""Field read-back arithmetic on a synthetic two-patch room (no OpenFOAM run)."""
import numpy as np
import pytest

pv = pytest.importorskip("pyvista")

from agent.scripted_api import air_metrics


def _quad(origin, du, dv, T, U, n=2):
    """n x n quads spanning du, dv from origin, point order giving normal du x dv."""
    points, faces = [], []
    for i in range(n + 1):
        for j in range(n + 1):
            points.append(np.asarray(origin) + np.asarray(du) * i / n + np.asarray(dv) * j / n)
    for i in range(n):
        for j in range(n):
            a = i * (n + 1) + j
            faces += [4, a, a + n + 1, a + n + 2, a + 1]
    surface = pv.PolyData(np.asarray(points, dtype=float), np.asarray(faces))
    surface.cell_data["T"] = np.full(surface.n_cells, T, dtype=float)
    surface.cell_data["U"] = np.tile(np.asarray(U, dtype=float), (surface.n_cells, 1))
    return surface


def test_newell_area_vectors_follow_the_point_loop():
    patch = _quad((0, 0, 0), (2, 0, 0), (0, 3, 0), 300., (0, 0, 1))
    vectors = air_metrics._area_vectors(patch)
    np.testing.assert_allclose(vectors.sum(axis=0), [0, 0, 6])
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.5)


def test_acu_heat_rack_inlet_and_balance(monkeypatch):
    from backend.aircooling.utils.const import air_specific_heat, rho_air
    # Supply: 1 m2 face with outward normal -z and inflow U=(0,0,1) -> phi = -1 m3/s at 288.15 K.
    supply = _quad((0, 0, 0), (0, 1, 0), (1, 0, 0), 288.15, (0, 0, 1))
    # Return: 1 m2 with outward normal +z, outflow U=(0,0,1) -> phi = +1 m3/s at 298.15 K.
    ret = _quad((0, 0, 3), (1, 0, 0), (0, 1, 0), 298.15, (0, 0, 1))
    # Server inlet (outflow from the room, 0.5 m3/s at 295.15 K) and outlet (inflow 0.5 m3/s at T_out).
    q_server = 5000.0
    t_out = 295.15 + q_server / (rho_air * air_specific_heat * 0.5)
    inlet = _quad((5, 0, 0), (0, 1, 0), (0, 0, 0.5), 295.15, (1, 0, 0))   # outward +x, U +x
    outlet = _quad((6, 0, 0), (0, 0, 0.5), (0, 1, 0), t_out, (1, 0, 0))   # outward -x, U +x (into room)
    boundary = pv.MultiBlock({"ACU_supply": supply, "ACU_return": ret, "S1_inlet": inlet, "S1_outlet": outlet})
    monkeypatch.setattr("backend.aircooling.runner.postprocess.read_openfoam_case",
                        lambda run_dir: (pv.MultiBlock({"boundary": boundary}), {"sourceFormat": "openfoam",
                                                                                "decomposed": False, "timeValue": 500.}))
    manifest = {"boundaryBindings": {
        "s": {"patchName": "ACU_supply", "bcType": "acu_supply", "objectPath": "constructions.acus.a",
              "parameters": {"temperatureC": 15, "flowRateM3s": 1, "returnPatchName": "ACU_return"}},
        "r": {"patchName": "ACU_return", "bcType": "acu_return", "objectPath": "constructions.acus.a", "parameters": {}},
        "i": {"patchName": "S1_inlet", "bcType": "fan_inlet", "objectPath": "constructions.racks.r1.servers.e1",
              "parameters": {"flowRateM3s": 0.5, "powerW": q_server}},
        "o": {"patchName": "S1_outlet", "bcType": "fan_outlet", "objectPath": "constructions.racks.r1.servers.e1",
              "parameters": {"flowRateM3s": 0.5, "powerW": q_server}},
    }}
    project = {"constructions": {"racks": {"r1": {"meta": {"name": "Rack A"}}}}}
    metrics = air_metrics.compute_air_metrics("unused", manifest, project)
    acu = metrics["acus"][0]
    assert acu["supply"]["outwardFlowM3s"] == pytest.approx(-1.0)
    assert acu["return"]["outwardFlowM3s"] == pytest.approx(1.0)
    assert acu["heatRemovedW"] == pytest.approx(rho_air * air_specific_heat * 10.0)
    rack = metrics["rackInlet"]["racks"][0]
    assert rack["name"] == "Rack A" and rack["areaMeanC"] == pytest.approx(22.0)
    balance = metrics["energyBalance"]
    assert balance["declaredItPowerW"] == pytest.approx(q_server)
    assert balance["serverHeatToAirW"] == pytest.approx(q_server)
    assert metrics["massBalance"]["netOutwardFlowM3s"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["missingPatches"] == []


def _write_native(case, time, patches):
    """A serial case root with a boundary file and ASCII phi/T for *patches* = {name: (phi, T)}."""
    mesh = case / "constant" / "polyMesh"
    mesh.mkdir(parents=True)
    rows = []
    for name, (phi, _) in patches.items():
        rows.append(f"    {name}\n    {{\n        type            patch;\n        nFaces          {len(phi)};\n"
                    f"        startFace       0;\n    }}")
    (mesh / "boundary").write_text(f"FoamFile {{ object boundary; }}\n{len(rows)}\n(\n" + "\n".join(rows) + "\n)\n")
    (case / time).mkdir()
    for index, field in enumerate(("phi", "T")):
        entries = "".join(
            f"    {name}\n    {{\n        type calculated;\n        value nonuniform List<scalar> {len(values[index])}"
            f" ( {' '.join(str(v) for v in values[index])} );\n    }}\n" for name, values in patches.items())
        (case / time / field).write_text(f"FoamFile {{ object {field}; }}\ninternalField uniform 0;\n"
                                         f"boundaryField\n{{\n{entries}}}\n")


def test_native_face_flux_is_used_where_the_face_velocity_is_not_the_flux(monkeypatch, tmp_path):
    from backend.aircooling.utils.const import air_specific_heat, rho_air
    supply = _quad((0, 0, 0), (0, 1, 0), (1, 0, 0), 288.15, (0, 0, 1))
    # A pressure outlet: the written face velocity is the adjacent cell's (U.S = 0.8 m3/s),
    # while the solver's conservative face flux phi carries the full 1 m3/s.
    ret = _quad((0, 0, 3), (1, 0, 0), (0, 1, 0), 298.15, (0, 0, 0.8))
    boundary = pv.MultiBlock({"ACU_supply": supply, "ACU_return": ret})
    monkeypatch.setattr("backend.aircooling.runner.postprocess.read_openfoam_case",
                        lambda run_dir: (pv.MultiBlock({"boundary": boundary}),
                                         {"sourceFormat": "openfoam", "decomposed": False, "timeValue": 500.0}))
    _write_native(tmp_path, "500", {"ACU_supply": ([-0.25] * 4, [288.15] * 4),
                                    "ACU_return": ([0.25] * 4, [298.15] * 4)})
    manifest = {"boundaryBindings": {
        "s": {"patchName": "ACU_supply", "bcType": "acu_supply", "objectPath": "constructions.acus.a",
              "parameters": {"temperatureC": 15, "flowRateM3s": 1, "returnPatchName": "ACU_return"}}}}
    metrics = air_metrics.compute_air_metrics(tmp_path, manifest, {})
    acu = metrics["acus"][0]
    assert metrics["fieldSource"]["fluxSource"].startswith("native phi")
    assert acu["return"]["outwardFlowM3s"] == pytest.approx(1.0)
    assert acu["return"]["velocityAreaFlowM3s"] == pytest.approx(0.8)
    assert acu["heatRemovedW"] == pytest.approx(rho_air * air_specific_heat * 10.0)
    assert metrics["massBalance"]["netOutwardFlowM3s"] == pytest.approx(0.0, abs=1e-12)


def test_overlapping_metric_reads_leave_stdout_where_it_was(monkeypatch):
    """A batch reads two runs' fields at once; the post-processor's chatter is silenced per read.

    ``contextlib.redirect_stdout`` swaps the process-wide ``sys.stdout``: two
    unsynchronised swaps that end in the wrong order leave one read's buffer
    installed for good, and the batch CLI's JSON summary vanished into it
    (2026-10-21, two-item SingleRack batch).
    """
    import sys
    import threading
    import time
    from backend.aircooling.runner import postprocess

    class Stop(Exception):
        pass

    def chatty(run_dir):
        print("[PP-TIMING] chatter")
        time.sleep(0.15)
        raise Stop()

    monkeypatch.setattr(postprocess, "read_openfoam_case", chatty)
    before = sys.stdout
    errors = []

    def read(name):
        try:
            air_metrics.compute_air_metrics(name, {})
        except Stop:
            pass
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
    first = threading.Thread(target=read, args=("a",))
    second = threading.Thread(target=read, args=("b",))
    first.start()
    time.sleep(0.05)
    second.start()
    first.join()
    second.join()
    assert errors == []
    assert sys.stdout is before


def test_metrics_convergence_carries_the_verdict_reason_and_the_minimum():
    # convergence.json (runner) -> metrics.convergence (result.json): the reason a
    # steady run is not CONVERGED and the run length it was judged on
    from agent.scripted_api.air import _convergence_summary
    report = {"verdict": "INCONCLUSIVE", "rule": "... residuals are diagnostics",
              "verdictReason": "too-short: 20 iterations < minimum 1000 (...)",
              "residuals": {"passed": False, "reasons": ["Ux: ..."], "iterations": 20},
              "sensors": {"passed": False, "status": "TOO_SHORT", "reasons": ["return: too-short: ..."],
                          "iterations": 20, "minimumIterations": 1000, "window_samples": 20, "samples": 20}}
    summary = _convergence_summary(report)
    assert summary["verdict"] == "INCONCLUSIVE" and summary["reason"] == report["verdictReason"]
    assert summary["sensors"] == {"passed": False, "status": "TOO_SHORT", "reasons": ["return: too-short: ..."],
                                  "iterations": 20, "minimumIterations": 1000, "samples": 20,
                                  "windowSamples": 20}
    # a report written before 2026-10-22 has no reason field
    assert _convergence_summary({"verdict": "CONVERGED", "sensors": {"passed": True}})["reason"] is None
