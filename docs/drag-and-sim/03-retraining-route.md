# The retraining route — the only remaining path to a real Drag & Sim

`02-inference-findings.md` closes the inference-side options. This is what is left, grounded
in the training code rather than in hope. **Nothing here has been run.** It is a plan with
measured inputs, not a result.

All training work happens in `/mnt/lv/home/litianyu/mycode/Gen-FVGN-refine`. The bundle
under `cfd_demo/SCANO/GTC_A3_Numerical/` is an inference subset with no training entry
point, and its `HomoGraph_dataset.py` carries ~311 additive lines of `SCANO_PIVOT_*`
machinery that the training repo does not have at all.

## VERDICT — the probe does not ship, and the whole basis-substitution family is closed

Two runs on 2026-08-10: a case0 sweep, then a cross-operating-point decision run with a
pre-registered rule and an adversarial re-run. 41 arms re-scored plus 26 new ones. Every
accuracy number below was re-derived independently of the agents that produced it.

**The case0 result did not survive leaving case0.** Study box, each point against its own
whole-domain reference, probe = 4×500 with four disjoint replicates:

| point | reference | ROI-only | probe (mean ± sd) | frozen basis | K45000 | **own-point oracle basis** |
|---|---|---|---|---|---|---|
| case0 | 0.2125 | +0.4051 | **−0.0006 ± 0.0099** PASS | +0.0039 | −0.0029 | — |
| case100 | 0.1730 | +0.4879 | +0.0272 ± 0.0076 GREY | +0.0262 | +0.0285 | +0.0181 PASS |
| case500 | 0.3284 | +0.7516 | +0.0785 ± 0.0379 GREY/FAIL | +0.1703 | +0.0667 | +0.0662 GREY |
| case700 | 0.0568 | +0.5548 | +0.0489 ± 0.0208 GREY | +0.1233 | +0.0334 | +0.0221 GREY |

**The bound that closes the family is the last column.** At case500 and case700 the *oracle* —
the whole-domain 51-draw basis captured at the very point it is used, 128× the probe's cost —
is itself only GREY. No basis-substitution scheme, free or probed or oracle, reaches PASS off
case0 on this checkpoint. This is not a probe-sizing problem and no cheaper capture fixes it.

**The probe loses to what already ships.** It beats the free frozen basis at 3 of 4 points
(and an adversarial re-run against the *whole* frozen family — every foreign basis plus a
leave-one-out mean — leaves that ordering intact), but it never beats K45000 anywhere off
case0, it costs 4 extra forwards, and end-to-end it is **1.57× slower** (947 ms vs 605 ms).
Its one concrete advantage: on the demo box at case0 it adds +0.06 K of T bias where K45000
adds +0.29 K.

**And the arm it loses to is worse than we thought** — see `CORRECTIONS.md` §3, reopened:
K45000 PASSes at 1 of 6 (box × point) cells and FAILs on the demo box at case500.

### Cost, now measured rather than fitted

Per-forward, warm, `cuda.synchronize` both sides, 25 repeats, first discarded:

| forward | pivots | nodes | warm ms |
|---|---|---|---|
| 500-pivot scattered probe draw | 500 | 4,064 | 12.51 ± 0.09 |
| ROI box draw | 12,108 | 13,967 | 20.33 ± 0.02 |
| K45000 draw | 55,886 | 244,499 | 311.16 ± 0.11 |
| stock whole-domain draw | 8,944 | 65,313 | 70.43 ± 0.45 |

Two structural facts worth keeping even though the probe is dead:

- **The forward is a plateau then a line**: flat at 12.5 ms up to ~8,000 nodes (launch-bound),
  then 1.24 µs/node. The earlier single-line fit was the wrong shape.
- **The full 856-patch / 102,576-row boundary cross-attention costs 4.5 ms**, not the 0.4 s
  the doc previously implied. The 0.40 s "naked ROI draw" was a *first* forward — every
  single-draw arm in the prior study reported exactly one forward, i.e. a warm-up.
- CPU per draw (~96–120 ms, flat in node count) is **7.7× the GPU per draw**. Paying ~100 ms
  of CPU to save ~75 ms of GPU per extra draw is why the 3.18× GPU win becomes a 1.57×
  end-to-end loss. That is an implementation property (`tta_num_workers=0`, uncached
  per-item read), not a property of the model — but it is what exists.

### Caveats carried forward

- The decision rule was pre-registered and applied as written, but the bar it set was already
  known unreachable at 2 of 4 points when it was written (the oracle numbers were in the
  pre-registration). The verdict is right; its information came from the numbers, not the rule.
