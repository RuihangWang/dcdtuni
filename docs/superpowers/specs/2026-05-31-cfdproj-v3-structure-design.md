# CFDProj v3 Structure Refactor — Design

**Date:** 2026-05-31
**Branch:** `refactor/cfdproj-v3-structure`
**Status:** Approved (pending spec review)
**Reference example:** `case/base_leakage/Base_leakage.cfdproj`

---

## 1. Problem

The current `.cfdproj` save format (`schemaVersion: 2`) has a flat, ~28-key top level where
concerns are blurred together:

- `models[]` is one flat array mixing *every* device kind (rack/acu/baffle/wall/floor), and each
  RACK/ACU instance carries its **full** physics inline. 142 identical racks means 142 duplicated
  copies — there is no notion of a reusable equipment definition.
- `cfdRoom` (80 keys) is named after the room but is really the **mesh + solver run config**, with
  a few genuine room-geometry fields (`height`, `roomTemperature`) buried inside it.
- `sensorPoints[]` is misnamed: these are CFD **sample/query points** (probes), not real sensors.
- There is no place for a human **description** of the model, and no place for **real-world measured
  sensor readings** (which the user wants as a frontend input distinct from probes).
- View/editor/session state (`view`, `snap*`, `selection`, `groups`, `dctwinConfig`, `treeCollapsed`,
  `preview*`, `_*`) sits at the top level next to model data, adding to the clutter.

The goal is a clearer, self-documenting structure organised around four user-facing concepts —
**meta / model / construction / sensorData** — plus a tidy split of run-config and editor state.

## 2. Goals & Non-Goals

### Goals
1. Restructure the on-disk `.cfdproj` into named top-level sections (schemaVersion 3).
2. Introduce a **per-project reusable equipment library** (`model`) for RACK and ACU.
3. Add a free-text project **description** editable at the model-tree root node.
4. Add a **real-world sensor data** section, distinct from the renamed probe data.
5. Mirror the new shape in the in-memory frontend `state` (full restructure).
6. Auto-migrate all existing v2 files (16 case projects + the dataset) losslessly on load.

### Non-Goals (explicitly out of scope)
- **No CFD runner / `.cfdrun` changes.** The export and `cfdproj_schema.py` (`CFDExport`) stay as-is
  (§7). Python that *reads* `.cfdproj` (tests, scoring, dataset) does change — but only by routing
  through a compat loader shim (§10), never the runner.
- **No global / cross-project library.** The library is per-project only; a global preset store is a
  possible future extension.
- **No solver coupling for `sensorData`.** It is a reference record only — stored, displayed, edited;
  never read by the runner or the export.
- **No live inheritance.** Instances are independent copies (template / copy-on-add), not live
  references to library entries.

## 3. Locked Decisions

| Decision | Choice | Consequence |
|---|---|---|
| Instance ↔ library relationship | **Template / copy-on-add** | Library entries are placement-free "spec sheets"; the add button stamps an independent full copy into `construction`; editing a library entry never touches existing instances. Each instance keeps a `fromModel` provenance tag (origin id only, no live binding). |
| Library scope | **Per-project** | The `model` library lives inside the cfdproj. No external store. |
| `sensorData` purpose | **Reference record only** | Metadata; no solver coupling, no validation tooling in v1. |
| Implementation depth | **Full in-memory restructure** | `state` mirrors the file 1:1; every reader is updated (≈840 references across the moved keys). Highest effort, cleanest result. |

## 4. Target Structure (schemaVersion 3)

