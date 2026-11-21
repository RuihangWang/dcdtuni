"""Serial and decomposed OpenFOAM cases read as ONE global mesh, vectorised, without reconstructParMesh.

``read_case(case_dir)`` reads ``constant/polyMesh`` (serial) or every ``processorN/constant/polyMesh`` (decomposed;
binary or ASCII, with or without ``*ProcAddressing`` files -- they are not used) and returns a :class:`FoamCase`:

* cells: the concatenation of the rank cells in rank order (``cell_proc``, ``cell_local``, ``proc_cell_offsets``);
* internal faces: rank by rank, the rank-internal faces followed by ONE copy of each processor face shared with a
  higher rank (owner on the lower rank, the lower rank's orientation); processor patch pairs ``procBoundaryAtoB`` /
  ``procBoundaryBtoA`` are matched by face order and verified geometrically (equal centres, opposite area vectors);
* boundary faces: the physical patches in the order of the rank-0 boundary file, each the concatenation over ranks of
  its faces (patches with zero faces are kept); cyclic pairs are checked by ``neighbourPatch`` order and geometrically;
* points: the duplicated processor-boundary points merged through the point correspondence of the processor faces
  (the convention is detected per face from the coordinates and reported);
* geometry (face centres / area vectors, cell centres / volumes) computed per rank -- it is local, so it equals the
  serial result -- with OpenFOAM's definitions (``polymesh.PolyMesh.compute_geometry``, bit for bit);
* the maps back to the ranks (``FoamCase.ranks[r].cell_global / face_global / face_flip / point_global``) used by
  ``Post_process/to_foam.py`` to write per-rank fields.

``read_case(..., keep="connected_open")`` (or ``keep_mask=``) drops cells: the connected components over interior
faces (rank-internal, processor pairs and cyclic pairs) that touch no face of an open patch (type ``patch``) -- e.g. a
sealed plenum -- are removed; cells, faces and points are renumbered, the patch table keeps its order (patches may end
with zero faces) and the per-rank maps mark dropped entries with -1.
"""

from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .foam_dict import FoamDict, read_foam_list
from .polymesh import Patch, PolyMesh, _read_boundary, _read_zones

__all__ = ["RankMap", "ProcPair", "FoamCase", "read_rank_mesh", "fast_geometry", "processor_dirs", "read_case", "box_labels", "near_patch_cells", "passage_thickness", "adaptive_box_labels", "coarse_quality", "agglomerate",
           "assemble", "subset", "connected_components", "open_component_mask", "verify_agglomerated_patches"]

log = logging.getLogger(__name__)

OPEN_PATCH_TYPES = ("patch",)


# ----------------------------------------------------------------------------------------------- per-rank reading
def _has_polymesh(d: Path) -> bool:
    pm = d / "constant" / "polyMesh"
    return (pm / "faces").exists() or (pm / "faces.gz").exists()


def processor_dirs(case_dir: str | Path) -> list[Path]:
    """``processorN`` directories holding a polyMesh, sorted by N (N must run 0..n-1)."""
    case_dir = Path(case_dir)
    ds = [p for p in case_dir.iterdir() if p.is_dir() and re.fullmatch(r"processor\d+", p.name) and _has_polymesh(p)]
    ds.sort(key=lambda p: int(p.name[9:]))
    ids = [int(p.name[9:]) for p in ds]
    if ids != list(range(len(ids))):
        raise ValueError(f"{case_dir}: processor directories are not processor0..processor{len(ids) - 1}: {ids}")
    return ds


def fast_geometry(m: PolyMesh, n_cells: int | None = None) -> PolyMesh:
    """``PolyMesh.compute_geometry`` with ``np.add.at`` replaced by ``np.bincount`` over the same summation order
    (identical bits, ~10x faster)."""
    off, fp, pts = m.face_offsets, m.face_points, m.points
    F = off.size - 1
    sizes = np.diff(off)
    if F and sizes.min() < 3:
        raise ValueError(f"{m.path}: face with fewer than three points")
    own, nei = m.owner, m.neighbour
    Fi = nei.shape[0]
    N = int(n_cells) if n_cells is not None else int(max(own.max(initial=-1), nei.max(initial=-1)) + 1)
    if F == 0:
        m.Cf = np.zeros((0, 3)); m.Sf = np.zeros((0, 3)); m.magSf = np.zeros(0)
        m.C = np.zeros((N, 3)); m.V = np.zeros(N)
        return m
    fid = np.repeat(np.arange(F), sizes)
    P = pts[fp]
    est = np.add.reduceat(P, off[:-1], axis=0) / sizes[:, None]
    nxt = np.arange(fp.size) + 1
    nxt[off[1:] - 1] = off[:-1]
    P1 = P[nxt]
    c3 = est[fid]
    n = np.cross(P1 - P, c3 - P)
    a = np.linalg.norm(n, axis=1)
    sumN = np.add.reduceat(n, off[:-1], axis=0)
    sumA = np.add.reduceat(a, off[:-1])
    sumAc = np.add.reduceat(a[:, None] * (P + P1 + c3), off[:-1], axis=0)
    del P, P1, c3, n, a, fid, nxt
    small = sumA <= 1e-300
    Cf = np.where(small[:, None], est, sumAc / (3.0 * np.where(small, 1.0, sumA))[:, None])
    Sf = 0.5 * sumN
    m.Cf, m.Sf = Cf, Sf
    m.magSf = np.linalg.norm(Sf, axis=1)

    both = np.concatenate([own, nei])
    nfc = np.bincount(own, minlength=N) + np.bincount(nei, minlength=N)
    cEst = np.empty((N, 3))
    for k in range(3):  # np.add.at(cEst, own, Cf); np.add.at(cEst, nei, Cf[:Fi]) in one ordered pass
        cEst[:, k] = np.bincount(both, weights=np.concatenate([Cf[:, k], Cf[:Fi, k]]), minlength=N)
    cEst /= nfc[:, None]
    vo = np.einsum("ij,ij->i", Sf, Cf - cEst[own])
    vn = np.einsum("ij,ij->i", Sf[:Fi], cEst[nei] - Cf[:Fi])
    V = np.bincount(own, weights=vo, minlength=N) + np.bincount(nei, weights=vn, minlength=N)
    wo = vo[:, None] * (0.75 * Cf + 0.25 * cEst[own])
    wn = vn[:, None] * (0.75 * Cf[:Fi] + 0.25 * cEst[nei])
    Cc = np.empty((N, 3))
    for k in range(3):
        Cc[:, k] = np.bincount(both, weights=np.concatenate([wo[:, k], wn[:, k]]), minlength=N)
    ok = np.abs(V) > 1e-300
    Cc[ok] /= V[ok, None]
    Cc[~ok] = cEst[~ok]
    m.C = Cc
    m.V = V / 3.0
    return m


def read_rank_mesh(case_dir: str | Path, zones: bool = False, geometry: bool = True) -> PolyMesh:
    """One polyMesh (a serial case or one ``processorN``); geometry by :func:`fast_geometry`."""
    case_dir = Path(case_dir)
    pm = case_dir / "constant" / "polyMesh"
    _, points = read_foam_list(pm / "points", "vector")
    _, (offsets, labels) = read_foam_list(pm / "faces", "face")
    _, owner = read_foam_list(pm / "owner", "label")
    _, neighbour = read_foam_list(pm / "neighbour", "label")
    mesh = PolyMesh(
        points=np.asarray(points, dtype=np.float64).reshape(-1, 3),
        face_offsets=np.asarray(offsets, dtype=np.int64),
        face_points=np.asarray(labels, dtype=np.int64),
        owner=np.asarray(owner, dtype=np.int64),
        neighbour=np.asarray(neighbour, dtype=np.int64),
        patches=_read_boundary(pm / "boundary"),
        cell_zones=_read_zones(pm / "cellZones", "cell") if zones else {},
        face_zones=_read_zones(pm / "faceZones", "face") if zones else {},
        path=pm,
    )
    if mesh.owner.shape[0] != mesh.face_offsets.shape[0] - 1:
        raise ValueError(f"{pm}: {mesh.owner.shape[0]} owners for {mesh.face_offsets.shape[0] - 1} faces")
    if geometry:
        fast_geometry(mesh)
    return mesh


# ------------------------------------------------------------------------------------------------- data classes
@dataclass
class RankMap:
    """How one OpenFOAM rank (or the serial mesh) maps into the global mesh; -1 marks entries dropped by a subset."""
    rank: int
    path: Path                    # the processorN (or serial case) directory
    patches: list[Patch]          # the rank's own boundary, processor patches included
    n_cells: int
    n_faces: int
    n_internal_faces: int
    n_points: int
    cell_global: np.ndarray       # (n_cells,) global cell of each local cell
    face_global: np.ndarray       # (n_faces,) global face of each local face (both sides of a processor face)
    face_flip: np.ndarray         # (n_faces,) bool: True where the local face is the REVERSE of the global one
    point_global: np.ndarray      # (n_points,) global point of each local point

    def patch(self, name: str) -> Patch:
        for p in self.patches:
            if p.name == name:
                return p
        raise KeyError(name)

    def patch_global_faces(self, name: str) -> np.ndarray:
        """Global face ids of the local faces of patch ``name`` on this rank (-1 = dropped)."""
        return self.face_global[self.patch(name).slice]


@dataclass
class ProcPair:
    rank_a: int                   # lower rank (owner side of the global face)
    rank_b: int
    patch_a: str                  # procBoundary<a>to<b>
    patch_b: str                  # procBoundary<b>to<a>
    local_a: np.ndarray           # local face ids on rank_a, in patch order
    local_b: np.ndarray           # local face ids on rank_b, same order (face i <-> face i)
    global_faces: np.ndarray      # the global internal face of each pair (-1 = dropped)


