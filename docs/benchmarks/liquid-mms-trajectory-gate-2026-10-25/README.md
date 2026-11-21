# Full-ring MMS with the whole-trajectory order gate (2026-10-25)

**The order gate registered on the whole-trajectory norm passes for every
thermal block at every refinement, coarsest step included.** This is the next
MMS packet the [HX-order cause receipt](../liquid-mms-hx-order-cause-2026-10-22/README.md)
asked for. The gate was committed in [GATE.md](GATE.md) (`2bd88f036`) before any
product run; results came afterwards and the gate was not edited.

## Problem

- **Network:** the full Shinaide ring, with 229 nodes, 286 edges, 892 water cells, 56 cold plates and one HX. Its input is that of the 2026-10-07 packet.
- **Manufactured solution:** the same independent NumPy constructor as that packet, with one change: the forcing period is 20 s instead of 32 s, so this is a new manufactured problem.
- **Run:** 0 to 8 s with backward Euler, at seven levels from dt = 2 s to dt = 0.03125 s.
- **Solver:** the product is `DAESimulation`, the object the run sessions use. It runs the current code (tee law v2), which on this ring is identical to v1 because no valve sits at a tee.

## Results

Whole-trajectory L∞ orders (gate G1, band 0.85–1.15) for each successive halving of dt:

| Block | 2→1 | 1→0.5 | 0.5→0.25 | 0.25→0.125 | 0.125→0.0625 | 0.0625→0.03125 | G1 |
|---|---:|---:|---:|---:|---:|---:|:-:|
| Node temperature | 1.002 | 1.001 | 1.000 | 1.000 | 1.000 | 1.000 | pass |
| Water cells | 1.002 | 1.000 | 0.999 | 1.000 | 1.000 | 1.000 | pass |
| Solid | 1.032 | 1.009 | 1.005 | 1.003 | 1.001 | 1.001 | pass |
| HX secondary | 0.924 | 0.935 | 0.935 | 0.946 | 0.964 | 0.979 | pass |
| HX primary | 1.045 | 1.024 | 1.013 | 1.007 | 1.003 | 1.002 | pass |

- **HX secondary:** the order rises steadily toward one, which is first order with an O(dt²) correction. The final-time Richardson ratio from the three finest levels is r ≈ −0.39 s⁻¹, so |r|·dt is 0.78 at the coarsest level and 0.012 at the finest.
- **Final-time orders:** they match the whole-trajectory orders here (results.json), because with this forcing the final time is not near a sign change of the leading error coefficient.
- **Other errors:**
  - At t = 0 the error is zero in every block.
  - Hydraulic pressure and flow match the manufactured values to 2.5e-8 Pa and 1.2e-11 kg/s.
  - Energy-balance residuals are at most 3.7e-7 W.

## Gate G2 is RED at one level (cause found)

As registered, G2 required every accepted step's scaled Newton residual to be
**at most `newton.atol`** (1e-10). One level misses it:

| dt (s) | 2 | 1 | 0.5 | 0.25 | 0.125 | 0.0625 | 0.03125 |
|---|---|---|---|---|---|---|---|
| max scaled residual | 1.1e-12 | 1.2e-12 | 1.6e-12 | 2.4e-12 | **1.07e-10** | 2.7e-11 | 1.6e-11 |

**Cause:** the product accepts a Newton step at `atol + rtol·r₀`, where r₀ is that
step's initial residual (`solver/dae_newton.py`: `tolerance=options.atol+options.rtol*initial`).
The gate restated only the `atol` term, so it is stricter than the solver's
documented acceptance rule. Every step satisfied the product's own rule, and the
largest excess is 7 % above `atol`, at a residual of 1e-10. No solver defect is
involved. The gate is not re-graded, because criteria are registered before
results. The next packet should register the product's acceptance rule, or an
explicit residual cap with a margin chosen in advance.

## Reproduce

```bash
conda activate .codex-tmp/envs/dcdtuni      # or your dcdtuni environment
PYTHONPATH=src OMP_NUM_THREADS=4 python docs/benchmarks/liquid-mms-trajectory-gate-2026-10-25/run.py
```

The run took about 14 minutes on 4 CPU threads (12 s to 385 s per level). `run.py` checks
the SHA-256 of the base input and of the constructor against GATE.md, then writes
`results.json`:
- the errors per level and block
- the trajectory and final-time orders
- the Richardson ratios
- the Newton and energy diagnostics
- the G1 and G2 verdicts

`run.log` is the console output.
