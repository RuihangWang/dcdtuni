# Server Geometry Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade server (equipment) geometry from loose face fragments to a watertight 6-face closed body with the same face patch system used by Box/Baffle/Floor models, replacing P5's bespoke `outletPatch`.

**Architecture:** Add `equipment.faces{}` dict to state with auto-migration from old fields. Extract `buildServerGeometry()` from `renderRackModel()`. Reuse `buildFaceIslands()` for patch rendering. Model tree gains Face→Patch hierarchy. Backend reads new `faces` dict for BC registration.

**Tech Stack:** Three.js r128, vanilla JS (STL_generate.html ~26000 lines), Python (stlgen_runner.py ~3300 lines).

**Spec:** `docs/superpowers/specs/2026-04-14-server-geometry-redesign-design.md`

---

## File Map

| File | Responsibility | Tasks |
|------|---------------|-------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Equipment state, geometry, tree, inspector, export | 1–7 |
| `src/backend/aircooling/runner/stlgen_runner.py` | Backend BC registration, snappyHexMesh solid collection | 8 |

---

### Task 1: Equipment State + Migration

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `cloneRackState()` (lines ~1976–2032)

**Context:** Currently each equipment has flat fields `frontColor`, `backColor`, `outletPatch`. We add a `faces{}` dict and migrate those fields. When loading old projects without `faces`, `cloneRackState()` auto-creates the dict. This task touches ONLY the state layer — no rendering, tree, or export changes.

- [ ] **Step 1: Add `getDefaultEquipmentFaces()` helper**

Insert this function near `getDefaultRackState()` (around line 1970):

```javascript
function getDefaultEquipmentFaces(equipment) {
    return {
        inlet:  { patches: [], color: equipment?.frontColor || '#4488ff' },
        outlet: { patches: [], color: equipment?.backColor  || '#44ff88' },
        top:    { patches: [] },
        bottom: { patches: [] },
        left:   { patches: [] },
        right:  { patches: [] }
    };
}
```

- [ ] **Step 2: Add `_migrateOutletPatchToFaces()` helper**

Insert right after `getDefaultEquipmentFaces()`:

```javascript
function _migrateOutletPatchToFaces(equipment, faces) {
    var op = equipment.outletPatch;
    if (!op) return;
    var patch = {
        id: generateId(),
        type: op.type === 'circle' ? 'circle' : 'rect',
        color: '#00cc66',
        applied: true
    };
    if (patch.type === 'rect') {
        var w = Number.isFinite(op.width)  ? op.width  : 0.6;
        var h = Number.isFinite(op.height) ? op.height : 0.8;
        var cx = Number.isFinite(op.cx) ? op.cx : 0.5;
        var cy = Number.isFinite(op.cy) ? op.cy : 0.5;
        patch.x = Math.max(0, cx - w / 2);
        patch.y = Math.max(0, cy - h / 2);
        patch.w = Math.min(w, 1 - patch.x);
        patch.h = Math.min(h, 1 - patch.y);
    } else {
        patch.cx = Number.isFinite(op.cx) ? op.cx : 0.5;
        patch.cy = Number.isFinite(op.cy) ? op.cy : 0.5;
        patch.r  = Number.isFinite(op.radius) ? op.radius : 0.3;
    }
    faces.outlet.patches.push(patch);
}
```

- [ ] **Step 3: Modify `cloneRackState()` equipment mapping to add `faces`**

In `cloneRackState()` (lines ~2007–2030), the equipment `.map()` callback currently returns an object with `frontColor`, `backColor`, `outletPatch`. Modify it to:

1. Build `faces` from existing data or create defaults
2. Remove `frontColor`, `backColor`, `outletPatch` from the returned object

Replace the equipment mapping (inside the `.map()`) with:

```javascript
const rawFaces = equipment.faces;
var faces;
if (rawFaces && typeof rawFaces === 'object' && rawFaces.inlet) {
    // New format — clone faces
    faces = {};
    ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'].forEach(function(fid) {
        var src = rawFaces[fid] || {};
        faces[fid] = {
            patches: Array.isArray(src.patches) ? src.patches.map(function(p) {
                return Object.assign({}, p, { id: p.id || generateId() });
            }) : [],
        };
        if (src.color) faces[fid].color = src.color;
    });
} else {
    // Old format — migrate
    faces = getDefaultEquipmentFaces(equipment);
    _migrateOutletPatchToFaces(equipment, faces);
}

return {
    id: equipment.id || ('e_raw_' + (index + 1)),
    kind: equipment.kind === 'fanServer' ? 'fanServer' : 'nonFan',
    name: equipment.name || (preset.defaultName + '_' + (index + 1)),
    startUnit: Number.isFinite(equipment.startUnit) ? equipment.startUnit : 0,
    spanUnits: Number.isFinite(equipment.spanUnits) ? equipment.spanUnits : 1,
    extraInletOffset: Number.isFinite(equipment.extraInletOffset) ? equipment.extraInletOffset : 0,
    extraOutletOffset: Number.isFinite(equipment.extraOutletOffset) ? equipment.extraOutletOffset : 0,
    faces: faces,
    liquidCooling: equipment.liquidCooling
        ? JSON.parse(JSON.stringify(equipment.liquidCooling)) : null
};
```

Note: `frontColor`, `backColor`, and `outletPatch` are no longer in the returned object. Colors live in `faces.inlet.color` / `faces.outlet.color`.

- [ ] **Step 4: Update all reads of `equipment.frontColor` / `equipment.backColor`**

Search for `equipment.frontColor` and `equipment.backColor` across the file. Each occurrence must be replaced with `(equipment.faces && equipment.faces.inlet && equipment.faces.inlet.color) || '#4488ff'` and `... .outlet.color || '#44ff88'` respectively. Use a helper:

```javascript
function getEquipmentFaceColor(equipment, faceId) {
    var defaultColors = { inlet: '#4488ff', outlet: '#44ff88' };
    if (equipment.faces && equipment.faces[faceId] && equipment.faces[faceId].color) {
        return equipment.faces[faceId].color;
    }
    return defaultColors[faceId] || '#888888';
}
```

Replace all `equipment.frontColor` with `getEquipmentFaceColor(equipment, 'inlet')` and all `equipment.backColor` with `getEquipmentFaceColor(equipment, 'outlet')`.

- [ ] **Step 5: Update `updateRackEquipment()` for color changes**

Find the `updateRackEquipment` function (or wherever `frontColor`/`backColor` are written). Change the color update path so that setting 'frontColor' writes to `equipment.faces.inlet.color` and 'backColor' writes to `equipment.faces.outlet.color`:

```javascript
if (key === 'frontColor') {
    if (!eq.faces) eq.faces = getDefaultEquipmentFaces(eq);
    if (!eq.faces.inlet) eq.faces.inlet = { patches: [] };
    eq.faces.inlet.color = value;
} else if (key === 'backColor') {
    if (!eq.faces) eq.faces = getDefaultEquipmentFaces(eq);
    if (!eq.faces.outlet) eq.faces.outlet = { patches: [] };
    eq.faces.outlet.color = value;
} else {
    eq[key] = value;
}
```

- [ ] **Step 6: Verify backward compat — load old project**

Run the dev server, open browser, load an existing project file (e.g., `V5_CFD_Station_1773892919284.cfdproj`). Verify:
- No console errors
- Rack renders with correct server colors
- If any server had P5 `outletPatch`, it should now be in `faces.outlet.patches[0]`

