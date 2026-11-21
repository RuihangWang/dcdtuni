"""The identity of a block sparsity pattern, the key of every cache that pairs per-block data with a later matrix."""
from __future__ import annotations

import torch


def same_block_pattern(row, col, other_row, other_col) -> bool:
    """Whether two block patterns ``(row, col)`` are the same blocks in the same order.

    The pattern is the key of every cache that pairs per-block data with a later matrix (the cached
    Theta of ``NNmodels.coupled_amg_net.ThetaReuse``, the AMG plan of ``solve_with_coupled_newton.cached_amg_plan``).  The block COUNT is not
    a key: ``jacobian.assemble`` keeps only ``value != 0`` entries, so two Newton steps can give the same
    ``nnzb`` with different blocks, and per-block data laid on the wrong blocks is a silently wrong
    smoother (GPU-vs-OpenFOAM feasibility plan, section 0 item 8).  The same tensor objects need no
    comparison: a reused plan hands every level the plan's own ``row`` / ``col``, so the steady state
    of a reuse run costs no device sync.  Otherwise the comparison reads both patterns once (about
    0.8 GB of int64 at LDC96's 24M blocks, a fraction of a millisecond at HBM bandwidth, beside a
    multi-second AMG build).
    """
    if row is other_row and col is other_col:
        return True
    return (row.shape == other_row.shape and col.shape == other_col.shape
            and bool(torch.equal(row, other_row)) and bool(torch.equal(col, other_col)))
