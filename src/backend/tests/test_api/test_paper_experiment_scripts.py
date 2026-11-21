"""docs/manuscript/paper_experiment drivers: scoring every case, and the CFD batch through agent.scripted_api.

``score_case.py all`` must finish when a case has no generated project yet.
``batch_cfd_pipeline.py`` turns a Builder's ``dc-params.json`` into automation
steps and builds the project headlessly (``agent.scripted_api.build``);
``run_all_cases.py`` runs every case as ONE ``agent.scripted_api`` batch, each item on
its own private server, in a new round directory -- nothing is deleted, no
server is started by hand and no login is needed.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from agent.scripted_api.identity import REPO

PAPER = REPO / "docs" / "manuscript" / "paper_experiment"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"paper_{name}", PAPER / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# A small layout in the dc-params.json shape the Builder writes (dc-builder Phase 1).
PARAMS = {
    "room": {"width": 5.0, "depth": 4.0, "height": 3.0},
    "rackColumns": [{"name": "A", "count": 2, "startPosition": [2.0, 0.0, 1.2], "spacing": 0.8,
                     "orientation": 90, "rackSize": {"W": 0.6, "D": 1.2, "H": 2.0},
                     "equipments": [{"kind": "fanServer", "name": "srv", "startUnit": 10, "spanUnits": 4,
                                     "power_W": 2000, "flowRate_m3s": 0.15}]}],
    "acus": [{"name": "ACU_1", "position": [0.5, 0.0, 3.0], "size": {"W": 1.0, "D": 0.6, "H": 1.8},
              "orientation": 0,
              "supply": {"face": "front", "temperature_C": 18, "flowRate_m3s": 1.0},
              "return": {"face": "top"}}],
    "baffles": [],
    "cfdSettings": {"solverType": "steady", "meshBaseSize": 0.25, "maxGlobalCells": 400000,
                    "parallelCores": 2, "endTime": 20, "roomTemperature_C": 22},
}


# ── score_case.py ───────────────────────────────────────────────────────────

def test_score_all_skips_a_case_without_a_generated_project(tmp_path, monkeypatch, capsys):
    score = _load("score_case")
    label = PAPER / "case_02_leakage" / "label" / "case_02_leakage.cfdproj"
    scored = tmp_path / "case_02_leakage"
    (scored / "label").mkdir(parents=True)
    (scored / "workdir").mkdir()
    shutil.copy(label, scored / "label" / label.name)
    shutil.copy(label, scored / "workdir" / label.name)  # the label scored against itself
    lonely = tmp_path / "case_99_empty"
    (lonely / "label").mkdir(parents=True)
    shutil.copy(label, lonely / "label" / "case_99_empty.cfdproj")
    monkeypatch.setattr(score, "DEMO", tmp_path)

    assert score.main(["all"]) == 0
    out = capsys.readouterr().out
    assert "case_99_empty" in out and "no cfdproj generated" in out
    assert "case_02_leakage" in out and "100.0%" in out
    assert "skipped 1" in out
    assert not (lonely / "workdir" / "score_result.json").exists()


# ── batch_cfd_pipeline.py ───────────────────────────────────────────────────

def test_params_become_build_steps_on_the_room_shell_and_patches():
    pipeline = _load("batch_cfd_pipeline")
    steps = pipeline.params_to_steps(json.loads(json.dumps(PARAMS)), project_name="mini")
    verbs = [step["verb"] for step in steps]
    assert verbs[:2] == ["setProjectMeta", "applyRoom"]
    assert "createDefaultFloorModel" not in verbs and "createDefaultWallModel" not in verbs
    assert verbs.count("createDefaultRackModel") == 2 and verbs.count("addRackServerAtRange") == 2
    roles = [step["args"][3] for step in steps
             if step["verb"] == "updateAcuPatchPhysics" and step["args"][2] == "role"]
    assert roles == ["supply", "return"]
    for index, step in enumerate(steps):  # every "$N" points at an earlier step
        for arg in json.dumps(step.get("args", [])).split('"'):
            if arg.startswith("$") and arg[1:].isdigit():
                assert int(arg[1:]) < index, (index, step)


def test_rack_columns_step_along_z_unless_they_name_the_x_axis():
    pipeline = _load("batch_cfd_pipeline")
    params = json.loads(json.dumps(PARAMS))
    params["rackColumns"].append({"name": "R", "count": 3, "startPosition": [1.0, 0.0, 3.5], "spacing": 0.6,
                                  "axis": "x", "orientation": 0, "rackSize": {"W": 0.6, "D": 1.0, "H": 2.0},
                                  "equipments": []})
    places = {step["args"][1]["name"]: step["args"][1]["location"]
              for step in pipeline.params_to_steps(params) if step["verb"] == "placeModel"}
    assert [places[f"Rack_A_{i}"] for i in (1, 2)] == [{"x": 2.0, "y": 0.0, "z": 1.2}, {"x": 2.0, "y": 0.0, "z": 2.0}]
    assert [places[f"Rack_R_{i}"]["x"] for i in (1, 2, 3)] == pytest.approx([1.0, 1.6, 2.2])
    assert {places[f"Rack_R_{i}"]["z"] for i in (1, 2, 3)} == {3.5}


def test_params_keep_the_projects_cell_size_rule_unless_they_name_one():
    """A new project starts on the recommended cell-size rule; dc-params only overrides what it states."""
    pipeline = _load("batch_cfd_pipeline")
    params = json.loads(json.dumps(PARAMS))
    for key in ("meshBaseSize", "maxGlobalCells"):
        params["cfdSettings"].pop(key)
    mesh_keys = [step["args"][0] for step in pipeline.params_to_steps(params) if step["verb"] == "setMeshConfig"]
    assert "meshBaseSize" not in mesh_keys and "maxGlobalCells" not in mesh_keys
    legacy = {"room": {"width": 4, "depth": 4, "height": 3}, "solver": {"endTime": 5}, "mesh": {}}
    mesh_keys = [step["args"][0] for step in pipeline.params_to_steps(legacy) if step["verb"] == "setMeshConfig"]
    assert "meshBaseSize" not in mesh_keys and "maxGlobalCells" not in mesh_keys
    stated = [step["args"] for step in pipeline.params_to_steps(json.loads(json.dumps(PARAMS)))
              if step["verb"] == "setMeshConfig" and step["args"][0] == "meshBaseSize"]
    assert stated == [["meshBaseSize", 0.25]]


def test_params_build_a_project_the_air_export_accepts(tmp_path):
    pipeline = _load("batch_cfd_pipeline")
    report = pipeline.build_from_params(json.loads(json.dumps(PARAMS)), tmp_path / "mini" / "mini.cfdproj")
    assert report["status"] == "succeeded", report.get("failure")
    check = report["selfCheck"]
    assert check["ok"], check
    assert {"acu_supply", "acu_return", "fan_inlet", "fan_outlet", "wall"} <= set(check["boundaryRoles"])
    assert report["modelCount"] >= 2 + 1 + 6  # racks, ACU, room shell (4 walls, floor, ceiling)
    assert report["layoutCheck"]["problems"] == [], report["layoutCheck"]


# ── the layout the Builder writes: inside the room, nothing interpenetrating ──

DC_BUILDER = REPO / ".claude" / "commands" / "dc-builder.md"


def _dc_builder_example():
    """The dc-params.json example of dc-builder §1.5 (the first json block after its heading)."""
    text = DC_BUILDER.read_text(encoding="utf-8")
    section = text[text.index("### 1.5"):]
    start = section.index("```json\n") + len("```json\n")
    return json.loads(section[start:section.index("```", start)])


def test_layout_check_flags_a_model_outside_the_room_and_a_baffle_through_a_rack(tmp_path):
    """The layout dc-builder once showed: its ACU stood outside the left wall, its baffle cut a rack."""
    pipeline = _load("batch_cfd_pipeline")
    params = json.loads(json.dumps(_dc_builder_example()))
    params["acus"][0].update(position=[0.2, 0.0, 2.0], orientation=270)  # anchor ON the wall, facing +X
    params["baffles"] = [{"name": "Baffle_S", "position": [3.0, 0.0, 0.8], "size": {"W": 2.4, "H": 2.2},
                          "orientation": 0}]                               # z = 0.8 runs through Rack_A_1
    report = pipeline.build_from_params(params, tmp_path / "bad" / "bad.cfdproj")
    assert report["status"] == "succeeded" and report["selfCheck"]["ok"]  # the export alone does not see it
    problems = report["layoutCheck"]["problems"]
    outside = [p for p in problems if p["kind"] == "outside-room"]
    overlap = [p for p in problems if p["kind"] == "interpenetrate"]
    assert [p["model"] for p in outside] == ["ACU_1"], problems
    assert outside[0]["box"]["x"][0] == pytest.approx(-0.8)
    assert [sorted(p["models"]) for p in overlap] == [["Baffle_S", "Rack_A_1"]], problems


def test_dc_builder_states_the_anchor_rule_the_export_follows(tmp_path):
    """Yaw turns a model about its anchor, a corner of its front face: the footprint table in dc-builder."""
    pipeline = _load("batch_cfd_pipeline")
    W, D, H = 0.6, 1.2, 2.0
    anchors = {0: (1.0, 1.0), 90: (4.0, 3.0), 180: (7.0, 3.0), 270: (8.0, 4.0)}
    columns = [{"name": f"Y{yaw}", "count": 1, "startPosition": [x, 0.0, z], "spacing": 1.0, "orientation": yaw,
                "rackSize": {"W": W, "D": D, "H": H}, "equipments": []} for yaw, (x, z) in anchors.items()]
    params = {"room": {"width": 10.0, "depth": 6.0, "height": 3.0}, "rackColumns": columns, "acus": [],
              "baffles": [], "cfdSettings": {"endTime": 5}}
    out = tmp_path / "yaw" / "yaw.cfdproj"
    assert pipeline.build_from_params(params, out)["status"] == "succeeded"
    boxes = pipeline.model_boxes(out)
    expected = {0: ((0, W), (0, D)), 90: ((0, D), (-W, 0)), 180: ((-W, 0), (-D, 0)), 270: ((-D, 0), (0, W))}
    for yaw, ((x0, x1), (z0, z1)) in expected.items():
        x, z = anchors[yaw]
        box = boxes[f"Rack_Y{yaw}_1"]
        assert box["x"] == pytest.approx([x + x0, x + x1]) and box["z"] == pytest.approx([z + z0, z + z1]), yaw
    table = DC_BUILDER.read_text(encoding="utf-8")
    for row in ("| 0 | −Z | `[x, x+W]` | `[z, z+D]` |", "| 90 | −X | `[x, x+D]` | `[z−W, z]` |",
                "| 180 | +Z | `[x−W, x]` | `[z−D, z]` |", "| 270 | +X | `[x−D, x]` | `[z, z+W]` |"):
        assert row in table, row


def test_the_arrow_tables_match_the_airflow_the_export_writes(tmp_path):
    """dc-builder's and dc-evaluator's arrow -> yaw tables, against a fan server's inlet -> outlet."""
    pipeline = _load("batch_cfd_pipeline")
    server = [{"kind": "fanServer", "name": "srv", "startUnit": 10, "spanUnits": 4, "power_W": 1000,
               "flowRate_m3s": 0.1}]
    anchors = {0: (2.0, 2.5), 90: (1.0, 2.5), 180: (6.0, 2.5), 270: (9.0, 2.5)}
    columns = [{"name": f"Y{yaw}", "count": 1, "startPosition": [x, 0.0, z], "spacing": 1.0, "orientation": yaw,
                "rackSize": {"W": 0.6, "D": 1.2, "H": 2.0}, "equipments": server} for yaw, (x, z) in anchors.items()]
    params = {"room": {"width": 10.0, "depth": 6.0, "height": 3.0}, "rackColumns": columns, "acus": [],
              "baffles": [], "cfdSettings": {"endTime": 5}}
    out = tmp_path / "arrows" / "arrows.cfdproj"
    assert pipeline.build_from_params(params, out)["status"] == "succeeded"
    from agent.scripted_api.air import export_air_case
    boxes = pipeline._solid_boxes(export_air_case(out)["stlFiles"])
    blows = {}
    for yaw in anchors:
        (ilo, ihi), (olo, ohi) = (boxes[f"Rack_Y{yaw}_1_fan_server_1_{end}_patch"] for end in ("inlet", "outlet"))
        dx = (olo[0] + ohi[0] - ilo[0] - ihi[0]) / 2
        dz = (olo[2] + ohi[2] - ilo[2] - ihi[2]) / 2
        blows[yaw] = ("+" if dx > 0 else "-") + "X" if abs(dx) > abs(dz) else ("+" if dz > 0 else "-") + "Z"
    measured = {arrow: yaw for yaw, arrow in blows.items()}
    builder = DC_BUILDER.read_text(encoding="utf-8")
    table = {a: int(y) for a, y in re.findall(r"Arrow points ([+-][XZ]) [^:\n]*: orientation = (\d+) deg", builder)}
    assert table == measured, (table, measured)
    evaluator = (REPO / ".claude" / "commands" / "dc-evaluator.md").read_text(encoding="utf-8")
    mapping = re.search(r"arrow table: ([^)]*)\)", evaluator).group(1)
    assert {a: int(y) for a, y in re.findall(r"([+-][XZ]) → (\d+)", mapping)} == measured


