"""Uniform node subsets keyed by physical identity (R0-S design section 4.2), next to the frozen refine sampler.

Every node gets a 64-bit key = hash(node kind, centroid quantised to 2^-30, case, epoch, salt); a draw takes the
`budget = max(1, int(N * sample_ratio))` nodes with the smallest keys (ties broken by the physical identity).
Hence
  * for a fixed configuration the chosen set is a uniformly random subset of that size (over cases / epochs);
  * the chosen PHYSICAL set does not depend on the node numbering: renumbering the mesh returns the same nodes;
  * it does not depend on the training seed (like the frozen sampler it depends only on case and epoch), so every
    arm of a seed sees the same subsets: a paired comparison.
No k-hop halo is added (Transolver-3 / AB-UPT style point subsets). The frozen k-hop / box sampler lives in
refine_sampling.py and is not touched.

    keys = NodeKeys(kind, centroid)                   # once per mesh (float64 centroids from the mesh file)
    nodes = uniform_nodes(keys, case, epoch, 0.12)    # sorted LongTensor, like khop_nodes
"""
from __future__ import annotations

import numpy as np
import torch

QUANTUM = 2.0 ** -30
SALT_TRAIN = 0x5CA10E5E
SALT_DEPLOY = 0x0DE9107E
_U = np.uint64


def _mix(x):
    """splitmix64 finaliser on a uint64 array (wrapping arithmetic)."""
    x = np.asarray(x, dtype=np.uint64) + _U(0x9E3779B97F4A7C15)
    x = (x ^ (x >> _U(30))) * _U(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> _U(27))) * _U(0x94D049BB133111EB)
    return x ^ (x >> _U(31))


class NodeKeys:
    """Physical identity of every node (kind, quantised centroid) and its 64-bit base hash."""

    def __init__(self, kind, centroid, quantum=QUANTUM):
        q = np.rint(np.asarray(centroid, np.float64) / quantum).astype(np.int64)
        self.ident = np.concatenate([np.asarray(kind, np.int64).reshape(-1, 1), q], 1)       # [N, 4]
        if len(np.unique(self.ident, axis=0)) != len(self.ident):
            raise ValueError('two nodes share kind and quantised centroid')
        h = _mix(self.ident[:, 0].astype(np.uint64))
        for c in range(1, self.ident.shape[1]):
            h = _mix(h ^ self.ident[:, c].astype(np.uint64))
        self.base = h
        self.n = len(h)

    def draw(self, case, epoch, salt=SALT_TRAIN):
        """Keys of one draw [N] (uint64)."""
        m = _mix(np.array([salt], np.uint64))
        m = _mix(m ^ np.array([case], np.int64).astype(np.uint64))
        m = _mix(m ^ np.array([epoch], np.int64).astype(np.uint64))
        return _mix(self.base ^ m)

    def ranked(self, keys, idx=None):
        """Node ids `idx` (all when None) ordered by key, ties by physical identity."""
        idx = np.arange(self.n) if idx is None else np.asarray(idx)
        i = self.ident[idx]
        return idx[np.lexsort((i[:, 3], i[:, 2], i[:, 1], i[:, 0], keys[idx]))]


def budget_of(n, sample_ratio):
    return max(1, int(n * sample_ratio))


def smallest(keys: NodeKeys, draw_keys, budget):
    """The `budget` node ids with the smallest draw keys (identity tie-break), in O(N)."""
    n = keys.n
    budget = min(int(budget), n)
    if budget == n:
        return np.arange(n)
    kth = np.partition(draw_keys, budget - 1)[budget - 1]
    below = np.nonzero(draw_keys < kth)[0]
    tied = np.nonzero(draw_keys == kth)[0]
    need = budget - len(below)
    if len(tied) > need:
        tied = keys.ranked(draw_keys, tied)[:need]
    return np.sort(np.concatenate([below, tied]))


def uniform_nodes(keys: NodeKeys, case, epoch, sample_ratio, salt=SALT_TRAIN):
    """Training draw: sorted LongTensor of budget_of(N, sample_ratio) nodes."""
    sel = smallest(keys, keys.draw(case, epoch, salt), budget_of(keys.n, sample_ratio))
    return torch.as_tensor(sel, dtype=torch.long)


def uniform_chunks(keys: NodeKeys, case, sample_ratio, draw=0, salt=SALT_DEPLOY):
    """Deployment: a random partition of the mesh into chunks of budget_of(N, sample_ratio) nodes (the last one may
    be smaller); every node appears exactly once. Yields sorted LongTensors."""
    order = keys.ranked(keys.draw(case, draw, salt))
    b = budget_of(keys.n, sample_ratio)
    for s in range(0, keys.n, b):
        yield torch.as_tensor(np.sort(order[s:s + b]), dtype=torch.long)


def stratified_face_pivots(keys: NodeKeys, groups, case, epoch, sample_ratio, salt=SALT_TRAIN):
    """A1: the uniform draw plus, for every group (patch id >= 0 per node, -1 = none) that the draw missed, the
    group's node with the smallest draw key. Returns a sorted LongTensor."""
    k = keys.draw(case, epoch, salt)
    sel = smallest(keys, k, budget_of(keys.n, sample_ratio))
    groups = np.asarray(groups)
    hit = np.zeros(groups.max() + 1 if groups.size and groups.max() >= 0 else 0, bool)
    g = groups[sel]
    hit[g[g >= 0]] = True
    extra = []
    for p in np.nonzero(~hit)[0]:
        members = np.nonzero(groups == p)[0]
        if len(members):
            extra.append(keys.ranked(k, members)[0])
    return torch.as_tensor(np.sort(np.concatenate([sel, np.asarray(extra, np.int64)])), dtype=torch.long)


def inclusion_probabilities(draw, n, draws):
    """Monte-Carlo inclusion probability pi_i of every node under a sampling configuration: `draw(e)` returns the
    node ids of draw e (a LongTensor or array); pi_i = (number of draws containing i) / draws."""
    count = np.zeros(n)
    for e in range(draws):
        nodes = draw(e)
        nodes = nodes.cpu().numpy() if torch.is_tensor(nodes) else np.asarray(nodes)
        count[np.unique(nodes)] += 1
    return count / draws


def z_order(points, bits=10):
    """Permutation of `points` [n, d] (d <= 3, float) into Z-order (Morton order) for spatially stratified anchors:
    per axis the dense rank of the coordinate (np.unique) scaled to `bits` bits, the bit patterns interleaved, then a
    stable sort of the codes. On a structured graded mesh (R0-S) the ranks are the cell indices along each axis, so an
    aligned run of 2^d cells of the order is one 2 x 2 x 2 block; on an unstructured mesh (DCTS) the ranks are
    quantile bins of the coordinates, finer where the cells are denser. Returns an int64 array of indices into
    `points`."""
    xyz = np.asarray(points, dtype=np.float64)
    if xyz.ndim != 2 or not 1 <= xyz.shape[1] <= 3:
        raise ValueError(f'z_order expects [n, d] points with d <= 3, got {xyz.shape}')
    d = xyz.shape[1]
    code = np.zeros(len(xyz), np.int64)
    for a in range(d):
        _, inv = np.unique(xyz[:, a], return_inverse=True)
        q = (inv.astype(np.int64) << bits) // (int(inv.max()) + 1)
        for b in range(bits):
            code |= ((q >> b) & 1) << (d * b + a)
    return np.argsort(code, kind='stable')
