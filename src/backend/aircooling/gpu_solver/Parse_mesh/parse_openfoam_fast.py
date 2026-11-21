"""Vectorised OpenFOAM -> LPCNS case writer (serial or decomposed input, no reconstructParMesh).

Writes the same ``<out>/<case_name>.h5`` datasets and attributes as ``parse_openfoam_refactored.py``
(``parse_base.save_to_hdf5``) and the same ``<out>/patchinfo.toml``, from ``foamio.decomposed.read_case``:

    python src/Parse_mesh/parse_openfoam_fast.py --case <foam case> --out <h5 dir> [--geometry_dtype float64]
        [--keep connected_open] [--case_name NAME]

Conventions reproduced from the slow parser (``parse_openfoam_refactored.OpenFOAMManager`` and ``parse_boundary``):

* compound cells = interior cells, then one ghost per boundary face in patch order (ghost of face f is
  ``n_cells + f - n_internal_faces``); ``cpd|neighbor_cell`` = (owner, neighbour-or-ghost) per face;
* ``cells_face`` per cell: the faces it owns (ascending), then the faces it neighbours (ascending);
* ``edge_node`` in order of first appearance while walking the faces, ``face_edge`` per face point;
* ``pv_cells_node``: VTK polyhedron stream per cell, a face's points fully reversed where the cell is its neighbour;
* ``cpd|neighbor_cell_x``: unique sorted (min, max) pairs of the 1-hop (and k-hop for ``[stencil] khops`` >= 2)
  compound adjacency; ``gnn|*``: the CSR arrays of ``build_gnn_csr_format``; ``cyclic_face``: (2, n) face pairs.

Two deliberate differences (both no-ops on the regression cases, see ``tests/test_parse_fast.py``):

* ``cells_node`` lists each cell's points in ascending order (the slow parser writes Python ``set`` iteration order,
  which is not reproducible in vectorised code); the sets per cell are identical.
* Geometry follows OpenFOAM's topological definitions (``foamio.decomposed.fast_geometry`` = ``primitiveMesh``):
  ``cells_face_normal`` points out of the cell by owner / neighbour, not by the sign of (face centre - cell centre);
  the number of cell-face pairs where the two rules differ is logged (zero on convex cells).

Extra datasets (ignored by ``Load_mesh``, which loads every dataset generically): ``cell|proc`` (int16/int32, the
OpenFOAM rank of each cell = the decomposition), ``cell|proc_local``, ``face|proc``, ``face|proc_local`` (rank and
local face of the defining side), ``face|proc_nb``, ``face|proc_local_nb`` (the other side of a processor face, -1
elsewhere) and, for a subset (``--keep``), ``cell|source_cell`` (global cell id before the subset). Root attribute
``decomposition`` (JSON). ``bc/`` is NOT written (the dctwin importer writes it); ``fvconfig.toml`` is copied from
the parser default when the output directory has none, as the slow parser does (``Load_mesh`` requires it).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from Parse_mesh.foamio.decomposed import FoamCase, read_case, _flat_index  # noqa: E402

log = logging.getLogger("parse_openfoam_fast")

# Utils.utilities.FaceType (NORMAL 0, PATCH 1, EMPTY 2, WALL 3, SYMMETRY 4, CYCLIC 5); parse_boundary maps wall to PATCH
_FACE_TYPE = {"cyclic": 5, "empty": 2, "symmetryplane": 4, "symmetry": 4, "wall": 1, "patch": 1, "inlet": 1,
              "outlet": 1}
DEFAULT_FVCONFIG = Path(__file__).resolve().parent / "fvconfig.toml"


def _face_type(geom_type: str) -> int:
    g = (geom_type or "").strip().lower()
    if not g:
        return 0
    if g not in _FACE_TYPE:
        raise ValueError(f"Unsupported geometry type '{geom_type}'")  # parse_boundary._map_geom_to_face_type
    return _FACE_TYPE[g]


def _excl(a: np.ndarray) -> np.ndarray:
    return np.cumsum(a) - a


def _unique_sorted(x: np.ndarray) -> np.ndarray:
    """np.unique by sorting (numpy's hash-based unique is several times slower on 10^7 int64 keys)."""
    x = np.sort(x)
    if x.size:
        x = x[np.concatenate([[True], x[1:] != x[:-1]])]
    return x


def _khop_from_fvconfig(path: Path) -> tuple[int, dict]:
    import tomlkit
    from Utils.toml_utils import toml_to_native
    with open(path, "r", encoding="utf-8") as f:
        cfg = toml_to_native(tomlkit.load(f))
    khop = int(cfg.get("stencil", {}).get("khops", 1))
    grad = str(cfg.get("fvSchemes", {}).get("gradSchemes", "1st"))
    if khop < 1:
        khop = 1
    if khop == 1 and grad in ("2nd", "3rd", "4th"):
        khop = 2  # parse_openfoam_refactored.calc_neighbor_cpd_cell_x clamps the same way
    return khop, cfg


SKIPPABLE = ("edges", "cells_node", "pyvista")


def build_arrays(case: FoamCase, khop: int = 1, skip=()) -> dict:
    """Every dataset of the LPCNS mesh h5 (float64 geometry; the writer casts), plus diagnostics.

    ``skip``: dataset groups a caller that only solves does not read and that cost a sort over all face corners
    each -- "edges" (edge_node, face_edge, face_edge_ptr), "cells_node" (cells_node, cells_node_ptr), "pyvista"
    (pv_cells_node, pv_cells_type; the VTK export needs them)."""
    unknown = set(skip) - set(SKIPPABLE)
    if unknown:
        raise ValueError(f"build_arrays: unknown dataset groups {sorted(unknown)} (known: {SKIPPABLE})")
    t0 = time.perf_counter()
    N, F, Fi = case.n_cells, case.n_faces, case.n_internal_faces
    P = int(case.points.shape[0])
    off, fp = case.face_offsets, case.face_points
    fsz = np.diff(off)
    own, nei = case.owner, case.neighbour
    out: dict = {}
    diag: dict = {}

    # ---- faces: nodes, edges
    face_ptr = np.repeat(np.arange(F, dtype=np.int64), fsz)
    out["face_node"] = fp.astype(np.int64)
    out["face_node_ptr"] = face_ptr
    if "edges" not in skip:
        nxt = np.arange(fp.size, dtype=np.int64) + 1
        nxt[off[1:] - 1] = off[:-1]
        a, b = fp, fp[nxt]
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        key = lo * P + hi
        uniq, first, inv = np.unique(key, return_index=True, return_inverse=True)
        order = np.argsort(first, kind="stable")          # edges numbered in order of first appearance
        rank = np.empty(uniq.size, dtype=np.int64)
        rank[order] = np.arange(uniq.size)
        out["edge_node"] = np.stack([lo[first[order]], hi[first[order]]])
        out["face_edge"] = rank[inv.reshape(-1)]
        out["face_edge_ptr"] = face_ptr
        del key, uniq, inv, nxt, a, b, lo, hi, first, order, rank

    # ---- cells: faces (owned ascending, then neighboured ascending)
    cf_cell = np.concatenate([own, nei])
    cf_face = np.concatenate([np.arange(F, dtype=np.int64), np.arange(Fi, dtype=np.int64)])
    cf_rev = np.concatenate([np.zeros(F, dtype=bool), np.ones(Fi, dtype=bool)])
    srt = np.argsort(cf_cell * 2 + cf_rev, kind="stable")
    cf_cell, cf_face, cf_rev = cf_cell[srt], cf_face[srt], cf_rev[srt]
    del srt
    out["cells_face"] = cf_face
    out["cells_face_ptr"] = cf_cell

    # ---- cells: nodes (ascending per cell)
    if "cells_node" not in skip:
        idx, _ = _flat_index(off, cf_face)
        cn_cell = np.repeat(cf_cell, fsz[cf_face])
        k2 = _unique_sorted(cn_cell * P + fp[idx])
        out["cells_node"] = k2 % P
        out["cells_node_ptr"] = k2 // P
        del k2, cn_cell

    # ---- pyvista polyhedron stream
    if "pyvista" not in skip:
        flen = fsz[cf_face]
        blen = 1 + flen
        nf = np.bincount(cf_cell, minlength=N)
        L = 1 + np.bincount(cf_cell, weights=blen, minlength=N).astype(np.int64)
        cstart = _excl(1 + L)
        total = int((1 + L).sum())
        pv = np.empty(total, dtype=np.int64)
        pv[cstart] = L
        pv[cstart + 1] = nf
        first_pair = _excl(nf)                              # first cell-face pair of each cell
        bex = _excl(blen)
        pstart = cstart[cf_cell] + 2 + (bex - bex[first_pair[cf_cell]])
        pv[pstart] = flen
        kk = np.arange(int(flen.sum()), dtype=np.int64) - np.repeat(_excl(flen), flen)
        rev_r = np.repeat(cf_rev, flen)
        src = np.repeat(off[cf_face], flen) + np.where(rev_r, np.repeat(flen, flen) - 1 - kk, kk)
        pv[np.repeat(pstart, flen) + 1 + kk] = fp[src]
        out["pv_cells_node"] = pv
        out["pv_cells_type"] = np.full(N, 42, dtype=np.int64)   # pyvista.CellType.POLYHEDRON
        del kk, rev_r, src, pv, pstart, bex

    # ---- geometry
    if np.any(case.V <= 0):
        raise ValueError(f"Found non-positive cell volumes! ({int((case.V <= 0).sum())} cells)")
    nhat = case.Sf / case.magSf[:, None]
    out["node|node_pos"] = case.points
    out["face|face_pos"] = case.Cf
    out["face|face_area"] = case.magSf
    out["face|face_normal"] = nhat
    out["cell|cell_pos"] = case.C
    out["cell|cell_volume"] = case.V[:, None]
    sgn = np.where(cf_rev, -1.0, 1.0)
    cfn = nhat[cf_face] * sgn[:, None]
    geo = np.einsum("ij,ij->i", case.Cf[cf_face] - case.C[cf_cell], nhat[cf_face]) < 0  # the slow parser's rule
    diag["cell_face_orientation_rule_differs"] = int((geo != cf_rev).sum())
    out["cells_face_normal"] = cfn
    out["cells_face_area"] = case.magSf[cf_face][:, None]
    sv = cfn * case.magSf[cf_face][:, None]
    clos = np.stack([np.bincount(cf_cell, weights=sv[:, k], minlength=N) for k in range(3)], 1)
    tot = np.bincount(cf_cell, weights=case.magSf[cf_face], minlength=N)
    defect = np.linalg.norm(clos, axis=1) / tot
    diag["closure_max"] = float(defect.max(initial=0.0))
    if diag["closure_max"] > 1e-9:  # parse_openfoam_refactored._validate_face_normal
        bad = int((defect > 1e-9).sum())
        raise ValueError(f"face-area closure failed on {bad} of {N} cells (max {diag['closure_max']:.3e})")
    del sv, clos, tot, defect, geo, cfn, sgn

    # ---- face types, compound cells
    ftype = np.zeros(F, dtype=np.int64)
    for p in case.patches:
        if p.size:
            ftype[p.start:p.start + p.size] = _face_type(p.type)
    out["face|face_type"] = ftype[:, None]
    nb = np.empty((2, F), dtype=np.int64)
    nb[0] = own
    nb[1, :Fi] = nei
    nb[1, Fi:] = N + np.arange(F - Fi)
    out["cpd|neighbor_cell"] = nb
    out["cpd|cell_type"] = np.concatenate([np.zeros(N, dtype=np.int64), ftype[Fi:]])[:, None]
    out["cpd|cell_pos"] = np.concatenate([case.C, case.Cf[Fi:]])
    Ncpd = N + F - Fi

    # cyclic pairs in patch order (parse_boundary._build_cyclic_pairs)
    pairs, done = [], set()
    names = {p.name: p for p in case.patches}
    for p in case.patches:
        if p.type.strip().lower() not in ("cyclic", "cyclicami") or p.name in done:
            continue
        q = names.get(p.neighbour_patch) if p.neighbour_patch else None
        if q is None or q.type.strip().lower() not in ("cyclic", "cyclicami") or p.size != q.size:
            continue
        pairs.append(np.stack([np.arange(p.start, p.start + p.size), np.arange(q.start, q.start + q.size)]))
        done.update((p.name, q.name))
    cyc = np.concatenate(pairs, axis=1).astype(np.int64) if pairs else np.full((2, 1), -1, dtype=np.int64)
    out["cyclic_face"] = cyc
    nnc = nb.copy()
    if (cyc[0] > 0).all():
        nnc[1, cyc[0]] = nb[0, cyc[1]]
        nnc[1, cyc[1]] = nb[0, cyc[0]]
    out["cpd|neighbor_cell_non_cyclic"] = nnc

    # ---- extended stencil
    lo, hi = np.minimum(nb[0], nb[1]), np.maximum(nb[0], nb[1])
    keys = [lo * Ncpd + hi]
    if khop >= 2:
        from scipy.sparse import coo_matrix
        tw = np.concatenate([nb, nb[::-1]], axis=1)
        A = coo_matrix((np.ones(tw.shape[1]), (tw[0], tw[1])), shape=(Ncpd, Ncpd)).tocsr()
        Ak = A
        for _k in range(2, khop + 1):
            Ak = Ak @ A
            c = Ak.tocoo()
            m = c.row != c.col
            r_, c_ = c.row[m].astype(np.int64), c.col[m].astype(np.int64)
            keys.append(np.minimum(r_, c_) * Ncpd + np.maximum(r_, c_))
    ux = _unique_sorted(np.concatenate(keys))
    out["cpd|neighbor_cell_x"] = np.stack([ux // Ncpd, ux % Ncpd])
    del keys, ux, lo, hi

    # ---- GNN CSR (build_gnn_csr_format)
    sources = np.concatenate([nb[0], nb[1]])
    receivers = np.concatenate([nb[1], nb[0]])
    sraw = np.argsort(receivers, kind="stable")
    out["gnn|edge_indices"] = sources[sraw]
    out["gnn|edge_counts"] = np.bincount(receivers[sraw], minlength=Ncpd).astype(np.int64)
    out["gnn|edge_sort_idx"] = np.where(sraw < F, 2 * sraw, 2 * (sraw - F) + 1)
    del sources, receivers, sraw

    # ---- decomposition maps (extra datasets)
    R = case.n_procs
    out["cell|proc"] = case.cell_proc.astype(np.int16 if R < 32767 else np.int32)
    out["cell|proc_local"] = case.cell_local.astype(np.int32 if case.cell_local.max(initial=0) < 2**31 else np.int64)
    fproc = np.full(F, -1, dtype=np.int32)
    floc = np.full(F, -1, dtype=np.int32)
    fproc_nb = np.full(F, -1, dtype=np.int32)
    floc_nb = np.full(F, -1, dtype=np.int32)
    for rm in case.ranks:
        g = rm.face_global
        ok = g >= 0
        d = ok & ~rm.face_flip
        fproc[g[d]] = rm.rank
        floc[g[d]] = np.flatnonzero(d)
        f2 = ok & rm.face_flip
        fproc_nb[g[f2]] = rm.rank
        floc_nb[g[f2]] = np.flatnonzero(f2)
    out["face|proc"], out["face|proc_local"] = fproc, floc
    out["face|proc_nb"], out["face|proc_local_nb"] = fproc_nb, floc_nb
    if case.source_cells is not None:
        out["cell|source_cell"] = case.source_cells.astype(np.int64)
    diag["build_s"] = time.perf_counter() - t0
    return {"datasets": out, "diag": diag}


GEOMETRY = ("node|node_pos", "face|face_pos", "cell|cell_pos", "face|face_area", "face|face_normal",
            "cell|cell_volume", "cpd|cell_pos", "cells_face_area", "cells_face_normal")


def _provenance(case_path: str) -> dict:
    out = {"writer": "Parse_mesh.parse_openfoam_fast", "case_path": str(case_path)}
    try:
        out["git"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=os.path.dirname(os.path.abspath(__file__)),
                                    capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        out["git"] = ""
    return out


def write_h5(path: Path, case: FoamCase, arrays: dict, case_name: str, geometry_dtype: str = "float32") -> None:
    import h5py
    from Utils import mesh_schema
    gdt = np.float64 if geometry_dtype == "float64" else np.float32
    ds = arrays["datasets"]
    with h5py.File(path, "w") as hf:
        hf.attrs["case_name"] = case_name
        hf.attrs["dimension"] = "3D"
        hf.attrs["case_path"] = str(case.path)
        hf.attrs["num_points"] = int(case.points.shape[0])
        hf.attrs["num_faces"] = case.n_faces
        hf.attrs["num_cells"] = case.n_cells
        hf.attrs["num_internal_faces"] = case.n_internal_faces
        if geometry_dtype == "float64":
            hf.attrs["geometry_dtype"] = "float64"
        for k, v in ds.items():
            hf.create_dataset(k, data=v.astype(gdt) if k in GEOMETRY else v)
        hf.attrs["decomposition"] = json.dumps({
            "n_procs": case.n_procs, "decomposed": case.decomposed, "proc_cell_offsets": case.proc_cell_offsets.tolist(),
            "rank_dirs": [str(r.path) for r in case.ranks], "subset": case.report.get("subset"),
            "point_merge": case.report.get("point_merge"),
            "cyclic_pairs": [{k: c[k] for k in ("patch_a", "patch_b", "transform")} for c in case.cyclic_pairs]},
            default=str)
        schema = mesh_schema.single_region(case.n_cells)
        schema.provenance = {**_provenance(str(case.path)), **schema.provenance}
        mesh_schema.write(hf, schema)


def write_patchinfo(path: Path, case: FoamCase) -> None:
    """``patchinfo.toml`` exactly as ``parse_boundary.export_patch_list_to_toml`` writes it."""
    import tomlkit
    doc = tomlkit.document()
    doc.add(tomlkit.comment("Patch list information from OpenFOAM polyMesh/boundary"))
    doc.add(tomlkit.nl())
    meta = tomlkit.table()
    meta["case_path"] = str(case.path)
    meta["description"] = "Patch information including face indices and compound cell indices"
    meta["num_nodes"] = int(case.points.shape[0])
    meta["num_faces"] = case.n_faces
    meta["num_cells"] = case.n_cells
    meta["num_internal_faces"] = case.n_internal_faces
    doc["metadata"] = meta
    doc.add(tomlkit.nl())
    sec = tomlkit.table()
    N, Fi = case.n_cells, case.n_internal_faces
    for p in case.patches:
        t = tomlkit.table()
        t["patch_name"] = p.name
        t["start_face"] = p.start
        t["n_faces"] = p.size
        t["geom_type"] = p.type
        nbp = p.dict.get("neighbourPatch")
        if nbp is not None:
            t["neighbour_patch"] = str(nbp)
        else:
            t.add(tomlkit.comment("neighbour_patch = null (not a cyclic patch)"))
        t["start_idx_face"] = p.start
        t["end_idx_face"] = p.start + p.size
        t["start_idx_cpd_cell"] = N + p.start - Fi
        t["end_idx_cpd_cell"] = N + p.start + p.size - Fi
        sec[p.name] = t
    doc["patch"] = sec
    with open(path, "w", encoding="utf-8") as f:
        tomlkit.dump(doc, f)


def convert(case_dir: str | Path, out_dir: str | Path, case_name: str | None = None, geometry_dtype: str | None = None,
            keep: str | None = None, keep_mask=None, layout: str = "auto", workers: int | None = None,
            case: FoamCase | None = None, skip=(), arrays: dict | None = None) -> dict:
    """Read ``case_dir`` (or take the already read ``case``) and write ``<out_dir>/<case_name>.h5`` +
    ``patchinfo.toml`` (+ default fvconfig.toml).  ``skip``: dataset groups left out (:func:`build_arrays`); ``arrays``: the
    result of :func:`build_arrays` for this ``case`` when the caller already has it."""
    t0 = time.perf_counter()
    case_dir, out_dir = Path(case_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_name = case_name or case_dir.name
    gname = (geometry_dtype or os.environ.get("LPCNS_GEOMETRY_DTYPE", "float32")).strip().lower()
    if gname not in ("float32", "float64"):
        raise ValueError(f"geometry dtype must be float32 or float64, got {gname!r}")
    if case is None:
        case = read_case(case_dir, layout=layout, keep=keep, keep_mask=keep_mask, workers=workers)
    t1 = time.perf_counter()
    fvc = out_dir / "fvconfig.toml"
    if not fvc.exists():
        shutil.copy(DEFAULT_FVCONFIG, fvc)
        log.warning("No fvconfig.toml in %s, copied the parser default %s", out_dir, DEFAULT_FVCONFIG)
    khop, _ = _khop_from_fvconfig(fvc)
    if arrays is None:
        arrays = build_arrays(case, khop=khop, skip=skip)
    t2 = time.perf_counter()
    h5 = out_dir / f"{case_name}.h5"
    write_h5(h5, case, arrays, case_name, gname)
    write_patchinfo(out_dir / "patchinfo.toml", case)
    t3 = time.perf_counter()
    info = {"h5": str(h5), "summary": case.summary(), "diag": arrays["diag"], "khop": khop,
            "timing_s": {"read_case": t1 - t0, "build": t2 - t1, "write": t3 - t2, "total": t3 - t0},
            "report": {k: v for k, v in case.report.items()}}
    log.info("wrote %s: %s", h5, json.dumps(info["timing_s"]))
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Vectorised OpenFOAM (serial or decomposed) -> LPCNS h5 case")
    ap.add_argument("--case", required=True, help="OpenFOAM case directory (constant/polyMesh or processorN/)")
    ap.add_argument("--out", required=True, help="output directory for <case_name>.h5 and patchinfo.toml")
    ap.add_argument("--case_name", default=None, help="h5 name and case_name attribute (default: case dir name)")
    ap.add_argument("--geometry_dtype", choices=("float32", "float64"), default=None,
                    help="storage type of the geometry arrays (default: $LPCNS_GEOMETRY_DTYPE, else float32)")
    ap.add_argument("--keep", choices=("connected_open",), default=None,
                    help="drop the connected components that touch no open (type patch) boundary")
    ap.add_argument("--layout", choices=("auto", "serial", "decomposed"), default="auto")
    ap.add_argument("--workers", type=int, default=None, help="threads for the per-rank reads")
    ap.add_argument("--report", default=None, help="write the conversion report (JSON) here")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    info = convert(args.case, args.out, args.case_name, args.geometry_dtype, args.keep, layout=args.layout,
                   workers=args.workers)
    if args.report:
        Path(args.report).write_text(json.dumps(info, indent=1, default=str))
    print(json.dumps({"h5": info["h5"], "summary": info["summary"], "timing_s": info["timing_s"],
                      "diag": info["diag"]}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
