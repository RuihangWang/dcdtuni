"""Two racks side by side over a raised-floor cut-out must not be seeded inside.

Base_coldAisleIso (2026-11-10): Rack_1 (z 2.2..3.0) and Rack_2 (z 3.0..3.8)
are hollow frames sharing the z = 3.0 wall, standing in ONE cut-out of the
raised floor. The floor sheet's area centroid lies in that cut-out, inside
Rack_1's side wall (z = 2.99997). The two-sided clearance from there still read
large (the ray runs up inside the wall and only meets the frame's top face), so
the centroid was kept as the anchor. The grid-plane nudge then moved the seed
from z = 2.99997 across the coincident walls to z = 3.0625, inside Rack_2's
side wall. dcCartMesh floods from every seed, so the whole frame of Rack_2 was
kept as fluid: its bottom, top, outer side and ring ends had no faces, and the
plenum opened into the room through the missing bottom.

The mesher was right; the seeds were wrong. Both steps are guarded now.
"""

from __future__ import annotations

from pathlib import Path

from backend.aircooling.runner import stlgen_runner as sr
from backend.aircooling.runner.stlgen_runner import STLGenRunner, _PatchSeedSpec


def quad(p0, p1, p2, p3):
    return [(p0, p1, p2), (p0, p2, p3)]


def box(x0, x1, y0, y1, z0, z1):
    t = []
    t += quad((x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1))
    t += quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))
    t += quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))
    t += quad((x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1))
    t += quad((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0))
    t += quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
    return t


WALL = 0.08
X0, X1, Y0, Y1 = 4.0, 5.2, 0.2, 2.4


def frame_slabs(z0, z1):
    """A hollow rack frame (tube along x) as four wall slabs."""
    return [
        (X0, X1, Y0, Y0 + 0.0489, z0 + WALL, z1 - WALL),   # bottom, between the sides
        (X0, X1, Y1 - 0.0489, Y1, z0 + WALL, z1 - WALL),   # top
        (X0, X1, Y0, Y1, z0, z0 + WALL),            # side
        (X0, X1, Y0, Y1, z1 - WALL, z1),            # side
    ]


ZMAX = 5.9999
RACK_1 = frame_slabs(2.2, 3.0)
RACK_2 = frame_slabs(3.0, 3.8)


def inside_solid(p):
    eps = 1e-9
    return any(
        x0 + eps < p[0] < x1 - eps and y0 + eps < p[1] < y1 - eps and z0 + eps < p[2] < z1 - eps
        for x0, x1, y0, y1, z0, z1 in RACK_1 + RACK_2
    )


def floor_with_cutout():
    """Raised floor y = 0.2 over x 0..10, z 0..ZMAX, open under both racks
    (x 4..5.2, z 2.2..3.8). Its area centroid is in the cut-out, a hair below
    the shared wall: inside Rack_1's side wall, as in the real case."""
    y = 0.2
    zmax = ZMAX
    t = []
    t += quad((0, y, 0), (10, y, 0), (10, y, 2.2), (0, y, 2.2))
    t += quad((0, y, 3.8), (10, y, 3.8), (10, y, zmax), (0, y, zmax))
    t += quad((0, y, 2.2), (4, y, 2.2), (4, y, 3.8), (0, y, 3.8))
    t += quad((5.2, y, 2.2), (10, y, 2.2), (10, y, 3.8), (5.2, y, 3.8))
    return t


def scene_cache():
    frames = {}
    for name, slabs in (("Rack_1_frame", RACK_1), ("Rack_2_frame", RACK_2)):
        frames[name] = [tri for s in slabs for tri in box(*s)]
    return {
        "merged_rack_frame.stl": frames,
        "wallBaffle_floor.stl": {"raised_floor_part1": floor_with_cutout()},
        "room_wall.stl": {"room": box(0, 10, 0, 3, 0, 6)},
    }


