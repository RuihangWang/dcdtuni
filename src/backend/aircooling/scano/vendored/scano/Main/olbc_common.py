"""Shared pieces of the olbc sampled-training test (c37): data on the device, refine-style subgraphs,
prediction with an explicit context set, and refine's inference accumulation.

Sampling follows Gen-FVGN-refine exactly (scano.Datasets.refine_sampling): batch_size 1, pivots from the
case's permutation (or an ROI box), k-hop subgraph on the mesh graph, every subgraph node supervised.
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F

from ..Datasets.refine_sampling import draw_box_pivots, inference_chunks, khop_nodes, sample_budget, train_pivots


class OlbcData:
    """Shared mesh plus per-case boundary data and targets, on the device."""

    def __init__(self, mesh, arrays, stats, device):
        self.device = device
        c = mesh['centroid']
        self.n = len(c)
        self.centroid_cpu = torch.tensor(c, dtype=torch.float32)  # [N,3], what refine's ROI box draw reads
        xy = c[:, :2].astype(np.float32)
        src = (mesh['source'] / stats['src_std']).astype(np.float32)
        self.static = torch.tensor(np.concatenate([xy, mesh['udf'][:, None], src[:, None]], 1),
                                   dtype=torch.float32, device=device)
        self.area = torch.tensor(mesh['area'] / mesh['area'].mean(), dtype=torch.float32, device=device)
        self.edge_index = torch.tensor(mesh['edge_index'], dtype=torch.long, device=device)
        self.xy = torch.tensor(xy, device=device)
        self.unv = torch.tensor(mesh['unv'][:, :2], dtype=torch.float32, device=device)
        self.bidx = torch.tensor(arrays['bidx'].astype(np.int64), device=device)       # [S,NB], per case as refine
        self.barc = torch.tensor(arrays['barc'], dtype=torch.float32, device=device)
        self.sol = torch.tensor(arrays['sol'], dtype=torch.float32, device=device)
        self.btype = torch.tensor(arrays['btype'].astype(np.int64), device=device)
        self.bg = torch.tensor(arrays['bg'] / stats['g_std'], dtype=torch.float32, device=device)
        self.balpha = torch.tensor(arrays['balpha'] / stats['a_std'], dtype=torch.float32, device=device)
        self.mean, self.std = stats['mean'], stats['std']

    def bnd_record(self, case):
        """[NB,5] = one-hot type (D,N,R), g, alpha."""
        onehot = F.one_hot(self.btype[case], 3).float()
        return torch.cat([onehot, self.bg[case][:, None], self.balpha[case][:, None]], 1)

    def node_features(self, case, nodes):
        """[P,10] = x, y, udf, source, is_boundary, one-hot type, g, alpha (BC fields zero on interior nodes)."""
        rec = self.bnd_record(case)
        slot = torch.full((self.n,), -1, dtype=torch.long, device=self.device)
        slot[self.bidx[case]] = torch.arange(self.bidx.shape[1], device=self.device)
        s = slot[nodes]
        isb = s >= 0
        dyn = torch.zeros(nodes.numel(), 6, device=self.device)
        dyn[isb, 0] = 1.0
        dyn[isb, 1:] = rec[s[isb]]
        return torch.cat([self.static[nodes], dyn], 1)

    def boundary(self, case):
        """[NB,9] = x, y, nx, ny, one-hot type, g, alpha; and [NB] arc-length weights."""
        b = self.bidx[case]
        return torch.cat([self.xy[b], self.unv[b], self.bnd_record(case)], 1), self.barc[case]

    def target(self, case, nodes):
        return self.sol[case, nodes]

    def subgraph(self, pivots, khop):
        return khop_nodes(pivots, khop, self.edge_index, self.n)


def train_nodes(data, case_idx, epoch, sample_ratio, khop, roi_cfg):
    """refine training draw for one case: pivots (permutation slice or ROI box), then the k-hop subgraph."""
    piv = train_pivots(case_idx, epoch, data.n, sample_ratio, roi_cfg, data.centroid_cpu)
    return data.subgraph(piv, khop)


def predict(model, data, case, nodes, arm, anchors=None, full_context=False):
    """Denormalised predictions at `nodes` (LongTensor on the device) for one case.

    coupled / single: context = the nodes themselves (area weights);
    decoupled:        context = `anchors` (area weights), or every node when full_context (mesh cache).
    """
    if arm == 'decoupled' and full_context:
        allnodes = torch.arange(data.n, device=data.device)
        return predict(model, data, case, allnodes, 'coupled')[nodes]
    if arm == 'decoupled':
        pts = torch.cat([nodes, anchors])
        ctx = torch.cat([torch.zeros(nodes.numel(), device=data.device), data.area[anchors]])
    else:
        pts, ctx = nodes, data.area[nodes]
    bf, bw = data.boundary(case)
    out = model(data.node_features(case, pts)[None], ctx[None], bf[None], bw[None])[0, :nodes.numel()]
    return out * data.std + data.mean


def draw_anchors(data, n_anchors, rng):
    pool = getattr(data, 'anchor_pool', None)          # r0s: cells only; olbc has none (random stream unchanged)
    if pool is not None:
        return torch.as_tensor(rng.choice(pool, n_anchors, replace=False), device=data.device)
    return torch.as_tensor(rng.choice(data.n, n_anchors, replace=False), device=data.device)


def refine_tta(model, data, case, sample_ratio, khop, arm, n_anchors, rng):
    """refine inference: the case's pivot pool in `budget` chunks, a k-hop subgraph per chunk, predictions
    accumulated over every subgraph node and divided by the per-node count. Returns (field [N], count [N])."""
    total = torch.zeros(data.n, device=data.device)
    count = torch.zeros(data.n, device=data.device)
    for piv in inference_chunks(case, data.n, sample_ratio):
        nodes = data.subgraph(piv, khop)
        anchors = draw_anchors(data, n_anchors, rng) if arm == 'decoupled' else None
        total.index_add_(0, nodes, predict(model, data, case, nodes, arm, anchors))
        count.index_add_(0, nodes, torch.ones(nodes.numel(), device=data.device))
    return total / count.clamp(min=1), count


ROI_SHAPE = (0.5, 2.0)                  # side ratios of a compact region (log-uniform; c112 amendment 2)


def compact_region(data, case, share, draw, seed=261014):
    """c112 amendment 2 (10-14): a compact region of interest -- every node (cell or boundary face) whose centroid
    lies in one axis-aligned box holding at least `share` of the cells: the centre a random cell, the three side
    ratios log-uniform in ROI_SHAPE times the extent of the cells, the size the smallest common scale (bisection) at
    which the box holds the share. Unlike refine's box draw (kind 'box' of `single_subgraph`, which draws its seeds
    inside a box of random size, tops them up over the whole mesh when the box holds fewer and grows them by k hops),
    nothing outside the box and every node inside it. Sorted node ids on the data's device."""
    pos = data.centroid_cpu.double()
    cmask = getattr(data, 'cell_mask_cpu', None)      # c121: meshes whose cells are not the first nodes (R0-S)
    if cmask is not None:
        cells = pos[cmask]
        ncell = int(cells.shape[0])
    else:
        ncell = int(getattr(data, 'n_cells', pos.shape[0]))
        cells = pos[:ncell]
    lo, hi = cells.min(0).values, cells.max(0).values
    ext = (hi - lo).clamp(min=1e-12)
    rng = np.random.default_rng([int(seed), int(case), int(draw), int(round(share * 1e6))])
    centre = cells[int(rng.integers(ncell))]
    w = torch.from_numpy(np.exp(rng.uniform(np.log(ROI_SHAPE[0]), np.log(ROI_SHAPE[1]), pos.shape[1])))
    target = max(1, int(np.ceil(share * ncell)))
    dist = (cells - centre).abs() / (0.5 * w * ext)            # the box of scale s holds the cells with dist <= s
    need = dist.max(1).values                                   # the scale at which each cell enters the box
    s = float(torch.kthvalue(need, target).values)              # the smallest scale holding `target` cells
    inside = ((pos - centre).abs() <= 0.5 * s * w * ext * (1 + 1e-12)).all(1)
    return torch.nonzero(inside).reshape(-1).to(data.device)


