"""The room enclosure must be closed: room walls run from the room floor to room.height.

Base604's room walls stopped at y=3.5 under a 4.4 m ceiling. The browser's
room_wall.stl is the geometry source of truth, so the runner no longer
re-extrudes the walls to room.height, and the 0.9 m band under the ceiling
stayed open. dcCartMesh's flood fill went over the wall tops into the mesh
bounding box: the room walls, floor and ceiling had fluid on both sides, got no
boundary faces, and createBaffles dropped their empty patches. The mesh kept
only domain_* sides.

``validate_geometry`` now stops such a run up front, and the dcCart pipeline
warns when a mesh shows the leak (domain_* patches plus missing room patches).
A wall lifted off the floor leaves the same open band, under it, so the check
also wants every room wall to reach down to the room floor: the lowest floor
bound as the room's bottom, or y=0 without one.
"""
import json
from pathlib import Path

import pytest
from loguru import logger

from backend.aircooling.runner import mesh_acceptance
from backend.aircooling.runner.stlgen_runner import (
    STLGenRunner,
    _domain_patches_with_faces,
    _exported_box_top_y,
)
from backend.tests._cfdrun_v2_test_helpers import wrap_meshgeometry

# Base604's room wall rotations, as the browser exported them
_QUATS = {
    "Room_Wall_1": {"x": 0, "y": 0.7071067811865475, "z": 0, "w": -0.7071067811865476},
    "Room_Wall_2": {"x": 0, "y": 0, "z": 0, "w": -1},
    "Room_Wall_3": {"x": 0, "y": -0.7071067811865475, "z": 0, "w": -0.7071067811865476},
    "Room_Wall_4": {"x": 0, "y": 1, "z": 0, "w": -6.123233995736766e-17},
}


def _wall(height, quaternion=None, width=15.6, scale_y=1.0, **extra):
    wall = {
        "stlFile": "room_wall.stl",
        "geometry": {
            "anchor": {"x": 0, "y": 0, "z": 0},
            "size": {"W": width, "D": 0, "H": height},
            "orientation": 0,
            "quaternion": quaternion or {"x": 0, "y": 0, "z": 0, "w": 1},
            "scale": {"x": 1, "y": scale_y, "z": 1},
        },
    }
    wall.update(extra)
    return wall


def _runner(tmp_path: Path, walls: dict, room_height: float = 4.4, floors: dict = None) -> STLGenRunner:
    blob = {
        "version": "1.0",
        "source": "frontend",
        "projectName": "enclosure",
        "room": {
            "height": room_height,
            "plane": [
                {"x": 0, "y": 0, "z": 0}, {"x": 15.6, "y": 0, "z": 0},
                {"x": 15.6, "y": 24.6, "z": 0}, {"x": 0, "y": 24.6, "z": 0},
            ],
            "locationInMesh": {"x": 5, "y": 2, "z": 5},
        },
        "floors": floors or {},
        "walls": walls,
    }
    model_dir = tmp_path / "case" / "model"
    model_dir.mkdir(parents=True)
    cfdrun = model_dir / "project.cfdrun"
    cfdrun.write_text(json.dumps(wrap_meshgeometry(blob)))
    return STLGenRunner(tmp_path / "case", cfdrun_path=cfdrun)


class TestRoomWallsReachRoomHeight:
    def test_base604_walls_below_the_ceiling_stop_the_run(self, tmp_path):
        walls = {name: _wall(3.5, q, isRoomBoundary=True) for name, q in _QUATS.items()}
        runner = _runner(tmp_path, walls)
        with pytest.raises(RuntimeError) as exc:
            runner.validate_geometry()
        msg = str(exc.value)
        for name in _QUATS:
            assert f"'{name}' top y=3.5" in msg
        assert "room.height=4.4" in msg
        assert "enclosure is open" in msg

    def test_walls_at_the_room_height_pass(self, tmp_path):
        walls = {name: _wall(4.4, q, isRoomBoundary=True) for name, q in _QUATS.items()}
        _runner(tmp_path, walls).validate_geometry()

    def test_sub_mm_export_rounding_passes(self, tmp_path):
        _runner(tmp_path, {"W": _wall(4.3999999046, isRoomBoundary=True)}).validate_geometry()

    def test_an_interior_wall_may_stop_below_the_ceiling(self, tmp_path):
        _runner(tmp_path, {"Partition": _wall(2.0, isRoomBoundary=False)}).validate_geometry()

    def test_a_manifest_without_the_flag_is_still_checked(self, tmp_path):
        # every .cfdrun exported before isRoomBoundary existed
        with pytest.raises(RuntimeError, match=r"'Room_Wall_2' top y=3\.5"):
            _runner(tmp_path, {"Room_Wall_2": _wall(3.5)}).validate_geometry()

    def test_scale_counts(self, tmp_path):
        _runner(tmp_path, {"W": _wall(2.2, scale_y=2.0, isRoomBoundary=True)}).validate_geometry()

    def test_the_top_is_the_rotated_box_not_anchor_plus_height(self, tmp_path):
        # 90 degrees about Z: the wall's length W stands up, its height H lies down
        tilt = {"x": 0, "y": 0, "z": 0.7071067811865476, "w": 0.7071067811865476}
        _runner(tmp_path, {"W": _wall(1.0, tilt, width=5.0, isRoomBoundary=True)}).validate_geometry()
        with pytest.raises(RuntimeError, match=r"'W' top y=3\. The room enclosure is open"):
            _runner(tmp_path / "b", {"W": _wall(9.0, tilt, width=3.0, isRoomBoundary=True)}).validate_geometry()

    def test_top_helper(self):
        assert _exported_box_top_y(_wall(3.5, _QUATS["Room_Wall_1"])["geometry"]) == pytest.approx(3.5)
        assert _exported_box_top_y({"anchor": {"y": 0.8}, "size": {"W": 1, "D": 1, "H": 2}}) == pytest.approx(2.8)
        assert _exported_box_top_y({"anchor": {"y": 0}, "size": {"W": 1}}) is None


