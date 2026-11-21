# Rack Thin-Gap Auto-Refinement & Per-Unit Bucket Cleanup

> **Goal:** Fix mesh generation failure in thin (<2U) gaps between rack equipment by auto-detecting under-resolved gaps and generating local higher-level refinement boxes. Simultaneously clean up the dead per-unit bucket refinement system.

> **Predecessor:** Supersedes `2026-04-07-rack-surface-refinement-design.md` per-unit bucket architecture. The per-unit system was never wired to the frontend and is replaced by this simpler approach.

---

## Problem Statement

When two rack equipment items are separated by a small gap (e.g., 1U = 48.89mm), snappyHexMesh cannot resolve the gap at the current default `rackRegionLevel = 3`:

- Base cell size ≈ 310mm → level 3 cell = 38.75mm
- 1U gap / 38.75mm = **1.26 cells** — snappy needs ≥ 2 to properly snap
- Result: mesh faces in the gap degenerate (e.g., `fan_server_1_top` gets only 3 faces instead of ~100+)

The existing per-unit bucket system (`_BUCKET_FILE_PATTERNS`, `serverFacePerUnitLevel`, etc.) was designed to address this but was never connected to the frontend, never exported in cfdrun files, and never exercised at runtime — it is dead code.

---

## Section 1: Simplification — Remove Per-Unit Bucket System

### Delete

| Item | Location |
|------|----------|
| `_BUCKET_FILE_PATTERNS` dict | `stlgen_runner.py` line 106-117 |
| `_bucket_level_lookup()` | `stlgen_runner.py` line 147-161 |
| `_bucket_feature_lookup()` | `stlgen_runner.py` line 164-180 |
| All per-unit meshConfig reads | Any code reading `serverFacePerUnitLevel`, `blankingPlatePerUnitLevel`, `rackLeakagePerUnitLevel`, `rackPartitionPerUnitLevel`, `serverFaceFeaturePerUnitLevel`, `blankingPlateFeaturePerUnitLevel`, etc. |
| Per-unit unified fallback reads | `serverFaceRefinementLevel`, `blankingPlateRefinementLevel`, `rackThroughZoneRefinementLevel`, `rackPartitionRefinementLevel` and their feature-level siblings |

### Keep

| Item | Reason |
|------|--------|
| `_parse_bucket_from_filename()` | Still needed to classify STL files by category (server/blanking/through-zone/door) during snappyHexMeshDict generation |

### Unified Rule

All rack-internal surfaces (server inlet/outlet/top/bottom, blanking panels, through-zone baffles, partitions, frame regions) use a single level pair in `refinementSurfaces`:

```python
rack_region_level = mc.get("rackRegionLevel", 3)
rl_rack_internal = [rack_region_level, rack_region_level]
```

Feature lines for rack-internal surfaces: continue skipping eMesh (`_EMESH_SKIP_PREFIXES`), rely on `implicitFeatureSnap`. Rack frame feature level synced to `rackRegionLevel`.

### Frontend

No changes. The single `ms-rack-region` input field → `meshConfig.rackRegionLevel` → controls everything.

---

## Section 2: Thin-Gap Auto-Detection Algorithm

### Execution Point

In `_generate_snappy_hex_mesh_dict()`, after generating main rack searchBox regions and before writing the final snappyHexMeshDict content.

### Precondition

Only racks with `blankingPanelType == "typicalSmallGap"` participate. Racks with `fullySealed` type have no air gaps and do not need this detection.

### Algorithm

```
For each rack where blankingPanelType == "typicalSmallGap":
  1. Collect all server-occupied U ranges:
       occupied = {(srv.startSlot, srv.startSlot + srv.spanSlots - 1) for srv in rack.servers}
  2. Full U range = [0, unitCount - 1]
  3. Compute unoccupied U positions = full range - occupied
  4. Merge contiguous unoccupied positions into ranges: [(start, end), ...]
  5. For each range:
       gapHeight = (end - start + 1) × unitHeight
       cellSize  = baseSize / 2^rackRegionLevel
       cellCount = gapHeight / cellSize
       if cellCount < 2:
         safeLevel = ceil(log2(baseSize × 2 / gapHeight))
         boxLevel  = max(safeLevel, rackRegionLevel + 1)
         → Emit thin-gap refinement searchBox at boxLevel
         → Log detection info
```

### Safety Formula

```
safeLevel = ceil(log2(baseSize × minCells / gapHeight))
```

Where `minCells = 2` (require at least 2 cells across the gap).

### Example (case_00_sketch, Rack_Col3_4)

| Parameter | Value |
|-----------|-------|
| unitHeight | 48.89mm |
| baseSize | ~310mm |
| rackRegionLevel | 3 |
| Level 3 cell | 38.75mm |

