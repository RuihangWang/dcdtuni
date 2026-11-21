# Universal Mesh Size + Quality Defaults for DC Cases

**Date:** 2026-04-19
**Scope:** dcdtuni runner + STLGenerator frontend + OpenFOAM template

## Goal

Three coordinated changes that give DC CFD cases a reasonable default mesh
out-of-the-box, without per-case hand-tuning:

1. **`baseSize` default → 0.35 m** (8U-equivalent, clean log2 bucket — already executed)
2. **Snappy quality defaults tightened** (`maxNonOrtho`, transition smoothness, feature snap iters)
3. **Decouple auto-detect reference from actual `baseSize`** via new `meshDetectBaseSize` field + UI input

## Motivation

Current state (pre-change):
- `baseSize` default 0.2 m (non-U-aligned, hardcoded in 5+ places, inconsistent per-case values in data: 0.2 / 0.25 / 0.4).
- Snappy `maxNonOrtho 65`, `nCellsBetweenLevels 3`, `nFeatureSnapIter 10` — all are the OpenFOAM generic defaults, not tuned for data-center thermal flows where buoyancy + lots of feature collisions (rack frame + U-slot + door + perforation patches) stress the mesh.
- Auto-detect uses `baseSize` directly as its reference. When the user wants a fine background (small `baseSize` for solver stability) but the auto-detect math would land on a different log2 bucket than intended, there's no knob.

