"""DCTS (500 industrial data-centre cases; docs/dcts_jupiter_design_2026-10-01.md): format checks of the H5
files and the compact per-case cache that training reads (scano.Main.dcts_common).

    PYTHONPATH=src python -m scano.Datasets.dcts inspect <case.h5> [--json out.json]
    PYTHONPATH=src python -m scano.Datasets.dcts check <case.h5> [--json out.json]
    PYTHONPATH=src python -m scano.Datasets.dcts build-cache <case.h5> [...] --meta <meta.json> --out data/dcts_cache_v1
    PYTHONPATH=src python -m scano.Datasets.dcts stats --out data/dcts_cache_v1 [--smoke]
    PYTHONPATH=src python -m scano.Datasets.dcts build-source data/dcts_cache_v1 [--cases ...] [--workers 16]
      (server pairs and the nearest-server index next to each case's cache arrays, which stay unchanged; the source
      features of boundary.source, scano.Datasets.dcts_source)
    PYTHONPATH=src python -m scano.Datasets.dcts build-spec data/dcts_cache_v1 --spec-root data/DCTS/cases
      (the boundary values of the cfdrun case specification next to each case's cache arrays: boundary.bc_input)

`inspect` lists every group, dataset and attribute of a case file. `check_assumptions` tests every assumption the
cache relies on and reports EVERY failure (with what it found) instead of guessing; `build-cache` refuses a file that
fails one. Key names are those of the refine loader that c21-c26 used (Gen-FVGN-refine roi-sampling@7d4a2b3,
src/FVdomain/HomoGraph_dataset.py, sha 2fa97b8c...). Layout confirmed on GTC_dataset_0003 (2026-10-01, design
section 1):
  cpd nodes      rows 0..Ncell-1 are the cells, rows Ncell..N_cpd-1 one node per boundary face, in face order;
  faces          the internal faces first (face_type 0), then the boundary faces in patch order;
  cpd|neighbor_cell [2, F]  one DIRECTED edge per face: owner cell -> neighbour cell, or owner cell -> boundary node;
                 for boundary face j (face index n_internal + j) the edge points to node Ncell + j, which is the
                 face-to-node map the cache uses; the internal faces point to cells only (row 1 < Ncell);
  root counts    the root attributes num_cells, num_faces and num_internal_faces, where present, equal Ncell, F and
                 n_internal counted from the arrays;
  positions      cpd|centroid of a boundary node is face|face_pos of its face on wall and patch faces; on cyclic faces
                 (baffles: master and slave faces coincide) it is the centroid of the cell across the interface
                 (cpd|neighbor_cell_non_cyclic row 1). The cache stores the FACE position for every boundary node;
  normals        cells_face / cells_face_ptr / cells_face_normal / cells_face_area list one row per (cell, face)
                 incidence (cells_face_ptr is the cell index of the row, not a CSR pointer); every boundary face has
                 exactly one incidence, its owner, and that row's normal is the unit outward normal of the face
                 (outward: n . (face centre - owner centroid) > -OUTWARD_EPS epsilons of the stored coordinates);
  patch list     root attribute patch_list (JSON): per patch start/end_idx_cpd_cell, start/end_idx_face, n_faces,
                 geom_type (wall / patch / cyclic), neighbour_patch; sorted by start the cpd ranges tile
                 [Ncell, N_cpd); patches can be EMPTY (two cyclic patches of 0003 have 0 faces);
  fields         field_target/{uvw,p_rgh,temp}_cpd_cell [N_cpd, 3|1|1] (converged, iteration 5000; channel order
                 u, v, w, p_rgh, T); field_unconverged_t1 the same at iteration 1. Its interior values are NOT zero
                 (u, v, w almost all 0, p_rgh 0, T = 295.15 K everywhere: the initial condition); refine zeroed the
                 interior rows in its importer (importer.py:720) and so used t1 only on boundary rows, as the
                 boundary-condition values. The cache keeps only the boundary rows of t1.

Cache (cache version 1): one directory per case, <out>/<case name>/, of uncompressed .npy files (training reads them
with np.load(mmap_mode='r'), so processes on one node share the OS page cache), written to <name>.partial and renamed
when complete:
  pos.npy     float32 [N, 3]   cell centroids, then boundary-face centroids (face|face_pos)
  volume.npy  float32 [Ncell]  cell volumes
  normal.npy  float32 [Nb, 3]  unit outward normals of the boundary faces
  area.npy    float32 [Nb]     boundary-face areas (face|face_area of the boundary faces)
  kind.npy    int8    [N]      0 cell, 1 wall, 2 patch (inlet / outlet), 3 cyclic (cpd|cell_type 0, 3, 1, 99)
  patch.npy   int32   [Nb]     patch index of each boundary face (position in the patch list sorted by start)
  bc.npy      float32 [Nb, 5]  boundary-condition values: field_unconverged_t1 on the boundary rows, u, v, w, p_rgh, T
  edges.npy   int32   [2, F]   cpd|neighbor_cell as stored (directed, NOT symmetrised)
  target.npy  float32 [N, 5]   field_target, u, v, w, p_rgh, T
  case.json   counts, the patch table (name, geom_type, role, number of faces, start; in patch index order), the check
              report, the source file (name, size, mtime, sha256; no path), the converter commit and source sha256,
              the conversion time and peak RSS (peak_rss_mb with peak_rss_scope "case": the peak of this case's checks
              and conversion, the process peak being reset when the case starts (Linux /proc/self/clear_refs);
              "process" where that reset is unavailable: the process maximum so far, which covers every earlier case
              of the same build-cache call). The role comes from text_conditioning/normalized_patch_roles (matched
              by name through text_conditioning/patch_order): server_inlet / server_outlet / acu_supply / acu_return for
              the geom_type "patch" patches, wall, cyclic; it labels eval_dcts's per-patch outputs and is never an input.
Patch names on GTC_dataset_0003 (84 of geom_type "patch"): rack_group_<g>_rack_row_<r>_rack_<k>_..._srv<s>_inlet_patch
(server_inlet, 40), ..._srv<s>_outlet_p<i> (server_outlet, 40), {left,right}_fan_wall_room_crac_<c>_Front_patch
(acu_supply, 2) and ..._Back_patch (acu_return, 2); walls (rack frames, server top / bottom / outlet walls, room,
pillars, CRAC housings) and cyclic baffles (perforated blanking panels, false-ceiling patches) make up the rest.
`stats` computes the normalisation from the TRAINING cases of meta.json only (refine used meta.json's
dataset_statistics, which cover all 500 cases including the test labels; this is a deliberate difference): per-field
mean / std of the targets over every cpd node of every training case (float64 sums), and the fixed position transform
pos' = (pos - pos_min) * pos_scale with pos_min the per-axis minimum and 1 / pos_scale the largest per-axis extent
over the training cases (one isotropic scale, so the training geometries lie in [0, 1] along their longest axis). It
writes stats.json and manifest.json (sha256 of every case.json and of stats.json): the last step after the case set is
complete, because train_cfg --resume refuses a run whose data manifest changed. `--smoke` uses every cached case
whatever its split (development with one local test case): stats.json then says smoke: true and dcts_common refuses
it outside smoke mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

DESIGN = 'docs/dcts_jupiter_design_2026-10-01.md'
NORMAL = 0                                  # refine Utils.utilities.FaceType.NORMAL: an interior cell / face
TARGETS = (('uvw_cpd_cell', 3), ('p_rgh_cpd_cell', 1), ('temp_cpd_cell', 1))     # field order [u, v, w, p_rgh, T]
FIELDS = ('u', 'v', 'w', 'p_rgh', 'T')
PATCH_KEYS = ('start_idx_cpd_cell', 'end_idx_cpd_cell')
GEOM_CELL_TYPE = {'wall': 3, 'patch': 1, 'cyclic': 99}      # patch geom_type -> cpd|cell_type of its boundary nodes
KIND_OF_CELL_TYPE = {0: 0, 3: 1, 1: 2, 99: 3}               # cpd|cell_type -> cache kind (cell, wall, patch, cyclic)
KINDS = ('cell', 'wall', 'patch', 'cyclic')
CACHE_VERSION = 1
CACHE_FILES = ('pos', 'volume', 'normal', 'area', 'kind', 'patch', 'bc', 'edges', 'target')
NORMAL_TOL = 1e-3                           # | |n| - 1 | of the stored unit normals
AREA_RTOL = 1e-5                            # cells_face_area vs face|face_area on the boundary faces
# outward test n . (face centre - owner centroid) > -OUTWARD_EPS * eps(stored dtype) * max |coordinate|: positions are
# stored in float32 (spacing 1.9e-6 m at 16-32 m), so a boundary cell whose centroid lies on its face plane to that
# precision is not an inverted normal (regenerated GTC_dataset_0155, 2026-10-03: -1.8e-7 m on one face, the same value
# after a re-mesh, so set by the geometry)
OUTWARD_EPS = 4


def _jsonable(v, limit=2000):
    """An attribute value as JSON-friendly data (long text and large arrays are summarised)."""
    if isinstance(v, bytes):
        v = v.decode('utf-8', 'replace')
    if isinstance(v, str):
        return v if len(v) <= limit else {'text_head': v[:limit], 'length': len(v)}
    if isinstance(v, np.ndarray):
        if v.dtype.kind in 'SO':
            v = np.array([x.decode('utf-8', 'replace') if isinstance(x, bytes) else str(x) for x in v.ravel()])
        return v.tolist() if v.size <= 64 else {'shape': list(v.shape), 'dtype': str(v.dtype),
                                                'head': v.ravel()[:16].tolist()}
    if isinstance(v, np.generic):
        return v.item()
    return v if isinstance(v, (int, float, bool)) or v is None else repr(v)


def inspect(h5_path):
    """Every object of the file (h5py visititems) as {'name', 'kind', 'shape', 'dtype', 'attrs'}; the root first."""
    import h5py
    rows = []
    with h5py.File(h5_path, 'r') as f:
        rows.append({'name': '/', 'kind': 'group', 'attrs': {k: _jsonable(v) for k, v in f.attrs.items()}})

        def visit(name, obj):
            row = {'name': name, 'kind': 'dataset' if isinstance(obj, h5py.Dataset) else 'group',
                   'attrs': {k: _jsonable(v) for k, v in obj.attrs.items()}}
            if row['kind'] == 'dataset':
                row.update(shape=list(obj.shape), dtype=str(obj.dtype))
            rows.append(row)
        f.visititems(visit)
    return rows


def _text(v):
    if isinstance(v, np.ndarray):
        if v.dtype.kind == 'u' and v.dtype.itemsize == 1:
            return v.tobytes().decode('utf-8')
        if v.shape == ():
            v = v.item()
    if isinstance(v, bytes):
        return v.decode('utf-8')
    if isinstance(v, str):
        return v
    raise TypeError(f'not text: {type(v).__name__}')


def _find_patch_list(f):
    """([(where, loader), ...], searched): every place that could hold the patch list {name: {start_idx_cpd_cell,
    end_idx_cpd_cell, ...}}: an attribute named patch_list (JSON text) on any object, a dataset named patch_list
    holding JSON text, or a group named patch_list whose members carry the two index attributes."""
    import h5py
    found = []

    def consider(where, obj):
        if 'patch_list' in obj.attrs:
            found.append((f'attribute patch_list of {where}', lambda o=obj: json.loads(_text(o.attrs['patch_list']))))
        if where.rsplit('/', 1)[-1] == 'patch_list':
            if isinstance(obj, h5py.Dataset):
                found.append((f'dataset {where}', lambda o=obj: json.loads(_text(o[()]))))
            else:
                found.append((f'group {where}', lambda o=obj: {k: {a: _jsonable(v) for a, v in o[k].attrs.items()}
                                                              for k in o}))
    consider('/', f)
    f.visititems(lambda name, obj: consider(name, obj))
    searched = ['attributes named patch_list on every object', 'datasets named patch_list (JSON text)',
                'groups named patch_list (members with start/end attributes)']
    return found, searched


def _squeeze1(a):
    a = np.asarray(a)
    return a.reshape(-1) if a.ndim == 2 and 1 in a.shape else a


def sorted_patches(patches):
    """[(start, end, name), ...] of the patch list sorted by (start, end, name): the patch index order of the cache.
    Empty patches (start == end) keep their place; they own no boundary face."""
    return sorted((int(m[PATCH_KEYS[0]]), int(m[PATCH_KEYS[1]]), name) for name, m in patches.items())


def check_assumptions(h5_path, strict=True):
    """Test the assumptions the cache relies on (module docstring) on one case file; returns the report
    {'file', 'checks': [{'id', 'required', 'ok', 'statement', 'detail'}], 'facts': {...}, 'failed_required'}. Required
    checks are what build_case relies on; the others are reported, never asserted. strict: raise AssertionError
    listing every failed required check (after running all of them)."""
    import h5py
    checks, facts = [], {}

    def check(cid, statement, ok, detail='', required=True):
        checks.append({'id': cid, 'required': required, 'ok': bool(ok), 'statement': statement, 'detail': detail})
        return bool(ok)

    def read(f, key):
        return f[key][()] if key in f and isinstance(f[key], h5py.Dataset) else None

    with h5py.File(h5_path, 'r') as f:
        lv = f.attrs.get('layout_version')
        check('layout_version', 'root attribute layout_version == 2', lv is not None and int(lv) == 2,
              f'found {_jsonable(lv)!r}')

        ct = read(f, 'cpd|cell_type')
        cen = read(f, 'cpd|centroid')
        ncell = n_cpd = None
        if check('cpd_cell_type', 'cpd|cell_type exists, one entry per cpd node', ct is not None,
                 '' if ct is not None else 'missing'):
            ct = _squeeze1(ct)
            n_cpd = int(ct.shape[0])
            is_cell = ct == NORMAL
            ncell = int(is_cell.sum())
            facts.update(n_cpd=n_cpd, n_cell=ncell, n_boundary_nodes=n_cpd - ncell,
                         cell_type_counts={str(k): int(v) for k, v in zip(*np.unique(ct, return_counts=True))})
            first_bnd = int(np.argmin(is_cell)) if not is_cell.all() else n_cpd
            check('cells_then_boundary_faces', 'cpd nodes: the Ncell cells (cell_type NORMAL) first, then one node per '
                  'boundary face', bool(is_cell[:ncell].all()) and not bool(is_cell[ncell:].any()),
                  f'Ncell = {ncell} of {n_cpd}; first non-cell node at {first_bnd}; '
                  f'cells after it: {int(is_cell[first_bnd:].sum())}')
            unknown = sorted(set(np.unique(ct[ncell:]).tolist()) - set(GEOM_CELL_TYPE.values()))
            check('boundary_cell_types', f'boundary nodes have cell_type in {sorted(GEOM_CELL_TYPE.values())} '
                  '(wall 3, patch 1, cyclic 99)', not unknown, f'other codes: {unknown}')
        cen_ok = check('cpd_centroid', 'cpd|centroid is [N_cpd, 3]', cen is not None and n_cpd is not None
                       and tuple(np.shape(cen)) == (n_cpd, 3), f'shape {None if cen is None else list(np.shape(cen))}')
        if cen_ok:
            facts['bbox'] = [np.asarray(cen).min(0).tolist(), np.asarray(cen).max(0).tolist()]

        vol = read(f, 'cell|cells_volume')
        check('cells_volume', 'cell|cells_volume has Ncell entries (positive)', vol is not None and ncell is not None
              and _squeeze1(vol).shape == (ncell,) and bool((_squeeze1(vol) > 0).all()),
              f'shape {None if vol is None else list(np.shape(vol))}, Ncell {ncell}')

        nb = read(f, 'cpd|neighbor_cell')
        ft = read(f, 'face|face_type')
        n_face = n_int = None
        if check('neighbor_cell_shape', 'cpd|neighbor_cell is [2, F] (one directed edge per face)',
                 nb is not None and np.ndim(nb) == 2 and np.shape(nb)[0] == 2,
                 f'shape {None if nb is None else list(np.shape(nb))}'):
            nb = np.asarray(nb).astype(np.int64)
            n_face = int(nb.shape[1])
            facts['n_face'] = n_face
            if n_cpd is not None:
                check('neighbor_cell_range', 'every index of cpd|neighbor_cell is in [0, N_cpd)',
                      nb.size and nb.min() >= 0 and nb.max() < n_cpd, f'min {nb.min()}, max {nb.max()}, N_cpd {n_cpd}')
                check('boundary_nodes_only_in_row_1', 'boundary nodes (index >= Ncell) appear only in row 1 '
                      '(owner -> neighbour or owner -> boundary node)', ncell is not None
                      and not bool((nb[0] >= ncell).any()),
                      f'row 0 entries >= Ncell: {int((nb[0] >= ncell).sum()) if ncell is not None else "?"}; '
                      f'row 1 entries >= Ncell: {int((nb[1] >= ncell).sum()) if ncell is not None else "?"}')
        else:
            nb = None
        n_bf = None
        if check('face_type', 'face|face_type has F entries', ft is not None and n_face is not None
                 and _squeeze1(ft).shape == (n_face,),
                 f'shape {None if ft is None else list(np.shape(ft))}, F {n_face}'):
            ft = _squeeze1(ft)
            n_bf = int((ft != NORMAL).sum())
            n_int = n_face - n_bf
            facts.update(n_boundary_faces=n_bf, n_internal_faces=n_int)
            check('boundary_face_count', 'boundary faces (face_type != NORMAL) == N_cpd - Ncell',
                  n_cpd is not None and ncell is not None and n_bf == n_cpd - ncell,
                  f'{n_bf} boundary faces, N_cpd - Ncell = {None if n_cpd is None else n_cpd - ncell}')
            check('internal_faces_first', 'faces: the internal faces (face_type NORMAL) first, then the boundary faces',
                  bool((ft[:n_int] == NORMAL).all()) and bool((ft[n_int:] != NORMAL).all()),
                  f'{n_int} internal faces')
        else:
            ft = None
        fa = read(f, 'face|face_area')
        fa_ok = check('face_area', 'face|face_area has F entries, positive on the boundary faces (N_cpd - Ncell of them)',
                      fa is not None and n_face is not None and _squeeze1(fa).shape == (n_face,) and ft is not None
                      and bool((_squeeze1(fa)[ft != NORMAL] > 0).all()),
                      f'shape {None if fa is None else list(np.shape(fa))}, F {n_face}')
        fa = _squeeze1(fa) if fa_ok else None

        # the face -> node map of the cache: boundary face j (face n_int + j) points to node Ncell + j
        order_ok = False
        if nb is not None and n_int is not None and ncell is not None and n_cpd is not None:
            tgt = nb[1, n_int:]
            order_ok = tgt.shape == (n_cpd - ncell,) and bool(np.array_equal(tgt, np.arange(ncell, n_cpd)))
            check('face_order_is_node_order', 'boundary faces in face order point to the boundary nodes in node order '
                  '(cpd|neighbor_cell[1, n_internal + j] == Ncell + j)', order_ok,
                  'the cache maps boundary face j to node Ncell + j')
        else:
            check('face_order_is_node_order', 'boundary faces in face order point to the boundary nodes in node order',
                  False, 'prerequisites missing')
        stmt = ('the internal faces (the first n_internal) connect two cells: cpd|neighbor_cell[1, :n_internal] < '
                'Ncell')
        if nb is not None and n_int is not None and ncell is not None:
            to_bnd = int((nb[1, :n_int] >= ncell).sum())
            check('internal_faces_between_cells', stmt, to_bnd == 0,
                  f'{to_bnd} of {n_int} internal faces point to a boundary node (index >= Ncell = {ncell})')
        else:
            check('internal_faces_between_cells', stmt, False, 'prerequisites missing')

        # the root count attributes (GTC_dataset_0003 has all three), where present, against the counted values
        counted = {'num_cells': ncell, 'num_faces': n_face, 'num_internal_faces': n_int}
        stored = {k: int(f.attrs[k]) for k in counted if k in f.attrs}
        facts['root_counts'] = stored
        off = [f'{k} = {v} but {counted[k]} counted' for k, v in stored.items() if v != counted[k]]
        check('root_counts', 'the root attributes num_cells / num_faces / num_internal_faces, where present, equal '
              'Ncell (cpd|cell_type), F (cpd|neighbor_cell) and n_internal (face|face_type)', not off,
              '; '.join(off) if off else (f'{len(stored)} of 3 present, all equal' if stored else 'none present'))

        # positions: face_pos of the boundary faces against the cpd centroids of their nodes
        fp = read(f, 'face|face_pos')
        nnc = read(f, 'cpd|neighbor_cell_non_cyclic')
        pos_ok, detail = False, 'prerequisites missing (face|face_pos, cpd|centroid, face order)'
        if order_ok and cen_ok and fp is not None and np.shape(fp) == (n_face, 3):
            cyc = ct[ncell:] == GEOM_CELL_TYPE['cyclic']
            fpb, cb = np.asarray(fp)[n_int:], np.asarray(cen)[ncell:]
            non_cyc = bool(np.array_equal(fpb[~cyc], cb[~cyc]))
            detail = (f'non-cyclic boundary nodes ({int((~cyc).sum())}): face_pos == cpd|centroid '
                      f'{"exactly" if non_cyc else "NOT everywhere (max |diff| %.3g)" % np.abs(fpb[~cyc] - cb[~cyc]).max()}')
            cyc_ok = True
            if cyc.any():
                if nnc is None or np.shape(nnc) != (2, n_face):
                    cyc_ok = False
                    detail += '; cyclic nodes present but cpd|neighbor_cell_non_cyclic [2, F] missing'
                else:
                    across = np.asarray(nnc)[1, n_int:][cyc]
                    cyc_ok = bool((across < ncell).all()) and bool(np.array_equal(cb[cyc], np.asarray(cen)[across]))
                    detail += (f'; cyclic boundary nodes ({int(cyc.sum())}): cpd|centroid == centroid of the cell across '
                               f'the interface {"exactly" if cyc_ok else "NOT everywhere"}; face_pos is the face centroid '
                               f'(max |face_pos - cpd centroid| {np.abs(fpb[cyc] - cb[cyc]).max():.4g})')
            pos_ok = non_cyc and cyc_ok
        check('boundary_positions', 'face|face_pos of every boundary face equals the cpd centroid of its node on walls and '
              'patches, and on cyclic faces the cpd centroid is the centroid of the cell across the interface', pos_ok,
              detail)

        # unit outward normals from the (cell, face) incidence rows
        cfc, cfp = read(f, 'cells_face'), read(f, 'cells_face_ptr')
        cfn, cfa = read(f, 'cells_face_normal'), read(f, 'cells_face_area')
        nrm_ok, detail = False, 'prerequisites missing (cells_face, cells_face_ptr, cells_face_normal, cells_face_area)'
        if (order_ok and cen_ok and fa is not None and fp is not None and cfc is not None and cfp is not None
                and cfn is not None and cfa is not None):
            cfc, cfp, cfa = _squeeze1(cfc).astype(np.int64), _squeeze1(cfp).astype(np.int64), _squeeze1(cfa)
            cfn = np.asarray(cfn)
            if not (cfc.shape == cfp.shape == cfa.shape and cfn.shape == (cfc.shape[0], 3)):
                detail = f'shapes {cfc.shape} {cfp.shape} {cfn.shape} {cfa.shape} do not match'
            else:
                count = np.bincount(cfc[cfc >= n_int] - n_int, minlength=n_bf)
                m = cfc >= n_int
                owner = bool(np.array_equal(cfp[m], nb[0, cfc[m]]))
                norm = np.linalg.norm(cfn[m].astype(np.float64), axis=1)
                out = np.einsum('ij,ij->i', cfn[m].astype(np.float64),
                                np.asarray(fp, np.float64)[cfc[m]] - np.asarray(cen, np.float64)[cfp[m]])
                dt = np.promote_types(np.asarray(fp).dtype, np.asarray(cen).dtype)
                eps = np.finfo(dt if np.issubdtype(dt, np.floating) else np.float64).eps
                out_tol = OUTWARD_EPS * eps * max(float(np.abs(fp).max()), float(np.abs(cen).max()))
                rel = np.abs(cfa[m].astype(np.float64) - fa[cfc[m]]) / fa[cfc[m]]
                once = bool((count == 1).all())
                nrm_ok = (once and owner and bool((np.abs(norm - 1) <= NORMAL_TOL).all()) and bool((out > -out_tol).all())
                          and bool((rel <= AREA_RTOL).all()))
                detail = (f'boundary faces with exactly one incidence: {int((count == 1).sum())} of {n_bf}; incidence '
                          f'cell == owner: {owner}; |n| in [{norm.min():.7f}, {norm.max():.7f}]; '
                          f'n . (face - owner centroid) min {out.min():.3g} (must be > -{out_tol:.2g}, {OUTWARD_EPS} '
                          f'epsilons of the stored coordinates; {int((out <= 0).sum())} faces at or below 0); '
                          f'cells_face_area vs face_area '
                          f'max rel diff {rel.max():.2g}')
        check('boundary_normals', 'every boundary face has exactly one (cell, face) incidence, its owner, with a unit '
              'outward normal and the face area', nrm_ok, detail)

        for key, width in TARGETS:
            a = read(f, f'field_target/{key}')
            check(f'target_{key}', f'field_target/{key} is [N_cpd, {width}] and finite', a is not None
                  and n_cpd is not None and np.shape(a) == (n_cpd, width) and bool(np.isfinite(a).all()),
                  f'shape {None if a is None else list(np.shape(a))}')

        t1 = f.get('field_unconverged_t1')
        t1_ok, detail = False, 'group missing'
        if t1 is not None and ncell is not None:
            missing = [k for k, _ in TARGETS if k not in t1]
            if missing:
                detail = f'members missing: {missing} (has {sorted(t1)})'
            else:
                parts = [np.asarray(t1[k][()]) for k, _ in TARGETS]
                shapes_ok = all(np.shape(p) == (n_cpd, w) for p, (_, w) in zip(parts, TARGETS))
                if not shapes_ok:
                    detail = f'shapes {[list(np.shape(p)) for p in parts]}'
                else:
                    a = np.concatenate([p.reshape(n_cpd, -1) for p in parts], 1)
                    t1_ok = bool(np.isfinite(a[ncell:]).all())
                    cells = a[:ncell]
                    facts['t1_interior'] = {fld: {'fraction_zero': float((cells[:, i] == 0).mean()),
                                                  'min': float(cells[:, i].min()), 'max': float(cells[:, i].max())}
                                            for i, fld in enumerate(FIELDS)}
                    detail = ('boundary rows finite: %s; interior (not used) fraction exactly 0 per field: %s; interior '
                              'T range [%.6g, %.6g]' % (t1_ok, ', '.join(f"{k} {v['fraction_zero']:.4f}" for k, v in
                                                                          facts['t1_interior'].items()),
                                                         cells[:, 4].min(), cells[:, 4].max()))
        check('unconverged_t1', 'field_unconverged_t1/{uvw,p_rgh,temp}_cpd_cell are [N_cpd, 3|1|1], finite on the '
              'boundary rows (the boundary-condition values)', t1_ok, detail)

        found, searched = _find_patch_list(f)
        ok = False
        patches = None
        if not found:
            detail = 'not found; searched: ' + '; '.join(searched)
        else:
            where = found[0][0]
            detail = f'found as {where}' + (f' (also: {[w for w, _ in found[1:]]})' if len(found) > 1 else '')
            try:
                patches = found[0][1]()
                ranges = sorted_patches(patches)
                empty = [name for s, e, name in ranges if s == e]
                facts.update(n_patches=len(ranges), n_empty_patches=len(empty), empty_patches=empty)
                cover = ncell is not None and bool(ranges) and ranges[0][0] == ncell and ranges[-1][1] == n_cpd \
                    and all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])) and all(s <= e for s, e, _ in ranges)
                ok = cover
                detail += (f'; {len(ranges)} patches ({len(empty)} empty), index ranges '
                           f'{"tile" if cover else "do NOT tile"} [Ncell, N_cpd) = [{ncell}, {n_cpd})')
            except Exception as e:                       # reported, never guessed around
                patches = None
                detail += f'; not parsable as {{name: {{{", ".join(PATCH_KEYS)}}}}}: {type(e).__name__}: {e}'
        check('patch_list', 'the patch list is stored, parsable, and its cpd index ranges tile the boundary nodes '
              '[Ncell, N_cpd) (empty patches allowed)', ok, detail)

        # each patch: face range consistent with its node range, and its nodes' cell_type that of its geom_type
        p_ok, detail = False, 'prerequisites missing'
        if ok and n_int is not None:
            bad = []
            for s, e, name in sorted_patches(patches):
                m = patches[name]
                gt = m.get('geom_type')
                if gt not in GEOM_CELL_TYPE:
                    bad.append(f'{name}: geom_type {gt!r}')
                    continue
                fs, fe = m.get('start_idx_face'), m.get('end_idx_face')
                if fs is None or fe is None or int(fs) - n_int != s - ncell or int(fe) - int(fs) != e - s \
                        or int(m.get('n_faces', e - s)) != e - s:
                    bad.append(f'{name}: face range [{fs}, {fe}) vs node range [{s}, {e})')
                if e > s and not bool((ct[s:e] == GEOM_CELL_TYPE[gt]).all()):
                    bad.append(f'{name}: cell_type of its nodes is not {GEOM_CELL_TYPE[gt]} ({gt})')
            p_ok = not bad
            facts['geom_type_counts'] = {g: sum(1 for m in patches.values() if m.get('geom_type') == g)
                                         for g in GEOM_CELL_TYPE}
            detail = f'{len(bad)} inconsistent patches' + (f': {bad[:5]}' if bad else '')
        check('patch_ranges_consistent', 'every patch has a known geom_type, a face range that matches its node range '
              '(start_idx_face - n_internal == start_idx_cpd_cell - Ncell, n_faces == length), and nodes of the '
              'matching cell_type', p_ok, detail)

        # reported only
        v1 = sorted(k for k, _ in TARGETS if k in f)
        check('no_v1_arrays', 'no layout-v1 flat field arrays left at the root after the v2 migration', not v1,
              f'root field arrays: {v1}', required=False)
        bt = f.get('face|boundary_type')
        check('boundary_type', 'face|boundary_type (per-field BC type codes) is present (not used by the cache)',
              bt is not None, f'shape {None if bt is None else list(bt.shape)}', required=False)

    failed = [c for c in checks if c['required'] and not c['ok']]
    report = {'file': Path(h5_path).name, 'design': DESIGN, 'checks': checks, 'facts': facts,
              'failed_required': [c['id'] for c in failed]}
    if strict and failed:
        raise AssertionError(f'{h5_path}: {len(failed)} assumption(s) of {DESIGN} section 1 do not hold:\n'
                             + '\n'.join(f"  {c['id']}: {c['statement']} -- {c['detail']}" for c in failed))
    return report


# ---------------------------------------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------------------------------------

def _sha256_file(path, block=1 << 24):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(block), b''):
            h.update(chunk)
    return h.hexdigest()


def _code_record():
    """Commit of the repository and sha256 of this file (provenance of a cache; no paths)."""
    here = Path(__file__).resolve()
    root = here.parents[3]
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
                                         stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', str(here)], cwd=root, capture_output=True,
                               text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, 'git unavailable'
    return {'git_commit': commit, 'converter': 'src/scano/Datasets/dcts.py', 'converter_sha256': _sha256_file(here),
            'converter_uncommitted': bool(dirty)}


def _peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0      # Linux: kilobytes


def _vm_hwm_mb():
    """VmHWM of /proc/self/status (the peak RSS since the process started or since the last reset), MiB; None
    where unavailable."""
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmHWM:'):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


def _reset_peak_rss():
    """Reset this process's peak RSS to its current RSS (Linux >= 4.0: 5 written to /proc/self/clear_refs, which
    touches nothing else), so that VmHWM afterwards is the peak of what follows; False where unavailable."""
    try:
        with open('/proc/self/clear_refs', 'w') as f:
            f.write('5')
    except OSError:
        return False
    return _vm_hwm_mb() is not None


def patch_roles(f, patches):
    """{patch name: role} from text_conditioning/normalized_patch_roles (server_inlet, server_outlet, acu_supply,
    acu_return, wall, cyclic on GTC_dataset_0003), matched by name through text_conditioning/patch_order; {} when the
    file has no roles. Reported in case.json for the per-patch outputs of eval_dcts (never a model input)."""
    tc = f.get('text_conditioning')
    if tc is None or 'normalized_patch_roles' not in tc or 'patch_order' not in tc:
        return {}
    dec = lambda a: [x.decode('utf-8') if isinstance(x, bytes) else str(x) for x in np.asarray(a).ravel()]  # noqa
    order, roles = dec(tc['patch_order'][()]), dec(tc['normalized_patch_roles'][()])
    if len(order) != len(roles):
        raise AssertionError(f'text_conditioning: {len(order)} patch names but {len(roles)} roles')
    unknown = sorted(set(order) - set(patches))
    if unknown:
        raise AssertionError(f'text_conditioning/patch_order names patches not in the patch list: {unknown[:5]}')
    return dict(zip(order, roles))


def case_arrays(h5_path):
    """The cache arrays of one case (dict name -> numpy array) and its patch table; check_assumptions must have
    passed on the file."""
    import h5py
    with h5py.File(h5_path, 'r') as f:
        ct = _squeeze1(f['cpd|cell_type'][()]).astype(np.int64)
        n = ct.shape[0]
        ncell = int((ct == NORMAL).sum())
        ft = _squeeze1(f['face|face_type'][()])
        n_int = int((ft == NORMAL).sum())
        pos = np.asarray(f['cpd|centroid'][()], np.float32).copy()
        pos[ncell:] = np.asarray(f['face|face_pos'][()], np.float32)[n_int:]
        volume = _squeeze1(f['cell|cells_volume'][()]).astype(np.float32)
        area = _squeeze1(f['face|face_area'][()]).astype(np.float32)[n_int:]
        cfc = _squeeze1(f['cells_face'][()]).astype(np.int64)
        m = cfc >= n_int
        normal = np.zeros((n - ncell, 3), np.float32)
        normal[cfc[m] - n_int] = np.asarray(f['cells_face_normal'][()], np.float32)[m]
        lut = np.full(max(KIND_OF_CELL_TYPE) + 1, -1, np.int8)
        lut[list(KIND_OF_CELL_TYPE)] = list(KIND_OF_CELL_TYPE.values())
        kind = lut[ct]
        patches = _find_patch_list(f)[0][0][1]()             # the patch list check_assumptions parsed
        table = sorted_patches(patches)
        patch = np.full(n - ncell, -1, np.int32)
        for j, (s, e, _) in enumerate(table):
            patch[s - ncell:e - ncell] = j
        t1 = f['field_unconverged_t1']
        bc = np.concatenate([np.asarray(t1[k][()], np.float32).reshape(n, -1)[ncell:] for k, _ in TARGETS], 1)
        edges = np.asarray(f['cpd|neighbor_cell'][()]).astype(np.int32)
        tg = f['field_target']
        target = np.concatenate([np.asarray(tg[k][()], np.float32).reshape(n, -1) for k, _ in TARGETS], 1)
        n_points = int(f.attrs['num_points']) if 'num_points' in f.attrs else None
        roles = patch_roles(f, patches)
    if (patch < 0).any():
        raise AssertionError(f'{h5_path}: {int((patch < 0).sum())} boundary faces outside every patch')
    info = [{'name': name, 'geom_type': patches[name]['geom_type'], 'role': roles.get(name), 'n_faces': e - s,
             'start': s, 'neighbour_patch': patches[name].get('neighbour_patch')} for s, e, name in table]
    arrays = {'pos': pos, 'volume': volume, 'normal': normal, 'area': area, 'kind': kind, 'patch': patch, 'bc': bc,
              'edges': edges, 'target': target}
    return arrays, info, {'n_nodes': int(n), 'n_cells': ncell, 'n_boundary': int(n - ncell), 'n_faces': int(ft.shape[0]),
                          'n_internal_faces': n_int, 'n_points': n_points}


def _split_of(meta, name):
    for split, key in (('train', 'train_cases'), ('val', 'val_cases'), ('test', 'test_cases')):
        if name in meta['case_splits'].get(key, []):
            return split
    return None


def install_meta(out_root, meta_path):
    """Copy meta.json into the cache root (once); a different meta.json already there is refused."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    dst = out_root / 'meta.json'
    if dst.exists():
        if _sha256_file(dst) != _sha256_file(meta_path):
            raise SystemExit(f'{dst} exists and differs from {meta_path}: one cache, one meta.json')
    else:
        shutil.copyfile(meta_path, dst)
    return json.loads(dst.read_text())


