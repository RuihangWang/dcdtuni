# Canonical and default Newton DAE entry

The canonical Shinaide project and projects without a saved experiment now select the synthetic Newton DAE path. The canonical input retains its original 180 s duration, 1 s time step, and inactive load step. Loading and rendering remain pure. Prepare/download persist the actual chosen mock equipment pack and HX assumptions before computing the submitted source-project hash.

The canonical construction JSON is unchanged: 14 racks, 56 compiled cold plates, the original heat loads, ports and authored routes. The compiler emits 187 nodes and 242 edges. Primary flow 1 kg/s, inlet 293.15 K and 0.02 m³ storage on each HX side are explicitly synthetic; fluid cp/rho follow the saved project. They are not measurements or a manufacturer calibration. The `Shinaide_dae_cpu.cfdproj` experiment, construction and MMS inputs are unchanged; only two provenance statements are corrected.

`engine: "newton-dae"` is a lightweight saved intent. A missing experiment reads as that intent without materializing or compiling. Ordinary time edits retain it, including empty/unconnected projects. Marked modes use the top-bar `domainModes`; stale marked analysis is resolved/displayed purely. Newly requested mock adoption also follows the top bar. Existing saved no-marker v2/PID inputs and saved no-marker v3 independent-clock inputs keep their compile compatibility. Unknown engine values and unsupported Newton+PID combinations fail clearly; PID settings are not silently discarded.

The canonical actual project loader exports steady v3 with `N_t=1`, and transient v4 with `N_t=181`, duration 180 s and no active load-step event. Reopening gives an identical transient manifest. The new semantic tests also exercise the actual Panel Prepare/download handlers with a mocked runtime client: they verify materialization happens before hashing and the submitted hash equals the re-read saved project. These tests do not start a backend solver.

The first full suite failed in five tests: two stale source-wiring assertions, two simulated SeedInspector edits that bypassed the native React value setter/focus contract, and one frozen research fixture whose missing experiment had become the new default. Root/context updated only the respective three test files. The research fixture explicitly restores its saved legacy intent before adoption, keeping the golden manifest unchanged. Later targeted regressions exposed marked stale-mode display and unmarked steady initialization under a transient legacy timeline; both are retained as red logs before the minimal pure-display corrections.

Two `.invalid-first-newline` files preserve the optional test artifact writer's first erroneous literal backslash-n suffix. They are invalid JSON and are never accepted evidence. The production download Blob was not affected. The first `baseline-red.exit` is empty because its shell wrapper did not capture the code; use `baseline-corrected-red-execution.json` and the runner-owned execution JSONs for return codes.

The final full suite passed 3526 tests with one existing optional fixture-dump skip (356 passing files, one skipped file); the final TypeScript check exited 0. Final test/typecheck tallies and run identities are in `receipt.json`. `case-semantic-diff.json` binds original and modified case bytes and exact allowed JSON paths. `source-before.tar.gz` and `source-after.tar.gz` preserve source bytes, including separately attributed root/context test-only maintenance. `source-identity.json` and both archive identity files bind every member. Valid canonical exports and invalid first artifacts are separately identified in `export-artifact-identity.json`. `manifest.json` binds all evidence files except itself.

Reproduce from the repository's `src/frontend/react` directory with repository-owned temporary storage and no CUDA:

```sh
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
TMPDIR=/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/liquid-canonical-default-newton/cache \
taskset -c 24-31 npm test -- --minWorkers=1 --maxWorkers=2
npm run typecheck
```

This entry change does not rerun the full graph MMS, CUDA parity or the old 600 s demo. Their frozen inputs/results remain separate evidence. A new real main-browser canonical steady→transient run is a separate product acceptance gate. Port spacing, inlet direction and route collision ambiguities remain physical design issues; display clipping does not validate or change the authored physical route. No backend equation, CDU/PDU visual source, physical geometry or solver threshold is modified here.
