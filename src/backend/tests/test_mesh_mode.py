"""Three-mode mesh generation: bodyFitted | cartesianLocalized | structured.

The mesh panel exposes a single `meshMode` dropdown (replacing the old
cartesianMode/structuredGrid checkboxes):

  * bodyFitted        — original snappyHexMesh body-fitted: snap ON, uniform bg.
  * cartesianLocalized — pure Cartesian: snap OFF, uniform bg + per-object local
    refinementRegions (octree). No domain-spanning grid lines (no cross-hatch);
    fewest cells; boundaries octree-stepped at the local fine-cell size.
  * structured        — grid-aligned Cartesian: snap OFF, blockMesh lines aligned
    on object faces (compute_aligned_grid). Smooth straight boundaries, but the
    lines span the domain (cross-hatch / 十字加密).

`_resolve_mesh_mode` is the single source of truth; it accepts the new `meshMode`
key and falls back to the legacy cartesianMode/structuredGrid booleans so old
.cfdrun manifests keep working. See docs spec on the 3 mesh modes.
"""
from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig
from backend.aircooling.runner.stlgen_runner import (
    _resolve_mesh_mode,
    _mesh_mode_is_cartesian,
    STLGenRunner,
)


# ---- _resolve_mesh_mode: the ENGINE decides (2026-09-09) ----
#
# The three-way meshMode is retired: snappy is body-fitted, dcCart is pure
# cut-cell on a uniform background. Explicit meshMode values and the legacy
# cartesianMode/structuredGrid booleans are ignored. An omitted engine is the
# dcCart product default (8d3ed7942).

def test_resolve_follows_engine():
    assert _resolve_mesh_mode({}) == "cartesianLocalized"
    assert _resolve_mesh_mode({"meshEngine": "snappy"}) == "bodyFitted"
    assert _resolve_mesh_mode({"meshEngine": "dcCart"}) == "cartesianLocalized"


def test_resolve_ignores_explicit_mode_and_legacy_booleans():
    snappy = {"meshEngine": "snappy"}
    assert _resolve_mesh_mode({**snappy, "meshMode": "structured"}) == "bodyFitted"
    assert _resolve_mesh_mode({**snappy, "meshMode": "cartesianLocalized"}) == "bodyFitted"
    assert _resolve_mesh_mode({**snappy, "cartesianMode": True, "structuredGrid": True}) == "bodyFitted"
    assert _resolve_mesh_mode({"meshMode": "structured", "meshEngine": "dcCart"}) == "cartesianLocalized"



def test_is_cartesian_helper():
    assert _mesh_mode_is_cartesian("cartesianLocalized") is True
    assert _mesh_mode_is_cartesian("structured") is True
    assert _mesh_mode_is_cartesian("bodyFitted") is False


# ---- schema ----

def test_schema_meshmode_default_body_fitted():
    mc = CFDExportMeshConfig()
    assert mc.meshMode == "bodyFitted"


# ---- snappyHexMeshDict snap toggle by mode ----

def _bootstrap(tmp_path, mesh_config):
    (tmp_path / "Mesh" / "system").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Mesh" / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)
    (tmp_path / "model").mkdir(parents=True, exist_ok=True)
    r = STLGenRunner(case_dir=tmp_path, cfdrun_path=tmp_path / "model" / "p.cfdrun")
    r._data = {
        "racks": {}, "meshConfig": mesh_config, "acus": {}, "baffles": {},
        "floors": {}, "walls": {}, "wallObjects": {}, "heatSources": {},
        "partitionWalls": {},
        "room": {"plane": [{"x": 0, "y": 0}, {"x": 10, "y": 0},
                            {"x": 10, "y": 6}, {"x": 0, "y": 6}], "height": 3.0},
        "simulationConfig": {},
    }
    r._stl_solids = {"room_wall.stl": ["room_wall"]}
    r._build_locations_in_mesh_entries = lambda: []
    # Mesh-mode tests have no .cfdrun source inventory; UID dictionary output
    # is covered by dedicated source-surface tests.
    r._source_surface_dict_block = lambda _mesh_config: ""
    return r


def _write_dummy_stl(tri_dir, filename, solid):
    (tri_dir / filename).write_text(
        f"solid {solid}\nfacet normal 0 0 1\n outer loop\n"
        f"  vertex 0 0 0\n  vertex 1 0 0\n  vertex 0 1 0\n endloop\nendfacet\n"
        f"endsolid {solid}\n")


def _snappy(tmp_path, mesh_config):
    r = _bootstrap(tmp_path, mesh_config)
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    _write_dummy_stl(tri, "room_wall.stl", "room_wall")
    sysd = tmp_path / "Mesh" / "system"
    r._generate_snappy_hex_mesh_dict(sysd)
    return (sysd / "snappyHexMeshDict").read_text()


def test_body_fitted_snaps_on(tmp_path):
    assert "snap on;" in _snappy(tmp_path, {"baseSize": 0.34, "meshEngine": "snappy", "meshMode": "bodyFitted"})


def test_dccart_engine_snaps_off(tmp_path):
    assert "snap off;" in _snappy(tmp_path, {"baseSize": 0.34, "meshEngine": "dcCart"})


def test_retired_modes_stay_body_fitted(tmp_path):
    # the retired three-way meshMode no longer turns snapping off on its own
    assert "snap on;" in _snappy(tmp_path, {"baseSize": 0.34, "meshEngine": "snappy", "meshMode": "structured"})
    assert "snap on;" in _snappy(tmp_path, {"baseSize": 0.34, "meshEngine": "snappy",
                                            "meshMode": "cartesianLocalized"})


# ---- blockMeshDict grid choice by mode ----

def _write_box_stl(tri_dir, filename, solid, lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    (tri_dir / filename).write_text(
        f"solid {solid}\nfacet normal 0 0 1\n outer loop\n  vertex {x0} {y0} {z0}\n"
        f"  vertex {x1} {y0} {z0}\n  vertex {x1} {y1} {z1}\n endloop\nendfacet\n"
        f"facet normal 0 0 1\n outer loop\n  vertex {x0} {y0} {z0}\n"
        f"  vertex {x1} {y1} {z1}\n  vertex {x0} {y1} {z1}\n endloop\nendfacet\n"
        f"endsolid {solid}\n")


def _blockmesh(tmp_path, mesh_config):
    r = _bootstrap(tmp_path, mesh_config)
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    _write_box_stl(tri, "merged_rack_frame.stl", "Rack_1", (1.37, 0.0, 1.0), (1.97, 2.0, 2.2))
    sysd = tmp_path / "Mesh" / "system"
    r._generate_block_mesh_dict(sysd)
    return (sysd / "blockMeshDict").read_text()


def test_retired_structured_no_longer_aligns_blockmesh(tmp_path):
    # grid-aligned breakpoints are gone: every engine keeps the uniform grid
    txt = _blockmesh(tmp_path, {"baseSize": 0.34, "meshMode": "structured"})
    assert "1.37" not in txt and "1.97" not in txt


def test_dccart_blockmesh_is_uniform(tmp_path):
    txt = _blockmesh(tmp_path, {"baseSize": 0.34, "meshEngine": "dcCart"})
    assert "1.37" not in txt


def test_body_fitted_blockmesh_is_uniform(tmp_path):
    txt = _blockmesh(tmp_path, {"baseSize": 0.34, "meshEngine": "snappy", "meshMode": "bodyFitted"})
    assert "1.37" not in txt
