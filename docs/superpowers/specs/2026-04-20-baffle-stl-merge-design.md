# Baffle STL Merge Optimization

## Problem

The runner's `_split_internal_face_solids()` extracts every baffle/door solid into its own `baffle_*.stl` file. Each file gets a separate geometry + refinementSurfaces + surfaceFeatureExtract entry in snappyHexMeshDict. For alibaba-hangchou (200 racks × 2 doors + wall baffles = 477 files), this produces a 107K-line snappyHexMeshDict and 57-minute mesh time. snappyHexMesh has O(cells × surfaces) complexity, so the surface count — not refinement level — is the dominant performance factor.

## Root Cause

snappyHexMesh ignores per-region `faceZone` directives. The only way to get a faceZone is at the surface level — one faceZone per STL file. The current code creates one file per baffle solid to guarantee one faceZone per solid for createBaffles.

## Experimental Validation (Base_leakage)

Verified on Base_leakage (4 racks, 2 doors, 3 wall baffles, perforated floor):

1. **per-region `faceZone` does NOT work** — snappy silently ignores it regardless of whether `regions {}` is declared in geometry or not.
2. **`faceType baffle` at surface level works** — snappy directly creates master/slave boundary patches (type wall), skipping createBaffles.
3. **Multi-solid merge with `faceType baffle` preserves face counts** — wall baffle master faces: 9220 vs 9277 original (0.6% noise). Door master faces: 2652 vs 2618 original (+1.3% noise).
4. **`faceType baffle` creates `type wall` patches** — suitable for wall baffles but NOT for porousBafflePressure (which requires `type cyclic`).

## Design

### Merge Strategy

Groups where all members share identical BC settings are merged into one STL file. Items that need individual porosity control stay as individual files.

| File | Contents | Merge | faceType | createBaffles? | Patch type |
|------|----------|-------|----------|---------------|------------|
| `merged_wall_baffle.stl` | All wall baffles: wall baffle models, wallBaffle rack solids (half-dissolved U-slot), wallBaffle floor remaining-area | N → 1 | `baffle` | No | wall (correct for no-slip) |
| `merged_rack_door.stl` | All rack perforated doors | 400 → 1 | `internal` | Yes (cyclic) | cyclic (needed for porousBafflePressure) |
| `perforated_baffle_<name>.stl` (individual) | Each perforated baffle model | unchanged | `internal` | Yes (cyclic) | cyclic (needed for porousBafflePressure) |
| `perforated_floor_<name>.stl` (individual) | Each perforated floor patch | unchanged | `internal` | Yes (cyclic) | cyclic (needed for porousBafflePressure) |

The difference in `faceType` is driven by the BC requirement:

- **Wall baffles** need `type wall` patches → `faceType baffle` directly produces these, no createBaffles needed.
- **Perforated items** need `type cyclic` patches for porousBafflePressure → `faceType internal` + createBaffles (with `type cyclic`) is the only path.

### Key Constraint: Uniform Door Porosity

All rack doors MUST share the same `openAreaPct`. The frontend validates this when the user clicks "generate mesh" — if any rack has a different door porosity, show an error dialog and block mesh generation.

### Rack Door Refinement Simplification

Since all doors merge into one file, per-U-slot-bucket door refinement is removed. The door refinement degrades to a single unified setting:

- `rackDoorRefinementLevel`: `[min, max]` — surface refinement level
- `rackDoorFeatureLevel`: scalar — feature edge refinement level

Removed settings (no longer needed):
- `rackDoorPerMinUnitLevel` — per-bucket surface levels
- `rackDoorFeaturePerMinUnitLevel` — per-bucket feature levels

Frontend: the door refinement section in the mesh tab simplifies to 3 inputs (surface min, surface max, feature level). No per-bucket rows.

### Porosity Change Without Re-meshing

Changing porosity/openAreaPct in the UI only updates BC values (porousBafflePressure D/I coefficients) in the 0/ field files. Patch names are stable because they derive from the faceZone name (`merged_rack_door_master/_slave`), not from porosity values. Solve-only re-run picks up the new BC without touching the mesh.

### Pipeline Changes

#### 1. Runner: `_split_internal_face_solids()` (stlgen_runner.py)

Currently extracts ALL baffle/door solids into individual files. Change to:

- **Wall baffles** (wall baffle models + wallBaffle rack solids + wallBaffle floor remaining-area): merge into `merged_wall_baffle.stl`. All wall baffles share one faceZone.
- **Rack doors** (from `merged_door_<N>U.stl` bucket files): merge into `merged_rack_door.stl`. All doors share one faceZone.
- **Perforated baffle models**: extract to individual `perforated_baffle_<name>.stl`.
- **Perforated floor patches**: extract to individual `perforated_floor_<name>.stl`.

#### 2. Runner: `_generate_snappy_hex_mesh_dict()` (stlgen_runner.py)

**Geometry section** — no `regions {}` block for merged files:

```
merged_wall_baffle.stl  { type triSurfaceMesh; name merged_wall_baffle; }
merged_rack_door.stl    { type triSurfaceMesh; name merged_rack_door; }
```

**refinementSurfaces section**:

```
merged_wall_baffle
{
    level (1 1);
    faceType baffle;
    faceZone merged_wall_baffle;
}

merged_rack_door
{
    level (<rackDoorRefinementLevel>);
    faceType internal;
    faceZone merged_rack_door;
}
```

Door refinement level: unified `rackDoorRefinementLevel` from meshConfig (single `[min, max]`, no per-bucket).

Individual perforated baffles/floors: unchanged (`faceType internal` + `faceZone <name>` per file).

#### 3. Runner: `_generate_create_baffles_dict()` (stlgen_runner.py)

- `merged_wall_baffle`: **no entry** — `faceType baffle` already created wall patches.
- `merged_rack_door`: **one cyclic entry** — faceZone `merged_rack_door` → patches `merged_rack_door_master` + `merged_rack_door_slave`.
- Individual perforated items: unchanged (one cyclic entry per item).

#### 4. Runner: `_convert_wall_baffle_patches_to_wall()` (stlgen_runner.py)

- `merged_wall_baffle` patches: `faceType baffle` already creates `type wall` — no conversion needed.
- `merged_rack_door` patches: created via createBaffles as `type cyclic` — no conversion needed.
- Conversion logic only applies to remaining individual wall baffles (if any).

#### 5. Runner: `_build_bridge_bc()` (stlgen_runner.py)

Patch naming convention per path:

| File | Path | Master patch name | Slave patch name |
|------|------|------------------|-----------------|
| `merged_wall_baffle.stl` | `faceType baffle` (snappy) | `merged_wall_baffle_<firstSolid>` | `merged_wall_baffle_<firstSolid>_slave` |
| `merged_rack_door.stl` | `faceType internal` + createBaffles | `merged_rack_door_master` | `merged_rack_door_slave` |
| `perforated_baffle_<name>.stl` | `faceType internal` + createBaffles | `<name>_master` | `<name>_slave` |
| `perforated_floor_<name>.stl` | `faceType internal` + createBaffles | `<name>_master` | `<name>_slave` |

Bridge BC registration:

- Wall baffles: register master/slave as `bc_type=wall_baffle`.
- Rack doors: register `merged_rack_door_master/_slave` as `bc_type=perforated_baffle` with the shared σ.
- Individual perforated: unchanged.

#### 6. Runner: Dual-side locationsInMesh seeds

Seeds must still cover all baffle locations even though they're merged. The seed generation iterates all wall baffle solids and all door solids, generating dual-side points for each. Seeds reference the merged faceZone name.

#### 7. Frontend: Door porosity validation (STL_generate.html)

In `runMeshGeneration()`, before STL export:

1. Collect all enabled rack doors' `openAreaPct` values.
2. If more than one unique value exists → error dialog listing conflicting racks → block.
3. All same (or 0-1 doors) → proceed.

#### 8. Frontend: STL export (`buildGroupedStlMap`)

Replace per-rack door export (current `merged_door_<N>U.stl` per-bucket) with a single merged export:

```javascript
{ key: 'rack_door', filename: 'merged_rack_door.stl', kinds: ['rack'], doorOnly: true }
```

All doors from all racks go into one file. The `merged_door_<N>U.stl` per-bucket files are no longer generated.

#### 9. Frontend: Mesh tab door refinement UI

Replace the per-bucket door refinement section with a single unified control:

- Surface level: `rackDoorRefinementLevel` `[min, max]` — two number inputs
- Feature level: `rackDoorFeatureLevel` — one number input

Remove the per-bucket door rows from `_renderCategorySection()`. The auto-detect `_autoDetectRackDoor()` function simplifies to compute one level for all doors (no bucket iteration).

#### 10. Schema: MeshConfig (cfdproj_schema.py)

Remove per-bucket door fields:

- Remove: `rackDoorPerMinUnitLevel: Dict[str, List[int]]`
- Remove: `rackDoorFeaturePerMinUnitLevel: Dict[str, int]`
- Keep: `rackDoorRefinementLevel: List[int]` (unified)
- Keep: `rackDoorFeatureLevel: Optional[int]` (unified)

### Files Changed

| File | Change |
|------|--------|
| `src/backend/aircooling/runner/stlgen_runner.py` | `_split_internal_face_solids`: merge wall baffles + doors; `_generate_snappy_hex_mesh_dict`: merged entries; `_generate_create_baffles_dict`: merged door entry; `_build_bridge_bc`: merged patch names; seed generation; remove per-bucket door level logic |
| `src/backend/aircooling/runner/cfdproj_schema.py` | Remove `rackDoorPerMinUnitLevel`, `rackDoorFeaturePerMinUnitLevel` from MeshConfig |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Door porosity validation; `buildGroupedStlMap` door export; door refinement UI simplification; remove per-bucket door auto-detect |
| `src/backend/aircooling/runner/tests/` | Tests for merge logic, face count validation |

### Test Plan

1. **Base_leakage**: Full mesh pipeline with merged baffles. Compare patch face counts against original — verify < 2% deviation.
2. **alibaba-hangchou**: Verify snappyHexMeshDict shrinks from ~107K to ~5K lines. Verify mesh time drops from ~57 min to < 10 min.
3. **Frontend**: Test door porosity mismatch error dialog.
4. **Porosity change**: Change door openAreaPct, run solve-only, verify BC updates without mesh regeneration.
