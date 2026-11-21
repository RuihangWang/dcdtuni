"""checkMesh -allGeometry counts a cell with two coplanar faces as concave.

Every octree transition cell has them (four sub-faces against four finer
neighbours), so the strict gate counts real concavity separately: the same
face-plane test OpenFOAM applies, but a face centre exactly IN another face's
plane does not count.
"""
import numpy as np

from backend.aircooling.runner.mesh_concavity import concave_cell_counts


def _mesh(cells):
    """cells: list of lists of (polygon points, outward) for each cell; one cell, all boundary."""
    pts, verts, offsets, owner = [], [], [0], []
    for c, polys in enumerate(cells):
        for poly in polys:
            for p in poly:
                pts.append(p)
                verts.append(len(pts) - 1)
            offsets.append(len(verts))
            owner.append(c)
    return (np.array(pts, float), np.array(offsets), np.array(verts), np.array(owner), np.array([], int))


def _quad(a, b, c, d):
    return [a, b, c, d]


def _box_faces(x0, x1, y0, y1, z0, z1, split_top=False):
    f = [
        _quad((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)),  # bottom, outward -z
        _quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)),  # -y
        _quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)),  # +x
        _quad((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)),  # +y
        _quad((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)),  # -x
    ]
    if not split_top:
        f.append(_quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)))
    else:
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        for (a0, a1, b0, b1) in ((x0, xm, y0, ym), (xm, x1, y0, ym), (xm, x1, ym, y1), (x0, xm, ym, y1)):
            f.append(_quad((a0, b0, z1), (a1, b0, z1), (a1, b1, z1), (a0, b1, z1)))
    return f


def test_a_transition_hex_is_only_planar_and_an_l_cell_is_concave():
    hexa = _box_faces(0, 1, 0, 1, 0, 1)
    transition = _box_faces(0, 1, 0, 1, 0, 1, split_top=True)
    # L-shaped prism in x-y, extruded in z: outward polygons
    L = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    ell = [[(x, y, 0) for x, y in reversed(L)], [(x, y, 1) for x, y in L]]
    for (ax, ay), (bx, by) in zip(L, L[1:] + L[:1]):
        ell.append(_quad((ax, ay, 0), (bx, by, 0), (bx, by, 1), (ax, ay, 1)))
    flagged, concave = concave_cell_counts(*_mesh([hexa, transition, ell]))
    assert (flagged, concave) == (2, 1)
