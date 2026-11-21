"""CPU-only render-overlay contract and real OpenUSD authoring tests."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kit/extensions/dc.cfd_bridge"))
from dc.cfd_bridge import render_scene as rs  # noqa: E402


IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def scene_packet():
    return {
        "schema": "dcdtuni.render-scene/v1",
        "project": {"uid": "project-1", "name": "overlay-test"},
        "coordinateSystem": {"upAxis": "Y", "metersPerUnit": 1, "handedness": "right"},
        "primitives": [], "textures": {}, "warnings": [],
        "view": {"selectedUids": ["rack-1"], "timeSeconds": 1,
                 "camera": {"position": [2, 3, 4], "target": [0, 0, 0],
                            "up": [0, 1, 0], "fov": 50, "aspect": 1.5,
                            "near": .1, "far": 100}},
    }


def overlay_packet():
    material = {"color": [.2, .4, .6], "opacity": .85,
                "doubleSided": True, "unlit": False}
    return {
        "schema": "dcdtuni.render-overlay/v1",
        "projectUid": "project-1",
        "source": {"kind": "meshPreview", "meshSnapshotKey": "stat-key-1"},
        "primitives": [
            {"id": "patch/floor/surface", "patchName": "floor", "kind": "triangles",
             "positions": [0, 0, 0, 1, 0, 0, 0, 0, 1], "indices": [0, 1, 2],
             "matrix": IDENTITY[:], "visible": True, "material": material},
            {"id": "patch/floor/edges", "patchName": "floor", "kind": "lines",
             "positions": [0, 0, 0, 1, 0, 0, 0, 0, 1], "indices": [0, 1, 1, 2],
             "matrix": IDENTITY[:], "visible": False,
             "material": {"color": [.05, .1, .2], "opacity": .35,
                          "doubleSided": False, "unlit": True},
             "width": .002},
        ],
    }


def stage_with_scene(tmp_path):
    from pxr import Usd
    stage = Usd.Stage.CreateInMemory()
    rs.author_scene(stage, scene_packet(), tmp_path)
    return stage


def path_for(pid):
    return "/World/RenderOverlay/Geometry/p_" + hashlib.sha256(pid.encode()).hexdigest()[:24]


def test_overlay_schema_is_strict_and_accepts_valid_packet():
    packet = overlay_packet()
    rs.validate_overlay(packet)
    schema_path = Path(rs.__file__).resolve().parents[7] / "shared/schema/render-overlay.schema.json"
    schema = json.loads(schema_path.read_text())
    assert schema["properties"]["schema"]["const"] == "dcdtuni.render-overlay/v1"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["primitives"]["items"]["additionalProperties"] is False


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(cfdrun={}),
    lambda p: p.update(projectUid=" "),
    lambda p: p["source"].update(kind="liquidResult"),
    lambda p: p["source"].update(meshSnapshotKey=" "),
    lambda p: p["source"].update(contentHash="not-the-stat-key"),
    lambda p: p["primitives"].append(copy.deepcopy(p["primitives"][0])),
    lambda p: p["primitives"][0].update(id=""),
    lambda p: p["primitives"][0].update(patchName=" "),
    lambda p: p["primitives"][0]["positions"].__setitem__(0, float("nan")),
    lambda p: p["primitives"][0]["indices"].__setitem__(0, -1),
    lambda p: p["primitives"][0]["indices"].__setitem__(0, True),
    lambda p: p["primitives"][0]["indices"].__setitem__(0, 99),
    lambda p: p["primitives"][0].update(indices=[0, 1]),
    lambda p: p["primitives"][0].update(positions=[]),
    lambda p: p["primitives"][0].update(matrix=IDENTITY[:-1]),
    lambda p: p["primitives"][0]["matrix"].__setitem__(0, float("inf")),
    lambda p: p["primitives"][0]["material"].update(color=[1, 0]),
    lambda p: p["primitives"][0]["material"].update(opacity=1.1),
    lambda p: p["primitives"][0]["material"].update(metalness=1),
    lambda p: p["primitives"][0].update(width=.005),
    lambda p: p["primitives"][1].update(width=0),
    lambda p: p["primitives"][1].update(indices=[0, 1, 2]),
])
def test_overlay_rejects_malformed_or_ambiguous_data(mutate):
    packet = overlay_packet()
    mutate(packet)
    with pytest.raises(ValueError):
        rs.validate_overlay(packet)


def test_overlay_usd_preserves_patch_source_and_scene_view(tmp_path):
    from pxr import Gf, UsdGeom, UsdShade
    stage = stage_with_scene(tmp_path)
    scene = stage.GetPrimAtPath("/World/RenderScene")
    camera = UsdGeom.Camera(stage.GetPrimAtPath("/World/RenderCamera"))
    camera_before = camera.GetLocalTransformation()
    receipt = rs.author_overlay(stage, overlay_packet())

    assert receipt["primitives"] == 2
    root = stage.GetPrimAtPath("/World/RenderOverlay")
    assert root.GetCustomDataByKey("dcdtuni:projectUid") == "project-1"
    assert root.GetCustomDataByKey("dcdtuni:sourceKind") == "meshPreview"
    assert root.GetCustomDataByKey("dcdtuni:meshSnapshotKey") == "stat-key-1"
    mesh_prim = stage.GetPrimAtPath(path_for("patch/floor/surface"))
    mesh = UsdGeom.Mesh(mesh_prim)
    assert mesh
    assert list(mesh.GetFaceVertexIndicesAttr().Get()) == [0, 1, 2]
    assert list(mesh.GetFaceVertexCountsAttr().Get()) == [3]
    assert mesh_prim.GetCustomDataByKey("dcdtuni:patchName") == "floor"
    assert mesh_prim.GetCustomDataByKey("dcdtuni:sourceKind") == "meshPreview"
    assert mesh_prim.GetCustomDataByKey("dcdtuni:meshSnapshotKey") == "stat-key-1"
    assert mesh_prim.GetCustomDataByKey("dcdtuni:modelUid") is None
    assert tuple(UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(0).Transform(Gf.Vec3d(0, 0, 0))) == (0, 0, 0)
    material = UsdShade.MaterialBindingAPI(mesh_prim).ComputeBoundMaterial()[0]
    surface = UsdShade.Shader(material.GetPrim().GetChild("Surface"))
    assert tuple(surface.GetInput("diffuseColor").Get()) == pytest.approx((.2, .4, .6))
    assert surface.GetInput("opacity").Get() == pytest.approx(.85)

    line_prim = stage.GetPrimAtPath(path_for("patch/floor/edges"))
    curves = UsdGeom.BasisCurves(line_prim)
    assert curves
    assert list(curves.GetCurveVertexCountsAttr().Get()) == [2, 2]
    assert len(curves.GetPointsAttr().Get()) == 4
    assert list(curves.GetWidthsAttr().Get()) == pytest.approx([.002])
    # A single authored width must be declared constant: USD's vertex default
    # makes hydra reject the 1-element array per prim ("widths primvar is not
    # valid") and fall back — noisy and one step from a silent render change.
    assert UsdGeom.Primvar(curves.GetWidthsAttr()).GetInterpolation() == 'constant'
    assert UsdGeom.Imageable(line_prim).GetVisibilityAttr().Get() == "invisible"
    assert line_prim.GetCustomDataByKey("dcdtuni:patchName") == "floor"
    assert line_prim.GetCustomDataByKey("dcdtuni:modelUid") is None
    assert stage.GetPrimAtPath("/World/RenderScene") == scene
    assert camera.GetLocalTransformation() == camera_before
    assert scene.GetCustomDataByKey("dcdtuni:timeSeconds") == 1.0


def test_overlay_replacement_clear_and_failure_preserve_existing_scene(tmp_path):
    from pxr import UsdGeom
    stage = stage_with_scene(tmp_path)
    first = overlay_packet()
    rs.author_overlay(stage, first)
    old_overlay = stage.GetRootLayer().ExportToString()
    bad = overlay_packet()
    bad["projectUid"] = "another-project"
    with pytest.raises(ValueError, match="project"):
        rs.author_overlay(stage, bad)
    assert stage.GetRootLayer().ExportToString() == old_overlay
    bad = overlay_packet()
    bad["primitives"][0]["indices"] = [0, 1, 99]
    with pytest.raises(ValueError):
        rs.author_overlay(stage, bad)
    assert stage.GetRootLayer().ExportToString() == old_overlay

    second = overlay_packet()
    second["source"]["meshSnapshotKey"] = "stat-key-2"
    second["primitives"] = [second["primitives"][1]]
    rs.author_overlay(stage, second)
    assert not stage.GetPrimAtPath(path_for("patch/floor/surface"))
    assert stage.GetPrimAtPath(path_for("patch/floor/edges"))
    assert stage.GetPrimAtPath("/World/RenderOverlay").GetCustomDataByKey("dcdtuni:meshSnapshotKey") == "stat-key-2"

    second["primitives"] = []
    rs.author_overlay(stage, second)
    assert not stage.GetPrimAtPath("/World/RenderOverlay")
    assert stage.GetPrimAtPath("/World/RenderScene")
    assert UsdGeom.Camera(stage.GetPrimAtPath("/World/RenderCamera"))


def test_overlay_replacement_uses_root_layer_with_another_active_edit_target(tmp_path):
    stage = stage_with_scene(tmp_path)
    session = stage.GetSessionLayer()
    stage.SetEditTarget(session)
    first = overlay_packet()
    rs.author_overlay(stage, first)
    second = overlay_packet()
    second["source"]["meshSnapshotKey"] = "stat-key-after"
    second["primitives"] = [second["primitives"][1]]
    rs.author_overlay(stage, second)
    assert stage.GetEditTarget().GetLayer() == session
    root = stage.GetPrimAtPath("/World/RenderOverlay")
    assert root.GetCustomDataByKey("dcdtuni:meshSnapshotKey") == "stat-key-after"
    assert not stage.GetPrimAtPath(path_for("patch/floor/surface"))
    assert stage.GetPrimAtPath(path_for("patch/floor/edges"))


def test_overlay_copy_failure_restores_prior_layer(tmp_path, monkeypatch):
    from pxr import Sdf
    stage = stage_with_scene(tmp_path)
    rs.author_overlay(stage, overlay_packet())
    before = stage.GetRootLayer().ExportToString()
    replacement = overlay_packet()
    replacement["source"]["meshSnapshotKey"] = "new-key"
    real_copy = Sdf.CopySpec
    failed = False

    def fail_new_copy(source_layer, source_path, target_layer, target_path):
        nonlocal failed
        if not failed and target_layer == stage.GetRootLayer() and source_layer != stage.GetRootLayer():
            failed = True
            return False
        return real_copy(source_layer, source_path, target_layer, target_path)

    monkeypatch.setattr(Sdf, "CopySpec", fail_new_copy)
    with pytest.raises(RuntimeError, match="replace"):
        rs.author_overlay(stage, replacement)
    assert failed
    assert stage.GetRootLayer().ExportToString() == before


def test_empty_overlay_accepts_null_stat_key_but_nonempty_needs_current_key(tmp_path):
    stage = stage_with_scene(tmp_path)
    rs.author_overlay(stage, overlay_packet())
    clear = overlay_packet()
    clear["source"]["meshSnapshotKey"] = None
    clear["primitives"] = []
    rs.validate_overlay(clear)
    rs.author_overlay(stage, clear)
    assert not stage.GetPrimAtPath("/World/RenderOverlay")
    clear["primitives"] = [overlay_packet()["primitives"][0]]
    with pytest.raises(ValueError, match="meshSnapshotKey|primitives"):
        rs.validate_overlay(clear)


def test_new_editor_scene_clears_previous_overlay_only_after_success(tmp_path):
    stage = stage_with_scene(tmp_path)
    rs.author_overlay(stage, overlay_packet())
    bad = scene_packet()
    bad["project"]["uid"] = "project-2"
    bad["primitives"] = [{"invalid": "scene"}]
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError):
        rs.author_scene(stage, bad, tmp_path)
    assert stage.GetRootLayer().ExportToString() == before

    next_scene = scene_packet()
    next_scene["project"]["uid"] = "project-2"
    rs.author_scene(stage, next_scene, tmp_path)
    assert stage.GetPrimAtPath("/World/RenderScene").GetCustomDataByKey("dcdtuni:projectUid") == "project-2"
    assert not stage.GetPrimAtPath("/World/RenderOverlay")


def test_editor_scene_copy_failure_preserves_previous_scene_overlay_and_camera(tmp_path, monkeypatch):
    from pxr import Sdf
    stage = stage_with_scene(tmp_path)
    rs.author_overlay(stage, overlay_packet())
    before = stage.GetRootLayer().ExportToString()
    new_scene = scene_packet()
    new_scene["project"]["uid"] = "project-2"
    real_copy = Sdf.CopySpec
    failed = False

    def fail_scene_copy(source_layer, source_path, target_layer, target_path):
        nonlocal failed
        if not failed and str(target_path) == "/World/RenderScene" and target_layer == stage.GetRootLayer():
            failed = True
            return False
        return real_copy(source_layer, source_path, target_layer, target_path)

    monkeypatch.setattr(Sdf, "CopySpec", fail_scene_copy)
    with pytest.raises(RuntimeError, match="render scene"):
        rs.author_scene(stage, new_scene, tmp_path)
    assert failed
    assert stage.GetRootLayer().ExportToString() == before


def test_scene_transfer_can_use_overlay_validator_without_changing_default():
    packet = overlay_packet()
    text = json.dumps(packet)
    transfer = rs.SceneTransfer(validator=rs.validate_overlay)
    transfer.begin("overlay-1", 2)
    transfer.chunk("overlay-1", 0, text[:80])
    transfer.chunk("overlay-1", 1, text[80:])
    assert transfer.finish("overlay-1") == packet

    invalid = overlay_packet()
    invalid["primitives"][0]["indices"] = [0, 1, 999]
    text = json.dumps(invalid)
    transfer.begin("overlay-2", 1)
    transfer.chunk("overlay-2", 0, text)
    with pytest.raises(ValueError):
        transfer.finish("overlay-2")


def test_shared_overlay_schema_also_rejects_null_key_with_nonempty_primitives():
    schema_path = Path(rs.__file__).resolve().parents[7] / "shared/schema/render-overlay.schema.json"
    schema = json.loads(schema_path.read_text())
    invalid = overlay_packet()
    invalid["source"]["meshSnapshotKey"] = None
    with pytest.raises(ValueError, match="primitives"):
        rs._validate(invalid, schema)
    invalid["primitives"] = []
    rs._validate(invalid, schema)
