"""An OpenFOAM mesh as the cell / boundary-face graph of the DCTS cache, with refine's geometry.

The cache SCANO trained on (DCTS cache v1, scano.Datasets.dcts) was converted from H5 files that Gen-FVGN-refine
``c018c80`` wrote (src/Parse_mesh/parse_base.py), so the geometry here follows that code rather than OpenFOAM's
primitiveMesh where the two differ:
  faces   unit normal = the normalised sum of (v_i - c) x (v_i+1 - c) about the vertex mean c (file vertex order);
          vertices re-sorted by their angle about c (pivot: the first vertex); triangles get the vertex mean and half
          the cross-product norm, larger polygons the fan about c: area = half the SUM OF THE TRIANGLE AREAS (not the
          norm of their vector sum, OpenFOAM's), centre = the area-weighted triangle centroids;
  cells   estimated centre = mean of the cell's face centres; each face normal flipped to point away from it;
          pyramids (fA . (fc - cEst), 3/4 fc + 1/4 cEst); volume = |sum| / 3, centre = the weighted pyramid centroids.
The two agree on flat convex faces and differ by up to a few percent on warped ones (snappy's cut cells), which is
why they are reproduced here: the model's inputs are the values it was trained on.

A decomposed mesh (processor*/constant/polyMesh) is joined without reconstructPar: the cells of rank r follow those
of ranks < r, the faces a processor patch shares become one internal face (owner on the lower rank, as OpenFOAM's
processor patches list their faces in the same order on both sides), and a processorCyclic face is a boundary face
of the cyclic patch it refers to. Boundary faces are ordered by patch (file order of rank 0's boundary list, then
ranks), the order the cache's patch table needs (contiguous ranges).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from backend.aircooling.runner.polymesh_io import read_all_faces, read_label_list, read_points

COUPLED = ('processor', 'processorCyclic')
KIND_OF_TYPE = {'wall': 1, 'patch': 2, 'cyclic': 3}       # the cache's kind: 0 cell, 1 wall, 2 patch, 3 cyclic


def parse_boundary(path: Path) -> List[Tuple[str, Dict[str, str]]]:
    """[(name, {type, nFaces, startFace, ...}), ...] of a polyMesh/boundary file in file order."""
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', Path(path).read_text(), flags=re.S)
    text = text[text.index('}', text.index('FoamFile')) + 1:]
    body = text[text.index('(') + 1:text.rindex(')')]
    out = []
    for m in re.finditer(r'(\S+)\s*\{([^{}]*)\}', body):
        out.append((m.group(1), {k: v.strip() for k, v in re.findall(r'(\w+)\s+([^;]*);', m.group(2))}))
    return out


def rank_mesh_dirs(case: Path, layout: str = 'auto') -> List[Path]:
    """The polyMesh directories of a case: [constant/polyMesh] when it is reconstructed (layout "auto"), else
    processor0..N-1 (or always the ranks: layout "processors")."""
    case = Path(case)
    if layout not in ('auto', 'processors'):
        raise ValueError(f'layout {layout!r}')
    if layout == 'auto' and (case / 'constant' / 'polyMesh' / 'faces').is_file():
        return [case / 'constant' / 'polyMesh']
    ranks = sorted(case.glob('processor*/constant/polyMesh'), key=lambda p: int(p.parts[-3][len('processor'):]))
    if not ranks:
        raise FileNotFoundError(f'{case}: no constant/polyMesh and no processor*/constant/polyMesh')
    return ranks


# --- refine's face and cell geometry (torch float64, grouped by vertex / face count as refine does) -------------

def _polygons(points, verts_by_face):
    """(unit normals, areas, centres) of faces with the same vertex count P: refine _sort_polygon_vertices +
    _calculate_polygon_centroids_and_areas. verts_by_face [N, P] long."""
    import torch
    v = points[verts_by_face]                                   # [N, P, 3]
    n_f, p_count, _ = v.shape
    c = v.mean(dim=1, keepdim=True)
    cp = v - c
    normal = torch.cross(cp, torch.roll(cp, -1, dims=1), dim=2).sum(dim=1)
    unit = normal / torch.norm(normal, dim=1, keepdim=True)
    piv = cp[:, 0:1, :].expand(-1, p_count, -1)
    dots = (piv * cp).sum(dim=2)
    mags = (torch.cross(piv, cp, dim=2) * unit.unsqueeze(1)).sum(dim=2)
    ang = torch.atan2(mags, dots)
    ang = torch.where(ang < 0, ang + 2 * math.pi, ang)
    first = torch.isclose(ang[:, 0], torch.full_like(ang[:, 0], 2 * math.pi), rtol=1e-5, atol=1e-8)
    ang[first, 0] = 0.0
    ang = torch.where(torch.abs(ang - 2 * math.pi) < 1e-10, torch.zeros_like(ang), ang)
    order = torch.argsort(ang, dim=1)
    v = v[torch.arange(n_f).unsqueeze(1).expand(-1, p_count), order]
    if p_count == 3:
        e = torch.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0], dim=1)
        return unit, 0.5 * torch.norm(e, dim=1), v.mean(dim=1)
    est = v.mean(dim=1)
    nxt = torch.roll(v, -1, dims=1)
    tri_c3 = est.unsqueeze(1) + v + nxt
    tri_a2 = torch.norm(torch.cross(nxt - v, est.unsqueeze(1) - v, dim=2), dim=2)
    sum_a = tri_a2.sum(dim=1)
    sum_ac = (tri_a2.unsqueeze(2) * tri_c3).sum(dim=1)
    centre, area = est.clone(), torch.zeros(n_f, dtype=v.dtype)
    ok = sum_a >= 1e-10
    centre[ok] = sum_ac[ok] / (3.0 * sum_a[ok].unsqueeze(1))
    area[ok] = 0.5 * sum_a[ok]
    return unit, area, centre


def face_geometry(points: np.ndarray, offsets: np.ndarray, verts: np.ndarray):
    """(unit normals [F, 3], areas [F], centres [F, 3]) float64 of every face (refine's algorithm)."""
    import torch
    pts = torch.from_numpy(np.ascontiguousarray(points, np.float64))
    counts = np.diff(offsets)
    nf = counts.size
    normal = torch.zeros(nf, 3, dtype=torch.float64)
    area = torch.zeros(nf, dtype=torch.float64)
    centre = torch.zeros(nf, 3, dtype=torch.float64)
    for p_count in np.unique(counts):
        faces = np.flatnonzero(counts == p_count)
        idx = offsets[faces][:, None] + np.arange(p_count)[None, :]
        u, a, c = _polygons(pts, torch.from_numpy(verts[idx]))
        fi = torch.from_numpy(faces)
        normal[fi], area[fi], centre[fi] = u, a, c
    return normal.numpy(), area.numpy(), centre.numpy()


def cell_geometry(owner, neighbour, ncell, f_normal, f_area, f_centre):
    """(centres [C, 3], volumes [C]) float64 by refine's pyramid decomposition (normals flipped away from the
    estimated centre, |sum| / 3). Summed per cell with bincount (refine sums each cell's faces in its own order;
    the difference is round-off)."""
    nint = neighbour.size
    cell_of = np.concatenate([owner, neighbour])               # one row per (cell, face) incidence
    face_of = np.concatenate([np.arange(owner.size), np.arange(nint)])
    cnt = np.bincount(cell_of, minlength=ncell).astype(np.float64)
    est = np.stack([np.bincount(cell_of, f_centre[face_of, k], ncell) for k in range(3)], 1) / cnt[:, None]
    d = f_centre[face_of] - est[cell_of]
    n = f_normal[face_of]
    flip = np.einsum('ij,ij->i', d, n) < 0
    n = np.where(flip[:, None], -n, n)
    pyr3 = f_area[face_of] * np.einsum('ij,ij->i', n, d)
    pc = 0.75 * f_centre[face_of] + 0.25 * est[cell_of]
    s = np.bincount(cell_of, pyr3, ncell)
    sc = np.stack([np.bincount(cell_of, pyr3 * pc[:, k], ncell) for k in range(3)], 1)
    ok = np.abs(s) > 1e-15
    centre = est.copy()
    centre[ok] = sc[ok] / s[ok, None]
    vol = np.where(ok, np.abs(s) / 3.0, 0.0)
    return centre, vol


# --- the joined mesh -----------------------------------------------------------------------------------------

@dataclass
class PatchInfo:
    name: str
    geom_type: str                       # wall / patch / cyclic
    n_faces: int
    neighbour_patch: Optional[str]


@dataclass
class FoamGraph:
    """Cells then boundary faces (one node per boundary face), as the cache wants them. ``rank_*`` keep the way
    back to each rank's own numbering, for reading fields and writing the prediction rank by rank."""
    n_cells: int
    pos: np.ndarray                      # [N, 3] float64: cell centroids, then boundary-face centroids
    volume: np.ndarray                   # [C]
    normal: np.ndarray                   # [Nb, 3] unit outward normals of the boundary faces
    area: np.ndarray                     # [Nb]
    kind: np.ndarray                     # [N] int8
    patch: np.ndarray                    # [Nb] int32, index into ``patches``
    edges: np.ndarray                    # [2, F] int64: owner -> neighbour (internal), owner -> Ncell + j (boundary)
    patches: List[PatchInfo]
    n_points: int
    rank_cell_offset: List[int] = field(default_factory=list)
    # per rank: {patch name in that rank's boundary file: (global boundary-face indices [n], local face offset)}
    rank_patch_rows: List[Dict[str, np.ndarray]] = field(default_factory=list)
    rank_dirs: List[Path] = field(default_factory=list)
    # per rank: {processor patch name: (global cells on this rank, global cells across)} per face, in the
    # patch's face order (coupled face values are their mean)
    rank_proc_faces: List[Dict[str, Tuple[np.ndarray, np.ndarray]]] = field(default_factory=list)

    @property
    def n_boundary(self) -> int:
        return self.pos.shape[0] - self.n_cells


def read_graph(case: Path, layout: str = 'auto') -> FoamGraph:
    """The joined cell / boundary-face graph of a case (module docstring; layout: rank_mesh_dirs)."""
    dirs = rank_mesh_dirs(case, layout)
    ranks = []
    for d in dirs:
        pts = read_points(d / 'points').astype(np.float64)
        off, verts = read_all_faces(d / 'faces')
        own = read_label_list(d / 'owner').astype(np.int64)
        nei = read_label_list(d / 'neighbour').astype(np.int64)
        ncell = int(max(own.max(), nei.max() if nei.size else -1)) + 1
        fn, fa, fc = face_geometry(pts, off, verts)
        cc, cv = cell_geometry(own, nei, ncell, fn, fa, fc)
        ranks.append({'dir': d, 'own': own, 'nei': nei, 'ncell': ncell, 'fn': fn, 'fa': fa, 'fc': fc, 'cc': cc,
                      'cv': cv, 'bnd': parse_boundary(d / 'boundary'), 'npts': pts.shape[0]})
    cell_off = np.cumsum([0] + [r['ncell'] for r in ranks]).tolist()
    ncell = cell_off[-1]

    # the physical patch list: every non-processor patch of every rank, in rank 0's order then new names
    order: List[str] = []
    info: Dict[str, PatchInfo] = {}
    for r in ranks:
        for name, d in r['bnd']:
            t = d['type']
            if t == 'processor':
                continue
            if t == 'processorCyclic':
                name, t = d['referPatch'], 'cyclic'
            if name not in info:
                if t not in KIND_OF_TYPE:
                    raise ValueError(f'patch {name}: type {t} has no DCTS cache kind (wall / patch / cyclic)')
                info[name] = PatchInfo(name, t, 0, d.get('neighbourPatch'))
                order.append(name)
            elif info[name].geom_type != t:
                raise ValueError(f'patch {name}: type {info[name].geom_type} on one rank, {t} on another')
    # internal edges: each rank's internal faces, then processor faces once (lower rank owns)
    int_own, int_nei = [], []
    for k, r in enumerate(ranks):
        nint = r['nei'].size
        int_own.append(r['own'][:nint] + cell_off[k])
        int_nei.append(r['nei'] + cell_off[k])
    n_internal = sum(a.size for a in int_own)
    proc_faces: List[Dict[str, Tuple[np.ndarray, np.ndarray]]] = [dict() for _ in ranks]
    by_pair = {}
    for k, r in enumerate(ranks):
        for name, d in r['bnd']:
            if d['type'] != 'processor':
                continue
            s, n = int(d['startFace']), int(d['nFaces'])
            mine, other = int(d['myProcNo']), int(d['neighbProcNo'])
            by_pair[(mine, other)] = (k, name, r['own'][s:s + n] + cell_off[k])
    for (a, b), (k, name, cells_a) in sorted(by_pair.items()):
        if a > b:
            continue
        kb, name_b, cells_b = by_pair[(b, a)]
        if cells_a.size != cells_b.size:
            raise ValueError(f'processor patches {a}->{b} and {b}->{a} have {cells_a.size} / {cells_b.size} faces')
        int_own.append(cells_a)
        int_nei.append(cells_b)
        proc_faces[k][name] = (cells_a, cells_b)
        proc_faces[kb][name_b] = (cells_b, cells_a)
        n_internal += cells_a.size

    # boundary faces grouped by patch
    rows_of: List[Dict[str, np.ndarray]] = [dict() for _ in ranks]
    b_own, b_fn, b_fa, b_fc, b_pid = [], [], [], [], []
    start = 0
    for pid, pname in enumerate(order):
        for k, r in enumerate(ranks):
            for name, d in r['bnd']:
                t = d['type']
                target = d['referPatch'] if t == 'processorCyclic' else name
                if t == 'processor' or target != pname:
                    continue
                s, n = int(d['startFace']), int(d['nFaces'])
                sl = slice(s, s + n)
                b_own.append(r['own'][sl] + cell_off[k])
                b_fn.append(r['fn'][sl])
                b_fa.append(r['fa'][sl])
                b_fc.append(r['fc'][sl])
                b_pid.append(np.full(n, pid, np.int32))
                rows_of[k][name] = np.arange(start, start + n)
                start += n
                info[pname].n_faces += n
    nb = start
    own_b = np.concatenate(b_own) if b_own else np.zeros(0, np.int64)
    fn_b = np.concatenate(b_fn) if b_fn else np.zeros((0, 3))
    fa_b = np.concatenate(b_fa) if b_fa else np.zeros(0)
    fc_b = np.concatenate(b_fc) if b_fc else np.zeros((0, 3))
    cc = np.concatenate([r['cc'] for r in ranks])
    # outward unit normals of the boundary faces: refine flips each (cell, face) normal away from the cell centre
    flip = np.einsum('ij,ij->i', fc_b - cc[own_b], fn_b) < 0
    normal = np.where(flip[:, None], -fn_b, fn_b)
    kinds = np.array([KIND_OF_TYPE[info[p].geom_type] for p in order], np.int8)
    pid = np.concatenate(b_pid) if b_pid else np.zeros(0, np.int32)
    edges = np.stack([np.concatenate(int_own + [own_b]),
                      np.concatenate(int_nei + [np.arange(ncell, ncell + nb)])])
    return FoamGraph(
        n_cells=ncell, pos=np.concatenate([cc, fc_b]), volume=np.concatenate([r['cv'] for r in ranks]),
        normal=normal, area=fa_b, kind=np.concatenate([np.zeros(ncell, np.int8), kinds[pid]]), patch=pid,
        edges=edges, patches=[info[p] for p in order], n_points=sum(r['npts'] for r in ranks),
        rank_cell_offset=cell_off[:-1], rank_patch_rows=rows_of, rank_dirs=[r['dir'] for r in ranks],
        rank_proc_faces=proc_faces)
