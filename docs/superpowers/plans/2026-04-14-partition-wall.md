# Thick Partition Wall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new thick `partitionWall` object with non-zero thickness, shared front/back through-hole patches, optional Darcy-Forchheimer resistance in the hole thickness volume, automatic dual-side mesh-retention seeds, and direct STL pipeline support.

**Architecture:** Frontend: add a dedicated `partitionWall` model kind, keep the hole list authoritative on the local `front` face, render the visible body as a CSG-subtracted solid, and export helper hole-zone STLs only for resistance-enabled holes. Backend: extend the cfdrun schema and `STLGenRunner` so partition wall body solids are regular wall obstacles, helper hole-zone STLs become snappyHexMesh cellZones, dual-side `none` seeds are auto-generated from geometry + quaternion, and fvOptions reuses the existing rack-leakage Darcy-Forchheimer pipeline.

**Tech Stack:** Three.js r128, `three-csg-ts`, `polygon-clipping`, vanilla JS (`STLGeneratorForOpenFoam/web/STL_generate.html`), Python (`src/backend/aircooling/runner/cfdproj_schema.py`, `src/backend/aircooling/runner/stlgen_runner.py`), OpenFOAM snappyHexMesh + fvOptions.

**Spec:** `docs/superpowers/specs/2026-04-14-partition-wall-design.md`

---

## File Map

| File | Responsibility | Tasks |
|------|----------------|-------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | New model kind, state validation, inspector, rendering, STL export, cfdrun serialization | 1–4 |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | New UI labels/hints in Chinese | 1 |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | New UI labels/hints in English | 1 |
| `src/backend/aircooling/runner/cfdproj_schema.py` | `partitionWalls` schema, hole physics, quaternion geometry, mesh config fields | 5 |
| `src/backend/aircooling/runner/stlgen_runner.py` | Wall BC registration, partition-wall seeds, helper hole cellZones, snappy config, fvOptions config | 6–7 |

---

### Task 1: Add the `partitionWall` model kind and basic editor wiring

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `getDefaultCfd()` (~line 2352)
  - model factories near `createWallModel()` / `createBaffleModel()` (~lines 2431–2485)
  - model-kind allowlists (~lines 2868, 12666, 15164, 15746, 16195)
  - create-button / tree-category arrays (~lines 11175, 11994, 12937–12972)
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

**Context:** This task creates the new object kind without changing rendering/export behavior yet. The object must behave like a box-like model with non-zero depth, not like a `baffle` that forcibly clamps `D = 0`.

- [ ] **Step 1: Add i18n keys**

Append these keys to both locale files:

```json
{
  "category.partition_wall": "隔断墙",
  "inspector.partition_wall_settings": "隔断墙设置 / Partition Wall Settings",
  "inspector.partition_wall_cfd": "贯穿孔 CFD / Through-Hole CFD",
  "label.partition_wall": "隔断墙",
  "label.apply_resistance": "施加阻力",
  "label.leakage_rate": "漏风率",
  "hint.partition_wall_role": "有厚度的实体隔断墙，孔洞为贯穿挖空，墙体内部不保留网格。",
  "hint.partition_wall_leakage": "0% = 最大阻力，100% = 零阻力。删除 patch 才表示完全封闭。",
  "label.partition_wall_refine": "隔断墙"
}
```

- [ ] **Step 2: Add `partitionWall` to `getDefaultCfd()`**

In `getDefaultCfd(kind)`, insert a new branch:

```javascript
            case 'partitionWall':
                return { role: 'partitionWall', patchPhysics: {} };
```

- [ ] **Step 3: Add `createPartitionWallModel()`**

Insert this factory right after `createBaffleModel()`:

```javascript
    function createPartitionWallModel(name = 'Partition_Wall') {
        return {
            id: nextModelId(),
            kind: 'partitionWall',
            name,
            enabled: true,
            showAnchorMarker: true,
            opacity: 1,
            anchor: { x: 0, y: 0, z: 0 },
            base: { W: 1.0, D: 0.1, H: 3.0 },
            transform: getDefaultModelTransform(),
            moveTool: getDefaultModelMoveTool(),
            faces: createFacesState(),
            solidRegions: [],
            customSTLBase64: null,
            customOffset: { x: 0, y: 0, z: 0 },
            cfd: getDefaultCfd('partitionWall')
        };
    }
```

- [ ] **Step 4: Wire the kind into generic model lists, but never zero its thickness**

Update the box-like allowlists to include `partitionWall`:

```javascript
if (model.kind === 'box' || model.kind === 'wall' || model.kind === 'baffle' || model.kind === 'floor' || model.kind === 'partitionWall' || model.kind === 'acu' || ...)
```

Keep the existing baffle clamp as-is, but do **not** apply it to partition walls:

```javascript
            if (model.kind === 'baffle') model.base.D = 0;
```

Do **not** change that line to a broader planar-kind check.

- [ ] **Step 5: Add tree/create-button/category wiring**

