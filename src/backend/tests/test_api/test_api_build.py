"""Headless modelling: the automation contract's document verbs, run in JSDOM, write a .cfdproj.

Skipped where Node.js or the frontend node_modules are not installed.
"""
from __future__ import annotations

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


def minimal_room():
    """One rack with one fan server, one ACU with a supply and a return patch, in a 4 m x 4 m x 3 m room.

    The room shell comes from ``applyRoom`` (the 2D editor's Apply): its floor and ceiling are room
    boundaries. Floors from ``createDefaultFloorModel`` are interior baffles and do not mesh at y = 0.
    """
    W, D, H = 4.0, 4.0, 3.0
    steps = [
        {"verb": "setProjectMeta", "args": [{"name": "MiniRoom", "inputUnit": "m", "roomTemperatureC": 22}]},
        {"verb": "applyRoom", "args": [[[0, 0], [W, 0], [W, D], [0, D]], H]},                    # 1
    ]
    rack = len(steps)
    steps += [
        {"verb": "createDefaultRackModel"},
        {"verb": "placeModel", "args": [f"${rack}", {"name": "Rack_1", "location": {"x": 1.7, "y": 0, "z": 1.4},
                                                    "size": {"W": 0.6, "D": 1.2, "H": 2.0}}]},
        {"verb": "addRackServerAtRange", "args": [f"${rack}", 10, 4, "fanServer"]},
    ]
    server = len(steps) - 1
    steps += [
        {"verb": "updateRackServerCfd", "args": [f"${rack}", f"${server}", "power", 3000]},
        {"verb": "updateRackServerCfd", "args": [f"${rack}", f"${server}", "volumeFlowRate", 0.25]},
    ]
    acu = len(steps)
    steps += [
        {"verb": "createDefaultAcuModel"},
        {"verb": "placeModel", "args": [f"${acu}", {"name": "ACU_1", "location": {"x": 0.4, "y": 0, "z": 3.1},
                                                   "size": {"W": 1.2, "D": 0.6, "H": 2.0}}]},
        {"verb": "addPatch", "args": [f"${acu}", "front", "rect"]},
    ]
    supply = len(steps) - 1
    steps += [
        {"verb": "fillFacePatch", "args": [f"${acu}", f"${supply}"]},
        {"verb": "updateAcuPatchPhysics", "args": [f"${acu}", f"${supply}", "role", "supply"]},
        {"verb": "updateAcuPatchPhysics", "args": [f"${acu}", f"${supply}", "temperature", 18]},
        {"verb": "updateAcuPatchPhysics", "args": [f"${acu}", f"${supply}", "volumeFlowRate", 1.0]},
        {"verb": "addPatch", "args": [f"${acu}", "top", "rect"]},
    ]
    ret = len(steps) - 1
    steps += [
        {"verb": "fillFacePatch", "args": [f"${acu}", f"${ret}"]},
        {"verb": "updateAcuPatchPhysics", "args": [f"${acu}", f"${ret}", "role", "return"]},
        {"verb": "setMeshConfig", "args": ["locationInMesh", {"x": 3.2, "y": 1.5, "z": 0.6}]},
        {"verb": "setMeshConfig", "args": ["meshBaseSize", 0.25]},
        {"verb": "setSolverConfig", "args": ["parallelSolve", True]},
        {"verb": "setSolverConfig", "args": ["solveProcess", 2]},
    ]
    return steps


