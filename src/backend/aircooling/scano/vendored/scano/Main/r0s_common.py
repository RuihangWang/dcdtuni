"""R0-S data on the device for the config-driven framework (dataset.name = "r0s"; design section 3).

R0SData has the interface of olbc_common.OlbcData, so cfg_common, train_cfg and select_cfg run every backbone on
it unchanged:
  nodes      the learning-mesh cells and boundary faces (node order of mesh_learn.npz, renumbered);
  node_in 10 x, y, z, d_w, is_face, one-hot type (D, N, R), value, alpha (BC fields zero on cells);
  bnd_in 11  per record (= boundary face node, one to one): x, y, z, nx, ny, nz, one-hot type, value, alpha;
  bnd_w      face areas (the config value 'arc' means 'record quadrature weight');
  area       cell volume / mean cell volume, 0 on boundary faces (they never enter the global state under the
             area measure); the count measure gives every passed node weight 1;
  subgraph   k-hop on the SAMPLING graph (cell-cell only: a boundary face is in a k-hop sample only as a pivot);
  edge_index the MESSAGE graph (cell-cell and face <-> owner cell) for graph backbones' induced subgraphs;
  anchor_pool the cells (decoupled anchors are drawn from cells only);
  node_keys  physical identities for the uniform-subset sampler.
Normalisation (training cases only): u by mean / std; record values by type (D g / std, N signed log1p(q) / std,
R u_inf / std, over patch records); alpha by the std of R alphas. Insulated walls are N records with value 0.

Also here (dev/r0s-eval): the A2/A3 design pieces (inclusion probabilities of the training sampler for
context.measure = "area_ht" and train.loss_weight = "v_over_pi" / "inv_pi", volume-PPS anchors, the uniform record
draw of boundary.record_sample), the patch strata of the training draws (R0SData.node_groups, sampling.stratify), and
`open_mesh`, the test split on any mesh of the family (D4: 16^3, 40^3 reverse-graded, 64^3), with the truth
restricted exactly from the stored 64^3 truth (restrict_between).

A1 (visibility, c60; scano.Datasets.design_sampling): `design_nodes` draws the A1 designs, the native k-hop or uniform
family with stratified boundary faces (sampling.faces = "stratified": every patch and the insulated part of every wall
is a stratum, n_h = max(1, round(rho m_h)) of its faces per draw; the cells are those of the native draw) and
partition + halo (sampling.regime = "partition": K = round(1 / rho) blocks of cells by recursive coordinate bisection,
faces with their owner cell, one block per draw plus a k-hop halo of cells and the faces they own; only the block is
supervised and scored); `design_pi` gives the inclusion probabilities of the Horvitz-Thompson loss
(train.loss_weight = "ht").
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from ..Datasets import r0s
from ..Datasets.design_sampling import (SALT_STRATA, block_of_draw, blocks_for, khop_pi, rcb_blocks, stratified_take,
                                        stratum_pi)
from ..Datasets.refine_sampling import khop_nodes, sample_budget
from ..Datasets.subset_sampling import NodeKeys, uniform_nodes, z_order

SALT_RECORDS = 0x2EC02D5A          # uniform record draw of boundary.record_sample (independent of the node draw)


def record_arrays(mesh, tab, layout, coef):
    """Native records of cases (layout [S], coef [S, Pmax, 3]): type [S, NB] (walls = N), raw value [S, NB],
    alpha [S, NB], patch id [S, NB] (-1 = insulated wall). Record order = boundary-face order of the mesh file."""
    layout = np.asarray(layout)
    lat = mesh['lattice_of']
    pid = tab['lattice_map'][layout][:, mesh['face_wall'], lat[mesh['face_p']], lat[mesh['face_q']]].astype(np.int64)
    on = pid >= 0
    safe = np.where(on, pid, 0)
    L = layout[:, None]
    rows = np.arange(len(layout))[:, None]
    dt1 = mesh['face_t'][None, :, 0] - tab['c1'][L, safe]
    dt2 = mesh['face_t'][None, :, 1] - tab['c2'][L, safe]
    val = coef[rows, safe, 0] + coef[rows, safe, 1] * dt1 + coef[rows, safe, 2] * dt2
    rtype = np.where(on, tab['ptype'][L, safe], r0s.N_TYPE)
    ralpha = np.where(on, tab['alpha'][L, safe], 0.0)
    return rtype, np.where(on, val, 0.0), ralpha, pid


def normalised_values(rtype, rval, stats):
    return np.where(rtype == r0s.D_TYPE, rval / stats['d_std'],
                    np.where(rtype == r0s.N_TYPE, r0s.slog1p(rval) / stats['n_std'], rval / stats['r_std']))


def r0s_train_stats(mesh, tab, arrays, n_train):
    """Normalisation statistics from the first n_train cases of train.npz (plain floats, JSON-serialisable)."""
    u = arrays['u'][:n_train]
    rtype, rval, ralpha, pid = record_arrays(mesh, tab, arrays['layout'][:n_train], arrays['coef'][:n_train])
    on = pid >= 0

    def std(x):
        s = float(np.std(x)) if x.size else 0.0
        return s if s > 0 else 1.0

    return {'mean': float(u.mean(dtype=np.float64)), 'std': float(u.std(dtype=np.float64)),
            'd_std': std(rval[on & (rtype == r0s.D_TYPE)]), 'n_std': std(r0s.slog1p(rval[on & (rtype == r0s.N_TYPE)])),
            'r_std': std(rval[on & (rtype == r0s.R_TYPE)]), 'a_std': std(ralpha[on & (rtype == r0s.R_TYPE)])}


class R0SData:
    """Learning-mesh geometry plus per-case records and targets, on the device (interface of OlbcData)."""

    def __init__(self, mesh, tab, arrays, stats, device):
        self.device = device
        c = mesh['centroid']
        kind = mesh['kind']
        self.n = int(mesh['n_nodes'])
        self.centroid_cpu = torch.tensor(c, dtype=torch.float32)             # what refine's ROI box draw reads
        self.cell_mask_cpu = torch.tensor(kind == 0)                         # c121: compact regions count cells
        self.node_keys = NodeKeys(kind, c)                                   # float64 centroids of the mesh file
        self.kind = torch.tensor(kind.astype(np.int64), device=device)
        self.static = torch.tensor(np.concatenate([c, mesh['d_w'][:, None]], 1), dtype=torch.float32, device=device)
        cell = kind == 0
        vol = mesh['volume']
        self.area = torch.tensor(np.where(cell, vol / vol[cell].mean(), 0.0), dtype=torch.float32, device=device)
        self.edge_index = torch.tensor(mesh['message_edge_index'], dtype=torch.long, device=device)
        self.sample_edge_index = torch.tensor(mesh['sample_edge_index'], dtype=torch.long, device=device)
        self.anchor_pool = np.nonzero(cell)[0]
        fn = mesh['face_node']
        self.face_node = torch.tensor(fn, dtype=torch.long, device=device)
        self.face_owner = torch.tensor(mesh['owner_node'], dtype=torch.long, device=device)   # [NB] owner cell node
        slot = np.full(self.n, -1, np.int64)
        slot[fn] = np.arange(len(fn))
        self.slot = torch.tensor(slot, device=device)
        self.rec_geo = torch.tensor(np.concatenate([mesh['face_centroid'], mesh['face_normal']], 1),
                                    dtype=torch.float32, device=device)
        self.rec_w = torch.tensor(mesh['face_area'], dtype=torch.float32, device=device)
        rtype, rval, ralpha, rpid = record_arrays(mesh, tab, arrays['layout'], arrays['coef'])
        self.rpid = rpid.astype(np.int16)                                    # [S, NB] patch id per record (-1 wall)
        self.face_wall = np.asarray(mesh['face_wall'])                      # [NB] wall of each record
        self.record_keys = NodeKeys(np.ones(len(fn), np.int64), mesh['face_centroid'])
        fw = np.zeros(self.n)
        fw[fn] = mesh['face_area']
        self.face_w = torch.tensor(fw, dtype=torch.float32, device=device)  # face area per node, 0 on cells
        self.volume = torch.tensor(vol, dtype=torch.float32, device=device) # physical cell volume, 0 on faces
        self.rtype = torch.tensor(rtype.astype(np.int8), device=device)
        self.rval = torch.tensor(normalised_values(rtype, rval, stats), dtype=torch.float32, device=device)
        self.ralpha = torch.tensor(ralpha / stats['a_std'], dtype=torch.float32, device=device)
        self.u = torch.tensor(arrays['u'], dtype=torch.float32, device=device)
        self.mean, self.std = stats['mean'], stats['std']
        self.stats = stats
        self.role = str(mesh['role'])
        self.records_hook = None          # K1: callable(case) -> (bnd_feat [M, 11], bnd_w [M]) replacing the records
        self.design = None                # attach_design: the inclusion-probability file area_ht / v_over_pi read
        self.oracle_code = None           # attach_oracle_code: [S, d] per-case oracle codes (boundary.oracle_code)
        self.oracle_w = None

    def bnd_record(self, case):
        """[NB, 5] = one-hot type (D, N, R), value, alpha."""
        onehot = F.one_hot(self.rtype[case].long(), 3).float()
        return torch.cat([onehot, self.rval[case][:, None], self.ralpha[case][:, None]], 1)

    def node_features(self, case, nodes):
        """[P, 10] = x, y, z, d_w, is_face, one-hot type, value, alpha (BC fields zero on cells)."""
        s = self.slot[nodes]
        isb = s >= 0
        dyn = torch.zeros(nodes.numel(), 6, device=self.device)
        dyn[isb, 0] = 1.0
        dyn[isb, 1:] = self.bnd_record(case)[s[isb]]
        return torch.cat([self.static[nodes], dyn], 1)

    def boundary(self, case):
        """[NB, 11] = x, y, z, nx, ny, nz, one-hot type, value, alpha; and [NB] face areas. With an oracle code
        attached (boundary.oracle_code, attach_oracle_code): one record [1, d] holding the case's code, weight 1."""
        if self.records_hook is not None:
            return self.records_hook(case)
        if self.oracle_code is not None:
            return self.oracle_code[case][None], self.oracle_w
        return torch.cat([self.rec_geo, self.bnd_record(case)], 1), self.rec_w

    def record_patch_ids(self, case):
        """[NB] patch id of every record of `boundary(case)`, for boundary.records = "patch": the patch index for
        faces on a patch, and -(1 + wall) for insulated faces, so the insulated part of each of the six walls becomes
        one record of its own (never merged across walls or into a patch). CPU LongTensor."""
        pid = torch.as_tensor(self.rpid[case].astype(np.int64))
        wall = torch.as_tensor(self.face_wall.astype(np.int64))
        return torch.where(pid >= 0, pid, -(1 + wall))

    def node_groups(self, case):
        """[n] int64 numpy: the patch id of every boundary-face node on a patch in `case`, -1 for cells and insulated
        faces (the strata of sampling.stratify = "patch", sampling_tricks.add_missed_groups)."""
        g = np.full(self.n, -1, np.int64)
        g[self.face_node.cpu().numpy()] = self.rpid[case]
        return g

    def target(self, case, nodes):
        return self.u[case, nodes]

    face_closure = False        # sampling.faces = "adjacent" (c123; set by cfg_common.open_data)

    def subgraph(self, pivots, khop):
        nodes = khop_nodes(pivots, khop, self.sample_edge_index, self.n)
        if self.face_closure:      # plus the boundary faces owned by the drawn cells (design_sampling.with_owned_faces)
            from ..Datasets.design_sampling import with_owned_faces
            nodes = with_owned_faces(nodes, self.face_node, self.face_owner)
        return nodes


