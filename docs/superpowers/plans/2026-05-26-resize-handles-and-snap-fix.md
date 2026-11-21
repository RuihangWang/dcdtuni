# Resize Handles & Snap System Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace invisible cursor-threshold edge resizing with explicit green cube handles + yellow outlines, and fix/extend the snap system for same-kind patch-to-patch and broader rack-level server patch snapping.

**Architecture:** All changes are in `STL_generate.html`. New overlay group in scene for handles. Replace edge-helper lines with yellow outline + green cubes. Simplify hover/drag detection to use raycast on cube meshes instead of proximity thresholds. Extend snap loops to include same-kind patches and cross-face server patches.

**Tech Stack:** Three.js r184 (IIFE bundle), vanilla JS within STL_generate.html

---

### Task 1: Create the resize handle overlay group and handle factory

**Files:**
- Modify: `src/frontend/web/STL_generate.html:2202-2220` (after `renderHelpers` declaration)
- Modify: `src/frontend/web/STL_generate.html:5294-5309` (`clearRender()`)
- Modify: `src/frontend/web/STL_generate.html:16589-16618` (`_applyProjectData`)

This task creates the overlay group that lives in `scene` (not `globalGroup`) and the factory functions for creating/destroying handles. No visual effect yet — that comes in Task 2.

- [ ] **Step 1: Add overlay group and handle state variables**

After `renderHelpers` declaration (line 2204), add:

```javascript
// ========== Resize Handle Overlay ==========
let _resizeHandleGroup = null;  // THREE.Group added to scene, not globalGroup
let _activeResizeHandles = null; // { outline, cubes: {top,bottom,left,right}, modelId, patchId, faceId }

function _ensureResizeHandleGroup() {
    if (_resizeHandleGroup) return _resizeHandleGroup;
    _resizeHandleGroup = new THREE.Group();
    _resizeHandleGroup.name = '_resizeHandles';
    _resizeHandleGroup.renderOrder = 10;
    scene.add(_resizeHandleGroup);
    return _resizeHandleGroup;
}
```

- [ ] **Step 2: Add the dispose function**

Directly after the code from Step 1, add:

```javascript
function _disposeResizeHandles() {
    if (!_activeResizeHandles) return;
    const grp = _activeResizeHandles._group;
    if (grp) {
        grp.traverse(child => {
            if (child.geometry) child.geometry.dispose();
            if (child.material) child.material.dispose();
        });
        if (grp.parent) grp.parent.remove(grp);
    }
    _activeResizeHandles = null;
}
```

- [ ] **Step 3: Add the create function**

Directly after the code from Step 2, add:

```javascript
function _createResizeHandles(corners, modelId, faceId, patchId) {
    // corners: [bottomLeft, bottomRight, topRight, topLeft] in world space
    _disposeResizeHandles();
    const grp = new THREE.Group();
    grp.name = '_resizeHandleInstance';
    _ensureResizeHandleGroup().add(grp);

    // Yellow outline
    const outlineGeo = new THREE.BufferGeometry().setFromPoints([
        corners[0], corners[1], corners[2], corners[3], corners[0]
    ]);
    const outlineMat = new THREE.LineBasicMaterial({
        color: 0xffdd00, depthTest: false, transparent: true, opacity: 1
    });
    const outline = new THREE.Line(outlineGeo, outlineMat);
    outline.renderOrder = 10;
    grp.add(outline);

    // Green cubes at edge centers
    const edgeCenters = {
        bottom: new THREE.Vector3().addVectors(corners[0], corners[1]).multiplyScalar(0.5),
        right:  new THREE.Vector3().addVectors(corners[1], corners[2]).multiplyScalar(0.5),
        top:    new THREE.Vector3().addVectors(corners[2], corners[3]).multiplyScalar(0.5),
        left:   new THREE.Vector3().addVectors(corners[3], corners[0]).multiplyScalar(0.5)
    };
    // Cube size: 3% of shorter edge, clamped [0.01, 0.05]
    const edgeLen1 = corners[0].distanceTo(corners[1]);
    const edgeLen2 = corners[1].distanceTo(corners[2]);
    const cubeSize = Math.max(0.01, Math.min(0.05, Math.min(edgeLen1, edgeLen2) * 0.03));
    const cubeGeo = new THREE.BoxGeometry(cubeSize, cubeSize, cubeSize);
    const cubeMat = new THREE.MeshBasicMaterial({
        color: 0x00cc44, depthTest: false, transparent: true, opacity: 1
    });

    const cubes = {};
    ['top', 'bottom', 'left', 'right'].forEach(edgeKey => {
        const mesh = new THREE.Mesh(cubeGeo, cubeMat.clone());
        mesh.position.copy(edgeCenters[edgeKey]);
        mesh.renderOrder = 11;
        mesh.userData = {
            isResizeHandle: true,
            edgeKey: edgeKey,
            modelId: modelId,
            faceId: faceId,
            patchId: patchId || null
        };
        grp.add(mesh);
        cubes[edgeKey] = mesh;
    });

    _activeResizeHandles = {
        _group: grp,
        outline: outline,
        cubes: cubes,
        modelId: modelId,
        faceId: faceId,
        patchId: patchId || null
    };
}
```

- [ ] **Step 4: Add cleanup hooks**

In `clearRender()` (line 5294), add `_disposeResizeHandles();` as the first line inside the function, before `clearStlPreviewNormalHelper()`.

In `_applyProjectData` (line 16589), add `_disposeResizeHandles();` after the line `if (typeof window.ppClearAll === 'function') window.ppClearAll();` (line 16595).

- [ ] **Step 5: Add tab visibility toggle**

