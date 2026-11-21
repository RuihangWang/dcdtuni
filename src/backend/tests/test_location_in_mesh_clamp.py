"""Regression test: the primary locationInMesh seed must be clamped inside the
room domain before it reaches snappyHexMesh.

An un-customized default seed (5, 4, 1.5) falls outside small rooms. Before the
fix, only the Y (vertical) coordinate was clamped to room height, so X/Z stayed
outside and snappyHexMesh aborted with:

    FOAM FATAL ERROR: Point (5 0.447 1.5) is not inside the mesh or on a face or
    edge.

_build_locations_in_mesh_entries now snaps any out-of-bounds coordinate to the
room centre on that axis (best-effort interior point) while leaving valid,
user-positioned coordinates untouched.

Runner footprint mapping (see _generate_block_mesh_dict): plane.x -> X,
plane.y -> Z (depth), height -> Y (vertical).
"""
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.cfdrun_schema import CFDExport
from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._cfdrun_v2_test_helpers import wrap_meshgeometry

# Small room: X in [-0.6, 2.8], Z(depth) in [0.0, 1.2], height (Y) 0.9.
PLANE = [
    {"x": -0.6, "y": 0.0, "z": 0.0},
    {"x": 2.8, "y": 0.0, "z": 0.0},
    {"x": 2.8, "y": 1.2, "z": 0.0},
    {"x": -0.6, "y": 1.2, "z": 0.0},
]
ROOM_HEIGHT = 0.9
CX, CY, CZ = (-0.6 + 2.8) / 2, ROOM_HEIGHT / 2, (0.0 + 1.2) / 2  # 1.1, 0.45, 0.6


def _runner(tmp_path: Path, location_in_mesh: dict) -> STLGenRunner:
    case_dir = tmp_path / "case"
    (case_dir / "model").mkdir(parents=True)
    (case_dir / "Mesh" / "constant" / "triSurface").mkdir(parents=True)
    cfd = case_dir / "model" / "project.cfdrun"
    cfd.write_text(
        json.dumps(wrap_meshgeometry(CFDExport().model_dump(mode="json"))),
        encoding="utf-8",
    )

    runner = STLGenRunner(case_dir, cfdrun_path=cfd)
    runner._load_cfd_json_only()
    runner._data["room"] = {
        "height": ROOM_HEIGHT,
        "plane": PLANE,
        "locationInMesh": location_in_mesh,
        "extraLocationsInMesh": [],
    }
    # Isolate the primary seed: silence the other seed contributors.
    runner._build_patch_driven_none_seed_entries = lambda: []
    runner._build_partition_hole_cellzone_seed_entries = lambda: []
    runner._build_partition_wall_seed_entries = lambda: []
    return runner


def test_out_of_bounds_default_seed_relocated_to_room_centre(tmp_path):
    """The default (5,4,1.5) seed is outside on all three axes -> room centre."""
    runner = _runner(tmp_path, {"x": 5.0, "y": 4.0, "z": 1.5})
    entries = runner._build_locations_in_mesh_entries()

    assert entries, "expected a primary seed entry"
    (x, y, z), mode = entries[0]
    assert mode == "none"
    assert (x, y, z) == pytest.approx((CX, CY, CZ))
    # And the relocated point lies strictly inside the domain.
    assert -0.6 <= x <= 2.8
    assert 0.0 <= y <= ROOM_HEIGHT
    assert 0.0 <= z <= 1.2


def test_in_bounds_user_seed_is_left_untouched(tmp_path):
    """A valid, user-positioned seed must not be moved (no regression)."""
    runner = _runner(tmp_path, {"x": 1.0, "y": 0.4, "z": 0.5})
    (x, y, z), _ = runner._build_locations_in_mesh_entries()[0]
    assert (x, y, z) == pytest.approx((1.0, 0.4, 0.5))


def test_only_out_of_bounds_axis_is_snapped(tmp_path):
    """X,Y in range but Z outside -> only Z is relocated to the room centre."""
    runner = _runner(tmp_path, {"x": 1.0, "y": 0.4, "z": 9.0})
    (x, y, z), _ = runner._build_locations_in_mesh_entries()[0]
    assert x == pytest.approx(1.0)
    assert y == pytest.approx(0.4)
    assert z == pytest.approx(CZ)  # snapped


def test_below_floor_vertical_seed_is_snapped(tmp_path):
    """A negative Y (below the floor) is also out of bounds -> centre height."""
    runner = _runner(tmp_path, {"x": 1.0, "y": -2.0, "z": 0.5})
    (x, y, z), _ = runner._build_locations_in_mesh_entries()[0]
    assert y == pytest.approx(CY)
    assert (x, z) == pytest.approx((1.0, 0.5))
