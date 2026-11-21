"""MeshPreviewLoader._patch_surface: the vectorized boundary-surface builder.

GTC's mesh preview spent ~30 s in a per-face Python loop (sets, dicts and an
ear clip for every polygon of five or more vertices); the builder is now numpy
over whole patches. These pin what it must still do: local point ids in sorted
global order, unique sorted edges, a fan for convex faces and an ear clip for
a concave cut-cell face, so the drawn area is the face's area.
"""

import numpy as np

from backend.aircooling.runner.postprocess import MeshPreviewLoader


def _area(points, tris):
    a, b, c = (points[tris[:, i]].astype(float) for i in range(3))
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()


def _faces(*faces):
    sizes = [len(f) for f in faces]
    offsets = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    return offsets, np.array([v for f in faces for v in f], dtype=np.int64)


def test_quad_and_convex_pentagon_fan_with_shared_edges_once():
    pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0], [2.5, 0.5, 0], [2, 1, 0]], np.float32)
    off, verts = _faces([0, 1, 2, 3], [1, 4, 5, 6, 2])
    p, tris, edges = MeshPreviewLoader._patch_surface(pts, off, verts)
    assert np.array_equal(p, pts)
    assert tris.tolist() == [[0, 1, 2], [0, 2, 3], [1, 4, 5], [1, 5, 6], [1, 6, 2]]
    assert edges.tolist() == sorted(edges.tolist())
    assert len(edges) == 8  # 4 + 5 sides, the shared side 1-2 once
    assert _area(p, tris) == 1.0 + 1.25


def test_concave_cut_cell_face_is_ear_clipped_not_fanned():
    # An L of area 3, one hexagon. Fanning from vertex 0 crosses the notch
    # at (1, 1) and covers area 4.
    pts = np.array([[0, 0, 0], [2, 0, 0], [2, 2, 0], [1, 2, 0], [1, 1, 0], [0, 1, 0]], np.float32)
    off, verts = _faces([0, 1, 2, 3, 4, 5])
    fan = np.array([[0, k, k + 1] for k in range(1, 5)])
    assert _area(pts, fan) != 3.0
    p, tris, _ = MeshPreviewLoader._patch_surface(pts, off, verts)
    assert len(tris) == 4
    assert abs(_area(p, tris) - 3.0) < 1e-6


def test_local_ids_follow_sorted_global_ids():
    pts = np.arange(30, dtype=np.float32).reshape(10, 3)
    off, verts = _faces([9, 4, 7])
    p, tris, _ = MeshPreviewLoader._patch_surface(pts, off, verts)
    assert np.array_equal(p, pts[[4, 7, 9]])
    assert tris.tolist() == [[2, 0, 1]]