In the category arrays, add:

```javascript
                    { kind: 'partitionWall', icon: '🧱', label: t('category.partition_wall') },
```

Add a create handler near `window.addWall` / `window.addBaffle`:

```javascript
    window.addPartitionWall = function() {
        const model = createPartitionWallModel(`Partition_Wall_${state.models.filter(item => item.kind === 'partitionWall').length + 1}`);
        state.models.push(model);
        setSelection('model', model.id);
        rebuildGeometry();
        updateUI();
        logOperation('addPartitionWall', 'Add partition wall', { modelId: model.id }, 'create');
    };
```

Add the matching button in the create panel next to wall/baffle/floor.

- [ ] **Step 6: Verify the object exists and keeps depth**

Refresh the browser, then run in devtools:

```javascript
window.addPartitionWall();
const m = getSelectedModel();
console.log(m.kind, m.base);
```

Expected:

- `m.kind === 'partitionWall'`
- `m.base.D === 0.1`
- the tree shows a new partition-wall category / node

- [ ] **Step 7: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html \
        STLGeneratorForOpenFoam/web/i18n/zh-CN.json \
        STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat(partition-wall): add model kind and basic editor wiring"
```

---

### Task 2: Add partition-wall validation, shared hole state, and patch CFD inspector

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - helper section near `buildFaceIslands()` (~line 5039)
  - model sync / update path near generic dimension updates (~lines 13123+)
  - `renderModelInspector()` / `renderPatchInspector()` (~lines 9085, 10332)

**Context:** Partition walls only allow patches on local `front` / `back`, but both sides must share one authoritative hole list. This task keeps the authoritative list on `faces.front.patches` and forces all editing paths to read/write through that list.

- [ ] **Step 1: Add helper functions for authoritative front-face storage**

Insert these helpers near `buildFaceIslands()`:

```javascript
    function isPartitionWallFace(faceId) {
        return faceId === 'front' || faceId === 'back';
    }

    function getPartitionWallPatchStore(model) {
        if (!model || model.kind !== 'partitionWall') return [];
        if (!model.faces.front) model.faces.front = { patches: [] };
        if (!Array.isArray(model.faces.front.patches)) model.faces.front.patches = [];
        return model.faces.front.patches;
    }

    function getPartitionWallEditableFaceId(model, faceId) {
        if (!model || model.kind !== 'partitionWall') return faceId;
        return isPartitionWallFace(faceId) ? 'front' : null;
    }

    function _partitionPatchToPoly(patch) {
        if (patch.type === 'circle') return circleToPoly(patch.cx, patch.cy, patch.r);
        return rectToPoly(patch.x, patch.y, patch.w, patch.h);
    }
```

- [ ] **Step 2: Add `syncPartitionWallModelState()`**

Insert this helper near the other model sync helpers:

```javascript
    function syncPartitionWallModelState(model) {
        if (!model || model.kind !== 'partitionWall') return;

        model.base.W = Math.max(0.01, Number(model.base.W) || 1.0);
        model.base.H = Math.max(0.01, Number(model.base.H) || 3.0);
        model.base.D = Math.max(0.001, Number(model.base.D) || 0.1);

        ['left', 'right', 'top', 'bottom'].forEach(fid => {
            if (model.faces[fid] && Array.isArray(model.faces[fid].patches)) model.faces[fid].patches = [];
        });
        if (model.faces.back && Array.isArray(model.faces.back.patches)) model.faces.back.patches = [];

        const patches = getPartitionWallPatchStore(model);
        const width = model.base.W;
        const height = model.base.H;

        patches.forEach(patch => {
            if (patch.type === 'circle') {
                patch.cx = Math.max(0, Math.min(width, Number(patch.cx) || width * 0.5));
                patch.cy = Math.max(0, Math.min(height, Number(patch.cy) || height * 0.5));
                patch.r = Math.max(0.005, Number(patch.r) || Math.min(width, height) * 0.15);
                patch.r = Math.min(patch.r, patch.cx, patch.cy, width - patch.cx, height - patch.cy);
            } else {
                patch.x = Math.max(0, Math.min(width, Number(patch.x) || width * 0.2));
                patch.y = Math.max(0, Math.min(height, Number(patch.y) || height * 0.2));
                patch.w = Math.max(0.01, Number(patch.w) || width * 0.3);
                patch.h = Math.max(0.01, Number(patch.h) || height * 0.3);
                patch.w = Math.min(patch.w, width - patch.x);
                patch.h = Math.min(patch.h, height - patch.y);
            }
        });

        for (let i = 0; i < patches.length; i++) {
            for (let j = i + 1; j < patches.length; j++) {
                let overlap = [];
                try { overlap = polygonClipping.intersection(_partitionPatchToPoly(patches[i]), _partitionPatchToPoly(patches[j])); } catch (e) {}
                if (overlap && overlap.length) {
                    throw new Error(`Partition wall hole overlap: ${patches[i].id} vs ${patches[j].id}`);
                }
            }
        }
    }