In `_applyLeftTabUI()` (around line 21519, near the `_sensorSpheresGroup` visibility line), add:

```javascript
if (_resizeHandleGroup) _resizeHandleGroup.visible = isGeo;
```

- [ ] **Step 6: Verify no errors on page load**

Start the server and open the page in browser-use:
```bash
export DISPLAY=:50 && export PATH="$HOME/.local/bin:$PATH"
browser-use eval "window.location.href='http://localhost:8005/STL_generate.html'"
# Wait for load
browser-use eval "typeof _disposeResizeHandles === 'function' && typeof _createResizeHandles === 'function' && typeof _ensureResizeHandleGroup === 'function' ? 'OK' : 'MISSING'"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): add resize handle overlay infrastructure"
```

---

### Task 2: Render yellow outline + green cubes on selection

**Files:**
- Modify: `src/frontend/web/STL_generate.html:6622-6840` (`updateHighlights()`)

This task wires handle creation into the selection system. When a qualifying quadrilateral is selected, compute its world-space corners and call `_createResizeHandles`.

- [ ] **Step 1: Add corner computation helper**

Before `updateHighlights()` (line 6622), add:

```javascript
function _computeQuadCornersWorld(model, faceId, patch, isServerPatch, equipment) {
    const modelRoot = getRenderRootByModelId(model.id);
    if (!modelRoot) return null;
    modelRoot.updateMatrixWorld(true);

    let x0, y0, w, h, layout;

    if (isServerPatch && equipment) {
        // Server patch: normalized 0-1 coords mapped to equipment face geometry
        const box = getRackServerBox(model, equipment);
        if (!box) return null;
        const fr = getServerFaceRect(box, faceId);
        if (!fr || fr.w < 1e-6 || fr.h < 1e-6) return null;
        layout = fr;
        x0 = (patch.x || 0) * fr.w;
        y0 = (patch.y || 0) * fr.h;
        w = (patch.w || 0.5) * fr.w;
        h = (patch.h || 0.5) * fr.h;
    } else if (patch) {
        // Box/baffle/floor/wall/ACU patch
        layout = getFaceLayouts(model)[faceId];
        if (!layout) return null;
        if (patch.type === 'circle') return null; // circles don't get rect handles
        x0 = patch.x || 0;
        y0 = patch.y || 0;
        w = patch.w || 0;
        h = patch.h || 0;
    } else {
        // Model-level face (wall/baffle/floor selected face)
        layout = getFaceLayouts(model)[faceId];
        if (!layout) return null;
        x0 = 0; y0 = 0;
        w = layout.w; h = layout.h;
    }

    if (w < 1e-6 || h < 1e-6) return null;

    // Compute 4 corners in model-local space, then transform to world
    const bl = layout.origin.clone().addScaledVector(layout.right, x0).addScaledVector(layout.up, y0);
    const br = layout.origin.clone().addScaledVector(layout.right, x0 + w).addScaledVector(layout.up, y0);
    const tr = layout.origin.clone().addScaledVector(layout.right, x0 + w).addScaledVector(layout.up, y0 + h);
    const tl = layout.origin.clone().addScaledVector(layout.right, x0).addScaledVector(layout.up, y0 + h);

    return [
        modelRoot.localToWorld(bl),
        modelRoot.localToWorld(br),
        modelRoot.localToWorld(tr),
        modelRoot.localToWorld(tl)
    ];
}
```

- [ ] **Step 2: Add handle creation call at end of updateHighlights**

