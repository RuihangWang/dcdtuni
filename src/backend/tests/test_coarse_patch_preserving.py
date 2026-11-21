"""Tests for coarsePatchPreserving preset and boundary patch preservation audit."""

import re
from pathlib import Path

from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig, MeshConfig
from backend.aircooling.runner.derived_config import derive_mesh_config, derive_run_config, shadow_drift
from backend.aircooling.runner.patch_audit import verify_patch_preservation
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def test_coarse_patch_preserving_schema_and_derivation():
    manifest = MeshConfig(meshEngine="dcCart", meshSizePreset="coarsePatchPreserving", parallelMesh=True, meshProcess=8)
    derived = derive_mesh_config(manifest.model_dump(exclude_none=True), {}, block_mesh_bounds=None)
    assert derived["meshSizePreset"] == "coarsePatchPreserving"
    assert derived["baseSize"] == 0.4
    assert derived["rackRegionLevel"] == 2
    assert derived["slotAlignedBackground"] is True
    assert derived["rackRegionMode"] == "servers"
    assert derived["rackRegionDepth"] == "slots"
    assert CFDExportMeshConfig.model_validate(derived).meshSizePreset == "coarsePatchPreserving"

    # When unset, derived output carries no meshSizePreset so shadow drift stays zero.
    default_derived = derive_mesh_config(MeshConfig(meshEngine="dcCart").model_dump(exclude_none=True), {}, block_mesh_bounds=None)
    assert "meshSizePreset" not in default_derived
    assert default_derived["baseSize"] == 0.25
    mg = derive_run_config({"meshConfig": {}, "simConfig": {}})
    assert shadow_drift({"meshConfig": {}, "simConfig": {}, "meshGeometry": mg}) == []


def test_coarse_patch_preserving_per_rack_and_per_bucket_levels(tmp_path):
    (tmp_path / "p.cfdrun").write_text("{}", encoding="utf-8")
    runner = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "p.cfdrun")
    runner._stl_solids = {
        "room_wall.stl": ["Room_Wall_1"],
        "merged_server_1U.stl": ["Rack1_srv1_inlet", "Rack2_srv1_inlet"],
        "merged_server_4U.stl": ["Rack2_srv2_inlet"],
        "merged_wallBlankingPanel_2U.stl": ["Rack1_blank2U"],
        "merged_wallBlankingPanel_4U.stl": ["Rack2_blank4U"],
    }
    runner._data = {
        "meshConfig": {
            "baseSize": 0.3556,
            "rackRegionLevel": 2,
            "baffleRefinementLevel": [1, 1],
            "meshMode": "cartesianLocalized",
            "meshEngine": "dcCart",
            "meshSizePreset": "coarsePatchPreserving",
            "rackRegionMode": "servers",
            "rackRegionDepth": "slots",
            "maxGlobalCells": 5000000,
        },
        "racks": {
            "Rack1": {
                "geometry": {
                    "size": {"W": 0.6, "H": 2.0, "D": 1.0},
                    "anchor": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "quaternion": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                    "unitHeight": 0.04445,
                },
                "servers": [{"name": "srv1", "type": "fanServer", "startSlot": 5, "spanSlots": 1, "outletOffsetM": 0.7}],
            },
            "Rack2": {
                "geometry": {
                    "size": {"W": 0.6, "H": 2.0, "D": 1.0},
                    "anchor": {"x": 2.0, "y": 0.0, "z": 0.0},
                    "quaternion": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                    "unitHeight": 0.04445,
                },
                "servers": [{"name": "srv2", "type": "fanServer", "startSlot": 5, "spanSlots": 4, "outletOffsetM": 0.7}],
            },
        },
        "serverInletSolids": ["Rack1_srv1_inlet", "Rack2_srv1_inlet", "Rack2_srv2_inlet"],
        "serverOutletSolids": [],
        "acuSupplySolids": [],
        "acuReturnSolids": [],
    }
    runner._generate_snappy_hex_mesh_dict(tmp_path)
    text = (tmp_path / "snappyHexMeshDict").read_text(encoding="utf-8")
    # Global Rack1 (1U -> level 3) must NOT force Rack2 (4U -> stays at requested level 2).
    assert re.search(r"rackRegion_Rack1_srv0\s*\{[^}]*levels \(\(1e15 3\)\)", text)
    assert re.search(r"rackRegion_Rack2_srv0\s*\{[^}]*levels \(\(1e15 2\)\)", text)
    # Blanking 2U needs level 2 (0.3556 / 4 = 0.0889 <= 0.0889); 4U needs level 1 (0.3556 / 2 = 0.1778).
    assert re.search(r"merged_wallBlankingPanel_2U\s*\{\s*level \(2 2\);", text)
    assert re.search(r"merged_wallBlankingPanel_4U\s*\{\s*level \(1 1\);", text)


def _write_boundary(path: Path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        f"    {name}\n    {{\n        type            {ptype};\n        nFaces          {nf};\n        startFace       {sf};\n    }}"
        for name, ptype, nf, sf in entries
    )
    path.write_text(
        f"FoamFile {{ version 2.0; format ascii; class polyBoundaryMesh; object boundary; }}\n{len(entries)}\n(\n{body}\n)\n",
        encoding="utf-8",
    )


def test_verify_patch_preservation(tmp_path):
    case = tmp_path / "case"
    _write_boundary(case / "constant/polyMesh/boundary", [
        ("Room_Wall", "wall", 10, 100),
        ("ACU_1_Front", "patch", 4, 110),
    ])
    _write_boundary(case / "processor0/constant/polyMesh/boundary", [
        ("Room_Wall", "wall", 6, 50),
        ("ACU_1_Front", "patch", 4, 56),
        ("procBoundary0to1", "processor", 8, 60),
    ])
    _write_boundary(case / "processor1/constant/polyMesh/boundary", [
        ("Room_Wall", "wall", 4, 40),
        ("ACU_1_Front", "patch", 0, 44),
        ("procBoundary1to0", "processor", 8, 44),
    ])
    ok = verify_patch_preservation(case, ["Room_Wall", "ACU_1_Front"], run_acceptance=False)
    assert ok["ok"] is True
    assert ok["dropped_patches"] == []

    bad = verify_patch_preservation(case, ["Room_Wall", "ACU_1_Front", "Dropped_Rack"], run_acceptance=False)
    assert bad["ok"] is False
    assert bad["dropped_patches"] == ["Dropped_Rack"]