def test_dc_builder_example_layout_is_clean(tmp_path):
    pipeline = _load("batch_cfd_pipeline")
    report = pipeline.build_from_params(_dc_builder_example(), tmp_path / "example" / "example.cfdproj")
    assert report["status"] == "succeeded" and report["selfCheck"]["ok"], report
    assert report["layoutCheck"]["problems"] == [], report["layoutCheck"]


# ── build_from_positions.py: logs in with the credentials the server runs with ──

class _FakeSession:
    def __init__(self, accepted):
        self.accepted, self.tried = accepted, []

    def post(self, url, json=None, timeout=None):
        self.tried.append((json["username"], json["password"]))
        ok = (json["username"], json["password"]) == self.accepted

        class Reply:
            status_code = 200 if ok else 401
            text = '{"ok": true}' if ok else '{"error": "bad credentials"}'
        return Reply()


def test_positions_builder_logs_in_with_the_local_credentials_file(tmp_path, monkeypatch):
    positions = _load("build_from_positions")
    cred = tmp_path / "admin_credentials"
    cred.write_text("admin\nlocal-secret\n")
    monkeypatch.setattr(positions, "LOCAL_CREDENTIALS", cred)
    monkeypatch.delenv("DCDTUNI_ADMIN_USER", raising=False)
    monkeypatch.delenv("DCDTUNI_ADMIN_PASSWORD", raising=False)
    session = _FakeSession(("admin", "local-secret"))
    positions.login(session)
    assert session.tried[-1] == ("admin", "local-secret")

    monkeypatch.setenv("DCDTUNI_ADMIN_USER", "ops")
    monkeypatch.setenv("DCDTUNI_ADMIN_PASSWORD", "env-secret")
    session = _FakeSession(("ops", "env-secret"))
    positions.login(session)
    assert session.tried == [("ops", "env-secret")]  # the launcher's environment pair goes first

    session = _FakeSession(("nobody", "nothing"))
    with pytest.raises(RuntimeError, match="login failed"):
        positions.login(session)
    assert ("admin", "local-secret") in session.tried and len(session.tried) >= 2


