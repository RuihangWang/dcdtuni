# Design: .cfdproj V4 + .cfdrun V2 format refactor

- **Date:** 2026-06-12
- **Status:** Approved in design review (pending spec review)
- **Branch:** `refactor/cfdproj-v4-cfdrun-v2`
- **Schema proposals (amended by this effort):** `docs/reference/cfdproj-v4-schema.md`, `docs/reference/cfdrun-v2-schema.md`
- **Related:** `docs/superpowers/specs/2026-05-31-cfdproj-v3-structure-design.md` (the v3 playbook this reuses), `docs/superpowers/specs/2026-06-12-frontend-modularization-audit.md` (long-term frontend roadmap)

## 1. Context

The current formats grew incrementally and have a split-brain problem:

- `.cfdproj` (schemaVersion 3, since 2026-05-31) is a verbatim `JSON.stringify(state)` of the whole editor state, including UI-only fields (`ui.*`, `ui.session`). Geometry lives in a flat `construction[]` array discriminated by `kind`.
- `.cfdrun` ("version 1.0", never bumped) is built by `buildCfdrunJson()` (~550 lines inside `STL_generate.html:31749-32299`), validated by Pydantic `CFDExport` with `extra="allow"` — many load-bearing fields (server `faces` dicts, ACU `coolingModel`, top-level `liquidCooling`, `blockMeshBounds`, `refinementZones`) are undeclared riders with zero validation.
- `STLGenRunner._build_bridge_bc()` (`stlgen_runner.py:3117-3690`) then **re-derives** all boundary bindings from the device manifest, so BC derivation rules live half in JS, half in Python.

Two colleague-authored proposals (`cfdproj-v4-schema.md`, `cfdrun-v2-schema.md`, staged 2026-06-10) define the target: `.cfdproj` V4 as a dclib-Room-style editable domain model, `.cfdrun` V2 as a frozen per-run manifest with precompiled `boundaryBindings`. The proposals are drafts; this design amends them where they were immature (user authorization 2026-06-12).

## 2. Decisions log (user-approved)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Scope | Implement V4/V2 formats end-to-end now; frontend code modularization is documented-only (separate audit doc) |
| D2 | Verification depth | Full pipeline: load → save/reload → export → mesh → parallel solve, for all 5 target cases, via WebGL browser-use |
| D3 | Compatibility | Migrate-on-load (v1/v2 → v3 → v4 chain), save writes V4 only; runner consumes only `.cfdrun` V2, old manifests regenerate by re-export |
| D4 | Schema docs | The V4/V2 proposal docs may be edited; amendments in §5/§7 are written back into them |
| D5 | `.cfdrun` generator location | **In-browser JavaScript** (new `js/cfdrun_export.js` module). Rationale: `.cfdrun` must snapshot *live* UI state at export/mesh/solve time — unsaved edits (e.g. a changed server power) must flow into the run, while `.cfdproj` only changes on explicit user save |
| D6 | V4 depth | **Full in-memory adoption**: the runtime editor state itself becomes V4-shaped (not a file-boundary adapter) |
| D7 | Schema isolation (2026-06-12 follow-up) | Each schema lives in its **own dedicated file**, separate from migration/translation logic: frontend `js/cfdproj_schema.js` + `js/cfdrun_schema.js`; backend gets a dedicated `.cfdrun` **reader/adapter** module that resolves the manifest *and* its STL files, so schema knowledge never mixes with runner orchestration context |

## 3. Goals / non-goals

**Goals**

