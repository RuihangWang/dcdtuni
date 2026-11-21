"""Grid-aligned ("structured") Cartesian background grid (6Sigma 'grid lines on faces').

Two Cartesian (snap-off) sub-modes share the same octree refinementRegions but
differ only in the blockMesh background:

* DEFAULT — pure Cartesian (``cartesianMode`` only): a UNIFORM coarse background
  (``compute_multiblock_grid``). Local per-object ``refinementRegions`` (octree)
  add the fine detail. No domain-spanning grid lines, so no cross-hatch.
* STRUCTURED (``cartesianMode`` + ``structuredGrid``): background grid lines are
  placed exactly on object faces (``compute_aligned_grid``). Boundaries come out
  perfectly straight, at the cost of those lines spanning the whole domain
  (the cross-hatch / 十字加密). Non-default second option.

See Ref_code/REPORT_Cartesian_Mesh_Generator.md and
Ref_code/VALIDATION_AllCases_Cartesian_vs_Snapping.md.
"""
from pathlib import Path

from backend.aircooling.runner.stlgen_runner import (
    compute_aligned_grid,
    compute_multiblock_grid,
    STLGenRunner,
)


def _boundaries(segs):
    """Sorted unique segment breakpoints (exclude the 1-cell padding ends)."""
    pts = sorted({round(s[0], 6) for s in segs} | {round(s[1], 6) for s in segs})
    return pts[1:-1]  # drop low/high padding extremes


def test_aligned_grid_places_breakpoints_on_object_faces():
    """Object faces become exact grid breakpoints."""
    rb = {"x": (0.0, 10.0), "y": (0.0, 3.0), "z": (0.0, 6.0)}
    planes = {"x": [3.0, 3.6], "y": [], "z": [1.0, 2.2]}
    mb = compute_aligned_grid(rb, 0.34, planes)
    bx = _boundaries(mb["x"])
    assert 3.0 in bx and 3.6 in bx
    assert 0.0 in bx and 10.0 in bx
    bz = _boundaries(mb["z"])
    assert 1.0 in bz and 2.2 in bz


def test_aligned_grid_merges_sliver_breakpoints():
    """Two faces forming a sub-sliver gap (< base/8) merge to one breakpoint."""
    rb = {"x": (0.0, 10.0), "y": (0.0, 3.0), "z": (0.0, 6.0)}
    # 3.60 and 3.62 are 0.02 apart << base/8 = 0.0425 → merge to one (avoid sliver)
    planes = {"x": [3.60, 3.62], "y": [], "z": []}
    mb = compute_aligned_grid(rb, 0.34, planes)
    bx = _boundaries(mb["x"])
    near = [p for p in bx if abs(p - 3.6) < 0.1]
    assert len(near) == 1, f"expected one merged breakpoint near 3.6, got {near}"


def test_aligned_grid_keeps_close_but_distinct_faces():
    """REGRESSION (XDUA box right face): two REAL faces ~50mm apart must BOTH
    survive — the old 0.25*base (~85mm) greedy merge dropped the second one, so
    a box face recessed by a full cell. Distinct faces above the sliver floor
    (base/8 ≈ 42mm) must each become a grid line so snap-off keeps them flush."""
    rb = {"x": (0.0, 10.0), "y": (0.0, 3.0), "z": (0.0, 6.0)}
    planes = {"x": [4.95, 5.0], "y": [], "z": []}  # 50mm apart, both real faces
    mb = compute_aligned_grid(rb, 0.34, planes)
    bx = _boundaries(mb["x"])
    assert 4.95 in bx and 5.0 in bx, (
        f"close-but-distinct faces (50mm) were dropped — staircase: {bx}")


def test_aligned_grid_keeps_face_near_room_bound():
    """REGRESSION (false ceiling near room top): an interior face only ~50mm
    below the room max must survive. The old (r_max - 0.25*base) margin excluded
    any face within ~85mm of a bound, so ceiling/floor-adjacent panels lost their
    grid line and went bumpy. Only planes essentially AT the bound (≤1mm) drop."""
    rb = {"x": (0.0, 10.0), "y": (0.0, 3.8), "z": (0.0, 6.0)}
    planes = {"y": [3.75], "x": [], "z": []}  # 50mm below the 3.8 ceiling
    mb = compute_aligned_grid(rb, 0.34, planes)
    by = _boundaries(mb["y"])
    assert 3.75 in by, f"near-bound interior face excluded — staircase: {by}"


def test_aligned_grid_does_not_move_trailing_face_onto_bound():
    """A face close to (but distinct from) the room max must NOT be snapped onto
    the boundary (the old trailing snap-to-r_max absorbed it, recessing it)."""
    rb = {"x": (0.0, 5.0), "y": (0.0, 3.0), "z": (0.0, 6.0)}
    planes = {"x": [4.9], "y": [], "z": []}  # 100mm below r_max=5.0
    mb = compute_aligned_grid(rb, 0.34, planes)
    bx = _boundaries(mb["x"])
    assert 4.9 in bx and 5.0 in bx, f"trailing face merged into r_max: {bx}"


def test_aligned_grid_cells_near_base_size():
    """Each segment between breakpoints is divided at ~base_size resolution."""
    rb = {"x": (0.0, 10.0), "y": (0.0, 3.0), "z": (0.0, 6.0)}
    planes = {"x": [3.0, 3.6], "y": [], "z": []}
    mb = compute_aligned_grid(rb, 0.34, planes)
    for lo, hi, n in mb["x"]:
        cs = (hi - lo) / n
        assert 0.17 <= cs <= 0.68, f"cell size {cs} for segment {(lo,hi,n)} off-base"