- The absolute ±0.020 band is not like-for-like across points where the model's own accuracy
  varies 5.8×. Relative degradation of the shipping arm on the demo box: +4.1% / +18.8% /
  **+82.7%** at case0 / case500 / case700.
- The demo renders a *difference* between operating points; every T-bias number here is
  per-point and would partly cancel in that difference. **The differenced quantity has never
  been measured** — that, not another mitigation sweep, is the measurement to make before
  anyone claims the demo's −0.94 K headline is or is not damaged.

---

## Superseded: the probe as first reported (kept for the reasoning)

Sweep plus an adversarial re-run, both on GPU 1, artifacts under `logger/basis_probe/` and
`logger/basis_probe_verify/` (gitignored). Everything below was re-scored independently of
the agents that produced it.

**Draw count is the variable, not probe size.** At a *fixed* pivot budget, splitting the
probe into more draws and averaging the basis is what works:

| probe pivots | 1 draw | split and averaged |
|---|---|---|
| 1,000 | +0.0105 | 2×500: +0.0094 |
| 2,000 | +0.0257 GREY | 4×500: **−0.0030** |
| 4,000 | +0.0293 GREY | 8×500: **+0.0003**, 4×1000: +0.0011, 2×2000: +0.0153 |
| 8,000 | — | 16×500: **−0.0076** |

Single draws are unstable — four disjoint 1×500 replicates span −0.0007 … +0.0485, two GREY
and two PASS. Four disjoint **4×500** replicates are **4/4 PASS, mean −0.0006 ± 0.0098**.
That is the honest headline: *a 4-draw × 500-pivot probe puts the ROI in PASS, replicated*.
It does **not** support "beats the whole-domain reference" — that gap is inside the noise.

**Controls.** Unarmed reproduces C2 (0.6178 vs 0.6175). Self-basis is a near no-op (+0.0024).
A G-permuted basis FAILS (+0.1447) — but it still removes 64% of the penalty, and its
per-field breakdown is the uncomfortable part:

| arm | u | v | w | p | **T** |
|---|---|---|---|---|---|
| whole-domain | 0.2801 | 0.3657 | 0.1926 | 0.0051 | **0.2186** |
| ROI-only | 0.6802 | 0.8931 | 0.6249 | 0.0960 | 0.7936 |
| **shuffled basis** | 0.5016 | 0.6602 | 0.3893 | 0.0174 | **0.2174** |
| 4×500 probe | 0.2898 | 0.3550 | 0.1762 | 0.0093 | 0.2172 |

**A shuffled basis rescues temperature completely.** Slice-index correspondence is what the
*velocity* fields need; T does not need it at all. The demo's headline number is a
temperature delta, so for the thing the demo actually shows, this result is at its weakest as
evidence that real information is being transferred.

### The cost claim is not settled, and two plausible numbers are both wrong

- **"8× cheaper than K45000"** counts node-forwards. Wrong unit: the naked ROI draw (13,967
  node-forwards) takes 0.40 s while K45000 (244,499) takes 0.45 s. Every forward pays the
  full 856-patch boundary cross-attention regardless of pivot count, so node-forwards
  flatters many-small-draws.
- **"only 1.18×, and ~2× slower end-to-end"** sums two separate process invocations, each
  paying the harness's per-run overhead. A production two-stage runs in one process and pays
  it once.

Decomposing the E-series (1/2/4/8/16 draws of 500 → 0.1531/0.1742/0.2217/0.2948/0.4569 s,
residuals ≤ 6 ms) gives **135 ms per-run overhead + 20.1 ms per 500-pivot draw**. On that
basis:

| | forward work |
|---|---|
| whole domain, 51 draws | 3560 ms |
| K45000 (ships) | 311 ms |
| **projected single-process 4×500 probe + ROI** | **~103 ms** |

≈ 3× faster than what ships and ≈ 35× faster than the baseline, at 1.05 GiB peak against
K45000's 5.11 GiB. **That is a projection from a fit, not a measurement.** The measurement is
to run both stages in one process and time it.

### The comparison the sweep never made

Injecting a **frozen basis from a different operating point** — zero probe cost — into the
case0 ROI:

| foreign basis | Δ | band |
|---|---|---|
| case100 | +0.0108 | **PASS** |
| case500 | +0.0806 | FAIL |
| case700 | +0.0966 | FAIL |

A stale basis passes at one point and fails at two, non-monotonically in the circularity
meter. So the probe has **not** been shown to beat a free frozen basis. The probe should win
by being reliable across operating points — but the sweep only ran at case0, so that is
asserted, not measured.