At the **end** of `updateHighlights()`, after the `renderHelpers.forEach(...)` block (around line 6838, just before the function's closing brace), add:

```javascript
    // --- Resize handles: yellow outline + green cubes ---
    _disposeResizeHandles();
    const _rhModel = sel.modelId ? getModelById(sel.modelId) : null;
    if (_rhModel) {
        const _rhKind = _rhModel.kind;
        if (sel.type === 'serverPatch' && sel.patchId) {
            const ctx = getPatchContextById(_rhModel, sel.patchId);
            if (ctx && ctx.isServerPatch && ctx.patch && ctx.patch.type === 'rect') {
                const corners = _computeQuadCornersWorld(_rhModel, ctx.faceId, ctx.patch, true, ctx.equipment);
                if (corners) _createResizeHandles(corners, _rhModel.id, ctx.faceId, sel.patchId);
            }
        } else if (sel.type === 'patch' && sel.patchId) {
            const ctx = getPatchContextById(_rhModel, sel.patchId);
            if (ctx && ctx.patch && ctx.patch.type === 'rect') {
                const corners = _computeQuadCornersWorld(_rhModel, ctx.faceId, ctx.patch, false, null);
                if (corners) _createResizeHandles(corners, _rhModel.id, ctx.faceId, sel.patchId);
            }
        } else if (sel.type === 'face' && sel.faceId
            && (_rhKind === 'wall' || _rhKind === 'baffle' || _rhKind === 'floor'
                || _rhKind === 'box' || _rhKind === 'acu' || _rhKind === 'partitionWall')) {
            const corners = _computeQuadCornersWorld(_rhModel, sel.faceId, null, false, null);
            if (corners) _createResizeHandles(corners, _rhModel.id, sel.faceId, null);
        }
    }
```

- [ ] **Step 3: Test with browser-use**

```bash
export DISPLAY=:50 && export PATH="$HOME/.local/bin:$PATH"
# Login and load a project
browser-use eval "fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'WIN#@desk'})}).then(r=>r.json())"
browser-use eval "window.t=window.I18n&&window.I18n.t?window.I18n.t.bind(window.I18n):function(k){return k}; var xhr=new XMLHttpRequest(); xhr.open('GET','/projects/STDCT.cfdproj',false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText)); 'loaded:'+state.models.length"
# Select a wall model
browser-use eval "var wall=state.models.find(m=>m.kind==='wall'); if(wall){setSelection('face',wall.id,'front');'selected wall:'+wall.id}else{'no wall'}"
# Check handles exist
browser-use eval "_activeResizeHandles ? 'handles: cubes='+Object.keys(_activeResizeHandles.cubes).join(',')+' outline='+!!_activeResizeHandles.outline : 'NO HANDLES'"
```
Expected: `handles: cubes=top,bottom,left,right outline=true`

Take screenshot to verify visually:
```bash
browser-use screenshot /tmp/resize_handles_test.png
```

- [ ] **Step 4: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): render yellow outline + green cubes on quad selection"
```

---

### Task 3: Wire green cubes into drag interaction

**Files:**
- Modify: `src/frontend/web/STL_generate.html:18592-18713` (`updatePatchEdgeHover()`)
- Modify: `src/frontend/web/STL_generate.html:17664-17850` (`beginPatchDrag()`)
- Modify: `src/frontend/web/STL_generate.html:18540-18583` (`endPatchDrag()` — cube color reset)

This task makes the green cubes interactive: hover shows `grab` cursor, click initiates drag with the correct mode, and the cube highlights during drag.

- [ ] **Step 1: Add resize handle raycast detection to updatePatchEdgeHover**

At the start of `updatePatchEdgeHover()` (line 18592), after the early return guards (lines 18593-18595), insert a raycast check for green cubes **before** the existing selected-model raycast:

```javascript
    // Check resize handle cubes first (they live in _resizeHandleGroup, not renderMeshes)
    if (_resizeHandleGroup && _activeResizeHandles) {
        updatePointerFromEvent(event);
        const handleMeshes = [];
        Object.values(_activeResizeHandles.cubes).forEach(c => handleMeshes.push(c));
        const handleHits = raycaster.intersectObjects(handleMeshes, false);
        if (handleHits.length > 0) {
            const hitCube = handleHits[0].object;
            const edgeKey = hitCube.userData.edgeKey;
            const nextHover = {
                modelId: hitCube.userData.modelId,
                patchId: hitCube.userData.patchId,
                edgeKey: edgeKey,
                faceId: hitCube.userData.faceId,
                isResizeHandle: true
            };
            const changed = !hoverPatchEdge
                || hoverPatchEdge.edgeKey !== edgeKey
                || !hoverPatchEdge.isResizeHandle;
            hoverPatchEdge = nextHover;
            setViewerCursor('grab');
            if (changed) updateHighlights();
            return;
        }
    }
```

- [ ] **Step 2: Add resize handle drag initiation to beginPatchDrag**

In `beginPatchDrag()` (line 17664), after `updatePointerFromEvent(event)` (line 17666) and **before** the `const hits = raycaster.intersectObjects(renderMeshes, false)` line (line 17667), insert:

```javascript
    // Check if clicking a resize handle cube
    if (_resizeHandleGroup && _activeResizeHandles) {
        const handleMeshes = Object.values(_activeResizeHandles.cubes);
        const handleHits = raycaster.intersectObjects(handleMeshes, false);
        if (handleHits.length > 0) {
            const hitCube = handleHits[0].object;
            const edgeKey = hitCube.userData.edgeKey;
            const handleModelId = hitCube.userData.modelId;
            const handleFaceId = hitCube.userData.faceId;
            const handlePatchId = hitCube.userData.patchId;
            const model = getModelById(handleModelId);
            if (!model) return false;
            const modelRoot = getRenderRootByModelId(model.id);
            if (!modelRoot) return false;

            // Highlight the grabbed cube
            hitCube.material.color.setHex(0xffee00);
            hitCube.userData._wasGrabbed = true;

            if (handlePatchId) {
                // Patch-level resize (box patch or server patch)
                const context = getPatchContextById(model, handlePatchId);
                if (!context) return false;
                const isServerPatch = !!context.isServerPatch;
                let fr, planePoint;
                if (isServerPatch) {
                    const box = getRackServerBox(model, context.equipment);
                    if (!box) return false;
                    fr = getServerFaceRect(box, context.faceId);
                    if (!fr || fr.w < 1e-6 || fr.h < 1e-6) return false;
                    planePoint = fr.origin.clone();
                } else {
                    fr = getFaceLayouts(model)[context.faceId];
                    planePoint = fr.origin.clone();
                }
                const normal = new THREE.Vector3().crossVectors(fr.right, fr.up).normalize()
                    .transformDirection(modelRoot.matrixWorld);
                const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(
                    normal, modelRoot.localToWorld(planePoint)
                );
                const worldPoint = new THREE.Vector3();
                if (!raycaster.ray.intersectPlane(plane, worldPoint)) return false;
                const facePoint = isServerPatch
                    ? getPointOnServerFace(model, context.equipment, context.faceId, worldPoint)
                    : getPointOnBoxFace(model, context.faceId, worldPoint);
                if (!facePoint) return false;

                pushUndoCheckpoint('patchDrag');
                dragState = {
                    mode: 'boxRectResize',
                    pointerId: event.pointerId,
                    modelId: model.id,
                    patchId: context.patch.id,
                    faceId: context.faceId,
                    plane: plane,
                    pointerDown2D: { x: facePoint.x, y: facePoint.y },
                    resizeEdge: edgeKey,
                    isServerPatch: isServerPatch,
                    equipmentId: isServerPatch ? context.equipment.id : null,
                    patchStart: { x: context.patch.x, y: context.patch.y, w: context.patch.w, h: context.patch.h }
                };
            } else {
                // Model-level face resize (wall/baffle/floor/box/ACU)
                const faceId = handleFaceId || (model.kind === 'floor' ? 'top' : 'front');
                const layout = getFaceLayouts(model)[faceId];
                const normal = new THREE.Vector3().crossVectors(layout.right, layout.up).normalize();
                const worldNormal = normal.transformDirection(modelRoot.matrixWorld);
                const worldOrigin = modelRoot.localToWorld(layout.origin.clone());
                const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(worldNormal, worldOrigin);
                const worldPoint = new THREE.Vector3();
                if (!raycaster.ray.intersectPlane(plane, worldPoint)) return false;
                const _DIM_DIR = { W: new THREE.Vector3(1,0,0), H: new THREE.Vector3(0,1,0), D: new THREE.Vector3(0,0,1) };
                const wType0 = FACE_CONFIGS[faceId].wType, hType0 = FACE_CONFIGS[faceId].hType;
                const worldRight = _DIM_DIR[wType0].clone().transformDirection(modelRoot.matrixWorld);
                const worldUp = _DIM_DIR[hType0].clone().transformDirection(modelRoot.matrixWorld);

                pushUndoCheckpoint('wallEdgeResize');
                const initDragDir = (edgeKey === 'right' || edgeKey === 'left') ? worldRight : worldUp;
                const initDimKey = (edgeKey === 'right' || edgeKey === 'left') ? wType0 : hType0;
                const initAnchorProj = new THREE.Vector3(model.anchor.x, model.anchor.y, model.anchor.z).dot(initDragDir);
                const initEdgeProj = (edgeKey === 'right' || edgeKey === 'top')
                    ? initAnchorProj + model.base[initDimKey]
                    : initAnchorProj;
                dragState = {
                    mode: 'wallResize',
                    pointerId: event.pointerId,
                    modelId: model.id,
                    faceId: faceId,
                    plane: plane,
                    pointerDown: worldPoint.clone(),
                    resizeEdge: edgeKey,
                    worldRight: worldRight.clone(),
                    worldUp: worldUp.clone(),
                    baseStart: { W: model.base.W, H: model.base.H, D: model.base.D },
                    anchorStart: { x: model.anchor.x, y: model.anchor.y, z: model.anchor.z },
                    wType: wType0,
                    hType: hType0,
                    initEdgeProj: initEdgeProj
                };
                initSnapCache();
            }
            hoverPatchEdge = { modelId: model.id, patchId: handlePatchId, edgeKey: edgeKey, isResizeHandle: true, faceId: handleFaceId };
            if (controls) controls.enabled = false;
            setViewerCursor('grabbing');
            try { if (renderer) renderer.domElement.setPointerCapture(event.pointerId); } catch (e) {}
            return true;
        }
    }
