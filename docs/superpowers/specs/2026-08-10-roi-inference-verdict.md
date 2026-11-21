# ROI-as-simulation-unit — the inference-side verdict

Companion to `2026-08-09-roi-as-simulation-unit.md`, which ranked "contiguous-block
sampling is out-of-distribution" as risk F.1 and said no engineering fixes it. Two
engineering attempts have now been measured. This records which one survives, because the
distinction is easy to lose and expensive to rediscover.

The question the demo actually needs answered: **can inference be restricted to the box the
user drags, so that drag-and-sim costs a fraction of a whole-domain pass?**

## Setup common to every number below

Box `13.489,7.514,0,17.511,11.537,5`; the identical 9,636 interior cells scored in every
arm; nRMSE normalised by `meta.json dataset_statistics.std`. Bands are the model's own:
PASS ≤ 0.020, FAIL ≥ 0.077342 (`dspread_overall`). Raw dumps in
`SCANO/GTC_A3_Numerical/scripts/slice_basis_results/`.

## What fails, and why it is not the boundary stream

Restricting the pivot pool to the box costs **+0.405 nRMSE**, 5.2× the fail threshold, with
a T bias of 1.03 K against a 0.94 K signal. Adversarial re-examination could not refute it:
the boundary stream is byte-identical in every arm (`kv_sum=62.739301`), and a whole-room
box control reproduces stock `draw_counts` bit-for-bit.

So the two-stream design is not the problem. The complete boundary is injected regardless
of sampling and it is load-bearing — zeroing it moves the whole-domain error 0.204 → 2.27.
The damage is on the **query** side: the pivot cloud's spatial extent collapses the slice
basis, occupancy entropy 2.694 → 2.285 nats, 14.80 → 9.83 effective slices of 32.

### Newly localised

The collapse is **not** at the cross-attention Q. Injecting a whole-domain basis at that one
site recovers nothing (+0.4107, marginally worse than no injection), and its entropy barely
moves under an ROI (3.4462 → 3.4453). All of the effect is at the **7 trunk
`AttnProcessor` pooling sites**, and it is depth-dependent: layer 0 is identical in every
arm (3.4652 vs 3.4655), the gap opens with depth, worst at layer 4 (2.8256 → 1.6120).

The boundary K/V site is sampling-independent to the byte (occupancy 2.2698 in both the
whole-domain and ROI arms) and moves only when the operating point moves — an independent
confirmation that the boundary really is present in every arm.

## Attempt 1 — reuse a cached whole-domain slice basis. Circular. Rejected.

Capture the pooled slice token at each site during a whole-domain pass, substitute it into
a later ROI-only pass. Only the pooled token `[B,H,G,D]` is substituted; `slice_weights`
and the deslice stay the ROI's own, because a cached weight matrix has the wrong node axis
and the weights are what route the answer back to *these* cells.

Same operating point:

| arm | nRMSE_box | Δ vs whole-domain | band |
|---|---|---|---|
| whole-domain (ref) | 0.212453 | — | |
| ROI-only | 0.617546 | +0.405093 | FAIL |
| ROI + basis, all 8 sites | 0.216324 | +0.003870 | PASS |
| ROI + basis, 7 trunk sites | 0.217300 | +0.004847 | PASS |
| ROI + basis, cross-attention Q only | 0.623190 | +0.410736 | FAIL |

99.0% of the penalty recovered — from a basis pooled by the very pass it is scored against.

Cross operating point is the test that decides. Basis captured once at case0, reused
unchanged:

| case | ROI-only | + basis(case0) | recovered | + basis(own point) | BC distance |
|---|---|---|---|---|---|
| case100 (supply 21.4 °C) | +0.487942 FAIL | +0.026195 GREY | 94.6% | +0.018126 PASS | 0.172 |
| case500 (24.0 °C) | +0.751613 FAIL | +0.170327 FAIL | 77.3% | +0.066214 GREY | 0.707 |
| case700 (14.0 °C) | +0.554829 FAIL | +0.123322 FAIL | 77.8% | +0.022139 GREY | 0.426 |

Worst cross-point delta +0.170, 2.2× the fail threshold, decaying monotonically with BC
distance. The circularity meter (boundary K/V cached-vs-own relative L2) reads 0.0028–0.0059
same-point — the bf16 quantisation floor, not zero — against 0.172 / 0.707 / 0.426
cross-point, so a same-point number cannot be quoted as a cross-point one.