def open_r0s(data_dir, split, device, val_count, stats=None):
    """(R0SData, stats, n_cases) of a split; stats are computed from the training cases of train.npz when None."""
    mesh, tab, arrays = r0s.load(data_dir, split)
    n_cases = int(arrays['u'].shape[0])
    if stats is None:
        if split != 'train':
            raise ValueError('normalisation statistics come from the train split; pass them for other splits')
        stats = r0s_train_stats(mesh, tab, arrays, n_cases - val_count)
    return R0SData(mesh, tab, arrays, stats, device), stats, n_cases


# ---------------------------------------------------------------------------------------------------------
# oracle code (boundary.oracle_code; R29 E4 decoding test)
# ---------------------------------------------------------------------------------------------------------

_POD_CACHE = {}


def pooled_pod_basis(data_dir, n_train, d):
    """Pooled POD of the training fields on the learning cells: (cells [n_c] node indices, sqrt volume [n_c],
    training mean [n_c], modes V [n_c, d] (orthonormal in the volume-weighted inner product after the sqrt(V)
    weighting), per-mode standard deviation over the training cases [d]). The training cases are the first n_train
    rows of train.npz. float64; sign of each mode fixed so that its largest-magnitude entry is positive (the code
    does not depend on the eigensolver's sign choice)."""
    key = (str(Path(data_dir).resolve()), int(n_train), int(d))
    if key in _POD_CACHE:
        return _POD_CACHE[key]
    mesh, _, arrays = r0s.load(data_dir, 'train')
    cells = np.nonzero(np.asarray(mesh['kind']) == 0)[0]
    sw = np.sqrt(np.asarray(mesh['volume'], np.float64)[cells])
    U = np.asarray(arrays['u'][:n_train][:, cells], np.float64)
    mean = U.mean(0)
    X = (U - mean) * sw
    ev, Q = np.linalg.eigh(X @ X.T)
    order = np.argsort(ev)[::-1][:d]
    s = np.sqrt(np.maximum(ev[order], 0.0))
    if s[-1] <= s[0] * 1e-12:
        raise ValueError(f'oracle_code {d}: the training fields have fewer than {d} independent modes')
    V = (X.T @ Q[:, order]) / s[None, :]
    big = np.argmax(np.abs(V), 0)
    V *= np.sign(V[big, np.arange(V.shape[1])])[None, :]
    out = (cells, sw, mean, V, s / np.sqrt(n_train))
    _POD_CACHE[key] = out
    return out


