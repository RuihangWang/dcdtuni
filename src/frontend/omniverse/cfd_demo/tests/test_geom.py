"""Pure geometry helpers behind the in-viewport gizmo fixes.

Both the ROI box and the streamline seed sphere are draggable in the Kit viewport.
A gizmo drag authors a USD XFORM on the prim; it does NOT rewrite the prim's authored
points. Everything downstream (the voxeliser's ROI, the seed clamp) reads the LOCAL
points, so a drag used to be invisible to the render. These helpers let the backend
bake a dragged xform back into world-space points and detect that a drag happened.

USD convention: Gf.Matrix4d is row-major with the ROW-VECTOR convention (v' = v * M),
so the translation lives in row 3.
"""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(
    HERE, "..", "kit", "extensions", "dc.cfd_bridge")))

from dc.cfd_bridge import geom  # noqa: E402

IDENT = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def translation(tx, ty, tz):
    return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [tx, ty, tz, 1]]


def scaling(s):
    return [[s, 0, 0, 0], [0, s, 0, 0], [0, 0, s, 0], [0, 0, 0, 1]]


UNIT_BOX = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]


# ── aabb_of ────────────────────────────────────────────────────────────────
def test_aabb_of_unit_box():
    assert geom.aabb_of(UNIT_BOX) == ([0, 0, 0], [1, 1, 1])


def test_aabb_of_empty_is_none():
    assert geom.aabb_of([]) is None


# ── transform_points (the drag) ────────────────────────────────────────────
def test_transform_points_identity_is_noop():
    out = geom.transform_points(UNIT_BOX, IDENT)
    assert [tuple(round(c, 6) for c in p) for p in out] == UNIT_BOX


def test_transform_points_translation_shifts_aabb():
    """A gizmo DRAG is exactly this: translation only, points untouched."""
    out = geom.transform_points(UNIT_BOX, translation(10, 2, -3))
    mn, mx = geom.aabb_of(out)
    assert [round(v, 6) for v in mn] == [10, 2, -3]
    assert [round(v, 6) for v in mx] == [11, 3, -2]


def test_transform_points_scale_grows_aabb():
    mn, mx = geom.aabb_of(geom.transform_points(UNIT_BOX, scaling(4)))
    assert [round(v, 6) for v in mn] == [0, 0, 0]
    assert [round(v, 6) for v in mx] == [4, 4, 4]


# ── remap_points_to_aabb (bake the drag back into points) ──────────────────
def test_remap_points_hits_target_aabb_exactly():
    tgt_min, tgt_max = [5.0, 1.0, 2.0], [9.0, 3.0, 8.0]
    out = geom.remap_points_to_aabb(UNIT_BOX, tgt_min, tgt_max)
    mn, mx = geom.aabb_of(out)
    assert [round(v, 6) for v in mn] == tgt_min
    assert [round(v, 6) for v in mx] == tgt_max


def test_remap_points_is_self_correcting_from_any_state():
    """Remapping an ALREADY-remapped box must still land on the target -- the backend
    calls this repeatedly against whatever the box currently is."""
    once = geom.remap_points_to_aabb(UNIT_BOX, [5, 1, 2], [9, 3, 8])
    twice = geom.remap_points_to_aabb(once, [0, 0, 0], [1, 1, 1])
    mn, mx = geom.aabb_of(twice)
    assert [round(v, 6) for v in mn] == [0, 0, 0]
    assert [round(v, 6) for v in mx] == [1, 1, 1]


def test_remap_points_degenerate_axis_does_not_divide_by_zero():
    """A flat box (zero extent on an axis) must not blow up."""
    flat = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]
    out = geom.remap_points_to_aabb(flat, [0, 0, 0], [2, 0, 2])
    assert all(len(p) == 3 for p in out)
    mn, mx = geom.aabb_of(out)
    assert [round(v, 6) for v in mn][0] == 0 and [round(v, 6) for v in mx][0] == 2


# ── is_identity (did the user drag?) ───────────────────────────────────────
def test_is_identity_true_for_identity():
    assert geom.is_identity(IDENT) is True


def test_is_identity_false_after_a_drag():
    assert geom.is_identity(translation(0.5, 0, 0)) is False


def test_is_identity_tolerates_float_noise():
    noisy = [[1 + 1e-12, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [1e-10, 0, 0, 1]]
    assert geom.is_identity(noisy) is True


def test_is_identity_none_is_identity():
    """No xform authored at all == not dragged."""
    assert geom.is_identity(None) is True


# ── moved (did the seed sphere get dragged away from what we commanded?) ───
def test_moved_detects_drag():
    assert geom.moved([15.5, 2.5, 9.6], [12.0, 2.5, 9.6]) is True


def test_moved_false_when_unchanged():
    assert geom.moved([15.5, 2.5, 9.6], [15.5, 2.5, 9.6]) is False


def test_moved_ignores_float_noise():
    assert geom.moved([15.5, 2.5, 9.6], [15.5 + 1e-9, 2.5, 9.6]) is False


def test_moved_true_when_either_side_missing():
    """No commanded value recorded yet -> treat the live prim as authoritative."""
    assert geom.moved(None, [1, 2, 3]) is True