def _floor(y, binding):
    """A floor sheet as cfdrun_build.js exports it."""
    return {
        "stlFile": "merged_floor_ceiling.stl",
        "geometry": {"anchor": {"x": 0, "y": y, "z": 0}, "size": {"W": 15.6, "D": 24.6}},
        "roomBoundaryType": binding,
        "defaultBC": "wall",
        "openings": [],
    }


def _lifted(wall, y):
    wall["geometry"]["anchor"]["y"] = y
    return wall


class TestRoomWallsReachTheFloor:
    """A room wall standing off the floor leaves the same open band, under it."""

    def test_a_wall_lifted_off_the_floor_stops_the_run(self, tmp_path):
        walls = {name: _wall(4.4, q, isRoomBoundary=True) for name, q in _QUATS.items()}
        # its top, 4.9, is above the ceiling: only the bottom is wrong
        walls["Room_Wall_2"] = _lifted(_wall(4.4, _QUATS["Room_Wall_2"], isRoomBoundary=True), 0.5)
        with pytest.raises(RuntimeError) as exc:
            _runner(tmp_path, walls).validate_geometry()
        msg = str(exc.value)
        assert "'Room_Wall_2' bottom y=0.5." in msg
        assert "top y=" not in msg
        assert "Room_Wall_1" not in msg and "Room_Wall_3" not in msg and "Room_Wall_4" not in msg
        assert "floor y=0 " in msg and "room.height=4.4" in msg
        assert "enclosure is open" in msg

    def test_a_lifted_and_short_wall_names_its_bottom_and_top(self, tmp_path):
        wall = _lifted(_wall(3.0, isRoomBoundary=True), 0.5)
        with pytest.raises(RuntimeError, match=r"'W' bottom y=0\.5 top y=3\.5\. The room enclosure is open"):
            _runner(tmp_path, {"W": wall}).validate_geometry()

    def test_short_and_lifted_walls_are_listed_together(self, tmp_path):
        walls = {
            "Short": _wall(3.5, isRoomBoundary=True),
            "Lifted": _lifted(_wall(4.4, isRoomBoundary=True), 0.2),
        }
        with pytest.raises(RuntimeError, match=r"'Short' top y=3\.5, 'Lifted' bottom y=0\.2\. "):
            _runner(tmp_path, walls).validate_geometry()

    def test_a_wall_reaching_below_the_floor_passes(self, tmp_path):
        _runner(tmp_path, {"W": _lifted(_wall(4.9, isRoomBoundary=True), -0.5)}).validate_geometry()

    def test_sub_mm_lift_passes(self, tmp_path):
        _runner(tmp_path, {"W": _lifted(_wall(4.4, isRoomBoundary=True), 0.0005)}).validate_geometry()

    def test_an_interior_wall_may_stand_off_the_floor(self, tmp_path):
        wall = _lifted(_wall(2.0, isRoomBoundary=False), 1.0)
        _runner(tmp_path, {"Partition": wall}).validate_geometry()

    def test_a_manifest_without_the_flag_is_still_checked(self, tmp_path):
        with pytest.raises(RuntimeError, match=r"'Room_Wall_2' bottom y=0\.3"):
            _runner(tmp_path, {"Room_Wall_2": _lifted(_wall(4.4), 0.3)}).validate_geometry()

    def test_without_a_bound_floor_the_room_floor_is_y0(self, tmp_path):
        with pytest.raises(RuntimeError, match=r"floor y=0 .*'W' bottom y=0\.2"):
            _runner(tmp_path, {"W": _lifted(_wall(4.4, isRoomBoundary=True), 0.2)}).validate_geometry()

    def test_the_room_floor_is_the_lowest_floor_bound_as_the_bottom(self, tmp_path):
        floors = {"Room_Floor": _floor(0.3, "bottom"), "Room_Ceiling": _floor(4.4, "top")}
        standing = _lifted(_wall(4.1, isRoomBoundary=True), 0.3)
        _runner(tmp_path, {"W": standing}, floors=floors).validate_geometry()
        pit = dict(floors, Pit_Floor=_floor(-0.2, "bottom"))
        with pytest.raises(RuntimeError, match=r"floor y=-0\.2 .*'W' bottom y=0\.3"):
            _runner(tmp_path / "b", {"W": _lifted(_wall(4.1, isRoomBoundary=True), 0.3)}, floors=pit).validate_geometry()

    def test_an_unbound_raised_floor_is_not_the_room_floor(self, tmp_path):
        # Base604's Room_Raising_Floor sits at y=0.8, bound to nothing
        floors = {"Room_Floor": _floor(0, "bottom"), "Room_Raising_Floor": _floor(0.8, "none")}
        with pytest.raises(RuntimeError, match=r"'W' bottom y=0\.8"):
            _runner(tmp_path, {"W": _lifted(_wall(3.6, isRoomBoundary=True), 0.8)}, floors=floors).validate_geometry()

    def test_base604_walls_on_the_floor_pass_with_its_floors(self, tmp_path):
        floors = {
            "Room_Floor": _floor(0, "bottom"),
            "Room_Ceiling": _floor(4.4, "top"),
            "Room_Raising_Floor": _floor(0.8, "none"),
        }
        walls = {name: _wall(4.4, q, isRoomBoundary=True) for name, q in _QUATS.items()}
        _runner(tmp_path, walls, floors=floors).validate_geometry()

    def test_the_bottom_is_the_rotated_box_not_the_anchor(self, tmp_path):
        # -90 degrees about Z: the wall's length W hangs down from its anchor
        tilt = {"x": 0, "y": 0, "z": -0.7071067811865476, "w": 0.7071067811865476}
        _runner(tmp_path, {"W": _lifted(_wall(1.0, tilt, width=5.0, isRoomBoundary=True), 5.0)}).validate_geometry()
        with pytest.raises(RuntimeError, match=r"'W' bottom y=0\.5\. The room enclosure is open"):
            _runner(tmp_path / "b", {"W": _lifted(_wall(1.0, tilt, width=5.0, isRoomBoundary=True), 5.5)}).validate_geometry()

    def test_y_range_helper(self):
        from backend.aircooling.runner.stlgen_runner import _exported_box_y_range

        assert _exported_box_y_range({"anchor": {"y": 0.8}, "size": {"W": 1, "D": 1, "H": 2}}) == pytest.approx((0.8, 2.8))
        assert _exported_box_y_range(_lifted(_wall(4.4, _QUATS["Room_Wall_4"]), 0.5)["geometry"]) == pytest.approx((0.5, 4.9))
        assert _exported_box_y_range({"anchor": {"y": 0}, "size": {"W": 1}}) is None


