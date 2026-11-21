# Rack Frame Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the rack shell into a watertight "picture frame" solid (`{rackName}_frame`), change safety margins from integer U-count to continuous meter values (additive, not occupying U-slots), and make partitions follow U-slot depth.

**Architecture:** Merge all rack shell faces into a single solid. Change `safeMarginTopUnits`/`safeMarginBottomUnits` (int) to `safeMarginTop`/`safeMarginBottom` (float, meters). Rack H = slotAreaHeight + margins. Partitions compute depth from intersection of adjacent slot depth ranges.

**Tech Stack:** Three.js r128, vanilla JS (STL_generate.html ~26600 lines), Python (stlgen_runner.py ~3300 lines).

**Spec:** `docs/superpowers/specs/2026-04-14-rack-frame-redesign-design.md`

---

## File Map

| File | Responsibility | Tasks |
|------|---------------|-------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | State, dimensions, geometry, inspector, export | 1–6 |
| `src/backend/aircooling/runner/stlgen_runner.py` | Backend BC registration, snappyHexMesh region | 7 |

---

### Task 1: State Migration — safeMargin from int to float

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `getDefaultRackState()` (~line 1952), `cloneRackState()` (~line 2021), `syncRackModelState()` (~line 2097)

**Context:** Currently `safeMarginTopUnits` and `safeMarginBottomUnits` are integer U-counts (default 1). We change them to `safeMarginTop` and `safeMarginBottom` as float meter values (default = `RACK_DEFAULT_UNIT_HEIGHT` = 0.04445m). Old projects with the integer fields are auto-migrated.

- [ ] **Step 1: Update `getDefaultRackState()`**

In `getDefaultRackState()` (line ~1952), replace:
```javascript
safeMarginTopUnits: 1,
safeMarginBottomUnits: 1,
```
with:
```javascript
safeMarginTop: RACK_DEFAULT_UNIT_HEIGHT,      // meters, default = 1U height
safeMarginBottom: RACK_DEFAULT_UNIT_HEIGHT,    // meters, default = 1U height
```

- [ ] **Step 2: Update `cloneRackState()` with migration**

In `cloneRackState()` (~line 2021), replace the safeMargin cloning lines (lines ~2033-2034):
```javascript
safeMarginTopUnits: Number.isFinite(rawRack.safeMarginTopUnits) ? rawRack.safeMarginTopUnits : fallback.safeMarginTopUnits,
safeMarginBottomUnits: Number.isFinite(rawRack.safeMarginBottomUnits) ? rawRack.safeMarginBottomUnits : fallback.safeMarginBottomUnits,
```
with migration logic:
```javascript
safeMarginTop: (function() {
    if (Number.isFinite(rawRack.safeMarginTop)) return rawRack.safeMarginTop;
    if (Number.isFinite(rawRack.safeMarginTopUnits)) {
        var uH = Number.isFinite(rawRack.unitHeight) ? rawRack.unitHeight : RACK_DEFAULT_UNIT_HEIGHT;
        return rawRack.safeMarginTopUnits * uH;
    }
    return fallback.safeMarginTop;
})(),
safeMarginBottom: (function() {
    if (Number.isFinite(rawRack.safeMarginBottom)) return rawRack.safeMarginBottom;
    if (Number.isFinite(rawRack.safeMarginBottomUnits)) {
        var uH = Number.isFinite(rawRack.unitHeight) ? rawRack.unitHeight : RACK_DEFAULT_UNIT_HEIGHT;
        return rawRack.safeMarginBottomUnits * uH;
    }
    return fallback.safeMarginBottom;
})(),
```

- [ ] **Step 3: Update `syncRackModelState()` — validation + H computation**

In `syncRackModelState()` (~line 2097):

