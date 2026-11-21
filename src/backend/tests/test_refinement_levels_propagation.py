"""Verify every meshConfig refinement/feature level propagates to snappyHexMeshDict.

Uses Base_leakage with deliberately distinct levels per category so that
a wrong mapping (e.g. floor using baffleRefinementLevel) is immediately
visible as a wrong number in the output.
"""
import copy
import json
import re

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._base_leakage_case import CFDRUN, make_case

# Distinct levels per category — no two categories share the same values.
# This makes mis-routing immediately detectable.
DISTINCT_LEVELS = {
    "refinementLevel": [1, 1],            # room_wall default
    "rackRefinementLevel": [2, 3],        # rack frame
    "serverFaceRefinementLevel": [1, 2],  # server faces
    "blankingPlateRefinementLevel": [1, 1],
    "acuRefinementLevel": [2, 2],         # ACU
    "boxRefinementLevel": [1, 1],
    "partitionWallRefinementLevel": [1, 1],  # will be auto-bumped
    "baffleRefinementLevel": [3, 3],      # wall baffles — DISTINCT
    "floorRefinementLevel": [2, 4],       # floor — DISTINCT from baffle
    "rackDoorRefinementLevel": [3, 4],    # rack doors — DISTINCT
    "rackThroughZoneRefinementLevel": [1, 2],
    "rackPartitionRefinementLevel": [1, 1],
    "leakageRefinementLevel": [2, 3],
    # Feature levels
    "defaultFeatureLevel": 2,
    "rackFeatureLevel": 3,
    "serverFaceFeatureLevel": 2,
    "blankingPlateFeatureLevel": 2,
    "acuFeatureLevel": 4,                 # DISTINCT
    "boxFeatureLevel": 2,
    "partitionWallFeatureLevel": 5,       # DISTINCT
    "baffleFeatureLevel": 6,              # DISTINCT
    "floorFeatureLevel": 7,               # DISTINCT from baffle
    "rackThroughZoneFeatureLevel": 2,
    "rackPartitionFeatureLevel": 2,
    "leakageFeatureLevel": 2,
}


@pytest.fixture
def snappy_text(tmp_path):
    """Generate snappyHexMeshDict with distinct levels and return its text."""
    with open(CFDRUN) as f:
        original = json.load(f)

    data = copy.deepcopy(original)
    # V2 manifest: the runner reads meshConfig from the meshGeometry blob; the
    # strict root meshConfig (raw UI config) rejects these per-category keys.
    mc = data["meshGeometry"].setdefault("meshConfig", {})
    mc.update(DISTINCT_LEVELS)
    # These are snappyHexMeshDict level contracts; an omitted engine is the
    # dcCart default, which drops the rack-frame feature refinement.
    mc["meshEngine"] = "snappy"

    case_dir, cfdrun = make_case(tmp_path, data)
    r = STLGenRunner(case_dir, cfdrun)
    r._load()
    r.validate_geometry()
    r._preflight_partition_wall_thickness()
    r._ensure_watertight_room_enclosure()
    r._prepare_runtime_metadata()
    r._generate_mesh_dicts()

    return (case_dir / "Mesh" / "system" / "snappyHexMeshDict").read_text()


def _extract_surface_block(text, surface_name):
    """Extract the refinementSurfaces block for a given surface name (brace-balanced)."""
    pattern = rf'^\s+{re.escape(surface_name)}\s*\{{'
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


def _extract_region_level(text, surface_name, region_name):
    """Extract level (min max) for a region inside a surface block."""
    block = _extract_surface_block(text, surface_name)
    if not block:
        return None
    idx = block.find(region_name)
    if idx < 0:
        return None
    after = block[idx:]
    m = re.search(r'level\s+\((\d+)\s+(\d+)\)', after[:300])
    return [int(m.group(1)), int(m.group(2))] if m else None


def _extract_surface_level(text, surface_name):
    """Extract the surface-level 'level (min max)' for a surface."""
    block = _extract_surface_block(text, surface_name)
    if not block:
        return None
    m = re.search(r'\n\s+level\s+\((\d+)\s+(\d+)\)', block)
    return [int(m.group(1)), int(m.group(2))] if m else None


def _extract_feature_level(text, emesh_name):
    """Extract the feature edge level for an eMesh file."""
    pattern = rf'file\s+"{re.escape(emesh_name)}";\s*\n\s*level\s+(\d+);'
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


# ── refinementSurfaces: surface-level or region-level ──


