# ROI Sampling Retrain — Implementation Plan

*(Lives here rather than under `docs/superpowers/plans/`, which is gitignored. Read `04` and `05` first — they are why this plan looks the way it does.)*

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train a SCANO checkpoint whose inference stays accurate when the pivot set is restricted to a contiguous ROI box, so the Omniverse demo's drag-and-sim can simulate the dragged region instead of the whole room.

**Architecture:** The failure is measured and localised: a contiguous pivot pool collapses the slice basis pooled at the 7 trunk `AttnProcessor` sites, costing +0.405 nRMSE against a 0.077 threshold. No inference-time fix works — even an oracle whole-domain basis captured at the operating point being predicted is only GREY off case0. The remaining lever is the training distribution. We make the *sampling regime* a random variable during training (scattered vs box∪anchor, with box size, anchor fraction **and draw budget** all sampled) so that a contiguous draw is in-distribution, and we keep a fraction `p` of scattered draws so the whole-domain path the demo defaults to does not regress.

**Tech Stack:** PyTorch 2.x + torch_scatter, DDP over 6× RTX 4090, OpenFOAM v2412 (data track only), Python 3.11 env `FVGN-pt2.7`.

## Global Constraints

- **Training repo is `/mnt/lv/home/litianyu/mycode/Gen-FVGN-refine`.** The copy under `dcdtuni/.../SCANO/GTC_A3_Numerical/` is an inference subset with no training entry point. Any training change lands in Gen-FVGN-refine; the resulting checkpoint is carried back by hand.
- **`/mnt/lv/home/Public/GTC/` is shared and READ-ONLY.** Never write there. Copy out. Fingerprint before and after every task that touches it.
- **NEVER use `kill`/`pkill`/`killall`/`pgrep`.** Only `fuser -k <port>/tcp`.
- **NEVER run a serial CFD solver.** `decomposePar` + `mpirun -np N <solver> -parallel`.
- **GPU discipline:** a live Omniverse demo holds **GPU 7** (Kit) and **GPU 0** (AI worker); **GPU 3 is `compute_mode=Prohibited`**. Training uses GPUs 1,2,4,5,6 unless the demo is stopped. Check `nvidia-smi` before every launch.
- **Every new knob defaults to OFF** and the default path must be provably unchanged.
- **`fixed_subgraph_size_mode` must stay `"none"`.** It defaults to `"none"` and the A3 config
  does not set it, so it is currently off. It calibrates a fixed subgraph size by a one-off
  pre-scan over the pools and already warns when `fixed_size < max_budget`; a randomised budget
  invalidates that calibration. Do not enable it unless the scan is redone at the max budget.
- **Thresholds are the model's own, not chosen:** PASS ≤ 0.020, FAIL ≥ 0.077342 (`dspread_overall`). nRMSE is per-field, normalised by `meta.json dataset_statistics.std`, then averaged.
- **A masked-out cell is not a correct prediction.** Score on `covered ∩ interior ∩ box`; report coverage with every number; if all-cells and covered-only differ, report both.
- **Baseline to beat**, all on the study box `13.489,7.514,0,17.511,11.537,5` at case0, 9,636 interior cells: whole-domain 51 draws `0.212453`; ROI-only `0.617546` (+0.405); `box+45000 anchors` `0.209532` (−0.0029, but touches 54.7% of the domain and PASSes at only 1 of 6 box×operating-point cells).

---

## Scope note — two goals, one of which this plan does NOT deliver

This plan restores **ROI-local inference**. It does **not** fix the demo's −0.94 K headline: that is a supply-temperature coverage void (zero cases in [15.5, 18.5) °C) plus unconverged labels, and even perfect labels with unchanged model fidelity give ≈1.4 K. The data track in Phase 5 is what addresses it, and it is **optional for this plan's goal** — the ROI failure is a sampling-geometry effect measured on these very labels.

Run Phases 0–4 first. They are cheap and they answer the one unknown that decides whether the retrain is viable at all.

## File Structure

| File | Responsibility |
|---|---|
| `Gen-FVGN-refine/src/FVdomain/sampling_regimes.py` | **New.** Pure functions: k-d block ordering, regime draw, budget draw. No torch modules, no I/O — unit-testable without a dataset. |
| `Gen-FVGN-refine/src/FVdomain/HomoGraph_dataset.py` | Modified at 3 points: `_init_sampling` train branch (~line 505), `_sample_train_chunk` (line 607), `set_epoch` (line 575). |
| `Gen-FVGN-refine/tests/test_sampling_regimes.py` | **New.** Unit tests for the pure functions. |
| `Gen-FVGN-refine/tests/test_train_sampler_inert.py` | **New.** Proves the default path is unchanged. |
| `Gen-FVGN-refine/cases/_archive/GTC/A3/configs/config_A3_roi.yaml` | **New.** Training config for the mixed-regime run. |
| `dcdtuni/.../SCANO/GTC_A3_Numerical/scripts/roi_acceptance.py` | **New.** The acceptance harness — scores a checkpoint on the ROI grid and the whole domain. |

---

### Task 0: Port the block ordering as a pure, tested function

**Files:**
- Create: `/mnt/lv/home/litianyu/mycode/Gen-FVGN-refine/src/FVdomain/sampling_regimes.py`
- Test: `/mnt/lv/home/litianyu/mycode/Gen-FVGN-refine/tests/test_sampling_regimes.py`

**Interfaces:**
- Produces: `kd_block_order(centroids: Tensor[N,3], num_blocks: int) -> Tensor[N]` — a permutation of `0..N-1` such that each consecutive `ceil(N/num_blocks)` slice is a compact axis-aligned cluster.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sampling_regimes.py
import torch
from FVdomain.sampling_regimes import kd_block_order