Replace margin validation (lines ~2121-2122):
```javascript
rack.safeMarginTopUnits = Math.max(0, Math.min(rack.unitCount - 1, Math.round(rack.safeMarginTopUnits || 0)));
rack.safeMarginBottomUnits = Math.max(0, Math.min(rack.unitCount - 1, Math.round(rack.safeMarginBottomUnits || 0)));
```
with:
```javascript
rack.safeMarginTop = Math.max(0, Number(rack.safeMarginTop) || 0);
rack.safeMarginBottom = Math.max(0, Number(rack.safeMarginBottom) || 0);
var slotAreaHeight = rack.unitCount * rack.unitHeight;
var maxMarginSum = slotAreaHeight * 0.5;  // margins can't exceed half of slot area
if (rack.safeMarginTop + rack.safeMarginBottom > maxMarginSum) {
    var mScale = maxMarginSum / (rack.safeMarginTop + rack.safeMarginBottom);
    rack.safeMarginTop *= mScale;
    rack.safeMarginBottom *= mScale;
}
```

Replace H computation (lines ~2125-2130):
```javascript
if (rack.heightMode === 'unitHeight') {
    model.base.H = rack.unitCount * rack.unitHeight;
} else {
    model.base.H = Math.max(model.base.H || rack.unitCount * rack.unitHeight, rack.unitCount * 0.01);
    rack.unitHeight = model.base.H / rack.unitCount;
}
```
with:
```javascript
var slotAreaH = rack.unitCount * rack.unitHeight;
model.base.H = slotAreaH + rack.safeMarginTop + rack.safeMarginBottom;
```

Note: The `heightMode` distinction is removed — H is always computed from `unitCount × unitHeight + margins`. The user controls `unitHeight` and `unitCount` directly.

Replace the door disable check (lines ~2132-2138) that uses old margin fields:
```javascript
var _syncSafeBot = rack.safeMarginBottomUnits * rack.unitHeight;
var _syncSafeTop = rack.safeMarginTopUnits * rack.unitHeight;
if (model.base.H - _syncSafeBot - _syncSafeTop <= 1e-6) {
```
with:
```javascript
if (slotAreaH <= 1e-6) {
```

- [ ] **Step 4: Update all references to old field names**

Search for ALL occurrences of `safeMarginTopUnits` and `safeMarginBottomUnits` across the entire file. Each must be replaced:

- Reads computing heights: `rack.safeMarginTopUnits * rack.unitHeight` → `rack.safeMarginTop`
- Reads computing heights: `rack.safeMarginBottomUnits * rack.unitHeight` → `rack.safeMarginBottom`
- The computed variables `safeBottomHeight`, `safeTopHeight`, `centralFrameHeight` in `renderRackModel()` need updating:
  ```javascript
  // Old:
  var safeBottomHeight = rack.safeMarginBottomUnits * rack.unitHeight;
  var safeTopHeight = rack.safeMarginTopUnits * rack.unitHeight;
  var centralFrameHeight = Math.max(0, model.base.H - safeBottomHeight - safeTopHeight);
  
  // New:
  var safeBottomHeight = rack.safeMarginBottom;
  var safeTopHeight = rack.safeMarginTop;
  var slotAreaHeight = rack.unitCount * rack.unitHeight;
  // centralFrameHeight is now slotAreaHeight (all slots are usable)
  var centralFrameHeight = slotAreaHeight;
  ```
- Also in `buildCfdExportJson()` door IIFEs that reference `_rack.safeMarginTopUnits` / `_rack.safeMarginBottomUnits`.

- [ ] **Step 5: Remove `getRackSelectableSlotRange()` and `isRackSlotSelectable()`**

Delete `getRackSelectableSlotRange()` (~line 2091) and `isRackSlotSelectable()` (~line 5067). Search for ALL call sites and remove or replace:
- Calls to `isRackSlotSelectable()` → always return `true` (all slots selectable)
- Calls to `getRackSelectableSlotRange()` → return `{ start: 0, end: rack.unitCount }`

Or simply inline these: remove the functions and fix their callers.

- [ ] **Step 6: Update `getRackSlotBounds()`**