class TestRackFrameLevel:
    def test_rack_frame_region_uses_fixed_baseline(self, snappy_text):
        lvl = _extract_region_level(snappy_text, "merged_rack_frame", "Default_Rack_1_frame")
        assert lvl == [3, 3], f"rack frame region should use rackRegionLevel [3,3], got {lvl}"

    def test_rack_frame_feature_uses_rack_region_level(self, snappy_text):
        fl = _extract_feature_level(snappy_text, "merged_rack_frame.eMesh")
        assert fl == 3, f"rack frame feature should be rackRegionLevel=3, got {fl}"


class TestACULevel:
    def test_acu_region_uses_acuRefinementLevel(self, snappy_text):
        lvl = _extract_region_level(snappy_text, "merged_acu", "ACU_1_Front")
        assert lvl == [2, 2], f"ACU region should use acuRefinementLevel [2,2], got {lvl}"

    def test_acu_feature_uses_acuFeatureLevel(self, snappy_text):
        fl = _extract_feature_level(snappy_text, "merged_acu.eMesh")
        assert fl == 4, f"ACU feature should be acuFeatureLevel=4, got {fl}"


class TestFloorCeilingLevel:
    def test_floor_region_uses_floorRefinementLevel(self, snappy_text):
        lvl = _extract_region_level(snappy_text, "merged_floor_ceiling", "Room_Floor")
        assert lvl == [2, 4], f"Room_Floor should use floorRefinementLevel [2,4], got {lvl}"

    def test_ceiling_region_uses_floorRefinementLevel(self, snappy_text):
        lvl = _extract_region_level(snappy_text, "merged_floor_ceiling", "Room_Ceiling")
        assert lvl == [2, 4], f"Room_Ceiling should use floorRefinementLevel [2,4], got {lvl}"

    def test_floor_feature_uses_floorFeatureLevel(self, snappy_text):
        fl = _extract_feature_level(snappy_text, "merged_floor_ceiling.eMesh")
        assert fl == 7, f"floor feature should be floorFeatureLevel=7, got {fl}"


class TestWallBaffleLevel:
    def test_baffle_model_uses_baffleRefinementLevel(self, snappy_text):
        lvl = _extract_region_level(snappy_text, "merged_wall_baffle", "Baffle_1")
        assert lvl == [3, 3], f"Baffle_1 should use baffleRefinementLevel [3,3], got {lvl}"

    def test_floor_wallbaffle_remaining_uses_floorRefinementLevel(self, snappy_text):
        """Floor wallBaffle remaining solids use floorRefinementLevel.

        Base_leakage's raised floor leaves Raised_Floor_copy_part1 (a floor
        wallBaffle remaining solid). Since a272a06a it is its own
        wallBaffle_floor_<name>.stl surface with its own faceZone, no longer a
        region of merged_wall_baffle, and it uses rl_floor (floored to level 2).
        """
        lvl = _extract_surface_level(
            snappy_text, "wallBaffle_floor_Raised_Floor_copy_part1")
        assert lvl == [2, 4], \
            f"floor wallBaffle remaining should use floorRefinementLevel [2,4], got {lvl}"

    def test_wall_baffle_feature_uses_baffleFeatureLevel(self, snappy_text):
        fl = _extract_feature_level(snappy_text, "merged_wall_baffle.eMesh")
        assert fl == 6, f"wall baffle feature should be baffleFeatureLevel=6, got {fl}"


class TestRackDoorLevel:
    def test_merged_rack_door_uses_rack_region_level(self, snappy_text):
        """merged_rack_door uses rl_rack_internal = [rackRegionLevel, rackRegionLevel]."""
        lvl = _extract_surface_level(snappy_text, "merged_rack_door")
        if lvl is None:
            pytest.skip("merged_rack_door not present in Base_leakage case")
        assert lvl == [3, 3], \
            f"merged_rack_door should use rl_rack_internal [3,3], got {lvl}"


class TestPerforatedFloorLevel:
    def test_perforated_floor_uses_floorRefinementLevel(self, snappy_text):
        """Perforated floor in Base_leakage is Raised_Floor_copy_patch."""
        lvl = _extract_surface_level(
            snappy_text, "perforated_floor_Raised_Floor_copy_patch")
        assert lvl == [2, 4], \
            f"perforated_floor should use floorRefinementLevel [2,4], got {lvl}"

    def test_perforated_floor_feature_uses_floorFeatureLevel(self, snappy_text):
        fl = _extract_feature_level(
            snappy_text, "perforated_floor_Raised_Floor_copy_patch.eMesh")
        assert fl == 7, f"perforated_floor feature should be floorFeatureLevel=7, got {fl}"


