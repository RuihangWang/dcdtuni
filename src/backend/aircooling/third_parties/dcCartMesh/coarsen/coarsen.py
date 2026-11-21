"""dcCart coarsening: a patch- and geometry-preserving coarse mesh from a fine dcCart mesh.

A coarse cell is a connected agglomerate of fine cells. The fine cells are grouped by an axis-aligned box lattice of
edge ``h`` (one agglomerate per connected part of a box, never across a cellZone), and agglomerates far smaller than
the target are absorbed into the neighbour they share the most face area with. ``h`` is chosen by bisection so that
the coarse mesh has the requested share of the fine cell count (``ratio``: about 100 for an L1 mesh, about 1000 for an
L0 / zonal mesh).

What is kept, face for face and in order:

* every boundary face, so every patch keeps its faces, its area and its shape, and the room keeps its geometry
  exactly (walls, racks, server inlets and outlets, blanking panels, perforated tiles, the cyclic pairs of the porous
  baffles);
* every processor-boundary face, so the coarse processor meshes match each other as the fine ones did and the
  coarse mesh is written decomposed, rank by rank, without a reconstruction;
* every fine interior face between two different agglomerates, so the result is an ordinary OpenFOAM polyMesh
  (two coarse cells may share several faces; a solver may merge them).

The fine interior faces inside an agglomerate, and the points only they used, are dropped. Each rank is coarsened on
its own: an agglomerate never crosses a processor boundary.

Written next to the coarse mesh: ``dccart-coarsen.json`` (the receipt: settings, counts, patch audit, quality) and
``coarsen/fineToCoarse_rank<N>.npy`` (the coarse cell of every fine cell, per rank).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from backend.aircooling.runner.polymesh_io import (
    _binary_list_at, _payload_start, foam_header_info, read_all_faces, read_label_list, read_points)

FORMAT = "dcdtuni.dccart-coarsen/v1"
LEVEL_RATIOS = {"L1": 100.0, "L0": 1000.0}
# L1 (the coarse dcRANS mesh): every merged interior face a valid finite-volume face (graded boxes split where not);
# L0 (the zonal network): well-mixed zones, plain connected boxes
LEVEL_OPTIONS = {"L1": {"max_nonortho_deg": 75.0, "passes": 8, "dissolve": True, "boundary_test": False,
                        "absorb": 0.25},
                 "L0": {"max_nonortho_deg": 90.0, "passes": 0, "dissolve": False, "boundary_test": False,
                        "absorb": 0.5}}
COUPLED = ("processor", "processorCyclic")


# ------------------------------------------------------------------------------------------------- reading
@dataclass
class RankMesh:
    path: Path                      # <case>/processorN/constant/polyMesh (or <case>/constant/polyMesh)
    points: np.ndarray              # (P, 3)
    offsets: np.ndarray             # (F + 1,)
    verts: np.ndarray               # (offsets[-1],)
    owner: np.ndarray               # (F,)
    neighbour: np.ndarray           # (Fi,)
    n_cells: int
    patches: List[Tuple[str, str, int, int]]     # (name, type, startFace, nFaces) in file order
    cell_zone: np.ndarray           # (C,) zone index, -1 for none

    @property
    def n_faces(self) -> int:
        return int(self.offsets.size - 1)

    @property
    def n_internal(self) -> int:
        return int(self.neighbour.size)


_PATCH_RE = re.compile(r"\n\s*([^\s{}()]+)\s*\n\s*\{(.*?)\n\s*\}", re.S)


def _boundary_text(path: Path) -> str:
    return (path / "boundary").read_bytes().decode("utf-8", errors="replace")


def parse_patches(text: str) -> List[Tuple[str, str, int, int]]:
    """(name, type, startFace, nFaces) of a polyMesh/boundary file, in file order."""
    body = text[text.index("// * *"):]
    body = body[body.index("(") + 1:]
    out = []
    for m in _PATCH_RE.finditer("\n" + body):
        entry = m.group(2)
        typ = re.search(r"\btype\s+(\w+)\s*;", entry)
        start = re.search(r"\bstartFace\s+(\d+)\s*;", entry)
        n = re.search(r"\bnFaces\s+(\d+)\s*;", entry)
        if not (typ and start and n):
            continue
        out.append((m.group(1), typ.group(1), int(start.group(1)), int(n.group(1))))
    return out


def _read_n_cells(owner_path: Path, owner: np.ndarray, neighbour: np.ndarray) -> int:
    head = owner_path.read_bytes()[:4096].decode("utf-8", errors="replace")
    m = re.search(r"nCells:\s*(\d+)", head)
    if m:
        return int(m.group(1))
    return int(max(owner.max(initial=-1), neighbour.max(initial=-1)) + 1)


def _read_zone_cells(path: Path, n_cells: int) -> np.ndarray:
    """The cellZone index of every cell (-1 outside every zone) from polyMesh/cellZones."""
    zone = np.full(n_cells, -1, dtype=np.int64)
    f = path / "cellZones"
    if not f.is_file():
        return zone
    raw = f.read_bytes()
    body = raw[_payload_start(raw):]
    if re.match(rb"\s*0\s*\(\s*\)", body):
        return zone
    info = foam_header_info(raw)
    dtype = {4: np.int32, 8: np.int64}[info["label_bytes"]]
    for k, m in enumerate(re.finditer(rb"cellLabels\s+List<label>\s*", raw)):
        pos = m.end()
        if info["format"] == "binary":
            count, off = _binary_list_at(raw, pos)
            cells = np.frombuffer(raw, dtype=dtype, count=count, offset=off)
        else:
            mm = re.match(rb"\s*(\d+)\s*\(([^)]*)\)", raw[pos:])
            cells = np.array(mm.group(2).split(), dtype=np.int64) if mm else np.zeros(0, dtype=np.int64)
        zone[cells] = k
    return zone


def read_rank(path: Path) -> RankMesh:
    path = Path(path)
    points = read_points(path / "points").astype(np.float64)
    offsets, verts = read_all_faces(path / "faces")
    owner = read_label_list(path / "owner").astype(np.int64)
    neighbour = read_label_list(path / "neighbour").astype(np.int64)
    n_cells = _read_n_cells(path / "owner", owner, neighbour)
    patches = parse_patches(_boundary_text(path))
    return RankMesh(path, points, offsets.astype(np.int64), verts.astype(np.int64), owner, neighbour, n_cells,
                    patches, _read_zone_cells(path, n_cells))


def rank_dirs(mesh_dir: Path) -> List[Path]:
    """The polyMesh directories of a mesh: processor0..N-1, or constant/polyMesh when it is not decomposed."""
    mesh_dir = Path(mesh_dir)
    ranks = sorted(mesh_dir.glob("processor*/constant/polyMesh"),
                   key=lambda p: int(p.parts[-3][len("processor"):]))
    ranks = [r for r in ranks if (r / "faces").is_file()]
    if ranks:
        return ranks
    if (mesh_dir / "constant" / "polyMesh" / "faces").is_file():
        return [mesh_dir / "constant" / "polyMesh"]
    raise FileNotFoundError(f"{mesh_dir}: no processor*/constant/polyMesh and no constant/polyMesh")


# ------------------------------------------------------------------------------------------------- geometry
def face_geometry(points, offsets, verts):
    """Area vector and centroid of every face (triangle fan about the face's point mean, as OpenFOAM does)."""
    n_f = offsets.size - 1
    size = np.diff(offsets)
    face_of = np.repeat(np.arange(n_f), size)
    p = points[verts]
    mean = np.zeros((n_f, 3))
    np.add.at(mean, face_of, p)
    mean /= size[:, None]
    nxt = np.arange(verts.size) + 1
    last = offsets[1:] - 1
    nxt[last] = offsets[:-1]
    a = p - mean[face_of]
    b = points[verts[nxt]] - mean[face_of]
    tri = 0.5 * np.cross(a, b)
    Sf = np.zeros((n_f, 3))
    np.add.at(Sf, face_of, tri)
    mag = np.linalg.norm(Sf, axis=1)
    n_hat = Sf / np.maximum(mag, 1e-300)[:, None]
    w = np.einsum("ij,ij->i", tri, n_hat[face_of])
    ctr = (mean[face_of] + p + points[verts[nxt]]) / 3.0
    Cf = np.zeros((n_f, 3))
    np.add.at(Cf, face_of, w[:, None] * ctr)
    wsum = np.bincount(face_of, w, minlength=n_f)
    ok = np.abs(wsum) > 1e-300
    Cf[ok] /= wsum[ok, None]
    Cf[~ok] = mean[~ok]
    return Sf, Cf, mag


def cell_geometry(owner, neighbour, n_cells, Sf, Cf):
    """Volume and centroid of every cell (pyramids on its faces about the mean of its face centres)."""
    fi = neighbour.size
    cnt = np.bincount(owner, minlength=n_cells) + np.bincount(neighbour, minlength=n_cells)
    apex = np.zeros((n_cells, 3))
    np.add.at(apex, owner, Cf)
    np.add.at(apex, neighbour, Cf[:fi])
    apex /= np.maximum(cnt, 1)[:, None]
    vo = np.einsum("ij,ij->i", Sf, Cf - apex[owner]) / 3.0
    vn = -np.einsum("ij,ij->i", Sf[:fi], Cf[:fi] - apex[neighbour]) / 3.0
    V = np.bincount(owner, vo, minlength=n_cells) + np.bincount(neighbour, vn, minlength=n_cells)
    C = np.zeros((n_cells, 3))
    np.add.at(C, owner, vo[:, None] * (0.75 * Cf + 0.25 * apex[owner]))
    np.add.at(C, neighbour, vn[:, None] * (0.75 * Cf[:fi] + 0.25 * apex[neighbour]))
    C /= np.where(np.abs(V) > 1e-300, V, 1.0)[:, None]
    return V, C


# ------------------------------------------------------------------------------------------------- agglomeration
def components(n: int, i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """Connected-component label (the smallest member index) of every node of the graph with edges (i, j)."""
    lab = np.arange(n, dtype=np.int64)
    if i.size == 0:
        return lab
    while True:
        li, lj = lab[i], lab[j]
        m = np.minimum(li, lj)
        new = lab.copy()
        np.minimum.at(new, i, m)
        np.minimum.at(new, j, m)
        new = new[new]                      # pointer jumping
        while True:
            jumped = new[new]
            if np.array_equal(jumped, new):
                break
            new = jumped
        if np.array_equal(new, lab):
            return lab
        lab = new


def _dense(lab: np.ndarray) -> np.ndarray:
    _, inv = np.unique(lab, return_inverse=True)
    return inv.astype(np.int64)


def box_labels(C: np.ndarray, zone: np.ndarray, owner_i, neighbour, h, origin: np.ndarray,
               level: Optional[np.ndarray] = None) -> np.ndarray:
    """One agglomerate per connected part of a box (within one cellZone). A fine cell of level L lies in the box
    lattice of edge h / 2**L (``h`` a scalar or one edge per axis)."""
    h = np.asarray(h, dtype=np.float64)
    n = C.shape[0]
    level = np.zeros(n, dtype=np.int64) if level is None else level
    key = np.zeros(n, dtype=np.int64)
    off = 0
    for L in np.unique(level):
        m = level == L
        k3 = np.floor((C[m] - origin) / (h / 2.0 ** int(L))).astype(np.int64)
        k3 -= k3.min(axis=0)
        span = k3.max(axis=0) + 1
        flat = (k3[:, 0] * span[1] + k3[:, 1]) * span[2] + k3[:, 2]
        key[m] = flat + off
        off += int(flat.max()) + 1
    key = key * (int(zone.max(initial=-1)) + 2) + (zone + 1)
    same = key[owner_i] == key[neighbour]
    return _dense(components(n, owner_i[same], neighbour[same]))


def balance_levels(level: np.ndarray, i: np.ndarray, j: np.ndarray, jump: int = 1) -> np.ndarray:
    """Raise levels until face neighbours differ by at most ``jump`` (graded boxes)."""
    level = level.copy()
    while True:
        new = level.copy()
        np.maximum.at(new, i, level[j] - jump)
        np.maximum.at(new, j, level[i] - jump)
        if np.array_equal(new, level):
            return level
        level = new


def coarse_bad(lab: np.ndarray, plan: "RankPlan", max_nonortho_deg: float, boundary_test: bool = True) -> np.ndarray:
    """Coarse cells that are not valid finite-volume cells: a merged interior face more than ``max_nonortho_deg``
    from the line between the two centres, a face centre on the wrong side of a cell centre (the centre lies outside
    the cell there), or two cells touching on opposite sides (their summed contact keeps under half its area).
    Interior faces between two coarse cells are merged per pair, as a solver sees them."""
    m = plan.mesh
    fi = m.n_internal
    G = int(lab.max()) + 1
    V = np.bincount(lab, plan.V, minlength=G)
    C = np.stack([np.bincount(lab, plan.V * plan.C[:, k], minlength=G) for k in range(3)], 1) / V[:, None]
    a, b = lab[m.owner[:fi]], lab[m.neighbour]
    cross = a != b
    a, b = a[cross], b[cross]
    S = plan.Sf[:fi][cross]
    Cf = plan.Cf[:fi][cross]
    w = plan.magSf[:fi][cross]
    flip = a > b
    S = np.where(flip[:, None], -S, S)
    lo, hi = np.where(flip, b, a), np.where(flip, a, b)
    up, inv = np.unique(lo * G + hi, return_inverse=True)
    po, pn = up // G, up % G
    Sp = np.stack([np.bincount(inv, S[:, k]) for k in range(3)], 1)
    tot = np.bincount(inv, w)
    Cp = np.stack([np.bincount(inv, w * Cf[:, k]) for k in range(3)], 1) / np.maximum(tot, 1e-300)[:, None]
    d = C[pn] - C[po]
    magS = np.linalg.norm(Sp, axis=1)
    cos = np.einsum("ij,ij->i", d, Sp) / np.maximum(np.linalg.norm(d, axis=1) * magS, 1e-300)
    bad_f = cos < np.cos(np.radians(max_nonortho_deg))
    bad_f |= np.einsum("ij,ij->i", Cp - C[po], Sp) <= 0
    bad_f |= np.einsum("ij,ij->i", Cp - C[pn], Sp) >= 0
    bad_f |= magS < 0.5 * tot
    bad = np.zeros(G, dtype=bool)
    bad[po[bad_f]] = True
    bad[pn[bad_f]] = True
    # boundary faces: the cell centre must lie behind each of its boundary faces (processor faces are interior faces
    # of the whole mesh, coupled to the next rank, not boundaries)
    ob = lab[m.owner[fi:]]
    bad_b = np.einsum("ij,ij->i", plan.Cf[fi:] - C[ob], plan.Sf[fi:]) <= 0
    bad_b &= ~plan.coupled_b
    if not boundary_test:
        return bad
    bad[ob[bad_b]] = True
    return bad


def absorb_small(lab: np.ndarray, V: np.ndarray, zone: np.ndarray, owner_i, neighbour, magSf_i,
                 v_min, passes: int = 8) -> np.ndarray:
    """Merge every agglomerate smaller than v_min into the neighbour (same cellZone) it shares the most area with.
    ``v_min`` is a volume, or one per fine cell (an agglomerate takes the smallest of its cells': a box refined to
    level L counts as small below v_min / 8**L)."""
    v_cell = np.broadcast_to(np.asarray(v_min, dtype=np.float64), V.shape)
    for _ in range(passes):
        G = int(lab.max()) + 1
        vol = np.bincount(lab, V, minlength=G)
        thr = np.full(G, np.inf)
        np.minimum.at(thr, lab, v_cell)
        gz = np.full(G, -1, dtype=np.int64)
        gz[lab] = zone
        a, b = lab[owner_i], lab[neighbour]
        cross = (a != b) & (gz[a] == gz[b])
        a, b, w = a[cross], b[cross], magSf_i[cross]
        src = np.concatenate([a, b])
        dst = np.concatenate([b, a])
        w = np.concatenate([w, w])
        small = vol < thr
        sel = small[src]
        if not sel.any():
            break
        src, dst, w = src[sel], dst[sel], w[sel]
        # shared area per (src, dst) pair; each small agglomerate picks its largest contact
        pair = src * G + dst
        up, inv = np.unique(pair, return_inverse=True)
        area = np.bincount(inv, w)
        ps, pd = up // G, up % G
        order = np.lexsort((-area, ps))
        first = np.ones(order.size, dtype=bool)
        first[1:] = ps[order][1:] != ps[order][:-1]
        ps, pd = ps[order][first], pd[order][first]
        # merge only towards the larger one (or the lower index between equals), so no two swap places
        go = (vol[pd] > vol[ps]) | ((vol[pd] == vol[ps]) & (pd < ps))
        target = np.arange(G, dtype=np.int64)
        target[ps[go]] = pd[go]
        while True:
            nxt = target[target]
            if np.array_equal(nxt, target):
                break
            target = nxt
        if not go.any():
            break
        lab = _dense(target[lab])
    return lab


# ------------------------------------------------------------------------------------------------- lattice
def background_lattice(plans: Sequence["RankPlan"], points_min: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """(origin, pitch per axis) of the dcCart background grid: the largest cells are its unrefined cubes, their
    centres sit at origin + (k + 1/2) * pitch; the pitch of an axis is the commonest spacing of those centres (dcCart
    stretches each axis to fit the room, so the three differ slightly; with slotAlignedBackground the vertical one
    is the U-slot multiple)."""
    vmax = max(float(p.V.max()) for p in plans)
    c = np.concatenate([p.C[p.V > 0.9 * vmax] for p in plans])
    pitch = np.empty(3)
    for ax in range(3):
        u = np.unique(np.round(c[:, ax], 7))
        d = np.diff(u)
        d = d[d > 0.5 * vmax ** (1 / 3)]
        vals, cnt = np.unique(np.round(d, 6), return_counts=True)
        pitch[ax] = vals[np.argmax(cnt)] if vals.size else vmax ** (1 / 3)
    # the origin: the lattice point at or below the mesh's lowest point
    k = np.floor((c - points_min) / pitch - 0.5 + 1e-6)
    origin = np.median(c - (k + 0.5) * pitch, axis=0)
    return origin, pitch


# ------------------------------------------------------------------------------------------------- per rank
@dataclass
class RankPlan:
    mesh: RankMesh
    V: np.ndarray
    C: np.ndarray
    Sf: np.ndarray
    Cf: np.ndarray
    magSf: np.ndarray
    coupled_b: np.ndarray           # boundary faces of processor patches


def _plan(path: Path) -> RankPlan:
    m = read_rank(path)
    Sf, Cf, mag = face_geometry(m.points, m.offsets, m.verts)
    V, C = cell_geometry(m.owner, m.neighbour, m.n_cells, Sf, Cf)
    coupled = np.zeros(m.n_faces - m.n_internal, dtype=bool)
    for name, typ, s, n in m.patches:
        if typ in COUPLED and n:
            coupled[s - m.n_internal:s - m.n_internal + n] = True
    return RankPlan(m, V, C, Sf, Cf, mag, coupled)


@dataclass
class Options:
    absorb: float = 0.25                # agglomerates below this share of the mean coarse volume join a neighbour
    max_nonortho_deg: float = 70.0      # quality bound of a merged coarse face
    passes: int = 8                     # box-level raises of failing agglomerates
    level_jump: int = 1                 # graded boxes: face neighbours differ by at most this many levels
    dissolve: bool = True               # agglomerates still failing after the passes return to their fine cells
    boundary_test: bool = True          # also require the cell centre behind each of its boundary faces


def _labels(plan: RankPlan, h: float, origin: np.ndarray, v_min: float, opt: Options,
            stats: Optional[dict] = None) -> np.ndarray:
    m = plan.mesh
    oi, nb = m.owner[:m.n_internal], m.neighbour
    level = np.zeros(m.n_cells, dtype=np.int64)
    history = []
    for k in range(opt.passes + 1):
        lab = box_labels(plan.C, m.cell_zone, oi, nb, h, origin, level)
        lab = absorb_small(lab, plan.V, m.cell_zone, oi, nb, plan.magSf[:m.n_internal], v_min / 8.0 ** level)
        bad = coarse_bad(lab, plan, opt.max_nonortho_deg, opt.boundary_test)
        size = np.bincount(lab)
        split = bad[lab] & (size[lab] > 1)
        history.append({"pass": k, "cells": int(lab.max()) + 1, "bad": int(bad.sum()),
                        "split_fine_cells": int(split.sum()), "max_level": int(level.max())})
        if not split.any() or k == opt.passes:
            break
        level[split] += 1
        level = balance_levels(level, oi, nb, opt.level_jump)
    dissolved = 0
    if opt.dissolve:
        for _ in range(4):
            bad = coarse_bad(lab, plan, opt.max_nonortho_deg, opt.boundary_test)
            size = np.bincount(lab)
            go = bad[lab] & (size[lab] > 1)
            if not go.any():
                break
            idx = np.flatnonzero(go)
            lab = lab.copy()
            lab[idx] = int(lab.max()) + 1 + np.arange(idx.size)
            lab = _dense(lab)
            dissolved += int(idx.size)
    if stats is not None:
        bad = coarse_bad(lab, plan, opt.max_nonortho_deg, opt.boundary_test)
        stats.update({"passes": history, "dissolved_fine_cells": dissolved, "remaining_bad": int(bad.sum()),
                      "max_level": int(level.max())})
    return lab


def _numbered(lab: np.ndarray) -> np.ndarray:
    """Coarse numbering by first fine cell (keeps the fine ordering's locality)."""
    _, first = np.unique(lab, return_index=True)
    order = np.argsort(first, kind="stable")
    rank_of = np.empty(order.size, dtype=np.int64)
    rank_of[order] = np.arange(order.size)
    return rank_of[lab]


# ------------------------------------------------------------------------------------------------- writing
_BANNER = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
           "  =========                 |\n"
           "  \\\\      /  F ield         | dcCart coarsening (dcdtuni)\n"
           "   \\\\    /   O peration     |\n"
           "    \\\\  /    A nd           |\n"
           "     \\\\/     M anipulation  |\n"
           "\\*---------------------------------------------------------------------------*/\n")


def _header(cls: str, obj: str, note: Optional[str] = None) -> bytes:
    lines = [_BANNER, "FoamFile\n{\n", "    version     2.0;\n", "    format      binary;\n",
             '    arch        "LSB;label=32;scalar=64";\n', f"    class       {cls};\n"]
    if note:
        lines.append(f'    note        "{note}";\n')
    lines += ['    location    "constant/polyMesh";\n', f"    object      {obj};\n", "}\n",
              "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n"]
    return "".join(lines).encode()


def _binary_list(a: np.ndarray, dtype) -> bytes:
    a = np.ascontiguousarray(a, dtype=dtype)
    n = a.shape[0]
    return f"{n}\n(".encode() + a.tobytes() + b")\n"


def _write(path: Path, header: bytes, *bodies: bytes) -> None:
    with open(path, "wb") as f:
        f.write(header)
        for b in bodies:
            f.write(b)
        f.write(b"\n\n// ************************************************************************* //\n")


def _rewrite_boundary(text: str, shift: int) -> str:
    """The boundary file with every startFace moved by ``shift`` (the boundary faces are kept in order)."""
    head, sep, body = text.partition("// * *")
    return head + sep + re.sub(r"(\bstartFace\s+)(\d+)(\s*;)", lambda m: f"{m.group(1)}{int(m.group(2)) + shift}"
                               f"{m.group(3)}", body)


def write_rank(plan: RankPlan, lab: np.ndarray, out_poly: Path) -> Dict[str, object]:
    m = plan.mesh
    fi, F = m.n_internal, m.n_faces
    lab = _numbered(lab)
    Nc = int(lab.max()) + 1
    lo, ln = lab[m.owner[:fi]], lab[m.neighbour]
    keep = np.flatnonzero(lo != ln)
    flip = lo[keep] > ln[keep]
    own_i = np.where(flip, ln[keep], lo[keep])
    nei_i = np.where(flip, lo[keep], ln[keep])
    order = np.lexsort((keep, nei_i, own_i))           # upper-triangular: by owner, then neighbour
    keep, flip, own_i, nei_i = keep[order], flip[order], own_i[order], nei_i[order]
    faces_new = np.concatenate([keep, np.arange(fi, F)])
    flip_all = np.concatenate([flip, np.zeros(F - fi, dtype=bool)])
    owner_new = np.concatenate([own_i, lab[m.owner[fi:]]])
    size = np.diff(m.offsets)[faces_new]
    starts = m.offsets[:-1][faces_new]
    idx = np.repeat(starts, size) + (np.arange(size.sum()) - np.repeat(np.cumsum(size) - size, size))
    # a flipped face lists its points in reverse order (first point kept)
    rev = np.repeat(flip_all, size)
    pos = np.arange(size.sum()) - np.repeat(np.cumsum(size) - size, size)
    rpos = np.where(pos == 0, 0, np.repeat(size, size) - pos)
    idx = np.where(rev, np.repeat(starts, size) + rpos, idx)
    verts = m.verts[idx]
    used = np.unique(verts)
    pmap = np.full(m.points.shape[0], -1, dtype=np.int64)
    pmap[used] = np.arange(used.size)
    verts = pmap[verts]
    offsets = np.concatenate([[0], np.cumsum(size)])
    n_int = int(keep.size)
    note = f"nPoints:{used.size}  nCells:{Nc}  nFaces:{faces_new.size}  nInternalFaces:{n_int}"
    out_poly.mkdir(parents=True, exist_ok=True)
    _write(out_poly / "points", _header("vectorField", "points"), _binary_list(m.points[used], np.float64))
    _write(out_poly / "faces", _header("faceCompactList", "faces"), _binary_list(offsets, np.int32),
           _binary_list(verts, np.int32))
    _write(out_poly / "owner", _header("labelList", "owner", note), _binary_list(owner_new, np.int32))
    _write(out_poly / "neighbour", _header("labelList", "neighbour", note), _binary_list(nei_i, np.int32))
    shift = n_int - fi
    (out_poly / "boundary").write_text(_rewrite_boundary(_boundary_text(m.path), shift))
    for zone_file in ("cellZones", "faceZones", "pointZones"):
        src = m.path / zone_file
        if src.is_file():
            raw = src.read_bytes()
            if not _zones_empty(raw, zone_file):
                raise NotImplementedError(f"{src}: non-empty {zone_file} are not remapped yet")
            shutil.copy2(src, out_poly / zone_file)
    return {"fine_cells": m.n_cells, "cells": Nc, "fine_internal_faces": fi, "internal_faces": n_int,
            "boundary_faces": F - fi, "fine_points": int(m.points.shape[0]), "points": int(used.size),
            "face_shift": shift, "labels": lab}


def _zones_empty(raw: bytes, kind: str) -> bool:
    body = raw[_payload_start(raw):]
    if re.match(rb"\s*0\s*\(\s*\)", body):
        return True
    counts = re.findall(rb"(?:cellLabels|faceLabels|pointLabels)\s+List<label>\s*(\d+)", body)
    return bool(counts) and all(int(c) == 0 for c in counts)


# ------------------------------------------------------------------------------------------------- audit
def audit_rank(fine: RankPlan, coarse_poly: Path, labels: np.ndarray) -> Dict[str, object]:
    """Recompute the written coarse mesh's geometry and compare it with the fine mesh."""
    c = read_rank(coarse_poly)
    Sf, Cf, mag = face_geometry(c.points, c.offsets, c.verts)
    V, C = cell_geometry(c.owner, c.neighbour, c.n_cells, Sf, Cf)
    fm = fine.mesh
    patch_err = 0.0
    phys = 0
    mismatch = []
    for (name, typ, s, n), (name_c, typ_c, s_c, n_c) in zip(fm.patches, c.patches):
        if name != name_c or typ != typ_c or n != n_c:
            mismatch.append(name)
            continue
        if typ not in COUPLED:
            phys += 1
        if n:
            a_f = float(fine.magSf[s:s + n].sum())
            a_c = float(mag[s_c:s_c + n_c].sum())
            patch_err = max(patch_err, abs(a_c - a_f) / max(a_f, 1e-300))
    # quality as a solver sees it: the fine faces between two coarse cells merged into one area vector per pair
    fi = c.n_internal
    o, n = c.owner[:fi], c.neighbour
    up, inv = np.unique(o * c.n_cells + n, return_inverse=True)
    po, pn = up // c.n_cells, up % c.n_cells
    Sp = np.stack([np.bincount(inv, Sf[:fi, k]) for k in range(3)], 1)
    tot = np.bincount(inv, mag[:fi])
    d = C[pn] - C[po]
    magS = np.linalg.norm(Sp, axis=1)
    cosang = np.einsum("ij,ij->i", d, Sp) / np.maximum(np.linalg.norm(d, axis=1) * magS, 1e-300)
    ang = np.degrees(np.arccos(np.clip(cosang, -1.0, 1.0)))
    coupled = np.zeros(c.n_faces - fi, dtype=bool)
    for name, typ, s, nf in c.patches:
        if typ in COUPLED and nf:
            coupled[s - fi:s - fi + nf] = True
    behind = (np.einsum("ij,ij->i", Cf[fi:] - C[c.owner[fi:]], Sf[fi:]) <= 0) & ~coupled
    # closedness: the area vectors of each cell's faces sum to zero
    close = np.zeros((c.n_cells, 3))
    np.add.at(close, c.owner, Sf)
    np.add.at(close, c.neighbour, -Sf[:c.n_internal])
    area = np.bincount(c.owner, mag, minlength=c.n_cells) + np.bincount(c.neighbour, mag[:c.n_internal],
                                                                         minlength=c.n_cells)
    open_rel = float((np.linalg.norm(close, axis=1) / np.maximum(area, 1e-300)).max(initial=0.0))
    return {"patches": len(c.patches), "physical_patches": phys, "patch_mismatch": mismatch,
            "patch_area_rel_err_max": patch_err,
            "fine_volume": float(fine.V.sum()), "volume": float(V.sum()),
            "negative_volume_cells": int((V <= 0).sum()),
            "pairs": int(up.size), "pair_nonortho_deg_max": float(ang.max(initial=0.0)),
            "pair_nonortho_deg_mean": float(ang.mean()) if ang.size else 0.0,
            "pairs_over_70_deg": int((ang > 70).sum()), "pairs_over_90_deg": int((ang > 90).sum()),
            "pairs_opposite_contact": int((magS < 0.5 * tot).sum()),
            "boundary_faces_behind_centre": int(behind.sum()),
            "cells_with_centre_behind_a_boundary_face": int(np.unique(c.owner[fi:][behind]).size),
            "cell_closedness_rel_max": open_rel,
            "fine_cells_per_cell_max": int(np.bincount(labels).max())}


# ------------------------------------------------------------------------------------------------- driver
def _copy_case_files(src: Path, dst: Path, n_ranks: int, shifts: Dict[int, int], decomposed: bool) -> None:
    """Everything of the fine mesh directory except the polyMesh itself (fields, dictionaries, source regions)."""
    for item in src.iterdir():
        if item.name.startswith("processor"):
            continue
        if item.name == "constant":
            (dst / "constant").mkdir(parents=True, exist_ok=True)
            for sub in item.iterdir():
                if sub.name == "polyMesh" and not decomposed:
                    continue
                target = dst / "constant" / sub.name
                if sub.is_dir():
                    shutil.copytree(sub, target, dirs_exist_ok=True, symlinks=True)
                else:
                    shutil.copy2(sub, target)
            continue
        if item.name.startswith("log.") or item.name in ("dccart-coarsen.json", "coarsen"):
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(item, target)
    if not decomposed:
        return
    for k in range(n_ranks):
        rs, rd = src / f"processor{k}", dst / f"processor{k}"
        for item in rs.iterdir():
            if item.name == "constant":
                for sub in item.iterdir():
                    if sub.name == "polyMesh":
                        continue
                    if sub.name == "dcCartSourceRegions.json":
                        _shift_source_regions(sub, rd / "constant" / sub.name, shifts[k])
                    elif sub.is_dir():
                        shutil.copytree(sub, rd / "constant" / sub.name, dirs_exist_ok=True, symlinks=True)
                    else:
                        shutil.copy2(sub, rd / "constant" / sub.name)
            elif item.is_dir():
                if re.fullmatch(r"\d+(\.\d+)?", item.name) and item.name != "0":
                    continue                          # time directories of a run on the fine mesh
                shutil.copytree(item, rd / item.name, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(item, rd / item.name)


def _shift_source_regions(src: Path, dst: Path, shift: int) -> None:
    d = json.loads(src.read_text())
    for r in d.get("regions", []):
        for pr in r.get("patchRanges", []) or []:
            if "startFace" in pr:
                pr["startFace"] = int(pr["startFace"]) + shift
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(d))


def coarsen_mesh(fine_dir: Path, out_dir: Path, *, ratio: Optional[float] = None, level: Optional[str] = None,
                 absorb: Optional[float] = None, tolerance: float = 0.05, workers: Optional[int] = None,
                 max_nonortho_deg: Optional[float] = None, passes: Optional[int] = None, level_jump: int = 1,
                 dissolve: Optional[bool] = None, boundary_test: Optional[bool] = None,
                 search_points: int = 12, max_bisection: int = 8) -> Dict[str, object]:
    """Write the coarse mesh of the fine mesh directory ``fine_dir`` (a case's ``Mesh/``) into ``out_dir``.

    ``ratio`` (or ``level``: L1 = 100, L0 = 1000) is the fine-to-coarse cell-count ratio; the box edge is bisected
    until the coarse count is within ``tolerance`` of ``fine / ratio``. ``absorb``: agglomerates below this share of
    the mean coarse volume are merged into a neighbour. Returns the receipt (also written as dccart-coarsen.json)."""
    t0 = time.perf_counter()
    if ratio is None:
        if level not in LEVEL_RATIOS:
            raise ValueError(f"give ratio or level (one of {sorted(LEVEL_RATIOS)})")
        ratio = LEVEL_RATIOS[level]
    ratio = float(ratio)
    preset = dict(LEVEL_OPTIONS.get(level or ("L0" if ratio >= 300 else "L1")))
    for key, val in (("max_nonortho_deg", max_nonortho_deg), ("passes", passes), ("dissolve", dissolve),
                     ("boundary_test", boundary_test), ("absorb", absorb)):
        if val is not None:
            preset[key] = val
    opt = Options(level_jump=level_jump, **preset)
    fine_dir, out_dir = Path(fine_dir).resolve(), Path(out_dir).resolve()
    if out_dir == fine_dir:
        raise ValueError("write the coarse mesh into another directory than the fine one")
    ranks = rank_dirs(fine_dir)
    decomposed = ranks[0].parts[-3].startswith("processor")
    workers = workers or min(len(ranks), max(1, (os.cpu_count() or 2) // 2), 16)
    out_polys = [out_dir / r.relative_to(fine_dir) for r in ranks]
    # numpy releases the GIL in the heavy kernels: one thread per rank
    plans: List[RankPlan] = []
    with ThreadPoolExecutor(max_workers=workers) as tp:
        plans = list(tp.map(_plan, ranks))
    t_read = time.perf_counter() - t0
    n_fine = sum(p.mesh.n_cells for p in plans)
    v_total = float(sum(p.V.sum() for p in plans))
    points_min = np.min([p.mesh.points.min(axis=0) for p in plans], axis=0)
    origin, pitch = background_lattice(plans, points_min)
    origin = origin - 1e-9 * pitch
    target = n_fine / ratio
    v_min = opt.absorb * v_total / target

    def count(m):
        h = pitch * np.asarray(m, dtype=np.float64)
        with ThreadPoolExecutor(max_workers=workers) as tp:
            return int(sum(tp.map(lambda p: int(_labels(p, h, origin, v_min, opt).max()) + 1, plans)))

    # boxes of (m_xy, m_xy, m_z) background cells, near-cubic (m_z within a factor 2 of m_xy); the count falls with
    # the box size, so for each m_xy the scan stops at the first m_z past the target
    history = []
    best = None
    m0 = max(1, int(round((v_total / target) ** (1 / 3) / float(pitch.mean()))))
    for mxy in range(max(1, m0 // 2), 2 * m0 + 3):
        first = True
        for mz in range(max(1, mxy // 2), 2 * mxy + 1):
            n = count((mxy, mxy, mz))
            history.append({"m": [mxy, mxy, mz], "box_m": (pitch * [mxy, mxy, mz]).tolist(), "cells": n})
            if best is None or abs(n - target) < abs(best[1] - target):
                best = ((mxy, mxy, mz), n)
            if n < target:
                break
            first = False
        if first and n < target:
            break                       # the flattest box of this m_xy is already below the target
    m_best = best[0]
    h = pitch * np.asarray(m_best, dtype=np.float64)
    t_bisect = time.perf_counter() - t0 - t_read
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    recs: Dict[int, Dict[str, object]] = {}
    (out_dir / "coarsen").mkdir()

    def write_one(k):
        plan = plans[k]
        stats: dict = {}
        lab = _labels(plan, h, origin, v_min, opt, stats)
        rec = write_rank(plan, lab, out_polys[k])
        rec["adapt"] = stats
        labels = rec.pop("labels")
        np.save(out_dir / "coarsen" / f"fineToCoarse_rank{k}.npy", labels.astype(np.int32))
        rec["audit"] = audit_rank(plan, out_polys[k], labels)
        return k, rec

    with ThreadPoolExecutor(max_workers=workers) as tp:
        for k, rec in tp.map(write_one, range(len(plans))):
            recs[k] = rec
    _copy_case_files(fine_dir, out_dir, len(plans), {k: int(r["face_shift"]) for k, r in recs.items()}, decomposed)
    n_coarse = sum(int(r["cells"]) for r in recs.values())
    audits = [r["audit"] for r in recs.values()]
    phys_names = {name for p in plans for (name, typ, _, _) in p.mesh.patches if typ not in COUPLED}
    receipt = {
        "format": FORMAT,
        "fineMesh": str(fine_dir), "coarseMesh": str(out_dir),
        "settings": {"ratio": ratio, "level": level, "absorb": opt.absorb, "tolerance": tolerance,
                     "maxNonOrthoDeg": opt.max_nonortho_deg, "passes": opt.passes, "levelJump": opt.level_jump,
                     "dissolve": opt.dissolve, "boundaryTest": opt.boundary_test},
        "lattice": {"origin": origin.tolist(), "pitch": pitch.tolist()},
        "boxCells": list(m_best), "boxEdge_m": h.tolist(), "search": history,
        "ranks": len(plans), "decomposed": decomposed,
        "fineCells": n_fine, "cells": n_coarse, "achievedRatio": n_fine / max(n_coarse, 1),
        "finePoints": sum(int(r["fine_points"]) for r in recs.values()),
        "points": sum(int(r["points"]) for r in recs.values()),
        "internalFaces": sum(int(r["internal_faces"]) for r in recs.values()),
        "boundaryFaces": sum(int(r["boundary_faces"]) for r in recs.values()),
        "physicalPatches": len(phys_names),
        "patchAudit": {
            "patchesPerRank": sorted({a["patches"] for a in audits}),
            "mismatchedPatches": sorted({n for a in audits for n in a["patch_mismatch"]}),
            "patchAreaRelErrMax": max(a["patch_area_rel_err_max"] for a in audits),
        },
        "volume": {"fine": v_total, "coarse": float(sum(a["volume"] for a in audits)),
                   "relErr": abs(sum(a["volume"] for a in audits) - v_total) / v_total},
        "quality": {
            "negativeVolumeCells": sum(a["negative_volume_cells"] for a in audits),
            "cellPairs": sum(a["pairs"] for a in audits),
            "pairNonOrthoDegMax": max(a["pair_nonortho_deg_max"] for a in audits),
            "pairsOver70Deg": sum(a["pairs_over_70_deg"] for a in audits),
            "pairsOver90Deg": sum(a["pairs_over_90_deg"] for a in audits),
            "pairsOppositeContact": sum(a["pairs_opposite_contact"] for a in audits),
            "cellsWithCentreBehindABoundaryFace": sum(a["cells_with_centre_behind_a_boundary_face"] for a in audits),
            "cellClosednessRelMax": max(a["cell_closedness_rel_max"] for a in audits),
            "fineCellsPerCellMax": max(a["fine_cells_per_cell_max"] for a in audits),
            "invalidCoarseCells": sum(int(r["adapt"]["remaining_bad"]) for r in recs.values()),
            "dissolvedFineCells": sum(int(r["adapt"]["dissolved_fine_cells"]) for r in recs.values()),
            "maxBoxLevel": max(int(r["adapt"]["max_level"]) for r in recs.values()),
        },
        "perRank": {str(k): r for k, r in sorted(recs.items())},
        "seconds": {"read": t_read, "bisection": t_bisect, "total": time.perf_counter() - t0},
    }
    receipt["ok"] = (not receipt["patchAudit"]["mismatchedPatches"]
                     and receipt["patchAudit"]["patchAreaRelErrMax"] < 1e-9
                     and receipt["volume"]["relErr"] < 1e-9
                     and receipt["quality"]["negativeVolumeCells"] == 0)
    (out_dir / "dccart-coarsen.json").write_text(json.dumps(receipt, indent=1, default=float))
    return receipt
