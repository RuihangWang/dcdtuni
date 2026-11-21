# Rack Perforated-Door Refinement Level (2026-04-17)

> **SUPERSEDED (2026-05-12):** The per-unit door bucket system (`rackDoorPerMinUnitLevel`) and its dependency on `serverFacePerUnitLevel`/`blankingPlatePerUnitLevel` have been removed. Door refinement now uses the unified `rackRegionLevel`. See `2026-05-12-thin-gap-auto-refinement-design.md`.

## Problem

Rack perforated doors currently receive the global `baffleRefinementLevel`
(default `[1, 1]`). On cases with dense 1U server slots the door surface
ends up coarser than the 1U patches it fronts, which produces two
downstream failures:

1. `snappyHexMesh` castellation generates non-manifold edges on every
   door (`checkMesh` reports ≈138 multiply-connected edges per door),
   plus collapsed sub-mm cells at door–frame junctions. The resulting
   mesh has `min volume ≈ 3e-9 m³` and an ill-conditioned T matrix that
   NaN-s at the first SIMPLE iteration even with robust scheme/solver
   choices.
2. Single-slot (1U) server inlet patches are dropped entirely because
   their faces fall below the door-level cell size, producing orphan
   outlet patches with no return path (observed for 3 servers on the
   Alibaba Hangzhou case).

The fix is to tie each rack's door refinement to the finest patch
behind it — so the door is at least as resolved as the features it
covers.

## Non-goals

- Changing the door's BC semantics (still `porousBafflePressure` via
  `createBaffles` cyclic pair).
- Introducing a per-rack UI (still bucketed).
- Touching non-door baffles; `baffleRefinementLevel` remains for
  `merged_baffle.stl` and standalone baffle models.

## UX

Panel *Rack 自动加密层级检查* gets one new row below the existing
frame / server / blanking / leakage rows:

    🚪 门 / Door       unified: [_, _]       1U (12): [_, _]   2U (4): [_, _] ...

- **Editable** (user-adjustable inputs, same widget style as existing
  rows). Any manual value strictly greater than the auto-computed max
  is preserved on subsequent auto-detect.
- **Bucket label** shows rack count in parentheses — identical format
  to the existing categories via the shared `_renderCategorySection`
  helper (which already interpolates `{count: bucket.count}` into the
  label template). The new scan function just has to emit `count` in
  its bucket entries; the render layer is untouched.
- **"🔍 自动检测" button** (single shared trigger — no new button)
  fills each bucket to `max(server, blanking, leakage)` across racks
  in that bucket; door auto-detect runs **after** the three source
  categories so it reads their just-updated values.
- **Warning surface**: the door row is visually decorated (same
  red-text style as other categories) when a rack's door level is
  below the min-safe level implied by its `minUSpan` — this is purely
  in-panel feedback.

The row only shows buckets actually present in the scene (e.g. a case
with only 4U racks renders one `4U` column plus unified).

### Preflight policy — door warnings are NOT blocking

Existing pre-mesh flow (`runMeshGeneration`):

1. `collectRackSurfaceRefinementWarnings()` collects warnings across
   the four existing categories.
2. If any warning exists, `showRackRefinementWarningDialog()` raises a
   confirmation dialog. Cancel → abort. Continue → proceed.

The door category **must not** participate in that dialog.
Rationale: door refinement is downstream of the three source
categories (derived, not independent), and a "too low" door level is
an explicit user choice that is often intentional (e.g. to cap cell
count when the source categories have already been bumped). The
downstream effect (frame feature-line level also follows, per §4.3)
means a low door is a visible, local degradation — not a silent
correctness hazard the gate needs to protect against.

Implementation:

- `collectRackSurfaceRefinementWarnings()` keeps its existing
  signature and return value (only the three source categories).
- A **separate** `collectDoorRefinementInfo()` feeds only the
  in-panel decoration / banner. It is NOT wired into
  `runMeshGeneration`'s preflight.

This is the only behavioural asymmetry between door and the three
source categories.

## Data model

Two new keys on `state.cfdRoom` / `cfd_export.json → meshConfig`:

    rackDoorRefinementLevel: [int, int]        // default [1, 1]
    rackDoorPerMinUnitLevel: { str(minUSpan): [int, int] }

Storage is **bucketed only** — there is no per-rack-id map. Each rack
looks up its bucket at export/resolve time. This mirrors the existing
`serverFacePerUnitLevel` convention.

`rackDoorPerMinUnitLevel` keys are the *rack-level* `minUSpan` (see
§Algorithm § `_computeRackMinUSpan`), **not** the span of any single
entry. A rack with 1U servers + 4U blanking plates buckets as `"1"`.

## Algorithm

### `_computeRackMinUSpan(rackModel)` — frontend only

