# Refinement Architecture Redesign — STL Layout + Feature-Line Fidelity (2026-04-17)

> **SUPERSEDED (2026-05-12):** The per-unit bucket dispatch (`_BUCKET_FILE_PATTERNS`, `_bucket_level_lookup`, `_bucket_feature_lookup`) and all `*PerUnitLevel` meshConfig keys have been removed. All rack-internal surfaces now use a single `rackRegionLevel`. See `2026-05-12-thin-gap-auto-refinement-design.md`. The STL file naming convention (`merged_server_<N>U.stl` etc.) is retained for classification purposes only.

## Problem

Today's STL layout (`merged_rack.stl` + individual `baffle_<solid>.stl` files
+ miscellaneous `leakage_<solid>.stl` / `partition_hole_<solid>.stl`) evolved
under the constraint that snappyHexMesh's `features` block applies ONE feature
level per eMesh file. To give distinct feature levels to e.g. each perforated
door, we split each door into its own STL file — 400 door files on
alibaba-hongchou, plus ~40 merged + individual baffles, ~200 per-instance
leakage files, etc.

Two consequences:

1. **The frontend UI now exposes refinement inputs that cannot actually land
   in snappyHexMeshDict.** For example, editing the "rack frame feature level"
   input in the panel affects `rackFeatureLevel` which is only consumed by
   `merged_rack.stl`'s eMesh — but `merged_rack.stl` today contains server
   inlet/outlet solids, blanking plates, frame solids, and wall baffles all
   sharing one feature level. Per-bucket feature refinement is not
   representable with the current file layout.

2. **Hundreds to thousands of STL files slow mesh generation** and obscure
   the actual refinement intent: on the alibaba-hongchou case we currently
   emit ~400 files, most of which share identical refinement settings but
   exist as separate files purely because snappy can't differentiate their
   feature levels otherwise.

The fix: **restructure STL emission so every file maps to exactly one
feature-line level, but allow within-file per-region surface differentiation
via `regions { }`**.

## Non-goals

- Changing how the cfd_export.json / .cfdrun serialisation format stores
  per-rack data (servers, partitions, leakage groups, doors). Only the
  refinement levels and the STL file grouping change.
- Changing the boundary-condition logic (`fields.py`). Per-rack-type BC
  assignment stays as-is.
- Changing snappyHexMesh algorithm parameters (meshQualityControls,
  castellatedMeshControls defaults). Only geometry/features/refinementSurfaces
  sections are rebuilt.