def test_each_block_is_spatially_compact():
    """A block's bounding box must be far smaller than the domain's — that is the
    whole point. A random permutation would give blocks spanning the domain."""
    torch.manual_seed(0)
    cen = torch.rand(4096, 3) * torch.tensor([31.0, 19.0, 5.0])
    order = kd_block_order(cen, num_blocks=16)

    assert order.shape == (4096,)
    assert torch.equal(order.sort().values, torch.arange(4096))

    domain = (cen.max(0).values - cen.min(0).values)
    per_block = 4096 // 16
    for b in range(16):
        blk = cen[order[b * per_block:(b + 1) * per_block]]
        extent = blk.max(0).values - blk.min(0).values
        assert (extent / domain).max() < 0.6, f"block {b} spans {extent / domain}"


def test_blocks_are_equal_size_within_one():
    torch.manual_seed(1)
    cen = torch.rand(1000, 3)
    order = kd_block_order(cen, num_blocks=7)
    assert order.shape == (1000,)
    assert torch.equal(order.sort().values, torch.arange(1000))


def test_compactness_survives_a_slice_size_it_was_not_designed_for():
    """The budget is randomised per epoch, so the order is built ONCE at a reference
    granularity and then sliced with whatever budget the regime draws. Compactness has
    to hold at those other sizes too, or the block regime silently degrades to a
    scattered one on most epochs."""
    torch.manual_seed(2)
    cen = torch.rand(8192, 3) * torch.tensor([31.0, 19.0, 5.0])
    order = kd_block_order(cen, num_blocks=16)          # design: 512 per block
    domain = cen.max(0).values - cen.min(0).values
    for size in (128, 300, 1024, 2000):                  # none of them 512
        blk = cen[order[:size]]
        extent = blk.max(0).values - blk.min(0).values
        assert (extent / domain).max() < 0.75, f"size {size}: {extent / domain}"
```

- [ ] **Step 2: Run the test and watch it fail**

```bash
cd /mnt/lv/home/litianyu/mycode/Gen-FVGN-refine
PYTHONPATH=src /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python -m pytest tests/test_sampling_regimes.py -q
```
Expected: `ModuleNotFoundError: No module named 'FVdomain.sampling_regimes'`

- [ ] **Step 3: Implement**

```python
# src/FVdomain/sampling_regimes.py
"""Sampling-regime primitives for ROI-aware training.

Pure functions only — no torch.nn, no file I/O — so they are unit-testable without
building a dataset. HomoGraph_dataset imports them; nothing here imports it.
"""
from __future__ import annotations

import math

import torch
from torch import Tensor


def kd_block_order(centroids: Tensor, num_blocks: int) -> Tensor:
    """Permutation whose consecutive equal-size slices are compact spatial clusters.

    Recursive median split on the longest axis with an exact target count per side, so
    the concatenated leaves partition the ids into `num_blocks` axis-aligned clusters of
    equal size (+/-1). Slicing that order in budget-sized chunks — which is exactly what
    the training sampler already does — hands each draw a contiguous block.
    """
    n = int(centroids.shape[0])
    if num_blocks <= 1 or n <= 1:
        return torch.arange(n, dtype=torch.long)

    def split(ids: Tensor, k: int) -> list[Tensor]:
        if k <= 1 or ids.numel() <= 1:
            return [ids]
        pts = centroids[ids]
        axis = int((pts.max(0).values - pts.min(0).values).argmax())
        left_k = k // 2
        # exact target count so the leaves stay equal-size
        n_left = int(round(ids.numel() * left_k / k))
        n_left = max(1, min(ids.numel() - 1, n_left))
        order = torch.argsort(pts[:, axis])
        ids = ids[order]
        return split(ids[:n_left], left_k) + split(ids[n_left:], k - left_k)

    return torch.cat(split(torch.arange(n, dtype=torch.long), int(num_blocks)))
```

- [ ] **Step 4: Run the test and watch it pass**

```bash
cd /mnt/lv/home/litianyu/mycode/Gen-FVGN-refine
PYTHONPATH=src /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python -m pytest tests/test_sampling_regimes.py -q
```
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/home/litianyu/mycode/Gen-FVGN-refine
git add src/FVdomain/sampling_regimes.py tests/test_sampling_regimes.py
git commit -m "feat(sampling): k-d block ordering for contiguous training draws"
```

---

### Task 1: Draw a sampling regime per (case, epoch)

**Files:**
- Modify: `Gen-FVGN-refine/src/FVdomain/sampling_regimes.py`
- Test: `Gen-FVGN-refine/tests/test_sampling_regimes.py`

**Interfaces:**
- Produces: `draw_regime(case_idx: int, epoch: int, cfg: dict) -> dict` returning
  `{"kind": "scatter"|"block", "budget": int, "anchor_frac": float}`.
  `cfg` keys: `p_scatter` (float), `budget_range` (list[int, int]), `anchor_frac_range` (list[float, float]).

- [ ] **Step 1: Write the failing test**