In `getRackSlotBounds()` (~line 5056), the `y` position must account for the bottom margin offset:
```javascript
function getRackSlotBounds(model, slotIndex) {
    var rack = model.rack;
    var slotWidth = model.base.W * rack.slotWidthRatio;
    return {
        x: (model.base.W - slotWidth) / 2,
        y: rack.safeMarginBottom + slotIndex * rack.unitHeight,  // was: slotIndex * rack.unitHeight
        w: slotWidth,
        h: rack.unitHeight
    };
}
```

- [ ] **Step 7: Verify backward compat — load old project**

Load an existing project. Verify:
- Old `safeMarginTopUnits=1` → migrated to `safeMarginTop=0.04445`
- Rack renders at correct height (now taller by 2 × margin)
- All 42 slots are accessible

- [ ] **Step 8: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(rack): migrate safeMargin from int U-count to continuous meters + H = slotArea + margins"
```

---

### Task 2: Frame Geometry Builder

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `renderRackModel()` (~lines 6721–6931)

**Context:** Replace all inline rack shell rendering code (outer faces, safe margin walls, frame strips, inner cavity walls) with a single `buildRackFrameGeometry()` function. All frame meshes share `exportName = '{rackName}_frame'`.

- [ ] **Step 1: Create `buildRackFrameGeometry()` function**

Insert near `renderRackModel()`. This function generates all frame faces as separate meshes but with the same `exportName`:

```javascript
/**
 * Build all faces of the rack "picture frame" — outer shell + inner cavity.
 * All meshes get exportName = '{rackName}_frame' → merge into 1 STL solid.
 *
 * @param {THREE.Group} modelGroup
 * @param {Object} model
 * @param {Object} frameParams - { rName, W, D, H, slotX, slotWidth, slotAreaHeight,
 *                                  marginBottom, marginTop, slotDepthStart, slotDepthEnd,
 *                                  inletOff, outletOff, opacity }
 */
