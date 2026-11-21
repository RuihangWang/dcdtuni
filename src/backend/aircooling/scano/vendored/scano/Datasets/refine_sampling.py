"""Subgraph sampling ported verbatim from Gen-FVGN-refine (the manuscript repository).

Source (read-only, pinned): Gen-FVGN-refine branch roi-sampling @ 7d4a2b3716f356744f2205da238f0f1d429cf318
  - src/FVdomain/HomoGraph_dataset.py : _init_sampling (budget, pools), _generate_train_pool_params,
    _sample_train_chunk, _sample_from_pool (train and inference branches), k_hop_subgraph call in get();
  - src/FVdomain/sampling_regimes.py : validate_regime_cfg, draw_regime, draw_box, select_in_box,
    draw_box_pivots (ROI-aware training draws), copied unchanged.
tests/test_refine_sampling.py compares every function here with the refine originals when that repository is
available (SCANO_REFINE_ROOT), so a drift in either copy fails the tests.

What refine does, in one place:
  training   budget = max(1, int(N * sample_ratio)); each case owns a lazy permutation
             (offset + p * stride) % N (seed 12345 + case_idx, stride coprime with N); epoch e takes the
             contiguous slice starting at (e * budget) % N as pivots. With an ROI config, a (case, epoch) coin
             with probability 1 - p_scatter replaces that by an axis-aligned box draw of the same budget.
  inference  single GPU: pool = randperm(N) with seed 42 + case_idx, consumed in chunks of `budget` until
             exhausted; every node is a pivot exactly once.
  subgraph   k_hop_subgraph(pivots, khop, edge_index, relabel_nodes=True, num_nodes=N); with batch_size 1
             there is no truncation. Inference accumulates predictions over ALL subgraph nodes of every
             chunk and divides by the per-node count (Runner._accumulate_predictions/_finalize_predictions).
"""
from __future__ import annotations

import math

import torch
from torch import Tensor

# ---------------------------------------------------------------------------------------------------------
# HomoGraph_dataset.py (training / inference pivot pools)
# ---------------------------------------------------------------------------------------------------------


def sample_budget(num_cells: int, sample_ratio: float) -> int:
    """_init_sampling: `budget = max(1, int(num_cells * self.sample_ratio))`."""
    return max(1, int(num_cells * sample_ratio))


def generate_train_pool_params(num_cells: int, seed: int):
    """HomoGraphDataset._generate_train_pool_params (unchanged)."""
    if num_cells <= 1:
        return 0, 1

    generator = torch.Generator()
    generator.manual_seed(int(seed))
    offset = int(torch.randint(0, num_cells, (1,), generator=generator).item())

    while True:
        stride = int(torch.randint(1, num_cells, (1,), generator=generator).item())
        if math.gcd(stride, num_cells) == 1:
            break

    return offset, stride


def sample_train_chunk(num_cells: int, offset: int, stride: int, start: int, count: int) -> Tensor:
    """HomoGraphDataset._sample_train_chunk with the per-case state passed explicitly (unchanged maths)."""
    if count <= 0:
        return torch.empty((0,), dtype=torch.long)
    positions = (torch.arange(start, start + count, dtype=torch.long)) % num_cells
    return (offset + positions * stride) % num_cells


def train_pivots(case_idx: int, epoch: int, num_cells: int, sample_ratio: float,
                 roi_cfg: dict | None = None, centroids: Tensor | None = None) -> Tensor:
    """HomoGraphDataset._sample_from_pool, training branch (seed 12345 + case_idx as in _init_sampling)."""
    budget = sample_budget(num_cells, sample_ratio)
    offset, stride = generate_train_pool_params(num_cells, 12345 + case_idx)
    pool_length = int(num_cells)
    budget = min(int(budget), pool_length)
    if roi_cfg is not None:
        if draw_regime(case_idx, int(epoch), roi_cfg)["kind"] == "box":
            if centroids is None:
                raise ValueError('ROI box draw needs the case centroids (refine would silently fall back)')
            return draw_box_pivots(centroids, case_idx, int(epoch), budget)
    start = (int(epoch) * budget) % max(1, pool_length)
    return sample_train_chunk(num_cells, offset, stride, start, budget)


def inference_pool(case_idx: int, num_cells: int) -> Tensor:
    """_init_sampling, inference branch, single GPU: randperm with seed 42 + case_idx."""
    full_pool = torch.arange(num_cells)
    generator = torch.Generator()
    generator.manual_seed(42 + case_idx)
    perm = torch.randperm(num_cells, generator=generator)
    return full_pool[perm]


def inference_chunks(case_idx: int, num_cells: int, sample_ratio: float):
    """_sample_from_pool, inference branch: consecutive `budget` slices of the pool until it is exhausted."""
    pool = inference_pool(case_idx, num_cells)
    budget = sample_budget(num_cells, sample_ratio)
    pos = 0
    while pos < len(pool):
        end_pos = min(pos + budget, len(pool))
        yield pool[pos:end_pos]
        pos = end_pos


def khop_nodes(pivots: Tensor, khop: int, edge_index: Tensor, num_nodes: int) -> Tensor:
    """HomoGraphDataset.get(): k_hop_subgraph(..., relabel_nodes=True, num_nodes=N); returns the sorted node ids."""
    from torch_geometric.utils import k_hop_subgraph
    subset, _, _, _ = k_hop_subgraph(pivots.to(edge_index.device), khop, edge_index,
                                     relabel_nodes=True, num_nodes=num_nodes)
    return subset