```

- [ ] **Step 3: Call `syncPartitionWallModelState(model)` from generic update paths**

Where the code currently rebuilds a box/wall/baffle/floor after dimension edits, add:

```javascript
        if (model.kind === 'partitionWall') {
            syncPartitionWallModelState(model);
        }
```

Also call it after patch edits for a partition wall.

- [ ] **Step 4: Route partition-wall patch editing through the front face**

In patch add/delete/update handlers, normalize the face id first:

```javascript
        const actualFaceId = model.kind === 'partitionWall'
            ? getPartitionWallEditableFaceId(model, faceId)
            : faceId;
        if (!actualFaceId) return;
```

Then look up `model.faces[actualFaceId]`.

- [ ] **Step 5: Add a partition-wall model inspector**

Inside `renderModelInspector(prop, model)`, add:

```javascript
        if (model.kind === 'partitionWall') {
            prop.innerHTML = `
                ${renderUnitControls()}
                <div class="prop-group" style="border-color:#16825d">
                    <div class="prop-title" style="color:#16825d">${t('inspector.partition_wall_settings')}</div>
                    <div style="font-size:0.78rem; color:var(--text-dim); line-height:1.55;">${t('hint.partition_wall_role')}</div>
                    <div class="prop-row"><label>${withUnit(t('label.width_w'))}</label><input type="number" step="${getLengthStep()}" value="${formatLength(model.base.W)}" onchange="updateModel('${model.id}', 'W', this.value)"></div>
                    <div class="prop-row"><label>${withUnit(t('label.depth_d'))}</label><input type="number" step="${getLengthStep()}" min="${formatLength(0.001)}" value="${formatLength(model.base.D)}" onchange="updateModel('${model.id}', 'D', this.value)"></div>
                    <div class="prop-row"><label>${withUnit(t('label.height_h'))}</label><input type="number" step="${getLengthStep()}" value="${formatLength(model.base.H)}" onchange="updateModel('${model.id}', 'H', this.value)"></div>
                </div>
                ${anchorControlsForModel(model)}
            `;
            return;
        }
```

- [ ] **Step 6: Add a partition-wall patch inspector**

In `renderPatchInspector(prop, model, faceId, patch)`, add this block before the heat / delete buttons:

```javascript
        if (model.kind === 'partitionWall') {
            const pp = (model.cfd.patchPhysics || {})[patch.id] || { resistanceEnabled: false, leakageRate: 100 };
            html += `<div class="prop-group" style="border-color:#16825d">
                <div class="prop-title" style="color:#16825d">${t('inspector.partition_wall_cfd')}</div>
                <div class="prop-row"><label>${t('label.apply_resistance')}</label>
                    <input type="checkbox" ${pp.resistanceEnabled ? 'checked' : ''} onchange="updatePartitionWallPatchPhysics('${model.id}', '${patch.id}', 'resistanceEnabled', this.checked)"></div>
                <div class="prop-row"><label>${t('label.leakage_rate')}</label>
                    <input type="number" min="0" max="100" step="1" value="${pp.leakageRate != null ? pp.leakageRate : 100}" onchange="updatePartitionWallPatchPhysics('${model.id}', '${patch.id}', 'leakageRate', parseFloat(this.value))"><span>%</span></div>
                <div style="font-size:0.78rem; color:var(--text-dim); line-height:1.55;">${t('hint.partition_wall_leakage')}</div>
            </div>`;
        }
```

Add the handler near the other patch-physics helpers:

```javascript
    window.updatePartitionWallPatchPhysics = function(modelId, patchId, key, value) {
        const model = getModelById(modelId);
        if (!model || model.kind !== 'partitionWall') return;
        pushUndoCheckpoint('partitionWallPatchPhysics');
        if (!model.cfd.patchPhysics) model.cfd.patchPhysics = {};
        if (!model.cfd.patchPhysics[patchId]) model.cfd.patchPhysics[patchId] = { resistanceEnabled: false, leakageRate: 100 };
        if (key === 'leakageRate') value = Math.max(0, Math.min(100, Number(value) || 0));
        model.cfd.patchPhysics[patchId][key] = value;
        rebuildSingleModel(modelId);
        updateUI();
        logOperation('partitionWallPatchPhysics', `Update partition wall patch ${key}`, { modelId, patchId, key, value }, 'modify');
    };
```

- [ ] **Step 7: Verify shared editing semantics**

Create one partition wall, add a front-face patch, then run in devtools:

```javascript
const m = getSelectedModel();
console.log(m.faces.front.patches.length, m.faces.back.patches.length);
console.log(m.cfd.patchPhysics);
```

Expected:

- authoritative patches exist only on `front`
- `back.patches.length === 0`
- the patch inspector shows `Apply Resistance` + `Leakage Rate`

- [ ] **Step 8: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(partition-wall): add shared hole state, validation, and patch CFD inspector"
```

