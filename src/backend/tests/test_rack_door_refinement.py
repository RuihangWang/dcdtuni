"""Regression: rack perforated-door refinement bucketed by min-U-span.

Spec: docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md
"""
from backend.aircooling.runner.cfdrun_schema import CFDExport, CFDExportMeshConfig
from backend.aircooling.runner.stlgen_runner import (
    _compute_rack_min_u_span,
    _door_level_for_rack,
    _door_feature_level_for_rack,
)


def test_cfdexport_round_trip_door_fields():
    """Full CFDExport preserves door refinement keys through pydantic round-trip."""
    exp = CFDExport.model_validate({
        "meshConfig": {
            "rackDoorRefinementLevel": [2, 3],
            "rackDoorFeatureLevel": 4,
        },
    })
    assert exp.meshConfig.rackDoorRefinementLevel == [2, 3]
    assert exp.meshConfig.rackDoorFeatureLevel == 4


def test_compute_min_u_span_reads_frontend_field():
    """Backend just reads the int the frontend already computed."""
    assert _compute_rack_min_u_span({"minUSpanForDoorBucket": 1}) == 1
    assert _compute_rack_min_u_span({"minUSpanForDoorBucket": 4}) == 4


def test_compute_min_u_span_null_field_returns_none():
    """Frontend writes null for racks with no server/blanking/leakage;
    backend treats that identically to a missing key."""
    assert _compute_rack_min_u_span({"minUSpanForDoorBucket": None}) is None


def test_compute_min_u_span_missing_field_returns_none():
    """Legacy cfdrun without the 2026-04-17 field → None → caller
    falls back to unified level."""
    assert _compute_rack_min_u_span({}) is None


def test_compute_min_u_span_string_value_is_coerced():
    """JSON round-trip through pydantic's extra='allow' may preserve
    the int as-is; accept stringified form defensively."""
    assert _compute_rack_min_u_span({"minUSpanForDoorBucket": "2"}) == 2


def test_door_level_unified_no_bucket():
    """After merge, _door_level_for_rack ignores per-bucket and uses unified level."""
    rack_data = {"minUSpanForDoorBucket": 2}
    mesh_config = {
        "rackDoorRefinementLevel": [3, 4],
        "rackDoorPerMinUnitLevel": {"2": [5, 6]},
    }
    result = _door_level_for_rack(rack_data, mesh_config)
    assert result == [3, 4], f"Expected unified [3,4], got {result}"


def test_door_feature_level_unified_no_bucket():
    """After merge, _door_feature_level_for_rack uses unified level only."""
    rack_data = {"minUSpanForDoorBucket": 2}
    mesh_config = {
        "rackDoorFeatureLevel": 3,
        "rackDoorRefinementLevel": [2, 4],
        "rackDoorFeaturePerMinUnitLevel": {"2": 6},
    }
    result = _door_feature_level_for_rack(rack_data, mesh_config)
    assert result == 3


def test_door_level_always_uses_unified():
    """All racks use rackDoorRefinementLevel regardless of per-bucket data."""
    rack = {"minUSpanForDoorBucket": 1}
    mc = {
        "rackDoorRefinementLevel": [2, 3],
        "rackDoorPerMinUnitLevel": {"1": [5, 5]},  # ignored
    }
    assert _door_level_for_rack(rack, mc) == [2, 3]


def test_door_level_unified_for_empty_rack():
    """Empty rack (minUSpanForDoorBucket absent) → unified."""
    rack = {}
    mc = {"rackDoorRefinementLevel": [2, 3]}
    assert _door_level_for_rack(rack, mc) == [2, 3]


def test_door_level_defaults_when_meshconfig_missing_keys():
    """Missing both keys → [1, 1] (matches frontend initializer)."""
    rack = {"minUSpanForDoorBucket": 1}
    mc = {}
    assert _door_level_for_rack(rack, mc) == [1, 1]


def test_door_level_null_span_field_uses_unified():
    """Explicit `{"minUSpanForDoorBucket": None}` → unified level."""
    rack = {"minUSpanForDoorBucket": None}
    mc = {"rackDoorRefinementLevel": [2, 3]}
    assert _door_level_for_rack(rack, mc) == [2, 3]


