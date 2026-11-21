# Liquid DAE past 4000 unknowns: the sparse Newton/adjoint path (2026-10-22)

**Result: networks above the old dense limit now solve, with the same values and
gradients as dense.** Before this change the runtime refused any network above
4000 unknowns, about 33 Shinaide racks. Above 4000 it now picks the sparse path
(`src/backend/liquidcooling/solver/dae_sparse.py`); up to 4000, every saved
project keeps the dense path it had.

## How it works

- **Jacobian.** The structural pattern is probed once per equation graph with
  chunked JVPs. Columns that share no row get one colour, so one JVP per colour
  fills every nonzero (nine colours on Shinaide).
- **Check.** Every Newton step compares the assembled matrix with one exact JVP
  in a random direction, row by row against each row's own magnitude.
  - A row that disagrees has a dependency the pattern lacks. Its exact
    nonzeros come from one reverse-mode product, and the pattern is recoloured.
  - Once a check fails, every row above round-off is repaired in the same pass.
  - A stale pattern is never used silently.
- **Solve.** SuperLU factors the matrix once per step, on the host for every
  device (`torch.sparse.spsolve` on CUDA needs a cuDSS build, which this
  environment lacks). The implicit adjoint solves the transpose with the same
  factorisation.
- **Choice.** `newton.linear_solver` in `.liquidrun` v3/v4 may name `dense` or
  `sparse`. Omitted, the runtime picks dense up to 4000 unknowns and sparse
  above. An explicit `dense` above 4000 is refused at admission.

## Two defects found by this benchmark, fixed before the numbers below

Both showed up only on the real Shinaide transient after a 20 % heat step; the
unit tests had passed.

1. **Missing entries went unseen.**
   - **Cause.** The steady pattern seeds the time-step pattern. At steady state
     a pipe cell's inlet and outlet temperatures are equal, so the advection
     term's flow derivative is exactly zero. After the heat step those entries
     are nonzero but small, about 1e-5 of their row. The first check compared
     mismatches against one global tolerance (1e-7 of the largest product), so
     most of these rows passed.
   - **Fix.** Each row is judged against its own magnitude (1e-9).
2. **Repairs ran out of attempts.**
   - **Cause.** Each probe exposed the tiny entries (1e-7 to 1e-9 of a row) a
     few at a time, so six attempts were not enough.
   - **Fix.** Once a check fails, every row whose gap is above round-off
     (1e-13) is repaired in the same pass.

`test_small_dependencies_that_appear_later_are_all_found_in_one_check` fails
against either earlier version and passes now.

## Results

The input is `case/Shinaide_Liquid/Shinaide_liquid.cfdproj`, exported through
`backend.api.export_liquidrun`, with every pipe's `n_cells` set to the value in
the table. Runs used the CPU with 4 threads, each in its own process. The host
was shared, with a load average of 76 before the runs and 43 after, so the
times are indicative. Raw rows are in `results.json`.

**Transient.** A 20 % heat step, backward Euler, dt = 2 s, 31 samples:

| Pipe cells | Unknowns | Path | Time (s) | Peak RSS (MB) | vs dense |
|---:|---:|---|---:|---:|---|
| 10 | 2941 | dense | 28.3 | 1653 | |
| 10 | 2941 | sparse | 5.8 | 926 | chip series within 1.1e-13 K |
| 60 | 13641 | sparse | 23.1 | 1432 | |

**Steady.** Equilibrium plus implicit-adjoint gradients for heat, pump speed
and valve opening:

| Pipe cells | Unknowns | Path | Time (s) | Peak RSS (MB) | vs dense |
|---:|---:|---|---:|---:|---|
| 2 | 1229 | dense | 5.8 | 750 | |
| 2 | 1229 | sparse | 4.4 | 541 | pressures and flows equal; gradients within 1.8e-14 (relative) |
| 30 | 7221 | dense (past the gate, for comparison) | 53.7 | 5642 | |
| 30 | 7221 | sparse | 8.4 | 722 | pressures and flows equal; gradients within 5.2e-14 |
| 60 | 13641 | sparse | 18.6 | 1115 | |
| 100 | 22201 | sparse | 65.6 | 1027 | |

Every run closes the energy balance to 2.5e-7 W. The steady initial guess is
already converged (Newton takes no iteration there), so the steady arm times
the convergence check and the adjoint. In the transient arm every step runs
Newton.

## Reproduce

```bash
PYTHONPATH=src python docs/benchmarks/liquid-dae-sparse-2026-10-22/bench.py
```

The dense rows past 4000 unknowns lift the admission limit inside that one
process; this is a research comparison, not a product path. The tests are in
`src/backend/tests/test_liquid/test_dae_sparse.py`:

- analytic steady and backward-Euler values and gradients;
- sparse against dense on the physical graph, on CPU and CUDA;
- a dependency that appears after a flow reversal;
- small dependencies that appear later;
- admission above 4000 unknowns.