class TestPartitionWallLevel:
    """Partition wall tests.

    Base_leakage has no partition walls, so these tests skip when the
    expected surfaces are absent.  They exist to guard the invariant if
    the test case is later extended with partitions.
    """

    def test_partition_wall_body_uses_exact_user_level(self, snappy_text):
        """Runner must NOT auto-bump — user controls the level via frontend auto-detect."""
        lvl = _extract_region_level(
            snappy_text, "merged_partition_wall", "Partition_Wall_1_body")
        if lvl is None:
            pytest.skip("merged_partition_wall not present in Base_leakage case")
        assert lvl == [1, 1], \
            f"Partition_Wall_1_body should use user-set partitionWallRefinementLevel " \
            f"[1,1] without auto-bump, got {lvl}"

    def test_partition_hole_uses_exact_user_level(self, snappy_text):
        lvl = _extract_surface_level(
            snappy_text, "partition_hole_Partition_Wall_1_hole_1_zone")
        if lvl is None:
            pytest.skip("partition_hole not present in Base_leakage case")
        assert lvl == [1, 1], \
            f"partition_hole should use user-set partitionWallRefinementLevel " \
            f"[1,1] without auto-bump, got {lvl}"

    def test_partition_wall_feature_uses_partitionWallFeatureLevel(self, snappy_text):
        fl = _extract_feature_level(snappy_text, "merged_partition_wall.eMesh")
        if fl is None:
            pytest.skip("merged_partition_wall not present in Base_leakage case")
        assert fl == 5, f"partition wall feature should be partitionWallFeatureLevel=5, got {fl}"

    def test_partition_hole_feature_uses_partitionWallFeatureLevel(self, snappy_text):
        fl = _extract_feature_level(
            snappy_text, "partition_hole_Partition_Wall_1_hole_1_zone.eMesh")
        if fl is None:
            pytest.skip("partition_hole not present in Base_leakage case")
        assert fl == 5, f"partition hole feature should be partitionWallFeatureLevel=5, got {fl}"


class TestRoomWallLevel:
    def test_room_wall_uses_defaultRefinementLevel(self, snappy_text):
        lvl = _extract_region_level(snappy_text, "room_wall", "Room_Wall_1")
        assert lvl == [1, 1], f"room wall should use refinementLevel [1,1], got {lvl}"

    def test_room_wall_feature_uses_defaultFeatureLevel(self, snappy_text):
        fl = _extract_feature_level(snappy_text, "room_wall.eMesh")
        assert fl == 2, f"room wall feature should be defaultFeatureLevel=2, got {fl}"


class TestRackRegionLevel:
    def test_rack_region_in_refinement_regions(self, snappy_text):
        """Each rack should have a rackRegion_* entry in refinementRegions."""
        assert "rackRegion_" in snappy_text
        assert "mode inside" in snappy_text


class TestServerFaceLevel:
    def test_server_inlet_uses_rack_region_level(self, snappy_text):
        """Rack-internal server faces use rl_rack_internal = [rackRegionLevel, rackRegionLevel]."""
        lvl = _extract_region_level(
            snappy_text, "merged_server_14U",
            "Default_Rack_1_fan_server_1_inlet_patch")
        assert lvl == [3, 3], \
            f"14U server inlet should use rl_rack_internal [3,3], got {lvl}"

    def test_server_feature_skipped(self, snappy_text):
        """Server feature edges should be skipped in rack region mode."""
        fl = _extract_feature_level(snappy_text, "merged_server_14U.eMesh")
        assert fl is None, "server feature edges should be skipped in rack region mode"


class TestBlankingPlateLevel:
    def test_blanking_uses_rack_region_level(self, snappy_text):
        """Blanking plates are rack-internal and use rl_rack_internal = [rackRegionLevel, rackRegionLevel].

        Base_leakage may not have blanking STLs — skip if absent.
        """
        lvl = _extract_region_level(
            snappy_text, "merged_blanking_3U",
            "Default_Rack_1_inlet_u1_u3")
        if lvl is None:
            pytest.skip("merged_blanking_3U not present in Base_leakage case")
        assert lvl == [3, 3], \
            f"blanking should use rl_rack_internal [3,3], got {lvl}"