1. `.cfdproj` V4 on disk and in memory: `schemaVersion/uid/name/meta/geometry/models/constructions/instrumentation/runDefaults`, uid-keyed construction dicts, unified `cooling` semantics, `PatchSurface.perforation`.
2. `.cfdrun` V2: frozen run manifest with `inputs/geometryExport/boundaryBindings/sourceTerms/porousBindings/meshConfig/simulationConfig/postprocessConfig/artifacts`, generated in-browser, strictly validated by a new Pydantic `CfdrunV2` schema (no `extra="allow"` riders).
3. Runner consumes only V2 through a dedicated reader/adapter (`cfdrun_reader.py`) that resolves the manifest and its STL files; `_build_bridge_bc` derivation is deleted; `fields.py` bc_type → OpenFOAM mappings unchanged.
4. Schema isolation (D7): each schema in exactly one dedicated file per side — `js/cfdproj_schema.js`, `js/cfdrun_schema.js`, `cfdrun_v2_schema.py` — with migration/translation/orchestration logic in separate modules.
5. Lossless v3 → v4 migration with lockstep JS/Python migrators and a parity test (the v3 playbook).
6. All 5 verification cases pass the full gate against pre-refactor baselines, loaded and verified exclusively through GPU WebGL browser-use (§9.4).

**Non-goals**

- Splitting `STL_generate.html` into modules (audit doc only; new format code does land in `js/` modules).
- Fixing the already-broken air↔liquid coupled path (`inheritLiquidResults` — stale paths/signatures predate this refactor; field is carried, repair deferred).
- Changing the `.liquidrun` format or any of `backend/liquidcooling/` (its ~200 tests act as a regression net).
- Regenerating `case/Slab_dataset/` artifacts (1001 v2-era `.cfdproj`, 1000 v1 `.cfdrun`). Projects load via the migration chain; stale `.cfdrun` regenerate on demand, out of scope.
- dctwin sync (`/sync-dcdtuni-dctwin` after merge, separate task).

## 4. Architecture and data flow

```text
.cfdproj V4 (user-saved document; explicit save only)
   │  load: migrate chain v1/v2 → v3 → v4 → normalizeState (V4-shaped runtime state)
   ▼
editor runtime state (V4 shape: state.constructions.*, state.runDefaults, ...)
   │  user clicks Export / Mesh / Solve
   ▼
js/cfdrun_export.js translators (read LIVE state, not the saved file)
   ├─ STL export (unchanged engine) ──► case/<name>/Mesh/constant/triSurface/*.stl
   └─ CfdrunV2 JSON ──► POST (cfdrunJson) ──► server _write_active_cfdrun
                                                  └─ fills artifacts.* paths server-side
   ▼
case/<name>/model/<name>.cfdrun  (schemaVersion: 2)
   ▼
STLGenRunner: CfdrunV2.model_validate → bindings loader → BridgeBCRegistry
   → snappyHexMesh dicts → fields.py 0/ fields → fvOptions → mesh/solve/harvest
```

Core contract (written into both schema docs):

- **`.cfdproj` = the saved document.** Changes only when the user saves. Holds the editable domain model plus editable run defaults. Never consumed by the OpenFOAM runner.
- **`.cfdrun` = a frozen snapshot of live editor state** at the moment a run is triggered. Regenerated on every export/mesh/solve. The runner consumes nothing else.
- Data priority during translation: `inputs` (live run panel) > construction instance fields > `models` library defaults > backend defaults.

## 5. `.cfdproj` V4 format

Top level: `schemaVersion: 4`, `uid` (generated at first migration, stable thereafter), `name`, `meta`, `geometry`, `models`, `constructions`, `instrumentation`, `runDefaults`.

### 5.1 Amendments to `cfdproj-v4-schema.md`