Check via browser console: `state.models.find(m=>m.kind==='rack').rack.equipments[0].faces` should exist with 6 face entries.

- [ ] **Step 7: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(server): add equipment.faces{} state + migration from frontColor/backColor/outletPatch"
```

---

### Task 2: Server Face Export Naming

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — near export naming functions (lines ~5178–5209)

**Context:** We need a naming function that produces backward-compatible solid names when a face has no patches, and new `_p{N}` / `_wall` names when patches exist. This must be created before the geometry builder (Task 3) since geometry meshes need `exportName` in `userData`.

- [ ] **Step 1: Create `getServerFaceExportName()` function**

Insert near `getRackFanServerExportName()` (around line 5209):

```javascript
/**
 * Generate STL solid name for a server face or its patch/wall sub-regions.
 *
 * @param {Object} model      - rack model
 * @param {Object} equipment  - equipment object
 * @param {string} faceId     - 'inlet'|'outlet'|'top'|'bottom'|'left'|'right'
 * @param {Object} [opts]     - { isPatch, patchIndex, patchCount, isWall }
 * @returns {string} sanitized solid name
 */
function getServerFaceExportName(model, equipment, faceId, opts) {
    var stem = getRackEquipmentExportStem(model, equipment);

    // Determine base face suffix (backward-compat with existing naming)
    var faceSuffix;
    if (equipment.kind === 'fanServer') {
        if (faceId === 'inlet')  faceSuffix = 'inlet_patch';
        else if (faceId === 'outlet') faceSuffix = 'outlet_patch';
        else faceSuffix = faceId;  // top, bottom, left, right
    } else {
        // nonFan: front/back/top/bottom/left/right
        if (faceId === 'inlet')  faceSuffix = 'front';
        else if (faceId === 'outlet') faceSuffix = 'back';
        else faceSuffix = faceId;
    }

    if (!opts) {
        // Whole face (no patches)
        return sanitizeName(stem + '_' + faceSuffix);
    }

    if (opts.isPatch) {
        // Patch region: {stem}_{faceId}_p{index}
        return sanitizeName(stem + '_' + faceId + '_p' + opts.patchIndex);
    }

    if (opts.isWall) {
        // Wall remainder: {stem}_{faceId}_wall
        return sanitizeName(stem + '_' + faceId + '_wall');
    }

    return sanitizeName(stem + '_' + faceSuffix);
}
```

- [ ] **Step 2: Verify naming backward compatibility**

In browser console, test with an existing rack:
```javascript
var m = state.models.find(m => m.kind === 'rack');
var eq = m.rack.equipments[0];
console.log(getServerFaceExportName(m, eq, 'inlet'));   // should match getRackFanServerExportName(m, eq, 'front')
console.log(getServerFaceExportName(m, eq, 'outlet'));  // should match getRackFanServerExportName(m, eq, 'back')
console.log(getServerFaceExportName(m, eq, 'top'));     // should match getRackChannelWallExportName(m, eq, 'top')
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(server): add getServerFaceExportName() with backward-compat naming"
```

---

### Task 3: buildServerGeometry() — Geometry Builder

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — new function + `renderRackModel()` integration (lines ~6590–6834)

**Context:** Extract the fan-server and non-fan rendering code into `buildServerGeometry()`. This function takes a server bounding box and renders 6 faces. Initially, it does NOT handle patches — just renders each face as a single solid (identical visual result to current code). Patch rendering is added in Task 4.

- [ ] **Step 1: Create `getServerFaceRect()` helper**

Insert near `spawnPlanarRect()` (around line 6316). This returns the 3D orientation of each face:

```javascript
/**
 * Get the 3D rectangle definition for a server face.
 * @param {Object} box - { x0, x1, y0, y1, z0, z1 } server bounding box
 *   x0/x1 = left/right, y0/y1 = front(inlet)/back(outlet), z0/z1 = bottom/top
 * @param {string} faceId - face identifier
 * @returns {{ origin: THREE.Vector3, right: THREE.Vector3, up: THREE.Vector3, w: number, h: number }}
 */
function getServerFaceRect(box, faceId) {
    var dx = box.x1 - box.x0;  // width
    var dy = box.y1 - box.y0;  // depth
    var dz = box.z1 - box.z0;  // height

    switch (faceId) {
        case 'inlet':
            return {
                origin: new THREE.Vector3(box.x0, box.y0, box.z0),
                right:  new THREE.Vector3(1, 0, 0),
                up:     new THREE.Vector3(0, 0, 1),
                w: dx, h: dz
            };
        case 'outlet':
            return {
                origin: new THREE.Vector3(box.x1, box.y1, box.z0),
                right:  new THREE.Vector3(-1, 0, 0),
                up:     new THREE.Vector3(0, 0, 1),
                w: dx, h: dz
            };
        case 'bottom':
            return {
                origin: new THREE.Vector3(box.x0, box.y0, box.z0),
                right:  new THREE.Vector3(1, 0, 0),
                up:     new THREE.Vector3(0, 1, 0),
                w: dx, h: dy
            };
        case 'top':
            return {
                origin: new THREE.Vector3(box.x0, box.y1, box.z1),
                right:  new THREE.Vector3(1, 0, 0),
                up:     new THREE.Vector3(0, -1, 0),
                w: dx, h: dy
            };
        case 'left':
            return {
                origin: new THREE.Vector3(box.x0, box.y0, box.z0),
                right:  new THREE.Vector3(0, 1, 0),
                up:     new THREE.Vector3(0, 0, 1),
                w: dy, h: dz
            };
        case 'right':
            return {
                origin: new THREE.Vector3(box.x1, box.y1, box.z0),
                right:  new THREE.Vector3(0, -1, 0),
                up:     new THREE.Vector3(0, 0, 1),
                w: dy, h: dz
            };
        default:
            return null;
    }
}
```

- [ ] **Step 2: Create `buildServerGeometry()` function**

Insert near `getServerFaceRect()`:

```javascript
/**
 * Build watertight 6-face geometry for a single server (equipment).
 * Replaces the inline fan-server and non-fan rendering code in renderRackModel().
 *
 * @param {THREE.Group} modelGroup - parent group for meshes
 * @param {Object} model           - the rack model
 * @param {Object} equipment       - the equipment object
 * @param {Object} serverBox       - { x0, x1, y0, y1, z0, z1 }
 * @param {number} opacity         - mesh opacity
 * @param {Array}  renderMeshes    - output array to push meshes
 * @param {Object} deletedFaces    - { front: bool, back: bool } whether inlet/outlet deleted
 */
