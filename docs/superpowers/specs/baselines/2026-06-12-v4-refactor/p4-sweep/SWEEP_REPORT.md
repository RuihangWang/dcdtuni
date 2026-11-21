# All-Case V4→V2 Pipeline Sweep — Final Report

**Date:** 2026-06-14
**Branch:** `refactor/cfdproj-v4-cfdrun-v2`
**Scope:** Every tracked case under `case/` run through the full pipeline:
GPU WebGL load → V3/V4→V4 save/reload (scene-identity) → V2 `.cfdrun` export →
`meshOnly` mesh. Baseline-free **survival** test (no parity assertions; that is
Plan 4's separate 5-case acceptance).

**Harness:** `scripts/p4_acceptance/sweep_case.sh` (per-case, browser re-open hardened).
**Driver evidence:** per-case dirs under this folder + `results.tsv`.

---

## Result Matrix (15 cases)

| Case | Models | Status | Bindings | Mesh cells | Notes |
|------|-------:|--------|---------:|-----------:|-------|
| LiquidSingle | 4 | ✅ PASS | 5 | 2,472 | |
| SingleRack | 8 | ✅ PASS | 13 | 31,438 | |
| Tri_angle | 8 | ✅ PASS | 27 | 39,825 | triangular room |
| Polygon_Room | 14 | ✅ PASS | 18 | 106,605 | non-rectangular |
| Base_coldAisleIso | 14 | ✅ PASS | 33 | 111,770 | cold-aisle containment; passed after schema fix (3 SimulationConfig fields) |
| Slab | 23 | ✅ PASS | 46 | 184,636 | raised floor |
| DAML-Testbed | 29 | ✅ PASS | 39 | 218,956 | lab testbed |
| Shinaide_Liquid | 32 | ✅ PASS | 306 | 263,492 | liquid cooling |
| paintToDC1 | 38 | ✅ PASS (after fix) | 180 | 628,293 | was BCFAIL (65 bindings) — fixed by deduping server uids; see Resolution |
| GTC | 75 | ✅ PASS | 1,646 | 853,536 | |
| Base2-9 | 169 | ✅ PASS | 1,753 | 2,740,039 | |
| Base604 | 177 | ✅ PASS | 4,885 | 2,706,094 | high density |
| Alibaba-hongchou | 287 | ✅ PASS | 10,102 | 4,162,652 | very large |
| Alibaba-sige | 324 | ✅ PASS | 8,001 | 4,135,155 | very large |
| CMI-Guichou | 343 | ✅ PASS | 8,703 | 4,848,375 | extra-large, 46 ACUs |

**15/15 PASS after the paintToDC1 fix.** All cases load to schemaVersion 4 with
scene identity preserved (`equal=True`), export to schemaVersion 2, and mesh
cleanly — across the full size range from 2.5k to 4.85M cells. The lone failure
was an isolated **pre-existing input-data bug** (not a V4/V2 refactor regression);
it is now resolved — see below.

---

## Root Cause — paintToDC1 (the one failure)

**Symptom (exact):**

```
STLGen runner failed: server-shaped patch 'Rack_Col1_1_fan_server_1_inlet_patch'
reached the generic T BC fallthrough — it was NOT registered in the bridge BC
registry (_imported_bc_data). Fan-server patches require a bridge BC entry
(fan_inlet/fan_outlet) so the temperature-rise BC can be built ...
```

**Where it fails:** *After* meshing. `snappyHexMesh` finished with no errors
(628,293 cells); the failure is in the post-mesh BC/field build step. So the
geometry and mesh are fine — only the boundary-condition wiring is incomplete.

**Why:** The V2 translator (`buildCfdrunJsonV2`) keys `boundaryBindings` /
source joins by `source.modelUid` (= the server's `uid`). paintToDC1 (the
hand-drawn `case_00_sketch` reference) contains **duplicate server uids** across
its racks. When two servers share a uid, the join collapses to a single entry, so
the fan-server patches of the colliding duplicates never receive a binding. At
mesh time those patches exist in the STL but have no registry entry, and the
field generator raises rather than silently emitting a wrong wall-T BC.

This is the runner behaving correctly — it refuses to guess a fan-server BC. The
defect is in the source project's data (non-unique uids), which predates the
refactor.

**Isolation:** No other case in the sweep has duplicate uids; all 14 others bind
and mesh cleanly. The failure is specific to paintToDC1's data.

---

## Resolution (2026-06-15) — repair data + harden translator

Decision taken: **repair the data AND harden the translator** (both).

**Why repair, not rack-scope the join:** an audit of every passing case settled
the design intent. All 14 passing projects — including CMI-Guichou (1,674
servers) — have **globally-unique** server uids. Only paintToDC1 reused one
(`e_3` ×24). Global uniqueness is the established invariant; paintToDC1 was a
template rack duplicated 24× without re-uniquifying. Rack-scoping the join would
also have been incomplete — `inputs.servers` is a second uid-keyed dict that
collapses identically — so the correct, complete fix is to restore the invariant.

1. **Data repair** — `case/paintToDC1/paintToDC1.cfdproj`: the 23 colliding
   `e_3` servers were given unique uids (`e_3__<rackUid>`), updating both the
   `constructions.servers` dict key and each server's `.uid` (the uid has no
   other in-rack references). 30/30 server uids now unique. Re-run through the
   full pipeline: load (sv=4, scene identity `equal=True`) → export **180
   bindings** (was 65 — the collision had dropped ~115 fan-server patches) →
   mesh **rc=0, 628,293 cells. PASS.**

2. **Translator guard** — `src/frontend/web/js/cfdrun_export.js`:
   `assertUniqueServerUids(state)` runs at the top of `assembleInputs` and throws
   an actionable error if any server uid repeats across racks. A future
   duplicate-uid project now fails loudly at export instead of via the cryptic
   post-mesh generic-T fallthrough. Covered by two new tests in
   `src/frontend/test/test_cfdrun_export.mjs` (TDD red→green).

**Result: 15/15 PASS.**