def oracle_codes(data_dir, n_train, d, u):
    """[S, d] standardised oracle codes of fields u [S, n_nodes] (node order of the learning mesh): the coefficients
    of the top-d pooled POD modes of the centred, volume-weighted cell values, divided by their training std."""
    cells, sw, mean, V, std = pooled_pod_basis(data_dir, n_train, d)
    X = (np.asarray(u[:, cells], np.float64) - mean) * sw
    return (X @ V) / std[None, :]


def attach_oracle_code(data, cfg):
    """boundary.oracle_code = d > 0: per-case oracle codes of every case of `data` (from its own true fields, with the
    basis of the run's training cases: train.npz minus dataset.val_count) on data.oracle_code [S, d]; the record
    route then reads one record per case (R0SData.boundary). Does nothing for d = 0."""
    d = int(cfg['boundary'].get('oracle_code', 0))
    if not d:
        return
    ds = cfg['dataset']
    man = json.loads((Path(ds['data_dir']) / 'manifest.json').read_text())
    n_train = int(man['n_train']) + int(man['n_val']) - int(ds['val_count'])       # rows of train.npz - validation
    code = oracle_codes(ds['data_dir'], n_train, d, data.u.cpu().numpy())
    data.oracle_code = torch.tensor(code, dtype=torch.float32, device=data.device)
    data.oracle_w = torch.ones(1, dtype=torch.float32, device=data.device)