Operates on the frontend `state.models[i]` shape (kind==='rack'). This
is the ONE canonical implementation. The runner does not re-compute
the span; instead the frontend emits the result per-rack into the
cfdrun (see §Backend).

    spans = []
    for eq in (rackModel.rack.equipments || []):
        if (eq.spanUnits) spans.push(eq.spanUnits);
    for side of ['front', 'back']:
        for range of (getRackMergedEmptySlotRanges(rackModel, side) || []):
            spans.push(range.endUnit - range.startUnit + 1);
    for grp of (_scanThroughSlotGroups(rackModel) || []):
        spans.push(grp.endUnit - grp.startUnit + 1);
    return spans.length ? Math.min(...spans) : null;

Used by both `scanRackDoorMinUnitBuckets()` (for panel rendering / auto-
detect) and `buildCfdExportJson()` (for per-rack export). Keeping both
call sites on this one helper ensures the UI bucket and the runner
lookup always agree.

### Auto-detect (frontend)

    for bucket_key, bucket_racks in scanRackDoorMinUnitBuckets():
        levels = []
        for rack in bucket_racks:
            for entry in (rack.servers + rack.blankings + rack.leakages):
                entry_level = per_unit_level_of(entry) or unified_of(entry.category)
                levels.append(entry_level)
        if not levels:
            continue
        safe_min = max(l[0] for l in levels)
        safe_max = max(l[1] for l in levels)
        current = state.cfdRoom.rackDoorPerMinUnitLevel[bucket_key]
                  || state.cfdRoom.rackDoorRefinementLevel
        new_level = [
            max(current[0], safe_min),   // never lower a user-set higher value
            max(current[1], safe_max),
        ]
        state.cfdRoom.rackDoorPerMinUnitLevel[bucket_key] = new_level

### Resolve (backend, in runner)

    def _compute_rack_min_u_span(rack_data):
        """Trivial: read the field the frontend already computed.
        Returns int or None."""
        v = rack_data.get("minUSpanForDoorBucket")
        return int(v) if v is not None else None

    def _door_level_for_rack(rack_data, mc):
        min_span = _compute_rack_min_u_span(rack_data)
        if min_span is not None:
            key = str(min_span)
            if key in mc.get('rackDoorPerMinUnitLevel', {}):
                return mc['rackDoorPerMinUnitLevel'][key]
        return mc.get('rackDoorRefinementLevel', [1, 1])

The backend helper is deliberately minimal — it does NOT re-derive the
min-U-span from the rack's server / blanking / leakage lists. The
frontend already produced an authoritative bucket key at export time;
the backend only reads it. If the key is missing (e.g. legacy cfdrun
file), the helper returns `None` and the door falls back to the
unified level — safe default.

## Frontend changes (`STL_generate.html`)

1. **State defaults** in the `getDefaultCfdRoom()`-style initializer
   (near `rackRefinementLevel`):
   - `rackDoorRefinementLevel: [1, 1]`
   - `rackDoorPerMinUnitLevel: {}`
2. **`scanRackDoorMinUnitBuckets()`** — new scan walking
   `state.models.filter(kind==='rack')`, calling
   `_computeRackMinUSpan`, grouping into `{ bucketKey → [rackIds] }`,
   skipping racks whose span list is empty.
3. **`_autoDetectRackDoor()`** — per §Algorithm. Wired into
   `autoDetectRackRefinement()` **after** the three dependencies so it
   sees the freshly auto-detected server / blanking / leakage values
   in the same run.
4. **UI row** — new entry in the panel rendering loop using the
   existing `_renderRackCategoryRow` helper. Both setters
   (`setUnifiedRackCategory`, `setPerUnitRackCategory`) already accept
   the state key + per-unit key as arguments — no new setter needed.
5. **Warnings** — `collectRackSurfaceRefinementWarnings()` adds one
   more call into the concat chain for the door category.
6. **Export** — `buildCfdExportJson()` emits:
   - `meshConfig.rackDoorRefinementLevel` and
     `meshConfig.rackDoorPerMinUnitLevel` (global settings).
   - For each rack in the `racks` dict, `rack.minUSpanForDoorBucket =
     _computeRackMinUSpan(rackModel)` (int or null). This is the bucket
     key the backend uses to look up the rack's door level — no span
     re-derivation server-side.
7. **Load/normalize** — `cloneCfdRoom` (or equivalent) ensures legacy
   projects lacking these keys get the `[1,1]` / `{}` defaults.

## Backend changes

### `src/backend/aircooling/runner/cfdproj_schema.py`

Add two fields to the `MeshConfig` pydantic model:

    rackDoorRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackDoorPerMinUnitLevel: Dict[str, List[int]] = Field(default_factory=dict)

Per the existing feedback memory (project conventions), add the
fields to **every** `*MeshConfig` subclass the runner reads.

### `src/backend/aircooling/runner/stlgen_runner.py`

1. **`_compute_rack_min_u_span(rack_data) → Optional[int]`** — trivial
   read of the per-rack `minUSpanForDoorBucket` field that the
   frontend emits. Does NOT re-derive spans from server / blanking /
   leakage lists (that work lives in the frontend `_computeRackMinUSpan`).
