"""The headless compiler returns the editor's export for a saved project.

Skipped where Node.js or the frontend node_modules (esbuild, jsdom, three) are
not installed; see agent.scripted_api.headless for how both are located.
"""
import hashlib
import json

import pytest

from agent.scripted_api import headless
from agent.scripted_api.identity import REPO

try:
    headless.find_node()
    _NODE = all((headless.node_modules_dir() / name / "package.json").is_file() for name in ("esbuild", "jsdom", "three"))
except FileNotFoundError:
    _NODE = False
pytestmark = pytest.mark.skipif(not _NODE, reason="Node.js or frontend node_modules not installed")

SINGLE_RACK = REPO / "case" / "SingleRack" / "SingleRack.cfdproj"
LIQUID_SINGLE = REPO / "case" / "LiquidSingle" / "LiquidSingle.cfdproj"
STAMP = "2026-10-20T02:00:00.000Z"


def _export(path, run_settings=None):
    from agent.scripted_api.air import export_air_case
    return export_air_case(path, run_settings=run_settings, timestamp=STAMP)


@pytest.mark.skipif(not SINGLE_RACK.is_file(), reason="case/SingleRack not checked out")
def test_air_export_is_schema_valid_hash_bound_and_deterministic():
    from backend.aircooling.runner.cfdrun_schema import CfdrunV2
    first, second = _export(SINGLE_RACK), _export(SINGLE_RACK)
    assert first["manifestErrors"] == []
    assert first["cfdrunJson"] == second["cfdrunJson"] and first["stlFiles"] == second["stlFiles"]
    manifest = json.loads(first["cfdrunJson"])
    CfdrunV2.model_validate(manifest)
    bound = {row["path"]: row["contentHash"] for row in manifest["geometryExport"]["stlFiles"].values()}
    assert bound == {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in first["stlFiles"].items()}
    roles = {row["bcType"] for row in manifest["boundaryBindings"].values()}
    assert {"acu_supply", "acu_return", "fan_inlet", "fan_outlet"} <= roles
    assert manifest["meshConfig"]["meshEngine"] == "dcCart"
    sources = first["implementation"]["sources"]
    assert "src/frontend/react/src/headless/projectCompiler.ts" in sources
    assert "src/frontend/shared/js/cfdrun_export.js" in sources


@pytest.mark.skipif(not SINGLE_RACK.is_file(), reason="case/SingleRack not checked out")
def test_a_timeline_mode_whose_solver_type_mirror_disagrees_is_reconciled(tmp_path):
    """A file whose timeline already says air transient but whose
    simulationConfig.solverType still says steady (hand-edited; the editor
    writes both together) must not export a steady run when transient is
    asked for: the manifest follows solverType."""
    from agent.scripted_api.air import export_air_case
    from agent.scripted_api.build import build_project
    v5 = tmp_path / "v5.cfdproj"   # the editor's own save: a v5 file keeps its timeline as written
    assert build_project([], out=v5, base=SINGLE_RACK)["status"] == "succeeded"
    project = json.loads(v5.read_text())
    assert project["schemaVersion"] == 5
    timeline = project["transient"]
    timeline.update(mode="transient", airMode="transient", liquidMode="steady", durationS=30, gridS=1,
                    start={"kind": "steadyConverged"}, tracks=timeline.get("tracks", []))
    project.setdefault("runDefaults", {}).setdefault("simulationConfig", {})["solverType"] = "steady"
    path = tmp_path / "SingleRack.cfdproj"
    path.write_text(json.dumps(project))
    reply = export_air_case(path, solver_type="transient", timestamp=STAMP)
    manifest = json.loads(reply["cfdrunJson"])
    assert manifest["simulationConfig"]["solverType"] == "transient"
    assert manifest["transient"]["durationS"] == 30
    assert reply["simulationMode"]["solverType"] == "transient"