# ---------------------------------------------------------------------------------------------------------
# A2 / A3 design pieces
# ---------------------------------------------------------------------------------------------------------

def sampler_name(cfg):
    """Name of the training sampler's inclusion-probability file (tools/r0s_checks.py CONFIGS)."""
    s = cfg['sampling']
    if s['regime'] == 'scatter':
        return f"khop_{s['sample_ratio']:g}_{s['khop']}"
    if s['regime'] == 'uniform':
        return f"uniform_{s['sample_ratio']:g}"
    if s['regime'] == 'roi':
        if s['p_scatter'] != 0.5:           # r0s_checks computes the roi mixture at p_scatter 0.5 only
            raise ValueError(f"inclusion probabilities exist for roi p_scatter 0.5 only, not {s['p_scatter']}")
        return f"roi_{s['sample_ratio']:g}_{s['khop']}"
    raise ValueError(s['regime'])


def needs_design(cfg):
    from .cfg_common import setting
    # c43's inclusion files: area_ht, v_over_pi and inv_pi (A8, c61); ht (A1, c60) has its own design_pi
    return cfg['context']['measure'] == 'area_ht' or setting(cfg, 'train', 'loss_weight') in ('v_over_pi', 'inv_pi')


def inclusion_pi(data_dir, cfg, n):
    """(pi [n] float64, record) of the training sampler: exactly budget / N for a uniform subset and 1 for
    sample_ratio 1; otherwise the Monte-Carlo file of c43 (<data_dir>/inclusion/<name>.npz), floored at half a hit
    (1 / (2 draws)) so a node the Monte Carlo never drew cannot get an infinite weight."""
    s = cfg['sampling']
    if s['sample_ratio'] >= 1:
        return np.ones(n), {'source': 'sample_ratio 1: pi = 1'}
    if s['regime'] == 'uniform':
        return np.full(n, sample_budget(n, s['sample_ratio']) / n), {'source': 'uniform subset: pi = budget / N'}
    name = sampler_name(cfg)
    f = Path(data_dir) / 'inclusion' / f'{name}.npz'
    if not f.is_file():
        raise FileNotFoundError(f'{f} (inclusion probabilities of the training sampler; tools/r0s_checks.py, c43)')
    z = np.load(f)
    pi = np.asarray(z['pi'], np.float64)
    if pi.shape != (n,):
        raise ValueError(f'{f}: {pi.shape} probabilities for {n} nodes')
    draws = int(len(z['cases']) * len(z['epochs']))
    floor = 0.5 / draws
    rec = {'source': f'inclusion/{name}.npz', 'sha256': hashlib.sha256(f.read_bytes()).hexdigest(), 'draws': draws,
           'floored_nodes': int((pi < floor).sum())}
    return np.maximum(pi, floor), rec