function buildRackFrameGeometry(modelGroup, model, frameParams) {
    var fp = frameParams;
    var exportName = fp.rName + '_frame';
    var frameColor = '#5c6370';
    var sideColor = '#7f848e';
    var topColor = '#9ba0a9';
    var bottomColor = '#4b5263';
    var innerColor = '#707783';

    var frameUserData = { modelId: model.id, isBaseWall: true, isRackFrame: true, exportName: sanitizeName(exportName) };

    // --- Outer shell: 4 side faces ---
    // Left wall (full height)
    spawnFaceRect('left', 0, 0, fp.D, fp.H, sideColor, Object.assign({}, frameUserData));
    // Right wall
    spawnFaceRect('right', 0, 0, fp.D, fp.H, sideColor, Object.assign({}, frameUserData));
    // Top face
    spawnFaceRect('top', 0, 0, fp.W, fp.D, topColor, Object.assign({}, frameUserData));
    // Bottom face
    spawnFaceRect('bottom', 0, 0, fp.W, fp.D, bottomColor, Object.assign({}, frameUserData));

    // --- Front/Back: margin strips + frame strips around slot opening ---
    ['front', 'back'].forEach(function(side) {
        var color = side === 'front' ? frameColor : innerColor;
        // Bottom margin strip
        if (fp.marginBottom > 1e-6) {
            spawnFaceRect(side, 0, 0, fp.W, fp.marginBottom, color, Object.assign({}, frameUserData, { side: side }));
        }
        // Top margin strip
        if (fp.marginTop > 1e-6) {
            spawnFaceRect(side, 0, fp.H - fp.marginTop, fp.W, fp.marginTop, color, Object.assign({}, frameUserData, { side: side }));
        }
        // Left frame strip (beside slot opening)
        var leftFrameW = fp.slotX;
        if (leftFrameW > 1e-6 && fp.slotAreaHeight > 1e-6) {
            spawnFaceRect(side, 0, fp.marginBottom, leftFrameW, fp.slotAreaHeight, color,
                Object.assign({}, frameUserData, { side: side }));
        }
        // Right frame strip
        var rightFrameW = fp.W - fp.slotX - fp.slotWidth;
        if (rightFrameW > 1e-6 && fp.slotAreaHeight > 1e-6) {
            spawnFaceRect(side, fp.slotX + fp.slotWidth, fp.marginBottom, rightFrameW, fp.slotAreaHeight, color,
                Object.assign({}, frameUserData, { side: side }));
        }
    });

    // --- Inner cavity walls (slot channel sides) ---
    var effectiveSlotDepth = fp.slotDepthEnd - fp.slotDepthStart;
    if (fp.slotAreaHeight > 1e-6 && effectiveSlotDepth > 1e-6) {
        // Inner left wall
        spawnPlanarRect(modelGroup,
            new THREE.Vector3(fp.slotX, fp.slotDepthStart, fp.marginBottom),
            new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1),
            effectiveSlotDepth, fp.slotAreaHeight,
            { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
              userData: Object.assign({}, frameUserData) });
        // Inner right wall
        spawnPlanarRect(modelGroup,
            new THREE.Vector3(fp.slotX + fp.slotWidth, fp.slotDepthStart, fp.marginBottom),
            new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0),
            fp.slotAreaHeight, effectiveSlotDepth,
            { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
              userData: Object.assign({}, frameUserData) });
    }

    // --- Inner ceiling + floor (top/bottom of slot cavity) ---
    if (fp.slotWidth > 1e-6 && effectiveSlotDepth > 1e-6) {
        // Inner floor (between bottom margin and slot area bottom)
        spawnPlanarRect(modelGroup,
            new THREE.Vector3(fp.slotX, fp.slotDepthStart, fp.marginBottom),
            new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0),
            fp.slotWidth, effectiveSlotDepth,
            { color: bottomColor, opacity: fp.opacity, doubleSide: true,
              userData: Object.assign({}, frameUserData) });
        // Inner ceiling (between slot area top and top margin)
        spawnPlanarRect(modelGroup,
            new THREE.Vector3(fp.slotX, fp.slotDepthEnd, fp.marginBottom + fp.slotAreaHeight),
            new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, -1, 0),
            fp.slotWidth, effectiveSlotDepth,
            { color: topColor, opacity: fp.opacity, doubleSide: true,
              userData: Object.assign({}, frameUserData) });
    }

    // --- Depth offset cavity walls (if inletOffset or outletOffset > 0) ---
    if (fp.inletOff > 1e-6 && fp.slotAreaHeight > 1e-6) {
        // Inlet cavity left
        spawnPlanarRect(modelGroup,
            new THREE.Vector3(fp.slotX, 0, fp.marginBottom),
            new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1),
            fp.inletOff, fp.slotAreaHeight,
            { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
              userData: Object.assign({}, frameUserData) });
        // Inlet cavity right
        spawnPlanarRect(modelGroup,
            new THREE.Vector3(fp.slotX + fp.slotWidth, 0, fp.marginBottom),
            new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0),
            fp.slotAreaHeight, fp.inletOff,
            { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
              userData: Object.assign({}, frameUserData) });
    }
    if (fp.outletOff > 1e-6 && fp.slotAreaHeight > 1e-6) {
        // Outlet cavity left
        spawnPlanarRect(modelGroup,
            new THREE.Vector3(fp.slotX, fp.slotDepthEnd, fp.marginBottom),
            new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1),
            fp.outletOff, fp.slotAreaHeight,
            { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
              userData: Object.assign({}, frameUserData) });
        // Outlet cavity right
        spawnPlanarRect(modelGroup,
            new THREE.Vector3(fp.slotX + fp.slotWidth, fp.slotDepthEnd, fp.marginBottom),
            new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0),
            fp.slotAreaHeight, fp.outletOff,
            { color: innerColor, opacity: fp.opacity, objectKind: 'rack-side',
              userData: Object.assign({}, frameUserData) });
    }
}
```

- [ ] **Step 2: Replace inline shell rendering in `renderRackModel()`**

Delete the following blocks in `renderRackModel()`:
1. Outer shell faces (lines ~6721–6736): `_Left`, `_Right`, `_Top`, `_Bottom`
2. Safe margin walls + frame strips (lines ~6738–6770): `_safe_bottom`, `_safe_top`, `_frame_left`, `_frame_right`
3. Inner cavity walls (lines ~6860–6931): `_inner_left`, `_inner_right`, `_inlet_cavity_*`, `_outlet_cavity_*`

Replace with a single call:
```javascript
var rName = sanitizeName(model.name);
var slotWidth = model.base.W * rack.slotWidthRatio;
var slotX = (model.base.W - slotWidth) / 2;
var inletOff = rack.inletOffset || 0;
var outletOff = rack.outletOffset || 0;
var slotDepthStart = inletOff;
var slotDepthEnd = model.base.D - outletOff;
var slotAreaHeight = rack.unitCount * rack.unitHeight;

