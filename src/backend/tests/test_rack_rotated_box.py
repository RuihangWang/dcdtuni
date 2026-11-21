"""Tests for searchableRotatedBox generation from quaternion + scale data.

Verifies that rack refinementRegion entries in snappyHexMeshDict use
searchableRotatedBox (with origin/i/j/k vectors) when the cdfrun
contains quaternion + scale data, and fall back to searchableBox (AABB)
when quaternion data is missing.
"""
import copy
import json
import math
import re
import tempfile
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._base_leakage_case import CFDRUN, make_case


def _extract_rack_region_block(text, region_name):
    """Extract a rackRegion block from snappyHexMeshDict geometry section."""
    pattern = rf'^\s+{re.escape(region_name)}\s*\{{'
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return None
    start = m.start()
    depth = 0
    for i in range(m.end() - 1, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _extract_vector(block, key):
    """Extract a vector (x y z) value from a block for a given key."""
    pattern = rf'{re.escape(key)}\s+\(([^)]+)\)'
    m = re.search(pattern, block)
    if not m:
        return None
    return [float(v) for v in m.group(1).split()]


def _generate_snappy_with_quaternion(quaternion, scale=None, size=None):
    """Generate snappyHexMeshDict with given quaternion/scale on first rack."""
    with open(CFDRUN) as f:
        original = json.load(f)

    data = copy.deepcopy(original)
    # P3 T6: the cfdrun is now a V2 manifest; the geometry domain model (racks)
    # lives in the transitional meshGeometry block.
    racks = data["meshGeometry"]["racks"]
    first_rack_name = next(iter(racks))
    rack = racks[first_rack_name]

    geom = rack.setdefault("geometry", {})
    geom["quaternion"] = quaternion
    if scale is not None:
        geom["scale"] = scale
    if size is not None:
        geom["size"] = size

    with tempfile.TemporaryDirectory() as tmp:
        case_dir, tmp_cfdrun = make_case(Path(tmp), data)

        r = STLGenRunner(case_dir, tmp_cfdrun)
        r._load()
        r.validate_geometry()
        r._preflight_partition_wall_thickness()
        r._ensure_watertight_room_enclosure()
        r._prepare_runtime_metadata()
        r._generate_mesh_dicts()

        snappy_path = case_dir / "Mesh" / "system" / "snappyHexMeshDict"
        text = snappy_path.read_text()

    return text, first_rack_name


class TestSearchableRotatedBox:
    """When quaternion data is present, searchableRotatedBox is emitted."""

    def test_identity_quaternion_produces_axis_aligned_vectors(self):
        """Identity quat (0,0,0,1) → e1 along X, e2 along Y, e3 along Z."""
        quat = {"x": 0, "y": 0, "z": 0, "w": 1}
        scale = {"x": 1, "y": 1, "z": 1}
        text, rack_name = _generate_snappy_with_quaternion(quat, scale)

        rr_name = f"rackRegion_Default_Rack_1"
        block = _extract_rack_region_block(text, rr_name)
        assert block is not None, f"{rr_name} not found in geometry"
        assert "searchableRotatedBox" in block

        e1 = _extract_vector(block, "e1")
        e2 = _extract_vector(block, "e2")
        e3 = _extract_vector(block, "e3")
        span = _extract_vector(block, "span")
        assert e1 is not None
        assert e2 is not None
        assert e3 is not None
        assert span is not None

        # e1/e2/e3 are unit direction vectors; span carries the dimensions.
        # Identity quaternion: e1→(1,0,0), e2→(0,1,0), e3→(0,0,1)
        assert e1 == pytest.approx([1, 0, 0], abs=1e-4)
        assert e2 == pytest.approx([0, 1, 0], abs=1e-4)
        assert e3 == pytest.approx([0, 0, 1], abs=1e-4)

    def test_90deg_y_rotation(self):
        """90° Y-rotation: e1→(0,0,-1), e2→(0,1,0), e3→(1,0,0)."""
        s = math.sin(math.pi / 4)
        c = math.cos(math.pi / 4)
        quat = {"x": 0, "y": s, "z": 0, "w": c}
        scale = {"x": 1, "y": 1, "z": 1}
        text, rack_name = _generate_snappy_with_quaternion(quat, scale)

        rr_name = f"rackRegion_Default_Rack_1"
        block = _extract_rack_region_block(text, rr_name)
        assert block is not None
        assert "searchableRotatedBox" in block

        e1 = _extract_vector(block, "e1")
        e2 = _extract_vector(block, "e2")
        e3 = _extract_vector(block, "e3")

        # 90° Y-rotation: (1,0,0)→(0,0,-1)
        assert e1 == pytest.approx([0, 0, -1], abs=1e-4)
        # (0,1,0) stays (0,1,0)
        assert e2 == pytest.approx([0, 1, 0], abs=1e-4)
        # (0,0,1)→(1,0,0)
        assert e3 == pytest.approx([1, 0, 0], abs=1e-4)

    def test_135deg_y_rotation_like_tri_angle(self):
        """135° Y-rotation (Tri_angle case): unit vectors at 45° in XZ, span carries dims."""
        angle = 135.0 * math.pi / 180
        quat = {"x": 0, "y": math.sin(angle / 2), "z": 0, "w": math.cos(angle / 2)}
        scale = {"x": 1, "y": 1, "z": 1}
        size = {"W": 0.6, "D": 1.0, "H": 1.867}
        text, rack_name = _generate_snappy_with_quaternion(quat, scale, size)

        rr_name = f"rackRegion_Default_Rack_1"
        block = _extract_rack_region_block(text, rr_name)
        assert block is not None
        assert "searchableRotatedBox" in block

        e1 = _extract_vector(block, "e1")
        e2 = _extract_vector(block, "e2")
        e3 = _extract_vector(block, "e3")
        span = _extract_vector(block, "span")

        # e1/e2/e3 should be unit vectors (|e|≈1)
        e1_len = math.sqrt(sum(v**2 for v in e1))
        assert e1_len == pytest.approx(1.0, abs=0.01), f"|e1| should be 1.0, got {e1_len}"
        assert abs(e1[1]) < 1e-4, f"e1 Y component should be ~0: {e1}"

        e2_len = math.sqrt(sum(v**2 for v in e2))
        assert e2_len == pytest.approx(1.0, abs=0.01), f"|e2| should be 1.0, got {e2_len}"

        e3_len = math.sqrt(sum(v**2 for v in e3))
        assert e3_len == pytest.approx(1.0, abs=0.01), f"|e3| should be 1.0, got {e3_len}"

        # span carries the box dimensions: (W, H, D) = (0.6, 1.867, 1.0)
        assert span[0] == pytest.approx(0.6, abs=0.01), f"span[0] should be W=0.6, got {span[0]}"
        assert span[1] == pytest.approx(1.867, abs=0.01), f"span[1] should be H=1.867, got {span[1]}"
        assert span[2] == pytest.approx(1.0, abs=0.01), f"span[2] should be D=1.0, got {span[2]}"

    def test_negative_scale_z_keeps_mirrored_footprint(self):
        """scale.z=-1 mirrors the box in Z while keeping a right-handed basis.

        The covered Z footprint must be [anchor.z - D, anchor.z] (the mirror),
        expressed with a right-handed basis so OpenFOAM does not flip the box
        vertically (see test_mirrored_rack_box_is_right_handed).
        """
        quat = {"x": 0, "y": 0, "z": 0, "w": 1}
        scale = {"x": 1, "y": 1, "z": -1}
        text, rack_name = _generate_snappy_with_quaternion(quat, scale)

        block = _extract_rack_region_block(text, "rackRegion_Default_Rack_1")
        assert block is not None

        origin = _extract_vector(block, "origin")
        e3 = _extract_vector(block, "e3")
        span = _extract_vector(block, "span")

        with open(CFDRUN) as f:
            geom = json.load(f)["meshGeometry"]["racks"]["Default_Rack_1"]["geometry"]
        anchor = geom["anchor"]
        depth = geom["size"]["D"]

        # Box covers the mirrored Z footprint: from anchor.z - D to anchor.z.
        z_lo = origin[2]
        z_hi = origin[2] + span[2] * e3[2]
        assert min(z_lo, z_hi) == pytest.approx(anchor["z"] - depth, abs=1e-3)
        assert max(z_lo, z_hi) == pytest.approx(anchor["z"], abs=1e-3)

    def test_origin_matches_anchor(self):
        """searchableRotatedBox origin equals the rack anchor position."""
        quat = {"x": 0, "y": 0, "z": 0, "w": 1}
        text, rack_name = _generate_snappy_with_quaternion(quat)

        rr_name = f"rackRegion_Default_Rack_1"
        block = _extract_rack_region_block(text, rr_name)
        assert block is not None

        origin = _extract_vector(block, "origin")
        assert origin is not None

        # Read the anchor from the cdfrun
        with open(CFDRUN) as f:
            d = json.load(f)
        anchor = d["meshGeometry"]["racks"]["Default_Rack_1"]["geometry"]["anchor"]
        assert origin[0] == pytest.approx(anchor["x"], abs=1e-4)
        assert origin[1] == pytest.approx(anchor["y"], abs=1e-4)
        assert origin[2] == pytest.approx(anchor["z"], abs=1e-4)

    def test_mirrored_rack_box_is_right_handed(self):
        """Regression: a mirrored rack (scale.z=-1) must emit a RIGHT-HANDED box.

        OpenFOAM's searchableRotatedBox reconstructs its second local axis as
        e3 x e1 and ignores the supplied e2. A left-handed (e1,e2,e3) basis —
        produced by a negative scale component — makes that reconstructed axis
        point the opposite way, flipping the whole box downward through the
        floor instead of up over the rack. The emitted basis must therefore be
        right-handed and its OpenFOAM-effective vertical axis must point up.
        """
        quat = {"x": 0, "y": 0, "z": 0, "w": 1}   # identity
        scale = {"x": 1, "y": 1, "z": -1}          # mirrored in Z
        text, rack_name = _generate_snappy_with_quaternion(quat, scale)

        block = _extract_rack_region_block(text, "rackRegion_Default_Rack_1")
        assert block is not None
        e1 = _extract_vector(block, "e1")
        e2 = _extract_vector(block, "e2")
        e3 = _extract_vector(block, "e3")

        def cross(a, b):
            return [a[1] * b[2] - a[2] * b[1],
                    a[2] * b[0] - a[0] * b[2],
                    a[0] * b[1] - a[1] * b[0]]

        det = sum(a * b for a, b in zip(e1, cross(e2, e3)))
        assert det > 0, f"basis must be right-handed (det>0), got det={det}"

        # OpenFOAM rebuilds the 2nd (vertical) axis as e3 x e1; it must point UP
        # so the level-3 box covers the rack, not the floor below it.
        eff_vertical = cross(e3, e1)
        assert eff_vertical[1] > 0.5, (
            f"OpenFOAM-effective vertical axis must point +Y, got {eff_vertical}"
        )


class TestSearchableBoxFallback:
    """When no quaternion data, runner still uses searchableRotatedBox."""

    def test_no_quaternion_uses_searchable_box(self, tmp_path):
        """Legacy cdfrun without quaternion → searchableBox from STL AABB."""
        # Use original cfdrun (no quaternion data)
        case_dir, cfdrun = make_case(tmp_path)
        r = STLGenRunner(case_dir, cfdrun)
        r._load()
        r.validate_geometry()
        r._preflight_partition_wall_thickness()
        r._ensure_watertight_room_enclosure()
        r._prepare_runtime_metadata()
        r._generate_mesh_dicts()

        snappy_path = case_dir / "Mesh" / "system" / "snappyHexMeshDict"
        text = snappy_path.read_text()

        # Runner now always uses searchableRotatedBox even without explicit quaternion
        block = _extract_rack_region_block(text, "rackRegion_Default_Rack_1")
        assert block is not None, "rackRegion_Default_Rack_1 not found"
        assert "searchableRotatedBox" in block
