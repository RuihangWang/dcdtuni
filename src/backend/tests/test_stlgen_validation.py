"""Tests for STLGenRunner geometry validation (room height vs floor y-coordinates)."""
import json
import pytest
from pathlib import Path

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._cfdrun_v2_test_helpers import wrap_meshgeometry


def _make_cfd_export(room_height: float, floors: dict) -> dict:
    """Build a minimal .cfdrun manifest dict with room height and floor definitions."""
    return {
        "version": "1.0",
        "source": "frontend",
        "projectName": "test",
        "room": {
            "height": room_height,
            "plane": [
                {"x": 0, "y": 0, "z": 0},
                {"x": 10, "y": 0, "z": 0},
                {"x": 10, "y": 10, "z": 0},
                {"x": 0, "y": 10, "z": 0},
            ],
            "locationInMesh": {"x": 5, "y": 5, "z": 1.5},
        },
        "floors": floors,
    }


def _setup_case(tmp_path: Path, cfd_data: dict) -> STLGenRunner:
    """Write a .cfdrun manifest and return an STLGenRunner pointing at it."""
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    cfd_path = model_dir / "project.cfdrun"
    cfd_path.write_text(json.dumps(wrap_meshgeometry(cfd_data)))
    return STLGenRunner(case_dir, cfdrun_path=cfd_path)


class TestRoomHeightFloorValidation:
    """room.height must be >= every floor's geometry.anchor.y (Y-up convention)."""

    def test_floor_y_exceeds_room_height_raises(self, tmp_path):
        """A floor at y=5.0 with room height=3.6 must raise RuntimeError."""
        data = _make_cfd_export(
            room_height=3.6,
            floors={
                "Ground_Floor": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
                "Real_Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 5.0, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        with pytest.raises(RuntimeError, match=r"floor.*Real_Ceiling.*y=5\.0.*exceeds.*room\.height=3\.6"):
            runner.validate_geometry()

    def test_floor_y_equals_room_height_passes(self, tmp_path):
        """A floor at y=3.6 with room height=3.6 is valid (the ceiling itself)."""
        data = _make_cfd_export(
            room_height=3.6,
            floors={
                "Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 3.6, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        # Should not raise
        runner.validate_geometry()

    def test_all_floors_within_room_height_passes(self, tmp_path):
        """Multiple floors all within room height should pass."""
        data = _make_cfd_export(
            room_height=5.0,
            floors={
                "Ground": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
                "False_Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 3.6, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wallBaffle",
                },
                "Real_Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 5.0, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        runner.validate_geometry()

    def test_sub_mm_floating_point_rounding_passes(self, tmp_path):
        """Sub-mm JS export rounding (e.g. y=3.4000000953) should be tolerated."""
        data = _make_cfd_export(
            room_height=3.4,
            floors={
                "Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 3.4000000953674316, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        runner.validate_geometry()  # should not raise

    def test_error_message_includes_fix_hint(self, tmp_path):
        """Error message should suggest updating room height in 2D floor plan."""
        data = _make_cfd_export(
            room_height=3.6,
            floors={
                "Top_Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 5.0, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        with pytest.raises(RuntimeError, match=r"2D.*floor.*plan|房间.*高度"):
            runner.validate_geometry()


def test_validate_geometry_checks_floor_anchor_y(tmp_path):
    """Post-refactor, validate_geometry must raise when floor.anchor.y
    exceeds room.height (not floor.anchor.z)."""
    data = {
        "room": {
            "bounds": {"xMin": 0, "xMax": 5, "yMin": 0, "yMax": 3,
                       "zMin": 0, "zMax": 5},
            "height": 3.0,
            "temperature": 20.0,
        },
        "floors": {
            "f1": {
                "stlFile": "merged_floor_ceiling.stl",
                "geometry": {
                    "anchor": {"x": 0, "y": 5.0, "z": 0},
                    "size": {"W": 5, "D": 5, "H": 0.1},
                },
                "kind": "floor",
                "enabled": True,
            }
        },
        "models": {},
        "bafflePlates": {},
        "simulationConfig": {
            "solverTemplate": "steady",
            "numerical": {"maxIterations": 100},
            "physics": {},
        },
    }
    runner = _setup_case(tmp_path, data)
    with pytest.raises(RuntimeError, match=r"y=5\.0.*exceeds.*room\.height=3\.0"):
        runner.validate_geometry()