At every case the case's *own*-point basis beats the transferred one. The shortfall is the
basis carrying the wrong boundary condition, not the ROI and not those cases being harder.
Obtaining the own-point basis requires exactly the whole-domain pass the ROI was meant to
replace. **The mechanism is confirmed; the fix is circular.** Per usable answer it costs one
whole-domain pass *plus* the ROI pass.

## Attempt 2 — box unioned with K room-wide anchors. Passes, honestly labelled.

| K | Δ vs whole-domain | band | domain touched |
|---|---|---|---|
| 0 | +0.4060 | FAIL | 3.1% |
| 30000 | +0.0054 | PASS | 42.6% |
| **45000** | **−0.0029** | **PASS** | **54.7%** |
| 60000 | +0.0215 | GREY | 63.5% |

K is not monotone; do not extrapolate upward. K=45000 also restores the occupancy entropy
essentially completely — 2.7009 against the whole-domain 2.6944, and at the worst layer
2.8354 against 2.8256 — whereas basis injection buys back only about half of it (2.4585).
The arm that genuinely un-collapses the basis is the arm that passes without circularity.

**It does not simulate only the ROI.** It touches 54.7% of the domain. What it saves is
compute, because the stock path spends 51 redundant scattered draws where one
distribution-matched draw suffices. Shipped behind an explicit opt-in checkbox, and the log
line says so.

## Cost

`node-forwards` = Σ per-cell `draw_counts`, measured, not estimated.

| arm | draws | node-forwards | domain touched | GPU forward | inference |
|---|---|---|---|---|---|
| whole-domain, 51 draws | 51 | 3,330,971 | 100.0% | 3706.2 ms | 14.77 s |
| ROI-only *(fails accuracy)* | 1 | 13,967 | 3.1% | 149.9 ms | 1.11 s |
| ROI + injection, 8 sites *(circular)* | 1 | 13,967 | 3.1% | 189.6 ms | 1.22 s |
| **K45000 box+anchor** | 1 | 244,499 | 54.7% | 446.8 ms | 1.76 s |
| capture run (the prerequisite injection needs) | 51 | 3,330,971 | 100.0% | 3853.8 ms | 14.33 s |

K45000 is 8.4× end-to-end with no prerequisite. Injection's 12–15× is only cheap if the
14.3 s capture amortises over many later ROI passes, and it does not amortise across
operating points — the only axis a drag-and-sim user moves along.

## Standing conclusion

Requirement 1 as originally stated — *only* simulate the ROI — is not reachable by
inference-side engineering on this checkpoint. Two independent attempts have been measured
and one of them is circular. What remains:

- **Accept K45000** and label it as what it is: faster, not ROI-local. Shipped (`6a6509c`).
- **Retrain** with spatially restricted pivot pools in the training distribution, which is
  the only route that makes a contiguous box in-distribution. Not started.

Requirements 2, 3.1, 3.2 and 3.3 are unaffected — the ROI query, the ROI-only export, the
exactly-once fill bookkeeping and the OpenFOAM clip path stand on their own and are shipped.

## Instrumentation left in the tree

`src/NNmodels/slice_basis.py` plus +70 lines across `GraphTransolver.py`,
`PatchCrossAttentionEncoder.py`, `importer.py` and `runner.py`, all behind one default-off
tri-state (`SCANO_BASIS_CAPTURE` / `SCANO_BASIS_INJECT` / `SCANO_BASIS_ENTROPY`).

A whole-run bitwise diff cannot prove the default path untouched — re-running a stock arm
reproduces `target`/`draw_counts`/`covered`/`is_boundary` bitwise but `prediction` moves by
up to 1.6, pre-existing non-determinism from `torch_scatter.scatter_add` atomics in the 7
untouched GN blocks. `scripts/prove_default_path_untouched.py` proves it in-process against
the pre-patch source instead: `_graph_forward_dense` (the production path) and
`PatchCrossAttentionEncoder.forward` are **bitwise identical, max|d| = 0.000e+00**, the
scatter branch is inside its own atomics noise, and end-to-end on the real 301-tensor
checkpoint the patch is statistically indistinguishable from re-running. 12/12 there, 31/31
in `scripts/test_slice_basis.py`, both re-run independently of the agent that wrote them.