def build_case(h5_path, out_root, meta=None):
    """Check one H5 file (refuse on any failed required check) and write its cache directory <out_root>/<name>
    (via <name>.partial, renamed when complete). An existing complete directory is never overwritten."""
    t0 = time.time()
    h5_path = Path(h5_path)
    name = h5_path.stem
    out_root = Path(out_root)
    final, part = out_root / name, out_root / f'{name}.partial'
    if final.exists():
        raise FileExistsError(f'{final} exists (a cache directory is never overwritten)')
    per_case_rss = _reset_peak_rss()            # case.json's peak RSS is this case's, not the batch's so far
    report = check_assumptions(h5_path, strict=True)
    st = h5_path.stat()
    source = {'file': h5_path.name, 'bytes': st.st_size,
              'mtime_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(st.st_mtime)), 'sha256': _sha256_file(h5_path)}
    arrays, info, counts = case_arrays(h5_path)
    norm = np.linalg.norm(arrays['normal'].astype(np.float64), axis=1)
    if not (np.abs(norm - 1) <= NORMAL_TOL).all():           # every boundary face got its normal
        raise AssertionError(f'{h5_path}: {int((np.abs(norm - 1) > NORMAL_TOL).sum())} boundary normals not unit')
    if part.exists():
        shutil.rmtree(part)                     # an interrupted earlier conversion
    part.mkdir(parents=True)
    nbytes = 0
    for key in CACHE_FILES:
        np.save(part / f'{key}.npy', np.ascontiguousarray(arrays[key]))
        nbytes += (part / f'{key}.npy').stat().st_size
    rec = {'cache_version': CACHE_VERSION, 'case': name, 'split': _split_of(meta, name) if meta else None,
           **counts, 'fields': list(FIELDS), 'kinds': list(KINDS), 'patches': info,
           'n_empty_patches': sum(1 for p in info if p['n_faces'] == 0),
           'kind_counts': {k: int((arrays['kind'] == i).sum()) for i, k in enumerate(KINDS)},
           'files': {k: {'shape': list(arrays[k].shape), 'dtype': str(arrays[k].dtype)} for k in CACHE_FILES},
           'cache_bytes': nbytes, 'source': source, 'checks': {c['id']: c['ok'] for c in report['checks']},
           'facts': report['facts'], **_code_record(),
           'convert_seconds': round(time.time() - t0, 2), **_rss_record(per_case_rss),
           'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (part / 'case.json').write_text(json.dumps(rec, indent=2) + '\n')
    os.replace(part, final)
    return rec


def _rss_record(per_case):
    """{'peak_rss_mb', 'peak_rss_scope'} for case.json (module docstring)."""
    hwm = _vm_hwm_mb() if per_case else None
    if hwm is not None:
        return {'peak_rss_mb': round(hwm, 1), 'peak_rss_scope': 'case'}
    return {'peak_rss_mb': round(_peak_rss_mb(), 1), 'peak_rss_scope': 'process'}


def cached_cases(out_root):
    """Names of the complete case directories (with case.json) under the cache root, sorted."""
    root = Path(out_root)
    return sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.endswith('.partial')
                  and (d / 'case.json').is_file())


def compute_stats(out_root, smoke=False):
    """stats.json + manifest.json of the cache (module docstring). Non-smoke: every training case of meta.json must be
    cached; only they enter. smoke: every cached case enters, whatever its split, and the files say so."""
    root = Path(out_root)
    meta = json.loads((root / 'meta.json').read_text())
    present = cached_cases(root)
    train = list(meta['case_splits']['train_cases'])
    if smoke:
        cases = present
        if not cases:
            raise SystemExit(f'{root}: no cached case')
    else:
        missing = [c for c in train if c not in present]
        if missing:
            raise SystemExit(f'{root}: {len(missing)} of the {len(train)} training cases of meta.json are not cached '
                             f'(first: {missing[:5]}); stats come from all training cases (or --smoke)')
        cases = train
    for name in ('stats.json', 'manifest.json'):
        if (root / name).exists():
            raise SystemExit(f'{root / name} exists (never overwritten; move it away to recompute)')
    s = np.zeros(len(FIELDS))
    ss = np.zeros(len(FIELDS))
    count = 0
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for c in cases:
        t = np.load(root / c / 'target.npy', mmap_mode='r').astype(np.float64)
        s += t.sum(0)
        ss += (t ** 2).sum(0)
        count += t.shape[0]
        p = np.load(root / c / 'pos.npy', mmap_mode='r')
        lo = np.minimum(lo, p.min(0))
        hi = np.maximum(hi, p.max(0))
    mean = s / count
    std = np.sqrt(np.maximum(ss / count - mean ** 2, 0.0))
    extent = float((hi - lo).max())
    stats = {'mean': mean.tolist(), 'std': std.tolist(), 'fields': list(FIELDS),
             'pos_min': lo.tolist(), 'pos_scale': 1.0 / extent, 'pos_max': hi.tolist(),
             'n_nodes': int(count), 'cases': list(cases), 'smoke': bool(smoke),
             'source': 'smoke: every cached case, whatever its split (development only)' if smoke else
             'the training cases of meta.json (case_splits.train_cases)',
             'meta_sha256': _sha256_file(root / 'meta.json'), 'cache_version': CACHE_VERSION, **_code_record(),
             'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (root / 'stats.json').write_text(json.dumps(stats, indent=2) + '\n')
    manifest = {'cache_version': CACHE_VERSION, 'meta_sha256': stats['meta_sha256'],
                'stats_sha256': _sha256_file(root / 'stats.json'),
                'cases': {c: _sha256_file(root / c / 'case.json') for c in present}}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return stats


def _build_source_one(case_dir):
    from . import dcts_source
    try:
        rec = dcts_source.build_source(case_dir, {**_code_record(), **dcts_source.module_record()})
    except FileExistsError:
        return case_dir.name, 'skipped (source.json exists)', True
    except (AssertionError, OSError, KeyError, ValueError, IndexError) as e:
        return case_dir.name, f'REFUSED: {type(e).__name__}: {e}', False
    f = rec['facts']
    return case_dir.name, (f"{f['n_servers']} servers, P {f['P_total_W'] / 1e3:.1f} kW, unpaired inlets "
                           f"{f['n_server_inlets_unpaired']}, inlet T_t1 dev {f['inlet_T_t1_max_dev_K']:.2g} K, "
                           f"{rec['nearest']['nodes_within_cap']}/{rec['nearest']['nodes']} nodes within "
                           f"{rec['constants']['D_CAP']:g} m, {rec['seconds']} s"), True


def build_sources(root, cases=None, workers=1):
    """build-source: dcts_source.build_source for every cached case (or `cases`), then source_manifest.json. Returns
    the exit code (1 when a case was refused)."""
    from . import dcts_source
    root = Path(root)
    names = list(cases) if cases else cached_cases(root)
    dirs = [root / n for n in names]
    missing = [d.name for d in dirs if not (d / 'case.json').is_file()]
    if missing:
        print(f'not cached: {missing[:5]}', file=sys.stderr)
        return 2
    if workers > 1:
        from multiprocessing import Pool
        with Pool(workers) as pool:
            results = pool.map(_build_source_one, dirs, chunksize=1)
    else:
        results = [_build_source_one(d) for d in dirs]
    ok = True
    for name, msg, good in results:
        print(f'{name}: {msg}', file=sys.stdout if good else sys.stderr, flush=True)
        ok &= good
    man = dcts_source.write_manifest(root, {**_code_record(), **dcts_source.module_record()})
    print(f"{root / dcts_source.MANIFEST}: {len(man['cases'])} cases with source files", flush=True)
    return 0 if ok else 1


def _build_spec_one(args):
    from . import dcts_source
    case_dir, spec_root = args
    try:
        rec = dcts_source.build_spec(case_dir, spec_root, {**_code_record(), **dcts_source.module_record()})
    except FileExistsError:
        return case_dir.name, 'skipped (spec_bc.json exists)', True
    except (AssertionError, OSError, KeyError, ValueError, IndexError) as e:
        return case_dir.name, f'REFUSED: {type(e).__name__}: {e}', False
    f = rec['facts_vs_t1']
    dev = ', '.join(f"{r} dT {v['max_abs_dT_vs_t1']:.1e} K" + (f" dU {v['max_rel_dU_vs_t1']:.1e}" if v['max_rel_dU_vs_t1']
                                                                is not None else '') for r, v in f.items())
    return case_dir.name, f"{len(rec['servers'])} servers; vs t1: {dev}; {rec['seconds']} s", True


def build_specs(root, spec_root, cases=None, workers=1):
    """build-spec: dcts_source.build_spec for every cached case (or `cases`), then spec_manifest.json. Exit code 1 when a
    case was refused."""
    from . import dcts_source
    root = Path(root)
    names = list(cases) if cases else cached_cases(root)
    jobs = [(root / n, spec_root) for n in names]
    if workers > 1:
        from multiprocessing import Pool
        with Pool(workers) as pool:
            results = pool.map(_build_spec_one, jobs, chunksize=1)
    else:
        results = [_build_spec_one(j) for j in jobs]
    ok = True
    for name, msg, good in results:
        print(f'{name}: {msg}', file=sys.stdout if good else sys.stderr, flush=True)
        ok &= good
    man = dcts_source.write_spec_manifest(root, {**_code_record(), **dcts_source.module_record()})
    print(f"{root / dcts_source.SPEC_MANIFEST}: {len(man['cases'])} cases with spec_bc.json", flush=True)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description='DCTS H5 checks and cache (docs/dcts_jupiter_design_2026-10-01.md)')
    sub = ap.add_subparsers(dest='command', required=True)
    for cmd in ('inspect', 'check'):
        p = sub.add_parser(cmd)
        p.add_argument('h5')
        p.add_argument('--json', help='also write the result to this file')
    b = sub.add_parser('build-cache', help='check and convert H5 case files into the per-case cache')
    b.add_argument('h5', nargs='+')
    b.add_argument('--out', required=True, help='cache root, for example data/dcts_cache_v1')
    b.add_argument('--meta', required=True, help='meta.json of the H5 set (copied into the cache root)')
    s = sub.add_parser('stats', help='train-only normalisation statistics and the cache manifest')
    s.add_argument('--out', required=True, help='cache root')
    s.add_argument('--smoke', action='store_true', help='development only: every cached case, whatever its split')
    bs = sub.add_parser('build-source', help='server pairs and nearest-server index of every cached case, written next '
                                             'to its cache arrays (boundary.source; scano.Datasets.dcts_source)')
    bs.add_argument('root', help='cache root, for example data/dcts_cache_v1')
    bs.add_argument('--cases', nargs='+', help='only these cases (default: every cached case)')
    bs.add_argument('--workers', type=int, default=1)
    sp = sub.add_parser('build-spec', help='boundary values from the cfdrun case specification of every cached case '
                                           '(boundary.bc_input = "spec"; scano.Datasets.dcts_source)')
    sp.add_argument('root', help='cache root')
    sp.add_argument('--spec-root', required=True, help='the cfdrun specs: <dir>/case_<number>/logs or <dir>/<case>/logs')
    sp.add_argument('--cases', nargs='+')
    sp.add_argument('--workers', type=int, default=1)
    args = ap.parse_args(argv)
    if args.command == 'build-spec':
        sys.exit(build_specs(args.root, args.spec_root, args.cases, args.workers))
    if args.command == 'build-source':
        sys.exit(build_sources(args.root, args.cases, args.workers))
    if args.command == 'inspect':
        result = inspect(args.h5)
        for r in result:
            print(f"{r['kind']:7s} {r['name']:50s} {str(r.get('shape', '')):22s} {r.get('dtype', ''):10s} "
                  f"{json.dumps(r['attrs'])[:200] if r['attrs'] else ''}")
    elif args.command == 'check':
        result = check_assumptions(args.h5, strict=False)
        for c in result['checks']:
            tag = ('ok  ' if c['ok'] else 'FAIL') if c['required'] else ('yes ' if c['ok'] else 'no  ')
            print(f"{tag} {'   ' if c['required'] else '(q)'} {c['id']:30s} {c['detail']}")
        print(json.dumps(result['facts']))
    elif args.command == 'build-cache':
        meta = install_meta(args.out, args.meta)
        failed = []
        for h5 in args.h5:
            name = Path(h5).stem
            if (Path(args.out) / name / 'case.json').is_file():
                print(f'{name}: cached already, skipped', flush=True)
                continue
            try:
                rec = build_case(h5, args.out, meta)
            except (AssertionError, OSError, KeyError, ValueError, IndexError) as e:   # the batch goes on
                failed.append(name)
                print(f'{name}: REFUSED: {type(e).__name__}: {e}', file=sys.stderr, flush=True)
                continue
            print(f"{name}: {rec['n_nodes']} nodes ({rec['n_cells']} cells, {rec['n_boundary']} boundary faces, "
                  f"{len(rec['patches'])} patches, {rec['n_empty_patches']} empty), {rec['cache_bytes'] / 2**20:.1f} MiB "
                  f"in {rec['convert_seconds']} s, peak RSS {rec['peak_rss_mb']:.0f} MiB"
                  + ('' if rec['peak_rss_scope'] == 'case' else ' (process maximum so far)'), flush=True)
        if failed:
            sys.exit(1)
        return
    else:
        st = compute_stats(args.out, smoke=args.smoke)
        print(json.dumps({k: st[k] for k in ('mean', 'std', 'pos_min', 'pos_scale', 'n_nodes', 'smoke')}))
        print(f"{len(st['cases'])} cases; {'SMOKE statistics (development only)' if st['smoke'] else 'training cases'}")
        return
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + '\n')
    if args.command == 'check' and result['failed_required']:
        sys.exit(1)


if __name__ == '__main__':
    main()