- Adding per-rack-instance refinement (e.g. "this specific A17 rack has its
  own level"). Refinement granularity is **per-category × per-bucket**.

## Core principles

1. **Feature-line granularity = STL file granularity.** One STL file ⇒ one
   eMesh ⇒ one `features { file; level N; }` entry.
2. **Surface granularity can be finer** via `regions { solidA { level (a b); } ... }`
   inside `refinementSurfaces`. Multiple solids in one STL CAN have different
   surface levels.
3. **UI inputs are canonical** — every refinement input the UI exposes must
   land in snappyHexMeshDict and take effect. No dead knobs.
4. **Leakage (through-slot) STLs mark cellZone volumes only** — they must
   not generate OpenFOAM patches via createBaffles.
5. **Patches inherit parent surface level** — patches added on top of a face
   (via UI "rectangular/circular patch on face" tools) share the refinement
   level of the face they sit on. No independent refinement slider per patch.
6. **Unified UI inputs broadcast to buckets; export reads buckets only.**
   The "unified" row in each panel is a convenience mass-edit, not a data
   source. Auto-detect and manual edits can override individual buckets;
   snappyHexMeshDict emission reads per-bucket values exclusively.

## STL file layout

### Scene-level merged files (single feature level; per-solid surface via regions)

| File | Contents | Feature key | Surface key |
|------|----------|-------------|-------------|
| `room_wall.stl` | Room enclosure walls | `defaultFeatureLevel` | `refinementLevel` |
| `merged_rack_frame.stl` | ALL rack frame solids: outer walls (`outer_top/bottom/left/right` full faces + `outer_front/back` picture-frame borders with slot cutouts) + inner walls (`inner_top/bottom/left/right`). Per rack typically 10-20 solids depending on equipment layout; per-solid surface via regions. **One unified surface level and one feature level for the entire rack-frame category — no per-side sub-buckets.** | `rackFeatureLevel` | `rackRefinementLevel` (unified, single pair) |
| `merged_acu.stl` | All ACUs; per-ACU faces via regions | `acuFeatureLevel` | `acuRefinementLevel` |
| `merged_box.stl` | All box models | `boxFeatureLevel` | `boxRefinementLevel` |
| `merged_baffle.stl` | Free-standing scene-level baffles (wall-type + perforated baffles placed outside racks). **Does not include any rack-internal geometry** — rack walls and interior components live in `merged_rack_frame` / rack-internal per-bucket files. | `baffleFeatureLevel` | `baffleRefinementLevel` |
| `merged_floor_ceiling.stl` | All floors + ceilings + floor opening perforated faces (per-solid regions) | `floorFeatureLevel` (new, defaults to `defaultFeatureLevel`) | `floorRefinementLevel` (new, defaults to `refinementLevel`) |

### Rack-internal per-bucket files (each bucket = one STL file = one feature level)

Buckets are keyed by U-span. Span is calculated per item:
- server: `spanUnits` on the equipment entry
- blanking panel: `endUnit - startUnit + 1` of the contiguous empty-slot range
- partition: `spanUnits` of the slot the partition sits in (typically 1)
- leakage: `endUnit - startUnit + 1` of the contiguous through-slot group

| Category | File pattern | Content | Feature level | Surface level | Produces patch? |
|----------|--------------|---------|---------------|---------------|-----------------|
| **server** | `merged_server_<span>U.stl` | All fan-server solids with span = `<span>`: inlet_patch, outlet_p0, outlet_wall, top, bottom, left, right — plus any sub-patches the user added via "patch on face" UI tools | `serverFaceFeaturePerUnitLevel[str(span)]` (new, falls back to `serverFaceFeatureLevel` unified) | `serverFacePerUnitLevel[str(span)]` (existing) | ✅ `inlet_patch` / `outlet_p0` → type patch; `outlet_wall` + side walls → type wall |
| **blanking** | `merged_blanking_<span>U.stl` | All blanking-panel solids for contiguous-empty-slot ranges of U-span = `<span>` | `blankingPlateFeaturePerUnitLevel[str(span)]` (**new**) | `blankingPlatePerUnitLevel[str(span)]` (existing) | ✅ type patch |
| **partition (new)** | `merged_partition_<span>U.stl` | All interior partition walls inside through-slots (middle-of-cavity separators when both front+back face is deleted and a partition is added), grouped by U-span of the slot they sit in | `rackPartitionFeaturePerUnitLevel[str(span)]` (**new**) | `rackPartitionPerUnitLevel[str(span)]` (**new**) | ✅ type wall |
| **leakage** | `merged_leakage_<span>U.stl` | Through-slot cellZone markers, grouped by U-span. **NO surface data contribution** — these files still contain triangle hulls that bound a cellZone volume, but they register as `faceType internal + cellZone`, and DO NOT enter createBaffles. | `rackLeakageFeaturePerUnitLevel[str(span)]` (new, defaults to `rackLeakageRefinementLevel[1]`) — the "feature" is really the volume-refinement level in this case | `rackLeakagePerUnitLevel[str(span)]` (existing) | ❌ **NO patch emitted**. `faceType internal; cellZone leakage_<span>; cellZoneInside inside;` only. |

### Rack-door per-bucket files (absorbed into this redesign)

| File pattern | Content | Feature | Surface |
|--------------|---------|---------|---------|
| `merged_door_<bucket>.stl` | All perforated doors whose rack's `minUSpanForDoorBucket == <bucket>`. Replaces the 400 individual `baffle_<rack>_<side>_door.stl` files from the 2026-04-17 Task 8-14 work | `rackDoorPerMinUnitLevel[str(bucket)][1]` (feature follows surface max, matching Task 6) | `rackDoorPerMinUnitLevel[str(bucket)]` |

### File-count estimate on alibaba-hongchou

| Group | Current files | After redesign |
|-------|---------------|----------------|
| merged scene-level | 5 | 6 |
| server buckets | (inside merged_rack) | 3-6 |
| blanking buckets | (inside merged_rack) | 3-6 |
| partition buckets | 0 (no support) | 0-3 |
| leakage buckets | 0 (all disabled) | 0-3 |
| door buckets | 400 | 2-5 |
| free baffles | ~5-10 individual | 0 (all in merged_baffle) |
| **Total** | **~420** | **~20-30** |

Snappy processing time: comparable to or **faster** than current (fewer STL files → fewer surface objects to track).

## UI redesign (frontend)

### Remove (per user request #3)

- The feature-line input widget from the "🏗 RACK 表面" row in the Rack 表面加密
  panel. The rack-frame feature level is set via the outer "机架" mesh-tab
  row (which already exposes `rackFeatureLevel`); the panel row's feature
  input was redundant and confusing.

### Add (per user requests #1 reformed + new 4th category)

- **Blanking panel**: new feature-line input on the unified row. Per-bucket
  feature-line inputs alongside each U-span's surface min/max.
- **Partition 隔板 (new category)**: entirely new panel row. Unified
  surface [min, max] + feature + per-span buckets each with their own
  surface [min, max] + feature.
- **Server face**: already has category-scalar `serverFaceFeatureLevel`.
  Extend to per-bucket via new `serverFaceFeaturePerUnitLevel` dict.
- **Door**: existing `rackDoorPerMinUnitLevel` already encodes per-bucket
  levels; surface max IS the feature level (no separate input needed).

### Keep unchanged (per user request #4)

- Server face's feature-line input stays exposed.

### Unified → bucket broadcast (per user request #5)

On any edit to a unified row's surface-min, surface-max, or feature-level
input:

1. Write the new value to the unified key (e.g. `state.cfdRoom.serverFaceRefinementLevel[0] = N`).
2. **For each existing key in the per-unit dict** (e.g. every entry in
   `serverFacePerUnitLevel`), update the corresponding field to match.
   This is a fire-and-forget mass-edit — the existing bucket overrides are
   replaced.
3. User or auto-detect can then override any individual bucket, and the
   override persists (until the user does another unified edit).

**Export contract**: `buildCfdExportJson` reads from the **per-unit dict** for
every bucket that has ever been populated by the auto-detect scan; for spans
not in the per-unit dict, it falls back to the unified value. snappyHexMeshDict
emission (runner side) does the same: always prefer per-unit, fall back to
unified, fall back to hard default.

### Auto-detect (per user clarification)

For **server, blanking, partition** (all three physical rack-internal face
categories):

- Surface level: `computeMinSafeLevel(baseSize, patchHeight)` (existing rule)
- Feature level: **= surface max** (locked-together rule)

For **leakage**:

- Surface level: `computeMinSafeLevel(baseSize, patchHeight)` as before
  (used as volume refinement since leakage has no real surface)
- Feature level: = surface max (same rule, even though it's not really a
  "feature" — the number still needs to exist to write into `features { }`)

For **door** (existing, per Task 10):

- Surface level: `max` across bucket's racks' server/blanking/leakage levels
- Feature level: surface max

## Patch-on-face refinement inheritance

When a user adds a patch (rectangular/circular) onto an existing face via
the STL editor UI, the patch becomes its own `solid <patch_name>...endsolid`
block inside the same STL as its parent face. Two rules:

1. **Surface level of the patch = surface level of its parent face.**
   In `refinementSurfaces`, the patch's region entry inherits the same
   `level (min max)` as the parent's region.
2. **Feature level is inherited automatically** by virtue of living in the
   same STL file (same eMesh = same `features { level N }`).

The refinement generator must walk each patch's parent solid name and copy
the parent's level into the patch's region entry. No new UI slider for
per-patch refinement — the UI only exposes category/bucket refinement, and
the patch hitches along.

**Implementation note**: patch solids are emitted by the frontend STL writer
using the naming convention `<parent_solid>_p<patch_index>` (e.g.
`A17_A17_01_inlet_patch_p0`). The runner's dict emitter detects the pattern
by checking whether a stripped-prefix form of the patch name matches a
registered parent solid; if so, it assigns the parent's surface level to
the patch's region block. If the parent cannot be resolved, the patch
falls back to the file's unified level (safe default).

## Backend runner changes

### `stlgen_runner.py` — STL grouping (the source of truth for file split)

The existing `buildGroupedStlMap` (frontend) + per-category detection code
(runner) needs to rebuild the grouping logic:

1. Walk each rack's servers, partitions (blanking slots), leakage groups,
   through-slot groups, doors. Tag each with `(category, span)`.
2. Group solids by `(category, span)` key. Each group → one STL file.
3. Emit scene-level merged files (room_wall, rack_frame, acu, box, baffle,
   floor_ceiling) as today.

The split implementation lives in the frontend (since `buildGroupedStlMap`
runs in the headless browser during export), but the runner's
`_generate_snappy_hex_mesh_dict` must understand the new filename
conventions (`merged_server_<N>U.stl`, `merged_blanking_<N>U.stl`, etc.) so
it can emit the right features/refinementSurfaces entries.

### `_generate_snappy_hex_mesh_dict` — dict emission

Replace the current big `if/elif` chain on filename with a table-driven
approach:

```python
BUCKET_FILE_PATTERNS = {
    "merged_server_": ("serverFacePerUnitLevel", "serverFaceFeaturePerUnitLevel", "serverFaceRefinementLevel", "serverFaceFeatureLevel"),
    "merged_blanking_": ("blankingPlatePerUnitLevel", "blankingPlateFeaturePerUnitLevel", "blankingPlateRefinementLevel", "blankingPlateFeatureLevel"),
    "merged_partition_": ("rackPartitionPerUnitLevel", "rackPartitionFeaturePerUnitLevel", "rackPartitionRefinementLevel", "rackPartitionFeatureLevel"),
    "merged_leakage_": ("rackLeakagePerUnitLevel", "rackLeakageFeaturePerUnitLevel", "rackLeakageRefinementLevel", "rackLeakageFeatureLevel"),
    "merged_door_": ...  # existing via _door_level_for_rack
}

SCENE_MERGED_FILES = {
    "room_wall.stl": ("refinementLevel", "defaultFeatureLevel"),
    "merged_rack_frame.stl": ("rackRefinementLevel", "rackFeatureLevel"),
    "merged_acu.stl": ("acuRefinementLevel", "acuFeatureLevel"),
    "merged_box.stl": ("boxRefinementLevel", "boxFeatureLevel"),
    "merged_baffle.stl": ("baffleRefinementLevel", "baffleFeatureLevel"),
    "merged_floor_ceiling.stl": ("floorRefinementLevel", "floorFeatureLevel"),
}
```

Refinement-surfaces for scene-merged files: always emit `regions { ... }`
block with a per-solid entry. Each entry reads the appropriate
per-solid-category bucket level if the solid belongs to a known bucket
(e.g. rack frame side), else the file's unified level.

Refinement-surfaces for per-bucket files: single top-level `level (min max)`
entry derived from the bucket's surface level. NO `regions {}` needed (all
solids in the file share the same level).

