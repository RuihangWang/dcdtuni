# Image Texture Application Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply 7 photographic image textures to specific data center object surfaces (server front/rear, rack PDU strips, ACU return, baffle/floor-vent grille) for realistic rendering.

**Architecture:** UV-normalized image materials with centralized UV handling in `spawnRenderable()`. New material typeKeys load image files via `externalTextureRegistry`, and a `uvNormalize` option on `spawnRenderable()` transforms geometry UVs per-mesh while keeping materials shared and cached.

**Tech Stack:** Three.js r128, MeshPhongMaterial, TextureLoader, ShapeGeometry UV manipulation

**Spec:** `docs/superpowers/specs/2026-05-27-image-texture-application-design.md`

---

## File Map

All changes are in a single file:

- **Modify:** `src/frontend/web/STL_generate.html`
  - Lines 1843-1857: `externalTextureRegistry` — add 7 new typeKey entries
  - Lines 1860-1878: `loadExternalTexture()` — add optional `wrapMode` parameter
  - After line 1885: new `_preloadImageTextures()` function
  - Lines 1926-1944: `_generateProceduralTexture()` — add null-returning entries for image typeKeys
  - Lines 2118-2133: `_materialPresets` — add 7 new preset entries
  - Lines 4361-4381: DOMContentLoaded handler — call `_preloadImageTextures()`
  - Lines 7134-7207: `spawnRenderable()` — add UV normalization block + modify tile-size repeat logic
  - Lines 7177: `tileSizes` — add 2 new entries for tile-physical types
  - Lines 7251: `renderPlanarModelWithPatches()` — use white color for image-textured patches
  - Lines 7285: `renderBaffleModel()` — pass `'baffle-grille'` as patchObjectKind
  - Lines 7570: `renderFloorModel()` — change patchObjectKind from `'floor-vent'` to `'baffle-grille'`
  - Lines 7593-7619: `_buildSingleBoxFace()` — use `'acu-return-photo'` for ACU return patches
  - Lines 7840-7901: `_buildSingleServerFace()` — change objectKind for inlet/outlet, add uvNormalize
  - Lines 7985-8109: `_buildServerFaceWithPatches()` — derive per-part objectKind + uvNormalize
  - Lines 8120-8278: `buildRackFrameGeometry()` — add uvNormalize to spawnFaceRect, use strip textures

---

### Task 1: Add material presets and texture registry entries

**Files:**
- Modify: `src/frontend/web/STL_generate.html:1843-1857` (externalTextureRegistry)
- Modify: `src/frontend/web/STL_generate.html:2118-2133` (_materialPresets)
- Modify: `src/frontend/web/STL_generate.html:1926-1944` (_generateProceduralTexture)
- Modify: `src/frontend/web/STL_generate.html:7177` (tileSizes)

- [ ] **Step 1: Add 7 entries to `externalTextureRegistry` (line 1857)**

Insert before the closing `};` of `externalTextureRegistry`:

```javascript
        'server-front-photo': { diffuse: null, normal: null, roughness: null, metalness: null },
        'server-rear-socket': { diffuse: null, normal: null, roughness: null, metalness: null },
        'server-outlet-mesh': { diffuse: null, normal: null, roughness: null, metalness: null },
        'rack-strip-left': { diffuse: null, normal: null, roughness: null, metalness: null },
        'rack-strip-right': { diffuse: null, normal: null, roughness: null, metalness: null },
        'acu-return-photo': { diffuse: null, normal: null, roughness: null, metalness: null },
        'baffle-grille': { diffuse: null, normal: null, roughness: null, metalness: null },
```

- [ ] **Step 2: Add 7 entries to `_materialPresets` (line 2133)**

Insert before the closing `};` of `_materialPresets`:

```javascript
        'server-front-photo': { metalness: 0.3,  roughness: 0.6,  normalScale: 0, envMapIntensity: 0.3,  tintColor: 0xffffff },
        'server-rear-socket': { metalness: 0.2,  roughness: 0.65, normalScale: 0, envMapIntensity: 0.25, tintColor: 0xffffff },
        'server-outlet-mesh': { metalness: 0.35, roughness: 0.5,  normalScale: 0, envMapIntensity: 0.3,  tintColor: 0xffffff },
        'rack-strip-left':    { metalness: 0.15, roughness: 0.7,  normalScale: 0, envMapIntensity: 0.2,  tintColor: 0xffffff },
        'rack-strip-right':   { metalness: 0.15, roughness: 0.7,  normalScale: 0, envMapIntensity: 0.2,  tintColor: 0xffffff },
        'acu-return-photo':   { metalness: 0.1,  roughness: 0.6,  normalScale: 0, envMapIntensity: 0.2,  tintColor: 0xffffff },
        'baffle-grille':      { metalness: 0.3,  roughness: 0.55, normalScale: 0, envMapIntensity: 0.25, tintColor: 0xffffff },
```

All use `tintColor: 0xffffff` (full-brightness photo) and `normalScale: 0` (no normal map).

- [ ] **Step 3: Add null generators in `_generateProceduralTexture` (line 1941)**

Insert before the closing `};` of the `generators` object inside `_generateProceduralTexture()`:

```javascript
            'server-front-photo': () => ({ diffuse: null, normal: null }),
            'server-rear-socket': () => ({ diffuse: null, normal: null }),
            'server-outlet-mesh': () => ({ diffuse: null, normal: null }),
            'rack-strip-left':    () => ({ diffuse: null, normal: null }),
            'rack-strip-right':   () => ({ diffuse: null, normal: null }),
            'acu-return-photo':   () => ({ diffuse: null, normal: null }),
            'baffle-grille':      () => ({ diffuse: null, normal: null }),
```

These bypass procedural canvas generation — textures come from image files via `externalTextureRegistry`.

- [ ] **Step 4: Add tile sizes for tile-physical types (line 7177)**

In the `tileSizes` object inside `spawnRenderable()`, add two entries. Change the line:

```javascript
                'server-front': 0.15, 'blank-panel': 0.3, 'floor': 0.6, 'floor-vent': 0.3, 'wall': 0.5, 'baffle': 0.25, 'box': 0.4, 'acu-supply': 0.25, 'acu-return': 0.3 };
```

To:

```javascript
                'server-front': 0.15, 'blank-panel': 0.3, 'floor': 0.6, 'floor-vent': 0.3, 'wall': 0.5, 'baffle': 0.25, 'box': 0.4, 'acu-supply': 0.25, 'acu-return': 0.3,
                'server-outlet-mesh': 0.1, 'baffle-grille': 0.3 };
```