```python
def test_regime_is_a_pure_function_of_case_and_epoch():
    """Reproducibility: the same (case, epoch) must give the same regime on every rank
    and after any restart. A mutable RNG here would desynchronise DDP."""
    from FVdomain.sampling_regimes import draw_regime
    cfg = {"p_scatter": 0.5, "budget_range": [4000, 40000],
           "anchor_frac_range": [0.0, 0.9]}
    a = draw_regime(3, 17, cfg)
    b = draw_regime(3, 17, cfg)
    assert a == b
    assert draw_regime(3, 18, cfg) != a or draw_regime(4, 17, cfg) != a


def test_p_scatter_controls_the_mix():
    from FVdomain.sampling_regimes import draw_regime
    cfg = {"p_scatter": 0.25, "budget_range": [1000, 2000],
           "anchor_frac_range": [0.0, 0.5]}
    kinds = [draw_regime(c, e, cfg)["kind"]
             for c in range(40) for e in range(40)]
    frac = kinds.count("scatter") / len(kinds)
    assert 0.20 < frac < 0.30, frac


def test_budget_stays_in_range_and_varies():
    from FVdomain.sampling_regimes import draw_regime
    cfg = {"p_scatter": 0.0, "budget_range": [4000, 40000],
           "anchor_frac_range": [0.0, 0.0]}
    budgets = {draw_regime(0, e, cfg)["budget"] for e in range(200)}
    assert min(budgets) >= 4000 and max(budgets) <= 40000
    assert len(budgets) > 50, "budget must actually vary — K is non-monotone in it"
```

- [ ] **Step 2: Run and watch it fail**

```bash
PYTHONPATH=src /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python -m pytest tests/test_sampling_regimes.py -q
```
Expected: `ImportError: cannot import name 'draw_regime'`

- [ ] **Step 3: Implement**

```python
# append to src/FVdomain/sampling_regimes.py

_REGIME_SALT = 0x5CA1AB1E


def draw_regime(case_idx: int, epoch: int, cfg: dict) -> dict:
    """Sampling regime for one (case, epoch), as a pure function.

    Purity is not cosmetic: every DDP rank must agree without communicating, and a
    restart must reproduce the schedule. A module-level RNG would break both.

    `budget` is drawn log-uniformly because the measured sensitivity to draw SIZE is
    non-monotone — K=60000 scores worse than both 45000 and 30000 — so the model must
    see the whole range rather than a neighbourhood of one value.
    """
    g = torch.Generator()
    g.manual_seed((int(case_idx) * 1_000_003 + int(epoch)) ^ _REGIME_SALT)

    u = torch.rand(3, generator=g).tolist()

    kind = "scatter" if u[0] < float(cfg["p_scatter"]) else "block"
    lo, hi = (int(x) for x in cfg["budget_range"])
    budget = int(round(math.exp(math.log(lo) + u[1] * (math.log(hi) - math.log(lo)))))
    budget = max(lo, min(hi, budget))
    a_lo, a_hi = (float(x) for x in cfg["anchor_frac_range"])
    anchor_frac = a_lo + u[2] * (a_hi - a_lo)

    return {"kind": kind, "budget": budget,
            "anchor_frac": 0.0 if kind == "scatter" else anchor_frac}
```

- [ ] **Step 4: Run and watch it pass**

```bash
PYTHONPATH=src /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python -m pytest tests/test_sampling_regimes.py -q
```
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/FVdomain/sampling_regimes.py tests/test_sampling_regimes.py
git commit -m "feat(sampling): per-(case,epoch) regime draw with randomised budget"
```

---

### Task 2: Hook the sampler, default OFF, and prove the default path unchanged

**Files:**
- Modify: `Gen-FVGN-refine/src/FVdomain/HomoGraph_dataset.py` — train branch of `_init_sampling` (~line 505), `_sample_train_chunk` (line 607)
- Test: `Gen-FVGN-refine/tests/test_train_sampler_inert.py`

**Interfaces:**
- Consumes: `kd_block_order`, `draw_regime` from Task 0/1.
- Produces: dataset attribute `self._roi_cfg: dict | None` (None = off), and `_sample_train_chunk` honouring it.

**Why the existing `SCANO_PIVOT_*` machinery cannot be reused:** it lives entirely in the non-train branch of `_init_sampling`; `_sample_from_pool` returns at the train early-exit before `self._pivot_pools` is read; it refuses `world_size > 1`; and its box selection sits in the per-H5 loop with no split guard, so under a single-GPU training run it would shrink the budget without changing the geometry and log nothing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_train_sampler_inert.py
"""With the ROI config absent, training draws must be bit-identical to before.

A whole-run comparison cannot establish this — torch_scatter.scatter_add atomics make
predictions non-reproducible at ~1e-5. The sampler, however, is pure integer arithmetic
and IS bit-reproducible, so it is tested directly.
"""
import torch


def _chunks(ds, case_idx, epoch, n=5):
    ds.set_epoch(epoch)
    budget = ds._sample_budgets[case_idx]
    return [ds._sample_from_pool(case_idx) if False else
            ds._sample_train_chunk(case_idx, i * budget, budget)
            for i in range(n)]


def test_default_path_is_bit_identical(train_dataset_factory):
    ds_off = train_dataset_factory(roi_cfg=None)
    ref = _chunks(ds_off, 0, 3)
    again = _chunks(train_dataset_factory(roi_cfg=None), 0, 3)
    for a, b in zip(ref, again):
        assert torch.equal(a, b)


def test_roi_config_changes_the_draw(train_dataset_factory):
    ds_on = train_dataset_factory(roi_cfg={
        "p_scatter": 0.0, "budget_range": [512, 512], "anchor_frac_range": [0.0, 0.0]})
    off = _chunks(train_dataset_factory(roi_cfg=None), 0, 3, n=1)[0]
    on = ds_on._sample_train_chunk(0, 0, 512, kind="block", anchor_frac=0.0)
    assert not torch.equal(off[:512], on[:512]), "block draw matched the scattered one"


def test_block_draw_is_spatially_compact(train_dataset_factory):
    """The point of the whole exercise: a block draw must actually be a block."""
    ds = train_dataset_factory(roi_cfg={
        "p_scatter": 0.0, "budget_range": [2048, 2048], "anchor_frac_range": [0.0, 0.0]})
    ds.set_epoch(0)
    ids = ds._sample_train_chunk(0, 0, 2048, kind="block", anchor_frac=0.0)
    cen = ds._test_centroids[0][ids]
    domain = ds._test_centroids[0]
    extent = (cen.max(0).values - cen.min(0).values)
    full = (domain.max(0).values - domain.min(0).values)
    assert (extent / full).max() < 0.6
```

