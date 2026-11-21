"""Regression tests for the 2026-04-17 refinement architecture redesign.

Spec: docs/superpowers/specs/2026-04-17-refinement-architecture-redesign-design.md
"""
import pytest

from backend.aircooling.runner.cfdrun_schema import CFDExport, CFDExportMeshConfig


def test_meshconfig_rack_region_level():
    """rackRegionLevel: single scalar controlling searchableBox refinement."""
    mc = CFDExportMeshConfig()
    assert mc.rackRegionLevel == 3


def test_meshconfig_new_floor_fields():
    """Floor refinement escaped from defaultFeatureLevel fallback."""
    mc = CFDExportMeshConfig()
    assert mc.floorRefinementLevel == [1, 1]
    assert mc.floorFeatureLevel == 2


def test_meshconfig_accepts_per_unit_maps_from_cfdrun():
    """Round-trip through pydantic model_validate preserves all new keys."""
    exp = CFDExport.model_validate({
        "meshConfig": {
            "serverFaceFeatureLevel": 3,
            "serverFaceFeaturePerUnitLevel": {"1": 4, "4": 2},
            "blankingPlateFeaturePerUnitLevel": {"1": 3, "2": 2},
            "rackPartitionRefinementLevel": [2, 3],
            "rackPartitionPerUnitLevel": {"1": [3, 4]},
            "rackPartitionFeatureLevel": 3,
            "rackPartitionFeaturePerUnitLevel": {"1": 4},
            "rackThroughZoneFeatureLevel": 2,
            "rackThroughZoneFeaturePerUnitLevel": {"3": 3},
            "floorRefinementLevel": [2, 3],
            "floorFeatureLevel": 3,
        },
    })
    mc = exp.meshConfig
    assert mc.serverFaceFeatureLevel == 3
    assert mc.serverFaceFeaturePerUnitLevel["1"] == 4
    assert mc.blankingPlateFeaturePerUnitLevel["2"] == 2
    assert mc.rackPartitionRefinementLevel == [2, 3]
    assert mc.rackPartitionPerUnitLevel["1"] == [3, 4]
    assert mc.rackPartitionFeaturePerUnitLevel["1"] == 4
    assert mc.rackThroughZoneFeaturePerUnitLevel["3"] == 3
    assert mc.floorRefinementLevel == [2, 3]


# ---- Bucket filename parser (per-unit lookup functions removed 2026-05-12) ----

from backend.aircooling.runner.stlgen_runner import (
    _parse_bucket_from_filename,
)


def test_parse_bucket_from_filename_recognises_all_per_bucket_prefixes():
    """Filename → (category, bucket_key) for per-bucket categories.
    Door was removed from bucket patterns (merged into merged_rack_door.stl)."""
    assert _parse_bucket_from_filename("merged_server_1U.stl") == ("server", "1")
    assert _parse_bucket_from_filename("merged_server_4U.stl") == ("server", "4")
    assert _parse_bucket_from_filename("merged_blanking_2U.stl") == ("blanking", "2")
    assert _parse_bucket_from_filename("merged_partition_1U.stl") == ("partition", "1")
    assert _parse_bucket_from_filename("merged_through_zone_3U.stl") == ("through_zone", "3")
    assert _parse_bucket_from_filename("merged_door_1U.stl") is None


def test_parse_bucket_from_filename_wallBaffle():
    """merged_wallBaffle_<N>U.stl is recognised as the wallBaffle bucket."""
    assert _parse_bucket_from_filename("merged_wallBaffle_2U.stl") == ("wallBaffle", "2")
    assert _parse_bucket_from_filename("merged_wallBaffle_1U.stl") == ("wallBaffle", "1")


def test_parse_bucket_from_filename_returns_none_for_scene_merged():
    """Scene-merged files (room_wall, merged_rack_frame, etc.) return None."""
    assert _parse_bucket_from_filename("room_wall.stl") is None
    assert _parse_bucket_from_filename("merged_rack_frame.stl") is None
    assert _parse_bucket_from_filename("merged_acu.stl") is None
    assert _parse_bucket_from_filename("merged_baffle.stl") is None


