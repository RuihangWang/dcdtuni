"""The Shinaide hybrid compiler: contract steps, pending families, and parity with the frozen dictionary compiler."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from agent.scenarios.hybrid import task as T
from agent.scenarios.hybrid.compile import DependencyPending, HybridDesignToProject, compile_design
from agent.tools.edits import ProjectView


@pytest.fixture(scope="module")
def task():
    return T.REGISTERED.load()


def shifted(task):
    action = T.reference_action(task)
    for row, dz in zip(action["layout"]["rows"], (.4, -.3)):
        row["center_m"] += dz
    for i, uid in enumerate(sorted(action["setpoints"])):
        action["setpoints"][uid] = {"supply_C": 18. + i, "flow_fraction": .85 + .02 * i}
    action["liquid"]["pump_N"] = 1.1
    return action


def compiled_steps(action, fault_id="normal"):
    view = ProjectView(document=json.loads(T.REGISTERED.project.read_text()), build_verbs=())
    return HybridDesignToProject(action, fault_id=fault_id).steps(view)


def test_rows_move_rigidly_and_the_pump_takes_its_speed(task):
    action = shifted(task)
    steps = compiled_steps(action)
    reference = json.loads(T.REGISTERED.project.read_text())["constructions"]
    moves = {s["args"][0]: s["args"][1] for s in steps if s["verb"] == "setModelLocation"}
    rows = {row["id"]: row for row in task["geometry"]["reference_rows"]}
    for row in action["layout"]["rows"]:
        dz = row["center_m"] - rows[row["id"]]["center_m"]
        for uid in row["slots"]:
            old = next(m for kind in ("racks", "acus", "cdus", "boxes") for u, m in reference[kind].items() if u == uid)
            assert moves[uid]["z"] == pytest.approx(old["geometry"]["location"]["z"] + dz)
            assert moves[uid]["x"] == pytest.approx(old["geometry"]["location"]["x"])
    assert steps[-1] == {"verb": "setCduPumpSpeed", "args": ["m_30", 1.1]}
    assert {s["args"][0] for s in steps if s["verb"] == "setPipeSegmentPoints"} == {"m_31", "m_32"}


def test_a_fault_closes_the_failed_acu_and_runs_the_others_at_rated_flow(task):
    steps = compiled_steps(shifted(task), "single-off/m_14")
    flow = {(s["args"][0], s["args"][1]): s["args"][3] for s in steps
            if s["verb"] == "updateAcuPatchPhysics" and s["args"][2] == "volumeFlowRate"}
    capacity = {s["args"][0]: s["args"][3] for s in steps
                if s["verb"] == "updateAcuPatchPhysics" and s["args"][2] == "coolingCapacity"}
    assert {v for (uid, _), v in flow.items() if uid == "m_14"} == {0.}
    assert {v for (uid, _), v in flow.items() if uid != "m_14"} == {T.RATED_FLOW_M3S}
    assert capacity["m_14"] == 0. and set(v for u, v in capacity.items() if u != "m_14") == {50.}


def test_designs_without_a_compiler_are_pending_not_dropped(task):
    permuted = T.reference_action(task)
    slots = permuted["layout"]["rows"][0]["slots"]
    slots[0], slots[1] = slots[1], slots[0]
    with pytest.raises(DependencyPending, match="UID-slot permutation"):
        HybridDesignToProject(permuted)
    report = compile_design(permuted, out="unused.cfdproj")
    assert report["status"] == "dependency_pending" and any("permutation" in d for d in report["dependencies"])
    with pytest.raises(ValueError, match="unregistered"):
        HybridDesignToProject(T.reference_action(task), fault_id="single-off/m_1")


def contained(task, architecture):
    action = shifted(task)
    action["architecture"] = architecture
    action["containment"]["pairs"] = [["A", "B"]]
    if architecture == "SLAB_INROW_HAC":
        action["layout"]["rows"][0]["front"], action["layout"]["rows"][1]["front"] = "+", "-"
    return action


def test_contained_aisles_get_three_leaky_sheets_and_hac_turns_the_rows(task):
    steps = compiled_steps(contained(task, "SLAB_INROW_CAC"))
    sheets = [s["args"][1] for s in steps if s["verb"] == "addModel"]
    assert [b["uid"] for b in sheets] == ["ispo_cac_end_left", "ispo_cac_end_right", "ispo_cac_roof"]
    assert all(b["geometry"]["cutouts"][0]["faceId"] == "front" for b in sheets)
    hac = compiled_steps(contained(task, "SLAB_INROW_HAC"))
    turns = {s["args"][0]: s["args"][1]["y"] for s in hac if s["verb"] == "setRotationDegrees"}
    assert len(turns) == 22 and set(turns.values()) == {0., 180.}
    # The mains follow the turned ports: compile_design routes them in its second build.
    assert not [s for s in hac if s["verb"] == "setPipeSegmentPoints"]
    assert [b["uid"] for b in (s["args"][1] for s in hac if s["verb"] == "addModel")][0] == "ispo_hac_end_left"


def close(a, b, tol=1e-12):
    """Equal documents, numbers to ``tol`` (a 180-degree turn's quaternion w is 6e-17 in the editor, 0 in the legacy)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= tol * max(1., abs(a))
    return a == b


FROZEN = Path(__file__).parent / "fixtures/hybrid_legacy"
BUILDS = json.loads((FROZEN / "compiler_v4_builds.json").read_text())["builds"]


@pytest.mark.parametrize("family", sorted(BUILDS))
def test_the_built_project_equals_the_legacy_compilers_through_the_same_editor(task, tmp_path, family):
    """The legacy wrote the v4 dictionary (frozen in fixtures/hybrid_legacy before agent_legacy was removed);
    the trunk drives the editor. Saved by the same editor, they agree."""
    from agent.tools.project import edit_project
    frozen = BUILDS[family]
    fault = frozen["fault"]
    action = shifted(task) if family == "SLAB_INROW_OPEN" else contained(task, family)
    assert json.loads(json.dumps(action)) == frozen["action"]
    (tmp_path / "legacy-v4.cfdproj").write_bytes(gzip.decompress((FROZEN / frozen["file"]).read_bytes()))
    edit_project(tmp_path / "legacy-v4.cfdproj", [], out=tmp_path / "legacy.cfdproj")
    report = compile_design(action, out=tmp_path / "trunk.cfdproj", fault_id=fault)
    assert report["status"] == "compiled"
    assert report["checks"] == {"fixed_parts_equal_reference": True, "liquidrun_pump_rpm_ratio": [1.1]}
    assert report["static_admission"]["static_passed"] and report["static_admission"]["port_to_liquid_graph_max_error_m"] == 0
    assert report["provenance"]["operation"]["failed_uid"] == (None if fault == "normal" else fault.split("/")[1])
    assert close(json.loads((tmp_path / "trunk.cfdproj").read_text()), json.loads((tmp_path / "legacy.cfdproj").read_text()))