def single_subgraph(data, case, kind, sample_ratio, khop, draw):
    """One deployment subgraph: the `draw`-th chunk of refine's inference pool (scatter) or refine's ROI box
    draw at the same budget with epoch=`draw` (box); then the k-hop subgraph. kind 'roi' (c112 amendment 2): the
    compact region of `compact_region` holding the share `sample_ratio` of the cells (no k-hop growth)."""
    if kind == 'roi':
        return compact_region(data, case, sample_ratio, draw)
    if kind == 'scatter':
        for i, piv in enumerate(inference_chunks(case, data.n, sample_ratio)):
            if i == draw:
                break
    else:
        piv = draw_box_pivots(data.centroid_cpu, case, draw, sample_budget(data.n, sample_ratio))
    return data.subgraph(piv, khop)


def rel_l2(pred, ref):
    return float(torch.linalg.vector_norm(pred - ref) / torch.linalg.vector_norm(ref))


def train_stats(mesh, arrays, n_train):
    tr = slice(0, n_train)
    alpha_r = arrays['balpha'][tr][arrays['btype'][tr] == 2]
    return {'mean': float(arrays['sol'][tr].mean()), 'std': float(arrays['sol'][tr].std()),
            'g_std': float(arrays['bg'][tr].std()),
            'a_std': float(alpha_r.std()) if alpha_r.size and alpha_r.std() > 0 else 1.0,
            'src_std': float(mesh['source'].std())}