# ---------------------------------------------------------------------------------------------------------
# sampling_regimes.py (copied unchanged)
# ---------------------------------------------------------------------------------------------------------

_SIZE_SPAN = 8
_SHAPE_LO, _SHAPE_HI = 0.25, 4.0
_REGIME_SALT = 0x5CA1AB1E
_BOX_SALT = 0x0B0C5EED
_PICK_SALT = 0x1D0FFEED
_ALLOWED_CFG_KEYS = ("p_scatter",)
_REMOVED_CFG_KEYS = {
    "budget_range": "the pivot budget is fixed at sample_ratio * N and no longer varies",
    "anchor_frac_range": "the whole-domain top-up is driven by geometry, not a fraction",
    "anchor_frac": "the whole-domain top-up is driven by geometry, not a fraction",
    "reference_budget": "there is no k-d order to build, so there is no reference granularity",
}


def validate_regime_cfg(cfg: dict) -> None:
    unknown = sorted(set(cfg) - set(_ALLOWED_CFG_KEYS))
    for key in unknown:
        if key in _REMOVED_CFG_KEYS:
            raise ValueError(
                f"ROI sampling config key {key!r} belongs to the superseded k-d-block "
                f"sampler and is no longer read: {_REMOVED_CFG_KEYS[key]}. Remove it. "
                f"Supported keys: {list(_ALLOWED_CFG_KEYS)}"
            )
    if unknown:
        raise ValueError(
            f"Unknown ROI sampling config key(s) {unknown}. Supported keys: "
            f"{list(_ALLOWED_CFG_KEYS)}"
        )

    if "p_scatter" not in cfg:
        raise ValueError(
            f"ROI sampling config is missing required key 'p_scatter'. Required keys: "
            f"{list(_ALLOWED_CFG_KEYS)}; got {sorted(cfg)}"
        )
    try:
        p = float(cfg["p_scatter"])
    except (TypeError, ValueError):
        raise ValueError(
            f"p_scatter must be a number in [0, 1], got {cfg['p_scatter']!r}"
        ) from None
    if not (0.0 <= p <= 1.0) or math.isnan(p):
        raise ValueError(f"p_scatter must lie within [0, 1], got {p!r}")


def _generator(case_idx: int, epoch: int, salt: int) -> torch.Generator:
    g = torch.Generator()
    g.manual_seed((int(case_idx) * 1_000_003 + int(epoch)) ^ int(salt))
    return g


def draw_regime(case_idx: int, epoch: int, cfg: dict) -> dict:
    g = _generator(case_idx, epoch, _REGIME_SALT)
    u = float(torch.rand(1, generator=g).item())
    return {"kind": "scatter" if u < float(cfg["p_scatter"]) else "box"}


def draw_box(centroids: Tensor, case_idx: int, epoch: int,
             budget: int) -> tuple[Tensor, Tensor]:
    lo_d = centroids.min(0).values
    hi_d = centroids.max(0).values
    extent = (hi_d - lo_d).clamp(min=0.0)
    num_cells = int(centroids.shape[0])

    g = _generator(case_idx, epoch, _BOX_SALT)
    u = torch.rand(7, generator=g).to(extent.dtype)

    lo_n = max(1, int(budget) // _SIZE_SPAN)
    hi_n = max(lo_n, min(num_cells, int(budget) * _SIZE_SPAN))
    target = math.exp(math.log(lo_n) + float(u[0]) * (math.log(hi_n) - math.log(lo_n)))
    frac = min(1.0, max(1e-12, target / max(1, num_cells)))

    log_lo, log_hi = math.log(_SHAPE_LO), math.log(_SHAPE_HI)
    w = torch.exp(u[1:4] * (log_hi - log_lo) + log_lo)
    c = (frac / float(w.prod())) ** (1.0 / 3.0)
    side = torch.minimum(c * w * extent, extent)

    slack = (extent - side).clamp(min=0.0)
    lo = lo_d + u[4:7] * slack
    return lo, lo + side


def select_in_box(centroids: Tensor, box_lo: Tensor, box_hi: Tensor, budget: int,
                  generator: torch.Generator) -> Tensor:
    num_cells = int(centroids.shape[0])
    budget = min(int(budget), num_cells)
    if budget <= 0:
        return torch.empty((0,), dtype=torch.long)

    inside = ((centroids >= box_lo) & (centroids <= box_hi)).all(dim=1)
    in_ids = torch.nonzero(inside).reshape(-1)

    if int(in_ids.numel()) >= budget:
        pick = torch.randperm(int(in_ids.numel()), generator=generator)[:budget]
        return in_ids[pick].long()

    out_ids = torch.nonzero(~inside).reshape(-1)
    need = budget - int(in_ids.numel())
    pick = torch.randperm(int(out_ids.numel()), generator=generator)[:need]
    return torch.cat([in_ids, out_ids[pick]]).long()


def draw_box_pivots(centroids: Tensor, case_idx: int, epoch: int,
                    budget: int) -> Tensor:
    box_lo, box_hi = draw_box(centroids, case_idx, epoch, budget)
    return select_in_box(centroids, box_lo, box_hi, budget,
                         _generator(case_idx, epoch, _PICK_SALT))
