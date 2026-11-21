# The training labels are unconverged — and regenerating them will not fix the demo

The experiment `04` called decisive: continue case0 past its label at iteration 500 and see
whether the level moves. It does. But the reason it moves is not the one that motivated the
run, and fixing it is not sufficient.

Two arms of 5,000 extra iterations plus an adversarial re-run (np 8, CPU only, ~26 h wall).
Numbers below re-derived from the raw trajectory independently of both agents.

## The premise is no longer assumed — it is measured

For a uniform supply shift the converged field must move by exactly δ. Continuing case0 and a
−6.000 K twin from the same state:

```
realised uniform-shift gain = 0.999980 ± 0.000683      (analytic: 1.000000)
```

over the settled window 2000–5500, n=3501. The adversary added the leg nobody had checked —
the shift must leave **U** untouched — and the velocity signature of the −6 K shift
(0.0233 m/s RMS) is *smaller than the arm's own limit-cycle noise* (0.0292 m/s). The
Boussinesq shift-covariance argument that the whole investigation rests on is now verified to
five decimals rather than argued from the case files.

## The labels drift, and by more than the effect under dispute

| iteration | ⟨T⟩ interior | shift from the label |
|---|---|---|
| 500 (the label) | 301.0289 | — |
| 750 | 300.1291 | −0.900 |
| 1000 | 299.9240 | −1.105 |
| 2000 | 299.9200 | −1.109 |
| 5500 | 299.9200 | −1.109 |

**Settled: −1.1075 K, sd 0.0026 K.** It stops moving; this is not an unfinished trend. The
change is a genuine field change, not a mean artefact: RMS 1.62 K, corr 0.985, 56.7% of cells
moving more than 1 K. And **−1.11 K exceeds the −0.94 K the demo renders**.

N=3, all negative, spread 3.5×: case0 −1.1075, case1 −0.3534, case2 −1.2382 K. **The
magnitude is per-case and not correctable by a constant.** All 753 cases have time dirs
exactly {0, 1, 500} — every run stopped on `endTime`, none on `residualControl`.

## The mechanism that motivated the run is refuted

The run was launched because the labels' boundary energy books are off. For case0 they are,
and they close on continuation: F/P_srv +0.1766 → +0.0001, a 100× reduction.

But the adversary continued a third case chosen because its label is energy-**clean**:

| case | label residual F/P | label return-temp error | settled level drift |
|---|---|---|---|
| case0 | +0.1766 | +2.066 K | −1.1075 K |
| case1 | −0.2256 | −2.612 K | −0.3534 K |
| **case2** | **+0.0027** | **+0.031 K** | **−1.2382 K** |

Opposite residual signs, same drift sign; a near-zero residual gives the **largest** drift.
**The energy balance is neither the mechanism nor a usable screening statistic.** What carries
the conclusion is the level drift itself and the 753/753 `endTime` stop.

Two of the numbers I quoted in `04` also do not reproduce under independent recomputation over
N=90 cases: residual/server-power **+0.196 → +0.0331** signed (0.107 mean absolute), return
error **+1.80 K → +0.29 K** signed (1.15 K mean absolute), fraction beyond 1 K **77.5% → 54.4%**.
Same sign and structure, wrong magnitude. And "138 cases of this topology" was wrong — it is
**552**. The supply correlation survives at −0.70.

## The decision: regenerate, but do the void first

The labels realise only **66%** of a supply move at their own iteration count — and that
number survives the harder test the first run did not attempt: a −2.0 K shift *inside* the
training cluster gives **0.6622** against the −6.0 K out-of-cluster **0.6599**, with the
gain-vs-iteration curves lying on top of each other from iteration 200 (0.47 at 400, 0.66 at
500, **still climbing**). So 0.66 is a property of `endTime 500`, not of leaving the cluster.

**But regeneration alone will not move the demo's headline.** The arithmetic:

| | |
|---|---|
| the demo renders | −0.94 K |
| its own labels' response to that move | 0.660 × 6.16 = **4.07 K** |
| so the model recovers | **23% of its own labels** |
| perfect labels, unchanged model fidelity | ≈ **1.4 K**, not several kelvin |

The **coverage void is the binding constraint** — confirmed again at case level: of 753 cases,
**zero** have a flow-weighted supply mean in [16, 18) °C, 51 sit below 16 and 504 at or above
18.5, and the slider drags case0 from 20.16 straight across it.

**Price, measured on this host** (meshes exist; this is solve-only): 0.28–0.41 s/iteration at
np 8; the level settles by ~1500–2000 iterations. At 2,500 iterations → 753 cases ≈ 150–215 h
of 8-rank time ≈ **31–43 h on 48 cores, i.e. 1.5–2 days**. Cost is not a reason to hesitate.

Filling the void — **12 cases × 2,500 iterations ≈ 2.4 h at np 8, about 1% of the regeneration
cost** — tests the binding constraint directly. **Do that first, or in parallel.**

## The operational landmine anyone regenerating must know

**`500/` is a flattened ML snapshot, not a restartable state.** Every patch in
`500/{T,U,p_rgh,p,k,epsilon,nut,alphat}` has been rewritten to `fixedValue`, 848 per field,
525 of them carrying an inline `// original: zeroGradient` marker. Restarting from it directly
converts **525 adiabatic walls into fixed-temperature walls** and freezes the server-outlet
`tSink + ΔT` expressions — a different physics problem, and it runs without error.

The un-flattened boundary spec survives only in **`_1_bak/`** (525 zeroGradient / 323
exprFixedValue / 8 cyclic). Verified dataset-wide: 40/40 sampled cases flattened the same way,
and all 753 have `_1_bak`. A genuine restart is `500/`'s internal field spliced onto
`_1_bak`'s boundary spec — zero type mismatches on T, U, p_rgh, k, epsilon, nut, alphat.

Also worth knowing before anyone recomputes an energy balance: `acu_return` and `server_inlet`
are `totalPressure` (pressure-driven) while carrying `flowRateOutletVelocity` on U, so their
nominal volumetric flows are not realised — `acu_return` delivers 30.06 m³/s against a 33.40
nominal and `server_inlet` the reverse. Per-subsystem mass therefore does not close (global
mass closes to 1.7e-5), so "420.94 kW of server power" is not a physical quantity and the
mixed-return temperature error is the defensible statistic.

## Convergence, stated plainly

Neither arm ever met `residualControl`; both ran the full 5,000 extra iterations to `endTime`.
At 5500, T (2.6e-5), p_rgh (5.4e-3), k and ε all pass, but **U plateaus at 1.4–2.4e-4 against
a 1e-4 threshold** and oscillates without trend from ~2000 on. "Converged" here means the
level and the energy balance are stationary to ~0.003 K and ~0.2%, **not** that the solver
reached its fixed point. Any regeneration must relax the U criterion or accept a weakly
unsteady solution.

The version gap is **v2406 → v2412**, not v1912 → v2412 as I first wrote — `v1912` appears only
on hand-authored template files no solver ever rewrote. Continuity was established properly by
the adversary: the cross-version reproduction discrepancy (field RMS 0.177 K at np 8) is
**smaller than the within-version decomposition scatter** (0.347 K between np 4 and np 8 of the
same binary).

## What remains unknown

- N=3 of 753 for the drift magnitude; N=90 for the residual statistics; N=1 case with two
  shift magnitudes for the 0.66.
- The 0.66 is measured only downward, away from the 297.15 K initial condition. An upward
  shift would likely realise more.
- Why the pressure-driven outlet split lands exactly on the swapped nominal totals. Stable
  across 5,000 iterations and across the 6 K shift, but unexplained.
