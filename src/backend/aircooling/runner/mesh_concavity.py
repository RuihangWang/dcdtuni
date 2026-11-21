"""Real concave cells, counted the way checkMesh -allGeometry counts concave cells.

OpenFOAM's ``primitiveMesh::checkConcaveCells`` marks a cell when the centre of
one of its faces is not strictly behind the plane of another of its faces:
``(pC & fN) > -planarCosAngle`` -- its own comment says "Concave or planar
face". A cell with two COPLANAR faces is therefore reported too, and every
octree transition cell has them (four sub-faces against four finer
neighbours): a snappyHexMesh SingleRack mesh reports 1249 such cells, and
94.6 % of the 68,786 reported on Shinaide_liquid are planar only.

``concave_cell_counts`` runs the same test on the same face areas and centres
and returns both numbers: the cells OpenFOAM reports, and the ones whose face
centre lies strictly in FRONT of another face's plane (``> +planarCosAngle``),
which are the real concavities.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

import numpy as np

from backend.aircooling.runner.polymesh_io import (
    read_all_faces,
    read_label_list,
    read_points,
)

PLANAR_COS_ANGLE = 1.0e-6   # primitiveMesh::planarCosAngle_ default


def _face_areas_centres(points, offsets, verts):
    """primitiveMesh::makeFaceCentresAndAreas: triangle fan about the point mean."""
    n_faces = len(offsets) - 1
    sizes = np.diff(offsets)
    face_of = np.repeat(np.arange(n_faces), sizes)
    pos = np.arange(len(verts))
    last = np.repeat(offsets[1:] - 1, sizes)
    first = np.repeat(offsets[:-1], sizes)
    nxt = np.where(pos == last, first, pos + 1)
    p = points[verts]
    q = points[verts[nxt]]

    def per_face(values):
        # np.add.at in a loop of bincounts: the same sums in the same order, several times faster
        values = np.asarray(values, float)
        if values.ndim == 1:
            return np.bincount(face_of, weights=values, minlength=n_faces)
        return np.stack([np.bincount(face_of, weights=values[:, k], minlength=n_faces)
                         for k in range(values.shape[1])], axis=1)
    c0 = per_face(p)
    c0 /= sizes[:, None]
    c = c0[face_of]
    n = np.cross(q - p, c - p)
    sum_n = per_face(n)
    mag = np.linalg.norm(sum_n, axis=1)
    nhat = sum_n / np.maximum(mag, 1e-300)[:, None]
    a = np.einsum("ij,ij->i", n, nhat[face_of])
    sum_a = per_face(a)
    sum_ac = per_face(a[:, None] * (p + q + c) / 3.0)
    centres = np.where(np.abs(sum_a)[:, None] > 1e-300,
                       sum_ac / np.where(np.abs(sum_a) > 1e-300, sum_a, 1.0)[:, None], c0)
    # three-point faces: OpenFOAM uses the triangle itself (same result)
    return 0.5 * sum_n, centres


def concave_cell_counts(points, offsets, verts, owner, neighbour) -> Tuple[int, int]:
    """(cells checkMesh reports as concave, cells really concave)."""
    points = np.asarray(points, float)
    offsets = np.asarray(offsets, np.int64)
    verts = np.asarray(verts, np.int64)
    owner = np.asarray(owner, np.int64)
    neighbour = np.asarray(neighbour, np.int64)
    areas, centres = _face_areas_centres(points, offsets, verts)
    normals = areas / np.maximum(np.linalg.norm(areas, axis=1), 1e-300)[:, None]

    inc_cell = np.concatenate([owner, neighbour])
    inc_face = np.concatenate([np.arange(len(owner)), np.arange(len(neighbour))])
    inc_sign = np.concatenate([np.ones(len(owner)), -np.ones(len(neighbour))])
    order = np.argsort(inc_cell, kind="stable")
    inc_cell, inc_face, inc_sign = inc_cell[order], inc_face[order], inc_sign[order]
    n_cells = int(inc_cell.max()) + 1 if inc_cell.size else 0
    counts = np.bincount(inc_cell, minlength=n_cells)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])

    flagged = concave = 0
    for k in np.unique(counts):
        if k < 2:
            continue
        cells = np.nonzero(counts == k)[0]
        for chunk in range(0, len(cells), max(1, 2_000_000 // int(k * k))):
            sel = cells[chunk: chunk + max(1, 2_000_000 // int(k * k))]
            idx = starts[sel][:, None] + np.arange(k)[None, :]
            f = inc_face[idx]
            fn = normals[f] * inc_sign[idx][:, :, None]          # outward
            fc = centres[f]
            d = fc[:, None, :, :] - fc[:, :, None, :]            # [cell, i, j] = c_j - c_i
            d /= np.maximum(np.linalg.norm(d, axis=3), 1e-300)[..., None]
            dots = np.einsum("cijx,cix->cij", d, fn)
            diag = np.arange(k)
            dots[:, diag, diag] = -np.inf
            worst = dots.reshape(len(sel), -1).max(axis=1)
            flagged += int(np.count_nonzero(worst > -PLANAR_COS_ANGLE))
            concave += int(np.count_nonzero(worst > PLANAR_COS_ANGLE))
    return flagged, concave


def _counts_of(root: Path) -> Tuple[int, int]:
    poly = Path(root) / "constant" / "polyMesh"
    offsets, verts = read_all_faces(poly / "faces")
    return concave_cell_counts(read_points(poly / "points"), offsets, verts,
                               read_label_list(poly / "owner"), read_label_list(poly / "neighbour"))


def concave_cell_counts_in(case_dir: Path, n_procs: int = 1) -> Tuple[int, int]:
    """The same counts for a serial or decomposed case (summed over processors). The processors are counted in
    parallel worker processes (Alibaba-hongchou's eight ranks of 1.3 M cells: 76 s one after another)."""
    roots = ([Path(case_dir) / f"processor{i}" for i in range(n_procs)]
             if n_procs > 1 else [Path(case_dir)])
    if len(roots) == 1:
        results = [_counts_of(roots[0])]
    else:
        import multiprocessing
        import os
        from concurrent.futures import ProcessPoolExecutor
        workers = max(1, min(len(roots), (os.cpu_count() or 2) // 2))
        with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as pool:
            results = list(pool.map(_counts_of, roots))
    return sum(f for f, _ in results), sum(c for _, c in results)