```

- [ ] **Step 3: Reset cube color on drag end**

In `endPatchDrag()` (find it near line 18540 — search for `function endPatchDrag`), at the start of the function (after the early return `if (!dragState) return;`), add:

```javascript
    // Reset grabbed resize handle cube color
    if (_activeResizeHandles) {
        Object.values(_activeResizeHandles.cubes).forEach(cube => {
            if (cube.userData._wasGrabbed) {
                cube.material.color.setHex(0x00cc44);
                cube.userData._wasGrabbed = false;
            }
        });
    }
```

- [ ] **Step 4: Update handle positions during drag**

In `updatePatchDrag()` (line 17955), at the very end of the function (before the closing brace), add a call to recompute handle positions so the outline and cubes follow the resized geometry:

```javascript
    // Update resize handle positions to follow the resizing geometry
    if (_activeResizeHandles && dragState) {
        const _uhModel = getModelById(dragState.modelId);
        if (_uhModel) {
            const _uhPatchId = _activeResizeHandles.patchId;
            const _uhFaceId = _activeResizeHandles.faceId;
            let corners = null;
            if (_uhPatchId) {
                const ctx = getPatchContextById(_uhModel, _uhPatchId);
                if (ctx && ctx.patch && ctx.patch.type === 'rect') {
                    corners = _computeQuadCornersWorld(_uhModel, ctx.faceId, ctx.patch, !!ctx.isServerPatch, ctx.equipment);
                }
            } else {
                corners = _computeQuadCornersWorld(_uhModel, _uhFaceId, null, false, null);
            }
            if (corners) {
                // Update outline geometry
                const outlinePos = _activeResizeHandles.outline.geometry.attributes.position;
                for (let i = 0; i < 4; i++) {
                    outlinePos.setXYZ(i, corners[i].x, corners[i].y, corners[i].z);
                }
                outlinePos.setXYZ(4, corners[0].x, corners[0].y, corners[0].z);
                outlinePos.needsUpdate = true;
                // Update cube positions
                const edgeCenters = {
                    bottom: new THREE.Vector3().addVectors(corners[0], corners[1]).multiplyScalar(0.5),
                    right:  new THREE.Vector3().addVectors(corners[1], corners[2]).multiplyScalar(0.5),
                    top:    new THREE.Vector3().addVectors(corners[2], corners[3]).multiplyScalar(0.5),
                    left:   new THREE.Vector3().addVectors(corners[3], corners[0]).multiplyScalar(0.5)
                };
                Object.keys(_activeResizeHandles.cubes).forEach(key => {
                    _activeResizeHandles.cubes[key].position.copy(edgeCenters[key]);
                });
            }
        }
    }
