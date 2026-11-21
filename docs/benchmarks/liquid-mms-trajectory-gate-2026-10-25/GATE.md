# Registered gate — full-ring MMS, whole-trajectory order (2026-10-25)

This file is committed **before any product run of this packet**; the run, its
results and the README come in later commits. Nothing below is changed after
results are seen. It follows the next step recorded by
[liquid-mms-hx-order-cause-2026-10-22](../liquid-mms-hx-order-cause-2026-10-22/README.md):
register the order gate on the whole-trajectory norm.

## Problem

- **Graph and coefficients:** the full Shinaide ring input of the 2026-10-07
  packet, `../liquid-full-mms-asymptotic-20261007/actual-v4/grid-0p03125/results/after-transient-base.liquidrun`
  (SHA-256 `622142abe69c0b42accf2e3c0dc6abeae5d509d15717bdf7b0161e371d46a4d5`):
  229 nodes, 286 edges, 892 water cells, 56 cold plates, one HX.
- **Manufactured solution:** the packet's independent NumPy constructor
  `../liquid-full-mms-asymptotic-20261007/original-coarse/independent_trajectory.py`
  (SHA-256 `270f992cd63d2de1688548863f84a10ebfdcc5a4657165c7b16feb3c9db3e7eb`),
  unchanged except one attribute set after construction: the forcing period
  is **20 s** (ω = 2π/20) instead of 32 s. It never calls the product residual or
  a solver; the product only receives its sources, initial state and initial rate.
- **Horizon:** 0–8 s, transient, consistent initialization, backward Euler.
- **Levels:** dt = 2, 1, 0.5, 0.25, 0.125, 0.0625, 0.03125 s (seven levels, six
  refinement pairs).
- Each level's input is the base input with only `time.dt_s`, `time.N_t` and
  `manufactured_benchmark` changed (the same three fields the 2026-10-07 packet's
  producer comparison allowed to change).

## Error measure

For each thermal block B in {node temperature, water cells, solid, HX secondary,
HX primary}:

E_B(dt) = max over every accepted sample t_n and every entry i of B of
|y_product(t_n)_i − y_manufactured(t_n)_i|  (whole-trajectory L∞, kelvin).

Node temperatures are compared only where the product marks them observable.
Hydraulic pressure and flow are reported but not graded: their manufactured
values are reproduced to solver precision at every level, so they carry no
time-discretisation order (as in the 2026-10-07 packet).

## Gates

- **G1 (order).** For every thermal block and every refinement pair,
  p = log2(E_B(dt) / E_B(dt/2)) lies in [0.85, 1.15].
- **G2 (solver).** Every accepted step has Newton scaled residual ≤ the input's
  `newton.atol`, and |energy balance| ≤ 1e-6 W relative to (1 + heat input).
  (Both are enforced by the product per step; a violation aborts the level.)

## Reported, not graded

Final-time orders per block; the Richardson ratio r = e₂/e₁ of the final-time
error from the three finest levels and |r|·dt per level; wall time per level.