def runner(planes=None):
    r = STLGenRunner.__new__(STLGenRunner)
    r._data = {"meshConfig": {"baseSize": 0.25, "baffleRefinementLevel": [1, 1]}}
    r.mesh_dir = Path("/nonexistent")
    r.tri_surface_dir = Path("/nonexistent")
    cache = scene_cache()
    r._stl_solids = {k: list(v) for k, v in cache.items()}
    r._build_stl_triangle_cache = lambda: cache
    r._collect_create_baffle_seed_specs = lambda: [
        _PatchSeedSpec("raised_floor_part1", "raised_floor_part1",
                       Path("wallBaffle_floor.stl"), "floor_surface_wallBaffle")
    ]
    if planes is not None:
        r._bg_grid_planes = planes
    return r


def test_floor_centroid_is_in_the_cutout_and_off_the_sheet():
    floor = floor_with_cutout()
    center, _ = sr._compute_planar_patch_geometry(floor)
    assert 4.0 < center[0] < 5.2 and 3.0 - WALL < center[2] < 3.0
    assert inside_solid((center[0], center[1] + 0.06, center[2]))
    assert not sr._point_on_sheet(center, floor)
    for cand in sr._sheet_anchor_candidates(floor):
        assert sr._point_on_sheet(cand, floor)


def test_floor_seeds_are_outside_both_frames():
    entries = runner()._build_patch_driven_none_seed_entries()
    assert len(entries) == 2
    for point, _ in entries:
        assert not inside_solid(point), f"seed {point} is inside a rack frame"


def test_nudge_does_not_cross_the_shared_rack_wall():
    """A seed just below the shared z = 3.0 wall (fluid or not) must not be
    pushed across it into the second frame."""
    planes = {"x": [0.0, 0.25 * 40], "y": [0.0, 0.25, 0.5], "z": [2.75, 3.0, 3.25]}
    r = runner(planes)
    seed = (4.6, 0.30, 2.99997)        # between the racks' outer z = 3.0 faces
    (pt, _), = r._nudge_seeds_off_grid_planes([(seed, "none")])
    assert not (3.0 < pt[2] < 3.08 and X0 < pt[0] < X1 and Y0 < pt[1] < Y1), pt


def test_nudge_still_moves_a_seed_with_room_on_the_roomier_side():
    planes = {"x": [0.0, 10.0], "y": [0.0, 3.0], "z": [5.5, 5.75, 6.0]}
    r = runner(planes)
    (pt, _), = r._nudge_seeds_off_grid_planes([((7.0, 1.0, 5.75), "none")])
    assert pt[2] != 5.75 and 5.5 < pt[2] < 6.0


def floor_open_under_rack_1_only():
    """Base_coldAisleIso_sealedColdAisle: the floor is cut open under Rack_1
    only and runs on under Rack_2, whose bottom face lies ON the sheet. The
    area centroid (~5.007, 0.2, ~3.007) is on the sheet, under Rack_2's side
    wall; the +y ray starts past the coincident bottom face and reports the
    room above the frame's top, 2.2 m away."""
    y = 0.2
    t = []
    t += quad((0, y, 0), (10, y, 0), (10, y, 2.2), (0, y, 2.2))
    t += quad((0, y, 3.0), (10, y, 3.0), (10, y, 6), (0, y, 6))
    t += quad((0, y, 2.2), (4, y, 2.2), (4, y, 3.0), (0, y, 3.0))
    t += quad((5.2, y, 2.2), (10, y, 2.2), (10, y, 3.0), (5.2, y, 3.0))
    return t


def test_seeds_skip_a_sheet_point_a_rack_stands_on():
    floor = floor_open_under_rack_1_only()
    center, _ = sr._compute_planar_patch_geometry(floor)
    assert sr._point_on_sheet(center, floor)
    assert inside_solid((center[0], center[1] + 0.06, center[2]))
    r = runner()
    cache = scene_cache()
    cache["wallBaffle_floor.stl"] = {"raised_floor_part1": floor}
    r._build_stl_triangle_cache = lambda: cache
    entries = r._build_patch_driven_none_seed_entries()
    assert len(entries) == 2
    for point, _ in entries:
        assert not inside_solid(point), f"seed {point} is inside a rack frame"
