"""DCTS data for the config-driven framework (dataset.name = "dcts"; docs/dcts_jupiter_design_2026-10-01.md).

GtcData has the interface of olbc_common.OlbcData / r0s_common.R0SData, so cfg_common, train_cfg, select_cfg and
eval_dcts run every backbone on it. Unlike olbc and R0-S, EVERY CASE HAS ITS OWN MESH. The framework therefore calls
`data.use_case(case)` (cfg_common.use_case, a no-op for data objects without it) before it touches a case, and the
mesh attributes below refer to that current case:
  n            number of nodes (cells, then one node per boundary face);
  area         cell volume / mean cell volume of the case on the cells, 0 on boundary-face nodes (the context measure;
               boundary faces never enter the global state under the area measure);
  volume       physical cell volume, 0 on boundary-face nodes;
  centroid_cpu physical positions (cell centroids, boundary-face centroids) on the CPU, what refine's ROI box reads;
  subgraph     k-hop on cpd|neighbor_cell AS STORED (directed owner -> neighbour / owner -> boundary node, not
               symmetrised; refine_sampling.khop_nodes), so coverage matches refine and c21-c26 (a boundary face enters
               a k-hop sample only as a pivot);
  edge_index   the message graph for graph backbones (mgn): those edges in both directions, built on first use;
  anchor_pool  the cells (decoupled anchors are drawn from cells only);
  node_keys    index-based keys for uniform subsets (subset_sampling.NodeKeys interface; identity = (kind, node
               index) of the case, no centroid quantisation, since the meshes differ per case). Kept for a later arm:
               run_config refuses sampling.regime = "uniform" for dcts, because select_cfg and eval_dcts deploy dcts
               runs with refine's k-hop inference, which a uniform subset does not define.
Methods that take `case` (node_features, boundary, record_patch_ids, target) RAISE when it is not the current case,
so a missing use_case call fails loudly instead of mixing two meshes.

Features (stats from the training cases only, scano.Datasets.dcts.compute_stats):
  node_in 15  x', y', z' (pos' = (pos - pos_min) * pos_scale), unit normal (3; zero on cells), kind one-hot
              (cell, wall, patch, cyclic), BC values u, v, w, p_rgh, T (5; z-scored with the training target mean / std,
              zero on cells): bc_cols = (10, 15);
  bnd_in 14   per boundary face: x', y', z', unit normal, kind one-hot (wall, patch, cyclic), BC values (5, z-scored);
              record weight = face area (boundary.records = "patch" aggregates them per patch: record_patch_ids);
  types       boundary.types = "role" (J7, decision d31) appends a role one-hot (server_inlet, server_outlet,
              acu_supply, acu_return; ROLES) to the boundary rows of the node features and to the records: node_in 19,
              bnd_in 18 (feature_widths). The role of a face is that of its patch in the cache's case.json (from the
              case specification: tools/dcts_regen/jupiter/text_case.py); walls and baffles keep the zero vector, the
              kind one-hot already tells them apart. On DCTS every per-field boundary type is fixed by the role (0003:
              each role has one set of per-field types, docs/dcts_jupiter_design_2026-10-01.md section 1.1), so the
              role one-hot carries the per-field types as well. A case whose patches have no role is refused.
              boundary.types = "none" (c50, the type ablation, H6) zeroes the kind one-hot of the boundary rows of the
              node features and of the records: the model sees the boundary values, positions and normals but no type
              (cells keep their cell flag, so boundary nodes stay recognisable); widths as for "kind".
  source      boundary.source (d49; scano.Datasets.dcts_source, which needs `dcts build-source` on the cache) appends
              heat-source columns after the role one-hot (fixed physical scales, no statistics):
                heat       F1, 3 columns on the boundary rows of the node features and on the records: the heat-flux
                           density q / Q_REF on server-outlet faces, the server's load P / P_REF and its temperature
                           rise dT / DT_REF (the jump datum) on its outlet and
                           inlet faces (zero elsewhere and on cells; on the node features they are BC data, zeroed with
                           the bc columns when boundary.node_bc is off: `source_bc_cols`);
                heat_near  F1 plus F2, 6 columns on every node: the nearest server outlet within D_CAP (3 m): its
                           P / P_REF, dT / DT_REF, offset / D_CAP (3) and distance / D_CAP (zeros and 1 beyond);
                heat_near_jet  F1 plus F2 in the exhaust frame of that server (R29 diagnosis of c66), 10 columns on
                           every node (dcts_source.F2J_COLUMNS): P / P_REF, dT / DT_REF, the offset along its exhaust
                           jet, lateral and vertical / D_CAP, the signed distance (+ downstream) / D_CAP, the distance
                           / D_CAP and the jet direction (room axes); the jet is the area-weighted boundary velocity
                           of its outlet faces in the boundary values the case uses (t1 or spec);
              node_in 15 (+4 role) (+3 heat) (+6 near | +10 near_jet), bnd_in 14 (+4 role) (+3 heat) (feature_widths).
              boundary.source_measure gives the server-outlet face records another record weight than their area:
              "flux" their mass-flux measure a |U| / U_MASS (independent of the load; Proposition W1), "heat" their
              load a q / Q_MASS (kW as "m^2"); every other record keeps its area,
              and `face_area` (the physical area that eval_dcts averages with) never changes.
  bc_input    boundary.bc_input = "spec" (R29 audit item 9; `dcts build-spec`, scano.Datasets.dcts_source) takes the boundary
              values from the cfdrun case specification instead of the first solver iteration: U = -/+ (Q / A) n on the
              server outlets / inlets and CRAC supplies, T_INIT, outlets T_INIT + P / (rho cp Q), supplies their set
              point, rest elsewhere; the p_rgh column is zero (the training mean): no solver output enters; F1 / F2 use
              the specification's server table.
  targets     [P, 5] u, v, w, p_rgh, T in physical units; data.mean / data.std are the per-field training statistics.
BC values = field_unconverged_t1 on the boundary rows only (the cache never stores interior t1): solver-free.

Splits (meta.json of the cache root, never a directory listing, because refine's pivot seeds are 12345 + case index):
  train split = meta train_cases then val_cases (case index i < n_train trains, the rest validate; dataset.val_count
                must equal the number of validation cases), test split = meta test_cases. Every listed case must be
                cached.
  statistics    (check_stats) computed for this cache's meta.json (stats meta_sha256 == sha256 of <root>/meta.json)
                and, outside smoke mode, from exactly the training cases of meta.json: a case outside them or a
                training case missing from them is refused. The provenance records whether they cover the training
                split (stats_cover_train).
  SMOKE (environment SCANO_DCTS_SMOKE=1, development with the one local case): every split is made of the cached
                cases, whatever their meta split (train: repeated SCANO_DCTS_SMOKE_REPEAT times, default 4, so the
                training draws differ; validation: repeated to val_count); stats must be smoke stats (stats --smoke).
                Refused when the cache holds every training case of meta.json (a real cache: use its splits). The
                data object's `provenance` says so, train_cfg writes it to config.json and status.json and prints
                it; select_cfg and eval_dcts refuse a smoke run (or smoke mode) unless --allow-smoke (refuse_smoke).
Memory: case arrays are np.load(mmap_mode='r') (one page cache per node) and the device tensors of at most
SCANO_DCTS_CACHE_CASES cases (default 4, least recently used) are kept. With SCANO_DCTS_RAM_CACHE=1 every case a
process opens is read into host RAM once (its arrays and case.json) and later switches build the tensors from RAM:
on a parallel file system the nine opens and header reads of a case cost about 0.8 s (JUPITER Lustre, 10-03
profile, docs/JUPITER.md section 6), longer than a training step. The 400 DCTS training cases take about 38 GB per
process (about 40 GB with the validation cases; JUPITER nodes have about 480 GB of host memory besides the GPUs'
HBM). Values are identical either way; off by default (shared machines).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from ..Datasets import dcts
from ..Datasets.design_sampling import with_owned_faces
from ..Datasets.refine_sampling import khop_nodes
from ..Datasets.subset_sampling import NodeKeys, _mix, z_order

SMOKE_ENV, SMOKE_REPEAT_ENV, CACHE_ENV = 'SCANO_DCTS_SMOKE', 'SCANO_DCTS_SMOKE_REPEAT', 'SCANO_DCTS_CACHE_CASES'
RAM_ENV = 'SCANO_DCTS_RAM_CACHE'


def env(name, default=''):
    """The environment variable `name`, or its name before the data set was renamed DCTS (SCANO_GTC_*, 10-14)."""
    return os.environ.get(name, os.environ.get(name.replace('SCANO_DCTS_', 'SCANO_GTC_'), default))
_RAM = {}                       # case directory -> (arrays, patch table), filled only with SCANO_DCTS_RAM_CACHE=1
_ZORDER = {}                    # case directory -> int32 Z-order of its cells (stratified anchors), once per process
_ZONE = {}                      # (case directory, radius) -> bool server zone of its nodes (train.zone_weight)
ZONE_ROLES = ('server_inlet', 'server_outlet')                            # train.zone_weight: the server zone
NODE_IN, BND_IN, BC_COLS = 15, 14, (10, 15)
BC_T_NODE, BC_T_REC = BC_COLS[0] + 4, 9 + 4      # T column of the boundary values: node features, records
ROLES = ('server_inlet', 'server_outlet', 'acu_supply', 'acu_return')     # boundary.types = "role"
SOURCES = ('none', 'heat', 'heat_near', 'heat_near_jet')                   # boundary.source
F1, F2, F2J = 3, 6, 10                                                    # source columns (heat, near, near_jet)
MEASURES = ('area', 'heat', 'flux')                                        # boundary.source_measure


def feature_widths(types='kind', source='none'):
    """(node_in, bnd_in) for boundary.types and boundary.source."""
    if source not in SOURCES:
        raise ValueError(f'boundary.source {source!r} not in {SOURCES}')
    extra = len(ROLES) if types == 'role' else 0
    f1 = F1 if source != 'none' else 0
    f2 = {'heat_near': F2, 'heat_near_jet': F2J}.get(source, 0)
    return NODE_IN + extra + f1 + f2, BND_IN + extra + f1


def source_bc_cols(types='kind', source='none'):
    """Node-feature columns of F1 (BC data on boundary points, zeroed when boundary.node_bc is off), or None."""
    if source == 'none':
        return None
    a = NODE_IN + (len(ROLES) if types == 'role' else 0)
    return a, a + F1


def role_onehot(patches, patch_ids, name):
    """[Nb, len(ROLES)] role one-hot of the boundary faces from the case.json patch table (zero for wall / cyclic)."""
    roles = [p.get('role') for p in patches]
    if any(r is None for r in roles):
        raise ValueError(f'{name}: case.json has patches without a role (rebuild the cache from H5 files with '
                         'text_conditioning; tools/dcts_regen/jupiter/text_case.py)')
    rid = torch.tensor([ROLES.index(r) if r in ROLES else -1 for r in roles], dtype=torch.long)
    face = rid[torch.as_tensor(np.asarray(patch_ids), dtype=torch.long)]
    out = torch.zeros(face.numel(), len(ROLES))
    hit = face >= 0
    out[hit, face[hit]] = 1.0
    return out


def server_zone_mask(pos, ncell, face_roles, radius, workers=4):
    """bool [N] server zone of one case (train.zone_weight; run_config): the boundary-face nodes whose role is in
    ZONE_ROLES and the cells whose centroid lies within `radius` metres of the centroid of one of those faces (the
    distance rule of eval_dcts_zones' near_<role> zones). pos [N, 3] physical positions (cells, then faces);
    face_roles [Nb] the role of every boundary face (case.json)."""
    from scipy.spatial import cKDTree
    pos = np.asarray(pos, np.float64)
    out = np.zeros(pos.shape[0], bool)
    sel = np.isin(np.asarray(face_roles, dtype=object), ZONE_ROLES)
    out[ncell:] = sel
    if sel.any():
        cnt = cKDTree(pos[ncell:][sel]).query_ball_point(pos[:ncell], radius, workers=workers, return_length=True)
        out[:ncell] = cnt > 0
    return out


def server_faces(servers, patch_ids, n_patches):
    """(in_face, in_server, out_face, out_server) int64 arrays: the boundary-face indices of every server-inlet and
    server-outlet face of the server table (source.json / spec_bc.json `servers`, patch indices in 'inlets' /
    'outlets'; dcts_source.face_columns uses the same map) and the index of their server. A table naming a patch twice
    is refused."""
    pid = np.asarray(patch_ids, np.int64)
    in_srv = np.full(n_patches, -1, np.int64)
    out_srv = np.full(n_patches, -1, np.int64)
    for k, s in enumerate(servers):
        for p in s['inlets']:
            if in_srv[p] >= 0:
                raise ValueError(f'server table: inlet patch {p} belongs to two servers')
            in_srv[p] = k
        for p in s['outlets']:
            if out_srv[p] >= 0:
                raise ValueError(f'server table: outlet patch {p} belongs to two servers')
            out_srv[p] = k
    fi, fo = np.flatnonzero(in_srv[pid] >= 0), np.flatnonzero(out_srv[pid] >= 0)
    return fi, in_srv[pid[fi]], fo, out_srv[pid[fo]]


def smoke_mode():
    return env(SMOKE_ENV, '') == '1'


def index_keys(kind):
    """subset_sampling.NodeKeys for one case keyed by (kind, node index): the same hashing as NodeKeys, the node
    index in place of the quantised centroid (unique by construction)."""
    keys = NodeKeys.__new__(NodeKeys)
    n = len(kind)
    keys.ident = np.stack([np.asarray(kind, np.int64), np.arange(n, dtype=np.int64), np.zeros(n, np.int64),
                           np.zeros(n, np.int64)], 1)
    h = _mix(keys.ident[:, 0].astype(np.uint64))
    for c in range(1, keys.ident.shape[1]):
        h = _mix(h ^ keys.ident[:, c].astype(np.uint64))
    keys.base = h
    keys.n = n
    return keys


def load_case_files(d):
    """The cache arrays and the case.json patch table of case directory d: memory-mapped, or, with
    SCANO_DCTS_RAM_CACHE=1, read once into this process's RAM and reused (module docstring)."""
    if env(RAM_ENV, '') != '1':
        return ({k: np.load(d / f'{k}.npy', mmap_mode='r') for k in dcts.CACHE_FILES},
                json.loads((d / 'case.json').read_text())['patches'])
    key = os.path.abspath(d)                    # no file-system lookup per switch (Lustre)
    if key not in _RAM:
        _RAM[key] = ({k: np.load(d / f'{k}.npy') for k in dcts.CACHE_FILES},
                     json.loads((d / 'case.json').read_text())['patches'])
    return _RAM[key]


def load_spec_files(d):
    """The spec_bc.json record of case directory d (dcts_source.load_spec), kept in RAM with SCANO_DCTS_RAM_CACHE=1."""
    from ..Datasets import dcts_source
    if env(RAM_ENV, '') != '1':
        return dcts_source.load_spec(d)
    key = os.path.abspath(d) + '#spec'
    if key not in _RAM:
        _RAM[key] = dcts_source.load_spec(d)
    return _RAM[key]


def load_source_files(d):
    """(source.json record, nearest-server index) of case directory d (dcts_source.load_source), kept in RAM with
    SCANO_DCTS_RAM_CACHE=1 like the cache arrays."""
    from ..Datasets import dcts_source
    if env(RAM_ENV, '') != '1':
        return dcts_source.load_source(d)
    key = os.path.abspath(d) + '#source'
    if key not in _RAM:
        _RAM[key] = dcts_source.load_source(d)
    return _RAM[key]


def _source_columns(d, a, patches, ncell, feat, bfeat, source, source_measure, bc_phys, spec=None):
    """Append the boundary.source columns (module docstring, scano.Datasets.dcts_source) to the node features [N, *]
    and the records [Nb, *] of case directory d; returns (feat, bfeat, record weights [Nb] float64 or None)."""
    from ..Datasets import dcts_source as gs
    rec, nearest = load_source_files(d)
    servers = rec['servers'] if spec is None else spec['servers']      # bc_input = "spec": the specification's table
    if spec is not None and [s['stem'] for s in spec['servers']] != [s['stem'] for s in rec['servers']]:
        raise ValueError(f'{d}: the servers of spec_bc.json and source.json differ (F2 indexes source.json\'s table)')
    n = feat.shape[0]
    if nearest.shape != (n,):
        raise ValueError(f'{d}: source_nearest.npy has shape {nearest.shape}, the case has {n} nodes')
    for s in servers:                                   # the table belongs to this patch list
        if any(patches[i].get('role') != 'server_outlet' for i in s['outlets']) or \
                any(patches[i].get('role') != 'server_inlet' for i in s['inlets']):
            raise ValueError(f'{d}/source.json: server {s["stem"]} does not match the patch roles of case.json')
    q, P, dT, speed, out = gs.face_columns(servers, np.asarray(a['patch']), len(patches), bc_phys, a['normal'],
                                           a['area'])
    f1 = torch.tensor(np.stack([q / gs.Q_REF, P / gs.P_REF, dT / gs.DT_REF], 1), dtype=torch.float32)
    node_f1 = torch.zeros(n, F1)
    node_f1[ncell:] = f1
    feat = torch.cat([feat, node_f1], 1)
    bfeat = torch.cat([bfeat, f1], 1)
    if source == 'heat_near':
        feat = torch.cat([feat, gs.node_columns(servers, nearest, a['pos'])], 1)
    elif source == 'heat_near_jet':                     # F2 in the exhaust frame, jets from the boundary values used
        jets = gs.server_jets(servers, a['patch'], len(patches), bc_phys, a['area'])
        feat = torch.cat([feat, gs.node_columns_jet(servers, jets, nearest, a['pos'])], 1)
    w = None
    if source_measure in ('heat', 'flux'):              # outlet records: the load (heat) or the mass flux (flux)
        w = np.asarray(a['area'], np.float64).copy()
        w[out] = w[out] * (q[out] / gs.Q_MASS if source_measure == 'heat' else speed[out] / gs.U_MASS)
        if not (w > 0).all():
            raise ValueError(f'{d}: non-positive source-measure weights on {int((w <= 0).sum())} outlet faces')
    return feat, bfeat, w


class _Case:
    """Device tensors of one case."""

    def __init__(self, root, name, stats, device, types='kind', source='none', source_measure='area', bc_input='t1'):
        d = Path(root) / name
        a, patches = load_case_files(d)
        self.name = name
        self.zkey = os.path.abspath(d)                                         # _ZORDER key (no file-system lookup)
        kind = np.asarray(a['kind']).astype(np.int64)
        n, ncell = kind.shape[0], a['volume'].shape[0]
        nb = n - ncell
        if a['pos'].shape != (n, 3) or a['bc'].shape != (nb, 5) or a['target'].shape != (n, 5) \
                or a['normal'].shape != (nb, 3) or a['area'].shape != (nb,) or a['patch'].shape != (nb,):
            raise ValueError(f'{d}: inconsistent cache arrays')
        self.n, self.ncell = n, ncell
        self.kind_np = kind
        f32 = torch.float32
        pos = torch.tensor(np.asarray(a['pos']), dtype=f32)
        self.centroid_cpu = pos                                                 # physical, for the ROI box draw
        mean = torch.tensor(stats['mean'], dtype=torch.float64)
        std = torch.tensor(stats['std'], dtype=torch.float64)
        pos_s = ((pos.double() - torch.tensor(stats['pos_min'], dtype=torch.float64)) * stats['pos_scale']).to(f32)
        onehot = torch.nn.functional.one_hot(torch.as_tensor(kind), len(dcts.KINDS)).to(f32)
        normal = torch.tensor(np.asarray(a['normal']), dtype=f32)
        spec = None
        bc_phys = a['bc']
        if bc_input == 'spec':                       # the case specification instead of the first iteration, no p_rgh
            from ..Datasets import dcts_source as gs
            spec = load_spec_files(d)
            bc_phys = gs.spec_bc(spec, a['patch'], a['normal'])
        bc = ((torch.tensor(np.asarray(bc_phys), dtype=torch.float64) - mean) / std).to(f32)
        if bc_input == 'spec':
            bc[:, 3] = 0.0                           # p_rgh is not specified: its column is the training mean
        feat = torch.zeros(n, NODE_IN, dtype=f32)
        feat[:, 0:3] = pos_s
        feat[ncell:, 3:6] = normal
        feat[:, 6:10] = onehot
        feat[ncell:, BC_COLS[0]:BC_COLS[1]] = bc
        bfeat = torch.cat([pos_s[ncell:], normal, onehot[ncell:, 1:], bc], 1)                         # [Nb, 14]
        self.patches = patches                                                                      # patch table
        if types == 'none':                                       # the type ablation: no kind on boundary rows
            feat[ncell:, 6:10] = 0.0
            bfeat[:, 6:9] = 0.0
        if types == 'role':
            role = role_onehot(self.patches, a['patch'], name)
            feat = torch.cat([feat, torch.cat([torch.zeros(ncell, len(ROLES)), role])], 1)
            bfeat = torch.cat([bfeat, role], 1)
        rec_w = None                                     # record weights: the face areas unless the source measure
        if source != 'none':
            feat, bfeat, rec_w = _source_columns(d, a, patches, ncell, feat, bfeat, source, source_measure, bc_phys, spec)
        self.feat = feat.to(device)
        self.bfeat = bfeat.to(device)
        self.barea = torch.tensor(np.asarray(a['area']), dtype=f32, device=device)
        self.bw = self.barea if rec_w is None else torch.tensor(rec_w, dtype=f32, device=device)
        self.patch_ids = torch.tensor(np.asarray(a['patch']).astype(np.int64))                       # CPU
        vol = np.asarray(a['volume'], np.float64)
        area = np.zeros(n)
        area[:ncell] = vol / vol.mean()
        self.area = torch.tensor(area, dtype=f32, device=device)
        volume = np.zeros(n)
        volume[:ncell] = vol
        self.volume = torch.tensor(volume, dtype=f32, device=device)
        edges = np.asarray(a['edges']).astype(np.int64)
        if not np.array_equal(edges[1, -nb:], np.arange(ncell, n)):
            raise ValueError(f'{d}: the last Nb edges do not point to the boundary nodes in order')
        self.edges = torch.tensor(edges, device=device)                                              # directed
        self.owner = torch.tensor(edges[0, -nb:], device=device)          # owner cell of every boundary face
        self.target = torch.tensor(np.asarray(a['target']), dtype=f32, device=device)
        self.anchor_pool = np.arange(ncell)
        self._message = None
        self._pos_dev = None
        self._keys = None
        self.dir, self.bc_input, self._srv = d, bc_input, None    # server_map (boundary.server_jump / route)

    @property
    def pos_device(self):
        """Physical node positions on the device (model.message_passing edge features), built on first use."""
        if self._pos_dev is None:
            self._pos_dev = self.centroid_cpu.to(self.edges.device)
        return self._pos_dev

    @property
    def message_edges(self):
        if self._message is None:
            self._message = torch.cat([self.edges, self.edges.flip(0)], 1)
        return self._message

    @property
    def keys(self):
        if self._keys is None:
            self._keys = index_keys(self.kind_np)
        return self._keys

    def z_order(self):
        """The cells of this case in Z-order (subset_sampling.z_order of the cell centroids: per-axis quantile ranks,
        Morton-interleaved), for context.anchor_design = "stratified". Computed on first use and kept for the whole
        process in _ZORDER (int32, about 4 MB per million cells), so a case evicted from the LRU and loaded again does
        not pay for it twice; the cells are nodes 0..ncell-1, so the order indexes the anchor pool directly."""
        z = _ZORDER.get(self.zkey)
        if z is None:
            z = _ZORDER[self.zkey] = z_order(self.centroid_cpu[:self.ncell].numpy()).astype(np.int32)
        return z

    def server_zone(self, radius, device):
        """bool [N] server zone of this case on `device` (server_zone_mask; train.zone_weight). The mask is computed
        once per process and radius (_ZONE, about 1.4 MB per case) and its device tensor kept with the case."""
        key = (self.zkey, float(radius))
        cached = getattr(self, '_zone', None)
        if cached is not None and cached[0] == key:
            return cached[1]
        z = _ZONE.get(key)
        if z is None:
            roles = np.asarray([p.get('role') or '' for p in self.patches], dtype=object)[self.patch_ids.numpy()]
            z = _ZONE[key] = server_zone_mask(self.centroid_cpu.numpy(), self.ncell, roles, radius)
        t = torch.as_tensor(z, device=device)
        self._zone = (key, t)
        return t

    def server_map(self, device):
        """The server table of this case (boundary.server_jump / server_route; server_faces), built once per case:
        n (servers), in_face / in_server / in_area (boundary-face index, server, face area of every inlet face),
        in_owner (their owner cells), out_face / out_server (every outlet face), dT [S] float64 (K; the table's
        temperature rise: source.json, or spec_bc.json under bc_input = "spec") and stems."""
        if self._srv is None:
            servers = (load_spec_files(self.dir) if self.bc_input == 'spec' else load_source_files(self.dir)[0])['servers']
            in_face, in_srv, out_face, out_srv = server_faces(servers, self.patch_ids.numpy(), len(self.patches))
            lt = lambda v: torch.as_tensor(v, dtype=torch.long, device=device)       # noqa: E731
            fi = lt(in_face)
            self._srv = {'n': len(servers), 'in_face': fi, 'in_server': lt(in_srv), 'in_owner': self.owner[fi],
                         'in_area': self.barea[fi].double(), 'out_face': lt(out_face), 'out_server': lt(out_srv),
                         'dT': torch.tensor([float(s['dT']) for s in servers], dtype=torch.float64, device=device),
                         'stems': [s['stem'] for s in servers]}
        return self._srv


class GtcData:
    """Per-case DCTS data on the device (module docstring). `names[i]` is the case of split index i."""

    def __init__(self, root, names, stats, device, provenance=None, cache_cases=None, types='kind', source='none',
                 source_measure='area', bc_input='t1'):
        self.root, self.names, self.device = Path(root), list(names), device
        self.stats = stats
        self.mean, self.std = list(stats['mean']), list(stats['std'])
        self.types = types                         # boundary.types: none, kind, or role (role one-hot appended)
        if source not in SOURCES or source_measure not in MEASURES or \
                (source == 'none' and source_measure != 'area'):
            raise ValueError(f'boundary.source {source!r} / source_measure {source_measure!r}')
        self.source, self.source_measure = source, source_measure        # boundary.source (heat columns)
        if bc_input not in ('t1', 'spec'):
            raise ValueError(f'boundary.bc_input {bc_input!r}')
        self.bc_input = bc_input                                          # boundary values: t1 or the specification
        self.source_bc_cols = source_bc_cols(types, source)               # F1 node columns (zeroed without node_bc)
        self.provenance = provenance
        self.design = None
        self.records_hook = None
        self.cache_cases = max(1, int(env(CACHE_ENV, '4') if cache_cases is None else cache_cases))
        self._lru = OrderedDict()
        self.current = None
        self._c = None

    # --- the per-case switch ---------------------------------------------------------------------------------
    def use_case(self, case):
        case = int(case)
        if case == self.current:
            return
        name = self.names[case]
        c = self._lru.pop(name, None)
        if c is None:
            c = _Case(self.root, name, self.stats, self.device, self.types, self.source, self.source_measure,
                      self.bc_input)
        self._lru[name] = c
        while len(self._lru) > self.cache_cases:
            self._lru.popitem(last=False)
        self.current, self._c = case, c

    def drop(self):
        """Forget every loaded case, the current one included (timing: the next use_case builds the case again, from
        the files, or from RAM when SCANO_DCTS_RAM_CACHE=1 keeps them)."""
        self._lru.clear()
        self.current, self._c = None, None

    def evict(self):
        """Drop every cached case except the current one (cost measurements: emulate distinct cases)."""
        for name in list(self._lru):
            if self._c is None or name != self._c.name:
                del self._lru[name]

    def _check(self, case):
        if self.current is None or int(case) != self.current:
            raise RuntimeError(f'GtcData: case {int(case)} requested while the current case is {self.current}; the '
                               'framework must call cfg_common.use_case(data, case) first (every case has its own mesh)')

    def _cur(self):
        if self._c is None:
            raise RuntimeError('GtcData: no current case (cfg_common.use_case was not called)')
        return self._c

    # --- mesh attributes of the current case -------------------------------------------------------------------
    n = property(lambda self: self._cur().n)
    n_cells = property(lambda self: self._cur().ncell)
    area = property(lambda self: self._cur().area)
    volume = property(lambda self: self._cur().volume)
    centroid_cpu = property(lambda self: self._cur().centroid_cpu)
    anchor_pool = property(lambda self: self._cur().anchor_pool)
    edge_index = property(lambda self: self._cur().message_edges)
    face_edges = property(lambda self: self._cur().edges)       # directed, one per face (owner -> neighbour / face)
    node_keys = property(lambda self: self._cur().keys)
    case_name = property(lambda self: self._cur().name)
    patch_table = property(lambda self: self._cur().patches)      # [{name, geom_type, role, n_faces, start}, ...]
    owner_cells = property(lambda self: self._cur().owner)        # [Nb] owner cell of each boundary face (device)
    face_area = property(lambda self: self._cur().barea)          # [Nb] boundary-face areas (device)
    patch_ids = property(lambda self: self._cur().patch_ids)      # [Nb] patch index of each boundary face (CPU)

    def cell_z_order(self):
        """The current case's cells in Z-order (_Case.z_order; r0s_common.morton_order reads it for stratified
        anchors, so every case uses its own mesh)."""
        return self._cur().z_order()

    def server_map(self):
        """The server table of the current case on the device (_Case.server_map; boundary.server_jump / route)."""
        return self._cur().server_map(self.device)

    def server_zone(self, radius):
        """bool [N] server zone of the current case (_Case.server_zone; train.zone_weight)."""
        return self._cur().server_zone(radius, self.device)

    # --- per-case data (the case must be the current one) ------------------------------------------------------
    def node_features(self, case, nodes):
        self._check(case)
        return self._c.feat[nodes]

    def boundary(self, case):
        """[Nb, bnd_in] boundary-face records and [Nb] record weights: the face areas (the same tensor as face_area),
        or under boundary.source_measure = "heat" the source measure on the server-outlet faces."""
        self._check(case)
        return self._c.bfeat, self._c.bw

    def record_patch_ids(self, case):
        """[Nb] patch index of every record of boundary(case) (CPU LongTensor), for boundary.records = "patch"."""
        self._check(case)
        return self._c.patch_ids

    def target(self, case, nodes):
        self._check(case)
        return self._c.target[nodes]

    face_closure = False        # sampling.faces = "adjacent" (c123; set by cfg_common.open_data)

    def subgraph(self, pivots, khop):
        c = self._cur()
        nodes = khop_nodes(pivots, khop, c.edges, c.n)
        if self.face_closure:      # plus the boundary faces owned by the drawn cells (design_sampling.with_owned_faces)
            if getattr(c, '_face_nodes', None) is None:
                c._face_nodes = torch.arange(c.ncell, c.n, device=c.owner.device)
            nodes = with_owned_faces(nodes, c._face_nodes, c.owner)
        return nodes


def _load_stats(root):
    p = Path(root) / 'stats.json'
    if not p.is_file():
        raise FileNotFoundError(f'{p}: run `python -m scano.Datasets.dcts stats --out {root}` (training cases only)')
    return json.loads(p.read_text())


def check_stats(stats, meta, smoke, meta_sha256=None):
    """Normalisation statistics must belong to this meta.json (`meta_sha256`, the sha256 of the cache's meta.json;
    None skips that check) and come from exactly its training cases, or be smoke statistics in smoke mode (and only
    there). Returns {'stats_cover_train': whether they include every training case of meta.json}."""
    for key in ('mean', 'std', 'pos_min', 'pos_scale', 'meta_sha256'):
        if key not in stats:
            raise ValueError(f'normalisation statistics lack {key!r}')
    if meta_sha256 is not None and stats['meta_sha256'] != meta_sha256:
        raise ValueError(f"these normalisation statistics were computed for another meta.json (meta_sha256 "
                         f"{stats['meta_sha256'][:12]}..., this cache's meta.json {meta_sha256[:12]}...)")
    if stats.get('smoke') and not smoke:
        raise ValueError(f'these normalisation statistics are SMOKE statistics (cases {stats.get("cases")}, whatever '
                         f'their split); they are refused outside smoke mode ({SMOKE_ENV}=1)')
    if smoke and not stats.get('smoke'):
        raise ValueError(f'smoke mode ({SMOKE_ENV}=1) needs smoke statistics (`python -m scano.Datasets.dcts stats '
                         '--smoke`); these are training statistics, so this is a real cache: unset '
                         f'{SMOKE_ENV}')
    train = list(meta['case_splits']['train_cases'])
    have = set(stats.get('cases', []))
    cover = set(train) <= have
    if not stats.get('smoke'):
        leak = [c for c in stats.get('cases', []) if c not in set(train)]
        if leak:
            raise ValueError(f'normalisation statistics include cases outside the training split: {leak[:5]}')
        if not cover:
            missing = [c for c in train if c not in have]
            raise ValueError(f'normalisation statistics cover {len(have)} of the {len(train)} training cases of '
                             f'meta.json (missing, first: {missing[:5]}); they must come from all of them')
    return {'stats_cover_train': cover}


def refuse_smoke(rec, allow, what):
    """select_cfg / eval_dcts: refuse a run trained in smoke mode (its config's data_provenance) and smoke mode
    itself unless `allow` (--allow-smoke): smoke results are development checks, never selections or results."""
    prov = rec.get('data_provenance') or {}
    if allow or not (prov.get('smoke') or smoke_mode()):
        return
    why = 'this run was trained in smoke mode' if prov.get('smoke') else f'{SMOKE_ENV}=1 is set'
    raise SystemExit(f'{what}: {why} (every split made of the cached cases, never citable); refused without '
                     '--allow-smoke')


def open_dcts(cfg, split, device, stats=None):
    """(GtcData, stats, n_cases) of `split` ('train' = training then validation cases, 'test'); see the module
    docstring for the split rules and smoke mode."""
    ds = cfg['dataset']
    root = Path(ds['data_dir'])
    meta = json.loads((root / 'meta.json').read_text())
    meta_sha = dcts._sha256_file(root / 'meta.json')
    present = set(dcts.cached_cases(root))
    smoke = smoke_mode()
    sp = meta['case_splits']
    if smoke:
        if sp['train_cases'] and all(c in present for c in sp['train_cases']):
            raise ValueError(f"{SMOKE_ENV}=1, but {root} holds all {len(sp['train_cases'])} training cases of "
                             'meta.json: smoke mode (every split made of the cached cases) is for a development cache '
                             f'with a few local cases; unset {SMOKE_ENV} to use the meta.json splits')
        names = sorted(present)
        if not names:
            raise FileNotFoundError(f'{root}: no cached case')
        repeat = max(1, int(env(SMOKE_REPEAT_ENV, '4')))
        train = (names * repeat)[:max(repeat, len(names))]
        val = (names * ds['val_count'])[:ds['val_count']]
        test = names
    else:
        train, val, test = list(sp['train_cases']), list(sp['val_cases']), list(sp['test_cases'])
        if split == 'train' and len(val) != ds['val_count']:
            raise ValueError(f'dataset.val_count = {ds["val_count"]}, but meta.json has {len(val)} validation cases')
    if split == 'train':
        names = train + val
    elif split == 'test':
        names = test
    else:
        raise ValueError(f'unknown split {split!r}')
    missing = sorted(set(names) - present)
    if missing:
        raise FileNotFoundError(f'{root}: {len(missing)} cases of the {split} split are not cached (first: '
                                f'{missing[:5]}); every case of meta.json must be converted (or {SMOKE_ENV}=1)')
    if stats is None:
        if split != 'train':
            raise ValueError('normalisation statistics come from the training cases; pass them for other splits')
        stats = _load_stats(root)
    cover = check_stats(stats, meta, smoke, meta_sha)
    prov = {'dataset': 'dcts', 'split': split, 'smoke': smoke, 'n_cases': len(names), 'distinct_cases': len(set(names)),
            'stats_cases': len(stats.get('cases', [])), 'stats_smoke': bool(stats.get('smoke')), **cover,
            'meta_sha256': meta_sha}
    opts = data_options(cfg)
    if opts['source'] != 'none' or opts['bc_input'] == 'spec':        # provenance of the derived inputs (dcts_source)
        from ..Datasets import dcts_source as gs
        for name, on in ((gs.MANIFEST, opts['source'] != 'none'), (gs.SPEC_MANIFEST, opts['bc_input'] == 'spec')):
            if on:
                p = root / name
                prov[name.replace('.json', '_sha256')] = dcts._sha256_file(p) if p.is_file() else None
        prov['source_version'], prov['spec_version'] = gs.SOURCE_VERSION, gs.SPEC_VERSION
    if smoke:
        prov['warning'] = (f'SMOKE MODE ({SMOKE_ENV}=1): every split is made of the cached cases {sorted(present)} '
                           'whatever their meta.json split; training, validation and test see the same case(s). '
                           'Development only, never citable.')
        print(f"WARNING: {prov['warning']}", file=sys.stderr, flush=True)
    n_cases = len(names)
    return GtcData(root, names, stats, device, prov, **opts), stats, n_cases


def data_options(cfg):
    """GtcData keyword arguments of a run configuration (boundary.types / source / source_measure; configurations
    stored before a key existed read its default)."""
    b = cfg['boundary']
    return {'types': b.get('types', 'kind'), 'source': b.get('source', 'none'),
            'source_measure': b.get('source_measure', 'area'), 'bc_input': b.get('bc_input', 't1')}


# --- paired-intervention training (dataset.pairs_dir, train.pair_*; docs/dcts_paired_design_2026-10-07.md) ----------
PAIR_NAME = re.compile(r'^(?P<base>.+)_(?P<knob>[A-Za-z0-9]+)$')       # <base room>_<knob>, as eval_dcts_resp


class Pairs:
    """The training-room interventions of a run (open_pairs): `data` (GtcData over the pair cache, the run's
    statistics and inputs), `by_case` {training index of the base room: [pair index, ...]}, `provenance` (written to
    config.json under data_provenance['pairs'], so a resume with another pair set is refused). `checked` holds the
    pair indices whose mesh was compared with the base room's (check_pair_mesh, once per pair and process)."""

    def __init__(self, data, by_case, provenance):
        self.data, self.by_case, self.provenance = data, by_case, provenance
        self.checked = set()


def open_pairs(cfg, data, train_cases, device):
    """Pairs of dataset.pairs_dir for a training run on `data` (the train split of the run cache). Refused: a pair cache
    with another meta.json than the run cache's (the same release, splits and statistics), a pair whose base room is
    not a TRAINING room of meta.json (a validation or test room, or unknown: the evaluation pairs live in their own
    cache and never enter training), a null diagnostic (knob null), a pair without a cached base, and an empty set.
    Pairs whose base lies beyond dataset.train_limit are kept out (smoke tests). The source inputs of the pairs
    (boundary.source != none) need `dcts build-source` on the pair cache, as on the run cache."""
    ds = cfg['dataset']
    root, main = Path(ds['pairs_dir']), Path(ds['data_dir'])
    if not (root / 'meta.json').is_file():
        raise FileNotFoundError(f'{root}/meta.json: the pair cache needs the run cache\'s meta.json (dcts build-cache '
                                '--meta)')
    meta_sha = dcts._sha256_file(root / 'meta.json')
    if meta_sha != dcts._sha256_file(main / 'meta.json'):
        raise ValueError(f'{root}/meta.json differs from {main}/meta.json: the pairs must belong to the run cache\'s '
                         'dataset release (its splits and training statistics)')
    sp = json.loads((root / 'meta.json').read_text())['case_splits']
    train = set(sp['train_cases'])
    index = {n: i for i, n in enumerate(data.names)}
    allowed = {int(i) for i in train_cases}
    names, bases, leaks = [], [], []
    for name in dcts.cached_cases(root):
        m = PAIR_NAME.match(name)
        if not m or m['base'] not in index:
            leaks.append(f'{name} (no base room in the run cache)')
            continue
        if m['base'] not in train:                  # validation / test room: never trained on
            leaks.append(f'{name} (base {m["base"]} is not a training room)')
            continue
        if m['knob'] == 'null':                     # a null diagnostic (factor 1) is never a training pair
            leaks.append(f'{name} (a null diagnostic)')
            continue
        if index[m['base']] in allowed:
            names.append(name)
            bases.append(index[m['base']])
    if leaks:
        raise ValueError(f'{root}: {len(leaks)} pairs refused: ' + '; '.join(leaks[:5]))
    if not names:
        raise ValueError(f'{root}: no pair of a training room of this run')
    by_case = {}
    for k, b in enumerate(bases):
        by_case.setdefault(b, []).append(k)
    knobs = {}
    for name in names:
        knob = PAIR_NAME.match(name)['knob']
        knobs[knob] = knobs.get(knob, 0) + 1
    prov = {'pairs_dir': str(root), 'meta_sha256': meta_sha, 'n_pairs': len(names), 'rooms': len(by_case),
            'knobs': knobs, 'names_sha256': hashlib.sha256('\n'.join(names).encode()).hexdigest()}
    opts = data_options(cfg)
    if opts['source'] != 'none':
        from ..Datasets import dcts_source as gs
        p = root / gs.MANIFEST
        prov[gs.MANIFEST.replace('.json', '_sha256')] = dcts._sha256_file(p) if p.is_file() else None
    pdata = GtcData(root, names, data.stats, device, {'dataset': 'dcts', 'split': 'train_pairs'}, cache_cases=1,
                    **opts)                      # a pair is used once per epoch: no device cache beyond the current one
    return Pairs(pdata, by_case, prov)


def check_pair_mesh(data, pdata):
    """The current case of `pdata` (a pair) must be the current case of `data` (its base room) node for node: node
    positions, kinds, cell volumes, the directed graph and the patch table."""
    a, b = data._cur(), pdata._cur()
    same = (a.n == b.n and a.ncell == b.ncell and torch.equal(a.centroid_cpu, b.centroid_cpu)
            and np.array_equal(a.kind_np, b.kind_np) and torch.equal(a.volume, b.volume)
            and torch.equal(a.edges, b.edges) and [p['name'] for p in a.patches] == [p['name'] for p in b.patches])
    if not same:
        raise ValueError(f'pair {b.name} is not on the mesh of its base room {a.name}')


def pair_term(model, data, pairs, cfg, case, k, nodes, anchors, records, pred, target, rng=None):
    """The pair term of one training step (train.pair_loss) for pair k of the base room `case` (the current case of
    `data`), on the room's own draw: the same `nodes`, decoupled `anchors` and `records`, so the difference of the two
    predictions comes from the boundary inputs alone. pred / target: the room's prediction and target on `nodes`.
      diff    rel_l2 of (f_pair - f_room) against (y_pair - y_room) per field of train.pair_fields (cfg_common.rel_l2_loss
              about zero, in units of the training std, with the training loss's node weights), mean over the fields;
      sample  the pair's own training loss (cfg_common.train_loss), the pair as an extra sample of the step."""
    from .cfg_common import loss_weights, predict, rel_l2_loss, setting, train_loss, use_case
    pdata = pairs.data
    use_case(pdata, k)
    if k not in pairs.checked:
        check_pair_mesh(data, pdata)
        pairs.checked.add(k)
    pred_p = predict(model, pdata, cfg, k, nodes, anchors, records=records, rng=rng)
    target_p = pdata.target(k, nodes)
    if setting(cfg, 'train', 'pair_loss') == 'sample':
        return train_loss(pdata, cfg, pred_p, target_p, nodes)
    cols = [dcts.FIELDS.index('T')] if setting(cfg, 'train', 'pair_fields') == 'T' else list(range(len(dcts.FIELDS)))
    std = [float(data.std[c]) for c in cols]
    scale = SimpleNamespace(mean=0.0, std=std)          # the response about zero, in units of each field's std
    return rel_l2_loss(scale, (pred_p - pred)[:, cols], (target_p - target)[:, cols],
                       loss_weights(data, cfg, nodes))