- [ ] **Step 5: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): add material presets and registry entries for 7 image textures"
```

---

### Task 2: Add image texture loading infrastructure

**Files:**
- Modify: `src/frontend/web/STL_generate.html:1860-1878` (loadExternalTexture)
- Modify: `src/frontend/web/STL_generate.html` after line 1885 (new function)
- Modify: `src/frontend/web/STL_generate.html:4361-4381` (DOMContentLoaded)

- [ ] **Step 1: Add `wrapMode` parameter to `loadExternalTexture` (line 1861)**

Change:

```javascript
    function loadExternalTexture(typeKey, mapType, url, rotation) {
        const loader = new THREE.TextureLoader();
        loader.load(url, (texture) => {
            texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
```

To:

```javascript
    function loadExternalTexture(typeKey, mapType, url, rotation, wrapMode) {
        const loader = new THREE.TextureLoader();
        loader.load(url, (texture) => {
            texture.wrapS = texture.wrapT = wrapMode ?? THREE.RepeatWrapping;
```

Existing callers pass no 5th argument → `undefined` → defaults to RepeatWrapping. No behavioral change for existing code.

- [ ] **Step 2: Add `_preloadImageTextures` function (after line 1885)**

Insert after the `_getOrCreateTexture` function (after line 1885):

```javascript
    function _preloadImageTextures() {
        var base = 'textures/MISC/';
        var R = THREE.RepeatWrapping, C = THREE.ClampToEdgeWrapping;
        loadExternalTexture('server-front-photo', 'diffuse', base + 'ServerFront_compressed.jpg', 0, R);
        loadExternalTexture('server-rear-socket', 'diffuse', base + '1U_ServerRearSocket.jpg', 0, R);
        loadExternalTexture('server-outlet-mesh', 'diffuse', base + 'sever_rear_outletMesh.jpg', 0, R);
        loadExternalTexture('rack-strip-left',    'diffuse', base + 'Power Strip 1.jpg', 0, C);
        loadExternalTexture('rack-strip-right',   'diffuse', base + 'Power Strip 2.jpg', 0, C);
        loadExternalTexture('acu-return-photo',   'diffuse', base + 'ACU Return 3 Panel.png', 0, C);
        loadExternalTexture('baffle-grille',      'diffuse', base + 'Slotted Grille - 600mm.jpg', 0, R);
    }
```

- [ ] **Step 3: Call `_preloadImageTextures()` in DOMContentLoaded (line 4381)**

After `setTimeout(_initTerminal, 500);` (line 4380), add:

```javascript
        _preloadImageTextures();
```

Textures load async; the existing `_textureRebuildTimer` debounce in `loadExternalTexture` triggers a geometry rebuild once loaded.

- [ ] **Step 4: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): add image texture preloading for 7 MISC textures"
```

---

### Task 3: Add UV normalization to spawnRenderable()

**Files:**
- Modify: `src/frontend/web/STL_generate.html:7163-7184` (spawnRenderable)

- [ ] **Step 1: Add UV normalization block after mesh creation (line 7167)**

Insert between `if (Number.isFinite(options.renderOrder)) mesh.renderOrder = options.renderOrder;` (line 7166) and the tile-size comment `// Scale texture repeat` (line 7167):

```javascript
        if (options.uvNormalize && geom.attributes && geom.attributes.uv) {
            var uvAttr = geom.attributes.uv;
            var uvMode = options.uvNormalize;
            if (uvMode.type === 'stretch' || uvMode.type === 'tile-per-u') {
                var minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
                for (var i = 0; i < uvAttr.count; i++) {
                    var u = uvAttr.getX(i), v = uvAttr.getY(i);
                    if (u < minU) minU = u; if (u > maxU) maxU = u;
                    if (v < minV) minV = v; if (v > maxV) maxV = v;
                }
                var rangeU = (maxU - minU) || 1, rangeV = (maxV - minV) || 1;
                var scaleV = (uvMode.type === 'tile-per-u') ? (uvMode.uCount || 1) : 1;
                for (var i = 0; i < uvAttr.count; i++) {
                    uvAttr.setX(i, (uvAttr.getX(i) - minU) / rangeU);
                    uvAttr.setY(i, ((uvAttr.getY(i) - minV) / rangeV) * scaleV);
                }
                uvAttr.needsUpdate = true;
            }
        }
```

- [ ] **Step 2: Modify the tile-size repeat block to respect uvNormalize (line 7168-7184)**

Change the existing repeat logic block from:

```javascript
        if (webglAvailable && material.map && geom.attributes && geom.attributes.uv) {
            const uvAttr = geom.attributes.uv;
            let maxU = 0, maxV = 0;
            for (let i = 0; i < uvAttr.count; i++) {
                maxU = Math.max(maxU, Math.abs(uvAttr.getX(i)));
                maxV = Math.max(maxV, Math.abs(uvAttr.getY(i)));
            }
            // Target: how many meters per texture tile (smaller = denser pattern)
            const tileSizes = { 'rack-side': 0.3, 'rack-front': 0.3, 'rack-top': 0.3, 'rack-bottom': 0.3,
                'server-front': 0.15, 'blank-panel': 0.3, 'floor': 0.6, 'floor-vent': 0.3, 'wall': 0.5, 'baffle': 0.25, 'box': 0.4, 'acu-supply': 0.25, 'acu-return': 0.3,
                'server-outlet-mesh': 0.1, 'baffle-grille': 0.3 };
            const tileSize = tileSizes[options.objectKind] || 0.4;
            const repeatX = Math.max(1, maxU / tileSize);
            const repeatY = Math.max(1, maxV / tileSize);
            material.map.repeat.set(repeatX, repeatY);
            if (material.normalMap) material.normalMap.repeat.set(repeatX, repeatY);
            if (material.roughnessMap) material.roughnessMap.repeat.set(repeatX, repeatY);
            if (material.metalnessMap) material.metalnessMap.repeat.set(repeatX, repeatY);
        }
```

To:

```javascript
        if (webglAvailable && material.map && geom.attributes && geom.attributes.uv) {
            var _uvNorm = options.uvNormalize;
            if (_uvNorm && (_uvNorm.type === 'stretch' || _uvNorm.type === 'tile-per-u')) {
                material.map.repeat.set(1, 1);
            } else {
                const uvAttr = geom.attributes.uv;
                let maxU = 0, maxV = 0;
                for (let i = 0; i < uvAttr.count; i++) {
                    maxU = Math.max(maxU, Math.abs(uvAttr.getX(i)));
                    maxV = Math.max(maxV, Math.abs(uvAttr.getY(i)));
                }
                const tileSizes = { 'rack-side': 0.3, 'rack-front': 0.3, 'rack-top': 0.3, 'rack-bottom': 0.3,
                    'server-front': 0.15, 'blank-panel': 0.3, 'floor': 0.6, 'floor-vent': 0.3, 'wall': 0.5, 'baffle': 0.25, 'box': 0.4, 'acu-supply': 0.25, 'acu-return': 0.3,
                    'server-outlet-mesh': 0.1, 'baffle-grille': 0.3 };
                const tileSize = (_uvNorm && _uvNorm.tileSize) || tileSizes[options.objectKind] || 0.4;
                const repeatX = Math.max(1, maxU / tileSize);
                const repeatY = Math.max(1, maxV / tileSize);
                material.map.repeat.set(repeatX, repeatY);
                if (material.normalMap) material.normalMap.repeat.set(repeatX, repeatY);
                if (material.roughnessMap) material.roughnessMap.repeat.set(repeatX, repeatY);
                if (material.metalnessMap) material.metalnessMap.repeat.set(repeatX, repeatY);
            }
        }
```

For `stretch` / `tile-per-u`: UVs already encode tiling, so repeat = (1,1).
For `tile-physical` (`_uvNorm.tileSize` set): use custom tile size instead of the `tileSizes` lookup.
For no `uvNormalize`: unchanged existing behavior.

- [ ] **Step 3: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): add UV normalization support in spawnRenderable for image textures"
```

---

### Task 4: Apply server front and rear image textures

**Files:**
- Modify: `src/frontend/web/STL_generate.html:7840-7901` (_buildSingleServerFace)
- Modify: `src/frontend/web/STL_generate.html:7985-8109` (_buildServerFaceWithPatches)

- [ ] **Step 1: Change objectKind in `_buildSingleServerFace` (line 7864)**

Change:

```javascript
        var isInletOutlet = (faceId === 'inlet' || faceId === 'outlet');
        var objectKind = isInletOutlet ? 'server-front' : undefined;
```

To:

```javascript
        var isInletOutlet = (faceId === 'inlet' || faceId === 'outlet');
        var objectKind = (faceId === 'inlet') ? 'server-front-photo'
                       : (faceId === 'outlet') ? 'server-rear-socket'
                       : undefined;
```

- [ ] **Step 2: Add uvNormalize to the no-patch path (line 7899-7900)**

Change:

```javascript
            if (objectKind) spawnOpts.objectKind = objectKind;
            spawnPlanarRect(modelGroup, fr.origin, fr.right, fr.up, fr.w, fr.h, spawnOpts);
```

To:

```javascript
            if (objectKind) spawnOpts.objectKind = objectKind;
            if (isInletOutlet) spawnOpts.uvNormalize = { type: 'tile-per-u', uCount: equipment.spanUnits };
            spawnPlanarRect(modelGroup, fr.origin, fr.right, fr.up, fr.w, fr.h, spawnOpts);
```

- [ ] **Step 3: Derive per-part objectKind + uvNormalize in `_buildServerFaceWithPatches` (line 7987)**

Insert after the opening brace of the function (after line 7987 `// Build islands from patches`):

```javascript
        var isOutlet = (faceId === 'outlet');
        var patchOK = isOutlet ? 'server-outlet-mesh' : objectKind;
        var patchUV = isOutlet
            ? { type: 'tile-physical', tileSize: 0.1 }
            : { type: 'tile-per-u', uCount: equipment.spanUnits };
        var wallOK = objectKind;
        var wallUV = (faceId === 'inlet' || isOutlet)
            ? { type: 'tile-per-u', uCount: equipment.spanUnits }
            : undefined;
```

- [ ] **Step 4: Use derived objectKind + uvNormalize for patch islands (line 8044-8062)**

Change the patch `spawnRenderable` call from:

```javascript
                var mesh = spawnRenderable(modelGroup, geom, {
                    // Yellow marker tint instead of dimmed opacity — user wants to
                    // see patches at the SAME opacity as the face, distinguished
                    // only by a yellow overlay.
                    color: SERVER_PATCH_TINT,
                    opacity: opacity,
                    objectKind: objectKind,
                    doubleSide: doubleSide,
                    renderOrder: 2,  // render above face remainder so raycaster picks patch first
```

To:

```javascript
                var _imgPatch = (patchOK === 'server-outlet-mesh' || patchOK === 'server-front-photo');
                var mesh = spawnRenderable(modelGroup, geom, {
                    color: _imgPatch ? '#ffffff' : SERVER_PATCH_TINT,
                    opacity: opacity,
                    objectKind: patchOK,
                    doubleSide: doubleSide,
                    renderOrder: 2,
                    uvNormalize: patchUV,
```

White color for image-textured patches so photos display at full brightness (not yellow-tinted). `patchOK` and `patchUV` use the derived values from Step 3.

- [ ] **Step 5: Use derived objectKind + uvNormalize for wall remainder (line 8094-8098)**

Change the wall remainder `spawnRenderable` call from:

```javascript
            var mesh = spawnRenderable(modelGroup, geom, {
                color: faceColor,
                opacity: opacity,  // full face opacity — patches are distinguished by the yellow tint overlay, not by opacity
                objectKind: objectKind,
                doubleSide: doubleSide,
```

To:

```javascript
            var mesh = spawnRenderable(modelGroup, geom, {
                color: faceColor,
                opacity: opacity,
                objectKind: wallOK,
                doubleSide: doubleSide,
                uvNormalize: wallUV,
```

- [ ] **Step 6: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): apply server front/rear image textures with per-U tiling"
```

---

### Task 5: Apply rack PDU strip textures

**Files:**
- Modify: `src/frontend/web/STL_generate.html:8149-8161` (spawnFaceRect closure)
- Modify: `src/frontend/web/STL_generate.html:8183-8191` (left/right strip calls)

- [ ] **Step 1: Add `uvNormalize` parameter to `spawnFaceRect` closure (line 8149)**

Change:

```javascript
        var spawnFaceRect = function(faceId, x, y, width, height, color, extraUD, objectKindOverride) {
            var layout = faceLayouts[faceId];
            var faceOpacity = (model.faces[faceId]?.opacity ?? (model.opacity ?? 1)) * fp.dimMult;
            return spawnPlanarRect(modelGroup,
                layout.origin.clone().add(layout.right.clone().multiplyScalar(x)).add(layout.up.clone().multiplyScalar(y)),
                layout.right.clone(), layout.up.clone(), width, height, {
                    color: color,
                    opacity: faceOpacity,
                    objectKind: objectKindOverride || rackFaceKind[faceId] || 'rack-side',
                    doubleSide: true,
                    userData: Object.assign({ modelId: model.id, faceId: faceId }, extraUD)
                });
        };
```

To:

```javascript
        var spawnFaceRect = function(faceId, x, y, width, height, color, extraUD, objectKindOverride, uvNormalize) {
            var layout = faceLayouts[faceId];
            var faceOpacity = (model.faces[faceId]?.opacity ?? (model.opacity ?? 1)) * fp.dimMult;
            var opts = {
                color: color,
                opacity: faceOpacity,
                objectKind: objectKindOverride || rackFaceKind[faceId] || 'rack-side',
                doubleSide: true,
                userData: Object.assign({ modelId: model.id, faceId: faceId }, extraUD)
            };
            if (uvNormalize) opts.uvNormalize = uvNormalize;
            return spawnPlanarRect(modelGroup,
                layout.origin.clone().add(layout.right.clone().multiplyScalar(x)).add(layout.up.clone().multiplyScalar(y)),
                layout.right.clone(), layout.up.clone(), width, height, opts);
        };
```

- [ ] **Step 2: Apply strip textures to front-face left/right strips (lines 8182-8192)**

Change:

```javascript
            // Left frame strip
            if (fp.slotX > 1e-6 && fp.slotAreaHeight > 1e-6) {
                spawnFaceRect(side, 0, fp.marginBottom, fp.slotX, fp.slotAreaHeight, color,
                    Object.assign({ side: side }, makeUD(side + '_strip_left')));
            }
            // Right frame strip
            var rightFrameW = fp.W - fp.slotX - fp.slotWidth;
            if (rightFrameW > 1e-6 && fp.slotAreaHeight > 1e-6) {
                spawnFaceRect(side, fp.slotX + fp.slotWidth, fp.marginBottom, rightFrameW, fp.slotAreaHeight, color,
                    Object.assign({ side: side }, makeUD(side + '_strip_right')));
            }
```

To:

```javascript
            // Left frame strip
            if (fp.slotX > 1e-6 && fp.slotAreaHeight > 1e-6) {
                var lStripOK = (side === 'front') ? 'rack-strip-left' : undefined;
                var lStripUV = (side === 'front') ? { type: 'stretch' } : undefined;
                spawnFaceRect(side, 0, fp.marginBottom, fp.slotX, fp.slotAreaHeight, color,
                    Object.assign({ side: side }, makeUD(side + '_strip_left')), lStripOK, lStripUV);
            }
            // Right frame strip
            var rightFrameW = fp.W - fp.slotX - fp.slotWidth;
            if (rightFrameW > 1e-6 && fp.slotAreaHeight > 1e-6) {
                var rStripOK = (side === 'front') ? 'rack-strip-right' : undefined;
                var rStripUV = (side === 'front') ? { type: 'stretch' } : undefined;
                spawnFaceRect(side, fp.slotX + fp.slotWidth, fp.marginBottom, rightFrameW, fp.slotAreaHeight, color,
                    Object.assign({ side: side }, makeUD(side + '_strip_right')), rStripOK, rStripUV);
            }
```

Front-face strips get image textures (`rack-strip-left`/`rack-strip-right` with `stretch` UV mode). Back-face strips pass `undefined` → no change from existing behavior.

- [ ] **Step 3: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): apply PDU power strip textures to rack frame front strips"
```

---

### Task 6: Apply ACU return panel texture

**Files:**
- Modify: `src/frontend/web/STL_generate.html:7588-7619` (_buildSingleBoxFace)

- [ ] **Step 1: Change ACU return patches to use image texture (lines 7590-7619)**

Change the ACU patch section from:

```javascript
            let islandKind = 'box';
            if (isACU && island.refIds?.length) {
                const role = acuPP[island.refIds[0]]?.role;
                if (role === 'supply') islandKind = 'acu-supply';
                else if (role === 'return') islandKind = 'acu-return';
                else islandKind = 'box';
            }
            island.poly.forEach(polygon => {
                const shape = new THREE.Shape();
                polygon[0].forEach((pt, i) => i === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                for (let i = 1; i < polygon.length; i++) {
                    const hole = new THREE.Path();
                    polygon[i].forEach((pt, j) => j === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1]));
                    shape.holes.push(hole);
                }
                const geom = new THREE.ShapeGeometry(shape, 32);
                geom.applyMatrix4(layout.mat);
                spawnRenderable(modelGroup, geom, {
                    color: island.color,
                    opacity: faceOpacity,
                    objectKind: islandKind,
                    renderOrder: 3,
                    userData: {
                        modelId: model.id,
                        faceId,
                        refIds: island.refIds,
                        exportName: composeModelScopedExportName(model, faceId, face, { isPatch: true, patchIndex: islandIdx, patchCount: facesIslands[faceId].length }),
                        isBaseWall: false
                    }
                });
            });
```

To:

```javascript
            let islandKind = 'box';
            if (isACU && island.refIds?.length) {
                const role = acuPP[island.refIds[0]]?.role;
                if (role === 'supply') islandKind = 'acu-supply';
                else if (role === 'return') islandKind = 'acu-return-photo';
                else islandKind = 'box';
            }
            var _isImgIsland = (islandKind === 'acu-return-photo');
            island.poly.forEach(polygon => {
                const shape = new THREE.Shape();
                polygon[0].forEach((pt, i) => i === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
                for (let i = 1; i < polygon.length; i++) {
                    const hole = new THREE.Path();
                    polygon[i].forEach((pt, j) => j === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1]));
                    shape.holes.push(hole);
                }
                const geom = new THREE.ShapeGeometry(shape, 32);
                geom.applyMatrix4(layout.mat);
                var _patchOpts = {
                    color: _isImgIsland ? '#ffffff' : island.color,
                    opacity: faceOpacity,
                    objectKind: islandKind,
                    renderOrder: 3,
                    userData: {
                        modelId: model.id,
                        faceId,
                        refIds: island.refIds,
                        exportName: composeModelScopedExportName(model, faceId, face, { isPatch: true, patchIndex: islandIdx, patchCount: facesIslands[faceId].length }),
                        isBaseWall: false
                    }
                };
                if (_isImgIsland) _patchOpts.uvNormalize = { type: 'stretch' };
                spawnRenderable(modelGroup, geom, _patchOpts);
            });
```

Key changes:
- `'acu-return'` → `'acu-return-photo'` for return role
- White color for image-textured patches (full-brightness photo)
- `uvNormalize: { type: 'stretch' }` stretches the full chevron panel image across the patch

- [ ] **Step 2: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): apply ACU return panel texture with stretch UV mapping"
```

---

### Task 7: Apply baffle and floor-vent slotted grille texture

**Files:**
- Modify: `src/frontend/web/STL_generate.html:7239-7258` (renderPlanarModelWithPatches patch color)
- Modify: `src/frontend/web/STL_generate.html:7285` (renderBaffleModel)
- Modify: `src/frontend/web/STL_generate.html:7570` (renderFloorModel)

- [ ] **Step 1: Use white color for image-textured patches in `renderPlanarModelWithPatches` (line 7251)**

Change the patch spawnRenderable call from:

```javascript
                spawnRenderable(modelGroup, geom, {
                    color: island.color,
                    opacity: faceOpacity,
                    doubleSide: true,
                    objectKind: patchObjectKind || objectKind,
                    renderOrder: 3,
                    userData: { modelId: model.id, faceId: primaryFaceId, refIds: island.refIds, exportName: composeModelScopedExportName(model, primaryFaceId, face, { isPatch: true, patchIndex: islandIndex, patchCount: islands.length }), isBaseWall: false }
                });
```

To:

```javascript
                var _pOK = patchObjectKind || objectKind;
                var _pIsImg = (_pOK === 'baffle-grille');
                spawnRenderable(modelGroup, geom, {
                    color: _pIsImg ? '#ffffff' : island.color,
                    opacity: faceOpacity,
                    doubleSide: true,
                    objectKind: _pOK,
                    renderOrder: 3,
                    userData: { modelId: model.id, faceId: primaryFaceId, refIds: island.refIds, exportName: composeModelScopedExportName(model, primaryFaceId, face, { isPatch: true, patchIndex: islandIndex, patchCount: islands.length }), isBaseWall: false }
                });
```

- [ ] **Step 2: Pass `'baffle-grille'` as patchObjectKind in `renderBaffleModel` (line 7285)**

Change:

```javascript
    function renderBaffleModel(model, modelGroup, faceLayouts) {
        renderPlanarModelWithPatches(model, modelGroup, faceLayouts, 'front', '#b08040', 'baffle');
    }
```

To:

```javascript
    function renderBaffleModel(model, modelGroup, faceLayouts) {
        renderPlanarModelWithPatches(model, modelGroup, faceLayouts, 'front', '#b08040', 'baffle', 'baffle-grille');
    }
```

- [ ] **Step 3: Change patchObjectKind in `renderFloorModel` from `'floor-vent'` to `'baffle-grille'` (line 7570)**

Change:

```javascript
    function renderFloorModel(model, modelGroup, faceLayouts) {
        renderPlanarModelWithPatches(model, modelGroup, faceLayouts, 'top', '#8a9a6a', 'floor', 'floor-vent');
    }
```

To:

```javascript
    function renderFloorModel(model, modelGroup, faceLayouts) {
        renderPlanarModelWithPatches(model, modelGroup, faceLayouts, 'top', '#8a9a6a', 'floor', 'baffle-grille');
    }
```

- [ ] **Step 4: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat(frontend): apply slotted grille texture to baffle and floor-vent patches"
```

---

### Task 8: Visual verification

- [ ] **Step 1: Start the server**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python src/server/server.py --case-dir /tmp/texture-test &
```

Open `http://localhost:8005/STL_generate.html` in browser.

- [ ] **Step 2: Verify server textures**

1. Create a rack with a 2U fanServer and a 4U fanServer
2. Check the **front face**: should show `ServerFront_compressed.jpg` tiled 2× and 4× vertically respectively
3. Rotate to view the **rear face**: outlet patches should show `sever_rear_outletMesh.jpg` (honeycomb mesh); the non-patch wall area should show `1U_ServerRearSocket.jpg` (IO panel) tiled per U-count
4. Confirm textures are not yellow-tinted (patches should show full-brightness photos)

- [ ] **Step 3: Verify rack strip textures**

1. Look at the rack **front face**: the left vertical strip beside the servers should show `Power Strip 1.jpg`; the right strip should show `Power Strip 2.jpg`
2. Rotate to view the **back face**: strips should NOT have image textures (still use frame color)

- [ ] **Step 4: Verify ACU return texture**

1. Create an ACU with a return patch
2. The return patch should show the blue chevron filter panel (`ACU Return 3 Panel.png`), stretched to fill the patch
3. Supply patches should still use the existing procedural perforated metal texture

- [ ] **Step 5: Verify baffle/floor-vent grille texture**

1. Create a baffle with a patch → patch should show the slotted grille photo
2. Create a raised floor tile with a vent patch → patch should also show the slotted grille photo
3. Base walls (non-patch areas) should still use existing textures

- [ ] **Step 6: Verify no regressions**

1. Toggle between rack-detail and overview modes — no crashes
2. Drag a server patch — should still work (face rebuilds correctly)
3. Adjust opacity on a rack — textures should blend correctly
4. Open an existing project file — verify textures apply to all racks/ACUs/baffles

- [ ] **Step 7: Final commit if any fixes needed**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "fix(frontend): address visual verification feedback for image textures"
```