```

- [ ] **Step 5: Test drag interaction with browser-use**

```bash
export DISPLAY=:50 && export PATH="$HOME/.local/bin:$PATH"
# With project already loaded, select a wall
browser-use eval "var wall=state.models.find(m=>m.kind==='wall'); setSelection('face',wall.id,'front'); 'selected:'+wall.id"
# Verify handles exist and have the right userData
browser-use eval "var cubes=_activeResizeHandles.cubes; Object.keys(cubes).map(k=>k+':isHandle='+cubes[k].userData.isResizeHandle+',edge='+cubes[k].userData.edgeKey).join(' | ')"
```
Expected: `top:isHandle=true,edge=top | bottom:isHandle=true,edge=bottom | left:isHandle=true,edge=left | right:isHandle=true,edge=right`

Take screenshot:
```bash
browser-use screenshot /tmp/resize_handles_drag_test.png
```

- [ ] **Step 6: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): wire green cube handles into drag interaction"
```

---

### Task 4: Remove old edge helper and threshold systems

**Files:**
- Modify: `src/frontend/web/STL_generate.html:7062-7116` (delete `spawnPatchEdgeHelper`, `createBoxPatchEdgeHelpers`)
- Modify: `src/frontend/web/STL_generate.html:7487-7537` (delete `createWallBaffleEdgeHelpers`)
- Modify: `src/frontend/web/STL_generate.html:6365-6394` (delete `getBoxPatchEdgeThreshold`, `getBoxPatchDragMode`)
- Modify: `src/frontend/web/STL_generate.html:7717-7747` (delete `getServerPatchDragMode`)
- Modify: `src/frontend/web/STL_generate.html:6792-6837` (remove `isPatchEdgeHelper` and `isWallEdgeHelper` blocks from `updateHighlights()`)
- Modify: calls in rendering functions that create edge helpers

This task removes the old invisible-threshold edge detection and edge helper lines, since they're now replaced by green cubes.

- [ ] **Step 1: Delete createBoxPatchEdgeHelpers and spawnPatchEdgeHelper**

Delete the functions `spawnPatchEdgeHelper` (lines 7062-7071) and `createBoxPatchEdgeHelpers` (lines 7073-7116) entirely.

- [ ] **Step 2: Remove all calls to createBoxPatchEdgeHelpers**

Search for `createBoxPatchEdgeHelpers` in the file and remove every call. These appear in:
- `renderPlanarModelWithPatches` (line 7191): `face.patches.forEach(patch => createBoxPatchEdgeHelpers(modelGroup, model, primaryFaceId, patch));` — delete this line.
- `_buildSingleBoxFace` (search for `createBoxPatchEdgeHelpers` in that function) — delete those calls.
- `_buildServerFaceWithPatches` (search for `createBoxPatchEdgeHelpers` in server rendering) — delete those calls.

- [ ] **Step 3: Delete createWallBaffleEdgeHelpers**

Delete the function `createWallBaffleEdgeHelpers` (lines 7487-7537) entirely.

- [ ] **Step 4: Remove all calls to createWallBaffleEdgeHelpers**

Search for `createWallBaffleEdgeHelpers` and remove every call:
- `renderBaffleModel` (line 7196): `createWallBaffleEdgeHelpers(modelGroup, model, faceLayouts);` — delete.
- `renderWallModel` (search near that function) — delete the call.
- `buildGeometryData` or `renderBoxModel` if called for ACU/box edge helpers — delete.

- [ ] **Step 5: Delete getBoxPatchEdgeThreshold and getBoxPatchDragMode**

Delete `getBoxPatchEdgeThreshold` (lines 6365-6368) and `getBoxPatchDragMode` (lines 6370-6394) entirely.

Delete `getServerPatchDragMode` (lines 7717-7747) entirely.

- [ ] **Step 6: Remove old edge helper highlight logic from updateHighlights**

In the `renderHelpers.forEach(...)` block of `updateHighlights()` (lines 6792-6837), delete the two blocks:
- The `if (data.isPatchEdgeHelper)` block (lines 6802-6819)
- The `if (data.isWallEdgeHelper)` block (lines 6821-6837)

- [ ] **Step 7: Simplify updatePatchEdgeHover — remove old threshold detection**

In `updatePatchEdgeHover()` (line 18592), after the new resize handle raycast check added in Task 3, **remove**:
- The `boxPatchHit` detection block (lines 18621-18659) that calls `getBoxPatchDragMode` / `getServerPatchDragMode`
- The `wallBaffleHit` detection block (lines 18662-18710) that does threshold-based edge detection

The function now only: (1) checks resize handle cubes (added in Task 3), (2) checks seed handles, (3) calls `clearHoveredPatchEdge()`.

- [ ] **Step 8: Simplify beginPatchDrag — remove old threshold-based wall edge initiation**

In `beginPatchDrag()`, the old wall/baffle edge resize block (lines 17801-17850) that uses `hoverPatchEdge.isWallEdge` is now handled by the new resize handle block from Task 3. **Remove** lines 17801-17850 (the `if (hoverPatchEdge && hoverPatchEdge.isWallEdge)` block).

Also simplify the `boxPatchHit` section of `beginPatchDrag()` (around lines 17773-17775). Replace the `interaction = ... getBoxPatchDragMode(...)` / `getServerPatchDragMode(...)` calls with a direct mode assignment. Since resize is now handled by green cubes (Task 3), any raycast hit on the patch body means a face-move drag. Change lines 17773-17775 to:

```javascript
            interaction = { mode: 'boxFace', edgeKey: null };
```

Keep the rest of the `boxPatchHit` block intact — it handles patch face-move (`boxFace` mode) and circle resize (`boxCircleResize` mode is still needed for circle patches). For circle patches specifically, keep the old circle detection inline:

```javascript
            if (!isServerPatch && context.patch.type === 'circle') {
                const dist = Math.hypot(facePoint.x - context.patch.cx, facePoint.y - context.patch.cy);
                const cThreshold = Math.max(Math.min(Math.min(fr.w, fr.h) * 0.06, 0.04), 0.01);
                if (Math.abs(dist - context.patch.r) <= cThreshold * 1.4) {
                    interaction = { mode: 'boxCircleResize', edgeKey: 'circle' };
                }
            }
```

- [ ] **Step 9: Verify page loads without errors**

```bash
export DISPLAY=:50 && export PATH="$HOME/.local/bin:$PATH"
browser-use eval "window.location.href='http://localhost:8005/STL_generate.html'"
# Wait for load
browser-use eval "typeof _createResizeHandles === 'function' && typeof createBoxPatchEdgeHelpers === 'undefined' && typeof getBoxPatchDragMode === 'undefined' ? 'CLEAN' : 'STILL EXISTS'"
```
Expected: `CLEAN`

- [ ] **Step 10: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "refactor(frontend): remove old edge helper and threshold systems"
```

---

### Task 5: Verify and fix box patch snap for baffles/floors (Layer A)

**Files:**
- Modify: `src/frontend/web/STL_generate.html:18318-18420` (box patch snap logic)

The existing snap code at lines 18318-18420 should already work for baffle/floor patches. This task verifies it and fixes any issues.

- [ ] **Step 1: Load a test project and test baffle patch snap**

```bash
export DISPLAY=:50 && export PATH="$HOME/.local/bin:$PATH"
# Load STDCT project (has baffles near racks)
browser-use eval "window.t=window.I18n&&window.I18n.t?window.I18n.t.bind(window.I18n):function(k){return k}; var xhr=new XMLHttpRequest(); xhr.open('GET','/projects/STDCT.cfdproj',false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText)); 'loaded:'+state.models.length"
# Find a baffle and add a rect patch
browser-use eval "var b=state.models.find(m=>m.kind==='baffle'); if(!b){'no baffle'}else{if(!b.faces.front.patches.length){b.faces.front.patches.push({id:'test_snap_patch',type:'rect',x:0.1,y:0.1,w:0.3,h:0.5})}; rebuildGeometry(); 'baffle:'+b.id+' patches:'+b.faces.front.patches.length}"
# Check snap state
browser-use eval "'snapFace:'+state.snapFace+' snapEdge:'+state.snapEdge"
```

Verify `snapFace:true snapEdge:true`. If they're false, enable them:
```bash
browser-use eval "state.snapFace=true; state.snapEdge=true; 'enabled'"
```

- [ ] **Step 2: Verify snap code path is reachable**

Add a temporary diagnostic to verify the snap code at line 18319 is reached for baffle patches. In the snap condition block, temporarily log:

```javascript
// At line 18323, after _lastSnapTime = performance.now();
console.log('[SNAP-DIAG] model.kind=' + model.kind + ' patch=' + patch.id + ' mode=' + dragState.mode);
```

Load and drag a baffle patch. Check console output. If the log appears, the snap path is reached. If not, investigate the conditions at lines 18319-18322.

After confirming, remove the diagnostic.

- [ ] **Step 3: Fix any issues found**

If the snap doesn't fire, check:
1. `_PATCH_SNAP_KINDS.has(model.kind)` — verify `BOX_PATCH_DRAG_KINDS` includes 'baffle' and 'floor' (line 2220)
2. `dragState.mode === 'boxFace' || dragState.mode === 'boxRectResize'` — verify green cube drag sets `mode: 'boxRectResize'`
3. `!context.isServerPatch` — verify baffle patch context has no `isServerPatch`
4. `getFaceLayouts(model)[dragState.faceId]` — verify face layout exists for the drag faceId

Fix any issues found.

- [ ] **Step 4: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "fix(frontend): verify and fix box patch snap for baffles and floors"
```

---

### Task 6: Add same-kind patch-to-patch snap (Layer B)

**Files:**
- Modify: `src/frontend/web/STL_generate.html:18364-18404` (snap loop in `updatePatchDrag`)

This task extends the existing snap loop to also compare against patches on other models of the same kind.

- [ ] **Step 1: Add same-kind patch snap inside the renderRoots.forEach loop**

In the snap loop at line 18364 (`renderRoots.forEach(root => { ... })`), after the existing AABB edge comparison (lines 18380-18403), add a patch-to-patch comparison block:

```javascript
                // Same-kind patch-to-patch snap: compare dragged patch edges
                // to other patches on models of the same kind.
                const otherModel = getModelById(oid);
                if (otherModel && otherModel.kind === model.kind && otherModel.kind !== 'rack') {
                    const otherRoot = root;
                    otherRoot.updateMatrixWorld(true);
                    const otherFaces = otherModel.faces || {};
                    Object.keys(otherFaces).forEach(ofid => {
                        const oPatches = (otherFaces[ofid].patches || []);
                        oPatches.forEach(op => {
                            if (op.type !== 'rect') return;
                            const oLayout = getFaceLayouts(otherModel)[ofid];
                            if (!oLayout) return;
                            // Compute patch corners in world-space projections
                            const oOrigin = otherRoot.localToWorld(oLayout.origin.clone());
                            const oWR = oLayout.right.clone().transformDirection(otherRoot.matrixWorld);
                            const oWU = oLayout.up.clone().transformDirection(otherRoot.matrixWorld);
                            const oOrigR = oOrigin.dot(worldRight);
                            const oOrigU = oOrigin.dot(worldUp);
                            const opMinR = oOrigR + op.x;
                            const opMaxR = oOrigR + op.x + (op.w || 0);
                            const opMinU = oOrigU + op.y;
                            const opMaxU = oOrigU + op.y + (op.h || 0);
                            // Right direction
                            if (!isResize || edge === 'left' || edge === 'right') {
                                [opMinR, opMaxR].forEach(t => {
                                    const myEdges = isResize ? (edge === 'right' ? [wMaxR] : [wMinR]) : [wMinR, wMaxR];
                                    myEdges.forEach(me => {
                                        const d = Math.abs(me - t);
                                        if (d < bestDistR) { bestDistR = d; bestDR = t - me; }
                                    });
                                });
                            }
                            // Up direction
                            if (!isResize || edge === 'top' || edge === 'bottom') {
                                [opMinU, opMaxU].forEach(t => {
                                    const myEdges = isResize ? (edge === 'top' ? [wMaxU] : [wMinU]) : [wMinU, wMaxU];
                                    myEdges.forEach(me => {
                                        const d = Math.abs(me - t);
                                        if (d < bestDistU) { bestDistU = d; bestDU = t - me; }
                                    });
                                });
                            }
                        });
                    });
                }
```