_BOUNDARY = """FoamFile { class polyBoundaryMesh; object boundary; }
3
(
    Rack_1_inlet
    {
        type            patch;
        nFaces          120;
        startFace       900;
    }
    domain_xMin
    {
        type            wall;
        inGroups        List<word> 1(wall);
        nFaces          4869;
        startFace       1020;
    }
    domain_yMax
    {
        type            wall;
        nFaces          0;
        startFace       5889;
    }
)
"""


def _mesh_with_boundary(root: Path, text: str) -> Path:
    poly = root / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "boundary").write_text(text)
    return root


def test_domain_patches_with_faces_skips_empty_ones(tmp_path):
    mesh = _mesh_with_boundary(tmp_path, _BOUNDARY)
    assert _domain_patches_with_faces(mesh / "constant" / "polyMesh" / "boundary") == ["domain_xMin"]
    assert _domain_patches_with_faces(tmp_path / "nothing") == []


class _Capture:
    def __enter__(self):
        self.messages = []
        self._id = logger.add(lambda m: self.messages.append(str(m)), level="WARNING", format="{message}")
        return self

    def __exit__(self, *exc):
        logger.remove(self._id)


def _leaky_runner(tmp_path):
    walls = {name: _wall(3.5, q, isRoomBoundary=True) for name, q in _QUATS.items()}
    walls["Partition"] = _wall(2.0, isRoomBoundary=False)
    runner = _runner(tmp_path, walls)
    runner._load_cfd_json_only()
    runner._data["floors"] = {
        "Room_Floor": {"roomBoundaryType": "bottom"},
        "Room_Ceiling": {"roomBoundaryType": "top"},
        "Room_Raising_Floor": {"roomBoundaryType": "none"},
    }
    return runner