```jsonc
{
  "schemaVersion": 3,

  // ① META — project identity + root-node settings + free-text description
  "meta": {
    "projectName": "Base_leakage",
    "exportFileName": "default",
    "inputUnit": "m",
    "description": "",                 // NEW — textarea at the model root node
    "room": {
      "bounds":  { /* x/y/z min-max */ },
      "polygon": [ /* footprint, or [] */ ],
      "height":  3.6,                  // moved from cfdRoom.height
      "temperature": 22.0              // moved from cfdRoom.roomTemperature
    },
    "case": { "path": "" }             // durable bit of dctwinConfig (env-specific)
  },

  // ② MODEL — per-project reusable template library (RACK & ACU only)
  "model": {
    "racks": {
      "<templateId>": {
        "label": "42U Rack — 1.6kW",
        "kind": "rack",
        "base": { "W": 0.6, "D": 1.0, "H": 1.867 },
        "faces": { /* per-face patch/naming config */ },
        "rack": { /* unitCount, doors, equipments, partitions, coolingType … */ },
        "cfd":  { "role": "rack", "servers": [ … ], "leakage": { … } }
        // NO id / name / anchor / transform / moveTool / rotateTool / enabled
      }
    },
    "acus": {
      "<templateId>": {
        "label": "ACU — 30kW",
        "kind": "acu",
        "base": { … },
        "faces": { … },
        "cfd":  { "role": "acu", "patchPhysics": { … } }
      }
    }
  },

  // ③ CONSTRUCTION — the model tree: concrete placed instances (replaces models[])
  "construction": [
    {
      "id": "m_1", "kind": "rack", "name": "Default_Rack_1", "enabled": true,
      "fromModel": "<templateId|null>",        // provenance only, NO live link
      "anchor": { … }, "transform": { … },     // placement (always per-instance)
      "base": { … }, "faces": { … }, "rack": { … }, "cfd": { … },
      // … all other current model fields preserved verbatim (opacity, cutouts,
      //     polygon, customSTLBase64, solidRegions, isRoomBoundary, …)
    }
    // … kind:"acu" / "baffle" / "wall" / "floor" instances also live here
  ],

  // ④a probeData — RENAMED from sensorPoints (CFD sample/query points; unchanged shape)
  "probeData": [ { "id": "p_1", "name": "p_1", "x": 2.15, "y": 3.1, "z": 0.89, "enabled": true } ],

  // ④b sensorData — NEW real-world dynamic measurements (reference record only)
  "sensorData": [
    {
      "id": "rs_1", "name": "Cold aisle A inlet",
      "quantity": "temperature",               // temperature|humidity|pressure|airflow|power|custom
      "unit": "C",
      "location": { "x": 2.1, "y": 1.5, "z": 0.9 },
      "bind": { "instanceId": null },          // optional tie to a construction instance
      "series": [ { "t": 0, "value": 24.1 }, { "t": 60, "value": 24.3 } ],
      "source": ""                              // free note (which BMS/CSV)
    }
  ],

  // ⑤ SIMULATION — mesh + solver run config (was cfdRoom, minus the room-geometry fields)
  "simulation": { /* meshBaseSize, refinement levels, solver, residuals, schemes, … */ },

  "liquidCooling": { /* unchanged, top-level */ },

  // ⑥ UI — editor / view / session state, regrouped off the top level
  "ui": {
    "displayMode": "air",
    "view": { … },
    "snapFace": true, "snapEdge": true, "snapGrid": false,
    "treeCollapsed": { … }, "selection": { … }, "groups": [ … ],
    "previewVisibility": { … }, "previewRegionVisibility": { … }, "previewRegionReverse": { … },
    "rackDetail": { "target": null, "returnMode": "3d", "showContext": false },
    "session": { /* ephemeral dctwinConfig: validated, activeTab, activeJobId,
                    showSeedHelper, meshReady, solveComplete, logRuns, liquidRuns */ }
  }
}
```

### Library template shape
A library entry is the kind-specific **reusable** subset of a model: `label` + `kind` + `base` +
`faces` + (`rack` for racks) + `cfd`. It explicitly **excludes** identity and placement
(`id`, `name`, `anchor`, `transform`, `moveTool`, `rotateTool`, `enabled`, `showAnchorMarker`).
Only parametric RACK/ACU are templatable in v1 (imported-STL models are not).

### `fromModel` semantics
`fromModel` records *which template an instance was stamped from*, for UI grouping and future
"re-stamp" affordances. It carries **no live binding** — editing the template or the instance does
not propagate either way. `null` for instances created directly or migrated from v2.

## 5. In-Memory State Map (v2 → v3)

The frontend `state` (from `getInitialState()`) is restructured to mirror the file. Every reader is
updated. Reference counts (current occurrences of `state.<key>` in `STL_generate.html`) indicate the
edit surface.

