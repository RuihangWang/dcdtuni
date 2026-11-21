# Rack Surface Per-Unit Refinement — Blanking Plates & Leakage Zones

> **SUPERSEDED (2026-05-12):** This spec is historical only. The per-unit bucket system (`blankingPlatePerUnitLevel`, `rackLeakagePerUnitLevel`, etc.) was removed. See `2026-05-12-thin-gap-auto-refinement-design.md` for the current design.

> **Goal:** Extend the per-U-height auto-detect refinement system to cover all three rack surface types: server faces, blanking plates (wall patch + wall baffle), and leakage zones (cellZone).

> **Architecture:** Expand the existing collapsible panel into a unified "Rack Surface Refinement" section with three sub-categories, each with independent per-U-bucket scanning, auto-detect, and backend dispatch. Decouple rack blanking plates from the standalone baffle refinement key.

> **Predecessor:** `2026-04-06-per-unit-server-refinement-design.md` — server faces only. This spec extends it.

---

## Problem Statement

The existing per-unit refinement system only handles fan-server inlet/outlet patches. Rack U-slots produce two other surface types that can also be very small (1U = 44.5mm):

1. **Blanking plates** — empty U-slot walls. Two sub-types share one refinement:
   - **Regular wall patch** (`rack_io_solids`): `patchInfo { type patch; }` — currently uses `rl_rack`
   - **Wall baffle** (`rack_wall_baffle_solids`): `faceType internal; faceZone ...` + createBaffles → wall — currently uses `rl_baffle` (shared with standalone baffle models)

2. **Leakage zones** — through-slot gaps where both front and back are deleted: `faceType internal; cellZone ...; cellZoneInside inside;` — currently uses `rl_leakage`

Both types need the same minimum-cell-size safety check as server faces. Additionally, rack blanking plates must be decoupled from standalone baffle model refinement.

---

## Section 1: Solid Classification (Backend)

| Solid Source | Detection | New Refinement Key | Fallback | snappy Type |
|---|---|---|---|---|
| Rack fan server inlet/outlet | `server_inlet_solids` / `server_outlet_solids` | `serverFacePerUnitLevel` | `rl_server` | boundary patch |
| Rack blanking plate (wall patch) | `rack_io_solids` (regex `_u\d`) | `blankingPlatePerUnitLevel` | `rl_rack` | boundary patch |
| Rack blanking plate (wall baffle) | `rack_wall_baffle_solids` | `blankingPlatePerUnitLevel` | `rl_baffle` | faceType internal + faceZone |
| Rack leakage zone | `leakage_solids` | `rackLeakagePerUnitLevel` | `rl_leakage` | faceType internal + cellZone |
| Standalone baffle model | remaining `wall_baffle_solids` (non-rack) | `rl_baffle` (unchanged) | — | faceType internal + faceZone |
| Standalone perforated floor | `perforated_solids` | `rl_baffle` (unchanged) | — | faceType internal + faceZone |

Key decoupling: rack blanking plates get their own per-unit key, independent from `baffleRefinementLevel` which continues to control standalone baffles and perforated floors.

---

## Section 2: Data Model

### New State Fields

```javascript
// state.cfdRoom — NEW fields
blankingPlateRefinementLevel: [1, 1],       // unified value for blanking plates
blankingPlatePerUnitLevel: { "1": [4,5] },  // per-U-height buckets

rackLeakageRefinementLevel: [1, 2],         // unified value for leakage zones
rackLeakagePerUnitLevel: { "2": [3,3] },    // per-U-height buckets
```

### cfd_export.json Output

```json
{
  "meshConfig": {
    "serverFaceRefinementLevel": [1, 1],
    "serverFacePerUnitLevel": { "1": [4,5], "2": [3,4] },
    "blankingPlateRefinementLevel": [1, 1],
    "blankingPlatePerUnitLevel": { "1": [4,5], "3": [2,3] },
    "rackLeakageRefinementLevel": [1, 2],
    "rackLeakagePerUnitLevel": { "2": [3,3], "4": [1,2] }
  }
}
```

### Backward Compatibility

- `blankingPlatePerUnitLevel` absent → wall patch blanking uses `rl_rack`, wall baffle blanking uses `rl_baffle` (current behavior)
- `rackLeakagePerUnitLevel` absent → leakage uses `rl_leakage` (current behavior)

---

## Section 3: Frontend Scanning Functions

Three scanning functions, all returning `{ "spanKey": { count, patchHeight } }`:

### `scanBlankingPlateUnitBuckets()`

Scan all rack models' `getRackMergedEmptySlotRanges()` for both sides (front/back). Count all ranges (both `isWallBaffle: true` and `isWallBaffle: false`). Group by span = `endUnit - startUnit + 1`.

### `scanLeakageUnitBuckets()`

Scan all rack models' `getRackLeakageGroups()`. Group by span = `endUnit - startUnit + 1`.

### Safety Formula (shared)

Same as server faces:
```
patchHeight = span * unitHeight
minMaxLevel = ceil(log2(baseSize / (patchHeight / 2)))
minMinLevel = max(minMaxLevel - 1, 0)
```

---

## Section 4: UI Layout

### Mesh Tab Changes

**Remove:**
- The "服务器面" row (lines 80-83) — moved into the folding panel
- The "漏风区域" row (lines 100-103) — merged into the folding panel

**Keep unchanged:**
- "房间墙壁" row
- "机架" row
- "ACU" row
- "白模" row
- "挡板/地板" row — controls standalone baffle models only (decoupled from rack blanking)

**Add:** A new "Rack 表面加密" collapsible section, placed where the "服务器面" row used to be:

```
Rack 表面加密                               ▼
```

Expanded:
```
Rack 表面加密                               ▲
  🖥 服务器面    [1] [1]
  ├ 1U (349台)   [4] [5]   ⚠
  ├ 2U (568台)   [3] [4]   ✓
  └ 5U (12台)    [1] [2]   ✓
  🧱 盲板        [1] [1]
  ├ 1U (20个)    [4] [5]   ⚠
  └ 3U (5个)     [2] [3]   ✓
  💨 漏风区域    [1] [2]
  ├ 2U (8个)     [3] [3]   ✓
  └ 4U (3个)     [1] [2]   ✓
  [🔍 自动检测]
```

### Interaction Rules

- Each category's unified row: modify → overwrites all that category's U-buckets
- Each U-bucket row: modify → only affects that bucket
- "🔍 自动检测": runs all three categories' auto-detect in one pass
- ⚠/✓ status icons: same formula as server faces

---

## Section 5: Backend Changes (stlgen_runner.py)

### Blanking Plate Span Mapping

Extract span from solid name using regex `_u(\d+)_u(\d+)` (two U markers) or `_u(\d+)(?!.*_u\d)` (single U marker → span=1):

```python
per_unit_blanking = mc.get("blankingPlatePerUnitLevel", {})
rl_blanking_unified = mc.get("blankingPlateRefinementLevel", rl_rack)

# Build solid → span mapping for rack_io_solids and rack_wall_baffle_solids
solid_to_blanking_span: Dict[str, int] = {}
_u_range_pat = re.compile(r'_u(\d+)_u(\d+)')
for solid_name in (rack_io_solids | rack_wall_baffle_solids):
    m = _u_range_pat.search(solid_name)
    if m:
        solid_to_blanking_span[solid_name] = int(m.group(2)) - int(m.group(1)) + 1
```

Then in refinementSurfaces generation, for both `rack_io_solids` and `rack_wall_baffle_solids`:
```python
_span = solid_to_blanking_span.get(sanitized)
_rl = per_unit_blanking.get(str(_span), rl_blanking_unified) if _span else fallback
```

Where `fallback` is `rl_rack` for rack_io_solids and `rl_baffle` for rack_wall_baffle_solids (preserving backward compat).

### Leakage Zone Span Mapping

```python
per_unit_leakage = mc.get("rackLeakagePerUnitLevel", {})
rl_leakage_unified = mc.get("rackLeakageRefinementLevel", rl_leakage)

solid_to_leakage_span: Dict[str, int] = {}
for rack in data.get("racks", {}).values():
    for grp in rack.get("leakage", {}).get("groups", []):
        san = _sanitize(grp["stlSolidName"])
        solid_to_leakage_span[san] = grp["endUnit"] - grp["startUnit"] + 1
```

Then in refinementSurfaces for leakage:
```python
_span = solid_to_leakage_span.get(sanitized)
_rl = per_unit_leakage.get(str(_span), rl_leakage_unified) if _span else rl_leakage
```

Note: leakage zones use `faceType internal; cellZone ...; cellZoneInside inside;` — the level still applies to the cellZone surface refinement.

---

## Section 6: Pre-Mesh Warning Dialog

Extends the existing dialog with three categories. Warning items are grouped by type with emoji prefix:

```
🖥 1U 服务器 (349台): 当前 [1,1], 建议至少 [4,5] — patch 高度 44.5mm, 当前最细 cell 200mm
🧱 1U 盲板 (20个): 当前 [1,1], 建议至少 [4,5] — patch 高度 44.5mm, 当前最细 cell 200mm
💨 2U 漏风区域 (8个): 当前 [1,1], 建议至少 [3,4] — patch 高度 89.0mm, 当前最细 cell 200mm
```

`collectServerRefinementWarnings()` → renamed to `collectRackSurfaceRefinementWarnings()`, returns warnings from all three categories with a `type` field ("server" / "blanking" / "leakage").

---

## Section 7: Internationalization Keys

New keys (add to both zh-CN and en-US):

```
label.rack_surface_refinement: "Rack 表面加密" / "Rack Surface Refinement"
label.blanking_plate: "🧱 盲板" / "🧱 Blanking Plate"
label.rack_leakage: "💨 漏风区域" / "💨 Leakage Zone"
label.server_face_section: "🖥 服务器面" / "🖥 Server Face"
label.blanking_per_unit: "{n}U ({count}个)" / "{n}U ({count} units)"
label.leakage_per_unit: "{n}U ({count}个)" / "{n}U ({count} units)"
dialog.rack_refinement_title: "Rack 表面网格精度预警" / "Rack Surface Mesh Accuracy Warning"
dialog.rack_refinement_desc: "以下 Rack 表面加密层级不足..." / "The following rack surface refinement levels are insufficient..."
```

Existing keys `dialog.server_refinement_*` replaced by `dialog.rack_refinement_*`.

---

## Section 8: Files to Modify

| File | Change |
|------|--------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Replace server-only collapsible panel with 3-section "Rack 表面加密" panel; remove "漏风区域" row; add scanning functions for blanking+leakage; update export; update auto-detect and pre-check |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | New/updated i18n keys |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | New/updated i18n keys |
| `dctwin/dctwin/runner/stlgen_runner.py` | Per-unit dispatch for blanking plate solids and leakage zone solids |

---

## Out of Scope

- Standalone baffle model refinement — unchanged, stays on `baffleRefinementLevel`
- Perforated floor refinement — unchanged, stays on `baffleRefinementLevel`
- Feature edge levels — rack surfaces share `rackFeatureLevel`, no per-unit feature levels