@pytest.mark.skipif(not SINGLE_RACK.is_file(), reason="case/SingleRack not checked out")
def test_run_settings_reach_the_manifest_through_run_defaults():
    reply = _export(SINGLE_RACK, {"meshConfig": {"meshEngine": "snappy", "meshProcess": 4},
                                  "simulationConfig": {"parallelSolve": True, "solveProcess": 4}})
    manifest = json.loads(reply["cfdrunJson"])
    assert manifest["meshConfig"]["meshEngine"] == "snappy" and manifest["meshConfig"]["meshProcess"] == 4
    assert manifest["simulationConfig"]["parallelSolve"] is True and manifest["simulationConfig"]["solveProcess"] == 4
    from agent.scripted_api.air import process_counts
    assert process_counts(manifest)["solveRanks"] == 4


@pytest.mark.skipif(not SINGLE_RACK.is_file(), reason="case/SingleRack not checked out")
def test_solver_type_goes_through_the_editor_mode_switch():
    from agent.scripted_api.air import export_air_case
    steady = export_air_case(SINGLE_RACK, timestamp=STAMP)
    assert steady["simulationMode"]["airMode"] == "steady"
    assert "transient" not in json.loads(steady["cfdrunJson"])
    for options in ({"solver_type": "transient"},
                    {"run_settings": {"simulationConfig": {"solverType": "transient"}}}):
        reply = export_air_case(SINGLE_RACK, timestamp=STAMP, **options)
        assert reply["manifestErrors"] == []
        mode = reply["simulationMode"]
        assert mode["airMode"] == "transient" and mode["mode"] == "transient" and mode["solverType"] == "transient"
        manifest = json.loads(reply["cfdrunJson"])
        # setSimulationMode mirrors the air mode into the solver and the physical stop time into the run
        assert manifest["simulationConfig"]["solverType"] == "transient"
        assert manifest["transient"]["durationS"] == mode["durationS"]
        assert manifest["simulationConfig"]["endTimeSeconds"] == mode["durationS"]


def test_export_refusal_is_reported_with_the_editor_message(tmp_path):
    path = tmp_path / "empty.cfdproj"
    path.write_text(json.dumps({"schemaVersion": 5, "uid": "p", "name": "empty", "constructions": {}}))
    with pytest.raises(headless.HeadlessCompileError):
        _export(path)


@pytest.mark.skipif(not LIQUID_SINGLE.is_file(), reason="case/LiquidSingle not checked out")
def test_liquid_export_is_a_dae_input_the_runtime_admits():
    torch = pytest.importorskip("torch")
    from agent.scripted_api.liquid import export_liquidrun
    from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
    reply = export_liquidrun(LIQUID_SINGLE)
    assert reply["controllers"] == []
    definition = admit_dae_runtime_input(reply["liquidrun"])
    assert definition.equation_graph.size > 0 and torch.is_tensor(definition.parameters(0))


@pytest.mark.skipif(not LIQUID_SINGLE.is_file(), reason="case/LiquidSingle not checked out")
def test_liquid_case_gradients_including_a_fully_open_valve_match_differences(tmp_path):
    pytest.importorskip("torch")
    from agent.scripted_api.liquid import run_liquid_case
    project = tmp_path / LIQUID_SINGLE.name
    project.write_bytes(LIQUID_SINGLE.read_bytes())
    result = run_liquid_case(project, device="cpu", threads=1, fd_check=3,
                             gradient_parameters=("heat_W", "pump_rpm_ratio", "valve_opening", "primary_inlet_K"))
    assert result["status"] == "succeeded", result["failure"]
    rows = result["metrics"]["finiteDifferenceCheck"]
    assert any(row["parameter"] == "valve_opening" and not row["negligibleAdjoint"] for row in rows)
    for row in rows:
        if row["negligibleAdjoint"]:
            assert abs(row["finiteDifference"]) < 1e-8, row
        else:
            assert row["relativeError"] < 1e-6, row
    assert (tmp_path / "log" / "api").is_dir()


LIQUID_CONTROL = REPO / "case" / "LiquidSingle_Control" / "LiquidSingle_Control.cfdproj"