| # | Amendment | Rationale |
|---|-----------|-----------|
| A1 | Add top-level `runDefaults` `{meshConfig, simulationConfig, liquid:{enabled, solver, inheritResults}}` | The proposal evicts `simulation` with no persistent home; users expect saved mesh/solver settings. `.cfdrun` freezes these at export; the runner still never reads `.cfdproj` |
| A2 | `ui` is dropped entirely; UI session prefs persist in browser `localStorage` keyed by project `uid` | Proposal suggested a sidecar file; YAGNI. UI state is on-the-fly, not document content |
| A3 | `models` buckets beyond `geometryModels` are **reserved** (documented, may be empty). `model.racks/acus` templates → `models.geometryModels.{racks,acus}`; liquid `specifications` → `models.coolingModels.{coldPlates,pipes,pumps,heatExchangers,valves}`; `liquidCooling.fluid` → `models.materialModels.fluids` | Populates the library with what actually exists; avoids speculative structure |
| A4 | Coordinate convention pinned: **Y-up, room plane vertices in x/z, all lengths in meters**; `geometry.quaternion` is authoritative for orientation when present (`orientation` degrees optional, derived). `meta.inputUnit` is retained (UI input parsing only) | Proposal left dclib's x/y-plane ambiguity unresolved |
| A5 | `validId` dropped; `uid` = the existing frontend `id` (`m_N`), the single stable identifier | Preserves every embedded reference (`pipe.startPortId = "<id>.supply"`, `valve.connectedRackId`, `fromModel`) across migration with zero rewriting |
| A6 | `ObjectMeta` gains `enabled: boolean` (and keeps `name`, `description`, `tags`) | v3 `enabled` must round-trip; disabled-but-exported room boundary items (Base_leakage) must keep working |
| A7 | Add the §4 format-philosophy contract (saved document vs live snapshot) verbatim | User's core mental model, previously implicit |

### 5.2 Bucket map (v3 `construction[]` kind → V4 `constructions.*` dict)

| v3 `kind` | V4 bucket | Notes |
|-----------|-----------|-------|
| `rack` | `constructions.racks` | servers nest under `racks.<uid>.constructions.servers` |
| `acu` | `constructions.acus` | patch roles become `geometry.supplyFace/returnFace/extraFaces` |
| `baffle` | `constructions.baffles` | |
| `wall` | `constructions.walls` | |
| `partitionWall` | `constructions.partitionWalls` | |
| `floor` | `constructions.floors` | `roomBoundaryType` → `cooling.roomBoundaryLocation` |
| `box` | `constructions.boxes` | |
| `custom` | `constructions.customMeshes` | `customSTLBase64` → `customMesh.stlBase64` (immutable) |
| `cdu` | `constructions.cdus` | `item.liquidCooling` decomposes per proposal §6.10 |
| `pipe_segment` | `constructions.pipeSegments` | `pipe.*` → `geometry/cooling` split per proposal §6.16 |
| `pipe_junction` | `constructions.pipeJunctions` | |
| `heat_source` | `constructions.boxes` with `cooling.boundaryMode: power/fixedTemperature` | dedicated kind retired |
| `crh`, `cold_plate` (legacy, dormant) | migrated into `boxes` / dropped if unreferenced | confirmed dormant by audit; migrator logs each occurrence |
| `state.liquidCooling.piping` | `constructions.liquidFlowNetworks.<uid>` | `{autoGenerated, mainPipeSpec, branchPipeSpec}`; `results` is a run artifact, dropped from the document |
| `probeData[]` | `instrumentation.probes` (uid-keyed) | |
| `sensorData` | `instrumentation.measurements` | |

### 5.3 Mechanical field rules (apply to every construction object)