---

### Task 3: Render partition-wall bodies with real through-holes and selectable front/back hole proxies

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - render switch blocks (~lines 7922, 7981, 8099, 17149)
  - selection allowlists (~lines 15164, 15746, 16195)
  - new helpers near `renderWallModel()` / `renderBaffleModel()`

**Context:** The viewport must show actual holes, not decorative decals. At the same time, selection/highlight still needs per-hole proxy meshes on both front and back so the user can click a hole from either side.

- [ ] **Step 1: Add hole-cutter geometry builders**

Insert these helpers near the other rendering helpers:

```javascript
    function buildPartitionWallHoleCutterGeometry(model, patch) {
        const depth = model.base.D + 0.004; // 2 mm overshoot on each side
        if (patch.type === 'circle') {
            const geom = new THREE.CylinderGeometry(patch.r, patch.r, depth, 48);
            geom.translate(patch.cx, model.base.D * 0.5, patch.cy);
            return geom;
        }
        const geom = new THREE.BoxGeometry(patch.w, depth, patch.h);
        geom.translate(patch.x + patch.w * 0.5, model.base.D * 0.5, patch.y + patch.h * 0.5);
        return geom;
    }

    function buildPartitionWallHoleProxyGeometry(patch) {
        if (patch.type === 'circle') return new THREE.CircleGeometry(patch.r, 48);
        return new THREE.PlaneGeometry(patch.w, patch.h);
    }
```

- [ ] **Step 2: Add `renderPartitionWallModel()`**

Insert a dedicated renderer next to `renderWallModel()` / `renderBaffleModel()`:

```javascript
    function renderPartitionWallModel(model, modelGroup, faceLayouts) {
        syncPartitionWallModelState(model);
        const CSG = window.threeCsgTs && window.threeCsgTs.CSG;
        if (!CSG) throw new Error('three-csg-ts is required for partition wall rendering');

        const bodyGeom = new THREE.BoxGeometry(model.base.W, model.base.D, model.base.H);
        bodyGeom.translate(model.base.W * 0.5, model.base.D * 0.5, model.base.H * 0.5);
        const baseMesh = new THREE.Mesh(bodyGeom);
        let bodyCSG = CSG.fromMesh(baseMesh);

        getPartitionWallPatchStore(model).forEach(patch => {
            const cutterMesh = new THREE.Mesh(buildPartitionWallHoleCutterGeometry(model, patch));
            bodyCSG = bodyCSG.subtract(CSG.fromMesh(cutterMesh));
        });

        const finalMesh = CSG.toMesh(bodyCSG, new THREE.Matrix4());
        finalMesh.material = createMaterialForType('wall', {
            color: '#8a919e',
            opacity: model.opacity ?? 1,
            doubleSide: true
        });
        finalMesh.userData = {
            modelId: model.id,
            exportName: `${sanitizeName(model.name)}_body`,
            origColor: '#8a919e',
            isBaseWall: true
        };
        modelGroup.add(finalMesh);
        renderMeshes.push(finalMesh);

        getPartitionWallPatchStore(model).forEach(patch => {
            ['front', 'back'].forEach(side => {
                const proxy = new THREE.Mesh(
                    buildPartitionWallHoleProxyGeometry(patch),
                    new THREE.MeshBasicMaterial({ color: patch.color || '#00cc66', transparent: true, opacity: 0.28, depthWrite: false, side: THREE.DoubleSide })
                );
                const cx = patch.type === 'circle' ? patch.cx : patch.x + patch.w * 0.5;
                const cz = patch.type === 'circle' ? patch.cy : patch.y + patch.h * 0.5;
                proxy.position.set(cx, side === 'front' ? -0.001 : model.base.D + 0.001, cz);
                proxy.rotation.x = side === 'front' ? Math.PI * 0.5 : -Math.PI * 0.5;
                proxy.userData = {
                    modelId: model.id,
                    faceId: side,
                    refIds: [patch.id],
                    origColor: patch.color || '#00cc66',
                    exportName: null,
                    isPartitionWallHoleProxy: true
                };
                modelGroup.add(proxy);
                renderMeshes.push(proxy);
            });
        });
    }
```

- [ ] **Step 3: Route the render switches to `renderPartitionWallModel()`**

Update every render-kind switch like this:

```javascript
            else if (model.kind === 'partitionWall') renderPartitionWallModel(model, modelGroup, faceLayouts);
```

Touch the four main sites:

- `withTemporaryRenderedModels()`
- `buildGeometryData()`
- `rebuildSingleModel()`
- rack-detail context rendering

- [ ] **Step 4: Extend patch selection allowlists**

Where the code currently checks `wall || baffle || floor`, include `partitionWall`:

```javascript
            if (model && (model.kind === 'wall' || model.kind === 'baffle' || model.kind === 'floor' || model.kind === 'partitionWall' || model.kind === 'acu')) {
```