buildRackFrameGeometry(modelGroup, model, {
    rName: rName,
    W: model.base.W, D: model.base.D, H: model.base.H,
    slotX: slotX, slotWidth: slotWidth,
    slotAreaHeight: slotAreaHeight,
    marginBottom: rack.safeMarginBottom,
    marginTop: rack.safeMarginTop,
    slotDepthStart: slotDepthStart, slotDepthEnd: slotDepthEnd,
    inletOff: inletOff, outletOff: outletOff,
    opacity: opacity
});
```

Keep the blind plate rendering, equipment rendering, partition rendering, and door rendering — only the shell faces move to the new function.

- [ ] **Step 3: Update blind plate Z positions**

The merged empty slot rendering (lines ~6774–6827) uses `getRackSlotBounds()` which now returns Y values offset by `marginBottom`. Verify that `mergedY` is already computed from `startBounds.y` (which includes the margin offset from Task 1 Step 6). If so, no change needed. If the code adds `safeBottomHeight` manually, remove that addition.

- [ ] **Step 4: Verify rendering**

Load a project. Verify:
- Rack frame renders with correct colors
- Inner cavity walls visible
- Blind plates at correct positions
- No gaps between frame and slot contents

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "refactor(rack): extract buildRackFrameGeometry() — all shell faces merged as {rackName}_frame"
```

---

### Task 3: Partition Depth

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — partition rendering (~lines 6966–6991)

**Context:** Partitions currently span full rack depth (`model.base.D`). They should now span only the slot cavity depth, using the intersection (shallower) of the two adjacent slots' depth ranges.

- [ ] **Step 1: Create `getSlotDepthRange()` helper**

```javascript
/**
 * Get the depth range (Y-axis) for a specific U-slot, accounting for
 * rack-level and per-equipment depth offsets.
 *
 * @param {Object} model - rack model
 * @param {number} slotIndex - 0-based slot index
 * @returns {{ y0: number, y1: number }} depth range (inlet side to outlet side)
 */
function getSlotDepthRange(model, slotIndex) {
    var rack = model.rack;
    var baseStart = rack.inletOffset || 0;
    var baseEnd = model.base.D - (rack.outletOffset || 0);

    // Check if this slot is occupied by equipment with extra offset
    var eq = getRackEquipmentForSlot(model, slotIndex);
    if (eq) {
        baseStart += (eq.extraInletOffset || 0);
        baseEnd -= (eq.extraOutletOffset || 0);
    }

    return { y0: baseStart, y1: Math.max(baseStart, baseEnd) };
}
```

Note: `getRackEquipmentForSlot(model, slotIndex)` should already exist — search for it. If not, it returns the equipment whose `[startUnit, startUnit+spanUnits)` range contains `slotIndex`.

- [ ] **Step 2: Create `getPartitionDepthRange()` helper**

```javascript
/**
 * Get the depth range for a partition between slot[index] and slot[index+1].
 * Uses the intersection (shallower) of the two adjacent slots.
 *
 * @param {Object} model - rack model
 * @param {number} partitionIndex - partition sits between slot[index] and slot[index+1]
 * @returns {{ y0: number, y1: number, depth: number }}
 */
function getPartitionDepthRange(model, partitionIndex) {
    var slotBelow = getSlotDepthRange(model, partitionIndex);
    var slotAbove = getSlotDepthRange(model, partitionIndex + 1);
    var y0 = Math.max(slotBelow.y0, slotAbove.y0);
    var y1 = Math.min(slotBelow.y1, slotAbove.y1);
    return { y0: y0, y1: y1, depth: Math.max(0, y1 - y0) };
}
```