For `merged_leakage_<N>U.stl`: `faceType internal + cellZone
leakage_<N>U + cellZoneInside inside`. Do NOT add this file to
createBafflesDict.

Features block: one entry per STL file, `level N` from the appropriate key
table.

### `_generate_create_baffles_dict` — baffle filter

The current dict emits a cyclic baffle pair for each perforated-baffle and
door solid. After redesign:

- `merged_server_<N>U.stl`: only the `outlet_p0` solid of a fan server gets
  cyclic/baffle treatment? No — fan server outlets are real patches (type patch),
  not baffles. createBaffles is only for cyclic pair creation. Revisit this
  in the spec: **who actually goes through createBaffles?**

  Answer: createBaffles is used for (a) perforated baffles to cyclic-pair
  them for porousBafflePressure BC, and (b) the original rack door design
  that used face zones. After this redesign:

  - Perforated free-standing baffles (in `merged_baffle.stl`): cyclic-paired via
    createBaffles (unchanged logic; uses the baffle's faceZone created by
    snappy refinementSurfaces faceType=internal + faceZone).
  - Doors (in `merged_door_<bucket>.stl`): cyclic-paired same as before.
  - Server inlet/outlet, blanking, partition: type patch/wall, NO
    createBaffles entry.
  - **Leakage: NO createBaffles entry** (user explicit requirement).