Update the patch-capable / face-capable checks at the other three allowlist sites the same way.

- [ ] **Step 5: Verify the viewport shows true holes**

In the browser:

1. Create a partition wall.
2. Add one rectangular hole and one circular hole.
3. Rotate the wall by yaw/pitch/roll.
4. Confirm:
   - the body remains a solid wall with actual cut-through voids,
   - clicking the hole highlight works from both front and back,
   - the wall still respects the model quaternion.

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(partition-wall): render thick wall body with through-holes and selectable proxies"
```

---

### Task 4: Export `merged_partition_wall.stl`, helper hole-zone STLs, and cfdrun `partitionWalls`

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - STL export group table (~lines 17120+)
  - grouped STL export (`buildGroupedStlMap()`)
  - cfdrun serialization (`exportCFDProject()`, ~lines 25890–26310)

**Context:** Visible partition wall geometry goes to the merged obstacle STL. Resistance-enabled holes additionally emit one helper STL file per hole-zone so backend meshing can create cellZones without parsing the wall body itself.

- [ ] **Step 1: Add a new export group**

In `STL_EXPORT_GROUPS`, insert:

```javascript
        { key: 'partitionWall', filename: 'merged_partition_wall.stl', kinds: ['partitionWall'] },
```

- [ ] **Step 2: Add helper-STL export for resistance-enabled holes**

Insert this helper near `buildGroupedStlMap()`:

```javascript
    function buildPartitionWallHoleZoneStlMap(model) {
        if (!model || model.kind !== 'partitionWall') return {};
        const result = {};
        const mat = new THREE.Matrix4();
        const root = getRenderRootByModelId(model.id);
        if (root) mat.copy(root.matrixWorld);
        else {
            const temp = new THREE.Group();
            temp.position.set(model.anchor.x, model.anchor.y, model.anchor.z);
            applyModelTransformToObject(temp, model);
            temp.updateMatrixWorld(true);
            mat.copy(temp.matrixWorld);
        }

        getPartitionWallPatchStore(model).forEach((patch, index) => {
            const phys = (model.cfd.patchPhysics || {})[patch.id] || { resistanceEnabled: false, leakageRate: 100 };
            if (!phys.resistanceEnabled) return;
            if ((phys.leakageRate != null ? phys.leakageRate : 100) >= 100) return;

            const zoneName = sanitizeName(`${model.name}_hole_${index + 1}_zone`);
            const geom = buildPartitionWallHoleCutterGeometry(model, patch).clone();
            geom.applyMatrix4(mat);
            const mesh = new THREE.Mesh(geom);
            let stl = '';
            stl = exportToStlString(zoneName, mesh, stl, false, null);
            result[`partition_hole_${zoneName}.stl`] = stl;
        });

        return result;
    }
```

- [ ] **Step 3: Merge helper-STL output into grouped export**

At the end of `buildGroupedStlMap()`, add:

```javascript
            state.models.forEach(model => {
                if (!idSet.has(model.id) || model.kind !== 'partitionWall') return;
                Object.assign(result, buildPartitionWallHoleZoneStlMap(model));
            });
```

- [ ] **Step 4: Extend cfdrun root data**

In `exportCFDProject()`, add new root buckets:

```javascript
            partitionWalls: {},
```

and mesh config defaults:

```javascript
                partitionWallRefinementLevel: state.cfdRoom.partitionWallRefinementLevel || [1, 1],
                partitionWallFeatureLevel: state.cfdRoom.partitionWallFeatureLevel || 2,