def attach_design(data, cfg):
    """Attach the training design's inclusion probabilities (data.pi_train, data.ht_pi) when area_ht or
    v_over_pi / inv_pi need them; data.ht_pi is what context.measure = "area_ht" divides by (evaluation swaps it for the
    deployment design)."""
    if cfg['dataset']['name'] != 'r0s' or not needs_design(cfg):
        return
    pi, rec = inclusion_pi(cfg['dataset']['data_dir'], cfg, data.n)
    data.pi_train = torch.tensor(pi, dtype=torch.float32, device=data.device)
    data.ht_pi = data.pi_train
    data.design = rec


def draw_anchors_pps(data, n_anchors, rng):
    """Volume-PPS anchors: n_anchors cells drawn with replacement, probability proportional to the cell volume."""
    pool = data.anchor_pool
    p = getattr(data, '_pps_p', None)
    if p is None:
        v = data.volume[torch.as_tensor(pool, device=data.device)].double().cpu().numpy()
        p = data._pps_p = v / v.sum()
    return torch.as_tensor(rng.choice(pool, n_anchors, replace=True, p=p), device=data.device)


def anchor_pool(data):
    """The nodes decoupled anchors are drawn from: data.anchor_pool (R0-S and DCTS: the cells), or every node when the
    data object has none (olbc, whose nodes are the cells of its 2-D mesh; olbc_common.draw_anchors)."""
    pool = getattr(data, 'anchor_pool', None)
    return np.arange(data.n) if pool is None else np.asarray(pool)


def anchor_volume(data):
    """[n] device tensor of the measure the volume-stratified design lays out: data.volume (R0-S and DCTS: the physical
    cell volume) or, on a data object without one, data.area (olbc: the lumped node area, the measure of its mesh
    cache and of the 'area' context). Only ratios matter for the draw (the strata cut the total into equal parts)."""
    vol = getattr(data, 'volume', None)
    return data.area if vol is None else vol


def morton_order(data):
    """The anchor pool (cells; olbc every node) of the current mesh in Z-order (subset_sampling.z_order of the cell
    centroids). Data objects with per-case meshes (GtcData) provide it per case through `cell_z_order()` (cached per
    case in dcts_common); otherwise it is computed once and cached on the data object. On the structured R0-S learning
    mesh an aligned run of 8 cells of the order is one 2 x 2 x 2 block; olbc's centroids are 2-D (z = 0), so the order
    is the 2-D Morton order of its nodes."""
    per_case = getattr(data, 'cell_z_order', None)
    if per_case is not None:
        return per_case()
    order = getattr(data, '_morton_order', None)
    if order is None:
        pool = anchor_pool(data)
        order = data._morton_order = pool[z_order(data.centroid_cpu.numpy()[pool])]
    return order


def strata(n_pool, n_anchors):
    """(start, size) of n_anchors consecutive strata of the Z-ordered pool, sizes q or q + 1 (np.array_split)."""
    if not 0 < n_anchors <= n_pool:
        raise ValueError(f'{n_anchors} stratified anchors from a pool of {n_pool}')
    q, r = divmod(n_pool, n_anchors)
    size = np.full(n_anchors, q, np.int64)
    size[:r] += 1
    return np.concatenate([[0], np.cumsum(size)[:-1]]), size


def draw_anchors_stratified(data, n_anchors, rng):
    """Spatially stratified anchors: one cell drawn uniformly from each of n_anchors consecutive strata of the
    Z-ordered cells (on R0-S with 4096 anchors one cell per 2 x 2 x 2 block). The inclusion probability of a cell is
    1 / (size of its stratum), so the anchors stay independent of the sample and of each other's strata."""
    order = morton_order(data)
    start, size = strata(len(order), n_anchors)
    pick = start + np.floor(rng.random(n_anchors) * size).astype(np.int64)
    return torch.as_tensor(order[pick].astype(np.int64), device=data.device)