2. **`_rack_of_door(filename: str) → str`** — builds a reverse map at
   runner init (filename → rack_name) from the existing iteration
   that populates `rack_door_filenames`, then looks up.
3. **Refinement branch** — in the `refinement_lines` construction
   (around line 3458), add an early branch:

        if filename in rack_door_filenames:
            rack_name = self._rack_of_door(filename)
            lvl = self._door_level_for_rack(data['racks'][rack_name], mc)
            refine_lines.append(f"""    {stem}
        {{
            level ({lvl[0]} {lvl[1]});
            faceType internal;
            faceZone {_sanitize(solids[0])};
        }}""")
            continue

   The existing `filename.startswith("baffle_")` branch stays as the
   catch-all for non-door baffles.
4. **Feature-edge branch** — around line 3390, replace the
   `elif filename in rack_door_filenames: feat_level = feat_rack`
   with:

        elif filename in rack_door_filenames:
            rack_name = self._rack_of_door(filename)
            lvl = self._door_level_for_rack(data['racks'][rack_name], mc)
            feat_level = lvl[1]

## Testing

All tests route through `FieldGenerator` / `STLGenRunner` with a
`BridgeBCRegistry` + fully-parsed `CFDExport` pydantic object — NO
dict-based shortcuts (per the Pydantic schema-additions feedback
memory).

1. **`test_rack_door_refinement_bucket_lookup.py`** — two racks,
   min-U-spans 1 and 4, `rackDoorPerMinUnitLevel={"1":[3,4],"4":[1,1]}`.
   Render `snappyHexMeshDict`; assert door level line for each rack.
2. **`test_rack_door_refinement_empty_rack_fallback.py`** — rack with
   no server / blanking / leakage, `rackDoorRefinementLevel=[2,3]`,
   `rackDoorPerMinUnitLevel={}`. Assert door emits `(2 3)`.
3. **`test_rack_door_feature_level_follows_surface.py`** — assert the
   door's feature eMesh level equals its surface `max` level.
4. **`test_rack_door_autodetect_frontend.py`** (headless browser) —
   load a project with mixed 1U/4U racks, run
   `window.runAutoDetectRackRefinement()`, assert
   `state.cfdRoom.rackDoorPerMinUnitLevel["1"][1]` ≥ the max of
   server / blanking / leakage level `[1]` entries seen in that bucket.
5. **`test_rack_door_autodetect_preserves_manual_override.py`** —
   user-set `rackDoorPerMinUnitLevel["1"] = [5, 6]` before auto-detect;
   auto-detect must NOT lower it.
6. **`test_rack_door_warning_not_in_preflight.py`** (headless) — set
   `rackDoorPerMinUnitLevel["1"] = [1,1]` on a 1U-dense scene where
   server / blanking / leakage levels are all at `[3,4]`; assert
   `collectRackSurfaceRefinementWarnings()` returns zero door entries
   AND that `runMeshGeneration` does NOT raise the refinement-warning
   dialog solely due to the low door level. Conversely, dropping any
   one of the three source categories (server, blanking, **or**
   leakage) below min-safe on the same scene *must* still raise the
   dialog — proves door is excluded without regressing the gate on
   any of the three source categories.

## Rollout

- No migration needed — missing keys resolve to `[1,1]` / `{}` on both
  frontend (via `cloneCfdRoom`) and backend (Field defaults).
- Existing saved `.cfdproj` files remain valid; their behaviour is
  unchanged until the user clicks "🔍 自动检测".
- Mesh regeneration is required for the fix to take effect on an
  already-meshed case (solve-only will NOT pick this up).

## Risks & mitigations

- **Cell-count blow-up.** Bumping every door from level 1 to
  max(server=3) is a 64x cell volume reduction in door regions. On
  the Alibaba Hangzhou case (200 racks × 2 doors) cell count may
  exceed `meshConfig.maxGlobalCells = 2_000_000`.
  *Mitigation*: surface the new door level in the safety-warning
  banner alongside the existing per-category warnings so the user
  sees it before running; document in the release note that
  `maxGlobalCells` may need bumping in parallel.
- **`minUSpan` mismatch between frontend and backend.** Eliminated by
  design. The frontend is the single source of truth: it computes
  `_computeRackMinUSpan(rackModel)` once at export and writes the
  integer to `rack.minUSpanForDoorBucket`. The backend simply reads
  that field — no span arithmetic server-side. Any change to the
  span rule lives in one place (frontend). If the field is absent
  on a legacy cfdrun, the backend falls back to the unified door
  level, which is the same no-op behaviour as the pre-2026-04-17
  baseline — safe default.
- **Legacy cases that never auto-detected.** Door still uses `[1,1]`
  and may still produce non-manifold edges.
  *Mitigation*: one-time nudge — on cfdproj load, if
  `rackDoorPerMinUnitLevel` is empty AND any `*PerUnitLevel` key has
  higher values than the door unified, pop a UI hint "推荐点击🔍
  自动检测刷新门加密"; do NOT auto-mutate on load.
