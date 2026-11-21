# Per-Unit Server Face Refinement & Mesh Capture Pre-check

> **SUPERSEDED (2026-05-12):** This spec is historical only. The per-unit bucket system was removed and replaced by unified `rackRegionLevel` + thin-gap auto-refinement. See `2026-05-12-thin-gap-auto-refinement-design.md` for the current design.

> **Goal:** Prevent snappyHexMesh from missing small server fan patches (especially 1U = 44.45mm) by giving each U-height category its own refinement level, with automatic safety checks before mesh generation.

> **Architecture:** Frontend-driven pre-check + per-U-bucket refinement UI in the mesh tab, with backend support for per-solid refinement level dispatch in snappyHexMeshDict.

---

## Problem Statement

In CMI-Guichou, a 1U fan-server (`G8_S4320_56TC`) has inlet/outlet patches only 44.45mm tall. With `baseSize=0.4m` and `serverFaceRefinementLevel=[1,1]`, the finest cell is 200mm — far too coarse to capture a 44.45mm face. snappyHexMesh silently drops the patch, and the solver crashes when `fields.py` generates an expression BC referencing the missing `inlet_patch`.

## Section 1: Data Model & Calculation

### U-Height Bucketing

Scan all rack models' `fanServer` equipment. Group by `spanUnits`:
- Bucket key = `spanUnits` value (1, 2, 3, ... up to any value)
- Only buckets that exist in the current scene are created
- Each bucket tracks: count of servers, patch height, user-set refinement, computed minimum safe level

### Per-Bucket Data Structure

```javascript
// In state.cfdRoom
serverFaceRefinementLevel: [1, 1],       // "unified" value (existing field, preserved)
serverFacePerUnitLevel: {                 // NEW — per-bucket overrides
  "1": [5, 5],    // 1U servers
  "2": [3, 3],    // 2U servers
  "13": [1, 1],   // 13U servers
}
```

### Minimum Safe Level Formula

```
patchHeight = spanUnits * RACK_DEFAULT_UNIT_HEIGHT   // 0.04445m per U
safety: need at least 2 cells across the patch height
→ maxCellSize = patchHeight / 2
→ minMaxLevel = ceil(log2(baseSize / maxCellSize))
→ minMinLevel = max(minMaxLevel - 1, 0)
→ minSafeLevel = [minMinLevel, minMaxLevel]
```

Example: baseSize=0.4m, 1U=0.04445m → maxCellSize=0.022m → minMaxLevel=ceil(log2(18.18))=ceil(4.18)=5 → minSafeLevel=[4,5]

### cfd_export.json Output

```json
{
  "meshConfig": {
    "serverFaceRefinementLevel": [1, 1],
    "serverFacePerUnitLevel": {
      "1": [5, 5],
      "2": [3, 3],
      "13": [1, 1]
    }
  }
}
```

Backward compatibility: if `serverFacePerUnitLevel` is absent (old project files), all server solids use `serverFaceRefinementLevel`.

---

## Section 2: UI Layout

### Location

Mesh tab, the existing "服务器面" row. Transforms into a collapsible section.

### Collapsed State (default)

```
服务器面  [min] [max]      ▼
```

