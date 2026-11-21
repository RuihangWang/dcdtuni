"""OpenFOAM's ``nearWallDist``: the wall distance its wall functions read, on the wall-function faces.

``turbulenceModel::y()`` is a ``nearWallDist``: for every wall face ``f`` it is the distance from the face's
cell centre to the NEAREST POINT of the face polygons in ``{f} + the point neighbours of f``
(``cellDistFuncs::getPointNeighbours`` / ``smallestDist``, ``meshTools/cellDist/cellDistFuncs.C:62-87``), each
polygon fanned into triangles about its centre (``face::nearestPointClassify``).  ``epsilonWallFunction``
(epsilon0 = Cmu^0.75 k^1.5 / (kappa y)), ``nutkWallFunction`` (y+), the wall-cell G and
``alphatJayatillekeWallFunction`` all read this y.

Which faces count as point neighbours.  v2406's ``nearWallDist.C:43-89`` searches the face's OWN patch.  The
v2412 dctwin HF run is reproduced only when the search runs over the UNION of the wall patches: in the
wall-adjacent corner cells whose faces lie on two patches (a room wall and the raised floor, a rack frame and a
server top) the HF epsilon is the log-layer value at the smaller of the two distances on BOTH faces (the median
of epsilon_HF / target goes from 1.04 to 1.000 and the 99.9 % quantile from 1.77 to 1.11 over 14,323 such
cells).  The union is what is implemented; the faces in it are those of every patch that declares
``wallDistance = "nearWallDist"``.

This repository's default wall distance (``FVInterpolation.wall_distance_face``) is the owner-centre NORMAL
projection ``|(x_c - x_f) . n_f|``.  The two agree where the centre projects inside its own face and no
neighbouring wall face is closer; they differ on cut cells (the projection falls outside the face: the
nearest-point distance is larger) and in corners (a neighbouring face is closer).  On a snappy data-centre
hall about 5 % of the wall faces differ by more than 1 %, the ratio nearWallDist / normal spans 0.4 .. 3.4, and
in wall-adjacent corner cells the normal projection puts the log-layer epsilon up to 2x above OpenFOAM's.

Opt-in: the ``epsilonWallFunction`` bc toml entry ``wallDistance = "nearWallDist"`` (default ``"normal"``).
The first eval of such a patch after a registration replaces the declaring patches' faces of
``wall_distance_face`` (``apply_near_wall_dist``), before the value is read in that forward, so every
wall-function consumer of the tensor sees the same y.  Serial definition: a decomposed OpenFOAM run sees only
the faces of its own processor (on the dctwin hall this changes no face).
"""
from __future__ import annotations

import torch

from FVsolver.Turbulence.wall_distance import _triangle_dist2, wall_fan_triangles