@dataclass
class FoamCase:
    """A global polyMesh with geometry and the decomposition maps."""
    path: Path
    decomposed: bool
    points: np.ndarray            # (P, 3)
    face_offsets: np.ndarray      # (F+1,)
    face_points: np.ndarray       # (sum of face sizes,)
    owner: np.ndarray             # (F,)
    neighbour: np.ndarray         # (Fi,)
    patches: list[Patch]          # physical patches, global start/size, rank-0 order
    Cf: np.ndarray
    Sf: np.ndarray
    magSf: np.ndarray
    C: np.ndarray
    V: np.ndarray
    cell_proc: np.ndarray         # (N,) int32 OpenFOAM rank of every cell (the decomposition: cell -> part)
    cell_local: np.ndarray        # (N,) int64 local cell index on that rank
    proc_cell_offsets: np.ndarray  # (nprocs+1,) cells of rank r are [off[r], off[r+1])
    ranks: list[RankMap]
    proc_pairs: list[ProcPair]
    cyclic_pairs: list[dict]      # {patch_a, patch_b, faces_a, faces_b (global), transform, check}
    source_cells: np.ndarray | None = None  # (N,) global cell id in the unsubsetted mesh (None = no subset)
    report: dict = field(default_factory=dict)

    @property
    def n_cells(self) -> int:
        return int(self.C.shape[0])

    @property
    def n_faces(self) -> int:
        return int(self.owner.shape[0])

    @property
    def n_internal_faces(self) -> int:
        return int(self.neighbour.shape[0])

    @property
    def n_procs(self) -> int:
        return len(self.ranks)

    @property
    def parts(self) -> np.ndarray:
        """The OpenFOAM decomposition as a cell -> part array (for multi-GPU partitions)."""
        return self.cell_proc

    def patch(self, name: str) -> Patch:
        for p in self.patches:
            if p.name == name:
                return p
        raise KeyError(name)

    def face_sizes(self) -> np.ndarray:
        return np.diff(self.face_offsets)

    def summary(self) -> dict:
        types = {}
        for p in self.patches:
            t = types.setdefault(p.type, {"patches": 0, "faces": 0, "zero_face_patches": 0})
            t["patches"] += 1
            t["faces"] += p.size
            t["zero_face_patches"] += int(p.size == 0)
        return {"cells": self.n_cells, "faces": self.n_faces, "internal_faces": self.n_internal_faces,
                "boundary_faces": self.n_faces - self.n_internal_faces, "points": int(self.points.shape[0]),
                "procs": self.n_procs, "processor_faces": int(sum(int((pp.global_faces >= 0).sum())
                                                                     for pp in self.proc_pairs)),
                "patches": len(self.patches), "zero_face_patches": sum(int(p.size == 0) for p in self.patches),
                "patch_types": types}