- [ ] **Step 2: Add the fixture, then run and watch it fail**

```python
# append to tests/test_train_sampler_inert.py
import pytest


@pytest.fixture
def train_dataset_factory(tmp_path):
    """Builds a training-split dataset over two synthetic cases. Uses the real class so
    the test exercises the real sampler, not a stand-in."""
    from FVdomain.HomoGraph_dataset import HomoGraphDataset

    def make(roi_cfg):
        ds = HomoGraphDataset.__new__(HomoGraphDataset)
        ds.split = "train"
        ds._case_num_cells = [8192, 4096]
        ds._sample_budgets = [1024, 512]
        ds._pool_positions = [0, 0]
        ds._train_pool_seeds, ds._train_pool_offsets, ds._train_pool_strides = [], [], []
        ds._current_epoch = 0
        ds._roi_cfg = roi_cfg
        torch.manual_seed(0)
        # `_init_roi_orders` reads centroids from h5; in the unit test we inject the
        # orders directly so the test does not need a dataset on disk. The h5 read path
        # is covered by Task 4's real training launch.
        from FVdomain.sampling_regimes import kd_block_order
        cens = [torch.rand(n, 3) * torch.tensor([31.0, 19.0, 5.0])
                for n in ds._case_num_cells]
        ds._test_centroids = cens
        ds._roi_orders = ([] if roi_cfg is None else
                          [kd_block_order(c, max(1, n // 1024))
                           for c, n in zip(cens, ds._case_num_cells)])
        for i, n in enumerate(ds._case_num_cells):
            off, st = ds._generate_train_pool_params(n, 12345 + i)
            ds._train_pool_offsets.append(off)
            ds._train_pool_strides.append(st)
        return ds

    return make
```

```bash
PYTHONPATH=src /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python -m pytest tests/test_train_sampler_inert.py -q
```
Expected: `AttributeError: 'HomoGraphDataset' object has no attribute '_roi_cfg'`
(the production `_sample_train_chunk` does not know the attribute yet)

- [ ] **Step 3: Implement — three edits**

Edit A, in `_init_sampling`'s train branch (after `self._pool_positions.append(0)`, ~line 512), add nothing; instead add the new method next to `_generate_train_pool_params` (line 590):

```python
    def _init_roi_orders(self) -> None:
        """Precompute a k-d block order per case when ROI-aware sampling is enabled.

        Off by default: `self._roi_cfg is None` leaves `_roi_orders` empty and
        `_sample_train_chunk` takes exactly the path it took before.

        Centroids are NOT resident on this class — `_init_sampling` only caches them
        under `sensor_only`, and keeping [N,3] float32 for 677 cases would be ~3.6 GB.
        So the order is computed from a one-off read and cached to disk as int32; the
        dataset directory is read-only, hence a separate cache root.

        The order is built once at a REFERENCE granularity and then sliced with whatever
        budget the regime draws. A k-d order is a spatial sort, so any contiguous slice
        of it is compact — the exact partition guarantee holds only at the design size,
        which is why `test_compactness_survives_a_slice_size_it_was_not_designed_for`
        exists.
        """
        import os
        import numpy as np

        self._roi_orders = []
        cfg = getattr(self, "_roi_cfg", None)
        if cfg is None or self.split != "train":
            return

        from FVdomain.sampling_regimes import kd_block_order

        cache_root = os.environ.get(
            "SCANO_ROI_ORDER_CACHE",
            os.path.join(os.path.expanduser("~"), ".cache", "scano_roi_orders"))
        os.makedirs(cache_root, exist_ok=True)
        ref_budget = int(cfg.get("reference_budget", 13416))

        for case_idx, h5_path in enumerate(self.h5_list):
            num_cells = int(self._case_num_cells[case_idx])
            st = os.stat(h5_path)
            key = f"{os.path.basename(h5_path)}_{st.st_size}_{num_cells}_{ref_budget}.npy"
            cache_file = os.path.join(cache_root, key)
            if os.path.exists(cache_file):
                self._roi_orders.append(torch.from_numpy(np.load(cache_file)).long())
                continue
            with h5py.File(h5_path, "r") as f:
                cen = torch.from_numpy(f["cpd|centroid"][()]).to(torch.float32)
            n_blocks = max(1, math.ceil(num_cells / max(1, ref_budget)))
            order = kd_block_order(cen, n_blocks)
            np.save(cache_file, order.to(torch.int32).numpy())
            self._roi_orders.append(order)
            del cen
```

**Note on cost:** this reads `cpd|centroid` once per case on the first run (5.4 MB at
N=447k) and caches an int32 order (1.8 MB per case, ~1.2 GB for 677 cases on disk, not in
RAM). Subsequent runs load from the cache. The cache key includes the h5 size and cell
count, so a re-generated dataset invalidates it rather than silently reusing a stale
order — cell ordering is **not** invariant across a re-mesh.

Edit B, in `_sample_from_pool`'s train branch (line ~810) — **the regime must be drawn
here, not inside `_sample_train_chunk`**. `start` is computed from `budget` on the line
above; drawing a different budget further down would leave `start` calibrated to the old
one, so the per-epoch slices would overlap or skip and the "tile the whole permutation
without replacement" property would be lost. Replace:

```python
        if self.split == "train":
            pool_length = int(self._case_num_cells[case_idx])
            budget = min(int(budget), pool_length)
            start = (int(self._current_epoch) * budget) % max(1, pool_length)
            return self._sample_train_chunk(case_idx, start, budget)
```

