# NV mock vectorized DAE CPU probe

Status: **passed**. Elapsed 9.876 s; 60 s caller cap, 55 s internal deadline.

Same immutable 316-node / 348-edge input, float64 PyTorch algorithm; one then two threads on CPU 0/1, nice 10. No CUDA or NSCC execution. Full hashes, build/SIMD capability and effective thread settings are in the JSON.

The heat multiplier is applied at the next step and the run stops before the saved 5 s event. All topology, physical coefficients and primary-circuit assumptions remain unchanged. Independent equilibrium is recorded separately, never inserted as a transient sample.

- CPU 1 thread(s): passed; affinity [0]; accepted transient steps 3.
  - t=0.0 s: primary return [301.28008130082003, 301.2800813008171] K; secondary supply [305.05785907860104, 305.0578590785967] K; mass residual 8.88178e-16 kg/s; energy balance -2.32772e-07 W.
  - t=1.0 s: primary return [301.28008186999597, 301.28008186999307] K; secondary supply [305.05786254909106, 305.0578625490868] K; mass residual 8.88178e-16 kg/s; energy balance -1.13505e-08 W.
  - t=2.0 s: primary return [301.2800854132582, 301.2800854132553] K; secondary supply [305.0578815169177, 305.0578815169134] K; mass residual 8.88178e-16 kg/s; energy balance 9.83709e-09 W.
  - t=3.0 s: primary return [301.2800976684399, 301.280097668437] K; secondary supply [305.0579398266155, 305.05793982661123] K; mass residual 8.88178e-16 kg/s; energy balance -2.50293e-09 W.
- CPU 2 thread(s): passed; affinity [0, 1]; accepted transient steps 1.
  - t=0.0 s: primary return [301.28008130081554, 301.2800813008176] K; secondary supply [305.0578590785945, 305.05785907859746] K; mass residual 4.44089e-16 kg/s; energy balance -1.49594e-07 W.
  - t=1.0 s: primary return [301.2800818699916, 301.28008186999364] K; secondary supply [305.0578625490845, 305.0578625490876] K; mass residual 4.44089e-16 kg/s; energy balance -1.74041e-08 W.

Single-run timing is reported without assuming two threads are faster. SIMD capability is a library/CPU report, not a hardware instruction trace. Unexecuted stages, gradients, CUDA and NV field validation remain unverified.

## Observed comparison

The 1-thread and 2-thread accepted steady states differ by at most `5.82e-11` in raw state values and `1.04e-13` after state scaling. The first transient step shows the same raw difference bound and a `1.03e-13` scaled difference. Both use 1,604 DAE state variables, 4,396 physical parameters, 588 pipe cells and the identical source manifest.

- Steady initialization: CPU1 `0.8014 s`; CPU2 `0.7399 s` (single-run ratio 1.083).
- First backward-Euler step: CPU1 `0.7383 s`; CPU2 `0.7190 s` (single-run ratio 1.027).
- Warmed batches of 20 residual evaluations: CPU1 `0.01405 s`; CPU2 `0.01493 s`. Two threads were slightly slower in this small residual-only measurement. These observations do not establish a general scaling advantage.

PyTorch reported `AVX512`; the effective thread counts were 1 and 2, with affinities `[0]` and `[0,1]`. This identifies available CPU dispatch capability, not a measured SIMD instruction trace. The CUDA-enabled PyTorch build (`2.5.1+cu121`) executed CPU float64 tensors only; CUDA validation was not run.

## Physical interpretation and independent equilibrium

The initial 340 kW steady balance gives each primary return `301.2800813 K`. Applying the runtime 1.2 multiplier changes total heat to 408 kW. At 3 s the primary returns are still approximately `301.2800977 K`: the short transient has **not** reached the new equilibrium. Almost all of the extra 68 kW is initially accumulating in modeled storage; at the third accepted step the storage rate is `67999.3155 W` and primary rejection is `340000.6845 W`.

The independently queried 408 kW equilibrium returns `302.9060976 K` on both primary circuits and `307.4394309 K` on both secondary supplies. The independent per-zone first-law reference `Tin + (408000/2)/(5*4182)` matches primary return within `5.68e-14 K`. Querying equilibrium left the transient sequence and state unchanged in both thread runs.

Across all recorded steady, transient and independent-equilibrium states, the largest mass residual was `8.89e-16 kg/s`; the largest absolute global-energy imbalance was `2.33e-7 W`. Each recorded full Newton root required one correction after the hydraulic predictor/previous accepted state and zero backtracks. This is short-step numerical evidence for the mock case; it is not timestep convergence, a complete settling response, gradient validation or NV field calibration.

Preflight note: the first invocation from `/tmp` stopped before importing PyTorch because a default-path expression indexed a nonexistent third parent (`IndexError: 2`). The harness default was made safe for a shallow temporary path, then the recorded run above completed. No physical source parameter or solver option was changed to obtain convergence.