- [ ] **Step 3: Update partition rendering**

In `renderRackModel()`, replace the partition rendering (~lines 6966–6991):

Old:
```javascript
spawnPlanarRect(modelGroup,
    new THREE.Vector3(slotX, 0, z),
    new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0),
    slotWidth, model.base.D, ...);
```

New:
```javascript
rack.partitions.forEach(function(partition) {
    if (partition.deleted) return;
    if (!partition.forceVisible && isPartitionInternalToMergedRange(model, partition.index)) return;

    var pRange = getPartitionDepthRange(model, partition.index);
    if (pRange.depth < 1e-6) return;

    var z = rack.safeMarginBottom + (partition.index + 1) * rack.unitHeight;

    spawnPlanarRect(modelGroup,
        new THREE.Vector3(slotX, pRange.y0, z),
        new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0),
        slotWidth, pRange.depth,
        {
            color: '#88909b', opacity: opacity, doubleSide: true,
            userData: {
                modelId: model.id, isRackPartition: true,
                partitionIndex: partition.index,
                exportName: getRackPartitionExportName(model, partition.index)
            }
        }
    );
});
```

Key changes:
- Z position now includes `rack.safeMarginBottom` offset
- Depth is `pRange.depth` instead of `model.base.D`
- Y origin is `pRange.y0` instead of `0`

- [ ] **Step 4: Verify partitions**

Load a project with partitions. Verify:
- Partitions render at correct Z positions (offset by bottom margin)
- Partition depth matches slot cavity depth
- If two adjacent servers have different extraOffsets, partition takes the shallower depth

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(rack): partition depth follows slot cavity — intersection of adjacent slot depths"
```

---

### Task 4: Inspector UI — Margin Inputs

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — inspector rendering (~lines 8995–8996)

**Context:** Replace the integer `safeMarginTopUnits` / `safeMarginBottomUnits` input fields with continuous value inputs that use the project's global unit system via `toDisplayLength()` / `fromDisplayLength()`.

- [ ] **Step 1: Replace margin input fields**

Find the margin input fields (~lines 8995–8996):
```html
<div class="prop-row"><label>${t('label.safe_margin_top')}</label><input type="number" min="0" step="1" value="${rack.safeMarginTopUnits}" onchange="updateRackSetting('${model.id}', 'safeMarginTopUnits', this.value)"></div>
<div class="prop-row"><label>${t('label.safe_margin_bottom')}</label><input type="number" min="0" step="1" value="${rack.safeMarginBottomUnits}" onchange="updateRackSetting('${model.id}', 'safeMarginBottomUnits', this.value)"></div>
```

Replace with:
```javascript
var marginTopDisplay = toDisplayLength(rack.safeMarginTop).toFixed(2);
var marginBotDisplay = toDisplayLength(rack.safeMarginBottom).toFixed(2);
var unitLabel = getActiveUnitConfig().label || 'm';

html += '<div class="prop-row"><label>' + t('label.safe_margin_top') + ' (' + unitLabel + ')</label>';
html += '<input type="number" min="0" step="0.01" value="' + marginTopDisplay + '"';
html += ' onchange="updateRackSetting(\'' + model.id + '\', \'safeMarginTop\', fromDisplayLength(parseFloat(this.value) || 0))">';
html += '</div>';