function buildServerGeometry(modelGroup, model, equipment, serverBox, opacity, renderMeshes, deletedFaces) {
    var faceIds = ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'];
    var exportStem = getRackEquipmentExportStem(model, equipment);
    var slotDepth = serverBox.y1 - serverBox.y0;

    // Skip left/right if server has no depth
    var activeFaces = faceIds.filter(function(fid) {
        if (fid === 'inlet' && deletedFaces.front) return false;
        if (fid === 'outlet' && deletedFaces.back) return false;
        if ((fid === 'left' || fid === 'right') && slotDepth < 1e-6) return false;
        return true;
    });

    var channelWallColor = '#666666';

    for (var fi = 0; fi < activeFaces.length; fi++) {
        var faceId = activeFaces[fi];
        var faceDef = (equipment.faces && equipment.faces[faceId]) || { patches: [] };
        var fr = getServerFaceRect(serverBox, faceId);
        if (!fr || fr.w < 1e-6 || fr.h < 1e-6) continue;

        var faceColor;
        if (faceId === 'inlet')       faceColor = getEquipmentFaceColor(equipment, 'inlet');
        else if (faceId === 'outlet') faceColor = getEquipmentFaceColor(equipment, 'outlet');
        else                          faceColor = channelWallColor;

        var objectKind = (faceId === 'inlet' || faceId === 'outlet') ? 'server-front' : 'server-wall';
        var doubleSide = (faceId !== 'inlet' && faceId !== 'outlet');

        var baseUserData = {
            modelId: model.id,
            isRackEquipment: true,
            equipmentId: equipment.id,
            faceId: faceId,
            isPatch: false,
            patchId: null
        };

        if (faceDef.patches && faceDef.patches.length > 0) {
            // Has patches → render with buildFaceIslands (Task 4)
            _buildServerFaceWithPatches(modelGroup, model, equipment, faceId, fr, faceDef, faceColor,
                opacity, objectKind, doubleSide, baseUserData, renderMeshes);
        } else {
            // No patches → single solid face
            var exportName = getServerFaceExportName(model, equipment, faceId);
            var mesh = spawnPlanarRect(modelGroup, fr.origin, fr.right, fr.up, fr.w, fr.h, {
                color: faceColor, opacity: opacity, objectKind: objectKind,
                doubleSide: doubleSide,
                userData: Object.assign({}, baseUserData, {
                    exportName: exportName,
                    side: (faceId === 'inlet') ? 'front' : (faceId === 'outlet') ? 'back' : faceId
                })
            });
            if (mesh) renderMeshes.push(mesh);
        }
    }
}

