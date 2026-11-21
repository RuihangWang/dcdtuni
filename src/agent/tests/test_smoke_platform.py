"""Real platform smoke through agent.tools (opt-in: ``DCDTUNI_AGENT_SMOKE=1``; CPU only, at most 4 processes).

Registered checks (fixed before the runs):

1. SingleRack copy, ``Fidelity.AIR_MESH`` through :func:`agent.tools.fidelity.evaluate`
   with ``mesh_processes=2``: the API run succeeds, checkMesh passes, the mesh
   quality receipt is ``accepted``, and the RunLogger holds one physics call
   whose result path and SHA-256 are the ``result.json`` the API wrote.
2. SingleRack copy, semantic air edits (server power, ACU supply temperature,
   a probe) through ``edit_project`` then ``admit_air``: the exported manifest
   carries the new values (``powerW`` 2500, supply ``temperatureC`` 18) and
   the project is admitted.
3. LiquidSingle_Control copy, ``evaluate_liquid(mode="steady", fd_check=1)``:
   every ``finiteDifferenceCheck`` row's adjoint equals the
   :func:`liquid_gradient` value for that component, and the finite
   difference agrees: relative error <= 1e-3 where the adjoint is significant,
   absolute error <= 1e-6 where it is round-off.
4. LiquidSingle_Control copy, ``SetValveOpening`` through ``edit_project`` and
   then ``export_liquidrun``: the valve edge of the compiled ``.liquidrun``
   has the new opening.
5. LiquidSingle_Control copy, ``export_liquid(mode="steady")`` then the
   in-process ``liquid_steady(fd_check=1)``: it writes no run directory, and
   every objective and every reported gradient component equals the
   ``evaluate_liquid`` run's within 1e-9 relative (+1e-12 absolute); its
   finite-difference rows meet the criteria of check 3.
6. ``RunStore.create_case`` / ``save_candidate`` / ``select_candidate`` on a
   SingleRack copy with the default validator (``validate_project``, the
   headless export): all three succeed, each record carries the validator's
   ``ok`` report, and the final candidate's SHA-256 is the one its manifest
   names.
7. ``agent.scenarios.air.compile_design`` builds the two-row hot-aisle
   in-row design on ``case/agent/air-v1-inrow-s01-v2/project.cfdproj``: the
   build's export self-check is ``ok``; the saved project holds 8 racks, 4
   ACUs and 11 baffles; the t = 0 server power is 8 x the template rack's;
   every ACU has a supply track at 22 °C and 0.7 x 2.8 m³/s; ``admit_air``
   admits it.

Cases are copied under ``$DCDTUNI_AGENT_SMOKE_ROOT`` (default
``<repo>/_tmp/agent_slim/cases``) into a fresh stamped directory; nothing is
written under ``case/``.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("DCDTUNI_AGENT_SMOKE") != "1",
                                reason="real platform smoke: set DCDTUNI_AGENT_SMOKE=1")

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def root():
    base = Path(os.environ.get("DCDTUNI_AGENT_SMOKE_ROOT", REPO / "_tmp" / "agent_slim" / "cases"))
    path = base / datetime.now().strftime("%Y-%m-%d_%H%M%S_smoke")
    path.mkdir(parents=True)
    return path


def copy_case(root: Path, name: str, project: str, target_name: str | None = None) -> Path:
    """Copy ``case/<name>/<project>`` into ``root/<target_name or name>/`` with its casePath pointing there."""
    target = root / (target_name or name)
    target.mkdir()
    doc = json.loads((REPO / "case" / name / project).read_text(encoding="utf-8"))
    try:
        doc.setdefault("meta", {})["casePath"] = target.relative_to(REPO).as_posix()
    except ValueError:
        doc.setdefault("meta", {})["casePath"] = str(target)
    path = target / project
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def test_air_mesh_through_the_fidelity_dispatcher(root):
    from agent.records import CostLedger, RunLogger, RunStore
    from agent.tools.fidelity import Budget, Fidelity, evaluate
    project = copy_case(root, "SingleRack", "SingleRack.cfdproj")
    logger = RunLogger(RunStore(root / "runs", create=True), "smoke-air-mesh", config={"case": str(project)},
                       identity={"model": "not used", "data": str(project), "seed": None, "purpose": "agent smoke"})
    budget, ledger = Budget(max_calls=1), CostLedger()
    record = evaluate(project, Fidelity.AIR_MESH, budget=budget, logger=logger, ledger=ledger,
                      case_dir=project.parent, mesh_processes=2, max_processes=2, label="agent-smoke")
    result = record.result
    assert result.succeeded, result.failure
    assert result.check_mesh_ok is True and result.mesh_receipt_status == "accepted"
    assert result.mesh_cells and result.mesh_cells > 0
    (line,) = (logger.path / "physics_calls.jsonl").read_text().splitlines()
    call = json.loads(line)
    path = REPO / call["result"]["path"]  # repository-relative inside the checkout (absolute otherwise)
    assert path.resolve() == Path(result["artifacts"]["resultJson"]).resolve()
    assert call["result"]["sha256"] == RunStore.sha256(path.read_bytes())
    assert call["evaluator"]["identity"]["repository"]["commit"] == result.identity["repository"]["commit"]
    assert call["costs"]["stage_seconds"]["mesh"] > 0 and call["costs"]["process"]["process_wall_seconds"] > 0
    logger.finish("completed", record=record.as_record())


def test_air_edits_reach_the_exported_manifest(root):
    from agent.tools import AddProbe, SetAcuSupply, SetServerLoad, admit_air, edit_project
    base = copy_case(root, "SingleRack", "SingleRack.cfdproj", "SingleRack_edit")
    out = base.parent / "edited.cfdproj"
    report = edit_project(base, [SetServerLoad("m_7", "e_1", power_w=2500.), SetAcuSupply("m_20", temperature_c=18.),
                                 AddProbe("agent_probe", 1.0, 1.0, 1.0)], out=out)
    assert report["status"] == "succeeded", report.get("failure")
    admitted = admit_air(out, keep_export=True)
    assert admitted["status"] == "succeeded" and admitted["admitted"] is True, admitted["blockers"]
    manifest = json.loads(admitted["export"]["cfdrunJson"])  # the export returns the manifest text
    assert any(row.get("powerW") == 2500 for row in walk(manifest))
    assert any(row.get("temperatureC") == 18 for row in walk(manifest))
    probes = json.loads(out.read_text())["instrumentation"]["probes"]
    assert [p["meta"]["name"] for p in probes.values()] == ["agent_probe"]


def test_liquid_gradient_matches_the_finite_difference_check(root):
    from agent.tools import evaluate_liquid, liquid_gradient
    project = copy_case(root, "LiquidSingle_Control", "LiquidSingle_Control.cfdproj")
    result = evaluate_liquid(project, mode="steady", fd_check=1, threads=2)
    assert result.succeeded, result.failure
    checks = result.finite_difference_checks
    assert checks
    for row in checks:
        assert "error" not in row, row
        gradient = liquid_gradient(result, row["objective"], [row["parameter"]])
        assert gradient[row["parameter"]][row["component"]] == row["adjoint"]
        if row["relativeError"] is not None:
            assert row["relativeError"] <= 1e-3, row
        else:
            assert row["absoluteError"] <= 1e-6, row
        assert math.isfinite(row["finiteDifference"])


def test_valve_opening_edit_reaches_the_liquidrun(root):
    import agent.scripted_api as api
    from agent.tools import SetValveOpening, edit_project
    base = copy_case(root, "LiquidSingle_Control", "LiquidSingle_Control.cfdproj", "LiquidSingle_valve")
    doc = json.loads(base.read_text())
    segment, valve = next((uid, v["id"]) for uid, m in doc["constructions"]["pipeSegments"].items()
                          for v in (m.get("cooling") or {}).get("valves") or [] if v.get("type") == "flow_control")
    before = api.export_liquidrun(base)["liquidrun"]
    out = base.parent / "valve.cfdproj"
    report = edit_project(base, [SetValveOpening(segment, valve, .35)], out=out)
    assert report["status"] == "succeeded", report.get("failure")
    after = api.export_liquidrun(out)["liquidrun"]

    def openings(liquidrun):
        return [row.get("params", {}).get("opening") for row in walk(liquidrun)
                if row.get("type") == "valve" and isinstance(row.get("params"), dict)]
    assert .35 not in openings(before) and .35 in openings(after), (openings(before), openings(after))


def test_in_process_liquid_steady_matches_the_liquid_run(root):
    from agent.tools import evaluate_liquid, export_liquid, liquid_steady
    project = copy_case(root, "LiquidSingle_Control", "LiquidSingle_Control.cfdproj", "LiquidSingle_inprocess")
    run = evaluate_liquid(project, mode="steady", threads=2)
    assert run.succeeded, run.failure
    before = sorted(p.name for p in project.parent.rglob("*"))
    liquidrun = export_liquid(project, mode="steady")["liquidrun"]
    steady = liquid_steady(liquidrun, fd_check=1, threads=2)
    assert steady.succeeded, steady.failure
    assert sorted(p.name for p in project.parent.rglob("*")) == before and steady.result_json is None

    def close(a, b):
        return abs(a - b) <= 1e-9 * max(abs(a), abs(b)) + 1e-12
    assert set(steady.objectives) == set(run.objectives)
    for name, value in run.objectives.items():
        assert close(steady.objectives[name], value), (name, steady.objectives[name], value)
    for objective, table in run.gradients.items():
        for parameter, values in table.items():
            for component, value in values.items():
                assert close(steady.gradients[objective][parameter][component], value), (objective, parameter, component)
    for row in steady.finite_difference_checks:
        assert "error" not in row, row
        if row["relativeError"] is not None:
            assert row["relativeError"] <= 1e-3, row
        else:
            assert row["absoluteError"] <= 1e-6, row


def test_run_store_records_with_the_platform_validator(root):
    from agent.records import RunStore, new_run_id
    project = copy_case(root, "SingleRack", "SingleRack.cfdproj", "SingleRack_store")
    store = RunStore.create_case("store-smoke", project, base=root / "stores")
    identity = json.loads((store.root / "reference/source_identity.json").read_text())
    assert identity["validator"]["ok"] is True and identity["validator"]["via"] == "agent.scripted_api.export_air_case"
    run_id = new_run_id("smoke")
    saved = store.save_candidate(run_id, "agent-0", json.loads(project.read_text()), structured_output={"n": 0},
                                 raw_output="{}", identity={"agent": "smoke"})
    assert json.loads((saved / "manifest.json").read_text())["project"]["validator"]["ok"] is True
    final = store.select_candidate(run_id, "agent-0", reason="only candidate")
    manifest = json.loads((store.root / "final_candidate.manifest.json").read_text())
    assert manifest["sha256"] == RunStore.sha256(final.read_bytes()) and manifest["validator"]["ok"] is True


def test_air_design_compiles_through_the_contract(root):
    from agent.scenarios.air import compile_design
    from agent.scenarios.air.spec import Design, Row, Setpoint, Task
    from agent.tools import admit_air
    template = REPO / "case/agent/air-v1-inrow-s01-v2/project.cfdproj"
    slots = ("RACK", "RACK", "IRC", "RACK", "RACK", "IRC")
    design = Design("SLAB_INROW_HAC", (Row("R1", "x", 3.0, 2.0, "-", slots), Row("R2", "x", 5.4, 2.0, "+", slots)),
                    containment_pairs=(("R1", "R2"),),
                    setpoints=tuple(Setpoint(u, 0.7, 22.0) for u in ("R1:2", "R1:5", "R2:2", "R2:5")))
    task = Task(room_x_m=12.0, room_y_m=9.0, room_h_m=3.6, n_racks=8, n_inrow_units=4)
    out = root / "air_compile" / "hac_demo.cfdproj"
    out.parent.mkdir()
    report = compile_design(design, task, template, out=out, name="hac_demo")
    assert report["status"] == "succeeded", report.get("failure")
    assert report["selfCheck"]["ok"] is True, report["selfCheck"]
    assert report["provenance"]["templateIdentity"]["sha256"]
    built = json.loads(out.read_text())
    counts = {bucket: len(built["constructions"][bucket]) for bucket in ("racks", "acus", "baffles")}
    assert counts == {"racks": 8, "acus": 4, "baffles": 11}
    rack = next(r for r in json.loads(template.read_text())["constructions"]["racks"].values()
                if r["constructions"].get("servers"))
    per_rack = sum(s["cooling"]["power"] for s in rack["constructions"]["servers"].values())
    tracks = built["transient"]["tracks"]
    power = sum(t["keyframes"][0]["value"] for t in tracks if t["param"] == "server.power")
    assert power == pytest.approx(8 * per_rack)
    for uid in built["constructions"]["acus"]:
        mine = {t["param"]: t["keyframes"][0]["value"] for t in tracks if t["modelUid"] == uid}
        assert mine == pytest.approx({"acu.supplyTemp": 22.0, "acu.airflow": 0.7 * 2.8})
    admitted = admit_air(out)
    assert admitted["status"] == "succeeded" and admitted["admitted"] is True, admitted["blockers"]
