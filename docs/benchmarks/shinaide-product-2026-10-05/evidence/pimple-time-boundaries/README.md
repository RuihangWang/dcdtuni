# Native PIMPLE physical time boundaries — 2026-10-05

Source fix: bbde05a76f75ba80621b154ffcd4648a1a95856c. Installed PIMPLE SHA256: b49ea3b9140e2943d0712bf021c0a9c54d40d17be64c297c4938e753903d4e3b. SIMPLE remains the independently paired 7932473e build; its source, physical boundary conditions and steady stopping thresholds were unchanged by this repair.

The actual React-exported diagnostic project had duration 0.005 s but initial deltaT 0.1 s. The previous owned PIMPLE used the stock Time::run() half-timestep stopping test before adjusting deltaT. On the strict 511-cell, two-rank channel, genuine SIMPLE initialization converged in 337 iterations, then the old 87987cc7 PIMPLE printed Starting time loop and End with no time step. API job 2771a1bc returned 1 because there was no positive field-bearing result. Its input, raw native logs, actual initialized fields and native steady archive are preserved in before-dt-over-end.

The new solver reads normal runtime-modified controls before each run() test, reduces the timestep to the remaining end/event interval before that test, and repeats the physical cap after Courant adaptation and before incrementing Time. It retains the stock run()/++Time stop handling; it does not assign a final timestamp or simulate completion after the fact. A genuine final solve is written even when endTime is off the write grid. Normal function object and stopAt handling remain in the installed OpenFOAM Time implementation.

The runner collects the original active BC registry corner timestamps, including steps and both ramp corners, before the existing OpenFOAM table epsilon conversion. The sorted unique physical times are written as dcTransientEvents in controlDict. Existing schedule values and interpolation expressions are unchanged. Completion reporting now rejects both premature termination and overshoot, with a relative tolerance reflecting the timeName precision rather than a one-second absolute floor.

Every test below went through the actual React browser export API and authenticated solve-only API on the dedicated 8392 service, using two real MPI ranks on CPU 36–39 and the same fully checked geometry. No mesh generation, analytic phi insertion, residual relaxation, or larger geometry substitution was used.

| Case | API job | Real computed times (s) | Result |
| --- | --- | --- | --- |
| Initial deltaT 0.1, duration 0.005, adaptive | 802b59a3 | 0.0012, 0.002, 0.003, 0.004, 0.005 | Completed; genuine final fields at 0.005 |
| Fixed deltaT 0.01, duration 0.026 | c580a5b0 | 0.01, 0.02, 0.026 | Completed; final nondivisible step calculated and written |
| Supply temperature step at 0.003, fixed deltaT 0.004, duration 0.011 | 904e1c5e | 0.003, 0.006, 0.009, 0.011 | Completed; no skipped event or terminal overshoot |
| Same temperature event, explicitly set output interval to every step | 611de215 | 0.003, 0.006, 0.009, 0.011 | 64 actual supply-face values equal 297.15 K at 0.003; initial values equal 295.15 K |

All four runs independently obtained the original 337-iteration genuine SIMPLE start, retained the exact two-partition mesh and full nonzero native phi, and finished without hitting a PIMPLE outer-iteration cap. The default/fixed-step output cadence was retained in the first three cases. The fourth changed only the configured output cadence so the event-time field could be audited directly.

With adjustTimeStep disabled, a physically shortened event step can remain the timestep for later steps; the implementation does not blindly restore controlDict.deltaT and thereby override function-object time control. All actual steps remain positive and no larger than the configured initial fixed timestep. The future refinement to restore a nominal fixed cadence needs a separate contract with runtime/function-object controls.

The independent audit validates input and final-field SHA256 identities, exact original mesh hashes using the earlier archived geometry, actual native Time/transport-solve/End records, both-rank field finiteness, whole native phi seed preservation, and the real event-face values. The shared geometry's complete allTopology/allGeometry raw evidence and the last input-bound quality receipt are preserved. Source snapshots, the source patch, compiler/linker output and two exact fuser checks before atomic ELF installation are in build/. The old 879 binary is retained inside the repository at .codex-tmp/pimple-time-boundaries/binaries/ and must be preserved when integration artifacts move into main.

Recheck the preserved evidence without launching CFD:

    python3 docs/benchmarks/shinaide-product-2026-10-05/evidence/pimple-time-boundaries/audit_time_boundaries.py

Before source repair, the new backend tests reported 13 failures. After repair, related time/template/schedule/convergence tests passed 119 with one skip, and lifecycle/partition/configuration tests passed 105. The real native regressions above supplement those Python tests.

This is a time-control and initialization lifecycle proof on the synthetic straight channel. Per-step transport/pressure/turbulence residuals remain in the raw logs and convergence.json as diagnostics. Completion and PIMPLE convergence markers do not establish temporal accuracy, MMS order, or acceptance of the large target airflow case. Its remaining full-geometry and physical-convergence gates are separate.
