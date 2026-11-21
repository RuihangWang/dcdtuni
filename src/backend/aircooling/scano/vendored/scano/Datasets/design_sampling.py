"""Sampling designs with known inclusion probabilities for A1 (visibility; PLAN sections 4 H1 and 5.4, decision d47).

The frozen refine sampler (refine_sampling.py) and the uniform subsets (subset_sampling.py) are not touched; every
design here is a separate option of the run configuration (sampling.faces, sampling.regime = "partition",
train.loss_weight = "ht"), and the defaults draw exactly what they drew before.

Stratified boundary faces
    The boundary faces of a case are split into strata (on R0-S: each patch of the case's layout, and the insulated
    part of each wall). Stratum h with m_h faces contributes n_h = min(m_h, max(1, floor(rho * m_h + 1/2))) faces,
    the n_h with the smallest draw keys, i.e. a simple random sample of the stratum; every stratum is seen in every
    draw and the inclusion probability of a face of stratum h is exactly pi = n_h / m_h. With these pi the
    Horvitz-Thompson count of the faces of a draw, sum_{i in S} 1 / pi_i, equals the number of faces in every draw.

Horvitz-Thompson weights
    w_i = 1 / pi_i makes sum_{i in S} w_i y_i a design-unbiased estimate of sum_i y_i over the mesh, for values y_i
    that do not depend on the draw. Applied to a training loss (train.loss_weight = "ht") the HT sum is unbiased for
    the whole-mesh node mean only at fixed predictions: a coupled model's prediction at node i changes with the other
    nodes of the draw (its global state), so the expected HT loss is not the whole-mesh loss of the whole-mesh
    prediction. This is a count-measure HT (every node, boundary faces included, weighs 1 in the target mean), not the
    volume form w / pi = V / pi of Proposition C (context.measure = "area_ht", train.loss_weight = "v_over_pi"). Against
    the unweighted loss it mainly up-weights the boundary faces, whose pi (~rho) is far below that of the cells (~0.6 at
    rho 0.12, 1 hop): about x5 relative to the cells. For the k-hop
    family (b pivots, then the closed k-hop ball of the sampling graph) a node is drawn when one of the m_i nodes of
    its closed ball B_i is a pivot:
        pi_i = 1 - prod_{k < m_i} (N - b - k) / (N - k),
    exact when the pivots are a simple random sample of the N nodes. The frozen sampler takes an arithmetic run of a
    per-case random permutation of the renumbered R0-S mesh; c47 (E0) found that the patch-miss law (1 - rho)^n holds
    for it, and tests/test_c60_sampling.py compares this pi with the frozen sampler's inclusion frequencies.

Partition + halo
    The cells are split into K blocks of (as nearly as possible) equal size by recursive coordinate bisection on the
    quantised centroids (longest extent first, ties by the other coordinates), which depends only on the geometry,
    not on the node numbering. A draw is one block: block (epoch + h(case)) mod K, h a hash of the case, so every case
    visits every block once in K consecutive epochs and at a fixed epoch the block is uniform over the cases (pi of
    a node = 1 / K).

Owned boundary faces (sampling.faces = "adjacent", c123; mock review round 7, both reviewers)
    The frozen k-hop sampler walks the stored, directed cell graph (owner -> neighbour, owner -> boundary face), so a
    boundary face enters a draw only as a pivot. The adjacent design keeps the cells of the native draw and adds every
    boundary face whose owner cell is among them, so face coverage follows cell coverage. It is applied inside the data
    objects' subgraph() (r0s_common.R0SData, dcts_common.GtcData), so training, validation, selection, the sample
    deployments, refine's TTA and sampled anchors all draw the same design; the whole mesh and the compact regions are
    not drawn through subgraph() and do not change.
"""
from __future__ import annotations

import numpy as np

from .subset_sampling import _mix

SALT_STRATA = 0x57A7F1ED          # within-stratum face draw (independent of the node draw)
SALT_BLOCK = 0x0B10C0FF           # per-case offset of the partition block schedule


def with_owned_faces(nodes, face_nodes, face_owner):
    """Sorted node ids of `nodes` (a LongTensor of a draw) plus every boundary-face node whose owner cell is in it.
    face_nodes [Nb] node ids of the boundary faces, face_owner [Nb] their owner cells (same device as `nodes`)."""
    import torch
    faces = face_nodes[torch.isin(face_owner, nodes)]
    return torch.unique(torch.cat([nodes, faces]))


def stratum_sizes(m, rho):
    """n_h = min(m_h, max(1, floor(rho * m_h + 1/2))) for stratum sizes m (array)."""
    m = np.asarray(m, np.int64)
    return np.minimum(m, np.maximum(1, np.floor(rho * m + 0.5).astype(np.int64)))