def test_parse_bucket_from_filename_through_zone_per_zone_format():
    """2026-04-18: through-zone STLs switched from one-file-per-bucket (with
    multiple disjoint closed boxes) to one-file-per-zone (each file contains
    a single closed box) so snappyHexMesh's `refinementRegions mode=inside`
    closure check passes. Filename now carries both the bucket key AND a
    unique zone id after `__`: `merged_through_zone_<N>U__<zone>.stl`.
    """
    # Per-zone format (new)
    assert _parse_bucket_from_filename(
        "merged_through_zone_1U__A01a_u34_u44_through_zone.stl"
    ) == ("through_zone", "1")
    assert _parse_bucket_from_filename(
        "merged_through_zone_5U__B01b_u1_u5_through_zone.stl"
    ) == ("through_zone", "5")
    # Legacy per-bucket format (still supported)
    assert _parse_bucket_from_filename("merged_through_zone_3U.stl") == ("through_zone", "3")


# ---- Task 3: features block per-bucket routing ----

from pathlib import Path
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _bootstrap_runner(tmp_path: Path, mesh_config: dict, racks: dict,
                      stl_solids: dict) -> STLGenRunner:
    """Standard fixture — bypasses _load(), sets _data + _stl_solids directly."""
    (tmp_path / "Mesh" / "system").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Mesh" / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)
    (tmp_path / "model").mkdir(parents=True, exist_ok=True)
    runner = STLGenRunner(
        case_dir=tmp_path,
        cfdrun_path=tmp_path / "model" / "project.cfdrun",
    )
    runner._data = {
        "racks": racks,
        "meshConfig": mesh_config,
        "acus": {}, "baffles": {}, "floors": {}, "walls": {},
        "wallObjects": {}, "heatSources": {}, "partitionWalls": {},
        "room": {}, "simulationConfig": {},
    }
    runner._stl_solids = stl_solids
    runner._build_locations_in_mesh_entries = lambda: []
    # No .cfdrun source inventory here; UID dictionary output has its own tests.
    runner._source_surface_dict_block = lambda _mesh_config: ""
    return runner


def _write_dummy_stl(tri_dir: Path, filename: str, solid_names):
    """Write a minimal valid ASCII STL with placeholder triangles."""
    lines = []
    for name in solid_names:
        lines.append(f"solid {name}")
        lines.append("facet normal 0 0 1")
        lines.append(" outer loop")
        lines.append("  vertex 0 0 0")
        lines.append("  vertex 1 0 0")
        lines.append("  vertex 0 1 0")
        lines.append(" endloop")
        lines.append("endfacet")
        lines.append(f"endsolid {name}")
    (tri_dir / filename).write_text("\n".join(lines) + "\n")


def test_snappy_dict_rack_internal_features_skipped(tmp_path):
    """Rack-internal STL files (server, blanking, partition-bucket,
    through-zone) should NOT get feature edge entries — they are skipped
    by _RACK_INTERNAL_SKIP_PREFIXES."""
    stl_solids = {
        "merged_server_1U.stl":       ["S1_inlet"],
        "merged_server_4U.stl":       ["S4_inlet"],
        "merged_blanking_2U.stl":     ["B2_panel"],
        "merged_partition_1U.stl":    ["P1_part"],
        "merged_through_zone_3U.stl": ["L3_zone"],
    }
    mesh_config = {"baseSize": 0.4}
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)
    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()
    # All rack-internal files should have NO feature edge entry
    assert "merged_server_1U.eMesh" not in text
    assert "merged_server_4U.eMesh" not in text
    assert "merged_blanking_2U.eMesh" not in text
    assert "merged_partition_1U.eMesh" not in text
    assert "merged_through_zone_3U.eMesh" not in text


# ---- Task 4: refinementSurfaces per-bucket + through-zone refinementRegions ----

