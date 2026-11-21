"""TDD: room_wall + merged_floor_ceiling STL and blockMeshDict must be
generated in Y-up orientation.

User symptom: Shinaide mesh "居然只有房间的一半". The frontend exports
the room polygon as `plane[i] = {x: ..., y: <face-local 2nd coord>, z: 0}`
where the face-local 2nd coord is a Z-axis (depth) value under Y-up.

The runner's `_ensure_watertight_room_enclosure` and
`_generate_block_mesh_dict` still treat `plane[i].y` as a WORLD-Y value
(Z-up convention) and extrude along Z. This produces a room enclosure
in the XY plane at Z ∈ [0, height] while all other STL files (racks,
floors, ...) live in the XZ plane at Y ∈ [0, height]. The two are
orthogonal → snappyHexMesh only keeps the intersection → half room.

Fix:
- Under Y-up, `plane[i].y` is the Z-axis (depth) floor-plan coord.
- Walls extrude along Y from y=0 to y=height.
- Wall outward normal is `(-dz, 0, dx)` for CCW polygon.
- blockMesh vertices use (x, 0..h, z) not (x, ..., 0..h).
"""
import json
import re
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._cfdrun_v2_test_helpers import wrap_meshgeometry


def _make_minimal_cfdrun(
    tmp_path: Path, plane: list, height: float, browser_enclosure: bool = True
) -> Path:
    """Build a minimal .cfdrun payload with a specific room polygon.

    `browser_enclosure=False` leaves room_wall.stl / merged_floor_ceiling.stl
    out of the export. `_ensure_watertight_room_enclosure` keeps a
    browser-exported enclosure as-is and synthesises one only when it is
    missing, so the STL tests below must start without it.
    """
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_dir.mkdir(parents=True)
    if browser_enclosure:
        (tri_dir / "room_wall.stl").write_text(
            "solid Room_Wall_1\nendsolid Room_Wall_1\n"
            "solid Room_Wall_2\nendsolid Room_Wall_2\n"
            "solid Room_Wall_3\nendsolid Room_Wall_3\n"
            "solid Room_Wall_4\nendsolid Room_Wall_4\n"
        )
        (tri_dir / "merged_floor_ceiling.stl").write_text(
            "solid Room_Floor\nendsolid Room_Floor\n"
            "solid Room_Ceiling\nendsolid Room_Ceiling\n"
        )
    payload = {
        "room": {
            "height": height,
            "plane": plane,
            "locationInMesh": {"x": 2.0, "y": 1.5, "z": 2.0},
        },
        "meshConfig": {"baseSize": 0.5, "maxGlobalCells": 1000000},
        "floors": {},
    }
    cfdrun_path = model_dir / "test.cfdrun"
    cfdrun_path.write_text(json.dumps(wrap_meshgeometry(payload)))
    return case_dir