### `fields.py` — boundary condition emission

No changes needed. The patch names remain the same (solid names in the STL
files didn't change); only the STL grouping changed. BC assignment walks
`data["racks"][<name>].servers[].inletSolidName` etc., which still resolves
to the right solid names.

## Frontend changes (STL_generate.html)

### State additions

```javascript
// getInitialState() — cfdRoom block (additions)
serverFaceFeaturePerUnitLevel: {},           // new
blankingPlateFeaturePerUnitLevel: {},        // new
blankingPlateFeatureLevel: 2,                 // new unified (default 2)
rackPartitionRefinementLevel: [1, 1],         // new unified
rackPartitionPerUnitLevel: {},                // new per-span
rackPartitionFeatureLevel: 2,                 // new unified
rackPartitionFeaturePerUnitLevel: {},         // new per-span
rackLeakageFeatureLevel: 2,                   // new
rackLeakageFeaturePerUnitLevel: {},           // new
floorRefinementLevel: [1, 1],                 // new (was: defaulted to refinementLevel)
floorFeatureLevel: 2,                         // new

// Removed state keys (no-op on legacy load; the runner ignores them):
// - rackFramePerSideLevel: {}  // per-side rack-frame sub-buckets, now unused
```

### Panel rows

- **RACK 表面 (frame)** row: REMOVE feature-line input (lives on the
  outer "机架" mesh-tab row via `rackFeatureLevel`); **also REMOVE the
  four `top`/`bottom`/`left`/`right` per-side sub-buckets** — the entire
  rack-frame category now uses one unified surface pair, no sub-rows.
- **服务器面 (server)** row: keep existing feature-line input on unified;
  ADD per-bucket feature-line inputs alongside per-bucket surface inputs.
- **盲板 (blanking)** row: ADD feature-line input on unified (new); ADD
  per-bucket feature-line inputs.
- **Partition 隔板** row (NEW): entire new row modelled on server face
  (unified surface + unified feature + per-span buckets each with surface
  + feature).
- **贯通区域 (leakage)** row: ADD feature-line input on unified + per-bucket
  (for volume-refinement consistency with other rack-internal categories,
  even though it's not a real feature-line).
- **穿孔门 (door)** row: unchanged (Task 8-13 design). Feature level still
  follows surface max via `_door_level_for_rack` — no new UI needed.

### `_renderCategorySection` or equivalent

Extend the helper to render per-bucket **feature-line** inputs alongside the
existing per-bucket surface inputs. Each bucket row becomes
`[surface_min] [surface_max] [feature]` (3 inputs) instead of
`[surface_min] [surface_max]` (2 inputs).

### Unified broadcast

When a unified input changes, iterate all existing keys in the per-unit dict
and update the corresponding field to the new unified value.

For feature-line inputs: same rule, but the per-unit dict is the
`*FeaturePerUnitLevel` dict.

### Auto-detect

Update `_autoDetectCategory` to also set the per-bucket feature level = the
computed surface max.

### Export (`buildCfdExportJson`)

Emit the new meshConfig keys and the per-rack STL grouping metadata. Each
rack gets a per-category bucket tag so the runner (or the browser-side STL
writer) can group solids:

```json
"racks": {
  "A17": {
    ...,
    "minUSpanForDoorBucket": 1,                // existing
    "minUSpanForBlankingBuckets": [1, 2, 4],    // for debugging only, not required by runner
    ...
  }
}
```

The actual STL grouping is done by `buildGroupedStlMap` (frontend), which
reads the per-category per-bucket taxonomy and emits the right
`merged_<cat>_<N>U.stl` grouping.

## Migration / compatibility

- Old cfdproj files still load (new fields default to safe values).
- Old cfdrun files: the runner detects missing per-unit feature keys and
  falls back to the unified value (or `defaultFeatureLevel`).
- **Re-meshing is REQUIRED** for the refactor to take effect. Solve-only on
  an existing mesh does NOT pick up the new refinement.

## Testing

1. **Schema test**: new `MeshConfig` fields have expected defaults and
   survive pydantic round-trip.
2. **STL grouping unit test**: for a synthetic 3-rack scene (1U/2U/4U
   servers + 2U blanking + 3U leakage + partition in a 3U slot), assert
   `buildGroupedStlMap` produces exactly the expected file list with
   expected solid membership.
3. **snappy dict generation**: for a multi-rack fixture, assert that:
   - Scene merged files get `regions { ... }` blocks with per-solid levels
   - `merged_server_1U.stl` has single `level (surface_min surface_max)` +
     single `level N` features entry
   - `merged_leakage_1U.stl` has `faceType internal + cellZone` and is NOT
     in createBafflesDict
   - Patches on server inlet faces inherit parent surface level in the
     regions block
4. **Unified→bucket broadcast**: frontend static test on
   `setUnifiedRackCategory` — verify it mutates all existing per-unit keys.
5. **Export round-trip**: load a cfdproj, run auto-detect, export cfdrun,
   re-load, verify per-bucket levels preserved.
6. **E2E regression on `case/base_leakage`**: mesh generates successfully,
   `ls constant/triSurface/` shows ≤10 files (smaller scene than
   alibaba-hongchou), snappyHexMeshDict has the expected structure, solve
   starts without patch-mismatch errors.

   **Must use `case/base_leakage` for the redesign's iterative testing.**
   Do NOT default to `alibaba-hongchou` — its 200-rack / 1.7M-cell mesh
   costs 20-30 min per iteration and blocks development velocity.
   `alibaba-hongchou` will be re-tested later as a separate validation
   once `base_leakage` passes; each plan task's acceptance criteria
   targets `base_leakage` unless a step explicitly calls out alibaba.

## Rollout

Phase 1 (spec + plan): this document + the writing-plans output.
Phase 2 (backend): new runner emitter + tests.
Phase 3 (frontend): panel redesign + `buildGroupedStlMap` refactor + export.
Phase 4 (E2E): validate on alibaba-hongchou (unblocks the paused Task 15
from the earlier rack-door-refinement plan).

The previous rack-door-refinement plan's door-bucket logic is subsumed into
this redesign: `merged_door_<bucket>.stl` IS one of the per-bucket files.
The Task 10 `_autoDetectRackDoor` and related helpers stay; the only change
is the STL export combines multi-rack doors per bucket instead of one-file-
per-door.

## Risks & mitigations

- **Solid-name collisions across ranks**. When merging per-bucket, solid
  names must remain unique across all contributing racks. Mitigation:
  retain the existing `<rack_name>_<original_solid_name>` prefix
  convention; this is already enforced on the frontend export side.
- **Feature-edge extraction bias**. Merging e.g. all 1U-server inlet faces
  into one STL means surfaceFeatureExtract runs on the union of edges.
  Between-rack gaps will be detected as feature edges, but they're at
  rack boundaries (large spacing) and won't be finer than the largest
  rack cell anyway. Not a correctness issue.
- **Legacy case directories with old STL layout**. If a user solve-onlys
  against a mesh generated with the old layout, nothing changes (the
  solve doesn't re-read STLs). New mesh generation produces the new
  layout.
- **createBaffles path for leakage**. The explicit user requirement that
  leakage STLs generate NO patches means removing any existing leakage
  entry from createBafflesDict — verify none slips through via the
  perforated-baffle or wall-baffle paths.
- **Unused `wallBaffleSolids` slot on rack schema**. Verified empty across
  all 200 racks in the alibaba-hongchou test case, and no known UI path
  populates it. Treat as YAGNI: if a non-empty `wallBaffleSolids` is
  encountered during STL grouping, the runner emits a clear warning
  (pointing at this spec) and drops the entry — do NOT silently route it
  to `merged_baffle.stl` or any other bucket. Rationale: rack-internal
  geometry belongs to the 4 enumerated rack-internal categories (server,
  blanking, partition, leakage). Anything outside that taxonomy is a
  schema drift that deserves explicit notice rather than a silent
  fallback.
- **Rack-frame per-side bucketing removed**. The previous
  `rackFramePerSideLevel` dict and the four `top`/`bottom`/`left`/`right`
  sub-rows in the UI panel are deleted — one unified `rackRefinementLevel`
  pair + one `rackFeatureLevel` scalar cover the entire
  `merged_rack_frame.stl` category. Rationale: per-side refinement
  carries no physical meaning (the frame is a watertight picture-frame;
  its four sides share the same rail thickness and share mesh-quality
  requirements).
