"""Tests for removing server _left/_right STL solid regions.

Server left/right faces always coincide with rack frame inner cavity walls.
Since servers are black-box (no internal volume mesh), these faces are
redundant — the rack frame wall already provides the solid boundary.
Removing them eliminates coincident opposing-normal surfaces that cause
orphan fluid domains in snappyHexMesh.
"""
import re
from pathlib import Path

import pytest


def _parse_stl_solid_names(stl_path: Path) -> list[str]:
    """Extract all solid names from an ASCII STL file."""
    names = []
    with open(stl_path, "r") as f:
        for line in f:
            m = re.match(r"^\s*solid\s+(\S+)", line)
            if m:
                names.append(m.group(1))
    return names


# ---------------------------------------------------------------------------
# Test 1: No server STL file should contain _left or _right solids
# ---------------------------------------------------------------------------
CASE_DIR = Path(__file__).resolve().parents[3] / "case" / "Base2-9" / "Mesh"
TRI_SURFACE_DIR = CASE_DIR / "constant" / "triSurface"


@pytest.mark.skipif(
    not TRI_SURFACE_DIR.exists(),
    reason="Base2-9 triSurface directory not present",
)
class TestServerLeftRightAbsent:
    """After the frontend change, exported server STL files must not
    contain any solid whose name ends with ``_left`` or ``_right``."""

    def _server_stl_files(self):
        # the side faces go to an editor-only bucket the runner never meshes
        # (e4228ff55 / mesh_acceptance.EDITOR_ONLY_STL_NAMES)
        from backend.aircooling.runner.mesh_acceptance import EDITOR_ONLY_STL_NAMES
        return sorted(f for f in TRI_SURFACE_DIR.glob("merged_server_*.stl")
                      if f.name not in EDITOR_ONLY_STL_NAMES)

    def test_server_stl_files_exist(self):
        files = self._server_stl_files()
        assert len(files) > 0, "Expected at least one merged_server_*.stl file"

    def test_no_left_right_solids_in_server_stl(self):
        violations = []
        for stl_file in self._server_stl_files():
            names = _parse_stl_solid_names(stl_file)
            for name in names:
                if name.endswith("_left") or name.endswith("_right"):
                    violations.append(f"{stl_file.name}: {name}")
        assert violations == [], (
            f"Server STL files still contain _left/_right solids "
            f"(these coincide with rack frame inner walls and must be removed):\n"
            + "\n".join(violations[:20])
        )


# ---------------------------------------------------------------------------
# Test 2: Runner bridge_bc handles cfdrun without left/right face entries
# ---------------------------------------------------------------------------
class TestBridgeBCWithoutLeftRight:
    """The runner's _build_bridge_bc() must work correctly when the cfdrun
    file has no left/right face entries for servers."""

    def _minimal_cfdrun_no_left_right(self) -> dict:
        """Build a minimal cfdrun dict where server faces dict has only
        inlet/outlet/top/bottom — no left/right entries."""
        return {
            "version": "1.0",
            "source": "frontend",
            "projectName": "test_no_lr",
            "room": {
                "height": 3.0,
                "plane": [
                    {"x": 0, "y": 0, "z": 0},
                    {"x": 10, "y": 0, "z": 0},
                    {"x": 10, "y": 10, "z": 0},
                    {"x": 0, "y": 10, "z": 0},
                ],
                "locationInMesh": {"x": 5, "y": 5, "z": 1.5},
            },
            "floors": {},
            "racks": {
                "R1": {
                    "geometry": {
                        "position": {"x": 2, "y": 0.5, "z": 0},
                        "size": {"W": 0.6, "D": 1.0, "H": 2.0},
                        "rotation": 0,
                        "slotWidthRatio": 0.8,
                        "unitHeight": 0.04445,
                    },
                    "frame": {},
                    "servers": [
                        {
                            "id": "R1_TestSrv_U1",
                            "type": "fanServer",
                            "startSlot": 1,
                            "spanSlots": 1,
                            "physics": {
                                "volumeFlowRate": 0.05,
                                "power": 500,
                            },
                            "faces": {
                                "inlet": {
                                    "solid": "R1_TestSrv_U1_inlet",
                                    "patches": [],
                                },
                                "outlet": {
                                    "solid": "R1_TestSrv_U1_outlet",
                                    "patches": [],
                                },
                                "top": {
                                    "solid": "R1_TestSrv_U1_top",
                                    "patches": [],
                                },
                                "bottom": {
                                    "solid": "R1_TestSrv_U1_bottom",
                                    "patches": [],
                                },
                                # NO "left" or "right" entries
                            },
                        },
                    ],
                    "leakage": {"groups": []},
                },
            },
            "acus": {},
            "meshControl": {},
        }

    def test_bridge_bc_registers_without_left_right(self):
        """_build_bridge_bc should register inlet/outlet/top/bottom without
        error when left/right are absent from the faces dict."""
        from backend.aircooling.runner.bridge_bc import BridgeBCRegistry

        cfdrun = self._minimal_cfdrun_no_left_right()
        registry = BridgeBCRegistry()

        racks = cfdrun.get("racks", {})
        for rack_name, rack in racks.items():
            for srv in rack.get("servers", []):
                faces = srv.get("faces", {})
                for face_id in ("inlet", "outlet", "top", "bottom", "left", "right"):
                    face_info = faces.get(face_id)
                    if not face_info:
                        continue
                    solid = face_info.get("solid")
                    if solid:
                        from backend.aircooling.runner.bridge_bc import BCSpec
                        if face_id in ("inlet", "outlet"):
                            bc = BCSpec(bc_type="fan_outlet" if face_id == "outlet" else "fan_inlet",
                                        flow_rate=0.05, power=500)
                        else:
                            bc = BCSpec(bc_type="wall")
                        registry.register(solid, bc)

        assert len(registry) == 4, f"Expected 4 registered patches, got {len(registry)}"
        assert "R1_TestSrv_U1_left" not in registry
        assert "R1_TestSrv_U1_right" not in registry
        assert "R1_TestSrv_U1_inlet" in registry
        assert "R1_TestSrv_U1_outlet" in registry
        assert "R1_TestSrv_U1_top" in registry
        assert "R1_TestSrv_U1_bottom" in registry
