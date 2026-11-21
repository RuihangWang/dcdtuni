"""Red-green test: thin partition wall (D < 2*h_local) must be caught
preflight because snappyHexMesh leaves orphaned cells inside the body,
producing a disconnected mesh (*Number of regions: 2*) that makes the
T-solver diverge on the very first iteration.

Root cause from Base_leakage debugging (2026-04-16):
  Partition_Wall_1  D=0.05 m,  baseSize=0.2 m,  refinementLevel=[1,1]
  → h_local = 0.2 / 2**1 = 0.1 m  (twice the wall thickness)
  → snappy traps 1 orphan cell at the wall's front face
  → checkMesh: *Number of regions: 2 (914857 + 1)
  → solver: Solving for T, Initial residual = 0.999966,
            Final residual = nan, No Iterations 1000
"""
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._cfdrun_v2_test_helpers import wrap_meshgeometry


def _make_runner_with_thin_wall(d: float, base_size: float, level: int):
    runner = STLGenRunner.__new__(STLGenRunner)
    runner._baffle_physics = {}
    runner._stl_solids = {"merged_partition_wall.stl": ["Partition_Wall_1_body"]}
    runner._data = {
        "baffles": {},
        "floors": {},
        "racks": {},
        "partitionWalls": {
            "Partition_Wall_1": {
                "stlFile": "merged_partition_wall.stl",
                "geometry": {
                    "anchor": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "size": {"W": 4.0, "D": d, "H": 2.7},
                    "transformQuaternion": {"x": 0, "y": 0, "z": 0, "w": 1},
                },
                "wallSolidNames": ["Partition_Wall_1_body"],
                "holes": [],
            }
        },
        "meshConfig": {
            "baseSize": base_size,
            "partitionWallRefinementLevel": [level, level],
            "refinementLevel": [level, level],
            "baffleRefinementLevel": [level, level],
        },
    }
    runner.model_dir = Path("/tmp")
    runner.case_dir = Path("/tmp")
    runner.tri_surface_dir = Path("/tmp")
    return runner


def test_thin_partition_wall_warns_but_does_not_bump():
    """D=0.05 m with baseSize=0.2 m, level=1 → preflight logs a warning
    recommending level ≥ 3 but does NOT auto-bump (frontend owns that).
    Does NOT raise."""
    runner = _make_runner_with_thin_wall(d=0.05, base_size=0.2, level=1)
    runner._preflight_partition_wall_thickness()
    unchanged = runner._data["meshConfig"]["partitionWallRefinementLevel"]
    assert unchanged == [1, 1], f"expected level unchanged at [1,1], got {unchanged}"


def test_thick_partition_wall_passes_preflight_unchanged():
    """D=0.30 m with baseSize=0.2 m, level=1 → h_local=0.1 m < D/2=0.15 m.
    Preflight succeeds and leaves the level alone."""
    runner = _make_runner_with_thin_wall(d=0.30, base_size=0.2, level=1)
    runner._preflight_partition_wall_thickness()
    assert runner._data["meshConfig"]["partitionWallRefinementLevel"] == [1, 1]


def test_thin_wall_ok_if_refinement_high_enough():
    """D=0.05 m, level=3 → h_local=0.025 m ≤ D/2=0.025 m → unchanged."""
    runner = _make_runner_with_thin_wall(d=0.05, base_size=0.2, level=3)
    runner._preflight_partition_wall_thickness()
    assert runner._data["meshConfig"]["partitionWallRefinementLevel"] == [3, 3]


def test_no_partition_wall_is_noop():
    runner = STLGenRunner.__new__(STLGenRunner)
    runner._data = {"partitionWalls": {}}
    runner._preflight_partition_wall_thickness()



# ----------------------------------------------------------------------
# Regression: solve-only must NOT delete user-uploaded helper STLs
# ----------------------------------------------------------------------
# Earlier, _load unconditionally `unlink()`d any partition_hole_*.stl
# the cfdrun marked stale. Toggling leakageRate=100 during a debug
# session removed the user's STL irrecoverably; the next solve crashed
# with "Referenced partition helper STL ... is missing". Fix:
# stop deleting from disk, just drop from the in-memory _stl_solids map.
# Mesh-phase re-upload (server overwrites triSurface) handles real cleanup.

def test_load_does_not_unlink_user_stls(tmp_path):
    """Even when cfdrun considers a partition_hole_*.stl stale (zone
    not enabled), _load must NOT physically delete the file on disk."""
    import json
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    # Build a minimal triSurface with a stale partition_hole STL
    tri = tmp_path / "triSurface"
    tri.mkdir()
    helper = tri / "partition_hole_OldZone.stl"
    helper.write_text("solid OldZone\nendsolid OldZone\n")
    # Required grouped STL so _load doesn't bail on primary_geometry_found
    (tri / "merged_partition_wall.stl").write_text(
        "solid Partition_Wall_1_body\nendsolid Partition_Wall_1_body\n")

    # Minimal cfdrun: partition wall present but hole disabled (leakage=100)
    case = tmp_path / "case"
    (case / "model").mkdir(parents=True)
    cfdrun = case / "model" / "x.cfdrun"
    cfdrun.write_text(json.dumps(wrap_meshgeometry({
        "version": "1.0",
        "source": "frontend",
        "projectName": "x",
        "room": {"height": 3.0, "plane": [
            {"x": 0, "y": 0, "z": 0}, {"x": 5, "y": 0, "z": 0},
            {"x": 5, "y": 5, "z": 0}, {"x": 0, "y": 5, "z": 0},
        ], "locationInMesh": {"x": 2.5, "y": 2.5, "z": 1.0}},
        "partitionWalls": {"Partition_Wall_1": {
            "stlFile": "merged_partition_wall.stl",
            "geometry": {
                "anchor": {"x": 0, "y": 2, "z": 0},
                "size": {"W": 4, "D": 0.5, "H": 2.5},
                "transformQuaternion": {"x": 0, "y": 0, "z": 0, "w": 1},
            },
            "wallSolidNames": ["Partition_Wall_1_body"],
            "holes": [{
                "patchId": "p1", "shape": "rectangle", "name": "Old",
                "zoneName": "OldZone",
                "zoneStlFile": None,  # legacy hole now disabled
                "physics": {"resistanceEnabled": False, "leakageRate": 100},
            }],
        }},
        "meshConfig": {"baseSize": 0.5, "partitionWallRefinementLevel": [1, 1]},
    })))

    runner = STLGenRunner(case_dir=case, cfdrun_path=cfdrun)
    runner.tri_surface_dir = tri
    runner._load()
    assert helper.exists(), \
        "STL was physically deleted by _load — destructive across re-runs"