@pytest.mark.skipif(not LIQUID_CONTROL.is_file(), reason="case/LiquidSingle_Control not checked out")
def test_liquid_mode_and_duration_go_through_the_editor_switches():
    from agent.scripted_api.liquid import export_liquidrun
    steady = export_liquidrun(LIQUID_CONTROL, mode="steady")
    assert steady["liquidrun"]["analysis"]["mode"] == "steady" and steady["liquidrun"]["time"]["N_t"] == 1
    assert steady["simulationMode"]["liquidMode"] == "steady"
    transient = export_liquidrun(LIQUID_CONTROL, mode="transient", duration_s=6)
    run = transient["liquidrun"]
    assert run["analysis"]["mode"] == "transient" and transient["simulationMode"]["liquidMode"] == "transient"
    # the timeline length the editor's strip sets, sampled on the experiment's dt
    assert transient["simulationMode"]["durationS"] == 6
    assert run["time"]["N_t"] == round(6 / run["time"]["dt_s"]) + 1
    with pytest.raises(headless.HeadlessCompileError, match="durationS"):
        export_liquidrun(LIQUID_CONTROL, mode="transient", duration_s=-1)


@pytest.mark.skipif(not LIQUID_SINGLE.is_file(), reason="case/LiquidSingle not checked out")
def test_hybrid_project_binds_the_hash_both_manifests_carry():
    """The server accepts hybridHeat only if project, air manifest and liquid run share one content hash."""
    from backend.aircooling.runner.hybrid_air_heat_handoff import cfdproj_content_hash
    project = json.loads(LIQUID_SINGLE.read_text())
    for options in ({}, {"solverType": "transient"}):
        hybrid = headless.compile_project("hybrid-project", project, str(LIQUID_SINGLE), options=options)
        air = headless.compile_project("air-case", project, str(LIQUID_SINGLE), timestamp=STAMP, options=options)
        liquid = headless.compile_project("liquidrun", project, str(LIQUID_SINGLE), options=options)
        assert cfdproj_content_hash(hybrid["project"]) == hybrid["contentHash"]
        assert json.loads(air["cfdrunJson"])["sourceProject"]["contentHash"] == hybrid["contentHash"]
        assert liquid["liquidrun"]["source_project"]["sha256"] == hybrid["contentHash"]
        assert hybrid["inheritResults"] is (project["runDefaults"]["liquid"].get("inheritResults") is True)
        assert hybrid["budgetVersion"] is None
    plain = headless.compile_project("hybrid-project", project, str(LIQUID_SINGLE))["contentHash"]
    assert plain != hybrid["contentHash"]  # the air-mode edit is part of the document


# ── the editor's export of the same saved project ─────────────────────────────
#
# Each case's model/<x>.cfdrun is what the editor uploaded (the server writes the
# manifest it receives, indented, and fills `artifacts` with its own paths). The
# headless export of the same saved document, at the same timestamp, must be that
# manifest -- monitoring included: the panel's residuals plus the ACU list's
# select-all, whose return-air temperatures the convergence verdict is judged on.
# `run_settings` replays panel edits the editor exported but never saved.

EDITOR_EXPORTS = [
    ("Tri_angle", {"simulationConfig": {"solveProcess": 8}}),
    ("Base_leakage", None),
    ("Slab", None),
    ("Shinaide", None),
    ("CMI-China", None),
    ("DAML-Testbed", {"simulationConfig": {"solveProcess": 8}}),
    ("STDCT", {"simulationConfig": {"solveProcess": 8}}),
]


def _editor_case(name):
    case = REPO / "case" / name
    manifests = sorted((case / "model").glob("*.cfdrun"))
    projects = sorted(case.glob("*.cfdproj"))
    if len(manifests) != 1 or len(projects) != 1:
        pytest.skip(f"case/{name} has no single editor export and saved project")
    return projects[0], json.loads(manifests[0].read_text())


@pytest.mark.parametrize("name, run_settings", EDITOR_EXPORTS)
def test_api_export_is_the_editor_export_of_the_same_saved_project(name, run_settings):
    from agent.scripted_api.air import export_air_case
    project, editor = _editor_case(name)
    reply = export_air_case(project, run_settings=run_settings, timestamp=editor["runId"][len("run_"):])
    api = json.loads(reply["cfdrunJson"])
    # the monitoring config only depends on the bindings: byte-equal whatever else was edited since
    assert json.dumps(api["simulationConfig"]["monitoring"]) == json.dumps(editor["simulationConfig"]["monitoring"])
    assert editor["simulationConfig"]["monitoring"]["acuReturnTemperature"]
    if api["sourceProject"]["contentHash"] != editor["sourceProject"]["contentHash"]:
        pytest.skip(f"case/{name}: the saved project changed after the editor export")
    editor.pop("artifacts", None)
    api.pop("artifacts", None)
    assert json.dumps(api) == json.dumps(editor)