| Gap Range | Height | Cells@L3 | Trigger? | boxLevel |
|-----------|--------|----------|----------|----------|
| units 0-28 | 1418mm | 36.6 | No | — |
| **unit 34** | **48.89mm** | **1.26** | **Yes** | **4** |
| units 36-42 | 342mm | 8.8 | No | — |

---

## Section 3: Thin-Gap SearchBox Geometry

### Coordinate Framework

Reuses the exact same `searchableRotatedBox` framework as the main rack region box: same `e1/e2/e3` unit direction vectors (from rack quaternion rotation), different origin offset and smaller span.

### Rack Local Coordinate System

- e1 → width direction (W)
- e2 → height direction (H)
- e3 → depth direction (D)

### Box Dimensions in Rack-Local Space

```
slotMargin   = (W - W × slotWidthRatio) / 2
slotWidth    = W × slotWidthRatio
channelDepth = D - inletOffset - outletOffset
gapStartH    = startUnit × unitHeight
gapHeight    = (endUnit - startUnit + 1) × unitHeight
```

### World-Space Origin

Offset from rack anchor along the rotated axes:

```
origin = anchor
       + slotMargin × e1_len × e1
       + gapStartH  × e2_len × e2
       + inletOffset × e3_len × e3
```

Where `e1, e2, e3` are normalized direction vectors and `e1_len, e2_len, e3_len` are their pre-normalization magnitudes (incorporating scale).

### Span

```
span = (slotWidth × e1_len,  gapHeight × e2_len,  channelDepth × e3_len)
```

### Naming Convention

```
rackThinGapRegion_{sanitized_rack_name}_u{startUnit}_u{endUnit}
```

### snappyHexMeshDict Output

```c++
// geometry block
rackThinGapRegion_Rack_Col3_4_u34_u34
{
    type searchableRotatedBox;
    origin (8.052000 1.711111 3.620000);
    e1 (0.000000 0.000000 -1.000000);      // same as rack main box
    e2 (0.000000 1.000000 0.000000);
    e3 (1.000000 0.000000 0.000000);
    span (0.640000 0.048889 0.720000);      // slotW × gapH × channelD
}

// refinementRegions block
rackThinGapRegion_Rack_Col3_4_u34_u34
{
    mode inside;
    levels ((1e15 4));
}
```

### Fallback (no quaternion)

When quaternion data is missing (legacy cfdrun), skip thin-gap box generation for that rack — only modern exports support the rotated box. Log a warning.

---

## Section 4: Code Changes

### New Function

```python
def _detect_thin_gap_regions(
    racks: Dict[str, Any],
    base_size: float,
    rack_region_level: int,
    min_cells: int = 2,
) -> List[Dict]:
    """Scan racks for thin unoccupied U-slot gaps that need higher refinement.

    Returns list of dicts, each containing:
      rack_name, start_unit, end_unit, box_level,
      gap_height, geom (geometry data for searchBox generation)
    """
```

### Modified Function

`_generate_snappy_hex_mesh_dict()`:

1. After building main rack searchBox regions (existing code at ~line 4697-4774)
2. Call `_detect_thin_gap_regions()`
3. For each detected gap:
   - Compute searchableRotatedBox origin + span using rack geometry
   - Append to `geom_lines` (geometry block)
   - Append to `rz_region_lines` (refinementRegions block)
4. Log each detection

### Deleted Code

- `_BUCKET_FILE_PATTERNS` dict (~line 106-117)
- `_bucket_level_lookup()` (~line 147-161)
- `_bucket_feature_lookup()` (~line 164-180)
- All per-unit/unified key reads from meshConfig (scattered through `_generate_snappy_hex_mesh_dict`)
- Simplify per-region emission in refinementSurfaces: all rack-internal solids → `rl_rack_internal`

### Unchanged

- Frontend: no changes
- cfdrun schema: no new fields
- `_parse_bucket_from_filename()`: kept for STL category classification
- eMesh skip logic: unchanged
- Main rack searchBox generation: unchanged

### Logging

```
INFO  Rack 'Rack_Col3_4': thin gap at units 34-34 (48.89mm, 1.26 cells at level 3)
        → auto-refinement box at level 4 (18.75mm cells, 2.61 cells across gap)
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Cleanup breaks existing cfdrun files with per-unit keys | Per-unit keys were never populated — meshConfig is always empty or has only `rackRegionLevel`/`baseSize`. No breakage. |
| Thin-gap box adds excessive cells | Box is tightly scoped: slot width × gap height × channel depth. For a 1U gap, the volume is tiny. Estimated impact: <5% cell count increase. |
| Multiple thin gaps per rack | Each gap gets its own independent box. Rare in practice. |
| Rack without quaternion | Thin-gap boxes skipped for legacy files; log warning. Main rack AABB box still works. |