- Same as current: two number inputs for serverFaceRefinementLevel
- Third column (feature) stays empty (server faces share rack's feature level)
- New: ▼ toggle button on the right to expand

### Expanded State

```
服务器面  [min] [max]      ▲
  ├ 1U (3台)   [5] [5]    ⚠️
  ├ 2U (2台)   [3] [3]    ✓
  └ 13U (4台)  [1] [1]    ✓
  [🔍 自动检测]
```

- One row per U-height bucket that exists in the scene
- Each row: label `{N}U ({count}台)`, two number inputs [min, max], status icon
- Status: ⚠ orange if current < minSafeLevel, ✓ green if sufficient
- Input box border turns orange when value is below safe threshold
- Bottom: "🔍 自动检测" button

### Behavior

- **Modify collapsed row** → writes to `serverFaceRefinementLevel` AND overwrites all buckets in `serverFacePerUnitLevel` → all expanded rows sync
- **Modify an expanded row** → writes only to that bucket in `serverFacePerUnitLevel`, does not affect other buckets or the collapsed row
- **"🔍 自动检测" button** → for each bucket where current < minSafeLevel, auto-fill minSafeLevel + turn border orange + log operation

---

## Section 3: Interaction Flows

### Flow 1: User switches to mesh tab (first time or after scene change)

1. Scan all rack fanServer equipment, group by spanUnits → build bucket list
2. If `serverFacePerUnitLevel` has saved values → restore; remove buckets no longer in scene; add new buckets with unified value
3. If absent (new/old project) → initialize all buckets from `serverFaceRefinementLevel`
4. Compute minSafeLevel for each bucket using current baseSize
5. Auto-fill: for each bucket where current < minSafeLevel → set to minSafeLevel + mark orange
6. Log: "自动调整: 1U [1,1]→[5,5], 2U [1,1]→[3,3]"

### Flow 2: User clicks "🔍 自动检测"

Same as steps 4-6 of Flow 1.

### Flow 3: User clicks "执行网格生成"

1. Run existing seed collision check
2. Run server face capture check: for each bucket, compare current vs minSafeLevel
3. If any bucket insufficient → show warning dialog (seed-collision style):
   - **Title:** "服务器面网格精度预警"
   - **Description:** "以下 U 位高度的服务器面加密层级不足，snappyHexMesh 可能无法捕获这些 patch，导致求解失败。"
   - **Per-item:** `1U 服务器 (3台): 当前 [1,1], 建议至少 [5,5] — patch 高度 44.5mm, 当前最细 cell 200mm`
   - **Buttons:** "返回修改" / "忽略并继续生成"
4. If all sufficient or user ignores → proceed with mesh generation

### Flow 4: Scene changes (add/delete/modify rack equipment)

Next time user switches to mesh tab → re-scan and re-bucket. New buckets inherit unified value. Deleted buckets are cleaned from `serverFacePerUnitLevel`.

---

## Section 4: Backend Changes (stlgen_runner.py)

### Per-Solid Refinement Dispatch

Current code (line ~2298-2303) applies `rl_server` uniformly to all server inlet/outlet solids.

Change to:
1. Read `serverFacePerUnitLevel` dict from meshConfig
2. Build a mapping: `solid_name → spanUnits` by cross-referencing the server equipment list in cfd_export.json (each server has `inletSolidName`, `outletSolidName`, and `spanSlots`)
3. For each server solid, look up its spanUnits → find the bucket → use that bucket's refinement level
4. Fallback: if no per-unit level found, use `rl_server` (backward compat)

```python
# Pseudocode
per_unit = mc.get("serverFacePerUnitLevel", {})
for solid in server_solids:
    span = solid_to_span[solid]
    bucket_key = str(span)
    rl = per_unit.get(bucket_key, rl_server)
    # use rl[0], rl[1] for this solid's level
```

---

## Section 5: Internationalization Keys

```
dialog.server_refinement_title: "服务器面网格精度预警" / "Server Face Mesh Accuracy Warning"
dialog.server_refinement_desc: "以下 U 位高度的服务器面加密层级不足..." / "The following server face refinement levels are insufficient..."
dialog.server_refinement_item: "{uHeight}U 服务器 ({count}台): 当前 [{curMin},{curMax}], 建议至少 [{safeMin},{safeMax}] — patch 高度 {patchH}mm, 当前最细 cell {cellSize}mm" / "{uHeight}U servers ({count} units): current [{curMin},{curMax}], recommended [{safeMin},{safeMax}] — patch height {patchH}mm, finest cell {cellSize}mm"
btn.server_refinement_back: "返回修改" / "Back to Edit"
btn.server_refinement_ignore: "忽略并继续生成" / "Ignore and Continue"
label.per_unit_refinement: "{n}U ({count}台)" / "{n}U ({count} units)"
btn.auto_detect_refinement: "🔍 自动检测" / "🔍 Auto Detect"
log.auto_adjust_refinement: "自动调整服务器面加密: {changes}" / "Auto-adjusted server face refinement: {changes}"
```

---

## Files to Modify

| File | Change |
|------|--------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Collapsible UI, bucketing logic, auto-detect function, pre-check dialog, export integration |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | New i18n keys |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | New i18n keys |
| `dctwin/dctwin/runner/stlgen_runner.py` | Per-solid refinement dispatch in `_generate_snappy_hex_mesh_dict()` |

---

## Out of Scope

- Post-mesh validation (comparing expected vs actual patches) — deferred to future work
- Separate `.eMesh` feature files for server faces — architectural limitation of snappyHexMesh per-region features
- fields.py fallback when inlet_patch is missing — separate defensive fix (should be done regardless)