| v2 `state` key | refs | v3 location |
|---|---:|---|
| `models` | 169 | `construction` |
| `cfdRoom` (mesh+solver) | 201 | `simulation` |
| `cfdRoom.height` | — | `meta.room.height` |
| `cfdRoom.roomTemperature` | — | `meta.room.temperature` |
| `bounds` | 12 | `meta.room.bounds` |
| `roomPolygon` | 10 | `meta.room.polygon` |
| `projectName` / `exportFileName` / `inputUnit` | — | `meta.*` |
| `sensorPoints` | 28 | `probeData` |
| `selection` | 277 | `ui.selection` |
| `dctwinConfig` | 115 | `ui.session` (`casePath` → `meta.case.path`) |
| `groups` | 19 | `ui.groups` |
| `displayMode` | 9 | `ui.displayMode` |
| `view` | — | `ui.view` |
| `snapFace` / `snapEdge` / `snapGrid` | — | `ui.*` |
| `treeCollapsed` | — | `ui.treeCollapsed` |
| `previewVisibility` / `previewRegionVisibility` / `previewRegionReverse` | — | `ui.*` |
| `rackDetailTarget` / `rackDetailReturnMode` / `rackDetailShowContext` | — | `ui.rackDetail.*` |
| `_selectedCategories` / `_anchorCategory` / `_lastClickedCategory` | — | `ui.*` |
| `liquidCooling` | — | `liquidCooling` (unchanged) |
| — (new) | — | `model`, `sensorData`, `meta.description`, `fromModel` |

Mechanical strategy: update `getInitialState()` to the new shape, then update readers section by
section (geometry → mesh → solver → tree/inspector → liquid → server/job). Keep the patch
serializable (no functions/DOM/Three.js objects in `state`), per the STL_generate.html coding rules.

## 6. Migration (v2 → v3)

`migrateV2toV3(raw)` runs at the top of `_applyProjectData(raw)` (the project-load entry point),
gated on version. **Absent `schemaVersion` is treated as legacy** —
3 real projects (`LiquidSingle`, `STDCT`, `Polygon_room`) and 2 `paper_experiment` labels carry no
version stamp yet are structurally v2. The migrator reads every source key with a default (missing
`sensorPoints` → `probeData: []`, missing `roomPolygon` → `[]`, etc.), never assuming a key exists.
Version gate: `Number(raw.schemaVersion || 0) < 3`. Lossless and idempotent (v3 passes through unchanged).

1. `models[]` → `construction[]`; add `fromModel: null` to each.
2. `model` library starts **empty** (existing instances are not retro-templated).
3. `sensorPoints[]` → `probeData[]`; add empty `sensorData: []`.
4. Build `meta` from `projectName`/`exportFileName`/`inputUnit`, `bounds`, `roomPolygon`,
   `cfdRoom.height`, `cfdRoom.roomTemperature`; `meta.description = ""`; `meta.case.path` from
   `dctwinConfig.casePath`.
5. `cfdRoom` minus the room-geometry fields → `simulation`.
6. View/snap/selection/groups/preview/rackDetail/`_*` + remaining `dctwinConfig` → `ui`
   (`ui.session` for ephemeral job state).
7. Stamp `schemaVersion = 3`. On next save the file is written in v3 form.

Unknown/extra keys are preserved (forward-compatible, matching the backend's `extra="allow"`
posture). All 16 case projects and the dataset must round-trip: load → save → reload → deep-equal.
Batch conversion of on-disk files and the Python-side reuse of this logic are covered in §10.

## 7. Export Boundary (backend unchanged)

The `.cfdrun` export and `CFDExport` Pydantic schema do **not** change:

- Export iterates `construction[]` instead of `models[]`; because instances are self-contained,
  the flattening into `racks`/`acus`/`baffles`/… is otherwise identical.
- The `model` library is authoring-only and **not exported**.
- `sensorData` is **not exported** (reference-only).
- `probeData` is emitted under the existing `sensorPoints` key the runner expects.
- Room geometry now sourced from `meta.room.*`; mesh/solver from `simulation`.

This keeps the refactor frontend-only.

## 8. UI Touchpoints (interface-level; pixel detail deferred to the plan)

All state-mutating handlers follow the STL_generate.html rules: `pushUndoCheckpoint()` first,
`logOperation()` after success; selection-only handlers push no checkpoint.

- **Root-node inspector** (`renderRootInspector`): add a `description` textarea bound to
  `meta.description`.
- **RACK/ACU inspector:** add a **固定入库** button. Deep-copies the selection's reusable subset
  (§4 template shape) into `model.racks` / `model.acus` with a generated `templateId` + a `label`.
- **Add-device control** (top of the right sidebar): a per-type dropdown listing library templates;
  selecting one stamps an independent copy into `construction` (fresh `id`, default placement,
  `fromModel` = templateId).
- **Sensor data panel:** a new CRUD surface (placement: a sub-section or dedicated panel, decided in
  the plan) to add/edit/delete `sensorData` records — location, quantity, unit, time-series rows,
  source note.

## 9. Risks & Testing

**Risks**
- Large edit surface (~840 references). Mitigated by the explicit field map (§5) and section-by-section
  conversion.