### Scope, stated plainly

The probe is a **spatially global** scattered draw: it reaches 500/500 voxels of a 10×10×5
grid over the room and needs the full mesh and all BCs. "6.59% of the domain" is a *cell*
count, not reach. This is not ROI-local inference; it is ROI-local *values* plus a thin
whole-room sampling.

### Next experiment — not a retrain

1. **Probe vs frozen across operating points.** Run the 4×500 probe at case100/500/700 and
   compare against the frozen basis at each. This is the decisive comparison and it is cheap.
2. **Single-process timing.** Settle the cost question with a measurement instead of a fit.
3. **A box a user would drag**, and a second case — the same gap that `CORRECTIONS.md` §3
   opens against K45000 applies here unchanged.

Only if those hold does the retrain question become "can training remove the need for the
4-draw reduction", which is much smaller than the 159 GPU-h plan below.

---

## The original proposal, kept for the reasoning that produced it

**Two-stage inference — a cheap scattered *basis probe*, then the ROI draw with that basis
injected.**

The circularity verdict in `02` was specific: reusing a basis is circular *because obtaining
it required the whole-domain pass the ROI was meant to replace* — 51 draws, 3,330,971
node-forwards. That argument does not apply to a basis obtained from a **small scattered
draw at the current operating point**. Three measured facts line up:

1. Injecting a correct basis into an ROI-only pass recovers **99.0%** of the penalty
   (+0.405 → +0.004). The basis is sufficient.
