# Rack Region Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-component rack surface refinement with a single `searchableBox` refinementRegion per rack, simplifying the mesh tab UI to one input and removing the bucket/auto-detect/warning system.

**Architecture:** Add `rackRegionLevel` (int, default 3) to MeshConfig. In `_generate_snappy_hex_mesh_dict()`, emit a `searchableBox` per rack in the `geometry` section and a corresponding `refinementRegions` entry. Hardcode all rack-internal `refinementSurfaces` entries to level (1,1) (keeping structural annotations like `type patch`, `faceType internal`, `faceZone` intact). Remove per-unit/per-bucket level lookups, through-zone refinementRegions, feature edges for rack internals, auto-detect, and warning dialog. Frontend simplifies to one `<input>` for `rackRegionLevel`.

**Tech Stack:** Python (Pydantic, stlgen_runner), HTML/JS (STL_generate.html), JSON (i18n)

---

### Task 1: Backend — Add `rackRegionLevel` to MeshConfig and remove old rack fields

**Files:**
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py:417-471`
- Test: `src/backend/tests/test_mesh_config_cell_limits.py` (add new test)

- [ ] **Step 1: Write test for new `rackRegionLevel` field**

Add to `src/backend/tests/test_mesh_config_cell_limits.py`:

```python
from dcdtuni.runner.cfdproj_schema import MeshConfig

def test_rack_region_level_default():
    mc = MeshConfig()
    assert mc.rackRegionLevel == 3

def test_rack_region_level_roundtrip():
    mc = MeshConfig(rackRegionLevel=5)
    d = mc.model_dump(mode="json")
    assert d["rackRegionLevel"] == 5
    mc2 = MeshConfig(**d)
    assert mc2.rackRegionLevel == 5

def test_old_rack_fields_ignored():
    """Old cfdproj files with removed rack fields should parse without error."""
    mc = MeshConfig(
        rackRefinementLevel=[2, 3],
        serverFaceRefinementLevel=[2, 3],
        baffleRefinementLevel=[1, 3],
        rackDoorRefinementLevel=[1, 1],
        serverFacePerUnitLevel={"2": [3, 4]},
    )
    # Old fields are preserved as extras (extra="allow")
    assert mc.rackRegionLevel == 3  # default applied
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_mesh_config_cell_limits.py -v -k "rack_region"` 
Expected: FAIL — `rackRegionLevel` not defined on MeshConfig

- [ ] **Step 3: Add `rackRegionLevel` field and remove old rack fields**

In `src/backend/aircooling/runner/cfdproj_schema.py`, in the `MeshConfig` class:

**Add** (after line 425, the `refinementLevel` field):
```python
    rackRegionLevel: int = 3
```

**Remove** these field declarations (lines 426-459):
```python
    rackRefinementLevel: List[int] = [2, 3]          # line 426
    serverFaceRefinementLevel: List[int] = [2, 3]    # line 427
    baffleRefinementLevel: List[int] = [1, 3]        # line 428
    rackDoorRefinementLevel: List[int] = [1, 1]      # line 435
    rackDoorFeatureLevel: Optional[int] = None       # line 437
    serverFaceFeatureLevel: int = 2                  # line 445
    serverFaceFeaturePerUnitLevel: Dict[str, int] = {}  # line 446
    blankingPlateFeatureLevel: int = 2               # line 447
    blankingPlateFeaturePerUnitLevel: Dict[str, int] = {}  # line 448
    rackPartitionRefinementLevel: List[int] = [1, 1]    # line 450
    rackPartitionPerUnitLevel: Dict[str, List[int]] = {}  # line 451
    rackPartitionFeatureLevel: int = 2               # line 452
    rackPartitionFeaturePerUnitLevel: Dict[str, int] = {}  # line 453
    rackThroughZoneFeatureLevel: int = 2             # line 458
    rackThroughZoneFeaturePerUnitLevel: Dict[str, int] = {}  # line 459