- [ ] **Step 2: Test same-kind patch snap**

```bash
export DISPLAY=:50 && export PATH="$HOME/.local/bin:$PATH"
# Create two baffles with patches and verify snap works between them
browser-use eval "
var b1=state.models.find(m=>m.kind==='baffle');
var b2=state.models.filter(m=>m.kind==='baffle')[1];
if(b1&&b2){
    if(!b1.faces.front.patches.length) b1.faces.front.patches.push({id:'bp1',type:'rect',x:0.1,y:0.1,w:0.3,h:0.5});
    if(!b2.faces.front.patches.length) b2.faces.front.patches.push({id:'bp2',type:'rect',x:0.2,y:0.1,w:0.3,h:0.5});
    rebuildGeometry();
    'b1:'+b1.id+' b2:'+b2.id+' patches OK';
} else { 'need 2 baffles'; }
"
```

- [ ] **Step 3: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): add same-kind patch-to-patch snap"
```

---

### Task 7: Extend server patch snap to all faces within same rack (Layer C)

**Files:**
- Modify: `src/frontend/web/STL_generate.html:18424-18487` (server patch snap)

This task extends the existing server patch inter-equipment snap to compare across ALL face types, not just the current face.

- [ ] **Step 1: Replace the face-type filter with cross-face comparison**

Replace the inner loop of the server patch snap (lines 18435-18472). Currently it only checks `otherFaces[dragState.faceId]` (same face type). Change it to iterate ALL face types:

Find the block starting with `var eqs = model.rack ? ...` (line 18434) and replace through line 18472 with:

```javascript
            var eqs = model.rack ? (model.rack.equipments || model.equipments || []) : (model.equipments || []);
            for (var ei = 0; ei < eqs.length; ei++) {
                var otherEq = eqs[ei];
                if (otherEq.id === _equipment.id) continue;
                var otherFaces = otherEq.faces;
                if (!otherFaces) continue;
                // Compare against patches on ALL face types in the same rack
                var faceIds = Object.keys(otherFaces);
                for (var fi = 0; fi < faceIds.length; fi++) {
                    var otherFaceId = faceIds[fi];
                    var otherPatches = otherFaces[otherFaceId].patches || [];
                    for (var pi = 0; pi < otherPatches.length; pi++) {
                        var op = otherPatches[pi];
                        if (op.type !== 'rect') continue;
                        var oy = op.y || 0, oyh = oy + (op.h || 0.5);
                        var ox = op.x || 0, oxw = ox + (op.w || 0.5);
                        // Y-edge snap candidates
                        var yCandidates = [
                            { d: Math.abs(myY - oy), shift: oy - myY },
                            { d: Math.abs(myY - oyh), shift: oyh - myY },
                            { d: Math.abs(myYH - oy), shift: oy - myYH },
                            { d: Math.abs(myYH - oyh), shift: oyh - myYH }
                        ];
                        for (var ci = 0; ci < yCandidates.length; ci++) {
                            if (yCandidates[ci].d < bestDistY) {
                                bestDistY = yCandidates[ci].d;
                                bestDY = yCandidates[ci].shift;
                            }
                        }
                        // X-edge snap candidates
                        var xCandidates = [
                            { d: Math.abs(myX - ox), shift: ox - myX },
                            { d: Math.abs(myX - oxw), shift: oxw - myX },
                            { d: Math.abs(myXW - ox), shift: ox - myXW },
                            { d: Math.abs(myXW - oxw), shift: oxw - myXW }
                        ];
                        for (var ci = 0; ci < xCandidates.length; ci++) {
                            if (xCandidates[ci].d < bestDistX) {
                                bestDistX = xCandidates[ci].d;
                                bestDX = xCandidates[ci].shift;
                            }
                        }
                    }
                }
            }
```

- [ ] **Step 2: Test server patch cross-face snap**

```bash
export DISPLAY=:50 && export PATH="$HOME/.local/bin:$PATH"
# Load a project with racks that have server equipment
browser-use eval "var r=state.models.find(m=>m.kind==='rack'&&m.rack&&m.rack.equipments&&m.rack.equipments.length>1); r ? 'rack:'+r.id+' equipments:'+r.rack.equipments.length : 'no multi-equip rack'"
```

- [ ] **Step 3: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): extend server patch snap to all faces within same rack"
```

---

### Task 8: Add snap visual feedback (dashed guide line)

**Files:**
- Modify: `src/frontend/web/STL_generate.html:18406-18420` (after snap application)
- Modify: `src/frontend/web/STL_generate.html:18540-18583` (`endPatchDrag()` cleanup)

This task adds a temporary dashed guide line when a snap activates.

- [ ] **Step 1: Add snap guide line state**

Near the resize handle state variables (added in Task 1), add:

```javascript
let _snapGuideLine = null;

function _showSnapGuide(fromWorld, toWorld) {
    _clearSnapGuide();
    const geo = new THREE.BufferGeometry().setFromPoints([fromWorld, toWorld]);
    const mat = new THREE.LineDashedMaterial({
        color: 0x00ffaa, dashSize: 0.05, gapSize: 0.03,
        depthTest: false, transparent: true, opacity: 0.8
    });
    _snapGuideLine = new THREE.Line(geo, mat);
    _snapGuideLine.computeLineDistances();
    _snapGuideLine.renderOrder = 12;
    _ensureResizeHandleGroup().add(_snapGuideLine);
}

function _clearSnapGuide() {
    if (_snapGuideLine) {
        if (_snapGuideLine.geometry) _snapGuideLine.geometry.dispose();
        if (_snapGuideLine.material) _snapGuideLine.material.dispose();
        if (_snapGuideLine.parent) _snapGuideLine.parent.remove(_snapGuideLine);
        _snapGuideLine = null;
    }
}
```

- [ ] **Step 2: Show guide line when box patch snap activates**

In the box patch snap block (around line 18408), replace the existing application block:

```javascript
            if (Math.abs(dx) > 1e-8 || Math.abs(dy) > 1e-8) {
```

with:

```javascript
            _clearSnapGuide();
            if (Math.abs(dx) > 1e-8 || Math.abs(dy) > 1e-8) {
```

This clears any previous guide on each drag frame. No guide line is shown for the box patch snap because the snap target is an AABB edge (not a single point) — the yellow outline + snapping behavior is sufficient feedback.

For the same-kind patch-to-patch snap (added in Task 6), the guide is also not needed because patches snap to other patches' edges which are already visible.

- [ ] **Step 3: Clear guide line on drag end**

In `endPatchDrag()`, add `_clearSnapGuide();` alongside the handle color reset (added in Task 3 Step 3).

Also add `_clearSnapGuide();` to `_disposeResizeHandles()` (Task 1 Step 2).

- [ ] **Step 4: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): add snap guide line visual feedback infrastructure"
```

---

### Task 9: Final integration test and verification

**Files:**
- No code changes — verification only

- [ ] **Step 1: Restart server with all changes**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
nohup conda run -n dcdtuni python src/server/server.py --port 8005 --ws-port 8006 > /tmp/server_final.log 2>&1 &
sleep 5
curl -s http://localhost:8005/ > /dev/null && echo "UP" || echo "DOWN"
```

- [ ] **Step 2: Test wall face selection shows handles**

```bash
export DISPLAY=:50 && export PATH="$HOME/.local/bin:$PATH"
browser-use eval "window.location.href='http://localhost:8005/STL_generate.html'"
# Wait and login
browser-use eval "window.t=window.I18n&&window.I18n.t?window.I18n.t.bind(window.I18n):function(k){return k}; fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'WIN#@desk'})}).then(r=>r.json())"
browser-use eval "var xhr=new XMLHttpRequest(); xhr.open('GET','/projects/STDCT.cfdproj',false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText)); 'loaded:'+state.models.length"
# Select wall
browser-use eval "var w=state.models.find(m=>m.kind==='wall'); setSelection('face',w.id,'front'); _activeResizeHandles ? 'HANDLES OK' : 'NO HANDLES'"
browser-use screenshot /tmp/final_wall_handles.png
```
Expected: `HANDLES OK` and screenshot shows yellow outline + 4 green cubes on wall.

- [ ] **Step 3: Test baffle face selection shows handles**

```bash
browser-use eval "var b=state.models.find(m=>m.kind==='baffle'); setSelection('face',b.id,'front'); _activeResizeHandles ? 'HANDLES OK' : 'NO HANDLES'"
browser-use screenshot /tmp/final_baffle_handles.png
```
Expected: `HANDLES OK`

- [ ] **Step 4: Test patch selection shows handles**

```bash
browser-use eval "var b=state.models.find(m=>m.kind==='box'); var p=b&&b.faces.front&&b.faces.front.patches&&b.faces.front.patches[0]; if(p){setSelection('patch',b.id,'front',p.id); _activeResizeHandles?'PATCH HANDLES OK':'NO PATCH HANDLES'}else{'no box patch found'}"
browser-use screenshot /tmp/final_patch_handles.png
```

- [ ] **Step 5: Test old systems are removed**

```bash
browser-use eval "typeof getBoxPatchDragMode + '|' + typeof getBoxPatchEdgeThreshold + '|' + typeof createBoxPatchEdgeHelpers + '|' + typeof createWallBaffleEdgeHelpers"
```
Expected: `undefined|undefined|undefined|undefined`

- [ ] **Step 6: Test snap is enabled**

```bash
browser-use eval "'snapFace:'+state.snapFace+' snapEdge:'+state.snapEdge"
```
Expected: `snapFace:true snapEdge:true`

- [ ] **Step 7: Test handles hidden on non-geometry tabs**

```bash
browser-use eval "state.dctwinConfig.activeTab='mesh'; _applyLeftTabUI(); _resizeHandleGroup ? 'visible:'+_resizeHandleGroup.visible : 'no group'"
browser-use eval "state.dctwinConfig.activeTab='geometry'; _applyLeftTabUI(); _resizeHandleGroup ? 'visible:'+_resizeHandleGroup.visible : 'no group'"
```
Expected: first `visible:false`, second `visible:true`

- [ ] **Step 8: Commit final state**

If any fixes were needed during verification, commit them:
```bash
git add src/frontend/web/STL_generate.html
git commit -m "fix(frontend): final integration fixes for resize handles and snap"
```
