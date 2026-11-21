"""A wall baffle with a cut-out around an ACU must not seed the ACU's inside.

STDCT (2026-09-22): Baffle_6_copy stands on x = 18.4 with a hole where PCU_3's
front is. The sheet's area centroid lies INSIDE that hole, on the ACU's front
plane, so the +X ray met the ACU at 0.1 mm, the offset was clamped to 0.08 mm
and the +X seed landed inside the ACU. dcCart's carve then flooded the ACU and
seven of its nine patches had no faces. The anchor has to be a point on the
sheet itself, where both sides are open.
"""

from backend.aircooling.runner.stlgen_runner import (
    _best_sheet_seed_anchor,
    _compute_planar_patch_geometry,
    _sheet_anchor_candidates,
    _two_sided_clearance,
)


def quad(p0, p1, p2, p3):
    return [(p0, p1, p2), (p0, p2, p3)]


def box_triangles(x0, x1, y0, y1, z0, z1):
    """A closed axis-aligned box as 12 triangles (winding irrelevant here)."""
    tris = []
    tris += quad((x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1))  # x0
    tris += quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))  # x1
    tris += quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))  # y0
    tris += quad((x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1))  # y1
    tris += quad((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0))  # z0
    tris += quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))  # z1
    return tris


def sheet_with_hole():
    """A sheet on x = 0, y 0..3, z 0..8, with the hole y 0..2, z 2..6 cut out."""
    x = 0.0
    tris = []
    tris += quad((x, 0, 0), (x, 3, 0), (x, 3, 2), (x, 0, 2))       # left band  z 0..2
    tris += quad((x, 0, 6), (x, 3, 6), (x, 3, 8), (x, 0, 8))       # right band z 6..8
    tris += quad((x, 2, 2), (x, 3, 2), (x, 3, 6), (x, 2, 6))       # top band   y 2..3
    return tris


def test_centroid_of_holed_sheet_lies_in_the_hole_and_is_rejected():
    sheet = sheet_with_hole()
    acu = box_triangles(0.0001, 1.6, 0, 2, 2, 6)          # against the sheet, in the hole
    wall = box_triangles(-3.0, -2.9, 0, 3, 0, 8)         # a wall 3 m behind
    far = box_triangles(5.0, 5.1, 0, 3, 0, 8)            # the room's far wall in front
    scene = [("baffle.stl", "sheet", t) for t in sheet]
    scene += [("acu.stl", "acu", t) for t in acu]
    scene += [("wall.stl", "wall", t) for t in wall]
    scene += [("wall.stl", "far", t) for t in far]
    ignored = {("baffle.stl", "sheet")}

    center, normal = _compute_planar_patch_geometry(sheet)
    direction = normal if normal[0] > 0 else (-normal[0], -normal[1], -normal[2])
    # The area centroid sits in the cut-out: rays from it hit the ACU at once.
    assert 2.0 < center[2] < 6.0 and center[1] < 2.0
    pos, neg = _two_sided_clearance(center, direction, scene, ignored)
    assert pos is not None and neg is not None
    assert min(pos, neg) < 0.001

    best = _best_sheet_seed_anchor(sheet, direction, offset=0.05, scene_triangles=scene, ignored_surfaces=ignored)
    assert best is not None
    anchor, cp, cn = best
    # On the sheet, outside the hole, with at least the offset on both sides.
    assert abs(anchor[0]) < 1e-9
    assert not (2.0 < anchor[2] < 6.0 and anchor[1] < 2.0)
    assert min(cp, cn) >= 0.05
    # And the seeds it yields are outside the ACU box.
    for side in (0.05, -0.05):
        seed = (anchor[0] + side, anchor[1], anchor[2])
        inside = 0.0001 < seed[0] < 1.6 and 0 < seed[1] < 2 and 2 < seed[2] < 6
        assert not inside


def test_candidates_lie_on_the_sheet():
    sheet = sheet_with_hole()
    cands = _sheet_anchor_candidates(sheet)
    assert len(cands) == 4 * len(sheet)
    for c in cands:
        assert abs(c[0]) < 1e-12
        assert 0 <= c[1] <= 3 and 0 <= c[2] <= 8
        assert not (2.0 < c[2] < 6.0 and c[1] < 2.0)


def test_open_sheet_keeps_a_fully_clear_anchor():
    sheet = quad((0, 0, 0), (0, 3, 0), (0, 3, 8), (0, 0, 8))
    scene = [("baffle.stl", "sheet", t) for t in sheet]
    scene += [("wall.stl", "wall", t) for t in box_triangles(-3.0, -2.9, 0, 3, 0, 8)]
    scene += [("wall.stl", "wall2", t) for t in box_triangles(2.9, 3.0, 0, 3, 0, 8)]
    best = _best_sheet_seed_anchor(sheet, (1.0, 0.0, 0.0), 0.05, scene, {("baffle.stl", "sheet")})
    assert best is not None and min(best[1], best[2]) > 2.0