@pytest.mark.skipif(not SINGLE_RACK.is_file(), reason="case/SingleRack not checked out")
def test_the_acu_list_is_the_one_the_server_offers_the_panel(tmp_path, monkeypatch):
    """Parity with GET /api/server-patches on the same manifest and a mesh that has every patch."""
    import importlib.util
    import re
    import sys
    # server.py imports its sibling modules by bare name. Load it under its own
    # module name: a test that imported the src/server *package* first (e.g.
    # test_verified_render_source.py) leaves sys.modules["server"] = the package.
    sys.path.insert(0, str(REPO / "src" / "server"))
    spec = importlib.util.spec_from_file_location("server_py_for_patch_test", REPO / "src" / "server" / "server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    from agent.scripted_api.air import export_air_case
    manifest = json.loads(export_air_case(SINGLE_RACK, timestamp=STAMP)["cfdrunJson"])
    names = sorted({row["patchName"] for row in manifest["boundaryBindings"].values()})
    poly = tmp_path / "Mesh" / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    for part in ("points", "faces", "owner", "neighbour"):
        (poly / part).write_text("mock complete mesh")
    (poly / "boundary").write_text(f"{len(names)}\n(\n" + "".join(
        f"    {name}\n    {{\n        type patch;\n    }}\n" for name in names) + ")\n")
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "SingleRack.cfdrun").write_text(json.dumps(manifest))
    replies = []
    monkeypatch.setattr(server, "CASE_DIR", tmp_path)
    monkeypatch.setattr(server, "_send_json", lambda _, status, body: replies.append((status, body)))
    server.handle_server_patches(object())
    (status, offered), = replies
    assert status == 200 and offered["acuReturnPatches"]
    assert manifest["simulationConfig"]["monitoring"] == {
        "residuals": True, "serverTemperature": [], "acuReturnTemperature": offered["acuReturnPatches"]}
    assert all(re.search(r"_(inlet|outlet)", row["patch"], re.I) for row in offered["patches"])


@pytest.mark.skipif(not SINGLE_RACK.is_file(), reason="case/SingleRack not checked out")
def test_run_settings_monitoring_reaches_the_manifest():
    from agent.scripted_api.air import export_air_case
    returns = [row["patchName"] for row in json.loads(_export(SINGLE_RACK)["cfdrunJson"])["boundaryBindings"].values()
               if row.get("role") == "acu_return"]
    assert returns
    for request, expected in [
        ({"residuals": False, "acuReturnTemperature": []}, {"residuals": False, "acuReturnTemperature": []}),
        ({"acuReturnTemperature": returns[:1], "serverTemperature": ["Rack_1_srv1_outlet_p0"]},
         {"residuals": True, "acuReturnTemperature": returns[:1], "serverTemperature": ["Rack_1_srv1_outlet_p0"]}),
    ]:
        monitoring = json.loads(_export(SINGLE_RACK, {"simulationConfig": {"monitoring": request}})["cfdrunJson"])[
            "simulationConfig"]["monitoring"]
        assert list(monitoring) == ["residuals", "serverTemperature", "acuReturnTemperature"]
        assert monitoring["residuals"] is expected["residuals"]
        assert [row["patch"] for row in monitoring["acuReturnTemperature"]] == expected["acuReturnTemperature"]
        assert [row["patch"] for row in monitoring["serverTemperature"]] == expected.get("serverTemperature", [])
    with pytest.raises(headless.HeadlessCompileError, match="not an ACU return"):
        _export(SINGLE_RACK, {"simulationConfig": {"monitoring": {"acuReturnTemperature": ["wall_patch"]}}})
    # the panel lists only what the mesh has: a return the mesh lacks is not offered
    reply = export_air_case(SINGLE_RACK, timestamp=STAMP, mesh_patches=["wall_patch"])
    assert json.loads(reply["cfdrunJson"])["simulationConfig"]["monitoring"]["acuReturnTemperature"] == []
