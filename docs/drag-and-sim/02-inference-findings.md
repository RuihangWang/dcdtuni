# Inference findings — why "only simulate the ROI" does not work, and what does

Everything here is reproducible from `SCANO/GTC_A3_Numerical/scripts/slice_basis_results/`
(committed) and `logger/roi_experiment/`, `logger/slice_basis_experiment/` (gitignored raw
dumps). The tables below were re-derived independently of the agents that produced them.

## The setup, so the numbers mean something

The GTC A3 case has **447,205 cpd rows = 344,629 interior cells + 102,576 boundary faces**
(verified against `case0.h5`: `cell|centroid` is 344,629, `cpd|centroid` is 447,205). The
identity is arithmetic, not a lookup — cell *i* → cpd *i*; boundary face *f* →
cpd `nCells + (f − nInternalFaces)`.

SCANO does not predict all of it in one forward. Each **draw** samples ρ·N **pivots** —
the cells it is asked to answer for — and drags their k-hop neighbourhood along as context.
Stock inference is ρ = 0.02 → 8,944 pivots per draw × **51 draws** to cover the domain,
then TTA-averages.

The property that breaks everything below: training and inference draws are **permutation
slices over all cells**, so every pivot set the model has ever seen is a maximally scattered
cloud spanning the whole room. A contiguous box is out of distribution by construction.

**Thresholds** are the model's own draw-to-draw disagreement, not chosen: PASS ≤ 0.020,
FAIL ≥ 0.077342 (`dspread_overall`; the S arm logs 0.077320 on its own run). nRMSE is
normalised per field by `meta.json dataset_statistics.std`. Everything is scored on the
**identical 9,636 in-box interior cells**.

## Finding 1 — restricting the pivots to a box fails, and it is not the boundary's fault

+0.405 nRMSE, 5.2× the fail threshold. T bias 1.03 K against the 0.94 K signal the demo
exists to show.

The two-stream design is exonerated, three independent ways:

- The complete boundary — 856 patches / 102,576 rows — is injected regardless of sampling.
  `kv_sum=62.739301` appears in 54 arm-log lines with **zero** variation.
- It is load-bearing: zeroing it moves the whole-domain error 0.204 → 2.27.
- The boundary pooling site's occupancy entropy is 2.2698 in both the whole-domain and the
  ROI-only arm (equal to 5 significant figures) and moves only when the *operating point*
  moves.

The damage is on the query side: the pivot cloud's spatial extent collapses the slice basis.
Occupancy entropy 2.6944 → 2.2854 nats, i.e. **14.80 → 9.83 effective slices of 32**.

### Extent is the variable — not budget, not draw count

| arm | what it changes | Δ | scored on |
|---|---|---|---|
| C1 — spatial order, 6 repeats | whole-domain pool, 51 draws, every cell covered ~9.5×; only each draw's *geometry* becomes compact | **+0.3909** | 9,636 |
| P — same budget and draws, confined to the ROI | geometry compact | +0.3910 | 9,636 |
| X — scattered pool, budget 1,748 × 7 draws | budget cut, geometry unchanged | +0.0176 | **2,356** |

Full coverage does not save it. This is not a knob to tune.

**Arm X is not comparable to the others and must not be read as if it were.** Its pool is a
*scattered* set of the same size as the box, so it covers only **24.4% of the in-box cells**
(2,356 of 9,636). Its Δ is computed on those, and on that same subset the whole-domain
baseline scores 0.2125 against X's 0.2310. Scoring X over all 9,636 gives 0.114 — *better*
than the baseline — because the 7,280 uncovered cells carry `prediction = target = 0` and
contribute exactly zero error. That number is meaningless; do not quote it.

Read correctly, arm X says something useful and narrow: **a scattered draw of only 1,748
pivots produces PASS-quality predictions for the cells it does predict.** A good slice basis
does not need a large draw — it needs a scattered one. That is the premise of the two-stage
proposal in `03-retraining-route.md`.

### Newly localised: the collapse is in the trunk, not the cross-attention Q

Injecting a whole-domain basis at one site at a time:

| injected at | nRMSE_box | Δ | band |
|---|---|---|---|
| all 8 sites | 0.216324 | +0.003870 | PASS |
| the 7 trunk `AttnProcessor` sites | 0.217300 | +0.004847 | PASS |
| the cross-attention Q only | 0.623190 | **+0.410736** | FAIL |

The named cross-attention Q site recovers **nothing** — marginally worse than no injection —
and its entropy barely moves under an ROI (3.4462 → 3.4453). All of the effect is at
`GraphTransolver.py`'s 7 trunk pooling sites, and it is depth-dependent: layer 0 is the same
in every arm (3.4652 vs 3.4655), the gap opens with depth, worst at layer 4
(**2.8256 → 1.6120**).

## Finding 2 — reusing a cached whole-domain basis works, and is circular

Capture the pooled slice token during a whole-domain pass, substitute it into a later
ROI-only pass. Only the pooled token `[B,H,G,D]` is substituted; `slice_weights` and the
deslice stay the ROI's own, because a cached weight matrix has the wrong node axis and the
weights are what route the answer back to *these* cells.

Same operating point: +0.405 → +0.004, 99.0% of the penalty recovered.

Cross operating point — basis captured once at case0, reused unchanged:

| case | ROI-only | + basis(case0) | recovered | + basis(own point) | circularity meter |
|---|---|---|---|---|---|
| case100 (supply 21.4 °C) | +0.487942 FAIL | +0.026195 GREY | 94.6% | +0.018126 PASS | 0.172 |
| case500 (24.0 °C) | +0.751613 FAIL | **+0.170327 FAIL** | 77.3% | +0.066214 GREY | 0.707 |
| case700 (14.0 °C) | +0.554829 FAIL | **+0.123322 FAIL** | 77.8% | +0.022139 GREY | 0.426 |

Worst +0.170, 2.2× the fail threshold, decaying monotonically with BC distance. The
circularity meter — boundary K/V cached-vs-own relative L2 — reads 0.0028–0.0059 same-point
(the bf16 quantisation floor, not zero) against 0.172/0.707/0.426 cross-point, so a
same-point number cannot be quoted as a cross-point one.

At every case the case's **own**-point basis beats the transferred one. The shortfall is the
basis carrying the wrong boundary condition — not the ROI, not the cell set, not those cases
being harder. Obtaining the own-point basis requires exactly the whole-domain pass the ROI
was meant to replace.

**Rejected as a feature. Kept as instrumentation** (`SCANO_BASIS_CAPTURE` /
`SCANO_BASIS_INJECT` / `SCANO_BASIS_ENTROPY`, all default-off, production path proven
bitwise identical).

## Finding 3 — box + K anchors passes, non-circularly

Union the box's pivots with K anchors drawn at random from the whole domain. Re-scored
independently across all 14 arms:

| arm | nRMSE_box | Δ | band |
|---|---|---|---|
| S — whole domain, 51 draws | 0.212453 | ref | |
| K=0 (box only) | 0.618431 | +0.405978 | FAIL |
| K=1748 | 0.414731 | +0.202278 | FAIL |
| K=4000 | 0.349785 | +0.137332 | FAIL |
| K=8944 | 0.273543 | +0.061089 | GREY |
| K=12000 | 0.262055 | +0.049601 | GREY |
| K=16000 | 0.250286 | +0.037832 | GREY |
| K=20000 | 0.248257 | +0.035804 | GREY |
| K=30000 | 0.217815 | +0.005361 | PASS |
| **K=45000** | **0.209532** | **−0.002922** | **PASS** |
| K=60000 | 0.233905 | +0.021452 | GREY |
| T=4000 (stratified) | 0.349281 | +0.136828 | FAIL |
| T=8944 (stratified) | 0.322634 | +0.110181 | FAIL |
| T=16000 (stratified) | 0.292784 | +0.080331 | FAIL |
| T=30000 (stratified) | 0.251192 | +0.038738 | GREY |

Three things this table says:

1. **K is not monotone.** K=60000 is worse than K=45000 *and* worse than K=30000. Do not
   extrapolate upward.
2. **Spread is not the active ingredient — matching the training distribution is.**
   Stratified anchors (one per occupied grid cell, spread by construction) are worse at
   every K above noise. The mesh is refined near the racks, so a spatially uniform sprinkle
   undersamples the dense regions, while training draws are a permutation over *cells* and
   therefore density-proportional. (At K=4000 the two are within 5.0e-4 — see
   `CORRECTIONS.md` §2.)
3. **Δ is negative at K=45000.** In the box it beats the 51-draw baseline, because there
   every cell is a pivot once, whereas the baseline covers each cell about 9.4 times with
   scattered draws whose pivots are elsewhere.

Entropy corroborates independently: K=45000 restores occupancy to 2.7009 against the
whole-domain 2.6944 (worst layer 2.8354 vs 2.8256), while basis injection buys back only
42.3%. The arm that genuinely un-collapses the basis is the one that passes without
circularity.

## Cost

`node-forwards` = Σ per-cell `draw_counts`, measured.

| arm | draws | pivots | node-forwards | domain touched | GPU forward | inference |
|---|---|---|---|---|---|---|
| whole domain, 51 draws | 51 | 8,944 × 51 | 3,330,971 | 100.0% | 3706.2 ms | 14.77 s |
| ROI-only *(fails)* | 1 | 12,108 | 13,967 | 3.1% | 149.9 ms | 1.11 s |
| ROI + injection *(circular)* | 1 | 12,108 | 13,967 | 3.1% | 189.6 ms | 1.22 s |
| **K45000** | 1 | 55,886 | 244,499 | 54.7% | 446.8 ms | 1.76 s |
| capture run (injection's prerequisite) | 51 | — | 3,330,971 | 100.0% | 3853.8 ms | 14.33 s |

The pool→coverage expansion factors are the whole story of why anchors are not cheap. A
contiguous box has almost all of its k-hop ball already inside itself — 12,108 → 13,967,
only the surface shell is new. An isolated anchor's ball is almost entirely new — 55,886 →
244,499, about 4.4×. The same property is why anchors *work*.

Two percentages that look inconsistent and are not: **7.18%** of node-forwards uses interior
cells as the denominator, **7.34%** uses all cpd rows. Likewise **2.7%** is the pool and
**3.1%** is the coverage. Always qualify.

## Standing conclusion

Requirement 1 as stated is not reachable by inference-side engineering on this checkpoint.
What remains:

- **Accept K45000**, labelled as what it is. Shipped, opt-in, off by default.
- **Retrain** with spatially restricted pivot pools. → `03-retraining-route.md`.