# --------------------------------------------------------------------------------------------------- helpers
def _flat_index(offsets: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Flat positions (into face_points) of the points of ``faces`` in face order, and the face sizes."""
    faces = np.asarray(faces, dtype=np.int64)
    sizes = offsets[faces + 1] - offsets[faces]
    if faces.size == 0:
        return np.zeros(0, dtype=np.int64), sizes
    starts = np.repeat(offsets[faces], sizes)
    excl = np.repeat(np.cumsum(sizes) - sizes, sizes)
    return starts + (np.arange(int(sizes.sum()), dtype=np.int64) - excl), sizes


def _phys(patches: list[Patch]) -> list[Patch]:
    return [p for p in patches if p.type not in ("processor", "processorCyclic")]


def _point_correspondence(ma: PolyMesh, la: np.ndarray, mb: PolyMesh, lb: np.ndarray, rtol: float):
    """For processor faces la (rank a) <-> lb (rank b): flat point positions of b matching each point of a, and the
    conventions found. Every face is matched from the coordinates: the b-point at A's first point, then the direction."""
    ia, na = _flat_index(ma.face_offsets, la)
    ib, nb = _flat_index(mb.face_offsets, lb)
    if not np.array_equal(na, nb):
        bad = np.flatnonzero(na != nb)
        raise ValueError(f"processor faces with different point counts on the two sides: {bad.size} faces, first "
                         f"local a={la[bad[0]]} ({na[bad[0]]} pts) b={lb[bad[0]]} ({nb[bad[0]]} pts)")
    XA = ma.points[ma.face_points[ia]]
    XB = mb.points[mb.face_points[ib]]
    n = np.repeat(na, na)
    s = np.repeat(np.cumsum(na) - na, na)
    k = np.arange(ia.size, dtype=np.int64) - s
    h = np.repeat(np.sqrt(ma.magSf[la]), na)          # local length scale per point entry
    tol2 = (rtol * h) ** 2

    def ok(idx):
        return np.einsum("ij,ij->i", XA - XB[idx], XA - XB[idx]) <= tol2

    conventions = {}
    cand = {
        "reverseFace (b0=a0, b_k=a_{n-k})": s + (n - k) % n,
        "same order": s + k,
        "full reversal (b_k=a_{n-1-k})": s + (n - 1 - k),
    }
    face_of = np.repeat(np.arange(la.size), na)
    for name, idx in cand.items():
        conventions[name] = int(la.size - np.unique(face_of[~ok(idx)]).size)
    best = max(cand, key=lambda c: conventions[c])
    if conventions[best] == la.size:
        idx = cand[best]
    else:
        # general per-face match: b-position of A's first point, then the orientation
        first = np.repeat(np.cumsum(na) - na, na)            # flat start of each face (same layout on both sides)
        A0 = XA[first]                                       # A's first point, repeated over the face's b points
        d0 = np.einsum("ij,ij->i", XB - A0, XB - A0)
        order = np.lexsort((d0, face_of))
        startpos = np.cumsum(na) - na
        j0 = order[startpos] - startpos                      # in-face index of the b point closest to a0
        j0r = np.repeat(j0, na)
        fwd = s + (j0r + k) % n
        bwd = s + (j0r - k) % n
        okf = ok(fwd)
        badf = np.zeros(la.size, dtype=bool)
        badf[np.unique(face_of[~okf])] = True
        idx = np.where(np.repeat(badf, na), bwd, fwd)
        conventions["general (per-face rotation/direction)"] = int(la.size)
    good = ok(idx)
    if not good.all():
        bad = np.unique(face_of[~good])
        raise ValueError(f"processor face points do not coincide on {bad.size} of {la.size} faces "
                         f"(first local faces a={la[bad[0]]}, b={lb[bad[0]]})")
    dev = np.sqrt(np.einsum("ij,ij->i", XA - XB[idx], XA - XB[idx]).max(initial=0.0))
    return ia, ib[idx], conventions, best if conventions[best] == la.size else "general", float(dev)


def _components(n: int, i: np.ndarray, j: np.ndarray) -> np.ndarray:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components as cc
    g = coo_matrix((np.ones(i.size, dtype=np.int8), (i, j)), shape=(n, n)).tocsr()
    return cc(g, directed=False)[1]


# ---------------------------------------------------------------------------------------------------- assemble
def assemble(meshes: list[PolyMesh], dirs: list[Path], case_dir: Path, decomposed: bool, *, rtol: float = 1e-6,
             check_points: bool = True) -> FoamCase:
    """Assemble per-rank meshes (geometry computed) into one global :class:`FoamCase`."""
    t0 = time.perf_counter()
    R = len(meshes)
    report: dict = {"n_procs": R}
    phys_r = [_phys(m.patches) for m in meshes]      # once per rank: indexed per patch below (4,804 patches x 16 ranks)
    phys0 = phys_r[0]
    for r, m in enumerate(meshes):
        pr = phys_r[r]
        if [(p.name, p.type) for p in pr] != [(p.name, p.type) for p in phys0]:
            raise ValueError(f"{dirs[r]}: physical patch list differs from processor0's")
        if any(p.type == "processorCyclic" for p in m.patches):
            raise NotImplementedError(f"{dirs[r]}: processorCyclic patches (a cyclic pair split across ranks by "
                                      f"decomposePar) are not supported; decompose with preservePatches for the "
                                      f"cyclic patches")
    ncell = np.array([int(max(m.owner.max(initial=-1), m.neighbour.max(initial=-1)) + 1) for m in meshes])
    coff = np.concatenate([[0], np.cumsum(ncell)])
    N = int(coff[-1])

    # processor patch pairs
    procs = {}
    for r, m in enumerate(meshes):
        for p in m.patches:
            if p.type != "processor":
                continue
            me, nb = int(p.dict["myProcNo"]), int(p.dict["neighbProcNo"])
            if me != r:
                raise ValueError(f"{dirs[r]}: patch {p.name} has myProcNo {me}")
            if (r, nb) in procs:
                raise NotImplementedError(f"{dirs[r]}: more than one processor patch towards rank {nb}")
            procs[(r, nb)] = p
    pairs: list[ProcPair] = []
    for (a, b), pa in sorted(procs.items()):
        if a > b:
            continue
        pb = procs.get((b, a))
        if pb is None:
            raise ValueError(f"rank {a} has {pa.name} but rank {b} has no processor patch towards rank {a}")
        if pa.size != pb.size:
            raise ValueError(f"{pa.name} has {pa.size} faces but {pb.name} has {pb.size}")
        ma, mb = meshes[a], meshes[b]
        la = np.arange(pa.start, pa.start + pa.size)
        lb = np.arange(pb.start, pb.start + pb.size)
        dC = np.linalg.norm(ma.Cf[la] - mb.Cf[lb], axis=1)
        dS = np.linalg.norm(ma.Sf[la] + mb.Sf[lb], axis=1)
        h = np.sqrt(ma.magSf[la])
        badC = dC > rtol * h
        badS = dS > rtol * ma.magSf[la]
        if badC.any() or badS.any():
            raise ValueError(f"processor patches {pa.name} (rank {a}) / {pb.name} (rank {b}) do not match by face "
                             f"order: {int(badC.sum())} faces with distinct centres, {int(badS.sum())} with area "
                             f"vectors not opposite (max |dCf|/h = {(dC / h).max():.3e})")
        pairs.append(ProcPair(a, b, pa.name, pb.name, la, lb, np.zeros(0, dtype=np.int64)))
    report["proc_pair_check"] = {f"{pp.patch_a}<->{pp.patch_b}": int(pp.local_a.size) for pp in pairs}

    # global face numbering
    face_global = [np.full(m.n_faces, -1, dtype=np.int64) for m in meshes]
    face_flip = [np.zeros(m.n_faces, dtype=bool) for m in meshes]
    cur = 0
    by_a = {}
    for pp in pairs:
        by_a.setdefault(pp.rank_a, []).append(pp)
    for r, m in enumerate(meshes):
        Fi = m.n_internal_faces
        face_global[r][:Fi] = np.arange(cur, cur + Fi)
        cur += Fi
        for pp in sorted(by_a.get(r, []), key=lambda q: meshes[r].patch(q.patch_a).start):
            g = np.arange(cur, cur + pp.local_a.size)
            face_global[pp.rank_a][pp.local_a] = g
            face_global[pp.rank_b][pp.local_b] = g
            face_flip[pp.rank_b][pp.local_b] = True
            pp.global_faces = g
            cur += g.size
    n_int = cur
    patches = []
    for k, p0 in enumerate(phys0):
        start = cur
        for r, m in enumerate(meshes):
            p = phys_r[r][k]
            face_global[r][p.start:p.start + p.size] = np.arange(cur, cur + p.size)
            cur += p.size
        d = FoamDict(p0.dict)
        d["nFaces"], d["startFace"] = cur - start, start
        patches.append(Patch(p0.name, p0.type, start, cur - start, d))
    F = cur
    for r, m in enumerate(meshes):
        if (face_global[r] < 0).any():
            raise ValueError(f"{dirs[r]}: {(face_global[r] < 0).sum()} faces in no patch")

    # global points
    poff = np.concatenate([[0], np.cumsum([m.points.shape[0] for m in meshes])])
    Ptot = int(poff[-1])
    conv_total: dict = {}
    if pairs:
        ei, ej, devs = [], [], []
        for pp in pairs:
            ma, mb = meshes[pp.rank_a], meshes[pp.rank_b]
            ia, ibm, conv, best, dev = _point_correspondence(ma, pp.local_a, mb, pp.local_b, rtol)
            ei.append(poff[pp.rank_a] + ma.face_points[ia])
            ej.append(poff[pp.rank_b] + mb.face_points[ibm])
            for c, v in conv.items():
                conv_total[c] = conv_total.get(c, 0) + v
            conv_total.setdefault("_used", set()).add(best)
            devs.append(dev)
        labels = _components(Ptot, np.concatenate(ei), np.concatenate(ej))
        uniq, first = np.unique(labels, return_index=True)
        perm = np.argsort(first, kind="stable")
        newid = np.empty(uniq.size, dtype=np.int64)
        newid[uniq[perm]] = np.arange(uniq.size)
        gid = newid[labels]
        allpts = np.concatenate([m.points for m in meshes])
        points = allpts[first[perm]]
        pdev = float(np.linalg.norm(allpts - points[gid], axis=1).max(initial=0.0))
        report["point_merge"] = {"rank_points": Ptot, "global_points": int(uniq.size),
                                 "max_coord_deviation": pdev, "max_face_point_deviation": max(devs),
                                 "conventions_faces": {k: v for k, v in conv_total.items() if k != "_used"},
                                 "convention_used": sorted(conv_total["_used"])}
        point_global = [gid[poff[r]:poff[r + 1]] for r in range(R)]
        del allpts
    else:
        points = meshes[0].points if R == 1 else np.concatenate([m.points for m in meshes])
        point_global = [np.arange(poff[r], poff[r + 1]) for r in range(R)]

    # face arrays: scatter from the defining side
    sizes = np.zeros(F, dtype=np.int64)
    owner = np.empty(F, dtype=np.int64)
    neighbour = np.empty(n_int, dtype=np.int64)
    Cf = np.empty((F, 3)); Sf = np.empty((F, 3)); magSf = np.empty(F)
    defining = []
    for r, m in enumerate(meshes):
        dsel = np.flatnonzero(~face_flip[r])
        g = face_global[r][dsel]
        defining.append((dsel, g))
        sizes[g] = np.diff(m.face_offsets)[dsel]
        owner[g] = m.owner[dsel] + coff[r]
        Cf[g] = m.Cf[dsel]; Sf[g] = m.Sf[dsel]; magSf[g] = m.magSf[dsel]
        Fi = m.n_internal_faces
        neighbour[face_global[r][:Fi]] = m.neighbour + coff[r]
    for pp in pairs:
        neighbour[pp.global_faces] = meshes[pp.rank_b].owner[pp.local_b] + coff[pp.rank_b]
    goff = np.concatenate([[0], np.cumsum(sizes)])
    face_points = np.empty(int(goff[-1]), dtype=np.int64)
    for r, m in enumerate(meshes):
        dsel, g = defining[r]
        src, sz = _flat_index(m.face_offsets, dsel)
        dst, _ = _flat_index(goff, g)
        face_points[dst] = point_global[r][m.face_points[src]]
    if np.any(owner[:n_int] >= neighbour):
        raise ValueError("global internal face with owner >= neighbour")

    C = np.concatenate([m.C for m in meshes]) if R > 1 else meshes[0].C
    V = np.concatenate([m.V for m in meshes]) if R > 1 else meshes[0].V
    cell_proc = np.repeat(np.arange(R, dtype=np.int32), ncell)
    cell_local = np.concatenate([np.arange(c, dtype=np.int64) for c in ncell])
    ranks = [RankMap(r, dirs[r], meshes[r].patches, int(ncell[r]), meshes[r].n_faces, meshes[r].n_internal_faces,
                     int(meshes[r].points.shape[0]), np.arange(coff[r], coff[r + 1], dtype=np.int64),
                     face_global[r], face_flip[r], point_global[r]) for r in range(R)]
    case = FoamCase(Path(case_dir), decomposed, points, goff, face_points, owner, neighbour, patches, Cf, Sf, magSf,
                    C, V, cell_proc, cell_local, coff.astype(np.int64), ranks, pairs, [], None, report)

    # cyclic pairs
    case.cyclic_pairs = _cyclic_pairs(case, meshes, rtol)
    if check_points and R > 1:
        report["coincident_distinct_points"] = _coincident_points(case)
    report["assemble_s"] = time.perf_counter() - t0
    return case


def _cyclic_pairs(case: FoamCase, meshes: list[PolyMesh], rtol: float) -> list[dict]:
    """Pair every cyclic patch with its neighbourPatch by global face order; verify geometrically; if the order
    pairing fails (halves split differently across ranks), re-pair geometrically and permute the second patch."""
    out, done = [], set()
    phys_r = [_phys(m.patches) for m in meshes]
    index = {q.name: k for k, q in enumerate(case.patches)}   # by name: list.index compares whole dataclasses
    for p in case.patches:
        if p.type != "cyclic" or p.name in done:
            continue
        q_name = p.neighbour_patch
        if q_name is None:
            raise ValueError(f"cyclic patch {p.name} has no neighbourPatch")
        q = case.patch(q_name)
        done.update((p.name, q_name))
        if p.size != q.size:
            raise ValueError(f"cyclic {p.name} has {p.size} faces, its neighbourPatch {q_name} {q.size}")
        per_rank = [(pr[index[p.name]].size, pr[index[q.name]].size) for pr in phys_r]
        fa = np.arange(p.start, p.start + p.size)
        fb = np.arange(q.start, q.start + q.size)
        transform = str(p.dict.get("transform", "unknown"))
        # OpenFOAM's own cyclic matching tolerance (cyclicPolyPatch matchTolerance, default 1e-4): the two halves
        # are written from points stored at the case's write precision
        rtol = max(rtol, float(p.dict.get("matchTolerance", 1e-4)))
        chk = _cyclic_check(case, fa, fb, transform, rtol)
        repaired = False
        if not chk["ok"] and transform != "rotational":
            perm = _cyclic_repair(case, fa, fb, chk["separation"], rtol)
            _permute_faces(case, fb, fb[perm])
            chk = _cyclic_check(case, fa, fb, transform, rtol)
            repaired = True
            if not chk["ok"]:
                raise ValueError(f"cyclic pair {p.name}/{q_name}: no geometric match after re-pairing: {chk}")
        elif not chk["ok"]:
            raise ValueError(f"cyclic pair {p.name}/{q_name} fails the geometric check: {chk}")
        chk["ranks_equal_split"] = all(a == b for a, b in per_rank)
        chk["tolerance"] = rtol
        chk["repaired_geometrically"] = repaired
        out.append({"patch_a": p.name, "patch_b": q_name, "faces_a": fa, "faces_b": fb, "transform": transform,
                    "per_rank_sizes": per_rank, "check": chk})
    return out


def _cyclic_check(case: FoamCase, fa, fb, transform: str, rtol: float) -> dict:
    if fa.size == 0:
        return {"ok": True, "n": 0, "separation": [0.0, 0.0, 0.0]}
    Sa, Sb = case.Sf[fa], case.Sf[fb]
    h = np.sqrt(case.magSf[fa])
    dmag = np.abs(case.magSf[fa] - case.magSf[fb]) / case.magSf[fa]
    res = {"n": int(fa.size), "max_rel_area_diff": float(dmag.max())}
    d = case.Cf[fb] - case.Cf[fa]
    if transform == "rotational":
        res["ok"] = bool(dmag.max() <= rtol)
        res["separation"] = [0.0, 0.0, 0.0]
        res["note"] = "rotational: only the area magnitudes are checked"
        return res
    sep = np.median(d, axis=0)
    dS = np.linalg.norm(Sa + Sb, axis=1) / case.magSf[fa]
    dC = np.linalg.norm(d - sep, axis=1) / h
    res.update(separation=sep.tolist(), max_rel_opposite_area=float(dS.max()), max_rel_centre_offset=float(dC.max()))
    res["ok"] = bool(dS.max() <= rtol and dC.max() <= rtol)
    return res


def _cyclic_repair(case: FoamCase, fa, fb, sep, rtol: float) -> np.ndarray:
    from scipy.spatial import cKDTree
    tree = cKDTree(case.Cf[fb] - np.asarray(sep))
    dist, j = tree.query(case.Cf[fa])
    if np.unique(j).size != j.size or (dist > rtol * np.sqrt(case.magSf[fa])).any():
        raise ValueError("cyclic re-pairing by face centres is not one-to-one")
    return j


def _permute_faces(case: FoamCase, slots: np.ndarray, src: np.ndarray) -> None:
    """Face at global position slots[i] becomes the face formerly at src[i] (boundary faces of one patch)."""
    if np.array_equal(slots, src):
        return
    F = case.n_faces
    old_of_new = np.arange(F)
    old_of_new[slots] = src
    new_of_old = np.empty(F, dtype=np.int64)
    new_of_old[old_of_new] = np.arange(F)
    sz = case.face_sizes()[old_of_new]
    idx, _ = _flat_index(case.face_offsets, old_of_new)
    case.face_points = case.face_points[idx]
    case.face_offsets = np.concatenate([[0], np.cumsum(sz)])
    case.owner = case.owner[old_of_new]
    case.Cf, case.Sf, case.magSf = case.Cf[old_of_new], case.Sf[old_of_new], case.magSf[old_of_new]
    for rm in case.ranks:
        ok = rm.face_global >= 0
        rm.face_global[ok] = new_of_old[rm.face_global[ok]]


def _coincident_points(case: FoamCase) -> dict:
    """Distinct global points closer than 1e-9 of the bounding-box diagonal (duplicated baffle points, or a merge
    the processor faces could not see)."""
    from scipy.spatial import cKDTree
    lo, hi = case.points.min(0), case.points.max(0)
    tol = 1e-9 * float(np.linalg.norm(hi - lo))
    pairs = cKDTree(case.points).query_pairs(tol, output_type="ndarray")
    return {"tolerance": tol, "pairs": int(pairs.shape[0])}


# ---------------------------------------------------------------------------------------------------- subset
def connected_components(case: FoamCase) -> np.ndarray:
    """Component label of every cell over interior faces (incl. processor pairs) and cyclic pairs."""
    i = [case.owner[:case.n_internal_faces]]
    j = [case.neighbour]
    for cp in case.cyclic_pairs:
        i.append(case.owner[cp["faces_a"]])
        j.append(case.owner[cp["faces_b"]])
    return _components(case.n_cells, np.concatenate(i), np.concatenate(j))


def open_component_mask(case: FoamCase, open_types=OPEN_PATCH_TYPES) -> tuple[np.ndarray, dict]:
    """Cells of the components that own at least one face of an open patch (type in ``open_types``)."""
    lab = connected_components(case)
    open_cells = [case.owner[p.start:p.start + p.size] for p in case.patches if p.type in open_types and p.size]
    open_labels = np.unique(lab[np.concatenate(open_cells)]) if open_cells else np.zeros(0, dtype=lab.dtype)
    keep = np.isin(lab, open_labels)
    counts = np.bincount(lab)
    info = {"components": int(counts.size), "component_cells": counts.tolist() if counts.size <= 50 else None,
            "open_components": int(open_labels.size), "kept_cells": int(keep.sum()),
            "dropped_cells": int((~keep).sum()),
            "dropped_component_cells": sorted(int(c) for c in counts[np.setdiff1d(np.arange(counts.size),
                                                                                open_labels)])}
    return keep, info


def subset(case: FoamCase, keep: np.ndarray, info: dict | None = None) -> FoamCase:
    """Keep the cells where ``keep`` is True; renumber cells, faces and points (order preserved)."""
    keep = np.asarray(keep, dtype=bool)
    if keep.shape != (case.n_cells,):
        raise ValueError(f"keep mask has shape {keep.shape}, the mesh has {case.n_cells} cells")
    Fi, F = case.n_internal_faces, case.n_faces
    new_cell = np.full(case.n_cells, -1, dtype=np.int64)
    new_cell[keep] = np.arange(int(keep.sum()))
    ko, kn = keep[case.owner[:Fi]], keep[case.neighbour]
    mixed = ko != kn
    if mixed.any():
        raise ValueError(f"{int(mixed.sum())} interior faces join a kept and a dropped cell (first face "
                         f"{int(np.flatnonzero(mixed)[0])}); the subset would cut the mesh")
    for cp in case.cyclic_pairs:
        a, b = keep[case.owner[cp["faces_a"]]], keep[case.owner[cp["faces_b"]]]
        if (a != b).any():
            raise ValueError(f"cyclic pair {cp['patch_a']}/{cp['patch_b']} joins kept and dropped cells")
    fkeep = np.concatenate([ko, keep[case.owner[Fi:]]])
    new_face = np.full(F, -1, dtype=np.int64)
    new_face[fkeep] = np.arange(int(fkeep.sum()))
    kept_faces = np.flatnonzero(fkeep)
    idx, sz = _flat_index(case.face_offsets, kept_faces)
    fp_old = case.face_points[idx]
    used = np.zeros(case.points.shape[0], dtype=bool)
    used[fp_old] = True
    new_point = np.full(case.points.shape[0], -1, dtype=np.int64)
    new_point[used] = np.arange(int(used.sum()))
    ck = np.concatenate([[0], np.cumsum(fkeep)])
    patches = []
    for p in case.patches:
        start, n = int(ck[p.start]), int(ck[p.start + p.size] - ck[p.start])
        d = FoamDict(p.dict)
        d["nFaces"], d["startFace"] = n, start
        patches.append(Patch(p.name, p.type, start, n, d))
    ranks = []
    for rm in case.ranks:
        def remap(arr, table):
            out = np.full(arr.shape, -1, dtype=np.int64)
            ok = arr >= 0
            out[ok] = table[arr[ok]]
            return out
        ranks.append(replace(rm, cell_global=remap(rm.cell_global, new_cell),
                             face_global=remap(rm.face_global, new_face),
                             point_global=remap(rm.point_global, new_point)))
    pairs = [replace(pp, global_faces=np.where(pp.global_faces >= 0, new_face[np.maximum(pp.global_faces, 0)], -1))
             for pp in case.proc_pairs]
    cyc = []
    for cp in case.cyclic_pairs:
        k = fkeep[cp["faces_a"]]
        cyc.append({**cp, "faces_a": new_face[cp["faces_a"][k]], "faces_b": new_face[cp["faces_b"][k]]})
    cell_proc = case.cell_proc[keep]
    counts = np.bincount(cell_proc, minlength=case.n_procs)
    src = np.flatnonzero(keep) if case.source_cells is None else case.source_cells[keep]
    rep = dict(case.report)
    rep["subset"] = {**(info or {}), "kept_cells": int(keep.sum()), "dropped_cells": int((~keep).sum()),
                     "kept_faces": int(fkeep.sum()), "dropped_faces": int((~fkeep).sum()),
                     "kept_points": int(used.sum()), "dropped_points": int((~used).sum()),
                     "zero_face_patches_after": sum(int(p.size == 0) for p in patches),
                     "zero_face_patches_before": sum(int(p.size == 0) for p in case.patches)}
    return FoamCase(case.path, case.decomposed, case.points[used], np.concatenate([[0], np.cumsum(sz)]),
                    new_point[fp_old], new_cell[case.owner[kept_faces]], new_cell[case.neighbour[kn]],
                    patches, case.Cf[kept_faces], case.Sf[kept_faces], case.magSf[kept_faces], case.C[keep],
                    case.V[keep], cell_proc, case.cell_local[keep],
                    np.concatenate([[0], np.cumsum(counts)]).astype(np.int64), ranks, pairs, cyc, src, rep)


# ------------------------------------------------------------------------------------------------ agglomeration
def box_labels(case: FoamCase, h: float, origin=None) -> np.ndarray:
    """Agglomerate label of every cell: the connected pieces (over interior faces) of the cells whose centres fall in
    the same axis-aligned box of edge ``h`` (metres; one float, or three for x, y, z). Boundary faces, among them
    every baffle (cyclic pair) and wall, never join two cells, so no agglomerate reaches across a solid, a porous
    sheet or a patch."""
    h = np.broadcast_to(np.asarray(h, dtype=np.float64), (3,))
    if (h <= 0).any():
        raise ValueError("box edge must be positive")
    o = case.C.min(0) if origin is None else np.asarray(origin, dtype=np.float64)
    ijk = np.floor((case.C - o) / h).astype(np.int64)
    ijk -= ijk.min(0)
    dims = ijk.max(0) + 1
    key = (ijk[:, 0] * dims[1] + ijk[:, 1]) * dims[2] + ijk[:, 2]
    Fi = case.n_internal_faces
    i, j = case.owner[:Fi], case.neighbour
    same = key[i] == key[j]
    return _components(case.n_cells, i[same], j[same])


def _box_key(C: np.ndarray, h, origin) -> np.ndarray:
    h = np.broadcast_to(np.asarray(h, dtype=np.float64), (3,))
    ijk = np.floor((C - origin) / h).astype(np.int64)
    ijk -= ijk.min(0)
    dims = ijk.max(0) + 1
    return (ijk[:, 0] * dims[1] + ijk[:, 1]) * dims[2] + ijk[:, 2]


def coarse_quality(c: FoamCase, max_nonortho_deg: float = 75.0, lsq_condition: float = 0.02,
                   max_volume_ratio: float | None = None, max_aspect: float | None = None) -> np.ndarray:
    """Cells of ``c`` that fail the convexity and orthogonality tests a finite-volume cell must pass: a face centre
    on the wrong side of its cell centre (the centre lies outside the cell there), an interior face whose normal
    is more than ``max_nonortho_deg`` from the line between the two centres, or a baffle (cyclic pair) that does not
    separate two cells on its two sides."""
    Fi = c.n_internal_faces
    o, n = c.owner[:Fi], c.neighbour
    S = c.Sf[:Fi]
    d = c.C[n] - c.C[o]
    cos = (d * S).sum(1) / np.maximum(np.linalg.norm(d, axis=1) * c.magSf[:Fi], 1e-300)
    side_o = ((c.Cf[:Fi] - c.C[o]) * S).sum(1) <= 0
    side_n = ((c.Cf[:Fi] - c.C[n]) * S).sum(1) >= 0
    bad_f = (cos < np.cos(np.radians(max_nonortho_deg))) | side_o | side_n
    bad_b = ((c.Cf[Fi:] - c.C[c.owner[Fi:]]) * c.Sf[Fi:]).sum(1) <= 0
    bad = np.zeros(c.n_cells, dtype=bool)
    bad[o[bad_f]] = True
    bad[n[bad_f]] = True
    bad[c.owner[Fi:][bad_b]] = True
    # the least-squares gradient of every cell must be determined: sum over its neighbour centres and boundary face
    # centres of d d^T well conditioned (a cell left with too few or coplanar stencil points gives a NaN gradient)
    # (unit directions: the inverse-distance-squared weighting of the gradient; with raw d d^T the far points of a
    # small cell beside large ones dominate and a stencil whose near points are nearly coplanar passes -- PERIM,
    # 2026-11-13: the two cells where k ran away had ratios 0.002-0.003, the fine mesh has none below 0.029)
    M = np.zeros((c.n_cells, 3, 3))
    d = c.C[n] - c.C[o]
    d = d / np.maximum(np.linalg.norm(d, axis=1), 1e-300)[:, None]
    dd = d[:, :, None] * d[:, None, :]
    np.add.at(M, o, dd)
    np.add.at(M, n, dd)
    db = c.Cf[Fi:] - c.C[c.owner[Fi:]]
    db = db / np.maximum(np.linalg.norm(db, axis=1), 1e-300)[:, None]
    np.add.at(M, c.owner[Fi:], db[:, :, None] * db[:, None, :])
    ev = np.linalg.eigvalsh(M)
    bad |= ev[:, 0] <= lsq_condition * np.maximum(ev[:, 2], 1e-300)
    # grading: across an interior face the larger cell may hold at most ``max_volume_ratio`` times the smaller's
    # volume (a fine cell left beside a big merged one makes the turbulence rows stiff: INROW_HAC, 2026-11-13);
    # the larger cell is the one marked (it is split)
    if max_volume_ratio:
        Vo, Vn = c.V[o], c.V[n]
        jump = np.maximum(Vo, Vn) > max_volume_ratio * np.minimum(Vo, Vn)
        bad[np.where(Vo >= Vn, o, n)[jump]] = True
    # aspect: the largest normal distance from the cell centre to one of its face planes over the smallest may be at
    # most ``max_aspect`` (a slab one fine cell thick between a rack frame and a server inlet, 0.17 m long, carried
    # the first velocity runaway on PERIM_HAC, 2026-11-13)
    if max_aspect:
        own_all = c.owner
        dn = np.abs(((c.Cf - c.C[own_all]) * c.Sf).sum(1)) / np.maximum(c.magSf, 1e-300)
        dmin = np.full(c.n_cells, np.inf); dmax = np.zeros(c.n_cells)
        np.minimum.at(dmin, own_all, dn); np.maximum.at(dmax, own_all, dn)
        dn_n = np.abs(((c.Cf[:Fi] - c.C[n]) * S).sum(1)) / np.maximum(c.magSf[:Fi], 1e-300)
        np.minimum.at(dmin, n, dn_n); np.maximum.at(dmax, n, dn_n)
        bad |= dmax > max_aspect * np.maximum(dmin, 1e-300)
    # a baffle (cyclic pair) must join two different cells, the side a cell lying behind face a and the side b cell
    # behind face b (an agglomerate that wraps around the baffle's edge couples to itself)
    for cp in c.cyclic_pairs:
        fa, fb = np.asarray(cp["faces_a"]), np.asarray(cp["faces_b"])
        if not fa.size:
            continue
        ca, cb = c.owner[fa], c.owner[fb]
        na = c.Sf[fa] / np.maximum(c.magSf[fa], 1e-300)[:, None]
        wrong = (ca == cb) | (((c.C[cb] - c.C[ca]) * na).sum(1) <= 0)
        bad[ca[wrong]] = True
        bad[cb[wrong]] = True
    return bad


def near_patch_cells(case: FoamCase, patches, radius: float) -> np.ndarray:
    """Cells whose centre lies within ``radius`` of a face centre of one of ``patches`` (names)."""
    from scipy.spatial import cKDTree
    names = set(patches)
    faces = [np.arange(p.start, p.start + p.size) for p in case.patches if p.name in names and p.size]
    if not faces:
        return np.zeros(case.n_cells, dtype=bool)
    d, _ = cKDTree(case.Cf[np.concatenate(faces)]).query(case.C, distance_upper_bound=radius)
    return np.isfinite(d)


def _balance_levels(level: np.ndarray, i: np.ndarray, j: np.ndarray, jump: int) -> np.ndarray:
    """Raise box levels until no two face neighbours differ by more than ``jump`` (graded refinement)."""
    level = level.copy()
    while True:
        li, lj = level[i], level[j]
        ui, uj = lj - jump > li, li - jump > lj
        if not (ui.any() or uj.any()):
            return level
        np.maximum.at(level, i[ui], lj[ui] - jump)
        np.maximum.at(level, j[uj], li[uj] - jump)


def passage_thickness(case: FoamCase) -> np.ndarray:
    """Per fine cell and axis the thickness of the fluid passage it sits in: the distance to the nearest wall face
    whose outward normal is -a plus the distance to the nearest one whose normal is +a (boundary faces of the
    non-coupled patches; nearest by Euclidean distance, an estimate). (n_cells, 3); inf where one side has none.
    Thin layers between large obstacles (PERIM, 2026-11-13: two under-floor pipe slabs split the plenum into
    0.1-0.25 m layers) are where a box joins the fluid of several layers around an obstacle edge."""
    from scipy.spatial import cKDTree
    faces = np.concatenate([np.arange(p.start, p.start + p.size) for p in case.patches
                            if p.size and p.type not in _COUPLED_TYPES] or [np.zeros(0, dtype=np.int64)])
    out = np.full((case.n_cells, 3), np.inf)
    if faces.size == 0:
        return out
    n = case.Sf[faces] / np.maximum(case.magSf[faces], 1e-300)[:, None]
    for ax in range(3):
        d = []
        for sign in (-1.0, 1.0):
            sel = n[:, ax] * sign > 0.9
            if not sel.any():
                d.append(np.full(case.n_cells, np.inf))
                continue
            dist, _ = cKDTree(case.Cf[faces[sel]]).query(case.C, k=1)
            d.append(dist)
        out[:, ax] = d[0] + d[1]
    return out


def adaptive_box_labels(case: FoamCase, h: float, *, max_nonortho_deg: float = 75.0, max_passes: int = 20,
                        merge_boundary: bool = False, near: np.ndarray | None = None, near_h: float | None = None,
                        max_level_jump: int | None = 1, dissolve: bool = True, absorb_ratio: float | None = 0.1,
                        layers: bool = False, max_layer_level: int = 3, max_volume_ratio: float | None = None,
                        max_ratio_level: int = 6, max_aspect: float | None = None, log=None) -> tuple[np.ndarray, dict]:
    """``box_labels`` refined where the coarse cell is not a valid finite-volume cell.

    ``max_volume_ratio`` R (default off): a coarse face whose two cells differ in volume by more than R also raises the
    level of the larger agglomerate's fine cells (up to ``max_ratio_level``), so that the box size steps down towards
    small cells kept at walls and obstacles. Every L1 defect of 2026-11-13 sat in a small cell beside a much larger
    one (volume ratios up to 9e3: the flow-rate patch switch on INROW_CAC, eps decaying to 0 on INROW_OPEN, the k
    production running away on the PERIM families).

    Every fine cell carries a box level L (box edge h / 2**L). Each pass agglomerates the connected cells of one box
    at one level, tests the coarse cells (``coarse_quality``) and raises by one the level of the fine cells of every
    failing agglomerate; a failing coarse cell of a single fine cell keeps its own boundary faces unmerged and has
    its merged neighbours split. With ``max_level_jump`` the levels are then balanced over the fine faces (graded
    refinement: face neighbours differ by at most that many levels; default 1). The 02:30 ablation that turned it
    off (graded INROW_OPEN NaN at step 206) ran before the float16 overflow of the smoother was found; repeated with
    that fixed (04:37), jump 1 lets INROW_HAC meet the stop rule (1158 steps) where the ungraded level stalls, and
    leaves INROW_OPEN unchanged. Stops when every coarse
    cell passes, or after ``max_passes`` raises; coarse cells that still fail are then dissolved into their fine
    cells, which keep their own faces."""
    o = case.C.min(0)
    Fi = case.n_internal_faces
    i, j = case.owner[:Fi], case.neighbour
    level = np.zeros(case.n_cells, dtype=np.int64)
    if near is not None and near_h:
        near = np.asarray(near, dtype=bool)
        level[near] = max(0, int(round(np.log2(h / near_h))))
    if layers:
        # a cell in a passage thinner than a box: boxes of at most half the passage thickness (to max_layer_level)
        th = passage_thickness(case).min(1)
        need = np.where(th < h, np.ceil(np.log2(2.0 * h / np.maximum(th, 1e-9))), 0).astype(np.int64)
        level = np.maximum(level, np.minimum(need, max_layer_level))
    if max_level_jump is not None:
        level = _balance_levels(level, i, j, max_level_jump)
    keep_faces = np.zeros(case.n_cells, dtype=bool)
    passes = []
    k = 0
    while True:
        key = np.zeros(case.n_cells, dtype=np.int64)
        off = 0
        for L in np.unique(level):
            m = level == L
            kk = _box_key(case.C[m], float(h) / 2 ** int(L), o)
            key[m] = kk + off
            off += int(kk.max()) + 1
        same = key[i] == key[j]
        labels = _components(case.n_cells, i[same], j[same])
        c = agglomerate(case, labels, merge_boundary=merge_boundary, keep_faces_of=keep_faces)
        bad_c = coarse_quality(c, max_nonortho_deg, max_aspect=max_aspect)
        sizes = np.bincount(labels)
        lab_c = _coarse_index(case, labels)
        # a failing coarse cell of one fine cell: its merged neighbours are split too (a merged interior face leaves
        # the small cell too few stencil points)
        single_c = bad_c & (np.bincount(lab_c, minlength=c.n_cells) == 1)
        if single_c.any():
            Fc = c.n_internal_faces
            oc, nc_ = c.owner[:Fc], c.neighbour
            nb_c = np.zeros(c.n_cells, dtype=bool)
            nb_c[nc_[single_c[oc]]] = True
            nb_c[oc[single_c[nc_]]] = True
            bad_c = bad_c | nb_c
        bad_fine = bad_c[lab_c] & (sizes[labels] > 1)
        n_ratio = 0
        if max_volume_ratio:
            Vc = np.bincount(lab_c, case.V, minlength=c.n_cells)
            Fc = c.n_internal_faces
            oc, nc_ = c.owner[:Fc], c.neighbour
            big = np.zeros(c.n_cells, dtype=bool)
            hi = Vc[oc] > max_volume_ratio * Vc[nc_]
            lo = Vc[nc_] > max_volume_ratio * Vc[oc]
            big[oc[hi]] = True
            big[nc_[lo]] = True
            ratio_fine = big[lab_c] & (sizes[labels] > 1) & (level < max_ratio_level) & ~bad_fine
            n_ratio = int(ratio_fine.sum())
            bad_fine = bad_fine | ratio_fine
        single_bad = bad_c[lab_c] & (sizes[labels] == 1) & ~keep_faces if merge_boundary else np.zeros_like(keep_faces)
        passes.append({"pass": k, "cells": c.n_cells, "bad_cells": int(bad_c.sum()),
                       "fine_cells_to_split": int(bad_fine.sum()), "unmerged_cells_added": int(single_bad.sum()),
                       "max_level": int(level.max()), "ratio_split_fine_cells": n_ratio})
        if log:
            log(f"agglomerate pass {k}: {c.n_cells} cells, {int(bad_c.sum())} fail, "
                f"{int(bad_fine.sum())} fine cells split again ({n_ratio} for the volume ratio), "
                f"{int(single_bad.sum())} keep their faces")
        keep_faces |= single_bad
        split = bad_fine.any() and k < max_passes
        if not split and not single_bad.any():
            break
        if split:
            k += 1
            level[bad_fine] += 1
            if max_level_jump is not None:
                level = _balance_levels(level, i, j, max_level_jump)
    if absorb_ratio:
        labels, absorbed = _absorb_small(case, labels, keep_faces, absorb_ratio, merge_boundary, max_nonortho_deg, log,
                                           max_aspect=max_aspect)
        c = agglomerate(case, labels, merge_boundary=merge_boundary, keep_faces_of=keep_faces)
        bad_c = coarse_quality(c, max_nonortho_deg, max_aspect=max_aspect)
        sizes = np.bincount(labels)
        lab_c = _coarse_index(case, labels)
        passes.append({"pass": "absorb", "cells": c.n_cells, "bad_cells": int(bad_c.sum()), "absorbed": absorbed})
    # after the pass limit: a coarse cell that still fails is dissolved into its fine cells, which keep their own
    # faces (the fine mesh is a valid finite-volume mesh, so this always ends with every coarse cell passing)
    dissolved = 0
    for _ in range(4 if dissolve else 0):
        bad_any = bad_c[lab_c] & ~(keep_faces & (sizes[labels] == 1))
        if not bad_any.any():
            break
        nxt = int(labels.max()) + 1
        idx = np.nonzero(bad_any)[0]
        labels = labels.copy()
        labels[idx] = nxt + np.arange(idx.size)
        keep_faces[idx] = True
        dissolved += int(idx.size)
        c = agglomerate(case, labels, merge_boundary=merge_boundary, keep_faces_of=keep_faces)
        bad_c = coarse_quality(c, max_nonortho_deg, max_aspect=max_aspect)
        sizes = np.bincount(labels)
        lab_c = _coarse_index(case, labels)
        if log:
            log(f"agglomerate: {idx.size} fine cells of failing coarse cells dissolved; {c.n_cells} cells, "
                f"{int(bad_c.sum())} fail")
    passes.append({"pass": "dissolve", "cells": c.n_cells, "bad_cells": int(bad_c.sum()),
                   "dissolved_fine_cells": dissolved})
    return labels, {"box_m": float(h), "max_nonortho_deg": max_nonortho_deg, "merge_boundary": bool(merge_boundary),
                    "max_level_jump": max_level_jump, "keep_faces_of": keep_faces,
                    "near_cells": int(near.sum()) if near is not None and near_h else 0,
                    "near_box_m": float(near_h) if near is not None and near_h else None,
                    "passes": passes, "remaining_bad_cells": passes[-1]["bad_cells"]}


def _absorb_small(case: FoamCase, labels: np.ndarray, keep_faces: np.ndarray, ratio: float, merge_boundary: bool,
                  max_nonortho_deg: float, log=None, rounds: int = 3, max_aspect: float | None = None) -> tuple[np.ndarray, int]:
    """Merge every agglomerate whose volume is below ``ratio`` times its largest face neighbour's into the neighbour
    it shares the most face area with; a merge whose coarse cell then fails ``coarse_quality`` is undone. A small
    agglomerate beside much larger ones (PERIM, 2026-11-13: four fine cells beside 0.4 m cells behind a rack, one
    fine cell in a tile gap) gives skewed least-squares gradients and the k production runs away there."""
    Fi = case.n_internal_faces
    i, j = case.owner[:Fi], case.neighbour
    total = 0
    for _ in range(rounds):
        _, L = np.unique(labels, return_inverse=True)
        N = int(L.max()) + 1
        vol = np.bincount(L, case.V, minlength=N)
        x = L[i] != L[j]
        a, b, w = L[i][x], L[j][x], case.magSf[:Fi][x]
        a, b, w = np.r_[a, b], np.r_[b, a], np.r_[w, w]
        vmax = np.zeros(N)
        np.maximum.at(vmax, a, vol[b])
        small = vol < ratio * vmax
        if not small.any():
            break
        key = a * N + b
        uk, inv = np.unique(key, return_inverse=True)
        area = np.bincount(inv, w)
        pa, pb = uk // N, uk % N
        ok = small[pa] & ~small[pb]                       # absorb into a neighbour that is not itself absorbed
        if not ok.any():
            break
        pa, pb, area = pa[ok], pb[ok], area[ok]
        o = np.lexsort((-area, pa))
        pa, pb = pa[o], pb[o]
        first = np.r_[True, pa[1:] != pa[:-1]]
        target = np.arange(N)
        target[pa[first]] = pb[first]
        ids = np.arange(N)
        # undo until every coarse cell passes: a failing coarse cell that received merges loses them; one next to a
        # receiving cell (its faces changed) makes that neighbour lose its merges
        for _undo in range(6):
            moved = target != ids
            if not moved.any():
                break
            new = target[L]
            c = agglomerate(case, new, merge_boundary=merge_boundary, keep_faces_of=keep_faces)
            bad_c = coarse_quality(c, max_nonortho_deg, max_aspect=max_aspect)
            if not bad_c.any():
                break
            ci = _coarse_index(case, new)                    # fine cell -> coarse cell
            recv = np.zeros(N, dtype=bool)
            recv[target[moved]] = True
            # coarse cell -> one of its fine cells' (pre-merge) target label
            tl = np.full(c.n_cells, -1, dtype=np.int64)
            tl[ci] = target[L]
            bad_t = set(tl[bad_c].tolist())
            Fc = c.n_internal_faces
            oc, nc_ = c.owner[:Fc], c.neighbour
            nb_bad = np.r_[tl[nc_[bad_c[oc]]], tl[oc[bad_c[nc_]]]]
            undo = np.zeros(N, dtype=bool)
            for t in bad_t | set(nb_bad.tolist()):
                if t >= 0 and recv[t]:
                    undo[t] = True
            if not undo.any():
                break
            revert = undo[target] & moved
            target[revert] = ids[revert]
        n_ok = int((target != ids).sum())
        if log:
            log(f"absorb: {int(small.sum())} small agglomerates, {n_ok} merged")
        if n_ok == 0:
            break
        labels = target[L]
        total += n_ok
    return labels, total


def _coarse_index(case: FoamCase, labels: np.ndarray) -> np.ndarray:
    """The coarse cell index ``agglomerate`` gives each fine cell (its numbering: rank of the first fine cell, then
    that cell's index)."""
    uniq, first = np.unique(labels, return_index=True)
    lab_dense = np.searchsorted(uniq, labels)
    order = np.lexsort((first, case.cell_proc[first]))
    rank_of = np.empty(uniq.size, dtype=np.int64)
    rank_of[order] = np.arange(uniq.size)
    return rank_of[lab_dense]


_COUPLED_TYPES = ("cyclic", "cyclicAMI", "cyclicSlip", "processor", "empty", "wedge")


def _merge_faces(case: FoamCase, faces: np.ndarray, grp: np.ndarray, G: int, flip: np.ndarray):
    """Merge ``faces`` by group ``grp`` (0..G-1): area vector = sum (each face negated where ``flip``), centre =
    area-weighted mean, points: a single-member group keeps its face's point order (reversed where flipped), a merged
    one takes its members' distinct points. Returns (Sf, Cf, magSf, point group, point)."""
    sgn = np.where(flip, -1.0, 1.0)
    Sf = case.Sf[faces] * sgn[:, None]
    Sf_c = np.stack([np.bincount(grp, Sf[:, k], minlength=G) for k in range(3)], 1)
    w = case.magSf[faces]
    Cf_c = np.stack([np.bincount(grp, w * case.Cf[faces][:, k], minlength=G) for k in range(3)], 1) \
        / np.maximum(np.bincount(grp, w, minlength=G), 1e-300)[:, None]
    magSf_c = np.linalg.norm(Sf_c, axis=1)
    idx, sz = _flat_index(case.face_offsets, faces)
    member = np.repeat(np.arange(faces.size), sz)
    fpts = case.face_points[idx]
    single = np.bincount(grp, minlength=G)[grp] == 1
    sm = single[member]
    rows = []
    if sm.any():
        pos = np.arange(idx.size) - np.repeat(np.cumsum(sz) - sz, sz)
        key = np.where(flip[member], -pos, pos)[sm]
        g_s = grp[member[sm]]
        o = np.lexsort((key, g_s))
        rows.append((g_s[o], fpts[sm][o]))
    if (~sm).any():
        P1 = case.points.shape[0] + 1
        u = np.unique(grp[member[~sm]] * P1 + fpts[~sm])
        rows.append((u // P1, u % P1))
    g_all = np.concatenate([r[0] for r in rows]) if rows else np.zeros(0, dtype=np.int64)
    p_all = np.concatenate([r[1] for r in rows]) if rows else np.zeros(0, dtype=np.int64)
    o = np.argsort(g_all, kind="stable")
    return Sf_c, Cf_c, magSf_c, g_all[o], p_all[o]


def agglomerate(case: FoamCase, labels: np.ndarray, info: dict | None = None, *,
                merge_boundary: bool = False, keep_faces_of: np.ndarray | None = None,
                merge_cyclic: bool = False, coplanar_interior: bool = False) -> FoamCase:
    """The coarse mesh whose cells are the agglomerates ``labels`` (one integer per cell; each agglomerate must be
    connected over interior faces, as ``box_labels`` makes them).

    * cells: volume = sum, centre = volume-weighted mean of the fine centres (the centroid of the union); numbered
      rank by rank (an agglomerate belongs to the rank of its first fine cell) so the decomposition stays contiguous;
    * interior faces: fine faces inside an agglomerate are dropped; the fine faces between the same two agglomerates
      become ONE face (area vector = sum, centre = area-weighted mean, points = their distinct points), oriented
      from the lower to the higher coarse cell and sorted (owner, neighbour) as OpenFOAM orders them; a pair whose
      faces' summed area vector keeps less than half of their total area (contact on opposite sides) gets one face
      per dominant normal direction instead;
    * boundary faces: kept face for face, renumbered after the interior; with ``merge_boundary`` the faces of one
      patch with the same coarse owner, the same dominant normal direction (+-x, +-y, +-z) and the same plane (to
      1 mm along that axis) become one face
      (other coupled patches always stay face for face, so their pairing holds); with ``merge_cyclic`` as well a
      cyclic pair's faces merge the same way, both halves by one grouping -- the coarse owners of the two sides, the
      dominant normal direction and the plane of the first half -- so face i of one half still pairs with face i of
      the other; with ``coplanar_interior`` the fine faces between two agglomerates merge only where they are
      coplanar (one face per dominant normal direction and plane, to 1 mm, as on the boundary), so a stepped contact
      keeps one face per step and every coarse face is the exact union of its fine faces;
    * the per-rank maps send every fine cell to its agglomerate, so a field written through them is the piecewise-
      constant prolongation; a fine face merged into a coarse face maps to that face, a dropped one to -1.

    The result is an ordinary :class:`FoamCase`; ``report['agglomerate']`` records the reduction."""
    labels = np.asarray(labels, dtype=np.int64)
    if labels.shape != (case.n_cells,):
        raise ValueError(f"labels have shape {labels.shape}, the mesh has {case.n_cells} cells")
    Fi, F = case.n_internal_faces, case.n_faces
    # coarse numbering: by rank of the first fine cell, then by that cell's index
    _, first = np.unique(labels, return_index=True)          # first fine cell of each label (labels sorted)
    uniq = np.unique(labels)
    lab_dense = np.searchsorted(uniq, labels)
    order = np.lexsort((first, case.cell_proc[first]))
    rank_of = np.empty(uniq.size, dtype=np.int64)
    rank_of[order] = np.arange(uniq.size)
    lab = rank_of[lab_dense]
    Nc = int(uniq.size)
    first_c = np.empty(Nc, dtype=np.int64)
    first_c[rank_of] = first
    # connectivity check: each agglomerate one component over its own interior faces
    i, j = case.owner[:Fi], case.neighbour
    inside = lab[i] == lab[j]
    comp = _components(case.n_cells, i[inside], j[inside])
    if np.unique(comp).size != Nc:
        raise ValueError(f"{np.unique(comp).size - Nc} agglomerates are not connected over interior faces")
    V = np.bincount(lab, case.V, minlength=Nc)
    C = np.stack([np.bincount(lab, case.V * case.C[:, k], minlength=Nc) for k in range(3)], 1) / V[:, None]
    # interior faces between agglomerates: one face per (owner, neighbour) pair
    keep = np.flatnonzero(~inside)
    lo, ln = lab[i[keep]], lab[j[keep]]
    flip = lo > ln
    pair = np.where(flip, ln, lo) * Nc + np.where(flip, lo, ln)
    # where one agglomerate touches another on opposite sides (a cell in a gap between the parts of a wrapping
    # neighbour; PERIM_HAC, 2026-11-13) one summed face cancels those areas and misstates the contact: a pair whose
    # summed area vector keeps less than half of its faces' total area is split by dominant normal direction
    # (oriented owner -> neighbour); a stepped contact (0.71 for a 45 degree staircase) stays one face
    n_k = case.Sf[keep] * np.where(flip, -1.0, 1.0)[:, None]
    _, pg = np.unique(pair, return_inverse=True)
    net = np.stack([np.bincount(pg, n_k[:, k]) for k in range(3)], 1)
    tot = np.bincount(pg, case.magSf[keep])
    cancel = (np.linalg.norm(net, axis=1) < 0.5 * tot)[pg]
    if os.environ.get("LPCNS_DC_AGGLOMERATE_SPLIT_DIR", "0") == "1":
        # every coarse interior face of one dominant normal direction only (an L-shaped or partly wrapping contact
        # keeps one face per side: PERIM_HAC k runaway beside a two-cell agglomerate left with 4 faces, 2026-11-13)
        cancel = np.ones_like(cancel)
    ax_k = np.abs(n_k).argmax(1)
    orient_k = np.where(cancel, 1 + ax_k * 2 + (n_k[np.arange(keep.size), ax_k] < 0), 0)
    if coplanar_interior:
        # a stepped contact merged into one face states its area by the net vector (Base201 L1: a rack-frame sliver
        # cell with 62 fine faces left with one interior face at 66-75 degrees and a 257 m/s runaway, 2026-11-18)
        orient_k = 1 + ax_k * 2 + (n_k[np.arange(keep.size), ax_k] < 0)
        plane = np.round(case.Cf[keep][np.arange(keep.size), ax_k] / 1e-3).astype(np.int64)
        _, pid = np.unique(plane, return_inverse=True)
        _, sub_k = np.unique(orient_k * (int(pid.max()) + 1) + pid, return_inverse=True)
    else:
        sub_k = orient_k
    nsub = int(sub_k.max()) + 1 if sub_k.size else 1
    grp_keys, grp = np.unique(pair * nsub + sub_k, return_inverse=True)
    G = int(grp_keys.size)
    own_c, nei_c = (grp_keys // nsub) // Nc, (grp_keys // nsub) % Nc   # unique() sorted them (owner, neighbour)
    Sf_c, Cf_c, magSf_c, g_pts, p_pts = _merge_faces(case, keep, grp, G, flip)
    # boundary faces: patch by patch; faces of one patch, one coarse owner and one dominant normal direction merge
    # into one face when ``merge_boundary`` (cyclic and other coupled patches stay face for face: their pairing)
    owner_b, Sf_b, Cf_b, magSf_b, sizes_b, pts_b = [], [], [], [], [], []
    cyc_group = {}
    if merge_boundary and merge_cyclic:
        for cp in case.cyclic_pairs:
            fa, fb = np.asarray(cp["faces_a"]), np.asarray(cp["faces_b"])
            if fa.size == 0:
                continue
            n = case.Sf[fa] / np.maximum(case.magSf[fa], 1e-300)[:, None]
            ax = np.abs(n).argmax(1)
            orient = ax * 2 + (n[np.arange(fa.size), ax] < 0)
            plane = np.round(case.Cf[fa][np.arange(fa.size), ax] / 1e-3).astype(np.int64)
            _, pid = np.unique(plane, return_inverse=True)
            key = ((lab[case.owner[fa]] * Nc + lab[case.owner[fb]]) * 6 + orient) * (int(pid.max()) + 1) + pid
            if keep_faces_of is not None:
                kf = np.asarray(keep_faces_of, dtype=bool)
                key = np.where(kf[case.owner[fa]] | kf[case.owner[fb]], -1 - np.arange(fa.size), key)
            _, gi = np.unique(key, return_inverse=True)
            cyc_group[cp["patch_a"]] = gi
            cyc_group[cp["patch_b"]] = gi
    new_face = np.full(F, -1, dtype=np.int64)
    new_face[keep] = grp
    patches = []
    nb = 0
    for p in case.patches:
        faces = np.arange(p.start, p.start + p.size)
        if p.name in cyc_group and p.size:
            gi = cyc_group[p.name]
            ng = int(gi.max()) + 1
            S_, C_, M_, gp, pp = _merge_faces(case, faces, gi, ng, np.zeros(faces.size, dtype=bool))
            first_f = np.zeros(ng, dtype=np.int64)
            first_f[gi[::-1]] = np.arange(faces.size)[::-1]
            owner_b.append(lab[case.owner[faces[first_f]]])
        elif merge_boundary and p.size and p.type not in _COUPLED_TYPES:
            n = case.Sf[faces] / np.maximum(case.magSf[faces], 1e-300)[:, None]
            ax = np.abs(n).argmax(1)
            orient = ax * 2 + (n[np.arange(faces.size), ax] < 0)
            # coplanar only: the plane offset along the dominant axis, to 1 mm (a stepped wall stays several faces)
            plane = np.round(case.Cf[faces][np.arange(faces.size), ax] / 1e-3).astype(np.int64)
            _, pid = np.unique(plane, return_inverse=True)
            okey = (lab[case.owner[faces]] * 6 + orient) * (int(pid.max()) + 1) + pid
            if keep_faces_of is not None:
                # faces of the listed fine cells are not merged: each its own group
                kf = np.asarray(keep_faces_of, dtype=bool)[case.owner[faces]]
                okey = np.where(kf, -1 - np.arange(faces.size), okey)
            gk, gi = np.unique(okey, return_inverse=True)
            ng = int(gk.size)
            S_, C_, M_, gp, pp = _merge_faces(case, faces, gi, ng, np.zeros(faces.size, dtype=bool))
            first_f = np.zeros(ng, dtype=np.int64)
            first_f[gi[::-1]] = np.arange(faces.size)[::-1]
            owner_b.append(lab[case.owner[faces[first_f]]])
        else:
            ng = int(p.size)
            gi = np.arange(ng)
            S_, C_, M_ = case.Sf[faces], case.Cf[faces], case.magSf[faces]
            idx, sz = _flat_index(case.face_offsets, faces)
            gp, pp = np.repeat(np.arange(ng), sz), case.face_points[idx]
            owner_b.append(lab[case.owner[faces]])
        new_face[faces] = G + nb + gi
        Sf_b.append(S_)
        Cf_b.append(C_)
        magSf_b.append(M_)
        sizes_b.append(np.bincount(gp, minlength=ng))
        pts_b.append(pp)
        d = FoamDict(p.dict)
        d["nFaces"], d["startFace"] = ng, G + nb
        patches.append(Patch(p.name, p.type, G + nb, ng, d))
        nb += ng
    cat = lambda xs, shape: np.concatenate(xs) if xs else np.zeros(shape)  # noqa: E731
    face_points = np.concatenate([p_pts, cat(pts_b, (0,)).astype(np.int64)])
    sizes = np.concatenate([np.bincount(g_pts, minlength=G), cat(sizes_b, (0,)).astype(np.int64)])
    offsets = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    owner = np.concatenate([own_c, cat(owner_b, (0,)).astype(np.int64)])
    Cf = np.concatenate([Cf_c, cat(Cf_b, (0, 3))])
    Sf_all = np.concatenate([Sf_c, cat(Sf_b, (0, 3))])
    magSf = np.concatenate([magSf_c, cat(magSf_b, (0,))])

    def remap(arr, table):
        out = np.full(arr.shape, -1, dtype=np.int64)
        ok = arr >= 0
        out[ok] = table[arr[ok]]
        return out

    ranks = [replace(rm, cell_global=remap(rm.cell_global, lab), face_global=remap(rm.face_global, new_face))
             for rm in case.ranks]
    pairs = [replace(pp, global_faces=np.where(pp.global_faces >= 0, new_face[np.maximum(pp.global_faces, 0)], -1))
             for pp in case.proc_pairs]
    cyc = [{**cp, "faces_a": new_face[cp["faces_a"]], "faces_b": new_face[cp["faces_b"]]} for cp in case.cyclic_pairs]
    cell_proc = case.cell_proc[first_c]
    pc = np.bincount(cell_proc, minlength=case.n_procs)
    cell_local = np.arange(Nc) - np.repeat(np.cumsum(pc) - pc, pc)
    src = None if case.source_cells is None else case.source_cells[first_c]
    rep = dict(case.report)
    rep["agglomerate"] = {**(info or {}), "fine_cells": case.n_cells, "cells": Nc,
                          "fine_internal_faces": Fi, "internal_faces": G, "fine_boundary_faces": F - Fi,
                          "boundary_faces": nb, "merge_boundary": bool(merge_boundary),
                          "merge_cyclic": bool(merge_boundary and merge_cyclic),
                          "merged_fine_faces": int(keep.size), "dropped_fine_faces": int(inside.sum()),
                          "fine_cells_per_cell_max": int(np.bincount(lab).max()),
                          "volume_ratio_max_min": float(V.max() / V.min())}
    return FoamCase(case.path, case.decomposed, case.points, offsets, face_points, owner, nei_c, patches, Cf, Sf_all,
                    magSf, C, V, cell_proc, cell_local.astype(np.int64),
                    np.concatenate([[0], np.cumsum(pc)]).astype(np.int64), ranks, pairs, cyc, src, rep)




def merge_faces(case: FoamCase, *, cyclic: bool = True) -> FoamCase:
    """The same cells with their faces merged (``agglomerate`` with every cell its own agglomerate): the fine faces
    between the same two cells become one face per plane, and the coplanar faces of one patch on one cell (with ``cyclic`` also
    those of the cyclic pairs, both halves alike) one face.  For a coarse level whose cells were merged but whose
    faces were kept (a dcCart coarse mesh: a valid OpenFOAM mesh needs planar faces, so its coarse cells keep the fine
    faces of their sides), this makes the face count follow the cells; the cell volumes and centres, every patch and
    its area are unchanged."""
    return agglomerate(case, np.arange(case.n_cells, dtype=np.int64), {"mode": "faces"}, merge_boundary=True,
                       merge_cyclic=cyclic, coplanar_interior=True)


def verify_agglomerated_patches(
    fine_case: FoamCase,
    agg_case: FoamCase,
    *,
    rel_area_tol: float = 1e-6,
) -> dict:
    """Verify every non-empty physical patch in ``fine_case`` survives in ``agg_case`` with conserved area and type."""
    fine_phys = [p for p in fine_case.patches if not p.name.startswith("procBoundary")]
    agg_phys = {p.name: p for p in agg_case.patches if not p.name.startswith("procBoundary")}

    dropped_patches: list[str] = []
    empty_patches_created: list[str] = []
    type_mismatches: list[dict] = []
    area_violations: list[dict] = []
    max_rel_area_error = 0.0
    non_empty_count = 0

    for fp in fine_phys:
        if int(fp.size) <= 0:
            continue
        non_empty_count += 1
        fine_area = float(np.asarray(fine_case.magSf[fp.slice], dtype=np.float64).sum())
        ap = agg_phys.get(fp.name)
        if ap is None:
            dropped_patches.append(fp.name)
            area_violations.append({"name": fp.name, "fine_area": fine_area, "agg_area": 0.0, "rel_error": 1.0})
            continue
        if int(ap.size) <= 0:
            empty_patches_created.append(fp.name)
        if fp.type != ap.type:
            type_mismatches.append({"name": fp.name, "fine_type": fp.type, "agg_type": ap.type})
        agg_area = float(np.asarray(agg_case.magSf[ap.slice], dtype=np.float64).sum())
        rel_err = abs(agg_area - fine_area) / max(fine_area, 1e-30)
        if rel_err > max_rel_area_error:
            max_rel_area_error = rel_err
        if rel_err > rel_area_tol:
            area_violations.append(
                {"name": fp.name, "fine_area": fine_area, "agg_area": agg_area, "rel_error": float(rel_err)}
            )

    fine_cells = int(fine_case.n_cells)
    agg_cells = int(agg_case.n_cells)
    ok = (
        not dropped_patches
        and not empty_patches_created
        and not type_mismatches
        and not area_violations
    )
    return {
        "ok": ok,
        "fine_cells": fine_cells,
        "agg_cells": agg_cells,
        "compression_ratio": float(fine_cells) / max(agg_cells, 1),
        "patch_count": non_empty_count,
        "dropped_patches": dropped_patches,
        "empty_patches_created": empty_patches_created,
        "type_mismatches": type_mismatches,
        "max_rel_area_error": float(max_rel_area_error),
        "area_violations": area_violations,
    }


# ---------------------------------------------------------------------------------------------------- entry point
def read_case(case_dir: str | Path, *, layout: str = "auto", keep: str | None = None,
              keep_mask: np.ndarray | None = None, workers: int | None = None, rtol: float = 1e-6,
              check_points: bool = True, open_types=OPEN_PATCH_TYPES) -> FoamCase:
    """Read a serial or decomposed OpenFOAM case into a global :class:`FoamCase`.

    layout: "auto" (serial ``constant/polyMesh`` if present, else ``processorN``), "serial" or "decomposed".
    keep: None or "connected_open" (drop the components touching no open patch); keep_mask: a cell mask (on the
    unsubsetted global numbering), applied after ``keep``.
    workers: threads for the per-rank reads (default min(n_ranks, 16)).
    """
    t0 = time.perf_counter()
    case_dir = Path(case_dir)
    if layout == "auto":
        layout = "serial" if _has_polymesh(case_dir) else "decomposed"
    if layout == "serial":
        dirs = [case_dir]
    elif layout == "decomposed":
        dirs = processor_dirs(case_dir)
        if not dirs:
            raise FileNotFoundError(f"{case_dir}: no processorN/constant/polyMesh")
    else:
        raise ValueError(f"layout must be auto, serial or decomposed, got {layout!r}")
    nw = workers or min(len(dirs), 16)
    if nw > 1 and len(dirs) > 1:
        with ThreadPoolExecutor(nw) as ex:
            meshes = list(ex.map(read_rank_mesh, dirs))
    else:
        meshes = [read_rank_mesh(d) for d in dirs]
    t1 = time.perf_counter()
    case = assemble(meshes, dirs, case_dir, layout == "decomposed", rtol=rtol, check_points=check_points)
    del meshes
    case.report["read_ranks_s"] = t1 - t0
    if keep is not None:
        if keep != "connected_open":
            raise ValueError(f"keep must be None or 'connected_open', got {keep!r}")
        mask, info = open_component_mask(case, open_types)
        info["rule"] = f"connected_open (open patch types {list(open_types)})"
        case = subset(case, mask, info)
    if keep_mask is not None:
        km = np.asarray(keep_mask, dtype=bool)
        if case.source_cells is not None:
            km = km[case.source_cells]
        case = subset(case, km, {"rule": "keep_mask"})
    case.report["total_s"] = time.perf_counter() - t0
    log.info("read_case %s: %s", case_dir, case.summary())
    return case