// Placeholder for Task 4 — will be replaced with real island rendering
function _buildServerFaceWithPatches(modelGroup, model, equipment, faceId, fr, faceDef, faceColor,
    opacity, objectKind, doubleSide, baseUserData, renderMeshes) {
    // Temporary: render as single face ignoring patches (replaced in Task 4)
    var exportName = getServerFaceExportName(model, equipment, faceId);
    var mesh = spawnPlanarRect(modelGroup, fr.origin, fr.right, fr.up, fr.w, fr.h, {
        color: faceColor, opacity: opacity, objectKind: objectKind,
        doubleSide: doubleSide,
        userData: Object.assign({}, baseUserData, { exportName: exportName, side: faceId })
    });
    if (mesh) renderMeshes.push(mesh);
}
```

- [ ] **Step 3: Replace fan-server rendering code in `renderRackModel()`**

Find the fan-server rendering block (lines ~6669–6834). This block starts after `if (equipment.kind === 'fanServer') {` (or similar check inside the equipment loop). Replace the entire fan-server face rendering with:

```javascript
// --- Fan-server: delegate to buildServerGeometry ---
var eqSlotStart = slotDepthStart + (equipment.extraInletOffset || 0);
var eqSlotEnd   = slotDepthEnd   - (equipment.extraOutletOffset || 0);
if (eqSlotEnd <= eqSlotStart) { eqSlotEnd = eqSlotStart + 0.001; }

var serverBox = {
    x0: bounds.x,
    x1: bounds.x + bounds.w,
    y0: eqSlotStart,
    y1: eqSlotEnd,
    z0: bounds.y,
    z1: bounds.y + height
};

buildServerGeometry(modelGroup, model, equipment, serverBox, opacity, renderMeshes, {
    front: frontDeleted, back: backDeleted
});
```

Where `slotDepthStart`, `slotDepthEnd`, `bounds`, `height`, `frontDeleted`, `backDeleted`, and `opacity` are the existing local variables already computed earlier in the equipment loop.

- [ ] **Step 4: Replace non-fan rendering code similarly**

Find the non-fan rendering block (lines ~6590–6668). Replace it with the same `buildServerGeometry()` call (the function handles both `fanServer` and `nonFan` — the only difference is color and export naming, which `getServerFaceExportName()` handles).

The non-fan block uses the same `serverBox` computation. The call is identical:

```javascript
// --- NonFan: delegate to buildServerGeometry ---
var eqSlotStart = slotDepthStart + (equipment.extraInletOffset || 0);
var eqSlotEnd   = slotDepthEnd   - (equipment.extraOutletOffset || 0);
if (eqSlotEnd <= eqSlotStart) { eqSlotEnd = eqSlotStart + 0.001; }

var serverBox = {
    x0: bounds.x,
    x1: bounds.x + bounds.w,
    y0: eqSlotStart,
    y1: eqSlotEnd,
    z0: bounds.y,
    z1: bounds.y + height
};

buildServerGeometry(modelGroup, model, equipment, serverBox, opacity, renderMeshes, {
    front: frontDeleted, back: backDeleted
});
```

Since both fanServer and nonFan now use the same call, the `if (equipment.kind === 'fanServer')` / `else` branch can be unified into a single block (remove the kind check for the rendering part — `buildServerGeometry` + `getServerFaceExportName` internally handle the kind difference).

- [ ] **Step 5: Verify rendering unchanged**

Open browser, load a project with rack + equipment. Verify:
- Fan-servers render with correct inlet (blue) / outlet (green) colors
- Non-fan equipment renders with correct colors on all 6 faces
- Top/bottom channel walls visible
- Left/right side walls visible when depth > 0
- Door rendering is NOT affected (door code stays in renderRackModel)

- [ ] **Step 6: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "refactor(server): extract buildServerGeometry() from renderRackModel()"
```

---

### Task 4: Face Patch Rendering

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — replace `_buildServerFaceWithPatches` placeholder

**Context:** Replace the Task 3 placeholder with real patch rendering using `buildFaceIslands()`. When a face has patches, the face is split into island meshes (patch regions) + wall remainder meshes (non-patch area), exactly like `renderBoxModel()` does for box faces.

- [ ] **Step 1: Implement `_buildServerFaceWithPatches()`**

Replace the placeholder function with the real implementation:

```javascript
function _buildServerFaceWithPatches(modelGroup, model, equipment, faceId, fr, faceDef, faceColor,
    opacity, objectKind, doubleSide, baseUserData, renderMeshes) {

    // Build a temporary model-like object so buildFaceIslands() can process it
    // buildFaceIslands() expects model.faces[faceId].patches[]
    var tempModel = { faces: {} };
    tempModel.faces[faceId] = { patches: faceDef.patches };

    // Override getPatchFinalExportName for server context
    var facesIslands = {};
    var islands = [];
    faceDef.patches.forEach(function(patch, pIdx) {
        var poly = patch.type === 'circle'
            ? circleToPoly(patch.cx, patch.cy, patch.r)
            : rectToPoly(patch.x, patch.y, patch.w, patch.h);

        var current = {
            poly: poly,
            name: getServerFaceExportName(model, equipment, faceId, { isPatch: true, patchIndex: pIdx }),
            color: patch.color || '#00cc66',
            refIds: [patch.id]
        };

        var nextIslands = [];
        islands.forEach(function(island) {
            var inter = [];
            try { inter = polygonClipping.intersection(island.poly, current.poly); } catch (e) {}
            if (inter && inter.length > 0) {
                current.poly = polygonClipping.union(current.poly, island.poly);
                current.refIds = current.refIds.concat(island.refIds);
            } else {
                nextIslands.push(island);
            }
        });
        nextIslands.push(current);
        islands = nextIslands;
    });

    // Build 3D transform matrix for this face
    var normal = new THREE.Vector3().crossVectors(fr.right, fr.up).normalize();
    var faceMat = new THREE.Matrix4().makeBasis(
        fr.right.clone(), fr.up.clone(), normal
    ).setPosition(fr.origin.clone());

    // Render each island (patch region)
    islands.forEach(function(island, islandIdx) {
        island.poly.forEach(function(polygon) {
            var shape = new THREE.Shape();
            polygon[0].forEach(function(pt, i) {
                if (i === 0) shape.moveTo(pt[0] * fr.w, pt[1] * fr.h);
                else shape.lineTo(pt[0] * fr.w, pt[1] * fr.h);
            });
            for (var hi = 1; hi < polygon.length; hi++) {
                var hole = new THREE.Path();
                polygon[hi].forEach(function(pt, j) {
                    if (j === 0) hole.moveTo(pt[0] * fr.w, pt[1] * fr.h);
                    else hole.lineTo(pt[0] * fr.w, pt[1] * fr.h);
                });
                shape.holes.push(hole);
            }

            var geom = new THREE.ShapeGeometry(shape, 32);
            geom.applyMatrix4(faceMat);

            var mesh = spawnRenderable(modelGroup, geom, {
                color: island.color,
                opacity: opacity * 0.9,
                objectKind: objectKind,
                doubleSide: doubleSide,
                userData: Object.assign({}, baseUserData, {
                    isPatch: true,
                    patchId: island.refIds[0],
                    exportName: island.name,
                    side: faceId
                })
            });
            if (mesh) renderMeshes.push(mesh);
        });
    });

    // Render wall remainder (face minus all islands)
    var wallPoly = [[
        [0, 0], [1, 0], [1, 1], [0, 1], [0, 0]
    ]];
    islands.forEach(function(island) {
        try { wallPoly = polygonClipping.difference(wallPoly, island.poly); } catch (e) {}
    });

    var wallExportName = getServerFaceExportName(model, equipment, faceId, { isWall: true });
    wallPoly.forEach(function(polygon) {
        if (!polygon || !polygon[0] || polygon[0].length < 4) return;
        var shape = new THREE.Shape();
        polygon[0].forEach(function(pt, i) {
            if (i === 0) shape.moveTo(pt[0] * fr.w, pt[1] * fr.h);
            else shape.lineTo(pt[0] * fr.w, pt[1] * fr.h);
        });
        for (var hi = 1; hi < polygon.length; hi++) {
            var hole = new THREE.Path();
            polygon[hi].forEach(function(pt, j) {
                if (j === 0) hole.moveTo(pt[0] * fr.w, pt[1] * fr.h);
                else hole.lineTo(pt[0] * fr.w, pt[1] * fr.h);
            });
            shape.holes.push(hole);
        }

        var geom = new THREE.ShapeGeometry(shape, 32);
        geom.applyMatrix4(faceMat);

        var mesh = spawnRenderable(modelGroup, geom, {
            color: faceColor,
            opacity: opacity * 0.6,
            objectKind: objectKind,
            doubleSide: doubleSide,
            userData: Object.assign({}, baseUserData, {
                isPatch: false,
                isWallRemainder: true,
                exportName: wallExportName,
                side: faceId
            })
        });
        if (mesh) renderMeshes.push(mesh);
    });
}
```

- [ ] **Step 2: Verify patch rendering with test data**

In browser console, manually add a patch to a server's outlet face:

```javascript
var m = state.models.find(m => m.kind === 'rack');
var eq = m.rack.equipments[0];
eq.faces.outlet.patches.push({
    id: 'test-patch-1', type: 'rect', x: 0.2, y: 0.1, w: 0.6, h: 0.8,
    color: '#00cc66', applied: true
});
rebuildGeometry(); updateUI();
```

Verify: green rectangular patch visible on outlet face, grey wall remainder around it.

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(server): face patch rendering via buildFaceIslands pattern"
```

---

### Task 5: Model Tree — Face→Patch Hierarchy

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — model tree rendering (near lines ~7973–7990)

**Context:** Currently equipment nodes in the tree expand to show child U-slot nodes. We replace this with a Face→Patch hierarchy: clicking an equipment expands to show 6 face nodes, each face expands to show its patches. We also need selection handlers for faces and patches.

- [ ] **Step 1: Add selection types for server face and patch**

Find where `state.selection` types are handled (search for `type === 'rackEquipment'`). We add two new selection types. Add these selection creator functions near the existing ones:

```javascript
window.selectServerFace = function(event, modelId, equipmentId, faceId) {
    if (event) event.stopPropagation();
    pushUndoCheckpoint();
    state.selection = {
        type: 'serverFace',
        modelId: modelId,
        equipmentId: equipmentId,
        faceId: faceId
    };
    updateUI();
    highlightSelection();
    logOperation('select', 'Selected server face: ' + faceId);
};

window.selectServerPatch = function(event, modelId, equipmentId, faceId, patchId) {
    if (event) event.stopPropagation();
    pushUndoCheckpoint();
    state.selection = {
        type: 'serverPatch',
        modelId: modelId,
        equipmentId: equipmentId,
        faceId: faceId,
        patchId: patchId
    };
    updateUI();
    highlightSelection();
    logOperation('select', 'Selected server patch: ' + patchId);
};
```

- [ ] **Step 2: Modify equipment tree node children**

Find the equipment tree rendering code (lines ~7973–7990) where child U-slot nodes are rendered. Replace the child loop with face→patch nodes:

```javascript
if (!isTreeCollapsed(equipmentKey)) {
    html += '<ul>';
    var faceOrder = ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'];
    var faceLabels = { inlet: '◻ Inlet', outlet: '◻ Outlet', top: '◻ Top',
                       bottom: '◻ Bottom', left: '◻ Left', right: '◻ Right' };

    faceOrder.forEach(function(fid) {
        var faceDef = (equipment.faces && equipment.faces[fid]) || { patches: [] };
        var faceTreeKey = 'srv-face:' + model.id + ':' + equipment.id + ':' + fid;
        var faceSelected = state.selection.type === 'serverFace'
            && state.selection.modelId === model.id
            && state.selection.equipmentId === equipment.id
            && state.selection.faceId === fid;

        html += '<li class="tree-node">';
        html += '<div class="node-label ' + (faceSelected ? 'selected' : '') + '"'
            + ' onclick="selectServerFace(event, \'' + model.id + '\', \'' + equipment.id + '\', \'' + fid + '\')">';
        html += '<span class="node-main">';
        if (faceDef.patches && faceDef.patches.length > 0) {
            html += getTreeExpander(faceTreeKey, true);
        }
        html += ' ' + faceLabels[fid];
        html += '</span>';
        if (faceDef.patches && faceDef.patches.length > 0) {
            html += '<span class="badge">' + faceDef.patches.length + ' patch</span>';
        }
        html += '</div>';

        // Patch children
        if (faceDef.patches && faceDef.patches.length > 0 && !isTreeCollapsed(faceTreeKey)) {
            html += '<ul>';
            faceDef.patches.forEach(function(patch, pIdx) {
                var patchSelected = state.selection.type === 'serverPatch'
                    && state.selection.patchId === patch.id;
                var pLabel = (patch.type === 'circle' ? '● ' : '■ ') + 'Patch ' + (pIdx + 1);
                var pDetail = patch.type === 'rect'
                    ? Math.round((patch.w||0)*100) + '×' + Math.round((patch.h||0)*100) + '%'
                    : 'r=' + Math.round((patch.r||0)*100) + '%';

                html += '<li class="tree-node">';
                html += '<div class="node-label ' + (patchSelected ? 'selected' : '') + '"'
                    + ' onclick="selectServerPatch(event, \'' + model.id + '\', \'' + equipment.id
                    + '\', \'' + fid + '\', \'' + patch.id + '\')">';
                html += '<span class="node-main">';
                html += '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
                    + 'background:' + (patch.color || '#00cc66') + ';vertical-align:middle;margin-right:4px;"></span>';
                html += pLabel;
                html += '</span>';
                html += '<span class="badge">' + pDetail + '</span>';
                html += '</div></li>';
            });
            html += '</ul>';
        }

        html += '</li>';
    });

    html += '</ul>';
}
```

- [ ] **Step 3: Update `highlightSelection()` for new selection types**

Find `highlightSelection()` (search for `function highlightSelection`). Add handling for `serverFace` and `serverPatch`:

```javascript
if (sel.type === 'serverFace') {
    renderMeshes.forEach(function(mesh) {
        var ud = mesh.userData || {};
        if (ud.modelId === sel.modelId && ud.equipmentId === sel.equipmentId
            && ud.faceId === sel.faceId) {
            highlightMesh(mesh);
        }
    });
} else if (sel.type === 'serverPatch') {
    renderMeshes.forEach(function(mesh) {
        var ud = mesh.userData || {};
        if (ud.modelId === sel.modelId && ud.equipmentId === sel.equipmentId
            && ud.patchId === sel.patchId) {
            highlightMesh(mesh);
        }
    });
}
```

- [ ] **Step 4: Update 3D click handler for face/patch mesh selection**

Find the raycaster click handler (search for `isRackEquipment` in click handling). When clicking a server mesh that has `userData.faceId`, dispatch to the appropriate selection:

```javascript
if (ud.isRackEquipment && ud.faceId) {
    if (ud.isPatch && ud.patchId) {
        selectServerPatch(null, ud.modelId, ud.equipmentId, ud.faceId, ud.patchId);
    } else {
        selectServerFace(null, ud.modelId, ud.equipmentId, ud.faceId);
    }
    return;
}
```

- [ ] **Step 5: Verify tree and selection**

Load a project with rack. Verify:
- Equipment node expands to show 6 face entries (inlet, outlet, top, bottom, left, right)
- Faces are collapsed by default (no patch children visible unless patches exist)
- Clicking a face in tree highlights corresponding meshes in 3D
- Clicking a face mesh in 3D selects it in tree

- [ ] **Step 6: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(server): model tree Face→Patch hierarchy + face/patch selection"
```

---

### Task 6: Inspector UI — Face Patch Editor

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — inspector panel rendering

**Context:** When a server face or patch is selected, the right panel shows a face inspector with color picker, patch list, and add/edit/delete controls. This reuses the same UI pattern as the box face inspector. Slider handlers must call `doRebuildRackDetail()` (not `rebuildGeometry()`) when in rack detail mode to avoid DOM rebuild during drag.

- [ ] **Step 1: Create `renderServerFaceInspector()` function**

Add near `renderRackEquipmentInspector()`:

```javascript
function renderServerFaceInspector(prop, model, equipment, faceId) {
    var faceDef = (equipment.faces && equipment.faces[faceId]) || { patches: [] };
    var faceLabels = { inlet: 'Inlet', outlet: 'Outlet', top: 'Top',
                       bottom: 'Bottom', left: 'Left', right: 'Right' };
    var faceColor = getEquipmentFaceColor(equipment, faceId);
    var showColor = (faceId === 'inlet' || faceId === 'outlet');

    var html = renderUnitControls();
    html += '<div class="prop-group"><div class="prop-title">Server Face</div>';
    html += '<div style="font-size:0.95rem;color:#fff;margin-bottom:4px;">';
    html += '<b>' + equipment.name + '</b> → ' + faceLabels[faceId];
    html += '</div>';

    if (showColor) {
        html += '<div class="prop-row">';
        html += '<label>面颜色</label>';
        html += '<input type="color" value="' + faceColor + '"'
            + ' onchange="window._srvSetFaceColor(\'' + model.id + '\',\'' + equipment.id
            + '\',\'' + faceId + '\',this.value)">';
        html += '</div>';
    }

    html += '<div class="prop-row"><label>STL Region</label>';
    html += '<input type="text" value="' + getServerFaceExportName(model, equipment, faceId)
        + '" disabled style="border-color:var(--hint-color);color:var(--hint-color);font-size:0.75rem;">';
    html += '</div></div>';

    // Patches section
    html += '<div class="prop-group" style="border-color:#00aa44;">';
    html += '<div class="prop-title" style="color:#00aa44;">Patches</div>';

    if (faceDef.patches && faceDef.patches.length > 0) {
        faceDef.patches.forEach(function(patch, idx) {
            var pLabel = patch.type === 'rect'
                ? Math.round((patch.w||0)*100) + '×' + Math.round((patch.h||0)*100) + '%'
                : 'r=' + Math.round((patch.r||0)*100) + '%';
            html += '<div style="display:flex;align-items:center;gap:6px;padding:4px 8px;'
                + 'background:#1a2a1a;border-radius:4px;margin-bottom:4px;">';
            html += '<span style="display:inline-block;width:12px;height:12px;border-radius:2px;'
                + 'background:' + (patch.color || '#00cc66') + ';"></span>';
            html += '<span style="flex:1;font-size:12px;">Patch ' + (idx + 1)
                + ' <span style="color:#888;">' + pLabel + '</span></span>';
            html += '<span style="color:#f88;cursor:pointer;font-size:11px;"'
                + ' onclick="window._srvDeletePatch(\'' + model.id + '\',\'' + equipment.id
                + '\',\'' + faceId + '\',\'' + patch.id + '\')">✕</span>';
            html += '</div>';
        });
    } else {
        html += '<div style="padding:4px 8px;font-size:12px;color:#666;">无 patch</div>';
    }

    html += '<button class="btn" style="width:100%;margin-top:6px;"'
        + ' onclick="window._srvAddPatch(\'' + model.id + '\',\'' + equipment.id
        + '\',\'' + faceId + '\')">+ 添加 Patch</button>';
    html += '</div>';

    // Selected patch editor (if a serverPatch is selected within this face)
    var sel = state.selection;
    if (sel.type === 'serverPatch' && sel.faceId === faceId && sel.equipmentId === equipment.id) {
        var selPatch = faceDef.patches.find(function(p) { return p.id === sel.patchId; });
        if (selPatch) {
            html += _renderServerPatchEditor(model, equipment, faceId, selPatch);
        }
    }

    prop.innerHTML = html;
}
```

- [ ] **Step 2: Create `_renderServerPatchEditor()` helper**

```javascript
function _renderServerPatchEditor(model, equipment, faceId, patch) {
    var mid = model.id, eid = equipment.id, pid = patch.id;
    var prefix = 'window._srvSetPatchProp(\'' + mid + '\',\'' + eid + '\',\'' + faceId + '\',\'' + pid + '\',';

    var html = '<div class="prop-group" style="border-color:#0a8;">';
    html += '<div class="prop-title" style="color:#0a8;">Patch 编辑</div>';

    // Type selector
    html += '<div class="prop-row"><label>类型</label>';
    html += '<select style="font-size:11px;" onchange="' + prefix + '\'type\',this.value)">';
    html += '<option value="rect"' + (patch.type === 'rect' ? ' selected' : '') + '>矩形 Rect</option>';
    html += '<option value="circle"' + (patch.type === 'circle' ? ' selected' : '') + '>圆形 Circle</option>';
    html += '</select></div>';

    // Color
    html += '<div class="prop-row"><label>颜色</label>';
    html += '<input type="color" value="' + (patch.color || '#00cc66') + '"'
        + ' onchange="' + prefix + '\'color\',this.value)"></div>';

    if (patch.type === 'rect') {
        html += _srvSliderRow('X 位置', patch.x || 0, prefix + '\'x\',$V)');
        html += _srvSliderRow('Y 位置', patch.y || 0, prefix + '\'y\',$V)');
        html += _srvSliderRow('宽度',   patch.w || 0.5, prefix + '\'w\',$V)');
        html += _srvSliderRow('高度',   patch.h || 0.5, prefix + '\'h\',$V)');
    } else {
        html += _srvSliderRow('中心 X', patch.cx || 0.5, prefix + '\'cx\',$V)');
        html += _srvSliderRow('中心 Y', patch.cy || 0.5, prefix + '\'cy\',$V)');
        html += _srvSliderRow('半径',   patch.r  || 0.3, prefix + '\'r\',$V)');
    }

    html += '</div>';
    return html;
}

function _srvSliderRow(label, value, onchangeTemplate) {
    var pct = Math.round(value * 100);
    var inputId = 'srv-sl-' + label.replace(/\s/g, '');
    var onInput = onchangeTemplate.replace('$V', 'parseFloat(this.value)/100');
    var onInputSync = ' oninput="document.getElementById(\'' + inputId + '-val\').value='
        + 'Math.round(this.value)+\'%\'; ' + onInput + '"';

    var html = '<div class="prop-row"><label style="font-size:11px;">' + label + '</label>';
    html += '<input id="' + inputId + '-val" type="text" value="' + pct + '%"'
        + ' style="width:50px;font-size:11px;text-align:right;"'
        + ' onchange="var v=parseFloat(this.value)/100; if(isFinite(v)){' + onchangeTemplate.replace('$V', 'v') + '}">';
    html += '<input type="range" min="0" max="100" value="' + pct + '"'
        + ' style="flex:1;margin-left:4px;"' + onInputSync + '>';
    html += '</div>';
    return html;
}
```

- [ ] **Step 3: Create window handler functions**

```javascript
window._srvSetFaceColor = function(modelId, equipmentId, faceId, color) {
    pushUndoCheckpoint();
    var model = findModel(modelId);
    if (!model) return;
    var eq = model.rack.equipments.find(function(e) { return e.id === equipmentId; });
    if (!eq || !eq.faces || !eq.faces[faceId]) return;
    eq.faces[faceId].color = color;
    if (isRackDetailMode()) doRebuildRackDetail(); else doRebuildGeometry();
    logOperation('modify', 'Set server face color: ' + faceId + ' → ' + color);
};

window._srvAddPatch = function(modelId, equipmentId, faceId) {
    pushUndoCheckpoint();
    var model = findModel(modelId);
    if (!model) return;
    var eq = model.rack.equipments.find(function(e) { return e.id === equipmentId; });
    if (!eq) return;
    if (!eq.faces) eq.faces = getDefaultEquipmentFaces(eq);
    if (!eq.faces[faceId]) eq.faces[faceId] = { patches: [] };
    var patch = {
        id: generateId(), type: 'rect',
        x: 0.2, y: 0.1, w: 0.6, h: 0.8,
        color: '#00cc66', applied: true
    };
    eq.faces[faceId].patches.push(patch);
    state.selection = { type: 'serverPatch', modelId: modelId, equipmentId: equipmentId,
                        faceId: faceId, patchId: patch.id };
    if (isRackDetailMode()) doRebuildRackDetail(); else doRebuildGeometry();
    updateUI();
    logOperation('create', 'Added patch to server face: ' + faceId);
};

window._srvDeletePatch = function(modelId, equipmentId, faceId, patchId) {
    pushUndoCheckpoint();
    var model = findModel(modelId);
    if (!model) return;
    var eq = model.rack.equipments.find(function(e) { return e.id === equipmentId; });
    if (!eq || !eq.faces || !eq.faces[faceId]) return;
    eq.faces[faceId].patches = eq.faces[faceId].patches.filter(function(p) {
        return p.id !== patchId;
    });
    state.selection = { type: 'serverFace', modelId: modelId, equipmentId: equipmentId,
                        faceId: faceId };
    if (isRackDetailMode()) doRebuildRackDetail(); else doRebuildGeometry();
    updateUI();
    logOperation('delete', 'Deleted patch from server face: ' + faceId);
};

var _srvPatchSliderCheckpointed = false;
window._srvSetPatchProp = function(modelId, equipmentId, faceId, patchId, key, value) {
    if (!_srvPatchSliderCheckpointed) {
        pushUndoCheckpoint();
        _srvPatchSliderCheckpointed = true;
        setTimeout(function() { _srvPatchSliderCheckpointed = false; }, 500);
    }
    var model = findModel(modelId);
    if (!model) return;
    var eq = model.rack.equipments.find(function(e) { return e.id === equipmentId; });
    if (!eq || !eq.faces || !eq.faces[faceId]) return;
    var patch = eq.faces[faceId].patches.find(function(p) { return p.id === patchId; });
    if (!patch) return;

    if (key === 'type' && value !== patch.type) {
        // Convert between rect and circle
        if (value === 'circle') {
            patch.type = 'circle';
            patch.cx = (patch.x || 0) + (patch.w || 0.5) / 2;
            patch.cy = (patch.y || 0) + (patch.h || 0.5) / 2;
            patch.r = Math.min(patch.w || 0.5, patch.h || 0.5) / 2;
            delete patch.x; delete patch.y; delete patch.w; delete patch.h;
        } else {
            patch.type = 'rect';
            patch.x = (patch.cx || 0.5) - (patch.r || 0.3);
            patch.y = (patch.cy || 0.5) - (patch.r || 0.3);
            patch.w = (patch.r || 0.3) * 2;
            patch.h = (patch.r || 0.3) * 2;
            delete patch.cx; delete patch.cy; delete patch.r;
        }
    } else {
        patch[key] = value;
    }

    if (isRackDetailMode()) doRebuildRackDetail(); else doRebuildGeometry();
};
```

- [ ] **Step 4: Wire inspector to selection types**

Find the function that decides which inspector to render based on `state.selection.type` (search for `renderRackEquipmentInspector` call). Add:

```javascript
if (sel.type === 'serverFace' || sel.type === 'serverPatch') {
    var srvModel = findModel(sel.modelId);
    if (srvModel && srvModel.rack) {
        var srvEq = srvModel.rack.equipments.find(function(e) { return e.id === sel.equipmentId; });
        if (srvEq) {
            renderServerFaceInspector(prop, srvModel, srvEq, sel.faceId);
            return;
        }
    }
}
```

- [ ] **Step 5: Verify full UI flow**

1. Open rack detail view
2. Click a server face in tree → right panel shows face inspector with color + patch controls
3. Click "+ 添加 Patch" → green rect patch appears on face, patch editor shows sliders
4. Drag sliders → patch moves/resizes in real-time
5. Change type to circle → patch converts
6. Click ✕ → patch removed
7. Undo → patch restored

- [ ] **Step 6: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(server): face inspector UI with patch add/edit/delete"
```

---

### Task 7: STL Export + CFD Export

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `buildCfdExportJson()` (lines ~25037–25236)

**Context:** STL export already works because `exportBoxModel()` groups by `userData.exportName`, and our new server meshes have correct `exportName` values from `getServerFaceExportName()`. The only change needed is in `buildCfdExportJson()` to emit the new `faces{}` dict. We also need to remove the P5 `outletPatch` / `outletWallSolidName` export code.

- [ ] **Step 1: Create `_buildServerFacesExport()` helper**

Add near `buildCfdExportJson()`:

```javascript
function _buildServerFacesExport(model, equipment) {
    var result = {};
    var faceIds = ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'];

    faceIds.forEach(function(faceId) {
        var faceDef = (equipment.faces && equipment.faces[faceId]) || { patches: [] };
        var entry = { solid: null, patches: [], wallSolid: null };

        if (faceDef.patches && faceDef.patches.length > 0) {
            // Face is subdivided
            entry.solid = null;
            faceDef.patches.forEach(function(patch, pIdx) {
                entry.patches.push({
                    solid: getServerFaceExportName(model, equipment, faceId,
                        { isPatch: true, patchIndex: pIdx }),
                    patchId: patch.id
                });
            });
            entry.wallSolid = getServerFaceExportName(model, equipment, faceId, { isWall: true });
        } else {
            // Whole face
            entry.solid = getServerFaceExportName(model, equipment, faceId);
        }

        result[faceId] = entry;
    });

    return result;
}
```

- [ ] **Step 2: Modify fan-server export in `buildCfdExportJson()`**

Find the fan-server export code (around lines 25146–25171). Replace the `outletPatch` / `outletWallSolidName` logic with the new `faces` dict:

```javascript
if (eq.kind === 'fanServer') {
    var facesExport = _buildServerFacesExport(model, eq);

    var fanSrvObj = {
        id: exportStem,
        type: 'fanServer',
        startSlot: eq.startUnit,
        spanSlots: eq.spanUnits,
        // Legacy fields (backward compat): whole-face solid or null if subdivided
        inletSolidName:  facesExport.inlet.solid,
        outletSolidName: facesExport.outlet.solid,
        // New faces dict
        faces: facesExport,
        physics: {
            volumeFlowRate: srvCfd.volumeFlowRate || 0.5,
            power: srvCfd.power || 1000,
            fixedTemperature: null,
            fixedTempFace: null
        },
        extraInletOffset: eq.extraInletOffset || 0,
        extraOutletOffset: eq.extraOutletOffset || 0
    };
    if (eq.liquidCooling && eq.liquidCooling.enabled) {
        fanSrvObj.liquidCooling = JSON.parse(JSON.stringify(eq.liquidCooling));
    }
    return fanSrvObj;
}
```

Note: `outletPatch` and `outletWallSolidName` fields are removed. The backend reads `faces` dict instead.

- [ ] **Step 3: Modify non-fan export similarly**

Find the non-fan export code (around lines 25172–25206). Add the `faces` dict:

```javascript
else {
    var facesExport = _buildServerFacesExport(model, eq);

    var nonFanObj = {
        id: exportStem,
        type: 'nonFan',
        startSlot: eq.startUnit,
        spanSlots: eq.spanUnits,
        // Legacy fields
        inletSolidName:  facesExport.inlet.solid,
        outletSolidName: facesExport.outlet.solid,
        topSolidName:    facesExport.top.solid,
        bottomSolidName: facesExport.bottom.solid,
        // New faces dict
        faces: facesExport,
        physics: {
            inlet: inletPhys,
            outlet: outletPhys
        }
    };
    if (eq.liquidCooling && eq.liquidCooling.enabled) {
        nonFanObj.liquidCooling = JSON.parse(JSON.stringify(eq.liquidCooling));
    }
    return nonFanObj;
}
```

- [ ] **Step 4: Verify export**

In browser console:
```javascript
var json = buildCfdExportJson();
var rack = Object.values(json.racks)[0];
console.log(JSON.stringify(rack.servers[0].faces, null, 2));
```

Verify: `faces` dict has 6 entries, each with `solid` (string when no patches, null when subdivided) and `patches[]` array.

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(server): CFD export with faces{} dict + backward-compat legacy fields"
```

---

### Task 8: Backend BC Registration

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` — `_build_bridge_bc()` (lines ~1906–1964), `_generate_snappy_hex_mesh_dict()` (lines ~2298–2323)

**Context:** The backend needs to read the new `faces` dict from cfd_export.json. When present, iterate faces and register BCs per-patch or per-whole-face. When absent (old format), fall back to existing `inletSolidName` logic. Also update the solid collection for snappyHexMesh to include patch/wall solids.

- [ ] **Step 1: Add `_resolve_server_face_bc()` helper**

Insert near `_build_bridge_bc()` (around line 1900):

```python
def _resolve_server_face_bc(
    self, srv: dict, face_id: str, registry, *, Q: float, P: float
) -> None:
    """Register BCs for one server face (possibly subdivided into patches)."""
    faces = srv.get("faces", {})
    face_info = faces.get(face_id, {})
    srv_type = srv.get("type", "fanServer")

    # Determine primary BC for this face
    if srv_type == "fanServer":
        if face_id == "inlet":
            bc = BCSpec(bc_type="fan_inlet", flow_rate=Q, power=P)
        elif face_id == "outlet":
            T_out = 25.0 + P / (1.2 * 1006 * Q) if Q > 0 else 35.0
            bc = BCSpec(bc_type="fan_outlet", flow_rate=Q, power=P, temperature=T_out)
        else:
            bc = BCSpec(bc_type="wall")
    else:
        phys = srv.get("physics", {})
        if face_id in ("inlet", "outlet"):
            face_phys = phys.get(face_id, {})
            bc_type = face_phys.get("bcType", "wall")
            if bc_type == "nonfan_fixed_temp":
                T_val = face_phys.get("temperature", 35.0) or 35.0
                bc = BCSpec(bc_type="nonfan_fixed_temp", temperature=T_val)
            elif bc_type == "nonfan_heat_flux":
                power_val = face_phys.get("power", 500) or 500
                span = srv.get("spanSlots", 1)
                eq_height = span * 0.04445
                rack_w = self._get_rack_width(rack) if hasattr(self, '_get_rack_width') else 0.6
                area = eq_height * rack_w
                hf = power_val / max(area, 1e-6)
                bc = BCSpec(bc_type="nonfan_heat_flux", heat_flux=hf)
            else:
                bc = BCSpec(bc_type="wall")
        else:
            bc = BCSpec(bc_type="wall")

    # Register based on face subdivision
    patches = face_info.get("patches", [])
    if patches:
        for p in patches:
            solid = p.get("solid")
            if solid:
                registry.register(_sanitize(solid), bc)
        wall_solid = face_info.get("wallSolid")
        if wall_solid:
            registry.register(_sanitize(wall_solid), BCSpec(bc_type="wall"))
    else:
        solid = face_info.get("solid")
        if solid:
            registry.register(_sanitize(solid), bc)
```

- [ ] **Step 2: Rewrite fan-server + non-fan BC registration in `_build_bridge_bc()`**

Replace the fan-server section (lines ~1906–1917) and non-fan section (lines ~1918–1964) with:

```python
faces = srv.get("faces")
if faces:
    # New format: iterate all faces
    for face_id in ("inlet", "outlet", "top", "bottom", "left", "right"):
        if face_id not in faces:
            continue
        self._resolve_server_face_bc(srv, face_id, registry, Q=Q, P=P)
    logger.info(f"  {server_id}: registered via faces dict ({len(faces)} faces)")
else:
    # Legacy fallback
    if srv_type == "fanServer":
        inlet = _sanitize(srv["inletSolidName"])
        outlet = _sanitize(srv["outletSolidName"])
        T_outlet = 25.0 + P / (1.2 * 1006 * Q) if Q > 0 else 35.0
        registry.register(inlet, BCSpec(bc_type="fan_inlet", flow_rate=Q, power=P))
        registry.register(outlet, BCSpec(bc_type="fan_outlet", flow_rate=Q, power=P,
                                         temperature=T_outlet))
        outlet_wall_name = srv.get("outletWallSolidName")
        if outlet_wall_name and srv.get("outletPatch"):
            registry.register(_sanitize(outlet_wall_name), BCSpec(bc_type="wall"))
    else:
        # Keep existing non-fan legacy code
        phys_inlet = phys.get("inlet", {})
        phys_outlet = phys.get("outlet", {})
        if not phys_inlet and not phys_outlet:
            T = phys.get("fixedTemperature", 35.0) or 35.0
            for face_key in ("inletSolidName", "outletSolidName"):
                solid = srv.get(face_key)
                if solid:
                    registry.register(_sanitize(solid),
                                      BCSpec(bc_type="nonfan_fixed_temp", temperature=T))
            for face_key in ("topSolidName", "bottomSolidName"):
                solid = srv.get(face_key)
                if solid:
                    registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
        else:
            def _register_nonfan_face(face_key, face_phys):
                solid_name = srv.get(face_key)
                if not solid_name:
                    return
                bc_type_val = face_phys.get("bcType", "wall")
                if bc_type_val == "nonfan_fixed_temp":
                    T_val = face_phys.get("temperature", 35.0) or 35.0
                    registry.register(_sanitize(solid_name),
                                      BCSpec(bc_type="nonfan_fixed_temp", temperature=T_val))
                elif bc_type_val == "nonfan_heat_flux":
                    power_val = face_phys.get("power", 500) or 500
                    span = srv.get("spanSlots", 1)
                    eq_height = span * 0.04445
                    area = eq_height * 0.6
                    hf = power_val / max(area, 1e-6)
                    registry.register(_sanitize(solid_name),
                                      BCSpec(bc_type="nonfan_heat_flux", heat_flux=hf))
                else:
                    registry.register(_sanitize(solid_name), BCSpec(bc_type="wall"))
            _register_nonfan_face("inletSolidName", phys_inlet)
            _register_nonfan_face("outletSolidName", phys_outlet)
            for face_key in ("topSolidName", "bottomSolidName"):
                solid = srv.get(face_key)
                if solid:
                    registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
```

- [ ] **Step 3: Update solid collection in `_generate_snappy_hex_mesh_dict()`**

In the solid collection loop (lines ~2298–2323), add faces-dict support:

```python
for rack in data.get("racks", {}).values():
    for srv in rack.get("servers", []):
        span = srv.get("spanSlots", 1)
        faces = srv.get("faces")

        if faces:
            # New format: collect all face/patch/wall solids
            for face_id, face_info in faces.items():
                patches = face_info.get("patches", [])
                if patches:
                    for p in patches:
                        s = p.get("solid")
                        if s:
                            san = _sanitize(s)
                            if face_id in ("inlet", "outlet"):
                                (server_inlet_solids if face_id == "inlet"
                                 else server_outlet_solids).add(san)
                                solid_to_span[san] = span
                            else:
                                nonfan_solids.add(san)
                    wall_s = face_info.get("wallSolid")
                    if wall_s:
                        nonfan_solids.add(_sanitize(wall_s))
                else:
                    s = face_info.get("solid")
                    if s:
                        san = _sanitize(s)
                        if srv.get("type") == "fanServer":
                            if face_id in ("inlet", "outlet"):
                                (server_inlet_solids if face_id == "inlet"
                                 else server_outlet_solids).add(san)
                                solid_to_span[san] = span
                            else:
                                nonfan_solids.add(san)
                        else:
                            nonfan_solids.add(san)
        else:
            # Legacy format (existing code)
            if srv.get("type") == "fanServer":
                server_inlet_solids.add(_sanitize(srv["inletSolidName"]))
                server_outlet_solids.add(_sanitize(srv["outletSolidName"]))
                solid_to_span[_sanitize(srv["inletSolidName"])] = span
                solid_to_span[_sanitize(srv["outletSolidName"])] = span
            else:
                for k in ("inletSolidName", "outletSolidName", "topSolidName", "bottomSolidName"):
                    if srv.get(k):
                        nonfan_solids.add(_sanitize(srv[k]))
```

- [ ] **Step 4: Verify backend processes new format**

Run a test with the canonical case:

```bash
conda run -n dcdtuni python -c "
from dcdtuni.runner.stlgen_runner import STLGenRunner
import json
# Load a test cfdrun
runner = STLGenRunner('/path/to/case')
runner._load()
runner._build_bridge_bc()
print('Registry entries:', len(runner._bridge_registry._registry))
"
```

Verify no errors and correct number of registry entries.

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(server): backend reads faces{} dict for server BC registration + legacy fallback"
```

---

## Self-Review Checklist

### Spec Coverage

| Spec Section | Plan Task |
|-------------|-----------|
| §1 State & Data Model | Task 1 |
| §2 Geometry Builder | Tasks 3, 4 |
| §3 Model Tree | Task 5 |
| §4 Inspector UI | Task 6 |
| §5 STL Export | Tasks 2, 4 (export names in userData) |
| §6 CFD Export | Task 7 |
| §7 Backend BC | Task 8 |
| §8 File Changes | All tasks |
| §9 Test Plan | Verification steps in each task |
| P5 outletPatch migration | Task 1 (state), Task 7 (export removal) |
| Backward compat | Task 1 (cloneRackState), Task 8 (legacy fallback) |

### Placeholder Scan
- No TBD/TODO markers
- All code blocks contain complete implementations
- All function signatures are consistent across tasks

### Type Consistency
- `equipment.faces` — used consistently as `{ inlet: { patches: [], color? }, ... }`
- `getServerFaceExportName(model, equipment, faceId, opts)` — consistent signature in Tasks 2, 4, 7
- `baseUserData.faceId` — set in Task 3, read in Tasks 5, 6
- `_srvSetPatchProp` / `_srvAddPatch` / `_srvDeletePatch` — consistent parameter order
- `facesExport` structure: `{ solid, patches: [{solid, patchId}], wallSolid }` — consistent between Task 7 frontend and Task 8 backend