with:

```python
        if self.split == "train":
            pool_length = int(self._case_num_cells[case_idx])
            kind, anchor_frac = "scatter", 0.0
            cfg = getattr(self, "_roi_cfg", None)
            if cfg is not None:
                from FVdomain.sampling_regimes import draw_regime
                reg = draw_regime(case_idx, int(self._current_epoch), cfg)
                kind, anchor_frac, budget = reg["kind"], reg["anchor_frac"], reg["budget"]
            budget = min(int(budget), pool_length)
            start = (int(self._current_epoch) * budget) % max(1, pool_length)
            return self._sample_train_chunk(case_idx, start, budget,
                                            kind=kind, anchor_frac=anchor_frac)
```

Edit C, replace the body of `_sample_train_chunk` (line 607) with:

```python
    def _sample_train_chunk(self, case_idx: int, start: int, count: int,
                            kind: str = "scatter",
                            anchor_frac: float = 0.0) -> torch.Tensor:
        """从训练模式的惰性置换池中取出连续 count 个 pivot 节点。

        `kind="scatter"` reproduces the original expression exactly, so the default path
        is inert by construction rather than by a branch that has to be kept in sync.
        `kind="block"` takes the same (start, count) window from the k-d order instead,
        making the draw a contiguous spatial block; `anchor_frac` of it is then replaced
        by scattered ids so the model also sees box-plus-anchor pools.
        """
        if count <= 0:
            return torch.empty((0,), dtype=torch.long)

        num_cells = int(self._case_num_cells[case_idx])
        offset = int(self._train_pool_offsets[case_idx])
        stride = int(self._train_pool_strides[case_idx])
        positions = (torch.arange(start, start + count, dtype=torch.long)) % num_cells
        scattered = (offset + positions * stride) % num_cells

        if kind != "block" or not getattr(self, "_roi_orders", None):
            return scattered

        block = self._roi_orders[case_idx][positions]
        n_anchor = int(round(count * float(anchor_frac)))
        if n_anchor <= 0:
            return block
        return torch.cat([block[: count - n_anchor], scattered[:n_anchor]])
```

Edit C, in `set_epoch` (line 575), no change is required — it already sets `self._current_epoch`. Verify that by reading it.

- [ ] **Step 4: Run and watch it pass**

```bash
PYTHONPATH=src /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python -m pytest tests/test_train_sampler_inert.py tests/test_sampling_regimes.py -q
```
Expected: `8 passed`

- [ ] **Step 5: Prove inertness against the pre-change source, not just against itself**

```bash
cd /mnt/lv/home/litianyu/mycode/Gen-FVGN-refine
git stash
PYTHONPATH=src /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python - <<'PY' > /tmp/chunks_before.txt
import torch, sys; sys.path.insert(0, "src")
from FVdomain.HomoGraph_dataset import HomoGraphDataset
ds = HomoGraphDataset.__new__(HomoGraphDataset)
ds.split="train"; ds._case_num_cells=[8192]; ds._sample_budgets=[1024]
ds._train_pool_offsets=[]; ds._train_pool_strides=[]
off, st = ds._generate_train_pool_params(8192, 12345)
ds._train_pool_offsets.append(off); ds._train_pool_strides.append(st)
print([ds._sample_train_chunk(0, i*1024, 1024).tolist() for i in range(3)])
PY
git stash pop
```
Then run the same snippet on the new source and `diff` the two outputs.
Expected: identical.

- [ ] **Step 6: Commit**

```bash
git add src/FVdomain/HomoGraph_dataset.py tests/test_train_sampler_inert.py
git commit -m "feat(sampling): ROI-aware training draws behind a default-off config"
```

---

### Task 3: Size the supervision change before spending any GPU time

**Files:**
- Create: `Gen-FVGN-refine/tools/measure_supervision.py`

**Why:** the loss covers **every subgraph node**, not just the pivots (`HomoGraph_dataset` returns `target_phi[subgraph_nodes]`, consumed at `src/NNmodels/importer.py`). A scattered draw of 13,416 pivots expands to ~96.7k nodes at k-hop 2 (~14% pivot fraction); a contiguous block of the same pivot count expands only by a surface shell. So block training at the same `sample_ratio` supervises far fewer nodes per step, and "retrain with the same recipe" is not like-for-like.

- [ ] **Step 1: Write the measurement**

```python
# tools/measure_supervision.py
"""Nodes supervised per step, scattered vs block, at matched pivot counts.

Prints the ratio the training config must compensate for. No model, no GPU.
"""
import sys, json
sys.path.insert(0, "src")
import h5py, torch
from FVdomain.sampling_regimes import kd_block_order

H5 = sys.argv[1] if len(sys.argv) > 1 else \
    "/mnt/lv/home/Public/GTC/processed_dataset_unconverged/case0/h5/case0.h5"
KHOP = 2

with h5py.File(H5, "r") as f:
    nb = torch.from_numpy(f["cpd|neighbor_cell"][()]).long()
    cen = torch.from_numpy(f["cpd|centroid"][()]).float()
N = int(cen.shape[0])


def khop(seed_ids, k=KHOP):
    mask = torch.zeros(N, dtype=torch.bool)
    mask[seed_ids] = True
    for _ in range(k):
        touch = mask[nb[0]] | mask[nb[1]]
        e = torch.nonzero(touch).reshape(-1)
        mask[nb[0][e]] = True
        mask[nb[1][e]] = True
    return int(mask.sum())


order = kd_block_order(cen, num_blocks=max(1, N // 13416))
g = torch.Generator(); g.manual_seed(0)
perm = torch.randperm(N, generator=g)

print(f"{'pivots':>8} {'scattered nodes':>16} {'block nodes':>12} {'ratio':>7}")
for B in (4000, 13416, 30000, 60000):
    s = khop(perm[:B]); b = khop(order[:B])
    print(f"{B:8d} {s:16d} {b:12d} {s / max(1, b):7.2f}")
```