def _point_neighbour_pairs(local_face: torch.Tensor, node: torch.Tensor, n_faces: int):
    """All (f, g) with faces f, g sharing a node (f == g included), each pair once."""
    order = torch.argsort(node, stable=True)
    nd, fj = node[order], local_face[order]
    _, counts = torch.unique_consecutive(nd, return_counts=True)
    starts = torch.cumsum(counts, 0) - counts
    sq = counts * counts
    group = torch.repeat_interleave(torch.arange(counts.numel(), device=node.device), sq)
    offset = torch.arange(int(sq.sum()), device=node.device) - torch.repeat_interleave(torch.cumsum(sq, 0) - sq, sq)
    m = counts[group]
    a = fj[starts[group] + offset // m]
    b = fj[starts[group] + offset % m]
    key = torch.unique(a * n_faces + b)
    return key // n_faces, key % n_faces


def near_wall_dist(cell_centre: torch.Tensor, face_node: torch.Tensor, face_node_ptr: torch.Tensor,
                   node_pos: torch.Tensor, face_pos: torch.Tensor, faces: torch.Tensor) -> torch.Tensor:
    """nearWallDist y [W] of the wall faces ``faces`` [W], the point neighbours searched among ``faces``.

    ``cell_centre`` [W, 3]: the centre of each face's (owner) cell, in the order of ``faces``; ``face_node`` /
    ``face_node_ptr``: the nodes of all faces and the face of each entry; ``face_pos``: all face centres."""
    dev = cell_centre.device
    W = int(faces.numel())
    if W == 0:                                         # a patch with no faces (the wall of a dropped region)
        return torch.empty(0, dtype=cell_centre.dtype, device=dev)
    loc = torch.full((int(face_node_ptr.max()) + 1,), -1, dtype=torch.long, device=dev)
    loc[faces] = torch.arange(W, device=dev)
    entry = loc[face_node_ptr]
    sel = entry >= 0
    f_loc, g_loc = _point_neighbour_pairs(entry[sel], face_node[sel], W)
    fc = face_pos[faces]
    b, c, _ = wall_fan_triangles(face_node, face_node_ptr, faces, node_pos, fc)
    y = torch.full((W,), float("inf"), dtype=cell_centre.dtype, device=dev)
    chunk = max(1, (1 << 22) // max(1, b.shape[1]))
    for s in range(0, f_loc.numel(), chunk):
        f, g = f_loc[s:s + chunk], g_loc[s:s + chunk]
        d2 = _triangle_dist2(cell_centre[f][:, None, :], fc[g][:, None, :], b[g], c[g])
        y.scatter_reduce_(0, f, d2.min(dim=1)[0].sqrt(), reduce="amin")
    return y


def _declaring_ranges(integrator):
    """Face ranges [s, e) of every BC with ``wall_distance == "nearWallDist"`` (the union searched)."""
    batch = getattr(integrator, "bcpatch_batch", None)
    fvpatch = getattr(integrator, "fvpatch_batch", None)
    if batch is None or fvpatch is None:
        return []
    out = []
    for i, bcpatch in enumerate(batch.first):
        offset = int(batch.first_patch_idx_offset[i])
        for bc in bcpatch:
            if getattr(bc, "wall_distance", None) == "nearWallDist":
                gp = int(bc.patch_idx) + offset
                s, e = int(fvpatch.start_idx_face[gp]), int(fvpatch.end_idx_face[gp])
                if e > s:
                    out.append((s, e))
    return out


def apply_near_wall_dist(integrator, start_f: int, end_f: int) -> None:
    """Replace ``integrator.wall_distance_face`` by nearWallDist on every declaring patch, once per registration.

    Called by each declaring patch with its own range; the first call after a registration computes all of them
    (the point-neighbour search spans their union), later calls return.  The tensor is REBOUND (clone, wall
    faces replaced), never written in place: an earlier eval in the same forward may hold a view of it in the
    autograd graph.  A registration that rebuilds ``wall_distance_face`` (``register_geometrics``) hands a new
    tensor object, which resets the record."""
    if end_f <= start_f:
        return
    wd = integrator.wall_distance_face
    state = getattr(integrator, "_near_wall_dist_state", None)
    if state is not None and state[0] is wd and (start_f, end_f) in state[1]:
        return
    ranges = _declaring_ranges(integrator)
    if (start_f, end_f) not in ranges:
        ranges.append((start_f, end_f))
    with torch.no_grad():
        # sorted: wall_fan_triangles selects the nodes in stored face order (torch.isin), so the union must be
        # ascending or the per-face triangles land on the wrong faces (several declaring patches)
        faces = torch.cat([torch.arange(s, e, device=wd.device) for s, e in sorted(ranges)])
        owner = integrator.neighbor_cpd_cell[0, faces]
        y = near_wall_dist(integrator.cpd_cell_pos[owner].to(wd.dtype), integrator.face_node, integrator.face_node_ptr,
                           integrator.node_pos.to(wd.dtype), integrator.face_pos.to(wd.dtype), faces)
        new = wd.detach().clone()
        new[faces] = y.reshape(-1, 1).to(wd.dtype)
    integrator.wall_distance_face = new
    integrator._near_wall_dist_state = (new, frozenset(ranges))