# ── run_all_cases.py ────────────────────────────────────────────────────────

def _dataset(tmp_path: Path) -> Path:
    data = tmp_path / "paper"
    label = PAPER / "case_02_leakage" / "label" / "case_02_leakage.cfdproj"
    built = data / "case_01_built" / "workdir"
    built.mkdir(parents=True)
    shutil.copy(label, built / "case_01_built.cfdproj")
    (built / "Mesh").mkdir()
    (built / "Mesh" / "keep.txt").write_text("an earlier round")
    (built / "log").mkdir()
    (built / "log" / "keep.txt").write_text("an earlier round")
    params = data / "case_02_params" / "workdir"
    params.mkdir(parents=True)
    (params / "dc-params.json").write_text(json.dumps(PARAMS))
    (data / "case_03_nothing" / "input").mkdir(parents=True)
    return data


def test_run_all_cases_submits_one_batch_and_deletes_nothing(tmp_path, monkeypatch, capsys):
    runner = _load("run_all_cases")
    data = _dataset(tmp_path)
    before = sorted(str(p.relative_to(data)) for p in data.rglob("*"))
    calls = {}

    def fake_build(params, out, **kwargs):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text("{}")
        return {"status": "succeeded", "modelCount": 9, "selfCheck": {"ok": True}, "out": str(out)}

    def fake_batch(items, *, out_root, max_parallel, max_processes, label=None):
        calls.update(items=items, out_root=out_root, max_parallel=max_parallel, max_processes=max_processes)
        return {"status": "succeeded", "batchDir": str(Path(out_root) / "b"), "resultJson": "x",
                "items": [{"index": i, "label": it.get("label"), "status": "succeeded", "failure": None,
                           "resultJson": f"r{i}"} for i, it in enumerate(items)]}

    monkeypatch.setattr(runner.pipeline, "build_from_params", fake_build)
    monkeypatch.setattr(runner, "run_batch", fake_batch)
    code = runner.main(["--dataset-dir", str(data), "--out-root", str(tmp_path / "batches"),
                        "--mesh-only", "--mesh-processes", "2", "--max-parallel", "2", "--max-processes", "4"])
    assert code == 0
    items = calls["items"]
    assert [Path(item["cfdproj"]).stem for item in items] == ["case_01_built", "case_02_params"]
    for item in items:
        assert item["stages"] == ["mesh"] and item["meshProcesses"] == 2
        assert Path(item["caseDir"]).parent.name == "cfd"            # a fresh round directory ...
        assert Path(item["caseDir"]).parent.parent.name == "workdir"  # ... beside the Builder's files
    assert calls["max_parallel"] == 2 and calls["max_processes"] == 4
    out = capsys.readouterr().out
    assert "case_03_nothing" in out and "skip" in out.lower()
    after = sorted(str(p.relative_to(data)) for p in data.rglob("*"))
    assert set(before) <= set(after), sorted(set(before) - set(after))
    assert (data / "case_01_built" / "workdir" / "Mesh" / "keep.txt").read_text() == "an earlier round"


# ── the short default --end-time is a pipeline smoke run (2026-10-22) ─────────

@pytest.mark.parametrize("name", ["run_all_cases", "batch_cfd_pipeline"])
def test_the_default_end_time_is_documented_as_an_inconclusive_smoke_run(name, capsys):
    from backend.aircooling.runner import convergence
    module = _load(name)
    with pytest.raises(SystemExit):
        module.main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    option = re.search(r"--end-time END_TIME (.*?)(?= --[a-z])", text)
    assert option, text
    help_text = option.group(1)
    assert "default 10" in help_text and "smoke" in help_text and "INCONCLUSIVE" in help_text
    assert f"{convergence.minimum_run_iterations()}" in help_text