def stratified_factor(data, anchors, n_anchors):
    """1 / pi of stratified anchors relative to the mean, = |stratum| / mean |stratum| (1 for every anchor when
    n_anchors divides the pool): the Horvitz-Thompson factor that multiplies the measure in the Hajek slice means.
    Positions in the order are recomputed per call (a few ms on a million cells), so nothing is cached across the
    per-case meshes of GtcData."""
    order = morton_order(data)
    pos = np.full(data.n, -1, np.int64)
    pos[order] = np.arange(len(order))
    if (pos[anchors.cpu().numpy()] < 0).any():
        raise ValueError('stratified anchors must be cells of the current mesh')
    start, size = strata(len(order), n_anchors)
    k = np.searchsorted(start, pos[anchors.cpu().numpy()], side='right') - 1
    return torch.as_tensor(size[k] / size.mean(), dtype=torch.float32, device=data.device)


def _mesh_owner(data):
    """The object that owns the current mesh: the current case of a data object with per-case meshes (GtcData._cur),
    else the data object itself. Per-mesh caches live on it, so a case switch never reads another mesh's cache."""
    cur = getattr(data, '_cur', None)
    return cur() if callable(cur) else data


def _volume_line(data):
    """(cells in Z-order, cumulative cell volume along that order; olbc: node area, anchor_volume) as device tensors of
    the current mesh, cached on the mesh owner (float64 cumulative sum)."""
    owner = _mesh_owner(data)
    hit = getattr(owner, '_volume_line_cache', None)
    if hit is None:
        order = torch.as_tensor(np.asarray(morton_order(data), np.int64), device=data.device)
        cum = torch.cumsum(anchor_volume(data)[order].double(), 0)
        hit = owner._volume_line_cache = (order, cum)
    return hit


def draw_anchors_stratified_volume(data, n_anchors, rng):
    """Volume-stratified anchors along the Z-order (probability proportional to volume within equal-volume strata):
    the cells in Z-order are laid end to end on [0, V) by their volumes, [0, V) is cut into n_anchors strata of equal
    volume V / n_anchors, one point is drawn uniformly in each and the cell that contains it is the anchor. A cell is
    drawn V_i / (V / n_anchors) times in expectation (a cell larger than a stratum can be drawn more than once), so
    every draw stands for the same volume and the anchors carry equal weights (Hansen-Hurwitz, as volume-PPS, but
    spread over space like the stratified design). On DCTS, whose cell volumes vary by orders of magnitude,
    this removes the volume part of the Monte Carlo error that uniform-by-count anchors carry (c67 DCTS check)."""
    if n_anchors < 1:
        raise ValueError(f'{n_anchors} volume-stratified anchors')
    order, cum = _volume_line(data)
    u = (torch.arange(n_anchors, dtype=torch.float64) + torch.as_tensor(rng.random(n_anchors))) * \
        (float(cum[-1]) / n_anchors)
    idx = torch.searchsorted(cum, u.to(cum.device), right=True).clamp_max(order.numel() - 1)
    return order[idx]


def record_draw(data, case, epoch, rate, salt=SALT_RECORDS):
    """The records one draw reads: a uniform subset of max(1, int(NB * rate)) records keyed by the physical record
    identity, the case and the epoch (independent of the seed and of the node draw); sorted indices on the device."""
    return uniform_nodes(data.record_keys, case, epoch, rate, salt).to(data.device)


# ---------------------------------------------------------------------------------------------------------
# A1 design pieces (c60): stratified boundary faces, partition + halo, Horvitz-Thompson inclusion probabilities
# ---------------------------------------------------------------------------------------------------------

def _cache(data):
    return data.__dict__.setdefault('_a1_cache', {})


def face_strata(data, case):
    """[NB] stratum of every boundary face (record order): its patch id, or -(1 + wall) for the insulated part of a
    wall (record_patch_ids), so each patch and the insulated part of each of the six walls is one stratum."""
    return data.record_patch_ids(case).numpy()