# ---------------------------------------------------------------------------
# Task 4: Integration — STLGenRunner._generate_snappy_hex_mesh_dict emits
#         bucketed door levels in the refinementSurfaces section.
# ---------------------------------------------------------------------------

from pathlib import Path

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _rack_stub(rack_name: str, min_span):
    """Minimum rack shape `_generate_snappy_hex_mesh_dict` reads.

    Uses the flat `minUSpanForDoorBucket` field the runner reads
    directly (spec §Algorithm §Resolve). Pass `None` to simulate an
    empty rack.
    """
    return {
        "minUSpanForDoorBucket": min_span,
        "inletDoor": {"enabled": True, "solidName": f"{rack_name}_inlet_door"},
        "outletDoor": {"enabled": True, "solidName": f"{rack_name}_outlet_door"},
    }


def _make_dummy_stl(solid_name: str) -> str:
    """Return a minimal valid ASCII STL with the given solid name."""
    return (
        f"solid {solid_name}\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 1 0 0\n"
        "      vertex 0 1 0\n"
        "    endloop\n"
        "  endfacet\n"
        f"endsolid {solid_name}\n"
    )


def _bootstrap_runner(tmp_path: Path, mesh_config: dict, racks: dict,
                      stl_solids: dict) -> STLGenRunner:
    """STLGenRunner with _data / _stl_solids pre-set (no _load call).

    Creates a minimal dummy STL in triSurface/ for every filename in
    stl_solids — each file contains all the solids listed for that file,
    so path-existence checks pass.

    Stubs out _build_locations_in_mesh_entries (returns []) to skip the
    geometry-driven baffle-seed computation that requires a real scene.
    """
    tri_surface = tmp_path / "Mesh" / "constant" / "triSurface"
    (tmp_path / "Mesh" / "system").mkdir(parents=True, exist_ok=True)
    tri_surface.mkdir(parents=True, exist_ok=True)
    (tmp_path / "model").mkdir(parents=True, exist_ok=True)
    # Create dummy STL files with correct solid names so triangle-cache passes.
    for filename, solids in stl_solids.items():
        content = "".join(_make_dummy_stl(s) for s in solids)
        (tri_surface / filename).write_text(content, encoding="utf-8")
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
    # Stub out geometry-driven seed computation — not under test here.
    runner._build_locations_in_mesh_entries = lambda: []
    # No .cfdrun source inventory here; UID dictionary output has its own tests.
    runner._source_surface_dict_block = lambda _mesh_config: ""
    return runner