html += '<div class="prop-row"><label>' + t('label.safe_margin_bottom') + ' (' + unitLabel + ')</label>';
html += '<input type="number" min="0" step="0.01" value="' + marginBotDisplay + '"';
html += ' onchange="updateRackSetting(\'' + model.id + '\', \'safeMarginBottom\', fromDisplayLength(parseFloat(this.value) || 0))">';
html += '</div>';
```

- [ ] **Step 2: Add computed total height display**

After the margin inputs, add a read-only display of computed total height:
```javascript
var totalH = toDisplayLength(model.base.H).toFixed(3);
html += '<div class="prop-row"><label>总高度 (计算)</label>';
html += '<span style="color:var(--hint-color);font-size:0.85rem;">' + totalH + ' ' + unitLabel + '</span>';
html += '</div>';
```

- [ ] **Step 3: Update `updateRackSetting()` handler**

Find `updateRackSetting()`. The old handler likely does `rack.safeMarginTopUnits = parseInt(value)`. For the new float fields, the `onchange` already passes `fromDisplayLength(...)` which converts to meters. The handler just needs to accept float values:

```javascript
if (key === 'safeMarginTop' || key === 'safeMarginBottom') {
    rack[key] = Math.max(0, parseFloat(value) || 0);
}
```

Verify the handler doesn't `parseInt` or `Math.round` the value.

- [ ] **Step 4: Remove `heightMode` selector if it still exists**

Since H is now always computed (`slotAreaH + margins`), the `heightMode` toggle ('totalHeight' vs 'unitHeight') is no longer needed. If the inspector shows a heightMode selector, remove it. The user controls `unitCount` and `unitHeight` directly.

- [ ] **Step 5: Verify UI**

Open browser, select a rack. Verify:
- Margin inputs show values in the project's unit (e.g., "44.45 mm")
- Changing margin updates rack height and re-renders
- Total height display updates correctly

- [ ] **Step 6: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(rack): inspector margin inputs with unit conversion + computed total height display"
```

---

### Task 5: CFD Export + STL Naming

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `buildCfdExportJson()` (~line 25791)

**Context:** Add `frameSolidName` and margin values to the rack export. Remove references to old shell solid names. Partition export already works (solid names unchanged). STL export already works because all frame meshes share `exportName = '{rackName}_frame'`.

- [ ] **Step 1: Add `frameSolidName` to rack export**

In `buildCfdExportJson()`, find the rack export object construction (~line 25791). Add:

```javascript
frameSolidName: sanitizeName(model.name) + '_frame',
safeMarginTop: model.rack.safeMarginTop || 0,
safeMarginBottom: model.rack.safeMarginBottom || 0,
```

- [ ] **Step 2: Add partition solid names to rack export**

Ensure the rack export includes partition solid names:
```javascript
partitions: rack.partitions
    .filter(function(p) { return !p.deleted; })
    .map(function(p) {
        return {
            index: p.index,
            solidName: getRackPartitionExportName(model, p.index)
        };
    }),
```

If this already exists, verify it's correct. If not, add it.

- [ ] **Step 3: Update door IIFE references**

The `inletDoor` and `outletDoor` IIFEs in `buildCfdExportJson()` reference `_rack.safeMarginTopUnits` / `_rack.safeMarginBottomUnits` to compute the central frame height for door validation. Update:

```javascript
// Old:
var _sb = Math.max(0, Math.round(_rack.safeMarginBottomUnits || 0)) * _rack.unitHeight;
var _st = Math.max(0, Math.round(_rack.safeMarginTopUnits || 0)) * _rack.unitHeight;
if (model.base.H - _sb - _st <= 1e-6) return null;

// New:
var slotAreaH = _rack.unitCount * _rack.unitHeight;
if (slotAreaH <= 1e-6) return null;
```

- [ ] **Step 4: Verify export**

In browser console:
```javascript
var json = buildCfdExportJson();
var rack = Object.values(json.racks)[0];
console.log(rack.frameSolidName);  // should be "{rackName}_frame"
console.log(rack.safeMarginTop);   // should be float (meters)
console.log(rack.partitions);      // should have solidName for each active partition
```

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(rack): CFD export frameSolidName + margin values + partition solid names"
```

---

### Task 6: Cleanup — Remove Dead Code

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

**Context:** Remove functions, variables, and code paths that are no longer used after the redesign.

- [ ] **Step 1: Remove `getRackSelectableSlotRange()` if not yet removed**

Delete the function and all callers. All slots are now selectable.

- [ ] **Step 2: Remove `isRackSlotSelectable()` if not yet removed**

Delete the function and all callers.

- [ ] **Step 3: Remove `heightMode` handling**

In `cloneRackState()`, remove the `heightMode` field. In `syncRackModelState()`, remove the `if (rack.heightMode === 'unitHeight')` branch. In the inspector, remove the heightMode selector.

- [ ] **Step 4: Verify no remaining references to old field names**

Search for `safeMarginTopUnits`, `safeMarginBottomUnits`, `heightMode` across the file. Should be zero matches (except in migration code in `cloneRackState` that reads old format).

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "chore(rack): remove dead code — getRackSelectableSlotRange, isRackSlotSelectable, heightMode"
```

