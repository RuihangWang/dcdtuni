# Coupled velocity startup and physical volume-flux conservation

The two-rank product baseline is locally conservative but globally nonconservative. Its processor face coordinates and relative vertex correspondence match exactly. At 0.02 s, 27 of 64 shared faces do not carry opposite fluxes: the processor-copy net is −7.480535929e−7 m³/s and the physical-boundary net is +7.4805354e−7 m³/s. The maximum cell imbalance is 5.830000517e−14 m³/s. The copied steady seed's shared fluxes all cancel exactly.

Zero cell divergence is therefore insufficient. For all ranks, internal face terms cancel within each rank, but a processor face is present once on each side. The exact accounting identity is Σ(cell net) = Σ(physical boundary phi) + Σ(all coupled copies phi). A conservative shared interface requires its two oriented copies to cancel individually; a pressure projection can hide a violation by increasing physical outflow. The new read-only audit reports all three quantities and validates actual reciprocal geometry before comparing phi. It does not declare convergence or product acceptance.

## Cause and minimal change

Installed OpenFOAM v2412 processorFvPatchField.C:88–92 initializes a missing value by extrapolating its LOCAL internal field. The product's internal-field-only seed writes no processor U value. Before that cache is exchanged, Euler oldTime can copy it; EulerDdtScheme.C:552–577 then forms phi.oldTime − Sf·interpolate(U.oldTime). The standard face-dependent ddt coefficient also depends on this asymmetric correction. actual-seed-euler-support.json and tools/audit_pimple_startup_euler_support.py independently reconstruct the ordinary processor interpolation weights and standard Euler coefficient from the frozen seed. The first production step's 27 mismatched shared faces coincide with the startup Euler correction's asymmetric support, and the proper remote-cache pair sums are all exactly zero; this support observation is a causal clue, not an independent reproduction of the full momentum matrix.

The production change calls U.boundaryFieldRef().evaluateCoupled<coupledFvPatch>() immediately after reading U, before createPhi and before any Euler history use. The installed two-phase method initiates and completes communication only for coupled patches. Physical fixed-value and wall patches are not evaluated or changed. No equation, physical boundary, flux averaging, convergence threshold, or pressure gauge is changed.

## Regression evidence

A pytest-only kernel extracts the exact U-read/startup block from the production header, uses the actual v2412 Euler ddtCorr and a two-rank pressure projection, and preserves all generated inputs and outputs. The two cells have distinct internal velocities and a conservative initial phi. Missing and deliberately stale processor caches are tested.

| Source / initial cache | Maximum cell net (m³/s) | Coupled corrected net (m³/s) | Physical net (m³/s) |
| --- | ---: | ---: | ---: |
| Original / missing | 0 | −0.25000000000000133 | +0.25000000000000133 |
| Original / stale | 0 | −0.18750000000000067 | +0.18750000000000067 |
| Fixed / missing | 0 | 0 | 0 |
| Fixed / stale | 0 | 0 | 0 |

The original production startup block fails both remote-old-velocity assertions (2 failed); the changed block passes both MPI2 regressions (2 passed). The fixed coupled Euler predictor sums also equal zero exactly. All physical velocity caches keep their prescribed values. Nine offline accounting/geometry tests pass. The public reproduction script passes all eleven tests (two MPI2 kernels plus nine offline tests). baseline-env-failure.txt is a separate activation error retained for provenance, not the numerical failing baseline.

## Reproduce and inspect

Activate the existing dcdtuni conda environment, then run bash tools/verify_pimple_startup_coupling.sh. The script sources the existing project build environment and compiles only an isolated pytest kernel; temporary/build/case files remain under the repository. It does not build or run the production solver.

The preserved product baseline can be recomputed without a solver:
    python tools/audit_air_written_flux_conservation.py --run docs/benchmarks/shinaide-product-2026-10-05/evidence/pimple-startup-coupled-phi/actual-main-raw --time 0 --time 0.01 --time 0.02 --output .codex-tmp/pimple-startup-coupling/recomputed-baseline.json

receipt.json records the tested production source hashes, raw kernel results and installed primary-source definitions. raw-manifest.json binds every frozen input/output. The old and new isolated kernel ELF hashes are recorded; those binaries are not production builds.

Scope: this validates fresh startup with missing or stale coupled U caches, using coincident processor coupling. It does not claim an already loaded U_0 restart history is repaired, nor transformed/AMI/mapped pairing support in the offline audit. The fixed production React/API run, written coupled phi and physical-boundary conservation must be checked separately before accepting the product trajectory.

The support-only diagnostic can be repeated with python tools/audit_pimple_startup_euler_support.py --run docs/benchmarks/shinaide-product-2026-10-05/evidence/pimple-startup-coupled-phi/actual-main-raw --dt .01 --first-time 0.01 --output .codex-tmp/pimple-startup-coupling/recomputed-support.json. It deliberately supports missing ordinary processor U values and the standard Euler coefficient only. Its separate frozen-case regression passes; no full momentum amplitude agreement is claimed.