def test_aligned_grid_ignores_planes_outside_room():
    """Planes at/outside the room bounds are not inserted as interior breaks."""
    rb = {"x": (0.0, 10.0), "y": (0.0, 3.0), "z": (0.0, 6.0)}
    planes = {"x": [-1.0, 0.0, 10.0, 12.0], "y": [], "z": []}
    mb = compute_aligned_grid(rb, 0.34, planes)
    interior = [p for p in _boundaries(mb["x"]) if 0.0 < p < 10.0]
    assert interior == [], f"no interior breaks expected, got {interior}"


# ---- integration: _generate_block_mesh_dict aligns to STL faces in cartesianMode ----

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
    r._stl_solids = {}
    r._build_locations_in_mesh_entries = lambda: []
    return r


def _write_box_stl(tri_dir, filename, solid, lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    f = (f"solid {solid}\n"
         f"facet normal 0 0 1\n outer loop\n  vertex {x0} {y0} {z0}\n"
         f"  vertex {x1} {y0} {z0}\n  vertex {x1} {y1} {z1}\n endloop\nendfacet\n"
         f"facet normal 0 0 1\n outer loop\n  vertex {x0} {y0} {z0}\n"
         f"  vertex {x1} {y1} {z1}\n  vertex {x0} {y1} {z1}\n endloop\nendfacet\n"
         f"endsolid {solid}\n")
    (tri_dir / filename).write_text(f)


def test_block_mesh_dict_structured_alignment_is_retired(tmp_path):
    """The grid-aligned ("structured") background is retired (2026-09-09): the
    legacy cartesianMode + structuredGrid pair no longer puts a rack face on a
    blockMesh vertex. dcCart resolves the face with its cut cells instead."""
    r = _bootstrap(tmp_path, {"baseSize": 0.34, "cartesianMode": True,
                              "structuredGrid": True, "meshEngine": "dcCart"})
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    # rack frame box: x 1.37..1.97 — NOT a multiple of 0.34
    _write_box_stl(tri, "merged_rack_frame.stl", "Rack_1", (1.37, 0.0, 1.0), (1.97, 2.0, 2.2))
    r._generate_block_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "blockMeshDict").read_text()
    assert "1.37" not in text and "1.97" not in text, "structured alignment should be retired"


def test_block_mesh_dict_pure_cartesian_default_is_uniform(tmp_path):
    """DEFAULT Cartesian (cartesianMode only, NO structuredGrid) keeps the UNIFORM
    background — pure octree Cartesian, no domain-spanning grid lines / cross-hatch.

    Object faces are captured by local refinementRegions, NOT by background grid
    lines, so a non-uniform rack face must NOT appear as a blockMesh vertex.
    """
    r = _bootstrap(tmp_path, {"baseSize": 0.34, "cartesianMode": True})
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    _write_box_stl(tri, "merged_rack_frame.stl", "Rack_1", (1.37, 0.0, 1.0), (1.97, 2.0, 2.2))
    r._generate_block_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "blockMeshDict").read_text()
    assert "1.37" not in text, "pure-cartesian default must not align background grid to rack faces"


def test_block_mesh_dict_uniform_when_body_fitted(tmp_path):
    """bodyFitted (opt-in via explicit meshMode) keeps the uniform grid — no
    face alignment. (Repo default is now 'structured', which DOES align.)"""
    r = _bootstrap(tmp_path, {"baseSize": 0.34, "meshMode": "bodyFitted"})
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    _write_box_stl(tri, "merged_rack_frame.stl", "Rack_1", (1.37, 0.0, 1.0), (1.97, 2.0, 2.2))
    r._generate_block_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "blockMeshDict").read_text()
    assert "1.37" not in text, "bodyFitted uniform grid must not align to rack faces"


def test_block_mesh_dict_uniform_for_baffles_with_dccart(tmp_path):
    """A containment or wall baffle face no longer becomes a grid line either:
    the uniform background is kept for every engine, the baffle is resolved by
    the cut-cell pass (dcCart) or by snapping (snappy)."""
    r = _bootstrap(tmp_path, {"baseSize": 0.5, "meshEngine": "dcCart"})
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    _write_box_stl(tri, "perforated_baffle_AHU_partition_patch_1.stl", "Baffle_1",
                   (3.37, 0.0, 1.0), (3.43, 2.0, 2.2))
    _write_box_stl(tri, "merged_wall_baffle.stl", "WallBaffle_1",
                   (4.21, 0.0, 1.0), (4.27, 2.0, 2.2))
    r._generate_block_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "blockMeshDict").read_text()
    assert "3.37" not in text and "4.21" not in text


def test_containment_baffle_not_aligned_in_pure_cartesian(tmp_path):
    """Pure-cartesian default (no structuredGrid) must NOT add a domain-spanning
    grid line for the baffle — it is captured by local refinement, not background
    cross-hatch. Guards against the new glob leaking into uniform-background mode."""
    r = _bootstrap(tmp_path, {"baseSize": 0.5, "cartesianMode": True})
    tri = tmp_path / "Mesh" / "constant" / "triSurface"
    _write_box_stl(tri, "perforated_baffle_AHU_partition_patch_1.stl", "Baffle_1",
                   (3.37, 0.0, 1.0), (3.43, 2.0, 2.2))
    r._generate_block_mesh_dict(tmp_path / "Mesh" / "system")
    text = (tmp_path / "Mesh" / "system" / "blockMeshDict").read_text()
    assert "3.37" not in text, "pure-cartesian must not align background grid to baffle"