1. `id` → `uid` (verbatim; also the dict key).
2. `anchor{x,y,z}` → `geometry.location{x,y,z}`.
3. `base{W,D,H}` → `geometry.size{x:W, y:H, z:D}` (Y-up; the W→x / H→y / D→z axis invariant is locked by golden round-trip tests + screenshot diffing before any sweep starts).
4. `transform.quaternion` → `geometry.quaternion`; `transform.scale` → `geometry.scale`.
5. `enabled`, `name`, `opacity`, `showAnchorMarker` → `meta.*` (A6; `opacity`/`showAnchorMarker` are editor presentation but document-worthy, kept under `meta.display`).
6. `cfd` → `cooling` (+ surface-level placement: `cfd.patchPhysics[patchId]` distributes onto the owning `PatchSurface`; `cfd.openings[]` → `geometry.openings[]`; `cfd.defaultBC*` → `cooling.defaultBoundaryType` + `PatchSurface.perforation`; `cfd.leakage` → `cooling.defaultLeakageSigma` + `cooling.leakageEnabled`).
7. All `perforated{shape,holeDiameterMm,openAreaPct}` blocks (baffle-level, patchPhysics-level, opening-level, defaultBC-level, rack door) → `perforation` on the corresponding `PatchSurface`/opening (proposal §5.4) — same object shape, new attachment point.
8. Rack: `rack.unitCount` → `geometry.slot`; `rack.equipments[]` → `constructions.servers` (uid-keyed; `startUnit` → `geometry.slotPosition`, `spanUnits` → `geometry.slotOccupation`); `rack.partitions` → `constructions.partitions`; `rack.inletDoor/outletDoor` → `geometry.faces[]` entries with `perforation`; `rack.coolingType` → `cooling.type`; `rack.liquidManifold` → `cooling.liquidManifold` (shape unchanged); `eq.liquidCooling.cpCuPairs[]` → `servers.<uid>.constructions.coldPlates` (uid-keyed, fields preserved).
9. Pipe: `pipe.waypoints` → `geometry.waypoints`; `pipe.diameter` → `geometry.diameterM`; `pipe.spec` → `cooling.spec`; `pipe.color` → `cooling.loopRole` (`supply/return/unconnected`); `pipe.valves` → `cooling.valves`; `pipe.startPortId/endPortId/blindEnds/junctions` preserved under `cooling.connection` (port-id string format unchanged per A5).
10. Faces/patches: per-face `patches[]` entries become `PatchSurface[]` with `uid` = patchId, `solidName` filled at export time; `wallNames`/`solidRegions` preserved.

The migrator carries any unknown keys forward verbatim (same forward-compat stance as `migrateCfdprojV2toV3`) and logs every legacy-kind conversion.

### 5.4 In-memory adoption strategy (D6)

The runtime state adopts the V4 shape. Because `m.anchor`/`m.base`/`state.construction` are referenced at thousands of sites across `STL_generate.html` and 9 `js/` modules, the conversion proceeds as **staged sweeps with a temporary alias layer**:

1. `normalizeState`/`getInitialState`/model factories emit V4-shaped objects, and a transitional `installLegacyAliases(model)` defines non-enumerable getters/setters (`anchor` ⇄ `geometry.location`, `base.W` ⇄ `geometry.size.x`, `cfd` ⇄ `cooling`, …) so every old call site keeps working immediately. Non-enumerable ⇒ aliases never serialize into saves or undo snapshots.
2. A transitional `allModels()` helper (flattened `Object.values` over all `constructions` buckets) replaces `state.construction` iteration; `window.state.construction` itself becomes an alias getter during the transition.
3. Sweeps replace alias uses bucket-by-bucket (core/geometry → per-kind render+inspector code → liquid modules → 2D editor). Each sweep stage must pass the per-stage gate (§9.3).
4. Alias removal at the end, enforced by structural grep tests (the repo's existing `src/frontend/test/test_*_frontend.py` pattern): zero remaining `\.anchor\b` / `\.base\.[WDH]\b` / `state\.construction\b` matches outside the migrator, plus a runtime dev-mode counter asserting aliases were never hit during the e2e script.
5. Undo (`captureStateSnapshot`/`_modelFingerprint`) and `_modelMap` adapt in stage 1 — `_modelMap` becomes a thin index over the uid-keyed buckets; fingerprints read `geometry.*`.

### 5.5 Dedicated frontend schema module: `js/cfdproj_schema.js` (D7)

The V4 document schema gets its own file, separate from migration logic: `src/frontend/web/js/cfdproj_schema.js` (IIFE + `window.cfdprojSchema` namespace + `module.exports` dual for node:test). It is the frontend's single source of truth for the document structure and owns:

- `SCHEMA_VERSION = 4` (re-exported by `cfdproj_migrate.js`, which depends on this module);
- the bucket registry (bucket names, kind → bucket map) and per-kind default factories;
- per-kind normalizers (the schema-shaped part of today's `normalizeState`/`createModelFromRaw` — `normalizeState` in the main file shrinks to orchestration that calls into this module);
- structural validation helpers (`validateDocument(doc)` returning path-addressed errors).

`STL_generate.html`, `cfdproj_migrate.js`, and `cfdrun_export.js` all consume the schema through this module; none of them re-declares structure knowledge.

## 6. `.cfdrun` V2 format and the JS translator modules

### 6.1 Module layout (D7: schema separate from translation)

Two new modules, both IIFE + `window` namespace + `module.exports` dual for node:test (the `cfdproj_migrate.js` pattern):

- **`src/frontend/web/js/cfdrun_schema.js`** — the V2 manifest schema only: `CFDRUN_SCHEMA_VERSION = 2`, section skeleton builders (`emptyManifest()`, `emptyArtifacts()`), the `bcType`/`role` enums, and `validateManifest(json)` (client-side structural validation before POST). No state access, no translation logic.
- **`src/frontend/web/js/cfdrun_export.js`** — the translators that read live state and fill the schema: per proposal §9.5 — `translateAcus`, `translateRackAndServers`, `translateOpenings`, `translateMonitors`, plus `assembleInputs`, `assembleGeometryExport` — each pure (state-in, fragment-out) and unit-testable under jsdom. Depends on `cfdrun_schema.js` (structure) and `cfdproj_schema.js` (document access helpers).

`buildCfdrunJson` logic in `STL_generate.html:31749-32299` is ported into `cfdrun_export.js` and the old function becomes a deprecated alias for one release, then removed.

`exportCFDProject` / mesh / solve entry points call the new builder; the POST body key stays `cfdrunJson`; `server.py` endpoints and `_write_active_cfdrun` are unchanged except for server-side filling of `artifacts.*` (see A11).

### 6.2 Amendments to `cfdrun-v2-schema.md`

| # | Amendment | Rationale |
|---|-----------|-----------|
| A8 | Generator pinned to in-browser JS reading live state (D5); philosophy section added | Was left open in the proposal |
| A9 | `bcType` enum extended with `nonfan_heat_flux` and informational `through_zone` | Both exist in `fields.py`/`bridge_bc.py` today; the proposal's table missed them |
| A10 | `StlSolidBinding` gains `areaM2` (browser-computed patch area) and optional `perimeterM` | The backend needs areas for flux/turbulence derivation without re-parsing STL triangles (today's `PatchGeometry` fallback remains for sanity-checking) |
| A11 | `artifacts.*` paths are filled **server-side** by `_write_active_cfdrun` at write time | The browser doesn't reliably know absolute case paths; the server does |
| A12 | §13 "backend accepts both old and V2 short-term" replaced by hard cutover: runner accepts only `schemaVersion: 2`, otherwise raises with "re-export from the editor" guidance | Decision D3; consistent with the repo's no-silent-fallback stance |
| A13 | `sourceProject.contentHash` = SHA-256 of the serialized live V4 state at export time (computed via SubtleCrypto), explicitly documented as potentially differing from the saved file's hash | The live-snapshot contract makes "hash of the .cfdproj file" wrong |
| A14 | `inputs.servers.*` carries `inheritLiquidResults` semantics unchanged-but-deferred; coupled-path repair is explicitly out of scope | Path already broken pre-refactor (stale `liquid_results.json` location + `run_liquid_solve` signature mismatch) |

### 6.3 Content mapping (current v1 → V2)

| Current `.cfdrun` | V2 destination |
|---|---|
| `racks.*.servers[*].physics` (fan + the undeclared nonFan `{inlet,outlet}` shape) | `inputs.servers.<uid>` (power/flow/tempRise/bcMode/liquidPercentage; nonFan face BCs become declared fields) + `boundaryBindings` + `sourceTerms` |
| `acus.*.supplyFaces/returnFaces[*].physics` (+ undeclared `coolingModel`) | `inputs.acus.<uid>` (declared `coolingMode/setPoint/coolant/performanceTable`) + `boundaryBindings` (incl. `parameters.returnPatchName` pairing) |
| server `faces` dicts, rack `frameSolidNames/partitions/doors`, baffle/floor solids | `geometryExport.solids` (`solidName → patchName → objectPath → surfaceUid → role`, + `areaM2`) |
| `baffles/floors` physics, perforated configs, through-zone groups, leakage | `porousBindings` (perforation/sigma/seedConfig) + `boundaryBindings` |
| `heatSources`, nonFan heat, RDHX placeholders | `sourceTerms` |
| `sensorPoints` | `postprocessConfig.probes` |
| `meshConfig` + undeclared riders (`blockMeshBounds`, `refinementZones`, acu/box/baffle levels) | `meshConfig`, all fields declared |
| `simulationConfig` + `startFrom` rider | `simulationConfig`, all fields declared |
| top-level `liquidCooling`, `inheritLiquidResults` | `inputs.cdus.<uid>` + `inputs.liquid{enabled, inheritResults}` |

### 6.4 Backend consumption (D7: dedicated schema + reader/adapter modules)

Two new dedicated modules under `src/backend/aircooling/runner/`, so schema knowledge and manifest resolution never mix with runner orchestration:

- **`cfdrun_v2_schema.py`** — Pydantic v2 `CfdrunV2` models only, with **declared fields only** (`extra="forbid"` on new models; every today-rider promoted). `model_validator`s keep the unique-server-id/patch-name invariants. No I/O, no filesystem access. (The existing `cfdproj_schema.py` — long misleadingly named, since it validates `.cfdrun`, not `.cfdproj` — is retired with the v1 path; the new naming fixes that confusion.)
- **`cfdrun_reader.py`** — the reader/adapter that resolves a run: discovers the single `.cfdrun` in `model/` (keeping the existing zero→`FileNotFoundError` / 2+→`RuntimeError` contract), enforces `schemaVersion == 2` (A12 error otherwise), validates via `CfdrunV2`, **resolves `geometryExport` against the actual STL files** (every `stlFiles.*.path` exists under `triSurface/`, every `solids.*.solidName` is present in its STL, `contentHash` cross-check, `areaM2` sanity-check against `PatchGeometry`), and materializes the run: `BridgeBCRegistry` from `boundaryBindings`/`porousBindings` (master/slave cyclic registration included), fvOptions inputs from `sourceTerms`, probes/monitors from `postprocessConfig`. It exposes one typed object (`ResolvedRun`) to the runner.

`STLGenRunner` consumes only `ResolvedRun` — `_build_bridge_bc`'s 570 lines of derivation are deleted, replaced by the adapter call. `fields.py` (bc_type → OpenFOAM patchField writers), snappy region naming, turbulence derivation, and solver orchestration are untouched.

The translator JS becomes the single home of "device → role/bcType/parameters" rules; Python keeps the single home of "bcType → OpenFOAM fields" rules. The split-brain is resolved by making the boundary explicit, and each side's schema lives in exactly one dedicated file (`js/cfdrun_schema.js` ↔ `cfdrun_v2_schema.py`).

## 7. Liquid path

- `.liquidrun` format, `backend/liquidcooling/`, and the solve trigger path are **unchanged**.
- Frontend liquid modules (`liquid_graphgen.js`, `liquid_cooling.js`, `liquid_mode.js`, `liquid_pipe_renderer.js`, `liquid_topology_editor.js`) update in the §5.4 sweep: kind-filters → `constructions.cdus/pipeSegments/racks` dicts; field paths via the mechanical rules. Port-id strings and name-derived node IDs (`<rack>_mf_supply`, matched by `backend/liquidcooling/runner.py:336`) survive because uids and names are preserved (A5).
- Gate: the regenerated `.liquidrun` for `case/Liquid/Liquid.cfdproj` must be semantically identical to the pre-refactor baseline (same node/edge/rack sets and parameters; ordering-insensitive diff), and the solve must reproduce baseline results.

## 8. Migration implementation

- `migrateCfdprojV3toV4(raw)` added to `js/cfdproj_migrate.js`; chain on load: v1/v2 → v3 (existing) → v4 (new). `CFDPROJ_SCHEMA_VERSION` → 4. Loader rejects `schemaVersion > 4` with a clear dialog.
- Python mirror `migrate_cfdproj_v3_to_v4()` in `src/frontend/scripts/cfdproj_migrate.py`; `load_cfdproj()` returns V4. Golden parity test: both migrators applied to all 5 target cases (+ the v2-era fixtures) must produce byte-identical canonical JSON.
- Batch tool: extend `src/frontend/scripts/migrate_cfdproj_v2_to_v3.py` lineage with a v3→v4 batch CLI; tracked projects (~36 + targets) convert **only after** the §9 verification gate passes. `Slab_dataset` stays on-disk v2 by design.
- `.cfdrun`: no migrator (D3/A12).
- `ui` handling on load of v3 files: section dropped; `displayMode`/snap prefs seeded into `localStorage` once.

## 9. Testing and verification (TDD)

### 9.1 Phase 0 — infrastructure and baselines

- Add a `dev` extra (`pytest`, `pytest-timeout`) to `pyproject.toml`; install into the `dcdtuni` env; wire into `setup_env.sh`. (Audit finding: pytest is currently not installed anywhere.)
- Add skip-if-server-down guards to the two live-server runtime tests so full suite runs are possible.
- Generate **pre-refactor baselines** for all 5 cases via server API/browser-use (Shinaide, CMI-China, STDCT have never been exported): for each air case — STL solid inventory + per-solid SHA, `.cfdrun` v1 JSON, checkMesh metrics (cells, regions), parallel-solve results (final residuals, ACU return temps, heat balance per `/dc-analyze`); for Liquid — `.liquidrun` + `liquid_result.json`. Store under `docs/superpowers/specs/baselines/2026-06-12-v4-refactor/` (small JSON digests, not full meshes).

### 9.2 Test-first inventory

| Layer | New tests (written before implementation) |
|---|---|
| JS schema modules | `src/frontend/test/test_cfdproj_schema.mjs` + `test_cfdrun_schema.mjs` — bucket registry/kind map, default factories, `validateDocument`/`validateManifest` accept the 5 migrated cases and reject mutated counter-examples |
| JS migrator | `src/frontend/test/test_cfdproj_migrate_v4.mjs` — v3→v4 unit cases per kind, golden round-trips of the 5 case files, unknown-key carry-forward, legacy-kind logging |
| JS translators | `src/frontend/test/test_cfdrun_export.mjs` — per-translator fixtures (ACU pairing, fan/nonFan servers, perforation→porousBindings, probes), full-document golden vs baseline-derived expectations |
| Python migrator | extend `test_cfdproj_migrate_py.py` + `test_cfdproj_migrate_parity.py` to v4 |
| Pydantic schema | `src/backend/tests/test_cfdrun_v2_schema.py` — strict validation, rider-promotion completeness (a V2 analogue of `test_schema_completeness.py`), rejection of v1 manifests with the A12 message |
| Reader/adapter | `src/backend/tests/test_cfdrun_reader.py` — manifest discovery contract preserved (zero/2+ errors), STL resolution failures (missing file, missing solid, hash mismatch) raise path-addressed errors, boundaryBindings→BCSpec equivalence against the Base_leakage baseline registry dump, porous master/slave, sourceTerms→fvOptions |
| Structural | grep-based alias-removal tests (§5.4.4); updated `e2e_tests.sh` assertions |

### 9.3 Per-stage gate (after every §5.4 sweep stage and every backend milestone)

`node --test` suite + `pytest src/backend/tests src/frontend/test -q` + browser-use WebGL load + screenshot-readback of Base_leakage and Liquid (per `/browser-verify`: programmatic eval AND multimodal screenshot before any claim).

### 9.4 Final acceptance gate (all 5 cases, per `/verification-before-completion`)

**Mandatory tooling:** all case loading and verification — here and in §9.3 — runs through the **GPU-accelerated WebGL browser-use** (NVIDIA Vulkan + `--headless=new`, installed/repaired via `/install-WebGL-browse-use`, procedure per `/browser-verify`). Every verification session starts with the GPU sanity check: the unmasked WebGL renderer must report `ANGLE (NVIDIA … Vulkan …)` — never `llvmpipe`/`SwiftShader` — and a `chrome --type=gpu-process` must appear in `nvidia-smi`. Every confirmation point pairs a programmatic eval with a screenshot that is read back multimodally before any claim. Plain headless Playwright or `curl`-only flows do not satisfy this gate (REST calls are permitted only for triggering mesh/solve jobs, per the repo's debugging rules).

For each of `case/Base_leakage/Base_leakage.cfdproj`, `case/Shinaide/Shinaide.cfdproj`, `case/CMI-China/CMI_China.cfdproj`, `case/STDCT/STDCT.cfdproj`, `case/Liquid/Liquid.cfdproj`, driven end-to-end through WebGL browser-use:

1. Load original v3 file → screenshot → scene-graph eval (model counts/positions).
2. Save as V4 → reload V4 → screenshot pixel-diff + state deep-diff vs step 1 (identical scene).
3. Export STL + `.cfdrun` V2 → STL solid-name set identical to baseline, per-solid SHA equal (export order preserved); V2 manifest semantically covers the baseline v1 manifest (mapping table §6.3).
4. Mesh via `/api/run-mesh` → checkMesh cells within 0.5% of baseline, identical region count.
5. Parallel solve (`mpirun`, never serial) → converged; ACU return temps within 0.5 °C of baseline; heat balance consistent per `/dc-analyze`. Liquid case: `.liquidrun` semantic-equal, solve results match baseline tolerances.
6. Only after 5/5 pass: batch-convert tracked projects, commit conversions separately.

## 10. Error handling

- Loader: `schemaVersion > 4` → blocking dialog naming the file's version and the supported range; migration exceptions surface the failing object path.
- Export: translator validation (missing supply face, zero-flow fan server, dangling port refs) → Operation Log errors, export aborted; same checks the current builder does, now centralized and unit-tested.
- Runner: Pydantic `ValidationError` rendered into the SSE job log with the JSON path; v1 manifest → A12 message. No silent fallbacks anywhere (repo stance, commit 7ae60e4 lineage).

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Axis-mapping mistake in `base{W,D,H}` → `size{x,y,z}` | Locked by golden round-trips + screenshot diffs before sweeps start (§5.3.3) |
| Sweep regressions in drag/undo/render hot paths | Alias layer keeps old call sites correct between stages; per-stage gate (§9.3); fingerprint/undo adapted first |
| STL export drift breaking baseline SHA equality | Export engine untouched; solid naming functions untouched; gate 3 catches any accidental reorder |
| `/api/browser/exec-js` automation and skills (dc-builder) call `window.*` by name | Window facade preserved; deprecated aliases (`buildCfdrunJson`) kept one release; skills updated in the same PR where they reference renamed state paths |
| Solver numeric drift on re-mesh | Tolerances in gate 4/5 (0.5% cells, 0.5 °C) chosen above round-off, below physical-change scale |
| `Slab_dataset` v1 `.cfdrun` become unusable by the runner | Documented; projects still load via chain; batch re-export procedure documented in the migration section of the reference docs |
| dctwin divergence until sync | Format change is self-contained in this repo; `/sync-dcdtuni-dctwin` scheduled post-merge |

## 12. Deliverables and sequencing

1. **Docs first:** amended `cfdproj-v4-schema.md` / `cfdrun-v2-schema.md` (A1–A14), this spec, the modularization audit doc — committed together.
2. **P0** test infra + baselines (§9.1).
3. **P1** `js/cfdproj_schema.js` + migrators (JS + Python + parity + goldens) — file-level, TDD.
4. **P2** in-memory V4 adoption sweeps with alias layer (§5.4) — per-stage gates.
5. **P3** `js/cfdrun_schema.js` + `js/cfdrun_export.js` translators + `cfdrun_v2_schema.py` + `cfdrun_reader.py` adapter — TDD.
6. **P4** final acceptance gate on all 5 cases (§9.4); batch conversion; reference-doc updates (`docs/reference/index.md`, `cfdproj-schema.md`/`cfdrun-schema.md` superseded notices, `docs/user-guide/cfdproj-format.md`).
7. Implementation plan via `superpowers:writing-plans` details P0–P4 into tasks.