```

- [ ] **Step 5: Serialize partition walls into `cfdData.partitionWalls`**

Insert a new model-kind branch before the fallback box/wall cases:

```javascript
            } else if (model.kind === 'partitionWall') {
                const bodySolidName = sanitizeName(`${model.name}_body`);
                const holes = getPartitionWallPatchStore(model).map((patch, index) => {
                    const phys = (model.cfd.patchPhysics || {})[patch.id] || { resistanceEnabled: false, leakageRate: 100 };
                    const zoneName = sanitizeName(`${model.name}_hole_${index + 1}_zone`);
                    const enabled = !!phys.resistanceEnabled && (phys.leakageRate != null ? phys.leakageRate : 100) < 100;
                    return {
                        patchId: patch.id,
                        shape: patch.type === 'circle' ? 'circle' : 'rectangle',
                        name: patch.name || `hole_${index + 1}`,
                        zoneName,
                        zoneStlFile: enabled ? `partition_hole_${zoneName}.stl` : null,
                        physics: {
                            resistanceEnabled: !!phys.resistanceEnabled,
                            leakageRate: phys.leakageRate != null ? phys.leakageRate : 100
                        }
                    };
                });

                cfdData.partitionWalls[name] = {
                    stlFile: 'merged_partition_wall.stl',
                    geometry: {
                        anchor: { ...geom.anchor },
                        size: { W: geom.size.W, D: geom.size.D, H: geom.size.H },
                        orientation: geom.orientation,
                        transformQuaternion: cloneModelTransform(model.transform).quaternion
                    },
                    wallSolidNames: [bodySolidName],
                    holes
                };
```

- [ ] **Step 6: Verify export contents**

Use the UI button `▶ 导出 CFD 工程包` and save to `/tmp/partition-wall-demo.zip`, then inspect:

```bash
rm -rf /tmp/partition-wall-demo
mkdir -p /tmp/partition-wall-demo
unzip -o /tmp/partition-wall-demo.zip -d /tmp/partition-wall-demo
find /tmp/partition-wall-demo -maxdepth 3 -type f | sort
jq '.partitionWalls' /tmp/partition-wall-demo/model/cfd_export.json
```

Expected:

- `Mesh/constant/triSurface/merged_partition_wall.stl` exists
- `partition_hole_*.stl` exists only for resistance-enabled holes with rate `< 100`
- `model/cfd_export.json` contains a `partitionWalls` object

- [ ] **Step 7: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(partition-wall): export merged body STL, helper hole zones, and cfdrun data"
```

---

### Task 5: Extend `cfdproj_schema.py` for partition walls and mesh config

**Files:**
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py`

**Context:** Backend validation needs first-class models for partition walls instead of reading anonymous `dict`s.

- [ ] **Step 1: Add quaternion and partition-wall models**

Insert these classes near `SimpleGeometry` / `CFDExportBaffle`:

```python
class Quaternion4(CFDBaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


class PartitionWallHolePhysics(CFDBaseModel):
    resistanceEnabled: bool = False
    leakageRate: float = 100.0


class PartitionWallHole(CFDBaseModel):
    patchId: str
    shape: Literal["rectangle", "circle"] = "rectangle"
    name: str = ""
    zoneName: str = ""
    zoneStlFile: Optional[str] = None
    physics: PartitionWallHolePhysics = Field(default_factory=PartitionWallHolePhysics)


class PartitionWallGeometry(SimpleGeometry):
    transformQuaternion: Quaternion4 = Field(default_factory=Quaternion4)


class CFDExportPartitionWall(CFDBaseModel):
    stlFile: str
    geometry: PartitionWallGeometry = Field(default_factory=PartitionWallGeometry)
    wallSolidNames: List[str] = Field(default_factory=list)
    holes: List[PartitionWallHole] = Field(default_factory=list)
```

- [ ] **Step 2: Extend `MeshConfig`**

In `MeshConfig`, add:

```python
    partitionWallRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    partitionWallFeatureLevel: int = 2
```

- [ ] **Step 3: Extend the root `CFDExport` model**

In `CFDExport`, add:

```python
    partitionWalls: Dict[str, CFDExportPartitionWall] = Field(default_factory=dict)
```

- [ ] **Step 4: Add a simple validation smoke check**

Run:

```bash
python - <<'PY'
from dcdtuni.runner.cfdproj_schema import CFDExport

payload = {
    "version": "1.0",
    "room": {"height": 3.0, "plane": [], "locationInMesh": {"x": 1, "y": 1, "z": 1}},
    "partitionWalls": {
        "Partition_1": {
            "stlFile": "merged_partition_wall.stl",
            "geometry": {
                "anchor": {"x": 0, "y": 0, "z": 0},
                "size": {"W": 2.0, "D": 0.1, "H": 3.0},
                "orientation": 0.0,
                "transformQuaternion": {"x": 0, "y": 0, "z": 0, "w": 1}
            },
            "wallSolidNames": ["Partition_1_body"],
            "holes": [{
                "patchId": "p1",
                "shape": "rectangle",
                "name": "hole_1",
                "zoneName": "Partition_1_hole_1_zone",
                "zoneStlFile": "partition_hole_Partition_1_hole_1_zone.stl",
                "physics": {"resistanceEnabled": True, "leakageRate": 40}
            }]
        }
    }
}

validated = CFDExport.model_validate(payload)
print(validated.partitionWalls["Partition_1"].holes[0].physics.leakageRate)
PY
```

Expected output:

```text
40.0
```

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/cfdproj_schema.py
git commit -m "feat(partition-wall): add cfdrun schema models and mesh config fields"
```

---

### Task 6: Register partition wall surfaces, dual-side `none` seeds, and partition-wall refinement in `STLGenRunner`

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py`

**Context:** The visible wall body is a normal obstacle surface. It does not use createBaffles. The backend therefore needs three things: wall BC registration, geometry-based dual-side `none` seeds, and dedicated refinement/feature levels for `merged_partition_wall.stl`.

- [ ] **Step 1: Add quaternion rotation helpers**

Insert these helpers near the other vector helpers:

```python
def _quat_normalize(q: Dict[str, float]) -> Tuple[float, float, float, float]:
    qx = float(q.get("x", 0.0))
    qy = float(q.get("y", 0.0))
    qz = float(q.get("z", 0.0))
    qw = float(q.get("w", 1.0))
    norm = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
    if norm <= _GEOM_EPS:
        return (0.0, 0.0, 0.0, 1.0)
    return (qx / norm, qy / norm, qz / norm, qw / norm)


def _quat_rotate(vec: Vec3, quat: Dict[str, float]) -> Vec3:
    qx, qy, qz, qw = _quat_normalize(quat)
    vx, vy, vz = vec
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )
```

- [ ] **Step 2: Register partition wall bodies as wall BCs**

In `_build_bridge_bc()`, after the room-wall loop or immediately before it, add:

```python
        for part_name, part in data.get("partitionWalls", {}).items():
            for solid in part.get("wallSolidNames", []):
                if solid:
                    registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
```

- [ ] **Step 3: Add automatic dual-side `none` seeds**

Insert this helper near `_build_leakage_cellzone_seed_entries()`:

```python
    def _build_partition_wall_seed_entries(self) -> List[Tuple[Vec3, str]]:
        entries: List[Tuple[Vec3, str]] = []
        for part in self._data.get("partitionWalls", {}).values():
            geom = part.get("geometry", {})
            anchor = geom.get("anchor", {"x": 0.0, "y": 0.0, "z": 0.0})
            size = geom.get("size", {})
            quat = geom.get("transformQuaternion", {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0})

            w = float(size.get("W", 1.0))
            d = max(0.001, float(size.get("D", 0.1)))
            h = float(size.get("H", 3.0))

            local_points = [
                (w * 0.5, -0.002, h * 0.5),
                (w * 0.5, d + 0.002, h * 0.5),
            ]
            for local in local_points:
                rotated = _quat_rotate(local, quat)
                world = (
                    float(anchor.get("x", 0.0)) + rotated[0],
                    float(anchor.get("y", 0.0)) + rotated[1],
                    float(anchor.get("z", 0.0)) + rotated[2],
                )
                entries.append((world, "none"))
        return entries
```

Then include it in `_build_locations_in_mesh_entries()`:

```python
        entries.extend(self._build_partition_wall_seed_entries())
```

- [ ] **Step 4: Add partition-wall refinement + feature levels**

At the top of `_generate_snappy_hex_mesh_dict()`, add:

```python
        rl_partition = mc.get("partitionWallRefinementLevel", rl_box)
```

In the features section, add:

```python
        feat_partition = _feat_int(mc.get("partitionWallFeatureLevel"), feat_box)
```

and route `merged_partition_wall.stl`:

```python
            elif filename == "merged_partition_wall.stl":
                feat_level = feat_partition
```

In the refinement section, add:

```python
                elif filename == "merged_partition_wall.stl":
                    rl = rl_partition
```

so the generated solid entries use:

```python
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_partition[0]} {rl_partition[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
```

- [ ] **Step 5: Verify seeds and refinement appear in generated dictionaries**

Use a case exported to `/tmp/partition-wall-demo` and run:

```bash
cp /tmp/partition-wall-demo/model/cfd_export.json /tmp/partition-wall-demo/model/project.cfdrun
conda run -n dcdtuni python -m dcdtuni.runner.run_cfd --from-cfdrun /tmp/partition-wall-demo/model/project.cfdrun --mesh-only
rg -n "merged_partition_wall|none" /tmp/partition-wall-demo/Mesh/system/snappyHexMeshDict
```

Expected:

- `merged_partition_wall` appears in geometry / features / refinement surfaces
- at least two extra `((x y z) none)` entries attributable to the partition wall

- [ ] **Step 6: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(partition-wall): register wall bodies, auto dual-side seeds, and refinement"
```

---

### Task 7: Turn helper hole STLs into cellZones and merge partition-hole resistance into fvOptions

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py`

**Context:** This task is the CFD core: helper hole STLs become `faceType internal + cellZone` surfaces, get named `locationsInMesh` seeds, and then feed the same Darcy-Forchheimer `fvOptions` machinery used by rack leakage.

- [ ] **Step 1: Treat `partition_hole_*.stl` like leakage-style internal-zone files**

In `_generate_snappy_hex_mesh_dict()`, add a branch above the regular solid-entry branch:

```python
            if filename.startswith("partition_hole_"):
                san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_leakage[0]} {rl_leakage[1]});
        faceType internal;
        cellZone {san};
        cellZoneInside inside;
    }}""")
                continue
```

- [ ] **Step 2: Add named cellZone seeds for partition-hole helpers**

Insert this helper near `_build_leakage_cellzone_seed_entries()`:

```python
    def _build_partition_hole_cellzone_seed_entries(self) -> List[Tuple[Vec3, str]]:
        entries: List[Tuple[Vec3, str]] = []
        for part in self._data.get("partitionWalls", {}).values():
            for hole in part.get("holes", []):
                phys = hole.get("physics", {})
                if not phys.get("resistanceEnabled", False):
                    continue
                rate = float(phys.get("leakageRate", 100.0))
                if rate >= 100.0:
                    continue
                zone_name = _sanitize(hole.get("zoneName", ""))
                zone_file = hole.get("zoneStlFile")
                if not zone_name or not zone_file:
                    continue
                zone_path = self.tri_surface_dir / zone_file
                if not zone_path.exists():
                    continue
                centroid = _compute_stl_solid_centroid(zone_path, zone_name)
                if centroid is None:
                    continue
                entries.append((centroid, zone_name))
        return entries
```

Then include it in `_build_locations_in_mesh_entries()`:

```python
        entries.extend(self._build_partition_hole_cellzone_seed_entries())
```

- [ ] **Step 3: Build partition-hole fvOptions config**

Insert a new helper after `_build_leakage_fvoptions_config()`:

```python
    def _build_partition_hole_fvoptions_config(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {}
        for part in self._data.get("partitionWalls", {}).values():
            geom = part.get("geometry", {})
            quat = geom.get("transformQuaternion", {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0})
            depth = max(0.001, float(geom.get("size", {}).get("D", 0.1)))

            thickness_axis = _quat_rotate((0.0, 1.0, 0.0), quat)
            e1 = _vec_normalize(thickness_axis)
            # pick a stable in-plane axis that is not parallel to e1
            tmp = (0.0, 0.0, 1.0) if abs(e1[2]) < 0.9 else (1.0, 0.0, 0.0)
            e2 = _vec_normalize(_vec_cross(tmp, e1))

            for hole in part.get("holes", []):
                phys = hole.get("physics", {})
                if not phys.get("resistanceEnabled", False):
                    continue
                rate = float(phys.get("leakageRate", 100.0))
                if rate >= 100.0:
                    continue

                sigma = max(0.0, min(1.0, rate / 100.0))
                d_coeff, f_coeff = _darcy_forchheimer(sigma, depth)
                zone_name = _sanitize(hole.get("zoneName", ""))
                zone_file = hole.get("zoneStlFile")
                if not zone_name or not zone_file:
                    continue

                zone_path = self.tri_surface_dir / zone_file
                if not zone_path.exists():
                    continue
                centroid = _compute_stl_solid_centroid(zone_path, zone_name)
                if centroid is None:
                    continue

                config[zone_name] = type("ZoneCfg", (), {
                    "cellzone_name": zone_name,
                    "sigma": sigma,
                    "d": f"({d_coeff:.6g} 0 0)",
                    "f": f"({f_coeff:.6g} 0 0)",
                    "origin": f"({centroid[0]:.6f} {centroid[1]:.6f} {centroid[2]:.6f})",
                    "e1": f"({e1[0]:.6g} {e1[1]:.6g} {e1[2]:.6g})",
                    "e2": f"({e2[0]:.6g} {e2[1]:.6g} {e2[2]:.6g})",
                })()
        return config
```

- [ ] **Step 4: Merge partition-hole config into the existing `generate_fv_options()` call**

In `run_mesh_only()` / case setup where fvOptions is generated, replace:

```python
        leakage_zones = self._build_leakage_fvoptions_config()
        generate_fv_options(mesh_dir, leakage_zones)
```

with:

```python
        leakage_zones = self._build_leakage_fvoptions_config()
        leakage_zones.update(self._build_partition_hole_fvoptions_config())
        generate_fv_options(mesh_dir, leakage_zones)
```

- [ ] **Step 5: End-to-end mesh verification**

Export a case with:

- one full-height partition wall,
- one hole with `resistanceEnabled = false`,
- one hole with `resistanceEnabled = true`, `leakageRate = 40`.

Then run:

```bash
cp /tmp/partition-wall-demo/model/cfd_export.json /tmp/partition-wall-demo/model/project.cfdrun
conda run -n dcdtuni python -m dcdtuni.runner.run_cfd --from-cfdrun /tmp/partition-wall-demo/model/project.cfdrun --mesh-only
rg -n "Partition_.*_zone|explicitPorositySource" /tmp/partition-wall-demo/Mesh/system/fvOptions
find /tmp/partition-wall-demo/Mesh/constant/triSurface -maxdepth 1 -type f | sort | rg "partition"
```

Expected:

- helper `partition_hole_*.stl` file exists for the 40% hole only
- `fvOptions` contains a `Partition_*_zone_porosity` block
- no fvOptions block is emitted for holes with `resistanceEnabled = false` or `leakageRate = 100`

- [ ] **Step 6: Regression check for old cases**

Run an existing non-partition-wall case:

```bash
conda run -n dcdtuni python -m dcdtuni.runner.run_cfd --from-cfdrun /mnt/lv/litianyu/mycode/dcdtuni/case/base_leakage/model/Base_leakage.cfdrun --mesh-only
```

Expected:

- runner completes without referencing `partitionWalls`
- existing rack / baffle / floor workflows remain unchanged

- [ ] **Step 7: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(partition-wall): add hole cellzones and Darcy fvOptions integration"
```