def test_block_mesh_dict_uses_yup_vertical():
    """blockMeshDict must place Y-axis as vertical (0..height), with XZ
    as the horizontal floor plan."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plane = [
            {"x": 0.0, "y": 0.0, "z": 0.0},
            {"x": 4.0, "y": 0.0, "z": 0.0},
            {"x": 4.0, "y": 5.0, "z": 0.0},  # face-local 2nd coord = Z-depth
            {"x": 0.0, "y": 5.0, "z": 0.0},
        ]
        case_dir = _make_minimal_cfdrun(tmp, plane, height=3.0)
        runner = STLGenRunner(case_dir)
        runner._load_cfd_json_only()
        runner._stl_solids = {
            "room_wall.stl": ["Room_Wall_1", "Room_Wall_2", "Room_Wall_3", "Room_Wall_4"],
            "merged_floor_ceiling.stl": ["Room_Floor", "Room_Ceiling"],
        }
        system_dir = case_dir / "Mesh" / "system"
        system_dir.mkdir(parents=True, exist_ok=True)
        runner._generate_block_mesh_dict(system_dir)
        content = (system_dir / "blockMeshDict").read_text()

        # Walls extend X ≈ [0,4] and Z ≈ [0,5] (horizontal floor plan),
        # with Y ≈ [0,3] vertical. Accept 10% margins.
        m = re.search(
            r"\(\s*(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s*\)",
            content,
        )
        assert m, "blockMeshDict vertex not parseable"
        x0, y0, z0 = map(float, m.groups())
        # Under Y-up: first vertex should be lower-back-lower-left, around
        # (x≈-0.4, y≈-0.3, z≈-0.5) after 10% margin.
        # Specifically, the Y-extent must be ~3m (the height) — NOT ~5m
        # (which was the face-local 2nd coord / Z-depth).
        assert -0.5 <= y0 <= 0.0, (
            f"y_min = {y0} looks wrong for Y-up. Under Y-up the vertical "
            f"axis is Y, so y_min should be slightly below 0 (floor at 0). "
            f"If this is the face-local Z-depth bound, the runner is still "
            f"Z-up."
        )

        # Parse the full Y range from the 4th-7th vertices (top of blockMesh).
        # Accept any dimension that covers ~3m (height + margin) on Y.
        # Only match inside the vertices (...) block to avoid hex/cells tuples.
        verts_block = re.search(r"vertices\s*\(\s*(.*?)\s*\)\s*;", content, re.DOTALL)
        assert verts_block, "blockMeshDict missing vertices block"
        verts = re.findall(
            r"\(\s*(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s*\)",
            verts_block.group(1),
        )
        assert len(verts) == 64, f"expected 64 blockMesh vertices (graded multi-block mesh), got {len(verts)}"
        ys = [float(v[1]) for v in verts]
        zs = [float(v[2]) for v in verts]
        y_range = max(ys) - min(ys)
        z_range = max(zs) - min(zs)
        assert 3.0 <= y_range <= 5.0, (
            f"Y range {y_range:.2f} should be ~3-4m (height + 1-cell padding). "
            f"Z range is {z_range:.2f}. If these are swapped, the runner is "
            f"still Z-up."
        )
        assert 5.0 <= z_range <= 7.0, (
            f"Z range {z_range:.2f} should be ~5-6m (polygon depth + 1-cell padding). "
            f"If this is ~3m, Y and Z are still swapped."
        )


def test_room_wall_stl_uses_yup_extrusion():
    """_ensure_watertight_room_enclosure must extrude walls along Y, not Z.

    A CCW unit square polygon {(0,0), (4,0), (4,5), (0,5)} at height=3 in
    Y-up produces walls whose outward normals lie in the XZ plane and
    vertex Y coordinates ∈ {0, 3}.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plane = [
            {"x": 0.0, "y": 0.0, "z": 0.0},
            {"x": 4.0, "y": 0.0, "z": 0.0},
            {"x": 4.0, "y": 5.0, "z": 0.0},
            {"x": 0.0, "y": 5.0, "z": 0.0},
        ]
        case_dir = _make_minimal_cfdrun(tmp, plane, height=3.0, browser_enclosure=False)
        runner = STLGenRunner(case_dir)
        runner._load_cfd_json_only()
        # No enclosure in the export -> nothing discovered for it.
        runner._stl_solids = {}
        runner._ensure_watertight_room_enclosure()

        stl = (case_dir / "Mesh" / "constant" / "triSurface" / "room_wall.stl").read_text()

        # All vertex Y coords must be ∈ {0.0, 3.0}. (Tolerance 1e-6.)
        verts = re.findall(
            r"vertex\s+(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+"
            r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+"
            r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)",
            stl,
        )
        assert verts, "No vertices found in room_wall.stl"
        ys = sorted(set(round(float(v[1]), 3) for v in verts))
        assert ys == [0.0, 3.0], (
            f"Wall vertex Y coords should be {{0.0, 3.0}} (extruded along Y "
            f"for Y-up). Got {ys}. If Y is all 0 and Z covers {{0, 3}}, the "
            f"runner is still Z-up-extruding."
        )

        # Facet normals in the XZ plane → middle component (Y) ≈ 0.
        normals = re.findall(
            r"facet normal\s+(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+"
            r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+"
            r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)",
            stl,
        )
        assert normals, "No facet normals found"
        for nx, ny, nz in normals:
            ny_v = float(ny)
            assert abs(ny_v) < 1e-6, (
                f"Wall facet normal Y component {ny_v} should be 0 "
                f"(walls are vertical in XZ plane under Y-up)."
            )


def test_floor_ceiling_stl_uses_yup():
    """Floor at Y=0, ceiling at Y=height."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plane = [
            {"x": 0.0, "y": 0.0, "z": 0.0},
            {"x": 4.0, "y": 0.0, "z": 0.0},
            {"x": 4.0, "y": 5.0, "z": 0.0},
            {"x": 0.0, "y": 5.0, "z": 0.0},
        ]
        case_dir = _make_minimal_cfdrun(tmp, plane, height=3.0, browser_enclosure=False)
        runner = STLGenRunner(case_dir)
        runner._load_cfd_json_only()
        # No enclosure in the export -> nothing discovered for it.
        runner._stl_solids = {}
        runner._ensure_watertight_room_enclosure()

        stl = (case_dir / "Mesh" / "constant" / "triSurface" / "merged_floor_ceiling.stl").read_text()
        verts = re.findall(
            r"vertex\s+(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+"
            r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+"
            r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)",
            stl,
        )
        ys = sorted(set(round(float(v[1]), 3) for v in verts))
        assert ys == [0.0, 3.0], (
            f"Floor/ceiling Y coords should be {{0.0, 3.0}} under Y-up. "
            f"Got {ys}."
        )
