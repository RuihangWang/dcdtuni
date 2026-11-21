"""locationsInMesh seeds must never sit on a background grid plane.

snappyHexMesh resolves each seed with polyMesh::findCell, whose tet test
rejects a point lying ON a cell face — it belongs to neither neighbour — and
the run dies at the first step with "Point (...) is not inside the mesh or on a
face or edge". Alibaba-hongchou died exactly this way on z=7.25.

In `structured` mode this is the norm, not a coincidence: grid lines are placed
on object faces, and a dual-side baffle seed is offset along the patch NORMAL
only, so its other two coordinates stay at the patch centroid — right where a
grid line tends to be.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _runner(planes: dict | None = None) -> STLGenRunner:
    r = STLGenRunner.__new__(STLGenRunner)
    r._data = {}
    r.mesh_dir = Path("/tmp")
    if planes is not None:
        r._bg_grid_planes = planes
    return r


UNIFORM = {
    "x": [0.0, 1.0, 2.0, 3.0],
    "y": [0.0, 1.0, 2.0, 3.0],
    "z": [0.0, 1.0, 2.0, 3.0],
}


def test_seed_exactly_on_a_plane_is_moved_off_it():
    r = _runner(UNIFORM)
    (pt, zone), = r._nudge_seeds_off_grid_planes([((1.5, 1.5, 2.0), "none")])
    assert zone == "none"
    assert pt[0] == 1.5 and pt[1] == 1.5, "axes already inside a cell must not move"
    assert pt[2] != 2.0
    assert min(abs(pt[2] - p) for p in UNIFORM["z"]) > 0.1


def test_a_seed_already_inside_a_cell_is_left_alone():
    r = _runner(UNIFORM)
    original = (1.5, 1.5, 1.5)
    (pt, _), = r._nudge_seeds_off_grid_planes([(original, "none")])
    assert pt == original


def test_all_three_axes_can_be_nudged_at_once():
    r = _runner(UNIFORM)
    (pt, _), = r._nudge_seeds_off_grid_planes([((1.0, 2.0, 1.0), "none")])
    for i, axis in enumerate("xyz"):
        assert min(abs(pt[i] - p) for p in UNIFORM[axis]) > 0.1, f"{axis} still on a plane"


def test_nudge_stays_inside_the_adjacent_cell():
    """A quarter-cell move: big enough for findCell, small enough that the seed
    stays in the region it was placed to identify."""
    r = _runner(UNIFORM)
    (pt, _), = r._nudge_seeds_off_grid_planes([((1.5, 1.5, 2.0), "none")])
    assert 1.0 < pt[2] < 3.0
    assert abs(pt[2] - 2.0) <= 0.5


def test_nudge_prefers_the_roomier_neighbour():
    """Graded grids are the whole point of structured mode; a seed on the plane
    between a thin cell and a fat one should move into the fat one."""
    r = _runner({"x": [0.0, 1.0], "y": [0.0, 1.0], "z": [0.0, 0.98, 1.0, 3.0]})
    (pt, _), = r._nudge_seeds_off_grid_planes([((0.5, 0.5, 1.0), "none")])
    assert pt[2] > 1.0, "should move into the 2.0-wide cell, not the 0.02-wide one"


def test_near_miss_within_the_band_is_also_nudged():
    """Exactly-on is what bit us, but a near-miss is just as ambiguous to the
    tet test, so the band catches it too."""
    r = _runner(UNIFORM)
    (pt, _), = r._nudge_seeds_off_grid_planes([((1.5, 1.5, 2.0 + 1e-9), "none")])
    assert abs(pt[2] - 2.0) > 0.1


def test_no_cached_grid_means_seeds_pass_through_untouched():
    """Mesh modes that never populate the cache must not be perturbed."""
    r = _runner(None)
    entries = [((1.0, 2.0, 3.0), "none")]
    assert r._nudge_seeds_off_grid_planes(entries) == entries


def test_hongchou_regression():
    """The real failure: z=7.25 was a grid plane, x and y were mid-cell."""
    planes = {
        "x": [3.15, 3.4416665],
        "y": [3.0, 3.333333333],
        "z": [6.65, 6.95, 7.25, 7.55],
    }
    r = _runner(planes)
    (pt, _), = r._nudge_seeds_off_grid_planes([((3.226, 3.175, 7.25), "none")])
    assert pt[0] == 3.226 and pt[1] == 3.175
    assert min(abs(pt[2] - p) for p in planes["z"]) > 1e-3