2. Arm X shows a scattered draw of **1,748 pivots** — about 12,500 node-forwards, **0.37%**
   of the baseline — already produces PASS-quality predictions for the cells it predicts
   (0.2310 against the baseline's 0.2125 on the same 2,356 cells). A good basis needs
   scatteredness, not size.
3. The cross-operating-point failure was a *stale* basis, i.e. the wrong boundary condition.
   A probe run now, at this operating point, is not stale.

Projected cost, from the measured cost table:

| | node-forwards | domain touched | GPU forward |
|---|---|---|---|
| whole domain, 51 draws | 3,330,971 | 100% | 3706 ms |
| K45000 (what ships) | 244,499 | 54.7% | 447 ms |
| **probe (B≈1,748) + ROI draw** | **~26,500** | **~6%** | **~165 ms** |

That would be the first configuration that is genuinely ROI-local *and* non-circular.

**The open risk, stated before the experiment rather than after.** The injected basis that
recovered 99% was captured across 52 forwards and reduced; a single small draw gives one
noisy sample. The probe may recover only part of the penalty. That is precisely what the
sweep measures.

**It needs no code change and no training.** The harness already does capture → file →
inject as two invocations, and the capture arm's sampling is set by existing knobs:

```
# stage 1 — capture from a SMALL SCATTERED draw, not the 51-draw whole-domain run
SCANO_PIVOT_POOL=scatter  SCANO_PIVOT_BUDGET=<B>  SCANO_BASIS_CAPTURE=basis_B.pt
# stage 2 — the ROI draw, with that basis
SCANO_PIVOT_POOL=box      SCANO_PIVOT_BOX=<box>   SCANO_BASIS_INJECT=basis_B.pt
```

Sweep B ∈ {500, 1000, 2000, 4000, 8944}, score on the same 9,636 cells, and report the
circularity meter for each. Five arms at 1–2 min each. **Do this before spending 159 GPU-h.**

If it works, requirement 1 is delivered without retraining and the sections below become
optional. If it does not, the sweep still says *how much* basis quality costs, which sizes
the retrain.

---

## Why retraining is the lever

Every pivot set the model has ever seen is a maximally-scattered ρ·N sample of the whole
mesh. That is structural, not incidental:

- `runner.py` train branch reads `training_config.sample_ratio` (0.03 for this checkpoint);
  inference reads a different key, `tta_sample_ratio` (0.02).
- The train branch **never materialises a pool**. `_init_sampling` appends `None` and stores
  `(offset, stride)` instead; `_sample_from_pool` early-returns into `_sample_train_chunk`,
  which is pure modular arithmetic — `(offset + positions * stride) % num_cells` with
  `stride` coprime to `num_cells`, i.e. a full-domain permutation, tiled by epoch.
- The loss covers **every subgraph node**, not just the pivots.

So a contiguous box is out of distribution by construction, and the fix has to be in the
distribution.

## What I would actually train — sampling augmentation, not box training

Training only on boxes is the obvious move and I think it is the wrong one: it abandons the
whole-domain path the demo defaults to, and it is a strictly harder problem than the one
that needs solving.

**Each training draw becomes a random sample from a family of sampling regimes**, not a
fixed one:

| drawn per step | from |
|---|---|
| regime | scattered (prob *p*) or box ∪ anchors (prob 1−*p*) |
| box size and position | a distribution over plausible ROIs, not one fixed box |
| anchor fraction | a range, so the model sees pools from box-dominated to anchor-dominated |
| **budget** | **a range, not the constant ρ·N** |

Randomising the *budget* is the non-obvious part, and I think it is load-bearing. K is not
monotone — K=60000 is worse than both 45000 and 30000 — and the pool sizes explain why:

| arm | pivots per draw | vs the training draw (13,416) |
|---|---|---|
| training | 13,416 | 1.0× |
| K=30000 | 41,284 | 3.1× |
| K=45000 | 55,886 | 4.2× |
| K=60000 | 70,483 | **5.3×** |

Every box+anchor arm is out of distribution in draw *size* as well as extent. The
non-monotonicity is what two competing pressures look like — anchors must dominate the node
set to restore the basis, but the pool must stay near a size the model has seen. Randomising
the budget in training removes the second pressure, leaving anchors to handle only the first.
That is a testable prediction, not a certainty: if it holds, the post-retrain anchor budget
should become small and should scale with ROI size instead of being a constant 45,000.

*p* keeps the whole-domain path in distribution. It is the knob that decides R3.

## The minimal change

**The existing `SCANO_PIVOT_*` machinery cannot be reused at training time.** Four reasons,
each of which is a real blocker:

1. Every pool-geometry consumer lives in the non-train branch of `_init_sampling`. The train
   branch is untouched upstream code.
2. `_sample_from_pool` returns before it ever reads `self._pivot_pools`.
3. The knobs hard-refuse under DDP (`world_size > 1`), and the reference run used 6 ranks.
4. Worse than refusing — if you bypass (3), the box-selection and budget-override blocks sit
   in the per-H5 loop with **no split guard**, so a single-GPU training run would shrink the
   budget to the box size while still drawing from the whole-domain permutation. It would
   change the budget and not the geometry, and log nothing.

**Insertion point: one function plus one initialiser.**

- `_sample_train_chunk` is the single `(start, count) → cpd ids` function for training.
  Route its `positions` through a precomputed per-case spatial order and every training draw
  becomes a contiguous block — with budget, epoch tiling, DDP case sharding, `set_epoch`,
  the trainer and the loss all unchanged.
- Build that order once in the train branch of `_init_sampling`, reusing `_kd_block_order`
  (recursive median split into equal-size axis-aligned clusters). **It must be ported** —
  the training repo does not have it.
- `set_epoch` is already a pure function of `(case_idx, epoch)`, so it is the natural switch
  for alternating block partitions, or for mixing box and scattered epochs.
- The DDP guard needs a deliberate train-mode carve-out: training shards by **case**, not by
  pivot, so "a box split across ranks is no longer a box" does not apply — but that has to
  be written, not assumed.

## What a retrain costs — measured for this exact checkpoint

From the archived run that produced the demo's `best.state`:

| Item | Value |
|---|---|
| Wall clock | **95,620 s = 26.6 h** |
| Hardware | 6 × RTX 4090 24 GB |
| Epochs | 2,000 (~48 s/epoch, 113 batches) |
| Batch size | 1 per rank (effective 6) |
| k-hop | 2 |
| `sample_ratio` | 0.03 |
| Model params | 8,901,369 |
| Dataset | 677 train / 76 test cases; 753 `.h5`, **418 GB** (directory 690 GB) |

**159.4 GPU-h is arithmetic** (6 × 26.6), not a recorded measurement. The paper's GPU-hours
table is for a different dataset and sampling ratio; which row corresponds to this
checkpoint is UNVERIFIED.

The demo's checkpoint is md5-identical to that archived run. Swapping in a retrained one
needs no code change — `checkpoint_path` is relative-to-bundle and `logger/` is gitignored,
so overwriting the file is enough. A `--checkpoint` CLI flag exists but `ai_worker` never
passes it, and there is no env override.

Two things a retrained checkpoint must satisfy: the `model_configs` block in the inference
YAML must match the new architecture (it is not read from the checkpoint), and
`patch_statistics.json` must be present in the dataset dir — if it is missing the code only
**warns** and silently falls back to the dataset-wide normaliser.

## Risks, ranked

**R1 — The gap is large, not a trim.** Arm C1 keeps the whole-domain pool, all 51 draws and
~9.5× coverage, and changes only each draw's *geometry*; it still costs +0.391. This is
teaching a representation the model does not have, not tuning one it has.

**R2 — The broken thing is shared with the path that must keep working.** The 32 slice
tokens the ROI collapses are the same tokens the whole-domain path uses. Retraining on boxes
re-optimises them for both regimes at once.

**R3 — One checkpoint serving both regimes is the open question, and there is no evidence
either way.** The demo's default is whole-domain: `simRoi` defaults false and the worker
states plainly that inference still runs over the whole domain. So a box-trained checkpoint
must *also* keep whole-domain accuracy. Two bounds on the hope: cross-*case* basis transfer
already degrades at fixed regime (case0's basis on case100: GREY 0.026 vs its own 0.018);
and mixing is *expressible* — batch size 1, one independent subgraph per sample, epoch-keyed
sampler — so a curriculum alternating box and scattered draws needs only the hook above.
**Nothing measures a mixed-training model. UNVERIFIED.**

**R4 — "Retrain with the same recipe" is not like-for-like.** A scattered 13,416-pivot draw
expands to ~96.7k nodes at k-hop 2 (~14% pivot fraction); a contiguous block of the same
pivot count expands only by a surface shell (~87% pivot fraction). Since the loss covers
every subgraph node, box training at ρ=0.03 supervises roughly 6× fewer nodes per step.
Restoring the node count means raising ρ by ~6–7×, which changes memory, the effective
batch, and the 2,000-epoch schedule. **159 GPU-h is a floor, not a forecast.**

**R5 — Boundary coverage.** cpd indexes interior cells *and* boundary faces. A box in
mid-room contains few boundary rows, so the cross-attention has little boundary to address
there. Retraining can adapt the trunk basis; it cannot manufacture boundary information the
box does not contain. This is consistent with why anchor *distribution* rather than anchor
count is what heals the inference-time mitigation.

**R6 — Operational.** The training repo is at a submission freeze (AAAI-27, double-blind,
last commit 2026-07-29). Any retrain means porting code into a frozen repo and carrying a
checkpoint back into a gitignored directory, where its only provenance today is a
hand-computed md5.

## A longer shot, worth stating because the evidence points at it

**Basis distillation: predict the slice basis from the boundary stream.**

The cross-operating-point table is the argument. Recovery decays *monotonically* with BC
distance — 94.6% / 77.8% / 77.3% at distances 0.172 / 0.426 / 0.707 — and at every case the
case's own-point basis beats the transferred one. So the basis is largely a function of the
boundary condition and the geometry. The two-stream design already carries exactly that: the
complete boundary, 856 patches / 102,576 rows, injected regardless of sampling and measured
sampling-independent.

So train a small head mapping (boundary tokens, the ROI's own nodes) → the whole-domain slice
basis, supervised by the basis captured during training — which the committed
`SCANO_BASIS_CAPTURE` machinery already produces. At inference: run the head, inject, done.
No probe pass at all.

Two risks, both real. The basis may also depend on interior state the boundary does not
determine — which is the reason a CFD surrogate exists at all. And the 99% recovery figure
came from the *exact* basis; a predicted one carries error, and if recovery drops to ~90% the
result lands at roughly +0.04, i.e. GREY rather than PASS. Worth doing only after the probe
sweep, which measures how gracefully recovery degrades with basis quality.

## Suggested order, if this is taken up

0. **Run the probe sweep first** (top of this file). Half an hour of GPU, no code change. It
   can make everything below unnecessary, and if it cannot, it sizes the retrain.
1. **Port `_kd_block_order` and hook `_sample_train_chunk`,** behind a default-off flag.
   Prove the default path is bitwise unchanged in-process against the pre-patch source —
   the same proof shape as `prove_default_path_untouched.py`, which exists because a
   whole-run diff cannot settle it (`scatter_add` atomics move predictions by ~1.6).
2. **Measure the supervision change before committing GPU time.** Count nodes per step for
   box vs scattered draws at the same ρ, and pick the ρ that holds node count constant.
   R4 says this dominates the schedule.
3. **Short mixed-curriculum probe first, not a full 2,000-epoch run.** The decisive question
   is R3 — whether one checkpoint holds both regimes. A few hundred epochs answers the
   direction; the full run only matters if the direction is right.
4. **Score on both**: the study box and the whole domain, against the same thresholds, plus
   at least one box a user would actually drag (the current sweep has never done this —
   `CORRECTIONS.md` §3).
5. Only then a full retrain, with the checkpoint's provenance recorded somewhere versioned.
