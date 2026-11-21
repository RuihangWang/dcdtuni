"""Tests for Approach A — pure octree Cartesian mesh mode.

Investigation & rationale: Ref_code/REPORT_Cartesian_Mesh_Generator.md.

dcdtuni already drives snappyHexMesh as an octree Cartesian engine. Approach A
turns the surface-snapping stage OFF so the mesh stays a 6Sigma-style axis-aligned
(stair-step) Cartesian grid, and exposes a `firstCellSize` knob that maps to a
wall/floor surface refinement level so near-wall cell size (hence y+) is
controllable: first-cell = baseSize / 2^level  ⇔  level = ceil(log2(baseSize/firstCellSize)).
"""
import math
from pathlib import Path

from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _bootstrap_runner(tmp_path: Path, mesh_config: dict, stl_solids: dict) -> STLGenRunner:
    """Minimal runner fixture — sets _data + _stl_solids directly (no _load())."""
    (tmp_path / "Mesh" / "system").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Mesh" / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)
    (tmp_path / "model").mkdir(parents=True, exist_ok=True)
    runner = STLGenRunner(
        case_dir=tmp_path,
        cfdrun_path=tmp_path / "model" / "project.cfdrun",
    )
    runner._data = {
        "racks": {}, "meshConfig": mesh_config,
        "acus": {}, "baffles": {}, "floors": {}, "walls": {},
        "wallObjects": {}, "heatSources": {}, "partitionWalls": {},
        "room": {}, "simulationConfig": {},
    }
    runner._stl_solids = stl_solids
    runner._build_locations_in_mesh_entries = lambda: []
    # This fixture tests only mesh-mode dictionary values; source ownership
    # serialization has its own manifest-backed tests.
    runner._source_surface_dict_block = lambda _mesh_config: ""
    return runner


def _write_dummy_stl(tri_dir: Path, filename: str, solid_names):
    lines = []
    for name in solid_names:
        lines += [f"solid {name}", "facet normal 0 0 1", " outer loop",
                  "  vertex 0 0 0", "  vertex 1 0 0", "  vertex 0 1 0",
                  " endloop", "endfacet", f"endsolid {name}"]
    (tri_dir / filename).write_text("\n".join(lines) + "\n")


def _gen(tmp_path: Path, mesh_config: dict) -> str:
    stl_solids = {"room_wall.stl": ["room_wall"]}
    runner = _bootstrap_runner(tmp_path, mesh_config, stl_solids)
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    _write_dummy_stl(tri_dir, "room_wall.stl", ["room_wall"])
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)
    return (system_dir / "snappyHexMeshDict").read_text()


def test_default_engine_is_dccart_cut_cell(tmp_path):
    """Default (no meshEngine) is the dcCart product default -> snap OFF. The
    three-way meshMode is retired (2026-09-09): the semantics follow the engine."""
    text = _gen(tmp_path, {"baseSize": 0.4})
    assert "snap off;" in text
    assert "snap on;" not in text


def test_explicit_body_fitted_keeps_snap_on(tmp_path):
    """bodyFitted is opt-in via the snappy engine -> snapping ON."""
    text = _gen(tmp_path, {"baseSize": 0.4, "meshEngine": "snappy", "meshMode": "bodyFitted"})
    assert "snap on;" in text
    assert "snap off;" not in text


def test_dccart_engine_turns_snap_off(tmp_path):
    """meshEngine=dcCart → pure cut-cell Cartesian: castellation only, snap off.
    The legacy cartesianMode boolean on its own is ignored."""
    text = _gen(tmp_path, {"baseSize": 0.4, "meshEngine": "dcCart"})
    assert "snap off;" in text
    assert "snap on;" not in text
    legacy = _gen(tmp_path / "legacy", {"baseSize": 0.4, "meshEngine": "snappy",
                                        "cartesianMode": True})
    assert "snap on;" in legacy
    # castellation + no layers are unchanged
    assert "castellatedMesh on;" in text
    assert "addLayers off;" in text


def test_first_cell_size_sets_wall_refinement_level(tmp_path):
    """firstCellSize maps to room-wall surface level = ceil(log2(baseSize/firstCellSize)).

    baseSize 0.4, firstCellSize 0.05 → 0.4/0.05 = 8 → log2 8 = 3 → level 3.
    """
    base, first = 0.4, 0.05
    expected = math.ceil(math.log2(base / first))  # = 3
    text = _gen(tmp_path, {"baseSize": base, "cartesianMode": True, "firstCellSize": first})
    assert f"level ({expected} {expected});" in text


def test_first_cell_size_only_refines_never_coarsens(tmp_path):
    """A coarse firstCellSize (level below the explicit refinementLevel) must NOT
    reduce an existing finer wall level — firstCellSize takes the max."""
    # refinementLevel [2,2]; firstCellSize 0.4 → level 0 → must stay >= 2.
    text = _gen(tmp_path, {"baseSize": 0.4, "cartesianMode": True,
                           "firstCellSize": 0.4, "refinementLevel": [2, 2]})
    assert "level (2 2);" in text
    assert "level (0 0);" not in text


def test_meshconfig_schema_has_cartesian_fields():
    """Schema exposes the new opt-in fields with backward-compatible defaults."""
    mc = CFDExportMeshConfig()
    assert mc.cartesianMode is False
    assert mc.firstCellSize is None
    # structuredGrid: opt-in grid-aligned ("structured") sub-mode of cartesianMode.
    # Default False = pure-cartesian uniform background (no cross-hatch).
    assert mc.structuredGrid is False