def stratified_take(labels, keys, rho, tiebreak=None):
    """Positions (into `labels`) of a stratified draw and their inclusion probabilities.

    labels    [M] stratum label of every unit (any integers);
    keys      [M] draw keys (uint64; a fresh hash per draw), the units with the smallest keys of a stratum are taken;
    tiebreak  [M, C] int64 columns that order equal keys (the physical identity), None = position.
    Returns (pos sorted int64, pi float64 of those units = n_h / m_h)."""
    labels = np.asarray(labels)
    _, inv, counts = np.unique(labels, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    n_h = stratum_sizes(counts, rho)
    cols = [] if tiebreak is None else [np.asarray(tiebreak)[:, c] for c in range(np.asarray(tiebreak).shape[1])][::-1]
    order = np.lexsort(tuple(cols) + (np.asarray(keys), inv))       # by stratum, then key, then identity
    start = np.concatenate([[0], np.cumsum(counts)[:-1]])
    rank = np.empty(len(order), np.int64)
    rank[order] = np.arange(len(order)) - start[inv[order]]
    take = np.nonzero(rank < n_h[inv])[0]
    return take, (n_h[inv[take]] / counts[inv[take]]).astype(np.float64)


def stratum_pi(labels, rho):
    """[M] inclusion probability n_h / m_h of every unit under the stratified draw."""
    _, inv, counts = np.unique(np.asarray(labels), return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    return (stratum_sizes(counts, rho) / counts)[inv].astype(np.float64)


def miss_probability(m, n, b):
    """P(none of m given units is among b units drawn without replacement from n) = prod_{k<m} (n-b-k)/(n-k),
    for an array m (0 when m > n - b)."""
    m = np.asarray(m, np.int64)
    top = int(m.max()) if m.size else 0
    k = np.arange(top, dtype=np.float64)
    with np.errstate(divide='ignore'):
        terms = np.log(np.clip((n - b - k) / (n - k), 0.0, None))
    cum = np.concatenate([[0.0], np.cumsum(terms)])
    return np.exp(cum[m])


def ball_sizes(edge_index, n, khop):
    """[n] size of the closed k-hop ball of every node in an undirected graph (edge_index [2, E], both directions
    or one: the graph is symmetrised)."""
    import scipy.sparse as sp
    e = np.asarray(edge_index)
    a = sp.coo_matrix((np.ones(e.shape[1], np.int32), (e[0], e[1])), shape=(n, n)).tocsr()
    a = ((a + a.T + sp.identity(n, dtype=np.int32, format='csr')) > 0).astype(np.int32)
    reach = sp.identity(n, dtype=np.int32, format='csr')
    for _ in range(int(khop)):
        reach = ((reach @ a) > 0).astype(np.int32)
    reach = reach.tocsr()
    reach.eliminate_zeros()
    return np.diff(reach.indptr).astype(np.int64)


def khop_pi(edge_index, n, khop, budget):
    """[n] inclusion probability of every node under b simple-random pivots followed by the closed k-hop ball."""
    return 1.0 - miss_probability(ball_sizes(edge_index, n, khop), n, budget)


def blocks_for(rho):
    """Number of partition blocks for a block fraction rho: max(1, round(1 / rho))."""
    return max(1, int(np.floor(1.0 / rho + 0.5)))


def rcb_blocks(coords, k):
    """[n] block id (0..k-1) of every point by recursive coordinate bisection into k blocks of sizes as equal as
    possible: split the longest extent of the current set (ties: the lower axis) at the floor(n k1 / k + 1/2)-th point
    in the order (that coordinate, then the others), k1 = k // 2. `coords` are integers (quantised centroids), so the
    result depends only on the geometry."""
    c = np.asarray(coords, np.int64)
    out = np.empty(len(c), np.int64)
    stack = [(np.arange(len(c)), int(k), 0)]
    while stack:
        idx, kk, first = stack.pop()
        if kk == 1:
            out[idx] = first
            continue
        sub = c[idx]
        ext = sub.max(0) - sub.min(0)
        ax = int(np.argmax(ext))
        others = [a for a in range(c.shape[1]) if a != ax]
        order = idx[np.lexsort(tuple(sub[:, a] for a in others[::-1]) + (sub[:, ax],))]
        k1 = kk // 2
        n1 = int(np.floor(len(idx) * k1 / kk + 0.5))
        stack.append((order[n1:], kk - k1, first + k1))
        stack.append((order[:n1], k1, first))
    return out


def block_of_draw(case, epoch, k, salt=SALT_BLOCK):
    """The block of draw (case, epoch): (epoch + h(case)) mod k."""
    h = _mix(_mix(np.array([salt], np.uint64)) ^ np.array([case], np.int64).astype(np.uint64))
    return int((int(h[0]) % int(k) + int(epoch)) % int(k))
