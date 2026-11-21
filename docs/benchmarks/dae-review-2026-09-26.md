# Targeted DAE correctness review

An independent read-only review covered `dae_newton`, `equation_graph`, the
strict v3 loader, numerical experiment host, run adapter and lifecycle manager.
One concrete correctness finding was identified: a pressure-only boundary with
both internal incoming flow and a positive net outgoing flow was allowed to
supply additional fluid without an inlet temperature. The old test only caught
a source with zero incoming flow. A constructed two-pressure loop could report
100 W source heat plus an undeclared 100 W boundary contribution as balanced.

The new regression `test_pressure_boundary_with_existing_inflow_cannot_supply_undeclared_extra_fluid`
failed before the change with `DID NOT RAISE`, confirming the issue through the
actual steady Newton solve. The accepted-state guard now checks **net** source
flow, with a dtype-dependent accumulation-roundoff tolerance. The residual
itself remains pure batched tensor algebra. Explicitly declared temperature
boundaries continue to allow their physical heat exchange, and the regression
also verifies that valid case. A companion regression ignores 1e-13 kg/s
roundoff at a closed-loop pressure datum.

This is a targeted review, not a claim that every repository subsystem or all
GPU execution paths were audited. The numerical GPU validation remains unrun.

Post-fix verification: **31 passed in 6.42 s** on 40901, CPU affinity [0],
PyTorch/BLAS/OpenMP1, nice10, CUDA hidden. The selected suites covered physical
EquationGraph, device/dtype and residual profiler, physical MMS, numerical
experiment host and v3 runtime. The fixed EquationGraph SHA-256 is
`b1b3112e0e0bbd2add495f71dc00fe257ed35d7a0b18e72f24a676c1eadfe783`.
Earlier numerical evidence retains its original source hashes; this guard
change does not alter the residual evaluated for a valid closed loop.
