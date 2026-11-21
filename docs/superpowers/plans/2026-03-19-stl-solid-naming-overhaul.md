# STL Solid Region Naming Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix STL solid region naming so every solid is unique, model-prefixed, and follows the convention `{ModelName}_{FaceDisplayName}_patch[_N]` / `_part{N}` for non-rack entities, and `{RackName}_{context}` for rack entities. Add duplicate-name detection before export.

**Architecture:** All changes are in `STLGeneratorForOpenFoam/STL_generate.html` (one monolithic file). Naming is determined at render-time via `userData.exportName` on Three.js meshes, then consumed at export-time by `exportBoxModel()` which groups meshes by that name into `solid`/`endsolid` blocks. `buildCfdExportJson()` independently computes `solidName` fields for the CFD pipeline — these MUST match the render-time names exactly.

**Tech Stack:** Vanilla JS in a single HTML file (Three.js r128). No build system.

---

## Current State (What's Already Done)

Previous session created these helper functions (all working and integrated into rendering):

| Function | Status | Used in rendering? |
|----------|--------|-------------------|
| `composeModelScopedExportName(model, faceId, face, opts)` | EXISTS at line ~1830 | YES — renderBoxModel, renderPlanarModelWithPatches |
| `getRackFanServerExportName(model, equipment, side)` | EXISTS at line ~2385 | YES — renderRackModel |
| `getRackChannelWallExportName(model, equipment, face)` | EXISTS at line ~2397 | YES — renderRackModel |
| `getRackMergedSlotExportName(model, side, startUnit, endUnit)` | EXISTS at line ~2406 | YES — renderRackModel |
| `getRackMergedEmptySlotRanges(model, side)` | EXISTS at line ~1849 | YES — renderRackModel |
| `isPartitionInternalToMergedRange(model, partitionIndex)` | EXISTS at line ~1880 | YES — renderRackModel |
| `getRackPartitionExportName(model, partitionIndex)` | EXISTS at line ~2419 | YES — renderRackModel |

Rendering functions are already updated:
- `renderRackModel`: uses `rName` prefix, merged slot ranges, fan-server separate inlet/outlet, partition suppression
- `renderBoxModel` / `renderPlanarModelWithPatches`: use `composeModelScopedExportName`

## What's Still Broken

### Problem 1: Patch naming uses patch's user-given name, not numbered `_patch[_N]`

**Current:** `composeModelScopedExportName(model, faceId, face, { baseName: island.name })` produces `ACU_1_Bottom_supply` (patch name as suffix)
**Required:** `ACU_1_Bottom_patch` (single) or `ACU_1_Bottom_patch_1`, `_patch_2` (multiple)

Affects: `renderPlanarModelWithPatches` (lines ~3034), `renderBoxModel` (lines ~3129), `getPatchFinalExportName` (lines ~1914-1918), `buildCfdExportJson` ACU/wallObject/floor sections.

### Problem 2: Leakage zone STL/JSON naming mismatch

**`buildLeakageZoneStl` (line ~9888):** `${rackName}_leakage_${startUnit}_${endUnit}` (0-indexed, no "u", no "leakage_zone")
**`buildCfdExportJson` (line ~10022):** `${name}_u${startUnit+1}_u${endUnit+1}_leakage_zone` (1-indexed, correct)

These MUST match. The JSON is correct; the STL function is wrong.

### Problem 3: Floor openings still use raw patch name

**`buildCfdExportJson` floor section (line ~10131):** `sanitizeName(patch.name)` — no model prefix, no face prefix.

### Problem 4: Generic box/wall heat patches (second block) still use raw patch name

**Lines ~10094-10106:** `sanitizeName(patch.name || patch.id)` — no model prefix.

### Problem 5: No duplicate solid name detection

No function exists. If two models produce the same solid name, export proceeds silently, causing corrupted OpenFOAM patches.

---

## File Map

All changes in one file:

| File | Action |
|------|--------|
| `STLGeneratorForOpenFoam/STL_generate.html` | MODIFY — 7 functions, 1 new function |

Functions to modify:
1. `composeModelScopedExportName` (~line 1830) — add `isPatch` mode with numbering
2. `getPatchFinalExportName` (~line 1914) — compute patch index/count from face
3. `renderPlanarModelWithPatches` (~line 2965) — use `isPatch` mode
4. `renderBoxModel` (~line 3068) — use `isPatch` mode
5. `buildLeakageZoneStl` (~line 9855) — fix solid name to match JSON
6. `buildCfdExportJson` (~line 9911) — fix floor openings + generic box/wall heat patches
7. `getRackEquipmentExportName` (~line 2429) — update UI display to show new format