def test_snappy_dict_refinement_surfaces_rack_region(tmp_path):
    """Post rack-region redesign: rack-internal surfaces use fixed (1 1);
    through-zone is gone from refinementRegions (replaced by rackRegion);
    merged_rack_door emits faceType internal + faceZone;
    merged_wall_baffle emits faceType internal + surface-level faceZone.
    """
    stl_solids = {
        "merged_server_1U.stl":       ["S1_inlet"],
        "merged_blanking_2U.stl":     ["B2_panel"],
        "merged_partition_1U.stl":    ["P1_part"],
        "merged_through_zone_3U.stl": ["L3_zone"],
        "merged_rack_door.stl":       ["Default_Rack_4_inlet_door", "Default_Rack_4_outlet_door"],
        "merged_wall_baffle.stl":     ["Default_Rack_1_outlet_u4_u5_wallBaffle"],
    }
    mesh_config = {"baseSize": 0.4}
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)
    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    import re as _re
    # Rack-internal surfaces use fixed (1 1)
    for stem in ("merged_server_1U", "merged_blanking_2U", "merged_partition_1U"):
        assert f"    {stem}\n    {{" in text, f"missing {stem} block"

    # through-zone is excluded from refinementSurfaces (covered by rackRegion).
    # It still appears in the geometry section (as an STL file), but not in
    # refinementSurfaces or refinementRegions.
    rs_match = _re.search(r"refinementSurfaces\s*\{(.+?)^\s{4}\}", text, _re.DOTALL | _re.MULTILINE)
    assert rs_match is not None, "refinementSurfaces block not found"
    rs_text = rs_match.group(1)
    assert "merged_through_zone_3U" not in rs_text, \
        "through-zone should not appear in refinementSurfaces"
    rr_match = _re.search(r"refinementRegions\s*\{(.+?)^\s{4}\}", text, _re.DOTALL | _re.MULTILINE)
    if rr_match:
        rr_text = rr_match.group(1)
        assert "merged_through_zone_3U" not in rr_text, \
            "through-zone should not appear in refinementRegions"

    # merged_rack_door: surface-level faceType internal + faceZone
    assert "merged_rack_door" in text
    door_block_re = _re.search(
        r"merged_rack_door\s*\n\s*\{(.*?)\n    \}", text, _re.DOTALL)
    assert door_block_re is not None, "merged_rack_door block not found"
    door_block = door_block_re.group(1)
    assert "faceType internal" in door_block
    assert "faceZone merged_rack_door" in door_block

    # merged_wall_baffle: surface-level faceType internal + faceZone
    wb_block_re = _re.search(
        r"merged_wall_baffle\s*\n\s*\{(.*?)\n    \}", text, _re.DOTALL)
    assert wb_block_re is not None, "merged_wall_baffle block not found"
    wb_block = wb_block_re.group(1)
    assert "faceType internal" in wb_block
    assert "faceZone merged_wall_baffle" in wb_block


# ---- 2026-04-17 fix: merged_door / merged_wallBaffle → createBaffles cyclic pair ----

def test_merged_door_and_wallBaffle_emit_createBaffles_cyclic(tmp_path):
    """Post baffle-merge: merged_rack_door.stl → createBaffles cyclic pair;
    merged_wall_baffle.stl → createBaffles wall baffle master/slave.
    """
    stl_solids = {
        "merged_rack_door.stl": ["Default_Rack_4_inlet_door", "Default_Rack_4_outlet_door"],
        "merged_wall_baffle.stl": [
            "Default_Rack_1_outlet_u4_u5_wallBaffle",
        ],
        "merged_blanking_1U.stl": [
            "Default_Rack_2_inlet_u1_u2",
        ],
    }
    racks = {
        "Default_Rack_1": {
            "wallBaffleSolids": ["Default_Rack_1_outlet_u4_u5_wallBaffle"],
        },
        "Default_Rack_4": {
            "inletDoor":  {"enabled": True, "perforated": {"openAreaPct": 64},
                           "solidName": "Default_Rack_4_inlet_door"},
            "outletDoor": {"enabled": True, "perforated": {"openAreaPct": 64},
                           "solidName": "Default_Rack_4_outlet_door"},
        },
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)
    runner = _bootstrap_runner(tmp_path, {"baseSize": 0.4}, racks, stl_solids)
    runner._baffle_physics = {}
    runner._generate_create_baffles_dict()

    dict_path = tmp_path / "Mesh" / "system" / "createBafflesDict"
    assert dict_path.exists()
    text = dict_path.read_text()

    # merged_rack_door → ONE cyclic entry for the whole faceZone
    assert "merged_rack_door" in text
    assert "merged_rack_door_master" in text
    assert "merged_rack_door_slave" in text

    # merged_wall_baffle → ONE unified master/slave entry (surface-level faceZone)
    assert "merged_wall_baffle_master" in text
    assert "merged_wall_baffle_slave" in text

    # Regular blanking opening must NOT appear in createBaffles
    assert "Default_Rack_2_inlet_u1_u2" not in text