def test_build_writes_the_saved_bytes_and_passes_the_air_self_check(tmp_path):
    from agent.scripted_api.build import build_project
    out = tmp_path / "MiniRoom" / "MiniRoom.cfdproj"
    result = build_project(minimal_room(), out=out)
    assert result["status"] == "succeeded", result.get("failure")
    text = out.read_text(encoding="utf-8")
    assert result["sha256"] == headless_sha(text)
    project = json.loads(text)
    racks, acus = project["constructions"]["racks"], project["constructions"]["acus"]
    assert len(racks) == 1 and len(acus) == 1
    acu = next(iter(acus.values()))
    roles = {patch["physics"]["role"] for face in acu["geometry"]["faces"].values() for patch in face["patches"]}
    assert roles == {"supply", "return"}
    rack = next(iter(racks.values()))
    server, = rack["constructions"]["servers"].values()
    assert server["type"] == "fanServer"
    # "$N" references reached the verbs as the uid step N returned
    steps = result["steps"]
    assert steps[3]["args"][0] == steps[2]["result"] and steps[2]["result"] in racks
    # the shell is the editor's: the ground floor and the ceiling are room boundaries
    shell = steps[1]["result"]
    floor, ceiling = (project["constructions"]["floors"][shell[key]] for key in ("floor", "ceiling"))
    assert floor["cooling"]["roomBoundaryLocation"] == "bottom" and ceiling["cooling"]["roomBoundaryLocation"] == "top"
    assert len(shell["walls"]) == 4 and all(uid in project["constructions"]["walls"] for uid in shell["walls"])
    check = result["selfCheck"]
    assert check["ok"] is True and check["manifestErrors"] == [] and check["preflightStops"] == []
    assert check["stlFileCount"] > 0
    roles = set(check["boundaryRoles"])
    assert {"acu_supply", "acu_return", "fan_inlet", "fan_outlet"} <= roles
    # every bound fan face is a surface the mesher gets: a server added to the default (open-backed)
    # rack used to export no outlet while the manifest bound one, and the mesher dropped the server
    import re
    from agent.scripted_api.air import export_air_case
    exported = export_air_case(out)
    solids = {name for text in exported["stlFiles"].values() for name in re.findall(r"^solid (\S+)", text, re.M)}
    bound = {row["patchName"] for row in json.loads(exported["cfdrunJson"])["boundaryBindings"].values()}
    assert {"Rack_1_fan_server_1_inlet_patch", "Rack_1_fan_server_1_outlet_patch"} <= bound
    assert bound <= solids, sorted(bound - solids)


def headless_sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.skipif(not SINGLE_RACK.is_file(), reason="case/SingleRack not checked out")
def test_build_on_a_base_project_edits_only_what_the_steps_say(tmp_path):
    from agent.scripted_api.build import build_project
    # Opening a saved file in the editor migrates it (v5, floor footprints...), so the baseline is an
    # open-and-save with no steps, not the file on disk.
    opened = tmp_path / "opened" / "SingleRack.cfdproj"
    assert build_project([], base=SINGLE_RACK, out=opened)["status"] == "succeeded"
    out = tmp_path / "SingleRack" / "SingleRack.cfdproj"
    result = build_project([{"verb": "setSolverConfig", "args": ["endTime", 77]}], base=SINGLE_RACK, out=out)
    assert result["status"] == "succeeded", result.get("failure")
    before, after = json.loads(opened.read_text()), json.loads(out.read_text())
    assert before["runDefaults"]["simulationConfig"]["endTime"] != 77
    assert after["runDefaults"]["simulationConfig"]["endTime"] == 77
    after["runDefaults"]["simulationConfig"]["endTime"] = before["runDefaults"]["simulationConfig"]["endTime"]
    assert after == before
    assert result["selfCheck"]["ok"] is True


@pytest.mark.parametrize("steps, message", [
    ([{"verb": "buildGeometryData"}], "not a build verb"),
    ([{"verb": "select", "args": ["m_1"]}], "not a build verb"),
    ([{"verb": "placeModel", "args": ["$4", {}]}], "$4"),
    ([{"verb": "placeModel", "args": ["no_such_uid", {"name": "x"}]}], "refused"),
    ([{"verb": "createDefaultRackModel"}, {"verb": "addRackServerAtRange", "args": ["$0", 60, 4, "fanServer"]}],
     "refused"),
])
def test_build_reports_the_failing_step_and_writes_nothing(tmp_path, steps, message):
    from agent.scripted_api.build import build_project
    out = tmp_path / "x.cfdproj"
    result = build_project(steps, out=out)
    assert result["status"] == "failed"
    failure = result["failure"]
    assert failure["stage"] == "build" and message in failure["reason"]
    assert isinstance(failure["step"], int)
    assert not out.exists()


def test_build_refuses_to_overwrite_unless_asked(tmp_path):
    from agent.scripted_api.build import build_project
    out = tmp_path / "x.cfdproj"
    out.write_text("{}")
    result = build_project([{"verb": "createDefaultRackModel"}], out=out)
    assert result["status"] == "failed" and result["failure"]["stage"] == "input"
    assert out.read_text() == "{}"
    result = build_project([{"verb": "createDefaultRackModel"}], out=out, overwrite=True)
    assert result["status"] == "succeeded", result.get("failure")
    assert len(json.loads(out.read_text())["constructions"]["racks"]) == 1
