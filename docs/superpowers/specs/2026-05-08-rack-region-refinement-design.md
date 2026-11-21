# Rack Region Refinement Redesign

> **EXTENDED (2026-05-12):** This spec's core design (single `rackRegionLevel` + searchBox per rack) is still valid. It was extended by `2026-05-12-thin-gap-auto-refinement-design.md` which adds auto-detection of thin (<2U) gaps and generates additional local refinement boxes.

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this spec.

**Goal:** Replace rack's per-component surface refinement system with a single `searchableBox` refinementRegion per rack, drastically simplifying both the UI and backend logic.

**Motivation:** The current rack refinement system has grown overly complex — per-solid surface refinement for rack_frame, server faces, blanking baffles, doors, partitions, and through-zones, each with [min, max] + feature level + per-unit bucket grouping + auto-detect safety checks + warning dialogs. In practice, a simple volume-based region refinement achieves equivalent mesh quality with far less configuration.

---

## Architecture Overview

**Before:** Each rack internal component (server, blanking, door, partition, through-zone) has its own `refinementSurfaces` entry with independently configurable [min, max] surface levels, feature edge levels, and per-unit-bucket overrides. Through-zones have separate `refinementRegions` entries. A complex auto-detect system warns users when levels are too low for the geometry.

**After:** Each rack gets one `searchableBox` refinementRegion (axis-aligned bounding box matching the rack's rotated extents). All rack internal components are removed from `refinementSurfaces` and `refinementRegions`. Only `merged_rack_frame.stl` retains a baseline `refinementSurfaces` entry at level (1 1) for proper boundary face capture. The UI shows a single integer input controlling the region level (default: 3).

---

## 1. Backend — snappyHexMeshDict Generation

**File:** `src/backend/aircooling/runner/stlgen_runner.py` → `_generate_snappy_hex_mesh_dict()`

### 1.1 Geometry section — add searchableBox per rack

For each rack model in the project, compute its axis-aligned bounding box (AABB) from `anchor + base(W,D,H) + quaternion`, then emit:

```
rackRegion_<sanitized_rack_name>
{
    type searchableBox;
    min (<xmin> <ymin> <zmin>);
    max (<xmax> <ymax> <zmax>);
}
```

**AABB calculation with rotation:**

The rack's local box has 8 corners at `(0,0,0)` to `(W, H, D)` (W=width, H=height, D=depth in local frame). Apply the quaternion rotation to each corner offset, add anchor, then take component-wise min/max:

```python
def rack_aabb(anchor, base, quaternion):
    """Compute axis-aligned bounding box for a rotated rack."""
    W, H, D = base["W"], base["H"], base["D"]
    # 8 corners of the local box (before rotation)
    local_corners = [
        (0, 0, 0), (W, 0, 0), (0, H, 0), (0, 0, D),
        (W, H, 0), (W, 0, D), (0, H, D), (W, H, D),
    ]
    # Rotate each corner by quaternion, add anchor
    rotated = [quat_rotate(quaternion, c) for c in local_corners]
    world = [(anchor["x"]+r[0], anchor["y"]+r[1], anchor["z"]+r[2]) for r in rotated]
    mins = [min(p[i] for p in world) for i in range(3)]
    maxs = [max(p[i] for p in world) for i in range(3)]
    return mins, maxs
```

The quaternion rotation helper already exists in the codebase or can be a simple implementation using the standard formula `q * v * q_conjugate`.

### 1.2 refinementSurfaces — skip rack internals

In the main STL file loop that builds `refine_lines`, skip the following patterns:

- `merged_server_face_*.stl` — server inlet/outlet faces
- `merged_blanking_*.stl` — blanking baffles
- `merged_door_*.stl` — perforated rack doors
- `merged_partition_*.stl` — rack internal partitions
- `merged_through_zone_*.stl` — through-zone volumes
- `merged_leakage_*.stl` — legacy through-zone files

**Keep:**
- `merged_rack_frame.stl` — at fixed level `(1 1)`, no per-unit lookup
- `merged_rack.stl` — if it still exists, at fixed level `(1 1)`

### 1.3 refinementRegions — add rack regions, remove through-zones

- Add each rack's `rackRegion_<name>` to the `rz_region_lines` list with `mode inside; levels ((1e15 <rackRegionLevel>));`
- Remove through-zone `refinementRegion` entries (both new `merged_through_zone_*.stl` and legacy `merged_leakage_*.stl`) — they are subsumed by the rack region
- Keep user-defined `searchableBox` zones from `meshConfig.refinementZones` (unchanged)

### 1.4 features — skip rack internal feature edges

Feature edge extraction (`surfaceFeatureExtract`) entries for rack internal STLs should also be skipped. Only `merged_rack_frame.stl` (and `merged_rack.stl` if present) retain feature edge entries.

---

## 2. Backend — MeshConfig Schema

**File:** `src/backend/aircooling/runner/cfdproj_schema.py`

### 2.1 New field

```python
rackRegionLevel: int = 3  # refinementRegion level for all racks
```

### 2.2 Fields to remove

All rack-internal refinement fields:

| Field | Purpose (removed) |
|-------|-------------------|
| `rackRefinementLevel` | rack frame surface [min, max] |
| `rackFeatureLevel` | rack frame feature edge level |
| `serverFaceRefinementLevel` | server face surface [min, max] |
| `serverFaceFeatureLevel` | server face feature level |
| `serverFacePerUnitLevel` | per-bucket server overrides |
| `baffleRefinementLevel` | blanking baffle surface [min, max] |
| `baffleFeatureLevel` | blanking feature level |
| `bafflePerUnitLevel` | per-bucket blanking overrides |
| `rackDoorRefinementLevel` | door surface [min, max] |
| `rackDoorFeatureLevel` | door feature level |
| `rackDoorPerUnitLevel` | per-bucket door overrides |
| `rackPartitionRefinementLevel` | partition surface [min, max] |
| `rackPartitionFeatureLevel` | partition feature level |
| `rackPartitionPerUnitLevel` | per-bucket partition overrides |
| `throughZoneRefinementLevel` | through-zone surface [min, max] |
| `throughZoneFeatureLevel` | through-zone feature level |
| `throughZonePerUnitLevel` | per-bucket through-zone overrides |

### 2.3 Backward compatibility

Old `.cfdproj` files containing the removed fields will be handled by Pydantic's `extra = "ignore"` configuration (already set on MeshConfig). The new `rackRegionLevel` defaults to 3 when absent.

---

## 3. Frontend — Mesh Tab UI

**File:** `STLGeneratorForOpenFoam/web/STL_generate.html`

### 3.1 HTML changes (lines ~154-164)

Replace the current rack section:
```html
<!-- OLD: 3 inputs for rack surface [min, max, feature] -->
<label>机架</label>
<input id="ms-rack-0" ...>  <!-- rackRefinementLevel[0] -->
<input id="ms-rack-1" ...>  <!-- rackRefinementLevel[1] -->
<input id="ms-feat-rack" ...>  <!-- rackFeatureLevel -->
<!-- OLD: expandable rack internal panel -->
<div>机架内设备加密 ▼</div>
<div id="rack-refine-panel">...</div>
```

With:
```html
<!-- NEW: single input for rack region level -->
<label data-i18n="label.rack_region">机架区域加密</label>
<input type="number" id="ms-rack-region" min="0" max="6" value="3"
       class="mesh-lvl-input" style="grid-column: span 3;"
       oninput="window.setMeshRefineLevel('rackRegionLevel',0,+this.value)">
```

The single input spans the 3 columns where [min, max, feature] used to be.

### 3.2 State initialization

In `state.cfdRoom` defaults (~line 3257):
```javascript
// OLD
rackRefinementLevel: [1, 1],
rackFeatureLevel: [2],
// ... many per-unit fields ...

// NEW
rackRegionLevel: 3,
```

### 3.3 syncMeshPanel changes

In `syncMeshPanel()` (~line 27323):
```javascript
// OLD
set('ms-rack-0', (cr.rackRefinementLevel || [1,1])[0]);
set('ms-rack-1', (cr.rackRefinementLevel || [1,1])[1]);
// ... rack panel rebuild ...

// NEW
set('ms-rack-region', cr.rackRegionLevel || 3);
```

### 3.4 Export (buildCfdrunJson / exportCFDProject)

In the meshConfig section of the cfdrun JSON export:
```javascript
// OLD
rackRefinementLevel: state.cfdRoom.rackRefinementLevel,
rackFeatureLevel: state.cfdRoom.rackFeatureLevel,
serverFaceRefinementLevel: ...,
// ... many fields ...

// NEW
rackRegionLevel: state.cfdRoom.rackRegionLevel || 3,
```

---

## 4. Frontend — Code Removal

### 4.1 Functions to remove

| Function | Lines (approx) | Purpose |
|----------|----------------|---------|
| `toggleRackRefinePanel()` | ~27851 | Toggle expand/collapse of rack refine panel |
| `buildRackRefinePanel()` | ~28092 | Build per-unit refinement UI rows |
| `scanRackDoorMinUnitBuckets()` | ~27535 | Bucket racks by min-U-span for doors |
| `scanThroughZoneUnitBuckets()` | ~27510 | Bucket through-zones by U-span |
| `scanRackPartitionBuckets()` | ~27555 | Bucket rack partitions |
| `_computeServerBucketKeyForSolid()` | ~27578 | Map solid name to bucket key |
| Rack refinement warning dialog | ~30459 | "rack-refinement-warning-dialog" popup |

### 4.2 Functions to simplify

| Function | Change |
|----------|--------|
| `computeMinSafeLevel()` | Remove rack-related branches (server, blanking, door, partition, through-zone) |
| `setMeshRefineLevel()` | Remove handling of all removed rack keys |
| `syncMeshPanel()` | Remove rack panel rebuild, simplify to single field sync |
| `autoDetectRefinementLevels()` | Remove rack-related auto-detect logic |

### 4.3 Functions to keep

| Function | Reason |
|----------|--------|
| `_computeRackMinUSpan()` | Check if blockMesh alignment depends on it — if not, also remove |
| blockMesh alignment logic | Explicitly preserved per user decision |

---

## 5. Backend — Code Removal

**File:** `src/backend/aircooling/runner/stlgen_runner.py`

### 5.1 In `_generate_snappy_hex_mesh_dict()`

- Remove all `mc.get("serverFaceRefinementLevel")` / `mc.get("baffleRefinementLevel")` / etc. lookups
- Remove `_bucket_level_lookup()` calls for rack components
- Remove `through_zone_region_lines` collection logic (through-zones no longer need refinementRegion entries)
- Remove per-solid classification switch for server_inlet_solids, nonfan_solids, etc. — these STLs are simply skipped in refinementSurfaces
- Add rack AABB computation + searchableBox emission

### 5.2 Helper functions to review for removal

- `_bucket_level_lookup()` — if only used for rack components, remove entirely
- Any rack-specific refinement computation functions

---

## 6. Data Flow Summary

```
Frontend                          cfdrun JSON              Backend
────────                          ──────────               ───────
[机架区域加密: 3]                                           
  │                                                        
  ├─ state.cfdRoom.rackRegionLevel = 3                     
  │                                                        
  └─ export ──► meshConfig: {          ──► MeshConfig:     
                  rackRegionLevel: 3         rackRegionLevel=3
                }                                          
                                                           
                                       ──► snappyHexMeshDict:
                                             geometry {
                                               rackRegion_Rack1 {
                                                 type searchableBox;
                                                 min (2.5 0 1.1);
                                                 max (3.7 2.2 5.9);
                                               }
                                             }
                                             refinementSurfaces {
                                               merged_rack_frame {
                                                 level (1 1);  // baseline
                                               }
                                               // NO server/blanking/door/...
                                             }
                                             refinementRegions {
                                               rackRegion_Rack1 {
                                                 mode inside;
                                                 levels ((1e15 3));
                                               }
                                             }
```

---

## 7. Preserved Behavior

- **blockMesh alignment** — rack-aware blockMesh grid alignment is preserved
- **baseSize = 0.34** — unchanged default
- **Non-rack refinement** — room wall, ACU, box, partition wall, baffle/floor all unchanged
- **User-defined refinementZones** — `meshConfig.refinementZones` searchableBox entries remain
- **Rack STL export** — frontend still exports all rack component STLs (frame, server, blanking, etc.) for correct geometry. They just aren't individually refined in snappyHexMeshDict.
- **Rack internal component boundary conditions** — these STLs are still in the geometry section for snappy to capture as boundary patches. They just don't have refinementSurfaces entries (snappy defaults to level 0 for unlisted surfaces, but the refinementRegion ensures adequate cell size).

---

## 8. i18n

**New keys:**
- `label.rack_region`: "机架区域加密" / "Rack Region Refinement"

**Keys to remove:**
- `label.rack_surface_refinement` and all rack-internal refinement labels
- `dialog.rack_refinement_title`, `dialog.rack_refinement_desc`
- `btn.server_refinement_back`, `btn.server_refinement_ignore`

---

## 9. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Rack internal surfaces not captured by snappy at default level 0 | refinementRegion level 3 ensures cells inside the rack are small enough to capture internal geometry |
| Old cfdproj files with removed fields | Pydantic `extra="ignore"` handles gracefully; new default rackRegionLevel=3 applied |
| Through-zone airflow channels under-refined | Rack region level 3 matches or exceeds typical through-zone levels (usually 2-3) |
| merged_rack_frame needs to stay in refinementSurfaces | Explicitly preserved at baseline (1,1) |