---

### Task 7: Backend — BC Registration + snappyHexMesh

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py`

**Context:** The backend uses pattern matching (`_rack_io_pat`) to identify rack structural solids. The new `_frame` solid needs to be handled. Partitions get explicit `wall` BC. Old shell solid patterns may need updating.

- [ ] **Step 1: Add frame solid BC registration**

In `_build_bridge_bc()`, add explicit registration for the frame solid:

```python
# Rack frame: single wall BC
frame_solid = rack.get("frameSolidName")
if frame_solid:
    registry.register(_sanitize(frame_solid), BCSpec(bc_type="wall"))
    logger.info(f"  Rack {rack_name}: frame solid {frame_solid} → wall")
```

- [ ] **Step 2: Add partition BC registration**

In `_build_bridge_bc()`, add partition registration:

```python
# Partitions: all wall
for part in rack.get("partitions", []):
    solid = part.get("solidName")
    if solid:
        registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
```

- [ ] **Step 3: Update snappyHexMesh solid categorization**

In `_generate_snappy_hex_mesh_dict()`, the frame solid needs to be added to the rack refinement group. Find where rack solids are categorized (pattern matching with `_rack_io_pat`). Add explicit handling for the frame solid:

```python
# Collect frame solid name
frame_solid_name = rack.get("frameSolidName")
if frame_solid_name:
    rack_frame_solids.add(_sanitize(frame_solid_name))
```

Then in the refinement section, give it the rack surface refinement level:
```python
if sanitized in rack_frame_solids:
    solid_entries.append(f"""            {solid}
    {{
        level ({rl_rack[0]} {rl_rack[1]});
        patchInfo {{ type wall; }}
    }}""")
```

- [ ] **Step 4: Handle partition solids in snappyHexMesh**

Partition solids should also get rack refinement level and wall type:
```python
for part in rack.get("partitions", []):
    solid = part.get("solidName")
    if solid:
        partition_solids.add(_sanitize(solid))
```

- [ ] **Step 5: Verify backend**

Quick verification that the backend can process the new format without errors.

- [ ] **Step 6: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(rack): backend registers frame + partition solids as wall BC + snappyHexMesh regions"
```

---

## Self-Review Checklist

### Spec Coverage

| Spec Section | Plan Task |
|-------------|-----------|
| §1 State Changes | Task 1 |
| §2 Dimension Calculations | Task 1 (Steps 3, 6) |
| §3 Frame Geometry | Task 2 |
| §4 Partition Depth | Task 3 |
| §5 Inspector UI | Task 4 |
| §6 Mesh Refinement | Task 7 (Step 3) |
| §7 CFD Export | Task 5 |
| §8 File Changes | All tasks |
| §9 Test Plan | Verification steps |
| Backward compat | Task 1 (Step 2, 7) |
| Dead code cleanup | Task 6 |

### Placeholder Scan
- No TBD/TODO
- All code blocks complete
- All function signatures consistent

### Type Consistency
- `rack.safeMarginTop` / `rack.safeMarginBottom` — float, meters — consistent across Tasks 1-5
- `buildRackFrameGeometry(modelGroup, model, frameParams)` — consistent between Task 2 definition and call
- `getSlotDepthRange(model, slotIndex)` returns `{ y0, y1 }` — consistent between Tasks 3.1 and 3.2
- `getPartitionDepthRange(model, partitionIndex)` returns `{ y0, y1, depth }` — consistent
- `frameSolidName` field name — consistent between Task 5 (export) and Task 7 (backend)
