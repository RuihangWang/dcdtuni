"""TDD: partition wall seed axis under Y-up.

Frontend mapping (STL_generate.html:6958-6960):
    BoxGeometry(W, H, D)  with translate(W/2, H/2, D/2)
    → W along local-X, H along local-Y (vertical), D along local-Z (thin).

Runner was Z-up: mapped thickness to local-Y via `_quat_rotate((0,1,0), quat)`.
Under Y-up that extracts the vertical (H) axis, making the partition
wall look like it's lying flat.

Symptom from base_leakage run log:
    direction=(0.000000, 1.000000, 0.000000)
    size=(4.034975, 0.050000, 2.718000)
    clearance_pos=1.842, clearance_neg=0.025 (walls stops 25mm from the floor)

Fix: D is along local-Z under Y-up → rotate (0, 0, 1), and local_center
for seed placement is (W/2, H/2, D/2).
"""
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._cfdrun_v2_test_helpers import wrap_meshgeometry


def _make_partition_case(tmp_path: Path) -> Path:
    """Build a minimal .cfdrun exercising one vertical partition wall."""
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_dir.mkdir(parents=True)
    (tri_dir / "merged_partition_wall.stl").write_text(
        "solid Partition_Wall_1_body\nendsolid Partition_Wall_1_body\n"
    )
    (tri_dir / "room_wall.stl").write_text(
        "solid Room_Wall_1\nendsolid Room_Wall_1\n"
    )
    (tri_dir / "merged_floor_ceiling.stl").write_text(
        "solid Room_Floor\nendsolid Room_Floor\n"
        "solid Room_Ceiling\nendsolid Room_Ceiling\n"
    )
    payload = {
        "room": {
            "height": 3.0,
            "plane": [
                {"x": 0.0, "y": 0.0, "z": 0.0},
                {"x": 10.0, "y": 0.0, "z": 0.0},
                {"x": 10.0, "y": 10.0, "z": 0.0},
                {"x": 0.0, "y": 10.0, "z": 0.0},
            ],
            "locationInMesh": {"x": 5.0, "y": 1.5, "z": 5.0},
        },
        "meshConfig": {"baseSize": 0.5, "maxGlobalCells": 1000000},
        "floors": {},
        "partitionWalls": {
            "Partition_Wall_1": {
                "stlFile": "merged_partition_wall.stl",
                "geometry": {
                    # Wall spans X=[2,6], Y=[0,2.7] (vertical), Z=[4,4.05]
                    "anchor": {"x": 2.0, "y": 0.0, "z": 4.0},
                    "size": {"W": 4.0, "D": 0.05, "H": 2.7},
                    "orientation": 0,
                    "transformQuaternion": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                },
                "wallSolidNames": ["Partition_Wall_1_body"],
                "holes": [],
            }
        },
    }
    cfdrun_path = model_dir / "test.cfdrun"
    cfdrun_path.write_text(json.dumps(wrap_meshgeometry(payload)))
    return case_dir


def test_partition_wall_seed_direction_is_horizontal():
    """Under Y-up, a vertical partition wall (identity quat) must have
    horizontal (XZ plane) thickness direction, not vertical Y."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        case_dir = _make_partition_case(Path(td))
        runner = STLGenRunner(case_dir)
        runner._load_cfd_json_only()
        runner._stl_solids = {
            "merged_partition_wall.stl": ["Partition_Wall_1_body"],
            "room_wall.stl": ["Room_Wall_1"],
            "merged_floor_ceiling.stl": ["Room_Floor", "Room_Ceiling"],
        }

        # Bypass triangle-cache + clearance check: just make sure the
        # direction the runner computes is horizontal. We patch
        # _nearest_ray_clearance to return a huge value so the check
        # passes, then read the returned seed entries.
        with patch(
            "backend.aircooling.runner.stlgen_runner._nearest_ray_clearance",
            return_value=10.0,
        ), patch(
            "backend.aircooling.runner.stlgen_runner._flatten_scene_triangles",
            return_value=[],
        ), patch(
            "backend.aircooling.runner.stlgen_runner._resolve_partition_wall_body_surfaces",
            return_value=(set(), ["Partition_Wall_1_body"]),
        ):
            entries = runner._build_partition_wall_seed_entries()

    # Two seeds: center + direction*offset and center - direction*offset.
    assert len(entries) == 2, f"expected 2 seed entries, got {len(entries)}"
    (p_pos, _), (p_neg, _) = entries
    # Under Y-up: wall stands up, thickness along world-Z. So the two seeds
    # differ only in Z (and maybe slightly in X/Y if quat rotates).
    dx = abs(p_pos[0] - p_neg[0])
    dy = abs(p_pos[1] - p_neg[1])
    dz = abs(p_pos[2] - p_neg[2])
    assert dy < 1e-6, (
        f"Y-up partition wall: seed points should differ in X/Z only (horizontal "
        f"thickness). Got dx={dx:.4f} dy={dy:.4f} dz={dz:.4f}. If dy ≈ offset, "
        f"the runner still treats D as local-Y (Z-up leftover)."
    )
    # With identity quat + local-Z thickness, the offset should be along world-Z.
    assert dz > 0.04, (
        f"Identity quat + Y-up: thickness should be along world-Z. Got dz={dz:.4f}."
    )


def test_partition_wall_seed_center_uses_h_as_local_y():
    """The seed center's Y coordinate must be at H/2 (vertical mid), not D/2."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        case_dir = _make_partition_case(Path(td))
        runner = STLGenRunner(case_dir)
        runner._load_cfd_json_only()
        runner._stl_solids = {
            "merged_partition_wall.stl": ["Partition_Wall_1_body"],
            "room_wall.stl": ["Room_Wall_1"],
            "merged_floor_ceiling.stl": ["Room_Floor", "Room_Ceiling"],
        }

        with patch(
            "backend.aircooling.runner.stlgen_runner._nearest_ray_clearance",
            return_value=10.0,
        ), patch(
            "backend.aircooling.runner.stlgen_runner._flatten_scene_triangles",
            return_value=[],
        ), patch(
            "backend.aircooling.runner.stlgen_runner._resolve_partition_wall_body_surfaces",
            return_value=(set(), ["Partition_Wall_1_body"]),
        ):
            entries = runner._build_partition_wall_seed_entries()

    # Center Y should be anchor.y + H/2 = 0.0 + 2.7/2 = 1.35.
    (p_pos, _), (p_neg, _) = entries
    center_y = (p_pos[1] + p_neg[1]) * 0.5
    assert abs(center_y - 1.35) < 1e-6, (
        f"Seed center Y should be at H/2=1.35 (wall vertical midpoint). "
        f"Got {center_y:.4f}. If this is D/2=0.025, the runner still maps D "
        f"to local-Y (Z-up leftover)."
    )
