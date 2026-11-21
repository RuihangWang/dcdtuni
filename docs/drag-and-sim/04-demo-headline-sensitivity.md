# The demo's headline number, and why — a data-coverage failure

The demo's ACU supply-temperature slider renders **−0.94 K**. It should render several kelvin.
This file went through three rounds of diagnosis, of which **the first two were wrong**; both
are kept below the verdict, because the corrections are the useful part.

Everything here was re-derived independently of the agents that produced it.

## The number, and the three-tier shortfall

Dragging case0's six ACUs from 18.6–22.8 °C down to 14.0 °C, reproduced three independent ways
(absolute setpoints, splicing a real 14 °C supply vector, the demo's own pipeline):
**−0.9327 to −0.9377 K interior**, 93–99% of cells moving.

| what | d⟨T⟩/dT_supply over that move |
|---|---|
| converged physics — premises verified twice | **1.000** |
| the model's own training targets — unconverged, IC-anchored | **~0.86** |
| **the model** | **0.151** |

**5.7× short of its own data; 6.6× short of physics.** The earlier framing — "wrong by ≥4.9×
against an exact bound" — conflated a data-fidelity failure with a model-fidelity failure.

### The bound, and what "exact" is worth

For a **uniform** supply shift the converged field moves by exactly δ. Premises verified
independently twice from the case files: `buoyantBoussinesqSimpleFoam`; `nu`, `beta`, `TRef`,
`Pr`, `Prt` all constant; `kEpsilon` incompressible; **no** `radiationProperties`, **no**
`fvOptions`; `0/T` = 335 zeroGradient / 323 exprFixedValue / 2 cyclic and nothing else — no
fixed-temperature wall; supplies `max(0,t2)` with `t2` absolute; 317 server outlets
`tSink_<inlet> + ΔT`; `0/U` volumetric flow; wall functions carry no absolute T.

So Σᵢ gᵢ = 1, and the demo's non-uniform move is bounded by its per-unit deltas,
**[−8.762, −4.589] K**. Two caveats now attach. The convex-combination step needs a comparison
principle that buoyancy coupling formally breaks — weak coupling, probably fine, but "exact" is
not earned for the *non-uniform* case. And the **targets do not realise gain 1**: six free
per-unit supply coefficients over the demo's 552-case topology sum to **0.9257 ± 0.0362**, or
**0.8526 ± 0.0193** with load controlled. Point estimate for the demo's move from the data
itself: **−5.29 K**.

## The verdict: a 3 K hole in the training data, on the exact axis the slider moves

Supply setpoints over the 552 cases of the demo's own topology, 3,312 unit values:

```
exactly 14.000 °C : 290          (14,15] : 4      (18,19] : 500
                                 (15,16] : 4      (19,20] : 502
                                 (16,17] : 4      (20,21] : 499
                                 (17,18] : 4      (21,22] : 504
                                                  (22,23] : 500
                                                  (23,24] : 501
```

Case level: **51 cases below 16 °C, ZERO in [15.5, 18.5), 501 at or above 18.5 °C.**
**case0 sits at 20.16 °C and the slider drags it to 14.0 °C — straight across the void.**

So the model learned a **two-regime discriminator with a step in the gap**, not a sensitivity.
Incremental gains, reconstructed from the raw dumps:

| base | 14→16 | 16→18 | 18→20 | 20→22 | 22→24 |
|---|---|---|---|---|---|
| case0 (base 20.16 °C) | +0.279 | +0.181 | +0.007 | +0.001 | +0.005 |
| case700 (base 14.0 °C) | +0.272 | **+2.140** | +0.091 | +0.022 | −0.016 |

Both are dead above 18 °C and carry all their sensitivity across 14–18 °C. Summing case0's row
over the slider's actual travel gives **≈0.93 K** — the rendered number, fully accounted for.

Inside the dominant cluster the axis is barely identifiable anyway: each unit is drawn
independently ~U(18,24), so the flow-weighted mean has sd only **0.734 K**, and the
supply-driven level signal sits at **SNR ≈ 1.6** against the target's own BC-unexplained level
variance. The model was never shown the within-cluster gradient at usable SNR, and never shown
the gap at all.

**This is a data-design failure.** Not the objective, not conditioning strength, not ROI.

## Two further findings that constrain any fix

**61% of the level response lives in a channel interaction.** Transplanting a donor case's
boundary rows into case0, with an exact positive control (replacing all three channels
reproduces the donor's own run to **0.0087 K RMS** = the run-to-run floor):

| rows replaced | Δlevel | share |
|---|---|---|
| all channels, 102,576 rows | −6.2547 K | **1.000** |
| T channel only | −0.9309 | 0.149 |
| U + pressure only | −1.5073 | 0.241 |
| T on the 6 ACU supply patches only | −0.9328 | 0.149 |
| **T on all 317 server-outlet patches** | **−0.0009** | **0.000** |

0.149 + 0.241 = 0.39 alone, 1.000 jointly. **A slider that moves only the temperature channel
cannot reach the response**, and the server-outlet temperature tokens — 93% of the T-channel's
K/V motion — are ignored outright.

**The targets are not converged.** Closing the boundary energy balance of `500/T` over 138
cases: residual/server-power mean **+0.196**, i.e. a mixed-return temperature error of mean
**+1.80 K**, with 77.5% of cases beyond 1 K. All 753 cases have time dirs exactly {0, 1, 500} —
every run stopped on `endTime`, none met its own `residualControl`. And the residual is
**−0.79 correlated with supply temperature**, because every case starts from the same
`internalField uniform 297.15`: the colder the supply, the further it still had to travel.
Controlling for it moves the truth's own supply coefficient 0.8526 → 0.9138.

## The two diagnoses this replaces

**Round 1 — "the model compresses the level ~4× and cannot express it; fix the objective."**
Wrong. The 0.249 slope is real and out-of-sample — it reproduces at 0.2452 on the train half
and 0.2501 on the held-out half with 0.0138 K scatter — but it is the *local* slope inside the
step's flat region, measured in a 1.24 K window. Over a held-out 8 K range the same model gives
slope **1.0010**, corr 0.9950, level error sd 0.35 K. The model expresses the level fine, and
"no conditioning change will help" was unsupported.

**Round 2 — "it is a window artifact / attenuation."** Also wrong, and wrong statistically: OLS
of `pred` on `true` with error in `pred` is unbiased; attenuation needs noise in the regressor.
The 0.249 is a tight systematic fact, not noise.

They are two measurements of a **step function taken from opposite sides of the step**.

Also withdrawn: the uniform-vs-non-uniform gain matrix (confounded with topology, and with a
supply temperature that is constant in training); "the level is worth 0.34% of the variance so
it is ignored"; and the claim that `T − (T_supply + ΔT_load)` removes 84% of the level spread —
that figure is for the *fitted* two-coefficient regression, while the coefficient-1
reparameterisation gives 69.2%.

One process note, since it caused a wrong round: the summary file the round-1 table was read
out of (`j2_analyze.json`) was written **before** the fine ladder and every case500 arm existed,
and contains only case0. Half the evidence was on disk and unread.

## What to do, in order

1. **Stop presenting −0.94 K as a physical result.** This survives every round.
2. **Settle whether the labels are sound** — the cheapest experiment by far, and it gates
   everything else. Restart case0 from its own `500/` and iterate to actual residual
   convergence; optionally repeat with all six supplies shifted −6 K, which must move the
   converged field by exactly −6.000 K. If the level moves the ~3 K the energy residual
   predicts, **every target in the dataset is wrong by a supply-correlated amount larger than
   the effect under dispute**, and the reparameterisation, any conditioning augmentation and
   the ROI retrain are all being fitted to corrupted labels — regenerate first. If it barely
   moves, the 0.86 needs another explanation and the analytic bound stands as the yardstick.
3. **Fill the void** — 12 cases across 15.5–18.5 °C at the existing 500 iterations, purely to
   measure whether the *truth's* supply gain in the gap matches the cluster's. Tests the
   coverage hypothesis without touching the model.
4. Only then choose between data regeneration, coverage augmentation, and any model change.
   The output reparameterisation is **not** currently supported: it would force coefficient 1
   onto targets whose realised coefficient is ~0.86 for a numerical reason, and it addresses
   neither the step, nor the void, nor the channel interaction.

## Still unsupported

- Anything about the 560-patch topology — its mass balance does not close (net −38 kg/s), so it
  is not adiabatic-closed and none of the above transfers.
- The non-uniform form of the bound.
- Whether the level's ~0.33 K BC-unexplained residual is entirely convergence noise: the
  residual proxy and supply temperature are collinear at −0.79, so the decomposition is not
  clean.