def stratified_faces(data, case, epoch, rho, salt=SALT_STRATA):
    """(face node ids, sorted LongTensor on the device; their inclusion probabilities n_h / m_h, float64 array) of
    the stratified face draw of (case, epoch): in every stratum the n_h faces with the smallest draw keys (keys of
    the physical identity, the case, the epoch and `salt`; ties by identity)."""
    fn = data.face_node.cpu().numpy()
    keys = data.node_keys.draw(case, epoch, salt)[fn]
    pos, pi = stratified_take(face_strata(data, case), keys, rho, data.node_keys.ident[fn])
    nodes = fn[pos]
    order = np.argsort(nodes)
    return torch.as_tensor(nodes[order], dtype=torch.long, device=data.device), pi[order]


def native_pi(data, regime, rho, khop):
    """[N] float64 inclusion probability of every node under the native design: uniform subset b / N; k-hop (scatter)
    1 - P(no node of the closed k-hop ball of the sampling graph is among b simple-random pivots) (b / N for the
    boundary faces, which are isolated in the sampling graph). Cached per (regime, rho, khop)."""
    key = ('pi', regime, float(rho), int(khop))
    cache = _cache(data)
    if key not in cache:
        b = sample_budget(data.n, rho)
        if regime == 'uniform':
            cache[key] = np.full(data.n, b / data.n)
        elif regime == 'scatter':
            cache[key] = khop_pi(data.sample_edge_index.cpu().numpy(), data.n, khop, b)
        else:
            raise ValueError(f'no analytic inclusion probabilities for sampling.regime = {regime!r}')
    return cache[key]


def design_pi(data, sampling, case):
    """[N] float32 tensor on the device: inclusion probability of every node in the training design of `case`
    (sampling = the resolved [sampling] table): native_pi, with the faces' n_h / m_h under sampling.faces =
    "stratified" (their strata belong to the case's layout)."""
    pi = native_pi(data, sampling['regime'], sampling['sample_ratio'], sampling['khop'])
    if sampling.get('faces', 'native') == 'stratified':
        pi = pi.copy()
        pi[data.face_node.cpu().numpy()] = stratum_pi(face_strata(data, case), sampling['sample_ratio'])
    return torch.tensor(pi, dtype=torch.float32, device=data.device)


def partition_blocks(data, k):
    """[N] int64 block of every node for K = k blocks: the cells by recursive coordinate bisection on their quantised
    centroids (design_sampling.rcb_blocks; independent of the numbering), every boundary face with its owner cell."""
    key = ('blocks', int(k))
    cache = _cache(data)
    if key not in cache:
        kind = data.kind.cpu().numpy()
        cells = np.nonzero(kind == 0)[0]
        block = np.full(data.n, -1, np.int64)
        block[cells] = rcb_blocks(data.node_keys.ident[cells, 1:], k)
        block[data.face_node.cpu().numpy()] = block[data.face_owner.cpu().numpy()]
        if (block < 0).any():
            raise ValueError('partition: a node has no block (a boundary face without an owner cell?)')
        cache[key] = block
    return cache[key]


def partition_draw(data, case, epoch, rho, khop):
    """(nodes, scored) of a partition + halo draw: block design_sampling.block_of_draw(case, epoch, K), K =
    blocks_for(rho); nodes = the block's cells, the cells within `khop` hops of them in the sampling graph (the halo)
    and every boundary face owned by one of those cells (sorted LongTensor on the device); scored = bool mask over
    nodes, true on the block (the supervised and scored nodes)."""
    k = blocks_for(rho)
    block = partition_blocks(data, k)
    b = block_of_draw(case, epoch, k)
    mine = torch.as_tensor(block == b, device=data.device)
    cells = torch.nonzero(mine & (data.kind == 0)).flatten()
    ball = data.subgraph(cells, khop) if khop > 0 else cells
    faces = data.face_node[torch.isin(data.face_owner, ball)]
    nodes = torch.unique(torch.cat([ball, faces]))
    return nodes, mine[nodes]


