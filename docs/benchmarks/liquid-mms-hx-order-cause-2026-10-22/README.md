# Full-ring MMS: why the coarse HX-secondary order gate stays RED (2026-10-22)

**Cause found; the solver needs no fix.** The [full-ring MMS receipt](../liquid-full-mms-asymptotic-20261007/README.md)
keeps one RED gate: the final-time order of the HX-secondary temperature at the
coarse steps (dt = 2 to 0.0625 s) is 0.50 to 0.89, outside the 0.85–1.15 band,
while every other block is first order there. This receipt finds the cause.

## What the evidence shows

1. **The product is an exact backward-Euler solver.** An independent linear
   recurrence of the BE truncation error reproduces the product's stored errors
   at all eight levels to within 5.4e-9 K. It uses the same formula as the
   asymptotic receipt's `independent_asymptotic_recurrence.py`, with the
   manufactured flows and storage temperatures in closed form. The coarse orders
   are therefore the orders of backward Euler itself.
2. **The order keeps rising to one.** Refined to dt = 1/256 s, the final-time
   HX-secondary order goes 0.938, 0.968, 0.983, 0.992. The gap to one halves
   with every halving of dt: first order with an O(dt²) correction.
3. **The coarse deficit is that correction.** At t = 8 s the error is
   e(dt) = e₁·dt + e₂·dt² with r = e₂/e₁ ≈ −1.45 s⁻¹. The leading coefficient
   e₁(t) of the HX secondary changes sign at t ≈ 6.5 s, so at 8 s it is still
   building up, and the second-order term stays comparable until |r|·dt is
   small (dt below about 0.1 s). The coarse steps have |r|·dt from 2.9 down to 0.18.
4. **It is not stiffness.** The residence times are all short against the
   coarse steps: water cells have a median of 0.13 s, the HX secondary 0.75 s
   and the solids 3–6 s. Yet making every storage four times faster
   (capacities × 0.25) puts all coarse orders inside the band, and making them
   four times slower pushes them above it.
5. **Neighbouring problems pass at the same coarse steps** unless the final time
   falls just after a sign change of e₁. Each variant uses the same graph and
   solver, with x\*(t) and m\*(t) kept and the source adapted:

| Variant | e₁ sign change (s) | e₁(8 s) / max | r (1/s) | Coarse final-time orders (dt 2 → 0.0625) |
|---|---|---:|---:|---|
| original | 6.47 | 0.53 | −1.45 | 0.50 0.68 0.74 0.82 0.89 |
| capacities × 0.25 | none | 0.58 | 0.12 | 0.87 0.92 0.95 0.98 0.99 |
| capacities × 4 | 7.97 | 0.02 | 2.90 | 1.18 1.25 1.30 1.26 1.18 |
| capacities × 16 | none | 0.85 | −0.16 | 0.76 0.89 0.94 0.97 0.99 |
| forcing frequency × 0.5 | none | 0.58 | 1.14 | 1.09 1.09 1.10 1.10 1.07 |
| forcing frequency × 2 | 3.25 | 0.99 | −0.45 | 0.98 0.97 0.95 0.96 0.97 |

By causing storage family, the water cells' truncation error converges most
slowly (0.23 → 0.99 over the same refinements). The HX secondary's own error
reaches 0.98 by dt = 0.125 s. The families add with one sign at t = 8 s, so no
family cancels another. The per-family orders are in `results.json`.

## Consequence

- The original coarse gate stays RED as recorded. It is not re-graded, because
  criteria are registered before results. A final-time order measured on one
  scalar at one instant tests the method only while |r|·dt is small; for this
  block at t = 8 s that holds below about 0.1 s, which the finer levels meet.
- The same block's whole-trajectory orders, in the asymptotic receipt, are 0.84
  at the coarsest step and 0.89–0.99 after it.
- **Next step for the next MMS packet:** register the order gate on the
  whole-trajectory norm, or compute |r|·dt from the two finest levels before the
  coarse levels are graded.

## Reproduce

```bash
PYTHONPATH=src OMP_NUM_THREADS=2 python docs/benchmarks/liquid-mms-hx-order-cause-2026-10-22/analysis.py
```

The run takes about two minutes on two threads. `analysis.py` reads only the
stored inputs of the asymptotic receipt; it checks each one's SHA-256 against
that receipt's `ROOT-archive-copy-map.json`. It compiles the graph with the
current backend and writes `results.json`, which holds:

- the reproduction differences;
- the final-time contributions and orders down to dt = 1/256 s;
- the residence times;
- for each variant: its coarse orders, e₁, e₂, r and the times where e₁ changes sign.
