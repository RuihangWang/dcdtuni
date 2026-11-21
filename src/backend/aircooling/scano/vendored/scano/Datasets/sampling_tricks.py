"""Training-draw variants of the sampled-training tricks (A8: cases c61 on R0-S and c62 on DCTS; PLAN section
5.4), next to the frozen samplers they modify. Every draw here is keyed by (case, epoch) through the splitmix64 hash of
scano.Datasets.subset_sampling, like the frozen samplers themselves: it does not depend on the training seed (every arm
of a seed sees the same draws, a paired comparison) and it needs no random state, so resuming a run is exact.

  mixed_ratio(case, epoch, ratios, draw)     the sampling ratio of one training draw (sampling.rho_mix): drawn
                                             uniformly from a set, or log-uniformly from a range [lo, hi];
  add_missed_groups(nodes, groups, keys, ...) the patch-stratified top-up (sampling.stratify = "patch"): any base draw
                                             (k-hop, uniform subset, roi) plus, for every group (patch) none of whose
                                             members the draw holds, the member with the smallest draw key. On top of
                                             the uniform draw (same keys and salt) it equals main's
                                             subset_sampling.stratified_face_pivots; on other draws it is its own
                                             design. It is NOT the A1 (c60) stratified design, which c60 implements
                                             separately (sampling.faces = "stratified", proportional allocation with
                                             exact pi); session decision of 10-07: c61's strat and ipw arms wait for
                                             c60's sampler and are not trained with this one;
  masked_records(record_keys, case, epoch, p) the boundary records whose values are hidden in one draw
                                             (boundary.node_bc_mask = p): the complement of a uniform subset of
                                             max(1, int(NB * (1 - p))) records, as the LFE extender keeps
                                             int(NB * (1 - p_masking)) records per layer (LFE's own p_masking draws
                                             with torch.randperm on the seeded torch stream instead);
  second_draw_epoch(case, epoch)             the key of the second draw of a step (train.consistency):
                                             SECOND_DRAW_EPOCH + a hashed offset in [0, 2^31). The frozen k-hop
                                             sampler takes the pivot window starting at (key * budget) mod N, so a
                                             fixed offset (key = epoch + const) would give a window shifted by a fixed
                                             amount from draw 1's (on R0-S 56% of the pivots shared at every step);
                                             the hashed offset spreads draw 2's start over [0, N) (over the multiples
                                             of gcd(budget, N); 1 on the R0-S learning mesh at 0.12) and the pivot
                                             overlap averages budget / N, as for two independent draws.
                                             It also keeps the roi coin and box of draw 2 apart from those of case + 1
                                             (refine's generator seeds case * 1,000,003 + key, so key = epoch +
                                             1,000,003 reproduced case + 1's draw); a collision with some real draw is
                                             left only when the key falls within `epochs` of a multiple of 1,000,003,
                                             a chance of about epochs / 1e6 per step. SECOND_DRAW_EPOCH only keeps the
                                             key above every training epoch.
"""
from __future__ import annotations

import math

import numpy as np
import torch

from .subset_sampling import SALT_TRAIN, NodeKeys, _mix, uniform_nodes

SALT_RHO = 0x7A11E5ED          # mixed sampling ratio of a draw
SALT_BC_MASK = 0x0BCDA5C0      # hidden boundary values of a draw (independent of the node and record draws)
SALT_SECOND = 0x5EC0D2A7       # key offset of the second draw of a step (consistency loss)
SECOND_DRAW_EPOCH = 1_000_003  # base of the second draw's key: above every training epoch (its primality plays no role)


def keyed_uniform(case, epoch, salt):
    """A number in [0, 1) that depends only on (case, epoch, salt): 53 bits of the splitmix64 hash."""
    m = _mix(np.array([salt], np.uint64))
    m = _mix(m ^ np.array([case], np.int64).astype(np.uint64))
    m = _mix(m ^ np.array([epoch], np.int64).astype(np.uint64))
    return float(int(m[0]) >> 11) / float(1 << 53)


def second_draw_epoch(case, epoch):
    """Key of the second, independent draw of the step (case, epoch) (train.consistency): SECOND_DRAW_EPOCH plus a
    hashed offset in [0, 2^31), so the k-hop pivot window of draw 2 is not a fixed shift of draw 1's (module
    docstring)."""
    return SECOND_DRAW_EPOCH + int(keyed_uniform(case, epoch, SALT_SECOND) * 2 ** 31)


def mixed_ratio(case, epoch, ratios, draw='set', salt=SALT_RHO):
    """Sampling ratio of the training draw of (case, epoch): ratios[k] with k uniform over the set (draw = "set"), or
    exp(U(log lo, log hi)) for ratios = [lo, hi] (draw = "log_uniform")."""
    u = keyed_uniform(case, epoch, salt)
    if draw == 'set':
        return float(ratios[min(int(u * len(ratios)), len(ratios) - 1)])
    if draw == 'log_uniform':
        lo, hi = ratios
        return float(math.exp(math.log(lo) + u * (math.log(hi) - math.log(lo))))
    raise ValueError(f'unknown ratio draw {draw!r}')


def add_missed_groups(nodes, groups, keys: NodeKeys, case, epoch, salt=SALT_TRAIN):
    """Sorted LongTensor (on nodes' device): `nodes` plus, for every group id g >= 0 of `groups` ([N] ints, -1 = no
    group) that has members but none in `nodes`, the member with the smallest key of keys.draw(case, epoch, salt)
    (ties by physical identity). The added nodes carry no k-hop halo (on R0-S a boundary face is in a k-hop sample
    only as a pivot, so a face pivot adds the face alone)."""
    groups = np.asarray(groups)
    sel = nodes.detach().cpu().numpy()
    n_groups = int(groups.max()) + 1 if groups.size and groups.max() >= 0 else 0
    hit = np.zeros(n_groups, bool)
    g = groups[sel]
    hit[g[g >= 0]] = True
    extra = []
    if not hit.all():
        k = keys.draw(case, epoch, salt)
        for p in np.nonzero(~hit)[0]:
            members = np.nonzero(groups == p)[0]
            if len(members):
                extra.append(keys.ranked(k, members)[0])
    if not extra:
        return nodes
    out = np.sort(np.concatenate([sel, np.asarray(extra, np.int64)]))
    return torch.as_tensor(out, dtype=torch.long, device=nodes.device)


def masked_records(record_keys: NodeKeys, case, epoch, p, salt=SALT_BC_MASK):
    """Sorted LongTensor of the record indices whose values are hidden in the draw of (case, epoch): every record not
    in the uniform subset of max(1, int(NB * (1 - p))) records."""
    keep = uniform_nodes(record_keys, case, epoch, 1.0 - p, salt).numpy()
    hidden = np.ones(record_keys.n, bool)
    hidden[keep] = False
    return torch.as_tensor(np.nonzero(hidden)[0], dtype=torch.long)
