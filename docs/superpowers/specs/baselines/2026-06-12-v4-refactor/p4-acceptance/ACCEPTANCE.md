# Plan 4 — Final Acceptance Record (cfdproj V4 / cfdrun V2)

**Date:** 2026-06-14 · **Branch:** `refactor/cfdproj-v4-cfdrun-v2`
**Gate:** spec §9.4, driven end-to-end through GPU WebGL browser-use
(renderer confirmed `ANGLE (NVIDIA … RTX 4090 … Vulkan)` on every case).
**Harness:** `scripts/p4_acceptance/` (per-case evidence in this directory).

## Result: 5/5 PASS

| Case | Scene (save/reload) | Export (V2) | Mesh cells vs baseline | Registry | Solve | Sensors |
|---|---|---|---|---|---|---|
| Base_leakage | ✅ n=16, identical, sv4 | sv2, 46+6=52, 18 STL | 476458 vs 476416 (**0.009%**) | ✅ 52 | rc=0 (1000) | ✅ 4/4 <0.5 °C (max 0.034) |
| Shinaide | ✅ n=29, identical, sv4 | sv2, 306+66=372, 9 STL | 241497 vs 241658 (**0.067%**) | ✅ 372 | rc=0 (200 cap) | 2/3 <0.5 °C, 1 @ 0.798 (capped) |
| CMI_China | ✅ n=37, identical, sv4 | sv2, 145+12=157, 21 STL | 385703 vs 385213 (**0.127%**) | ✅ 157 | rc=0 (500) | 7/8 <0.5 °C, 1 @ 0.812 (capped) |
| STDCT | ✅ n=44, identical, sv4 | sv2, 1579+34=1613, 9 STL | 559822 vs 560021 (**0.036%**) | ✅ 1613 | rc=0 (200 cap) | ✅ 4/4 <0.5 °C (max 0.015) |
| Liquid | ✅ n=13, identical, sv4 | sv2, 24+16=40, 6 STL | 74754 vs 74754 (**exact**) | ✅ 40 | mesh-only baseline | n/a — `.liquidrun` semantic ✅ |

All mesh deltas are within the §9.4 tolerance (0.5%). Registry parity is exact for
all 5 (same 52/372/157/1613/40 the cfdrun V2 toolchain proved in P3 T7). Every case's
V3→V4 save/reload produced a structurally identical scene fingerprint and a clean GPU
render (screenshots + `scene_readback.txt` per case).

## Solve depth (user decision 2026-06-14)

Solves were **capped (~200 steady iterations)** as a convergence + sanity check, not the
strict 0.5 °C parity gate (the locked D-decision was full solve; the user relaxed it for
Plan 4 to save hours). Base_leakage happened to run to 1000 (baseline-equivalent) and
clears the strict gate (max 0.034 °C); STDCT clears it even at 200 steps (max 0.015 °C);
Shinaide/CMI each have one sensor that has not fully converged at the capped depth
(0.798 / 0.812 °C) — expected for a partial steady solve and flagged as WARN, not fail.
The cap is applied by patching `meshGeometry.simulationConfig.endTime` (the runner reads
the transitional blob, not the top-level `simulationConfig`).

## Tracked-project conversion (P4 T7)

After 5/5 passed, the 20 tracked top-level `.cfdproj` files were batch-converted to
schemaVersion 4 (commit `9f96af9`) via `src/frontend/scripts/migrate_cfdproj_to_v4.py`.
`case/Slab_dataset/*` is **excluded** — it stays on-disk v2 by design (spec §5.4/§11);
those projects still open via migrate-on-load, and their v1 `.cfdrun` need a one-time
editor re-export to run under the V2 runner.

## Reference docs (P4 T8)

`docs/reference/index.md`, `cfdproj-schema.md`, `cfdrun-schema.md`,
`docs/user-guide/cfdproj-format.md`, `cfdrun-format.md` updated: V4/V2 marked current,
v3/v1 superseded, Slab_dataset re-export note added (commit `d9141b2`).

## Harness bugs surfaced & fixed during acceptance

1. `/api/read-file` is GET `?path=` (not POST) + browser session needs an explicit login fetch.
2. `prepare-case` body shape: `cfdrunJson`(string) + `stlFiles`(base64 dict) + `cfdrunFilename`.
3. Capped solve must patch `meshGeometry.simulationConfig.endTime` — the runner validates
   `CFDExport.model_validate(meshGeometry)`, so the top-level `simulationConfig` is ignored.
4. `sensorResults/*.json` land in `log/<timestamp>/sensorResults/`, not `case/sensorResults/`.
5. Per-case baseline dir naming tolerates `_`/`-` (`CMI_China` registry vs `CMI-China` dir).
6. Cap patches the actual `model/*.cfdrun` (e.g. `<proj>_fixed.cfdrun` from `getCfdrunFilename()`).
7. Screenshot: re-auth + dismiss the `#auth-overlay` (short-lived token) before capture.

**Conclusion:** the cfdproj V4 / cfdrun V2 refactor passes the final acceptance gate.
The four-plan effort (P1 infra/migrators → P2 in-memory V4 adoption → P3 cfdrun V2
toolchain → P4 acceptance + conversion + docs) is complete on
`refactor/cfdproj-v4-cfdrun-v2`. Post-merge follow-up: `/sync-dcdtuni-dctwin`.