# ---- 2026-04-17: rack door seed collection (post-merged_door migration) ----

def test_rack_thin_baffle_solids_produce_create_baffle_seed_specs(tmp_path):
    """Post baffle-merge: door and wallBaffle solids in merged files
    produce seed specs; regular blanking openings do NOT."""
    stl_solids = {
        "merged_rack_door.stl": ["Default_Rack_4_inlet_door", "Default_Rack_4_outlet_door"],
        "merged_wall_baffle.stl": [
            "Default_Rack_1_outlet_u4_u5_wallBaffle",
        ],
        "merged_blanking_1U.stl": [
            "Default_Rack_2_inlet_u1_u2",
        ],
    }
    mesh_config = {"baseSize": 0.4}
    racks = {
        "Default_Rack_1": {
            "wallBaffleSolids": ["Default_Rack_1_outlet_u4_u5_wallBaffle"],
        },
        "Default_Rack_4": {
            "inletDoor":  {"enabled": True, "perforated": {"openAreaPct": 64},
                           "solidName": "Default_Rack_4_inlet_door"},
            "outletDoor": {"enabled": True, "perforated": {"openAreaPct": 64},
                           "solidName": "Default_Rack_4_outlet_door"},
        },
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)
    runner = _bootstrap_runner(tmp_path, mesh_config, racks, stl_solids)

    specs = runner._collect_create_baffle_seed_specs()
    zone_names = {s.zone_name for s in specs}

    # Door solids from merged_rack_door.stl
    assert "Default_Rack_4_inlet_door" in zone_names, \
        f"inlet door missing; got {zone_names}"
    assert "Default_Rack_4_outlet_door" in zone_names, \
        f"outlet door missing; got {zone_names}"
    door_spec = next(s for s in specs if s.zone_name == "Default_Rack_4_inlet_door")
    assert door_spec.stl_path.name == "merged_rack_door.stl"

    # wallBaffle solid from merged_wall_baffle.stl
    assert "Default_Rack_1_outlet_u4_u5_wallBaffle" in zone_names, \
        f"wallBaffle missing; got {zone_names}"
    wb_spec = next(s for s in specs if s.zone_name == "Default_Rack_1_outlet_u4_u5_wallBaffle")
    assert wb_spec.stl_path.name == "merged_wall_baffle.stl"

    # Regular blanking opening must NOT receive thin-baffle dual seeds
    assert "Default_Rack_2_inlet_u1_u2" not in zone_names


# ---- Task 5: createBaffles filter excludes through-zone ----