Function to create:
1. `checkDuplicateSolidNames()` — collect all names, detect collisions, return duplicate list

Integration point to modify:
1. `runMeshGeneration` / `buildGroupedStlMap` caller — call `checkDuplicateSolidNames` before export, abort if duplicates found

---

### Task 1: Fix `composeModelScopedExportName` to support numbered patches

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:~1830-1843`

- [ ] **Step 1: Read current function**

Read lines 1830-1843 to get exact current code.

- [ ] **Step 2: Modify `composeModelScopedExportName` to add `isPatch` mode**

Replace the current function with:

```javascript
function composeModelScopedExportName(model, faceId, face, opts) {
    const modelPrefix = sanitizeName(model.name);
    const facePrefix = sanitizeName(getFaceDisplayName(face) || faceId || 'face');
    if (opts && opts.isWallPart) {
        const hasPatches = face.patches && face.patches.length > 0;
        if (hasPatches) {
            return sanitizeName(`${modelPrefix}_${facePrefix}_part${opts.partIndex + 1}`);
        }
        return sanitizeName(`${modelPrefix}_${facePrefix}`);
    }
    if (opts && opts.isPatch) {
        // Numbered patches: single → _patch, multiple → _patch_1, _patch_2
        const suffix = opts.patchCount === 1 ? '' : `_${opts.patchIndex + 1}`;
        return sanitizeName(`${modelPrefix}_${facePrefix}_patch${suffix}`);
    }
    // Legacy fallback: use baseName directly
    const baseName = opts && opts.baseName ? sanitizeName(opts.baseName) : 'patch';
    return sanitizeName(`${modelPrefix}_${facePrefix}_${baseName}`);
}
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "feat: add isPatch numbering mode to composeModelScopedExportName"
```

---

### Task 2: Fix `getPatchFinalExportName` to use numbered patches

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:~1914-1918`

- [ ] **Step 1: Read current function**

Read lines 1914-1918 to get exact current code.

- [ ] **Step 2: Replace `getPatchFinalExportName`**

