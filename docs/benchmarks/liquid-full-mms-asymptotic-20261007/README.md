# Full Shinaide ring MMS: two finer time steps, 2026-10-07

The manufactured benchmark now has actual product-chain Newton DAE trajectories at dt=0.03125 s (257 frames) and dt=0.015625 s (513 frames), in addition to the unchanged six original coarse levels. These use the full ring graph: 1,693 unknowns, 950 storage states, 229 nodes, 286 edges, 892 water temperatures, 56 solid temperatures and two heat-exchanger temperatures. The case geometry/topology is real; coefficient fixtures and manufactured forcing are synthetic and explicitly identified. This proves this numerical benchmark, not calibration or the ordinary engineering run.

See [the complete derivation](original-coarse/DERIVATION.md) for equations, manufactured states, source terms, initial/boundary conditions and error definitions. The genuine React run-config producer, graph compiler and PyTorch Newton solver were used. Independent Node canonical comparisons permit only the three declared time/source fields to change; all other nonbenchmark input and initial state fields remain equal. The 505 frozen source files and all eight cycle-coefficient arrays remain held.

| Last refinement dt (s) | Node temperature final order | Water final order | Solid final order | HX secondary final order | HX primary final order |
|---|---:|---:|---:|---:|---:|
| 0.0625 -> 0.03125 | 0.99991 | 1.00017 | 0.99922 | 0.93842 | 1.01222 |
| 0.03125 -> 0.015625 | 0.99995 | 1.00008 | 0.99961 | 0.96762 | 1.00617 |

The original coarse HX-secondary order gate remains RED in the retained original packet. Finer results approach first-order backward-Euler convergence; no original result or gate was replaced. Its whole-trajectory orders on the two new refinements are 0.98382 and 0.99144. Hydraulic pressure/flow errors are near solver precision and are not assigned a convergence order.

The two fine solves completed with no timeout. Actual Newton process times were 225.63 s and 456.73 s, under a private 900 s budget per fine level; the existing production 300 s budget was unchanged. The maximum scaled residuals were 1.524e-11 and 3.148e-11; maximum energy residuals were 4.363e-7 and 7.230e-7 W. Independent analytical backward-Euler recurrence over all 1,179 thermal unknowns agrees with product error trajectories within 1.541e-10 and 3.871e-11 K. This independent calculation is not a second invocation of the product solver.

## Evidence and reproduction

- `original-coarse/`: unchanged original six-level source/derivation/results packet, including its RED gate.
- `prepared-v4/`: exact execution plan, producer comparison and source cohort; earlier V1-V3 pre-Newton tool failures are retained.
- `actual-v4/`: actual two fine levels, raw frames, seven block states/reference/error arrays, flat 1,693-state arrays, independent recurrence contributions, producer logs and all eight closed process statuses.
- `root-executions/`: original failed attempts and successful Root V4 execution authorization/status.
- `independent-actual-review/REPORT.md`: independent raw-data and primary-binding audit; it did not run another Newton solve.
- `ROOT-raw-arithmetic-verification.json`: Root directly recomputed raw actual-minus-reference errors, initial state, final/trajectory norms, contribution sums and orders from all four finest NPZ datasets.
- `ROOT-current-normal-cohort-comparison.json` and `ROOT-frozen-versus-current-backend.patch`: 46 of the current 48 backend files equal the experiment cohort. Two explicitly recorded current changes concern accepted-trace source/time truncation and optional manifest lookup. This receipt does not claim all current backend bytes or loaded service memory equal the frozen experiment.

`ROOT-archive-copy-map.json` binds every original file and stored archive path with original/stored SHA256 and byte lengths. Fourteen large files are losslessly gzip-encoded; decode only entries whose `encoding` indicates gzip and verify their original SHA before use. Already compressed files are retained literally. The two duplicated actual-after source cohorts were verified against the canonical 505 source files before deduplication; the map retains all 1,010 original path bindings. Runtime caches and package links are explicitly excluded. All artifacts live under dcdtuni.

`MANIFEST.json` covers this durable receipt by stored bytes. To reproduce execution, materialize the frozen source/input and producer dependencies in a fresh directory under dcdtuni and follow the exact V4 plan, preserving the private budgets and source bindings. The archived execution scripts contain absolute historical paths; they are evidence and must not be blindly rerun over existing once-only directories.

Remaining acceptance is separate: ordinary liquid parameters, finite-difference gradient checks, GPU parity for this full case, and real-browser steady/transient run and replay. No browser, physical calibration, gradient or GPU acceptance follows from this manufactured benchmark. Large Shinaide strict mesh and CFD acceptance remain unresolved.