Observed symptoms on Tri_angle (`case/Tri_angle`):
- At `baseSize=0.45 m`, auto-detect pushes 2U door bucket to level [4,5] → 14 mm cells on a porous door → wasted cells.
- Rack frame near door/corner junctions visibly distorted (snap can't settle in 10 iters with so many colliding features).
- `base_size=0.2` and `base_size=0.356` both produce the same auto-detect levels (same log2 bucket for all U-snap patch sizes) — user's intuition that 0.4 might be "cleaner" is wrong: 0.4 crosses the log2 threshold and inflates every level by 1.

## Non-Goals

- Don't rework the auto-detect algorithm itself (`ceil(log2(base / (H/2)))` logic stays).
- Don't add per-patch-type refinement knobs (server-face vs door vs frame). Current per-bucket scheme (U-span keyed) is untouched.
- Don't touch layer-addition (`addLayersControls`) — layers still off in mesh-only mode.
- No migration script: existing 40-case cfdproj/cfdrun are batch-updated in this same change, not migrated at load-time.

---

## Change 1: `baseSize` default → 0.35 m

**Status:** executed already (uncommitted in working tree).

**Rationale:** 0.35 m ≈ 8U (44.45 mm × 8 = 355.6 mm). Any `baseSize` in the half-open interval `[0.178, 0.356)` m lands in the same log2 bucket as 0.2 and 0.356 for all standard U-span patches (2U, 4U, 8U), so auto-detect output is identical. 0.35 was chosen over 0.356 for readability; the ~1.6% deviation from exact 8U is below rounding resolution of the mesh pipeline.

**Traps avoided:**
- 0.4 m and 0.5 m sit just over the log2 boundary → every auto-detect bucket inflates by 1, cells explode ~8×.
- 0.178 m (= 4U) is mathematically cleanest but makes large rooms over-meshed.

**Files changed:**
- `src/backend/aircooling/runner/cfdproj_schema.py:403` — `MeshConfig.baseSize` default
- `src/backend/aircooling/runner/stlgen_runner.py:2366, 2559, 3364` — three `mc.get("baseSize", 0.2)` fallbacks
- `STLGeneratorForOpenFoam/web/STL_generate.html` — 5 sites (state init + 4 fallback reads)
- `AGENTS.md`, `STLGeneratorForOpenFoam/Instruction.md` — doc examples
- **Data files (21 total):** 14 cfdproj + 7 cfdrun under `STLGeneratorForOpenFoam/projects/` and `case/*/model/` normalized to 0.35 (overwriting prior 0.2 / 0.25 / 0.4 values).

**Verification:** 138 runner tests pass (one pre-existing `test_partition_hole_fvoptions` failure is unrelated).

---

## Change 2: Snappy quality defaults (Approach A: global, no UI)

**Design principle:** DC thermal flows need tighter quality than generic OF defaults. Values are baked into the Jinja2 template variables' defaults — users can still override per-case by editing cfdrun JSON, but the UI doesn't expose them (prevents non-expert users from detuning).

**Four parameter changes:**

| Template variable | Current | New | Reason |
|---|---|---|---|
| `max_non_ortho` | 65 | **60** | Buoyant solver (`buoyantBoussinesqSimpleFoam`) sensitivity — 65 lets marginal cells through that hurt convergence |
| `max_non_ortho_relaxed` | 75 | **70** | Same, relaxed-pass threshold |
| `n_cells_between_levels` | 3 | **4** | Level 1→4 transitions (room→door) need 4 cells to avoid anisotropic stretched cells at the step — the direct cause of visible rack-frame distortion |
| `snap_n_feature_snap_iter` | 10 | **15** | Rack geometry has 4+ colliding feature classes (outer edge / U-slot / door / perforation); 10 iters insufficient for snap to settle |

**Files changed:**
- `src/backend/aircooling/third_parties/foam/template/mesh/snappyHexMeshDict_stl.j2` — 4 template variable defaults
- `src/backend/aircooling/third_parties/foam/template/mesh/snappyHexMeshDict.j2` — same 4 defaults (mirrored template)
- `src/backend/aircooling/runner/stlgen_runner.py` — any site that passes these as Jinja context: verify the runner doesn't hard-code the old values when the mesh config omits them

**Left alone (already correct for DC):**
- `explicitFeatureSnap true`, `implicitFeatureSnap false` — DC needs sharp corners
- `multiRegionFeatureSnap false` — single region topology
- `resolveFeatureAngle 30°` — covers rack 90° corners fine
- All other `meshQualityControls` (minVol, minTetQuality, minTwist, etc.) — OF defaults fit DC cases

**No UI exposure.** Users who need to tune further can edit `meshConfig.maxNonOrtho` etc. in the cfdrun JSON directly (the runner still reads those overrides if present). This is deliberate — the 4 params interact with each other and with `nCellsBetweenLevels`; exposing as UI sliders invites bad tuning.

---

## Change 3: `meshDetectBaseSize` — decouple auto-detect reference from `baseSize`

### Problem

The auto-detect formula `minLevel = ceil(log2(baseSize / (patchHeight/2)))` uses `baseSize` as a scale reference. When the user wants a **finer** actual `baseSize` (e.g., 0.2 for better solver behavior in small rooms) but the same **level numbers** as a coarser reference (0.35) would produce, there's no separation.

In practice, 0.2 and 0.35 land in the same log2 bucket for all U-span patches, so the current coupling happens to work — but it's fragile. The moment a user picks an odd `baseSize` (0.28, 0.42), the auto-detect output becomes hard to reason about.

### Solution

New field `MeshConfig.detectBaseSize: Optional[float] = None`. When set, auto-detect uses **this value** as the reference scale; when `None` (default), falls back to `baseSize` (current behavior).

**Semantics:**
- `detectBaseSize = None` → auto-detect acts exactly as today (backward compatible)
- `detectBaseSize = X` → auto-detect's `ceil(log2(X / (H/2)))` computed with X, but **actual mesh** still uses `baseSize`.
- The cell size at level L is still `baseSize / 2^L` — this field **only** affects auto-detect's level-picking logic.

**Typical use case:** baseSize=0.2 m (fine bg for small room), detectBaseSize=0.35 m → auto-detect produces level numbers as if base were 0.35 (predictable), but actual mesh at each level is 0.2 / 2^L (finer than a 0.35 base at same level).

### Schema

`src/backend/aircooling/runner/cfdproj_schema.py MeshConfig` adds:
```python
detectBaseSize: Optional[float] = None
```

### Frontend UI

Location: 网格 tab → "自动检测" 按钮**上一行**,单独一排。

Layout:
```
[检测基准 (m):  0.35  ]    ← new number input, single row
[🔍 自动检测 ]             ← existing button, unchanged position
```

Input behavior:
- First render: input value = `state.cfdRoom.meshBaseSize` (inheriting, not storing)
- On user edit: store to `state.cfdRoom.meshDetectBaseSize`, mark state dirty (undo checkpoint)
- Placeholder shown when `meshDetectBaseSize` is null: "继承 baseSize = 0.35"
- Clear button (tiny ✖): resets to null (back to inheriting)

### Auto-detect wiring

All client-side JS. In `STL_generate.html`:
- `_autoDetectRack*` family of functions (lines ~26045-26187) currently read `state.cfdRoom.meshBaseSize` (or pass through `_autoDetectCategory`).
- Replace those reads with:
  ```js
  const detectBase = Number(state.cfdRoom.meshDetectBaseSize) || Number(state.cfdRoom.meshBaseSize) || 0.35;
  ```
- `computeMinSafeLevel(patchHeight)` uses `detectBase`, not `baseSize`.

### Runner consumes

Runner **does not need** `detectBaseSize` — auto-detect is client-side, and by the time cfdrun reaches the runner, the level numbers are already materialized into `serverFaceRefinementLevel`, `rackDoorPerMinUnitLevel`, etc. The field just needs to round-trip through schema validation (Pydantic extras="allow" already supports this, but we declare it explicitly for clarity).

### Persistence

Frontend's cfdproj export + cfdrun export includes `meshDetectBaseSize` alongside `meshBaseSize` when non-null. When null, field is omitted (keeps existing files diff-clean).

### Backward compatibility

Every existing cfdproj / cfdrun lacks this field → `detectBaseSize = None` → auto-detect uses `baseSize` → identical behavior to today. No migration.

---

## Testing Strategy

### Unit tests (Python)

- Tests pass after `MeshConfig.detectBaseSize` is added (schema round-trip).
- No new unit tests for auto-detect (client-side JS); manual verification via browser-use.

### Integration tests (manual via browser-use + /api/run-mesh)

For Change 2 (quality params):
1. Load `case/Tri_angle/model/Tri_angle.cfdrun` (already at baseSize=0.35), run mesh.
2. Load mesh preview → check visible distortion in rack frame is reduced.
3. Check mesh-tab log reports cell count + boundary face count; cell count in same order of magnitude (~150k-200k).

For Change 3 (detectBaseSize):
1. Open STL_generate.html, create a simple project with one rack.
2. Click auto-detect → note level numbers (e.g., 2U=[3,4]).
3. Change 检测基准 input from inherited 0.35 to 0.178 → click auto-detect again → levels should drop by 1 (2U=[2,3]).
4. Clear the override → next auto-detect matches step 2.

### Verification of non-regression

- Run full `pytest src/backend/aircooling/runner/tests/` — all previously-passing tests still pass.
- Open an existing project in UI — levels + baseSize render correctly, auto-detect gives expected output.

## Out-of-scope / future work

- Per-patch-type refinement tuning (server / door / frame separately beyond U-span bucketing)
- Layers + boundary-layer refinement for thermal BL resolution
- Case-specific `resolveFeatureAngle` (e.g., angled rack doors need 45°)
- Dynamic `nCellsBetweenLevels` based on max level in scene (could auto-pick 3/4/5)