The function currently passes `baseName` (the patch's user name). Change it to compute the patch's index within its face and use numbered `isPatch` mode:

```javascript
function getPatchFinalExportName(model, faceId, patch) {
    const face = model.faces[faceId];
    const patches = face ? (face.patches || []) : [];
    const patchIndex = patches.findIndex(p => p.id === patch.id);
    const idx = patchIndex >= 0 ? patchIndex : 0;
    return composeModelScopedExportName(model, faceId, face, {
        isPatch: true, patchIndex: idx, patchCount: patches.length || 1
    });
}
```

This function is called in 7 places (custom STL export, UI preview, buildFaceIslands). All callers get the correct numbered name automatically.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "feat: getPatchFinalExportName uses numbered patch convention"
```

---

### Task 3: Update `renderPlanarModelWithPatches` and `renderBoxModel` to use `isPatch` mode

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:~3034,~3129`

- [ ] **Step 1: Read current rendering code**

Read the island export name lines in both functions.

- [ ] **Step 2: Update `renderPlanarModelWithPatches` island export name (~line 3034)**

Change:
```javascript
exportName: composeModelScopedExportName(model, primaryFaceId, face, { baseName: island.name }),
```

To:
```javascript
exportName: composeModelScopedExportName(model, primaryFaceId, face, { isPatch: true, patchIndex: islandIndex, patchCount: islands.length }),
```

Note: the `islands.forEach` loop must expose the index. Change `islands.forEach(island => {` to `islands.forEach((island, islandIndex) => {`.

- [ ] **Step 3: Update `renderBoxModel` island export name (~line 3129)**

Change:
```javascript
exportName: composeModelScopedExportName(model, faceId, face, { baseName: island.name }),
```

To:
```javascript
exportName: composeModelScopedExportName(model, faceId, face, { isPatch: true, patchIndex: islandIdx, patchCount: facesIslands[faceId].length }),
```

Note: the `facesIslands[faceId].forEach` loop must expose the index. Change it to use `(island, islandIdx)`.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "feat: rendering functions use numbered patch export names"
```

---

### Task 4: Fix `buildLeakageZoneStl` solid name to match JSON convention

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:~9888`

- [ ] **Step 1: Read current line**

Read line 9888 to get exact current pattern.

- [ ] **Step 2: Fix the solid name**

Change:
```javascript
const solidName = `${rackName}_leakage_${group.startUnit}_${group.endUnit}`;
```

To:
```javascript
const solidName = `${rackName}_u${group.startUnit + 1}_u${group.endUnit + 1}_leakage_zone`;
```

This matches the pattern already used in `buildCfdExportJson` at line ~10022.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "fix: buildLeakageZoneStl solid name matches cfd_export.json convention"
```

---

### Task 5: Fix ALL `buildCfdExportJson` solidName references to use `isPatch` mode

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:~10032-10043,~10058-10087,~10094-10106,~10128-10134`

The `buildCdfExportJson` function has FOUR sections that compute solidNames independently. ALL must use `isPatch` mode to match what rendering produces.

Helper: Add a utility inside `buildCfdExportJson` (at the top of the function, after `cfdData` declaration):

```javascript
// Compute the same numbered patch name that rendering produces
function patchSolidName(mdl, faceId, patchId) {
    const face = mdl.faces[faceId];
    const patches = face ? (face.patches || []) : [];
    const pIdx = patches.findIndex(p => p.id === patchId);
    return composeModelScopedExportName(mdl, faceId, face, {
        isPatch: true, patchIndex: Math.max(0, pIdx), patchCount: patches.length || 1
    });
}
```

- [ ] **Step 1: Read all four buildCfdExportJson solidName sections**

Read lines 10027-10145 to see ACU, wallObject box, generic box/wall, and floor sections.

- [ ] **Step 2: Fix ACU supply/return solidNames (lines ~10032-10043)**

Change both:
```javascript
solidName: composeModelScopedExportName(model, p.faceId, model.faces[p.faceId], { baseName: p.name || p.id }),
```

To:
```javascript
solidName: patchSolidName(model, p.faceId, p.id),
```

This applies to BOTH the `supplyFaces` map (~line 10033) and the `returnFaces` map (~line 10043).

- [ ] **Step 3: Fix wallObject box heat patches (lines ~10058-10087)**

Change:
```javascript
solidName: composeModelScopedExportName(model, fid, face, { baseName: patch.name || patch.id }),
```

To:
```javascript
solidName: patchSolidName(model, fid, patch.id),
```

- [ ] **Step 4: Fix generic box/wall heat patches (lines ~10094-10106)**

Change:
```javascript
solidName: sanitizeName(patch.name || patch.id),
```

To:
```javascript
solidName: patchSolidName(model, fid, patch.id),
```

- [ ] **Step 5: Fix floor openings (line ~10131)**

Change:
```javascript
solidName: patch ? sanitizeName(patch.name) : `opening_${op.patchId}`,
```

To:
```javascript
solidName: (() => {
    if (!patch) return `opening_${op.patchId}`;
    // Find which face this patch belongs to
    const faceId = Object.keys(model.faces).find(fid =>
        (model.faces[fid].patches || []).some(p => p.id === op.patchId)
    ) || 'top';
    return patchSolidName(model, faceId, op.patchId);
})(),
```

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "fix: all buildCfdExportJson solidNames use isPatch numbered convention"
```

---

### Task 6: Add duplicate solid name detection

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` — add new function + integrate into export flow

- [ ] **Step 1: Add `checkDuplicateSolidNames` function**

Insert near the export functions (after `buildGroupedStlMap`, around line ~8750):

```javascript
/**
 * Check for duplicate solid names across all export groups.
 * Returns { hasDuplicates: bool, duplicates: [{name, sources: ['ModelA', 'ModelB']}] }
 */
function checkDuplicateSolidNames(modelIds) {
    const idSet = new Set(modelIds);
    const nameMap = {}; // { solidName: [{modelName, source}] }

    const addName = (solidName, modelName, source) => {
        if (!nameMap[solidName]) nameMap[solidName] = [];
        nameMap[solidName].push({ model: modelName, source });
    };

    // Collect from render meshes (rack body, box, ACU, wall, baffle, floor)
    renderMeshes.forEach(mesh => {
        const data = mesh.userData || {};
        if (!data.exportName || !data.modelId) return;
        if (!idSet.has(data.modelId)) return;
        const model = getModelById(data.modelId);
        if (!model) return;
        addName(data.exportName, model.name || model.id, 'mesh');
    });

    // Collect from leakage zones (separate export path, uses getRackLeakageGroups directly)
    state.models.forEach(model => {
        if (!idSet.has(model.id) || model.kind !== 'rack') return;
        const groups = getRackLeakageGroups(model);
        if (!groups.length) return;
        const rackName = sanitizeName(model.name);
        groups.forEach(g => {
            const solidName = `${rackName}_u${g.startUnit + 1}_u${g.endUnit + 1}_leakage_zone`;
            addName(solidName, model.name || model.id, 'leakage');
        });
    });

    // Deduplicate: group by solidName, detect names that appear from multiple distinct sources
    // (same model + same exportName from multiple meshes is OK — they merge into one solid)
    const duplicates = [];
    Object.entries(nameMap).forEach(([name, entries]) => {
        const uniqueModels = [...new Set(entries.map(e => e.model))];
        if (uniqueModels.length > 1) {
            duplicates.push({ name, sources: uniqueModels });
        }
    });

    return { hasDuplicates: duplicates.length > 0, duplicates };
}
```

- [ ] **Step 2: Integrate into `window.runMeshGeneration` (line ~9537)**

Insert BEFORE `const allIds = state.models.map(m => m.id);` at line 9565 (inside the `try` block, before `buildGroupedStlMap`):

```javascript
// Duplicate solid name check before export
const dupAllIds = state.models.map(m => m.id);
const dupCheck = checkDuplicateSolidNames(dupAllIds);
if (dupCheck.hasDuplicates) {
    const dupMsg = dupCheck.duplicates.map(d =>
        `  "${d.name}" 出现在模型: ${d.sources.join(', ')}`
    ).join('\n');
    _appendDctwinLogLine(`[ERROR] STL solid region 命名重复，无法导出！`);
    dupCheck.duplicates.forEach(d => {
        _appendDctwinLogLine(`  重复名: "${d.name}" — 模型: ${d.sources.join(', ')}`);
    });
    _appendDctwinLogLine(`[提示] 请在几何栏目修改重名模型的名称后重试。`);
    if (statusEl) statusEl.textContent = `❌ 命名重复，导出已中止`;
    if (runBtn) runBtn.disabled = false;
    return;
}
```

- [ ] **Step 3: Integrate into `window.confirmExportMergedSTL` (line ~10339)**

Insert BEFORE `buildGroupedStlMap` call at line ~10163 (same duplicate check pattern):

```javascript
const dupAllIds = state.models.map(m => m.id);
const dupCheck = checkDuplicateSolidNames(dupAllIds);
if (dupCheck.hasDuplicates) {
    const dupMsg = dupCheck.duplicates.map(d =>
        `"${d.name}" 出现在: ${d.sources.join(', ')}`
    ).join('\n');
    alert(`STL导出失败：发现重复的 solid region 命名。\n${dupMsg}\n请修改模型命名后重试。`);
    return;
}
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "feat: add duplicate solid name detection before STL export"
```

---

### Task 7: Update `getRackEquipmentExportName` for UI display

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:~2429-2432`

- [ ] **Step 1: Update the function to show new-format name in UI**

This function is only used for the disabled "STL Region" display field in the equipment inspector. Change it to show the correct new-format name:

```javascript
function getRackEquipmentExportName(model, equipment) {
    if (equipment.kind === 'fanServer') {
        return getRackFanServerExportName(model, equipment, 'front');
    }
    const rName = sanitizeName(model.name);
    const eqName = sanitizeName(equipment.name || equipment.id);
    return `${rName}_${eqName}_front`;
}
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "chore: update getRackEquipmentExportName UI display to match new convention"
```

---

### Task 8: Verify end-to-end with V5_CFD_Station

- [ ] **Step 1: Start the server**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
./STLGeneratorForOpenFoam/start_server.sh --port 8000
```

- [ ] **Step 2: Load the V5_CFD_Station project in browser**

Open `http://localhost:8000/STL_generate.html`, load `V5_CFD_Station_1773823196452.cfdproj`.

- [ ] **Step 3: Click mesh generation and check STL solid names**

After mesh generation completes, verify:
```bash
for f in dctwin/case/V5_CFD_Station/Mesh/constant/triSurface/merged_*.stl room_wall.stl; do
    echo "=== $(basename $f) ==="
    grep "^solid " "$f"
done
```

Expected naming patterns:
- `Default_Rack_1_Left`, `Default_Rack_1_inlet_safe_bottom`, ...
- `Default_Rack_1_fan_server_1_inlet_patch`, `Default_Rack_1_fan_server_1_outlet_patch`
- `Default_Rack_1_inlet_u1_u14` (merged empty slots)
- `Default_Rack_1_u15_u20_leakage_zone`
- `ACU_1_Bottom_patch`, `ACU_1_Top_patch`
- No duplicate names across any STL files

- [ ] **Step 4: Check the boundary file**

```bash
grep "^    [A-Za-z]" dctwin/case/V5_CFD_Station/Mesh/constant/polyMesh/boundary
```

All patch names should be unique, with model name prefixes.

- [ ] **Step 5: Commit final state**

```bash
git add -A
git commit -m "test: verify STL solid naming with V5_CFD_Station case"
```