- A missed reader silently reads `undefined` after a key moves. Mitigated by tests + browser verify.
- Round-trip data loss during migration. Mitigated by the deep-equal round-trip test over all cases.

**Testing**
- Unit: `migrateV2toV3` on each of the 16 case projects → schemaVersion 3, no key dropped.
- Migrator parity: `Base_leakage` v2 through the JS and Python migrators yields identical v3 (anti-drift).
- Batch script: `--dry-run` review, `.cfdproj.bak` backups, idempotency (second run is a no-op), over the ~50 real projects (§10).
- Round-trip: load → serialize → reload → deep-equal for every case project.
- Library: 固定入库 a rack → add two copies → edit one → other copy + template unchanged
  (copy-on-add isolation).
- Export equivalence: `.cfdrun` produced from a migrated v3 project is byte-equivalent (modulo key
  order) to the v2-era export for the same project.
- **Acceptance gate — `/browser-verify` (per repo rule, mandatory before merge):**
  - **All 50 real projects load.** Loop over both roots in one browser session; for each project run
    `window._applyProjectData(JSON.parse(...))` and assert: no JS error, `state.schemaVersion === 3`,
    visible mesh count > 0 (`scene.traverse`), and `state.construction.length` equals the source
    model count. Capture a screenshot per project and read a sample. Any load error / zero-mesh /
    count mismatch fails the gate.
  - **Lazy-path spot-check:** load 3 `Slab_dataset` files (still v2 on disk) to exercise the
    in-browser `migrateV2toV3` path inside `_applyProjectData`.
  - **Feature round-trip on Base_leakage:** edit description, 固定入库 a rack, add-from-library,
    add a sensorData record, save → reload → all survive.

## 10. Batch Conversion & Rollout

**Decisions:** convert the ~50 real projects **now** — 21 under `case/` (excluding
`case/Slab_dataset/`) + 29 under `docs/manuscript/paper_experiment/` (each case's `label/` and
`workdir/`); Python readers adopt v3 via a **compat loader shim**. The 1001-file dataset stays v2 on disk and
keeps working because every reader auto-migrates v2→v3 in memory (the JS loader in the frontend, the
Python shim below). It can be batch-converted later by pointing the script at `case/Slab_dataset/`.

**Three migration code paths, kept in lockstep:**
1. JS `migrateV2toV3` — frontend load-time (§6).
2. Python `migrate_v2_to_v3(dict) -> dict` — one importable module (e.g.
   `src/frontend/scripts/cfdproj_migrate.py`) reused by both the batch script and the compat shim.
3. Golden cross-check test — `Base_leakage` v2 fed to the JS and Python migrators must yield
   identical v3. Prevents the two implementations from drifting.

**Python compat shim** — add `load_cfdproj(path) -> dict` that reads JSON and runs `migrate_v2_to_v3`
when `schemaVersion < 3`. All structural readers route through it and always see v3:
- `docs/manuscript/paper_experiment/score_case.py`, `run_all_cases.py`, `generate_dataset.py`
- the `dc-score` / `dc-dataset-reverse` skills (`.claude/commands/`)
- tests reading `*.cfdproj` fixtures (`test_floor_room_boundary.py`, …)
- `src/server/converter.py` emits v2 today (legacy room.json path); left as-is — its output
  auto-migrates downstream. Optionally updated to emit v3 later.

**Batch script** `src/frontend/scripts/migrate_cfdproj_v2_to_v3.py` (mirrors
`cleanup_cfdproj_legacy_partitions.py`): `argparse`, `--dry-run`, single-file or recursive dir,
`.cfdproj.bak` backup, idempotent (re-running on v3 is a no-op). It skips `Slab_dataset/` by default.

```bash
# review the ~50 (both roots; Slab_dataset/ skipped by default)
python src/frontend/scripts/migrate_cfdproj_v2_to_v3.py case/ docs/manuscript/paper_experiment/ --dry-run
# convert
python src/frontend/scripts/migrate_cfdproj_v2_to_v3.py case/ docs/manuscript/paper_experiment/
```

**Rollout order:** lock + test the v3 frontend format → add the Python migrator + shim + golden test
→ batch-convert the ~50 real projects → commit (code + real-project conversions together; dataset
deferred) → **`/browser-verify` acceptance gate: all 50 real projects load (§9) before merge.**

## 11. Open Questions

None outstanding — the structural forks (reuse model, library scope, sensorData purpose), the
implementation-depth choice, and the rollout decisions (file scope, Python-reader strategy) are all
resolved (§3, §10).