```

These will still be accepted in incoming JSON thanks to `extra="allow"` from `CFDBaseModel`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_mesh_config_cell_limits.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/cfdproj_schema.py src/backend/tests/test_mesh_config_cell_limits.py
git commit -m "feat(schema): add rackRegionLevel, remove old rack refinement fields"
```

---

### Task 2: Backend — Add `_rack_aabb()` helper with test

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` (near line 304, after `_quat_rotate`)
- Create: `src/backend/tests/test_rack_aabb.py`

- [ ] **Step 1: Write tests for `_rack_aabb()`**

Create `src/backend/tests/test_rack_aabb.py`:

```python
"""Tests for _rack_aabb: axis-aligned bounding box for rotated racks."""
import pytest
from dcdtuni.runner.stlgen_runner import _rack_aabb


class TestRackAABB:
    def test_no_rotation(self):
        """orientation=0: W along X, D along Z, H along Y."""
        anchor = {"x": 2.0, "y": 0.0, "z": 1.0}
        size = {"W": 0.8, "D": 1.2, "H": 2.2}
        mn, mx = _rack_aabb(anchor, size, orientation=0)
        assert mn == pytest.approx([2.0, 0.0, 1.0])
        assert mx == pytest.approx([2.8, 2.2, 2.2])

    def test_90_degrees(self):
        """orientation=90: W along -Z, D along X → box rotated 90° CW in XZ."""
        anchor = {"x": 3.0, "y": 0.0, "z": 5.0}
        size = {"W": 0.8, "D": 1.2, "H": 2.2}
        mn, mx = _rack_aabb(anchor, size, orientation=90)
        # At 90°: depth axis = X, width axis = Z
        # Box extends D along X, W along Z (swapped)
        assert mn == pytest.approx([3.0, 0.0, 5.0])
        assert mx == pytest.approx([4.2, 2.2, 5.8])

    def test_180_degrees(self):
        """orientation=180: same axis mapping as 0° (W along X, D along Z)."""
        anchor = {"x": 1.0, "y": 0.5, "z": 2.0}
        size = {"W": 0.8, "D": 1.2, "H": 2.2}
        mn, mx = _rack_aabb(anchor, size, orientation=180)
        assert mn == pytest.approx([1.0, 0.5, 2.0])
        assert mx == pytest.approx([1.8, 2.7, 3.2])

    def test_270_degrees(self):
        """orientation=270: same axis mapping as 90°."""
        anchor = {"x": 5.0, "y": 0.0, "z": 3.0}
        size = {"W": 0.8, "D": 1.2, "H": 2.2}
        mn, mx = _rack_aabb(anchor, size, orientation=270)
        assert mn == pytest.approx([5.0, 0.0, 3.0])
        assert mx == pytest.approx([6.2, 2.2, 3.8])

    def test_default_orientation(self):
        """Missing orientation defaults to 0."""
        anchor = {"x": 0, "y": 0, "z": 0}
        size = {"W": 1.0, "D": 2.0, "H": 3.0}
        mn, mx = _rack_aabb(anchor, size, orientation=0)
        assert mn == pytest.approx([0, 0, 0])
        assert mx == pytest.approx([1.0, 3.0, 2.0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_rack_aabb.py -v`
Expected: FAIL — `_rack_aabb` not importable

- [ ] **Step 3: Implement `_rack_aabb()`**

In `src/backend/aircooling/runner/stlgen_runner.py`, after `_quat_rotate` (around line 315), add:

```python
def _rack_aabb(anchor: Dict[str, float], size: Dict[str, float],
               orientation: int = 0) -> Tuple[List[float], List[float]]:
    """Compute axis-aligned bounding box for a rack at a given orientation.

    Racks are axis-aligned at 0°/90°/180°/270°. At 0°/180°, W maps to X
    and D maps to Z. At 90°/270°, W maps to Z and D maps to X.

    Returns (min_xyz, max_xyz) as 3-element lists.
    """
    W = size.get("W", 0)
    D = size.get("D", 0)
    H = size.get("H", 0)
    ax, ay, az = anchor.get("x", 0), anchor.get("y", 0), anchor.get("z", 0)

    ori = round(orientation) % 360
    if ori in (0, 180):
        # W along X, D along Z
        return [ax, ay, az], [ax + W, ay + H, az + D]
    else:
        # 90°/270°: W along Z, D along X (swapped)
        return [ax, ay, az], [ax + D, ay + H, az + W]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_rack_aabb.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py src/backend/tests/test_rack_aabb.py
git commit -m "feat(runner): add _rack_aabb helper for rack bounding box computation"
```

---

### Task 3: Backend — Refactor `_generate_snappy_hex_mesh_dict()` for rack region refinement

This is the core backend change. The function is large (~1000 lines starting at line 3988), so changes are organized by section.

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:3988-4963`

- [ ] **Step 1: Simplify meshConfig level reads (lines 4009-4028)**

Replace the rack-specific level reads with a single `rackRegionLevel` read. Keep non-rack levels unchanged.

**Remove** these lines (4010-4028):
```python
rl_rack = mc.get("rackRefinementLevel", [1, 1])
rl_server = mc.get("serverFaceRefinementLevel", [1, 1])
...
per_unit_levels = mc.get("serverFacePerUnitLevel", {})
per_unit_blanking = mc.get("blankingPlatePerUnitLevel", {})
rl_blanking_unified = mc.get("blankingPlateRefinementLevel", rl_rack)
per_unit_through_zone = mc.get("rackThroughZonePerUnitLevel", {})
rl_through_zone_unified = mc.get("rackThroughZoneRefinementLevel", rl_through_zone)
```

**Replace with:**
```python
rl_rack_internal = [1, 1]   # fixed baseline — actual refinement from refinementRegion
rack_region_level = mc.get("rackRegionLevel", 3)
```

Keep `rl_default`, `rl_acu`, `rl_box`, `rl_partition` (partition wall), `rl_baffle`, `rl_floor` unchanged.

- [ ] **Step 2: Simplify feature level reads (lines 4344-4350)**

**Remove** rack-internal feature level reads:
```python
feat_rack = _feat_int(mc.get("rackFeatureLevel"), 2)
feat_through_zone = _feat_int(mc.get("throughZoneFeatureLevel"), feat_rack)
```

**Replace with:**
```python
feat_rack = 1   # minimal — region refinement handles mesh density
```

Keep `feat_default`, `feat_acu`, `feat_box`, `feat_partition`, `feat_baffle` unchanged.

- [ ] **Step 3: Simplify feature edge extraction loop (lines 4352-4408)**

In the feature edge loop, skip all rack-internal STL files. Keep entries only for rack frame and non-rack files.

Replace the per-bucket feature lookup and rack-internal routing with a skip-set approach. Add this before the loop:

```python
# Rack internal STLs that should NOT get feature edge extraction
_RACK_INTERNAL_PREFIXES = (
    "merged_server_face_", "merged_blanking_", "merged_door_",
    "merged_partition_", "merged_through_zone_", "merged_leakage",
    "leakage_",
)
```

Then at the top of the feature loop (after `for filename, solids in sorted(self._stl_solids.items()):`), add:

```python
            stem = Path(filename).stem
            # Skip feature edges for rack internal STLs
            if any(filename.startswith(p) for p in _RACK_INTERNAL_PREFIXES):
                continue
```

Remove the `_bucket_feature_lookup` calls and all rack-internal routing in the loop (lines 4358-4401). Keep the non-rack routing (merged_acu, merged_box, merged_partition_wall, merged_baffle, merged_floor_ceiling, room_wall).

For `merged_rack_frame.stl` (line 4363), use `feat_rack` (now fixed at 1).
For `merged_rack_door.stl` (line 4372), skip (add to `_RACK_INTERNAL_PREFIXES` — already covered by prefix "merged_door_"... wait, `merged_rack_door.stl` doesn't start with `merged_door_`. Add `"merged_rack_door"` and `"merged_wall_baffle"` to the skip list):

Updated prefix list:
```python
_RACK_INTERNAL_PREFIXES = (
    "merged_server_face_", "merged_blanking_", "merged_door_",
    "merged_partition_", "merged_through_zone_", "merged_leakage",
    "leakage_", "merged_rack_door", "merged_wall_baffle",
)
```

Keep individual `baffle_*.stl` (non-rack baffles) and `perforated_baffle_*.stl`, `perforated_floor_*.stl` with their existing levels.

**Important:** `merged_rack_frame.stl` does NOT start with any of these prefixes, so it's correctly kept.

- [ ] **Step 4: Simplify refinementSurfaces loop (lines 4416-4797)**

The key change: replace all per-unit/per-bucket level lookups with the fixed `rl_rack_internal = [1, 1]`. Keep all structural annotations (`type patch`, `faceType internal`, `faceZone`) intact.

**4a.** Through-zone handling (lines 4429-4473): Remove the `through_zone_region_lines.append(...)` collection. Through-zones that were going to refinementRegions now simply get `continue` (skip entirely — the rack searchableBox covers them):

Replace lines 4429-4473 (the two through-zone blocks) — change from appending to `through_zone_region_lines` to just `continue`:

```python
            if (filename == "merged_leakage.stl"
                    or filename.startswith("leakage_")
                    or re.match(r"merged_leakage_\d+U\.stl$", filename)):
                continue  # covered by rack refinementRegion

            bucket = _parse_bucket_from_filename(filename)
            if bucket is not None and bucket[0] == "through_zone":
                continue  # covered by rack refinementRegion
```

**4b.** Per-solid classification (lines 4644-4781): Replace all per-unit level lookups with `rl_rack_internal`:

- `server_inlet_solids` / `server_outlet_solids` (line 4654): change `_unit_level(...)` to `rl_rack_internal`. Keep `type patch`.
- `nonfan_solids` (line 4665): change `_unit_level(...)` to `rl_rack_internal`. Keep `type wall`.
- `rack_io_solids` (line 4679): change `_unit_level(...)` to `rl_rack_internal`. Keep `type patch`.
- `rack_frame_solids` / `partition_solids` (line 4708): change `rl_rack` to `rl_rack_internal`. Keep `type wall`.
- `rack_door_solids` (line 4729): change `_door_level_for_rack(...)` to `rl_rack_internal`. Keep `faceType internal` + `faceZone`.
- Default case for `merged_rack.stl` / `merged_rack_frame.stl` (line 4769): change `rl_rack` to `rl_rack_internal`.

**4c.** Bucket-file-level routing (lines 4516-4545): For `wallBaffle` / `perforatedBlankingPanel` / `wallBlankingPanel` bucket entries, replace `_bucket_level_lookup(...)` with `rl_rack_internal`. Keep `faceType internal` + `faceZone` annotations.

- [ ] **Step 5: Add searchableBox geometry + refinementRegion entries for racks**

After the `rz_list` searchableBox emission (line 4816) and before `geom_str = ...` (line 4818), add rack region searchableBox geometry:

```python
        # ── Rack refinementRegion searchableBox entries ──
        rack_region_names = []
        data = self._data
        for rack_name, rack in (data.get("racks") or {}).items():
            geo = rack.get("geometry", {})
            anchor = geo.get("anchor", {})
            size = geo.get("size", {})
            ori = geo.get("orientation", 0)
            mn, mx = _rack_aabb(anchor, size, ori)
            rr_name = f"rackRegion_{_sanitize(rack_name)}"
            rack_region_names.append(rr_name)
            geom_lines.append(f"""    {rr_name}
    {{
        type searchableBox;
        min ({mn[0]:.6f} {mn[1]:.6f} {mn[2]:.6f});
        max ({mx[0]:.6f} {mx[1]:.6f} {mx[2]:.6f});
    }}""")
```

In the refinementRegions assembly (around line 4827), after the user `rz_list` loop, add rack regions:

```python
        # Add rack refinementRegion entries
        for rr_name in rack_region_names:
            rz_region_lines.append(f"""        {rr_name}
        {{
            mode inside;
            levels ((1e15 {rack_region_level}));
        }}""")
```

**Remove** the `through_zone_region_lines` extension (line 4837):
```python
        # REMOVE: rz_region_lines.extend(through_zone_region_lines)
```

Also remove the `through_zone_region_lines = []` initialization (line 4414) and the variable `through_zone_region_lines` entirely.

- [ ] **Step 6: Run existing tests to check for breakage**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_refinement_levels_propagation.py src/backend/tests/test_refinement_redesign.py src/backend/tests/test_rack_door_refinement.py -v --tb=short 2>&1 | tail -40`

Expected: Some tests will FAIL because they expect old behavior (per-unit levels, through-zone refinementRegions, etc.). Note which tests fail — they'll be fixed in Task 4.

- [ ] **Step 7: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(runner): rack region refinement — searchableBox + simplified surfaces"
```

---

### Task 4: Backend — Update existing tests for rack region refinement

**Files:**
- Modify: `src/backend/tests/test_refinement_levels_propagation.py`
- Modify: `src/backend/tests/test_refinement_redesign.py`
- Modify: `src/backend/tests/test_rack_door_refinement.py`

- [ ] **Step 1: Update `test_refinement_levels_propagation.py`**

**`TestRackFrameLevel` (line 136):** Update expected levels from `(2, 3)` to `(1, 1)` (new fixed baseline). Keep the test structure but update assertions.

**`TestThroughZoneLevel` (line 248):** This test checks through-zone refinementRegions entries exist. Since through-zones no longer emit refinementRegion entries, either:
- Remove the test entirely, or
- Change it to verify through-zone is NOT in refinementRegions

Replace with a test verifying the rack region exists instead:

```python
class TestRackRegionLevel:
    def test_rack_region_in_refinement_regions(self, snappy_text):
        """Each rack should have a rackRegion_* entry in refinementRegions."""
        assert "rackRegion_" in snappy_text
        assert "mode inside" in snappy_text
```

**`TestServerFaceLevel` (line 255):** Update expected per-unit level from the specific per-unit value to `(1 1)` (fixed baseline).

**`TestBlankingPlateLevel` (line 268):** Same — update expected level to `(1 1)`.

**`TestRackDoorLevel` (line 192):** Update expected level to `(1 1)`.

- [ ] **Step 2: Update `test_refinement_redesign.py`**

**`test_parse_bucket_from_filename_*` tests (lines 94-135):** These test the bucket parser which still exists — KEEP UNCHANGED (the parser is still used for routing in the refinementSurfaces loop, even though levels are now fixed).

**`test_bucket_level_lookup_*` tests (lines 137-178):** These test `_bucket_level_lookup` which is no longer called. Either:
- Remove these tests, or
- Keep them as unit tests for the function (even if unused in production)

Recommendation: Remove — dead code tests just add maintenance burden.

**`test_snappy_dict_refinement_surfaces_per_bucket` (line 268):** This test verifies per-bucket surface levels and through-zone refinementRegions. Update to verify:
- Rack internal surfaces use level (1 1)
- Through-zone is NOT in refinementRegions
- Rack regions ARE in refinementRegions

**`test_snappy_dict_features_block_reads_per_bucket_levels` (line 225):** Update — rack internal features should no longer appear. Verify only rack_frame feature edge remains.

- [ ] **Step 3: Update `test_rack_door_refinement.py`**

**`test_meshconfig_default_rack_door_fields` (line 13):** Remove — fields no longer exist on MeshConfig.

**`test_cfdexport_round_trip_door_fields` (line 20):** Remove — fields no longer exist.

**`test_compute_min_u_span_*` (lines 32-53):** Keep — `_compute_rack_min_u_span` may still exist (check if blockMesh alignment uses it). If not used, remove.

**`test_door_level_*` and `test_door_feature_level_*` (lines 56-108):** Remove — door level functions are no longer used.

**snappyHexMeshDict integration tests (lines 185+):** Update to check door surfaces use fixed (1 1) level instead of per-rack overrides.

- [ ] **Step 4: Run all tests**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/ -v --tb=short 2>&1 | tail -60`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/tests/
git commit -m "test: update refinement tests for rack region simplification"
```

---

### Task 5: Frontend — UI, state, sync, export, dead code removal, and i18n

This task modifies `STLGeneratorForOpenFoam/web/STL_generate.html` and i18n JSON files.

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Replace mesh tab rack HTML (lines 154-164)**

Replace the rack row + rack refine panel:

```html
<!-- OLD (lines 154-164): rack surface [min,max,feat] + collapsible panel -->
<label title="机架外壳及内部构件 (merged_rack.stl)" data-i18n="label.rack" data-i18n-title="tooltip.refine_rack">机架</label>
<input type="number" id="ms-rack-0" ...>
<input type="number" id="ms-rack-1" ...>
<input type="number" id="ms-feat-rack" ...>
<div style="grid-column:1/-1;...">
    <span ... data-i18n="label.rack_surface_refinement">机架内设备加密</span>
    <button id="rack-refine-toggle" ...>▼</button>
</div>
<div id="rack-refine-panel" ...>
    <div id="rack-refine-content" ...></div>
</div>
```

With:

```html
<!-- NEW: single input for rack region refinement -->
<label data-i18n="label.rack_region" title="每个机架生成一个区域加密体 (searchableBox refinementRegion)">机架区域加密</label>
<input type="number" id="ms-rack-region" min="0" max="6" value="3" class="mesh-lvl-input" style="grid-column: span 3; max-width: 60px;" oninput="window.setMeshRefineLevel('rackRegionLevel',0,+this.value)">
```

- [ ] **Step 2: Update `state.cfdRoom` defaults (lines 3257-3284)**

Remove all rack-internal refinement defaults and add `rackRegionLevel`:

**Remove** (lines 3257-3284):
```javascript
rackRefinementLevel: [1, 1],
serverFaceRefinementLevel: [1, 1],
rackDoorRefinementLevel: [1, 1],
rackDoorFeatureLevel: null,
serverFacePerUnitLevel: {},
serverFaceFeaturePerUnitLevel: {},
blankingPlateRefinementLevel: [1, 1],
blankingPlatePerUnitLevel: {},
blankingPlateFeatureLevel: 2,
blankingPlateFeaturePerUnitLevel: {},
rackPartitionRefinementLevel: [1, 1],
rackPartitionPerUnitLevel: {},
rackPartitionFeatureLevel: 2,
rackPartitionFeaturePerUnitLevel: {},
rackThroughZoneFeatureLevel: 2,
rackThroughZoneFeaturePerUnitLevel: {},
rackFeatureLevel: 2,
serverFaceFeatureLevel: 2,
```

**Add** (in place of the above):
```javascript
rackRegionLevel: 3,
```

- [ ] **Step 3: Update `_syncMeshPanelInputs()` (lines 27323-27335)**

Replace rack field sync with single field:

**Remove:**
```javascript
set('ms-rack-0', (cr.rackRefinementLevel || [1,1])[0]);
set('ms-rack-1', (cr.rackRefinementLevel || [1,1])[1]);
refreshRackRefinePanelUI();
...
set('ms-feat-rack', cr.rackFeatureLevel || 2);
```

**Replace with:**
```javascript
set('ms-rack-region', cr.rackRegionLevel || 3);
```

- [ ] **Step 4: Update `setMeshRefineLevel()` (line 28160)**

The existing generic setter handles both array and scalar keys. `rackRegionLevel` is a scalar (not an array), so the existing code path `state.cdfRoom[key] = value` would work — but only if `key.endsWith('FeatureLevel')`. Since `rackRegionLevel` doesn't end with `FeatureLevel`, the code would try to set it as an array.

Fix: Update the condition to also handle `rackRegionLevel` as a scalar:

```javascript
window.setMeshRefineLevel = function(key, idx, value) {
    pushUndoCheckpoint('setMeshRefineLevel');
    if (key.endsWith('FeatureLevel') || key === 'rackRegionLevel') {
        state.cfdRoom[key] = value;          // scalar
    } else {
        if (!Array.isArray(state.cfdRoom[key])) state.cdfRoom[key] = [1, 1];
        state.cfdRoom[key][idx] = value;     // array[idx]
    }
    logOperation('setMeshRefineLevel', t('log.refinement_strategy', { key, value: JSON.stringify(state.cfdRoom[key]) }), { key, idx, value }, 'modify');
};
```

- [ ] **Step 5: Update export code (lines 31625-31652)**

In `buildCfdrunJson()`, replace the rack refinement fields in the `meshConfig` object:

**Remove** (lines 31625-31652):
```javascript
rackRefinementLevel: cr.rackRefinementLevel || [1,1],
serverFaceRefinementLevel: cr.serverFaceRefinementLevel || [1,1],
serverFacePerUnitLevel: cr.serverFacePerUnitLevel || {},
blankingPlateRefinementLevel: cr.blankingPlateRefinementLevel || [1,1],
blankingPlatePerUnitLevel: cr.blankingPlatePerUnitLevel || {},
rackThroughZoneRefinementLevel: cr.rackThroughZoneRefinementLevel || [1,2],
rackThroughZonePerUnitLevel: cr.rackThroughZonePerUnitLevel || {},
rackPartitionRefinementLevel: cr.rackPartitionRefinementLevel || [1,1],
rackPartitionPerUnitLevel: cr.rackPartitionPerUnitLevel || {},
rackDoorRefinementLevel: cr.rackDoorRefinementLevel || [1,1],
rackDoorFeatureLevel: ...,
rackFeatureLevel: cr.rackFeatureLevel || 2,
serverFaceFeatureLevel: cr.serverFaceFeatureLevel || 2,
serverFaceFeaturePerUnitLevel: cr.serverFaceFeaturePerUnitLevel || {},
blankingPlateFeatureLevel: cr.blankingPlateFeatureLevel || 2,
blankingPlateFeaturePerUnitLevel: cr.blankingPlateFeaturePerUnitLevel || {},
rackPartitionFeatureLevel: cr.rackPartitionFeatureLevel || 2,
rackPartitionFeaturePerUnitLevel: cr.rackPartitionFeaturePerUnitLevel || {},
rackThroughZoneFeatureLevel: cr.rackThroughZoneFeatureLevel || 2,
rackThroughZoneFeaturePerUnitLevel: cr.rackThroughZoneFeaturePerUnitLevel || {},
```

**Replace with:**
```javascript
rackRegionLevel: cr.rackRegionLevel || 3,
```

Also **remove** the `minUSpanForDoorBucket` from rack export (line 31891):
```javascript
// REMOVE: minUSpanForDoorBucket: _computeRackMinUSpan(model)
```

- [ ] **Step 6: Remove dead functions**

Remove the following functions entirely:

| Function | Lines (approx) |
|----------|----------------|
| `toggleRackRefinePanel()` | 27850-27858 |
| `refreshRackRefinePanelUI()` | 28091-28158 |
| `_renderCategorySection()` | 28003-28089 |
| `scanRackDoorMinUnitBuckets()` | 27535-27548 |
| `scanThroughZoneUnitBuckets()` | 27510-27526 |
| `scanRackPartitionBuckets()` | 27555-27571 |
| `scanServerUnitBuckets()` | 27430-27458 |
| `scanBlankingPlateUnitBuckets()` | 27460-27476 |
| `_computeServerBucketKeyForSolid()` | 27578-27595 |
| `scanRackFrameBuckets()` | 27604-27620 |
| `showRackRefinementWarningDialog()` | 30457-30493 |
| `collectRackSurfaceRefinementWarnings()` | 27842-27848 |
| `autoDetectRackRefinement()` | 27793-27811 |
| `_autoDetectRackFrame()` | 27727-27761 |
| `_autoDetectRackDoor()` | 27766-27791 |
| `_computeFrameMinSafeLevel()` | 27706-27712 |
| `_autoDetectCategory()` helper | (find near 27713-27726) |

Also in `runAutoDetectRefinement()` (~line 27970): remove the rack auto-detect calls (only keep partition wall auto-detect if present).

In `runMeshGeneration()` (~line 30594): remove the `collectRackSurfaceRefinementWarnings()` call and the conditional `showRackRefinementWarningDialog()` call.

Remove `_computeRackMinUSpan()` (~line 27490) ONLY IF blockMesh alignment does NOT depend on it. Check: search for `_computeRackMinUSpan` references in the file. If only called from `scanRackDoorMinUnitBuckets` and `buildCfdrunJson` (both being removed), then safe to delete. If blockMesh alignment calls it, keep it.

- [ ] **Step 7: Update i18n files**

**`STLGeneratorForOpenFoam/web/i18n/zh-CN.json`:**

Add:
```json
"label.rack_region": "机架区域加密",
```

Remove (if no longer referenced):
```json
"label.rack_surface_refinement": ...,
"label.server_face": ...,
"label.server_face_section": ...,
"label.blanking_plate": ...,
"label.rack_through_zone": ...,
"label.rack_door_section": ...,
"label.rack_door_per_min_unit": ...,
"label.blanking_per_unit": ...,
"label.through_zone_per_unit": ...,
"label.rack_partition_section": ...,
"label.rack_partition_per_unit": ...,
"btn.server_refinement_back": ...,
"btn.server_refinement_ignore": ...,
"dialog.rack_refinement_title": ...,
"dialog.rack_refinement_desc": ...,
"dialog.server_refinement_item": ...,
```

**`STLGeneratorForOpenFoam/web/i18n/en-US.json`:**

Add:
```json
"label.rack_region": "Rack Region Refinement",
```

Remove the same keys as zh-CN (check that each key is not referenced elsewhere before deleting).

- [ ] **Step 8: Verify in browser**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "window.switchLeftTab && window.switchLeftTab('mesh')"
browser-use screenshot /tmp/mesh_tab_after.png
```

Verify: The mesh tab shows "机架区域加密: [3]" (single input) with no expandable sub-panel.

- [ ] **Step 9: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat(ui): simplify rack mesh settings to single region level input

Remove per-component rack refinement UI (server/blanking/door/partition/
through-zone), auto-detect, warning dialog. Replace with single
rackRegionLevel input (default 3)."
```

---

### Task 6: End-to-end verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/ -v --tb=short 2>&1 | tail -80
```

Expected: ALL PASS

- [ ] **Step 2: Generate mesh with a test case**

Load a project with racks (e.g., `paintToDC1.cfdproj` or `V5_CFD_Station_1773892919284.cfdproj`) via the browser, export STL+cfdrun, and verify:

1. The snappyHexMeshDict contains `rackRegion_*` entries in both `geometry` and `refinementRegions`
2. Rack internal surfaces use level (1 1) in refinementSurfaces
3. No through-zone entries in refinementRegions
4. Mesh generates successfully

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
# Load project, export, check generated snappyHexMeshDict
```

- [ ] **Step 3: Verify backward compatibility**

Load an old cfdproj that has `rackRefinementLevel: [2, 3]` and other old fields. Verify:
1. Project loads without errors
2. Old fields are ignored
3. `rackRegionLevel` defaults to 3
4. Mesh tab shows single input with value 3
