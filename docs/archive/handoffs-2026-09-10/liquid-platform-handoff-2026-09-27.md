# Liquid platform handoff — 2026-09-27 SGT

The Mac task checkout is on `codex/liquid-control-platform`. Its
`c6a63a045637e1a8502020e3dce74f230ea8f7ba` code commit contains
the project-derived v3 PyTorch DAE solver, NV and Shinaide `.cfdproj` cases,
React workbench, Three-to-USD bridge, tests and bounded verification reports.
It has **not** been pushed to the GitHub remote or merged into `main`.

The matching 40901 isolated workspace is
`/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform`. SSH recovered after
two transient `Connection closed by UNKNOWN port 65535` errors. The Mac commit
objects were transferred directly to that host without pushing to GitHub.
All 175 changed paths were checked against the target commit: 174 regular files
matched byte-for-byte, and the plotted PNG's decoded file hash matched too.
There were no staged or unexpected tracked user changes. `git reset --mixed`
then moved only this task worktree's branch/index to `0528d190`; it did not
overwrite working files. A later documentation-only follow-up was mirrored
through the same checked process, and the two branch HEADs matched at the last
read. The 57 pre-existing Git LFS texture status items
remain visible and were not staged, reset hard or deleted. The repository
default branch has advanced independently; inspect both branches before
integration and preserve 40901's working data.

The local commit deliberately excludes user `.DS_Store` files,
`docs/developer/macos_build_notes.md`, pre-existing local
`tools/build_third_parties.sh` and `tools/foam_env.sh`, plus derived 123 MB
`case/Shinaide_Liquid/Render/` scene/USD/texture data. Original archived run
directories remain under ignored `case/*/Liquid/` on the hosts. The committed
case project definitions and mock fixtures are sufficient to compile and run
new examples; see `liquid-dae-workbench.md` and `shinaide-dae-workbench.md`.

## Verified scope

- [Final CPU regression](../../benchmarks/dae-final-verification-2026-09-26.json):
  86 numerical tests pass; an opt-in CUDA test was skipped in that CPU run.
  [MMS](../../benchmarks/liquid-dae-mms-2026-09-26.json) and NV/Shinaide gradients
  have separate physical evidence.
- [NV CPU1/CPU4](../../benchmarks/nv-cpu1-cpu4-2026-09-27.json): independent
  processes use the same immutable 1604-state mock. Scaled steady/one-step
  differences are about `1.04e-13`; heat gradients match. One-step times are
  `1.4582/1.2697 s`, a single-run comparison rather than a speedup claim.
- [Authorized CUDA episode](../../benchmarks/single-gpu-numerical-2026-09-26.json):
  both actual CUDA tests pass, and the full NV schema/tensor entry agrees with
  CPU to scaled state `1.23e-13` with an equal heat gradient. The one short
  float64 GPU entry was slower than CPU; no general performance claim follows.
- The production React build passed after the final CPU compatibility changes.
  The preceding full frontend suite passed 2547 tests; later focused runs
  passed 39 protocol/sync and 26 overlay/sync tests. These counts overlap.
  The browser loaded Shinaide's completed 600 s/31-sample run and the saved
  project, with temperature overlays and safe Three.js fallback.
- CPU OpenUSD authoring/reopening preserves the engineering geometry,
  identity and texture references at t=0/600. Archived accepted-input replays
  reproduce all eight physical field groups exactly; their copied local files
  were independently rechecked without another solve.

## Still open

Live Three.js → OV → Three.js **has not passed**. One authorized single-GPU
episode loaded Kit, the new bridge and signaling TCP, but two browser attempts
timed out before decoded video or scene ACK. The process and display were
cleaned up before the fixed deadline. See the
[Kit diagnosis](../../benchmarks/single-gpu-kit-2026-09-26/README.md). Subsequent
CPU-only fixes pin one NVIDIA ICD, configure an explicit four-core/two-numerical
thread budget, collect bounded SDK diagnostics, reconstruct multipart ACKs and
notify on material-only scene changes. Their live effect is unverified.

[The OV-only retest plan](../omniverse-2026-09-10/ov-retest-plan-2026-09-27.md) is prepared. The first
GPU authorization was explicitly one-time and is consumed. Do not start Kit or
another GPU test until the user grants a new explicit authorization. Field NV
telemetry/calibration, exact valve closure and large sparse DAE scaling also
remain outside the demonstrated mock scope.

An isolated 2026-09-27 Shinaide air check now exercises the existing
`.cfdproj → strict .cfdrun + STL → OpenFOAM mesh → ten-iteration solve`
chain with four CPU ranks. It produced fields and sensor output, but three
mesh-quality checks failed and convergence was `NOT_CONVERGED`; this is a
pipeline smoke rather than an accepted air prediction. See the
[air smoke report](../../benchmarks/shinaide-air-smoke-2026-09-27.json). A v2 execution-budget parity guard was
added after an initial root/compatibility-block mismatch would have launched
eight ranks; both loader paths now reject that ambiguity before OpenFOAM.