def test_leaked_enclosure_is_warned_about(tmp_path, monkeypatch):
    runner = _leaky_runner(tmp_path)
    mesh = _mesh_with_boundary(tmp_path / "Mesh", _BOUNDARY)
    report = mesh_acceptance.MeshAcceptance(
        case="enclosure",
        missing_patches=["Room_Ceiling", "Room_Floor", "Room_Wall_1", "Room_Wall_2",
                         "Room_Wall_3", "Room_Wall_4", "Rack_9_outlet"],
        unexpected_patches=["domain_xMin", "stray_patch"],
    )
    calls = []
    monkeypatch.setattr(mesh_acceptance, "check_patch_solid_correspondence",
                        lambda d, case_name="": calls.append(d) or report)
    with _Capture() as cap:
        runner._warn_open_room_enclosure(mesh)
    assert calls == [mesh]
    text = "\n".join(cap.messages)
    assert "Room enclosure is open" in text
    for name in ("Room_Wall_1", "Room_Wall_4", "Room_Floor", "Room_Ceiling", "domain_xMin"):
        assert name in text
    # only the enclosure is named, not every unrelated missing / unexpected patch
    assert "Rack_9_outlet" not in text and "stray_patch" not in text


def test_a_closed_mesh_is_not_checked(tmp_path, monkeypatch):
    runner = _leaky_runner(tmp_path)
    closed = _BOUNDARY.replace("nFaces          4869;", "nFaces          0;")
    mesh = _mesh_with_boundary(tmp_path / "Mesh", closed)
    monkeypatch.setattr(mesh_acceptance, "check_patch_solid_correspondence",
                        lambda *a, **k: pytest.fail("the 20 s check ran on a closed mesh"))
    with _Capture() as cap:
        runner._warn_open_room_enclosure(mesh)
    assert cap.messages == []


def test_domain_patches_without_missing_room_patches_are_not_an_enclosure_leak(tmp_path, monkeypatch):
    runner = _leaky_runner(tmp_path)
    mesh = _mesh_with_boundary(tmp_path / "Mesh", _BOUNDARY)
    report = mesh_acceptance.MeshAcceptance(case="x", unexpected_patches=["domain_xMin"])
    monkeypatch.setattr(mesh_acceptance, "check_patch_solid_correspondence", lambda *a, **k: report)
    with _Capture() as cap:
        runner._warn_open_room_enclosure(mesh)
    assert cap.messages == []


def test_a_failing_check_never_breaks_the_mesh_run(tmp_path, monkeypatch):
    runner = _leaky_runner(tmp_path)
    mesh = _mesh_with_boundary(tmp_path / "Mesh", _BOUNDARY)

    def boom(*a, **k):
        raise OSError("unreadable faces")

    monkeypatch.setattr(mesh_acceptance, "check_patch_solid_correspondence", boom)
    with _Capture() as cap:
        runner._warn_open_room_enclosure(mesh)
    assert any("skipped" in m for m in cap.messages)


class _Pipeline:
    """What _run_dcCart_mesh_pipeline touches, recording the order of the steps."""

    def __init__(self, case_dir):
        self.mesh_dir = case_dir
        self.steps = []

    def _start_mesh_budget(self): pass
    def _clear_dcCart_previous_outputs(self, case_dir): pass
    def _ensure_mesh_system_files(self): pass
    def _clean_stale_polymesh_zones(self, case_dir): pass
    def _write_dcCart_mesh_dict(self, system_dir): pass
    def _mesh_time_remaining(self): return None
    def _diagnose_background_patch(self, case_dir): pass
    def _drop_empty_baffle_patches(self, case_dir): pass
    def _regenerate_fields_for_baffles(self, case_dir): pass
    def _dcCart_split_baffles(self, case_dir, n_procs): return False

    @staticmethod
    def _resolve_mesher_binary(name): return "/bin/" + name

    def _run_cmd(self, cmd, workdir): self.steps.append(cmd.split()[0])
    def _convert_wall_baffle_patches_to_wall(self, case_dir): self.steps.append("convert")
    def _warn_open_room_enclosure(self, case_dir): self.steps.append("enclosure")
    def _remove_disconnected_cells(self, case_dir): self.steps.append("checkMesh")

    def __getattr__(self, name):
        # Budget checks, receipts and archives the pipeline grew since: not
        # part of the order this test pins.
        if name.startswith("_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


def test_dcCart_pipeline_checks_the_enclosure_after_createBaffles(tmp_path):
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "createBafflesDict").write_text("")
    p = _Pipeline(tmp_path)
    STLGenRunner._run_dcCart_mesh_pipeline(p, 1)
    assert p.steps == ["/bin/dcCartMesh", "createBaffles", "convert", "enclosure", "checkMesh"]