def test_snappy_dict_emits_fixed_door_level(tmp_path):
    """All doors merged into merged_rack_door.stl use rl_rack_internal — rack region handles density."""
    racks = {
        "R1U": _rack_stub("R1U", 1),
        "R4U": _rack_stub("R4U", 4),
    }
    stl_solids = {
        "merged_rack.stl": ["R1U", "R4U"],
        "merged_rack_door.stl": ["R1U_inlet_door", "R1U_outlet_door",
                                  "R4U_inlet_door", "R4U_outlet_door"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "rackDoorRefinementLevel": [2, 3],  # ignored — extras
    }
    runner = _bootstrap_runner(tmp_path, mesh_config, racks, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    # Merged door entry uses rl_rack_internal with faceType internal
    assert "merged_rack_door" in text
    assert "faceType internal;" in text
    assert "faceZone merged_rack_door;" in text


def test_snappy_dict_empty_rack_door_uses_fixed(tmp_path):
    """Empty rack door also uses rl_rack_internal (1 1)."""
    racks = {"REMPTY": _rack_stub("REMPTY", None)}  # None = empty rack
    stl_solids = {
        "merged_rack.stl": ["REMPTY"],
        "baffle_REMPTY_inlet_door.stl": ["REMPTY_inlet_door"],
        "baffle_REMPTY_outlet_door.stl": ["REMPTY_outlet_door"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "rackDoorRefinementLevel": [2, 3],  # ignored — extras
    }
    runner = _bootstrap_runner(tmp_path, mesh_config, racks, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    # Doors use rack-internal refinement level (3 3) with faceType internal
    assert 'baffle_REMPTY_inlet_door\n    {\n        level (3 3);' in text
    assert 'baffle_REMPTY_outlet_door\n    {\n        level (3 3);' in text


def test_snappy_dict_door_feature_edge_uses_baffle_feature(tmp_path):
    """Door files start with baffle_ so they get feat_baffle level."""
    racks = {"R1U": _rack_stub("R1U", 1)}
    stl_solids = {
        "merged_rack.stl": ["R1U"],
        "baffle_R1U_inlet_door.stl": ["R1U_inlet_door"],
        "baffle_R1U_outlet_door.stl": ["R1U_outlet_door"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "baffleFeatureLevel": 3,  # door files use feat_baffle
    }
    runner = _bootstrap_runner(tmp_path, mesh_config, racks, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    # Door feature level = feat_baffle = 3
    assert 'file "baffle_R1U_inlet_door.eMesh";\n        level 3;' in text
    assert 'file "baffle_R1U_outlet_door.eMesh";\n        level 3;' in text


# ---------------------------------------------------------------------------
# 2026-04-19: Door feature-line level is now user-controllable
# (unified scalar + per-min-unit override). Auto-fallback to surface max
# is preserved when neither is set, to stay backward-compatible with
# existing cfdrun files written before this change.
# ---------------------------------------------------------------------------

def test_meshconfig_accepts_door_feature_value():
    mc = CFDExportMeshConfig(rackDoorFeatureLevel=4)
    assert mc.rackDoorFeatureLevel == 4


def test_door_feature_falls_back_to_surface_max_when_unset():
    """No rackDoorFeatureLevel → fall back to surface max of unified level."""
    rack = {"minUSpanForDoorBucket": 1}
    mc = {"rackDoorRefinementLevel": [3, 5]}
    assert _door_feature_level_for_rack(rack, mc) == 5


def test_door_feature_uses_unified_scalar_when_set():
    """rackDoorFeatureLevel overrides the surface-max auto default."""
    rack = {"minUSpanForDoorBucket": 1}
    mc = {
        "rackDoorRefinementLevel": [3, 5],
        "rackDoorFeatureLevel": 4,
    }
    assert _door_feature_level_for_rack(rack, mc) == 4


def test_door_feature_null_span_uses_unified():
    """Empty rack (minUSpanForDoorBucket=None) — unified scalar if set,
    else auto-fallback to surface max."""
    rack = {"minUSpanForDoorBucket": None}
    mc = {"rackDoorRefinementLevel": [2, 3]}
    # Unified unset → surface max = 3
    assert _door_feature_level_for_rack(rack, mc) == 3
    mc["rackDoorFeatureLevel"] = 5
    # Unified set → 5
    assert _door_feature_level_for_rack(rack, mc) == 5


def test_snappy_dict_door_feature_uses_baffle_feature_all_racks(tmp_path):
    """End-to-end: door feature level comes from baffleFeatureLevel."""
    racks = {"R1U": _rack_stub("R1U", 1), "R4U": _rack_stub("R4U", 4)}
    stl_solids = {
        "merged_rack.stl": ["R1U", "R4U"],
        "baffle_R1U_inlet_door.stl": ["R1U_inlet_door"],
        "baffle_R1U_outlet_door.stl": ["R1U_outlet_door"],
        "baffle_R4U_inlet_door.stl": ["R4U_inlet_door"],
        "baffle_R4U_outlet_door.stl": ["R4U_outlet_door"],
    }
    mesh_config = {
        "baseSize": 0.4,
        "baffleFeatureLevel": 4,  # door files use feat_baffle
    }
    runner = _bootstrap_runner(tmp_path, mesh_config, racks, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    text = (system_dir / "snappyHexMeshDict").read_text()

    # All doors use feat_baffle = 4
    assert 'file "baffle_R1U_inlet_door.eMesh";\n        level 4;' in text
    assert 'file "baffle_R4U_inlet_door.eMesh";\n        level 4;' in text