def design_nodes(data, sampling, case, epoch, rho=None):
    """(nodes, scored) of one draw of an A1 design (sampling = a resolved [sampling] table, rho overrides its
    sample_ratio, as the D1 deployments do): partition + halo (scored = the block), or the native k-hop / uniform
    family with stratified faces (the cells of the native draw, the faces of stratified_faces; scored None = all).
    The native family without stratified faces is cfg_common.sample_nodes' own path and not drawn here."""
    from .olbc_common import train_nodes
    rho = sampling['sample_ratio'] if rho is None else rho
    regime = sampling['regime']
    if regime == 'partition':
        return partition_draw(data, case, epoch, rho, sampling['khop'])
    if sampling.get('faces', 'native') != 'stratified':
        raise ValueError('design_nodes draws partition + halo or stratified faces only')
    if regime == 'uniform':
        base = uniform_nodes(data.node_keys, case, epoch, rho).to(data.device)
    elif regime == 'scatter':
        base = train_nodes(data, case, epoch, rho, sampling['khop'], None)
    else:
        raise ValueError(f'stratified faces are implemented for regimes scatter and uniform, not {regime!r}')
    cells = base[data.kind[base] == 0]
    faces, _ = stratified_faces(data, case, epoch, rho)
    return torch.sort(torch.cat([cells, faces])).values, None


# ---------------------------------------------------------------------------------------------------------
# other meshes of the family (D4) and exact restriction between them
# ---------------------------------------------------------------------------------------------------------

def restrict_between(cfg, fine, coarse, u_lex):
    """Exact finite-volume restriction of lex fields [S, N_fine] on mesh `fine` to mesh `coarse` (lex), whose lines
    are a subset of fine's: cells by volume averages, boundary faces by area averages. With fine = the truth mesh
    this is r0s.restrict_lex."""
    fi, ci = np.asarray(fine.line_index), np.asarray(coarse.line_index)
    if not np.isin(ci, fi).all():
        raise ValueError('the coarse mesh lines are not a subset of the fine mesh lines')
    u = np.atleast_2d(u_lex)
    S, nf = u.shape[0], fine.n
    W = np.zeros((coarse.n, nf))
    pos = np.searchsorted(fi, ci)
    for m in range(coarse.n):
        W[m, pos[m]:pos[m + 1]] = fine.width[pos[m]:pos[m + 1]]
    cells = u[:, :fine.n_cells].reshape(S, nf, nf, nf)
    cc = np.einsum('Kk,Jj,Ii,skji->sKJI', W, W, W, cells, optimize=True)
    cc /= (coarse.width[:, None, None] * coarse.width[None, :, None] * coarse.width[None, None, :])
    faces = u[:, fine.n_cells:].reshape(S, 6, nf, nf)
    ff = np.einsum('Qq,Pp,sfqp->sfQP', W, W, faces, optimize=True)
    ff /= (coarse.width[:, None] * coarse.width[None, :])
    return np.concatenate([cc.reshape(S, -1), ff.reshape(S, -1)], 1)


def common_mesh(cfg, a, b):
    """The coarsest mesh both BoxMeshes restrict to: the intersection of their lines (it holds the lattice lines)."""
    return r0s.BoxMesh(cfg, 'common', np.intersect1d(a.line_index, b.line_index))


def box_mesh(data_dir, role):
    """BoxMesh of a stored mesh role, checked against the file (same lines and renumbering)."""
    cfg = r0s.config_of(data_dir)
    m = r0s.BoxMesh(cfg, role)
    with np.load(Path(data_dir) / f'mesh_{role}.npz') as z:
        if not (np.array_equal(z['perm'], m.perm) and np.array_equal(z['line_index'], m.line_index)):
            raise ValueError(f'mesh_{role}.npz does not match BoxMesh({role})')
    return cfg, m


def open_mesh(data_dir, role, device, stats, chunk=32):
    """The test split on mesh `role` (learn, coarse, reverse, truth) as an R0SData: records from the boundary
    specification on that mesh's faces, targets = the stored learning-mesh fields (learn), the stored 64^3 truth
    (truth), or its exact restriction (coarse, reverse). Case index = row of test.npz on every mesh."""
    d = Path(data_dir)
    mesh, tab, arrays = r0s.load(d, 'test', role)
    if role == 'learn':
        u = arrays['u']
    else:
        truth_u = np.load(d / 'truth_test.npz')['u']
        if role == 'truth':
            u = truth_u
        else:
            cfg, target = box_mesh(d, role)
            _, truth = box_mesh(d, 'truth')
            u = np.empty((len(truth_u), target.n_nodes), np.float32)
            for i in range(0, len(truth_u), chunk):
                lex = truth.to_lex(truth_u[i:i + chunk].astype(np.float64))
                u[i:i + chunk] = target.to_nodes(restrict_between(cfg, truth, target, lex))
    return R0SData(mesh, tab, {'layout': arrays['layout'], 'coef': arrays['coef'], 'u': u}, stats, device)