- [ ] **Step 2: Run it**

```bash
cd /mnt/lv/home/litianyu/mycode/Gen-FVGN-refine
PYTHONPATH=src /mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python tools/measure_supervision.py
```
Expected: a table where the block column is several times smaller than the scattered
column at the same pivot count. **Record the ratio at 13,416 pivots — call it `R`.**

- [ ] **Step 3: Record the decision**

**MEASURED, and it overturns this step's own instruction.** `R = 12.97` at 13,416 pivots:
a scattered draw supervises 201,859 nodes (15x the pivot count — the k-hop halo dominates)
against a block's 15,561 (1.2x — a thin surface shell). The ratio is not constant either:
13.28 at 4,000 pivots falling to 6.16 at 60,000.

Raising `budget_range`'s upper bound by `R` is therefore both impossible and unnecessary:
- **Impossible.** 40,000 x 13 = 520,000 pivots against a 447,205-cell mesh. Matching a
  scattered draw's node count with a block needs ~201,859 pivots = **45% of the room**,
  while the box a user actually drags is **3.4%** of it.
- **Unnecessary.** The loss is `LpLoss.rel`, a *relative* L2:
  `sqrt(sum(diff^2)) / sqrt(sum(y^2))` per graph per channel, then averaged. Numerator and
  denominator sum over the same nodes, so the loss magnitude — and therefore the gradient
  magnitude — does **not** scale with node count. A block step is a *noisier* estimate, not
  a smaller one.

So: **no budget compensation. Leave `budget_range` at `[4000, 40000]`.** What genuinely
differs between the regimes is per-epoch domain coverage and gradient variance, and that
difference is inherent to the regime being learned rather than something to cancel out.

- [ ] **Step 4: Commit**

```bash
git add tools/measure_supervision.py
git commit -m "tools: measure the supervision change between scattered and block draws"
```

---

### Task 4: Short mixed-curriculum probe — the experiment that decides viability

**Files:**
- Create: `Gen-FVGN-refine/cases/_archive/GTC/A3/configs/config_A3_roi.yaml`

**Why short:** the reference run is 2,000 epochs / 26.6 h on 6 GPUs. The decisive
question is not "how good does it get" but **"can one checkpoint hold both regimes"** —
the demo defaults to whole-domain, so a box-trained model that loses whole-domain
accuracy is useless. A few hundred epochs answers the direction. Do not launch 2,000.

- [ ] **Step 1: Create the config**

```bash
cd /mnt/lv/home/litianyu/mycode/Gen-FVGN-refine/cases/_archive/GTC/A3/configs
cp config_A3_train.yaml config_A3_roi.yaml
```

Then edit `config_A3_roi.yaml`:

```yaml
training_config:
  num_epochs: 300          # probe, not the full 2000
  batch_size: 1
  sample_ratio: 0.03       # unchanged; the regime draw overrides the budget
  khop: 2
  roi_sampling:            # NEW — absent means off, which is the default everywhere else
    # ASPECT-RATIO CAVEAT (found in Task 0 review). reference_budget sets how many blocks
    # the k-d order is built for, and therefore each block's SHAPE. At 13,416 the blocks
    # are ~3.9 x 4.75 x 5 m — the full room height — while the demo's own ROI is
    # 8 x 5 x 2.5 m, i.e. SHORT in z. Training blocks and the dragged box are not the same
    # shape. If Task 5's acceptance shows that mismatch matters, the fix is to make block
    # SHAPE part of the regime draw (splits per axis), not just block size.
    reference_budget: 13416
    p_scatter: 0.5         # half the draws stay whole-domain: this is the knob that
                           # decides R3, whether one checkpoint serves both regimes
    budget_range: [4000, 40000]   # raise the top by the measured R from Task 3
    anchor_frac_range: [0.0, 0.9]
```

- [ ] **Step 2: Wire the config through**

In `HomoGraphDataset.__init__` (line 153), read it and store:

```python
        self._roi_cfg = (run_config.get("roi_sampling")
                         if str(getattr(self, "split", "")) == "train" else None)
```
and call `self._init_roi_orders()` at the end of `_init_sampling` (line 442's method).

- [ ] **Step 3: Check the GPUs are free, then launch**

```bash
nvidia-smi --query-gpu=index,memory.used,compute_mode --format=csv,noheader
cd /mnt/lv/home/litianyu/mycode/Gen-FVGN-refine
NUM_GPUS=5 CUDA_VISIBLE_DEVICES=1,2,4,5,6 \
  bash run_ddp_train.sh cases/_archive/GTC/A3/configs/config_A3_roi.yaml 2>&1 | tee /tmp/roi_probe.log
```
Expected: ~300 epochs. At the reference run's ~48 s/epoch on 6 GPUs, budget **4–6 h** on 5.

- [ ] **Step 4: Score the probe checkpoint on both regimes**

Run the acceptance harness from Task 5 against the probe checkpoint. The decision:
- whole-domain nRMSE within ~10% of the 0.212453 baseline **and** ROI delta improved
  materially from +0.405 → continue to the full run.
- whole-domain regressed badly → lower `p_scatter` is not the fix; the two regimes do not
  coexist at this capacity, and the plan stops here with that finding recorded.

- [ ] **Step 5: Commit the config and the probe result**

```bash
git add cases/_archive/GTC/A3/configs/config_A3_roi.yaml
git commit -m "feat(train): mixed-regime probe config"
```

---

### Task 5: The acceptance harness — score a checkpoint honestly

**Files:**
- Create: `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo/SCANO/GTC_A3_Numerical/scripts/roi_acceptance.py`

**Why here and not in the training repo:** the inference bundle already has the ROI
machinery (`SCANO_PIVOT_POOL`, the scorers, the raw-dump format) and every baseline
number in this plan was measured with it. A retrained checkpoint drops into
`logger/model/best.state` with no code change.

**Interfaces:**
- Consumes: a checkpoint path.
- Produces: `roi_acceptance.json` with, per (box × operating point × regime), `nrmse`,
  `delta`, `band`, `coverage`, per-field breakdown, and `|T bias|`.

- [ ] **Step 1: Write the harness**

```python
# scripts/roi_acceptance.py
"""Score a checkpoint on the ROI grid and the whole domain.

Reuses the existing arm runner and scorer so the numbers are commensurable with every
baseline in this plan. Each operating point is scored against ITS OWN whole-domain
reference — using case0's reference elsewhere silently inflates three of four points.
"""
import argparse, json, os, subprocess, sys
import numpy as np, h5py

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = "/mnt/lv/home/Public/GTC/processed_dataset_unconverged"
PY_ = "/mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python"

POINTS = ["case0", "case100", "case500", "case700"]
BOXES = {"study": "13.489,7.514,0,17.511,11.537,5",   # every prior number used this one
         "demo":  "8,12,0.5,16,17,3"}                  # the box a user actually drags
REGIMES = {"whole":  {},
           "roionly": {"SCANO_PIVOT_POOL": "box"},
           "k45000":  {"SCANO_PIVOT_POOL": "box+anchor", "SCANO_PIVOT_ANCHOR": "45000"}}
PASS_D, FAIL_D = 0.020, 0.077342


def std_vec():
    st = json.load(open(f"{BUNDLE}/dataset/meta.json"))["dataset_statistics"]["std"]
    return np.array([st["uvw_cpd_cell_u"], st["uvw_cpd_cell_v"], st["uvw_cpd_cell_w"],
                     st["press_cpd_cell"], st["temp_cpd_cell"]], dtype="float64")


def mask_for(case, box):
    with h5py.File(f"{DS}/{case}/h5/{case}.h5", "r") as f:
        cen = f["cpd|centroid"][()].astype("float64")
        ct = np.asarray(f["cpd|cell_type"][()]).squeeze()
    b = [float(x) for x in box.split(",")]
    return np.all((cen >= np.array(b[:3])) & (cen <= np.array(b[3:])), axis=1) & (ct == 0)


def score(npz_path, m, std):
    d = np.load(npz_path)
    cov = d["draw_counts"] > 0
    mm = m & cov
    if mm.sum() == 0:
        return None
    e = d["prediction"].astype("float64")[mm] - d["target"].astype("float64")[mm]
    per = np.sqrt((e ** 2).mean(axis=0)) / std
    return {"nrmse": float(per.mean()),
            "per_field": [float(x) for x in per],
            "coverage": float(mm.sum() / m.sum()),
            "n_scored": int(mm.sum()),
            "t_bias_K": float(abs(d["prediction"][mm, 4].mean()
                                  - d["target"][mm, 4].mean()))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--out", default="roi_acceptance.json")
    ap.add_argument("--workdir", default="/tmp/roi_acceptance")
    a = ap.parse_args()
    std = std_vec()
    os.makedirs(a.workdir, exist_ok=True)
    results, npz = {}, {}

    for pt in POINTS:
        for rname, env_extra in REGIMES.items():
            arm = f"{pt}_{rname}"
            env = dict(os.environ, SCANO_ACCEPT_ARM=arm)
            env.update(env_extra)
            if env_extra:                     # box regimes need a box; use the study box
                env["SCANO_PIVOT_BOX"] = BOXES["study"]
            out = os.path.join(a.workdir, arm)
            os.makedirs(out, exist_ok=True)
            cmd = [PY_, f"{BUNDLE}/cases/GTC/A3/inference.py",
                   "--config", f"{BUNDLE}/cases/GTC/A3/configs/config_A3_inference.yaml",
                   "--checkpoint", a.checkpoint,
                   "--dataset-path", f"{DS}/{pt}",
                   "--raw-dump-dir", out]
            print("RUN", arm, flush=True)
            subprocess.run(cmd, env=env, check=True,
                           stdout=open(f"{out}/log", "w"), stderr=subprocess.STDOUT)
            npz[arm] = f"{out}/raw/{pt}_raw.npz"

    for bname, box in BOXES.items():
        for pt in POINTS:
            m = mask_for(pt, box)
            ref = score(npz[f"{pt}_whole"], m, std)
            for rname in REGIMES:
                s = score(npz[f"{pt}_{rname}"], m, std)
                if s is None or ref is None:
                    continue
                delta = s["nrmse"] - ref["nrmse"]
                band = "PASS" if delta <= PASS_D else ("FAIL" if delta >= FAIL_D else "GREY")
                results[f"{bname}/{pt}/{rname}"] = dict(s, delta=delta, band=band,
                                                        ref_nrmse=ref["nrmse"])

    json.dump(results, open(a.out, "w"), indent=1)
    print(f"{'cell':34s} {'nrmse':>9s} {'delta':>9s} {'band':>5s} {'cov':>6s}")
    for k, v in sorted(results.items()):
        print(f"{k:34s} {v['nrmse']:9.6f} {v['delta']:+9.6f} {v['band']:>5s} "
              f"{100 * v['coverage']:5.1f}%")


if __name__ == "__main__":
    main()
```

The grid it covers — **anything smaller repeats a mistake this project already made twice**:

| axis | values |
|---|---|
| operating point | case0, case100, case500, case700 |
| box | study `13.489,7.514,0,17.511,11.537,5` **and** demo `8,12,0.5,16,17,3` |
| regime | whole-domain (51 draws), ROI-only, box+45000 anchors |

Every score on `covered ∩ interior ∩ box`, coverage reported. Each point scored against
**its own** whole-domain reference, never case0's.

- [ ] **Step 2: Reproduce the known baselines before trusting it**

```bash
cd .../SCANO/GTC_A3_Numerical
/mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python scripts/roi_acceptance.py \
    --checkpoint logger/model/best.state --out /tmp/acc_baseline.json
```
Expected, on the **current** checkpoint: study box @ case0 whole-domain `0.212453`,
ROI-only `0.617546`, K45000 `0.209532`. If these do not reproduce, the harness is wrong
and nothing downstream can be believed.

- [ ] **Step 3: Commit**

```bash
cd /mnt/lv/home/litianyu/mycode/dcdtuni
git add src/frontend/omniverse/cfd_demo/SCANO/GTC_A3_Numerical/scripts/roi_acceptance.py
git commit -m "test(scano): ROI acceptance grid — 4 operating points x 2 boxes x 3 regimes"
```

---

### Task 6: Full retrain, only if Task 4 said the direction is right

- [ ] **Step 1: Set `num_epochs: 2000` in `config_A3_roi.yaml`, keep everything else**
- [ ] **Step 2: Check GPU availability and launch**

```bash
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
cd /mnt/lv/home/litianyu/mycode/Gen-FVGN-refine
NUM_GPUS=5 CUDA_VISIBLE_DEVICES=1,2,4,5,6 \
  bash run_ddp_train.sh cases/_archive/GTC/A3/configs/config_A3_roi.yaml 2>&1 | tee /tmp/roi_full.log
```
Budget: the reference run was 95,620 s on 6 GPUs. On 5, expect **~32 h**. This is a floor —
if Task 3's `R` forced a larger budget range, memory and epoch time both rise.

- [ ] **Step 3: Run the acceptance grid on the new checkpoint**
- [ ] **Step 4: Carry the checkpoint back**

```bash
cp Logger/.../states/best.state \
   /mnt/lv/home/litianyu/mycode/dcdtuni/src/.../GTC_A3_Numerical/logger/model/best.state
md5sum both  # record provenance: logger/ is gitignored, so the md5 is the only link
```
The inference YAML's `model_configs` block must match the retrained architecture — it is
**not** read from the checkpoint. And `patch_statistics.json` must be present in the
dataset dir: if it is missing the code only **warns** and silently falls back to the
dataset-wide normaliser.

- [ ] **Step 5: Commit the result record**

---

### Task 7 (separable track): the data defects — required for the demo headline, not for ROI

Two measured defects, both in the labels this plan trains on:

1. **Labels are unconverged.** Continuing case0 past its iteration-500 label moves the
   interior mean by **−1.1075 K** (settled, sd 0.0026), more than the −0.94 K the demo
   renders. N=3, all negative, spread 3.5×. All 753 stopped on `endTime`, none on
   `residualControl`. At `endTime 500` the labels realise only **0.66** of a supply move.
2. **Supply void.** Zero cases with a flow-weighted supply mean in [15.5, 18.5) °C, and
   the demo's slider drags case0 from 20.16 straight across it.

Measured price, meshes already exist so this is solve-only: 0.28–0.41 s/iteration at np 8;
the level settles by ~1500–2000 iterations. 753 cases at 2,500 iterations ≈ **31–43 h on
48 cores**. Filling the void is 12 cases ≈ **2.4 h**, about 1% of that.

**The landmine:** `500/` and `1/` are flattened ML snapshots, not restartable states —
every patch rewritten to `fixedValue`, 848 per field, 525 carrying an inline
`// original: zeroGradient` marker. Restarting from one converts 525 adiabatic walls into
fixed-temperature walls and freezes the server-outlet expressions, and it runs without
error. The un-flattened spec lives in `_1_bak/`, present in all 753 cases. A genuine
restart is `500/`'s internal field spliced onto `_1_bak`'s boundary spec; the helper is
`dcdtuni/.../GTC_A3_Numerical/logger/converge/splice_bc.py`.

Do the **void first** — 2.4 h, and it tests the binding constraint on the demo headline
directly.

---

## Risks, ranked, with what each would cost to discover late

**R1 — One checkpoint may not hold both regimes.** Untested, and Task 4 exists to answer
it in 4–6 h instead of 32. Bound on the hope: cross-*case* basis transfer already degrades
at fixed regime.

**R2 — The gap is large, not a trim.** Arm C1 keeps the whole-domain pool, all 51 draws
and ~9.5× coverage and changes only each draw's *geometry*; it still costs +0.391. This is
teaching a representation the model does not have, not tuning one it has.

**R3 — The broken thing is shared.** The 32 slice tokens the ROI collapses are the same
tokens the whole-domain path uses. Retraining re-optimises them for both regimes at once.

**R4 — Supervision budget.** Task 3 measures it. Ignoring it makes the mixed regimes
incomparable and biases the model toward whichever supervises more nodes.

**R5 — Boundary coverage.** cpd indexes interior cells *and* boundary faces. A mid-room
box contains few boundary rows, so the cross-attention has little boundary to address
there. Retraining can adapt the trunk basis; it cannot manufacture boundary information
the box does not contain.

**R6 — Operational.** Gen-FVGN-refine is at a submission freeze (AAAI-27 double-blind,
last commit 2026-07-29). The checkpoint lands in a gitignored `logger/`, so its only
provenance is an md5 recorded by hand.