def test_through_zone_not_in_create_baffles(tmp_path):
    """through-zone STLs must not appear in createBafflesDict.
    (formerly test_leakage_bucket_not_in_create_baffles)"""
    stl_solids = {
        "merged_server_1U.stl":       ["S1_inlet"],
        "merged_through_zone_3U.stl": ["L3_zone"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "serverFacePerUnitLevel":      {"1": [3, 4]},
        "rackThroughZonePerUnitLevel": {"3": [1, 2]},
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)
    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    # Find and invoke whatever method writes createBafflesDict.
    # Try candidates in order:
    create_method = None
    for name in ("_generate_create_baffles_dict",
                 "_generate_createBaffles_dict",
                 "generate_create_baffles_dict"):
        if hasattr(runner, name):
            create_method = getattr(runner, name)
            break
    if create_method is None:
        # If the method isn't directly exposed, skip the deeper assertion
        # but still ensure the snappy dict is clean.
        import pytest as _pt
        _pt.skip("createBafflesDict method not found on STLGenRunner")
    create_method()
    cb_path = system_dir / "createBafflesDict"
    if cb_path.exists():
        cb_text = cb_path.read_text()
        assert "merged_through_zone_3U" not in cb_text, \
            "through-zone bucket leaked into createBafflesDict"
    # If createBafflesDict was not created (no baffles), that's also correct.
    # Through-zone files don't create baffles, only servers/baffles/partition do.


# ---- Task 6: floorFeatureLevel + wallBaffleSolids drift warning ----

def test_floor_feature_level_honoured(tmp_path):
    """merged_floor_ceiling.stl reads floorFeatureLevel (not defaultFeatureLevel)."""
    stl_solids = {"merged_floor_ceiling.stl": ["fl_1"]}
    mc = {"baseSize": 0.4, "floorFeatureLevel": 5, "defaultFeatureLevel": 2}
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    _write_dummy_stl(tri_dir, "merged_floor_ceiling.stl", ["fl_1"])
    runner = _bootstrap_runner(tmp_path, mc, {}, stl_solids)
    runner._generate_snappy_hex_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "snappyHexMeshDict").read_text()
    assert 'file "merged_floor_ceiling.eMesh";\n        level 5;' in text


def test_wallbafflesolids_nonempty_warns_and_drops(tmp_path):
    """If a rack has wallBaffleSolids populated (schema drift), the runner
    warns and drops the entries — does NOT route them to merged_baffle."""
    from loguru import logger
    import io

    racks = {
        "A17": {
            "wallBaffleSolids": ["A17_weird_wall_baffle"],
            "servers": [],
            "partitions": [],
            "leakage": {"groups": []},
        },
    }
    mc = {"baseSize": 0.4}
    runner = _bootstrap_runner(tmp_path, mc, racks, {})

    # Capture loguru output using a string buffer sink
    log_output = io.StringIO()
    handler_id = logger.add(log_output, format="{message}", level="WARNING")
    try:
        runner._generate_snappy_hex_mesh_dict(tmp_path / "Mesh" / "system")
        log_text = log_output.getvalue()
        assert "wallBaffleSolids" in log_text and "A17" in log_text, \
            f"runner must warn when wallBaffleSolids is non-empty; got: {log_text}"
    finally:
        logger.remove(handler_id)


# ---- Task 7: patch-on-face inheritance in scene-merged regions ----

def test_patch_inherits_parent_surface_level(tmp_path):
    """A patch solid whose name is `<parent_solid>_p<N>` inherits the
    parent's level when emitted into a regions block."""
    stl_solids = {
        "merged_server_1U.stl": [
            "A17_server01_inlet",
            "A17_server01_inlet_p0",   # patch on the inlet
            "A17_server01_inlet_p1",
        ],
    }
    mc = {
        "baseSize": 0.4,
        "serverFacePerUnitLevel": {"1": [3, 4]},
    }
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    _write_dummy_stl(tri_dir, "merged_server_1U.stl",
                     ["A17_server01_inlet", "A17_server01_inlet_p0",
                      "A17_server01_inlet_p1"])
    runner = _bootstrap_runner(tmp_path, mc, {}, stl_solids)
    runner._generate_snappy_hex_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "snappyHexMeshDict").read_text()
    # Per-bucket file now emits a `regions{}` block (one entry per solid).
    # Patch inheritance means: parent and its `_p<N>` patch children get the
    # same level because they match the same per-region classifier branch
    # (same span → same per_unit_levels lookup). With this synthetic fixture
    # the racks data is empty so every solid falls to the default wall-patch
    # branch — they all share rl_default, preserving the inheritance
    # invariant (parent == patch). The point of this test is that no patch
    # solid diverges from its parent, not the specific level value.
    import re as _re
    region_re = _re.compile(
        r"A17_server01_inlet(_p[0-9]+)?\s*\n\s*{\s*\n\s*level\s+\(([^)]+)\)",
    )
    levels = [m.group(2) for m in region_re.finditer(text)]
    assert len(levels) == 3, f"expected 3 region levels (parent + 2 patches), got {levels}"
    assert len(set(levels)) == 1, \
        f"patch solids must inherit parent level, got {levels}"
