# Floor Polygon Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Floor models to use arbitrary polygon boundaries instead of only rectangles.

**Architecture:** Add optional `model.polygon` (array of [x,y] vertices) to Floor. When present, `renderPlanarModelWithPatches` uses polygon as base shape instead of `rectToPoly(0,0,W,H)`. `createModelFromRaw` preserves polygon from raw data. Inspector shows read-only polygon info. STL export unchanged (ShapeGeometry → BufferGeometry → appendMeshFacets).

**Tech Stack:** Three.js r128 (ShapeGeometry with earcut triangulation), polygon-clipping library (already in project)

**Spec:** `docs/superpowers/specs/2026-04-04-floor-polygon-design.md`

---

### Task 1: Data model — polygon property in createModelFromRaw + normalizeState

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add polygon preservation in `createModelFromRaw` (~line 2306)**

Find `function createModelFromRaw`. In the model object literal (after `cutouts:` line), add:

```javascript
            polygon: Array.isArray(rawModel.polygon) ? rawModel.polygon.map(v => [v[0], v[1]]) : null,
```

- [ ] **Step 2: Add `syncFloorBaseFromPolygon` helper and `computePolygonArea` helper**

Insert after `createModelFromRaw` function:

```javascript
    function syncFloorBaseFromPolygon(model) {
        if (model.kind !== 'floor' || !model.polygon || model.polygon.length < 3) return;
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        model.polygon.forEach(([x, y]) => {
            if (x < minX) minX = x; if (x > maxX) maxX = x;
            if (y < minY) minY = y; if (y > maxY) maxY = y;
        });
        model.base.W = maxX - minX;
        model.base.D = maxY - minY;
    }

    function computePolygonArea(polygon) {
        if (!polygon || polygon.length < 3) return 0;
        let area = 0;
        for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
            area += polygon[j][0] * polygon[i][1] - polygon[i][0] * polygon[j][1];
        }
        return Math.abs(area) / 2;
    }
```

- [ ] **Step 3: Add polygon validation in `normalizeState` (~line 2435)**

Find the groups normalization block in `normalizeState` (starts with `if (!next.groups)`). BEFORE that block, add:

```javascript
        // Floor polygon validation
        next.models.forEach(m => {
            if (m.kind === 'floor' && m.polygon != null) {
                if (!Array.isArray(m.polygon) || m.polygon.length < 3
                    || !m.polygon.every(v => Array.isArray(v) && v.length >= 2
                        && Number.isFinite(v[0]) && Number.isFinite(v[1]))) {
                    m.polygon = null;
                }
            }
            if (m.kind === 'floor' && m.polygon) syncFloorBaseFromPolygon(m);
        });
```

- [ ] **Step 4: Add polygon to `_modelFingerprint` for undo incremental detection**

Find `function _modelFingerprint`. Add polygon length to the fingerprint string:

Change:
```javascript
return `${m.anchor.x},...|${(m.cutouts||[]).length}`;
```
to include polygon:
```javascript
return `${m.anchor.x},${m.anchor.y},${m.anchor.z}|${m.base.W},${m.base.D},${m.base.H}|${m.enabled}|${m.opacity}|${Object.keys(m.faces||{}).length}|${(m.cutouts||[]).length}|${(m.polygon||[]).length}`;
```

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(polygon-floor): add polygon property to floor data model

createModelFromRaw preserves polygon from raw data.
syncFloorBaseFromPolygon syncs base.W/D from polygon bounding box.
computePolygonArea implements shoelace formula.
normalizeState validates polygon arrays.
_modelFingerprint includes polygon length for undo detection."
```

---

### Task 2: Rendering — polygon floor in renderPlanarModelWithPatches

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Modify `renderPlanarModelWithPatches` to use polygon when available (~line 5360)**

Find the line:
```javascript
        let wallPoly = rectToPoly(0, 0, W, H);
```

Replace with:

```javascript
        let wallPoly;
        if (model.polygon && model.polygon.length >= 3 && (primaryFaceId === 'top' || primaryFaceId === 'bottom')) {
            // Polygon floor/ceiling: use polygon vertices as base shape
            wallPoly = [model.polygon.map(v => [v[0], v[1]])];
        } else {
            wallPoly = rectToPoly(0, 0, W, H);
        }
```

Note: `wallPoly` is in polygon-clipping format: `[[[x,y],[x,y],...]]` (array of rings). `model.polygon` is `[[x,y],[x,y],...]` (single ring), so we wrap it in an outer array.

- [ ] **Step 2: Verify edge helpers work with polygon floors**

`createWallBaffleEdgeHelpers` creates edge outlines. Check if it uses `layout.w`/`layout.h` to draw rectangular edges. If so, it needs a polygon path for polygon floors.

Find `createWallBaffleEdgeHelpers`. If it draws edges based on the rectangular w/h, add a polygon branch:

After the existing edge line creation, add a check:

```javascript
        // For polygon floors, override rectangular edge outline with polygon outline
        if (model.polygon && model.polygon.length >= 3 && (primaryFaceId === 'top' || primaryFaceId === 'bottom')) {
            // Remove rectangular edge helpers that were just created
            // and replace with polygon outline
            const polyPoints = model.polygon.map(([x, y]) => new THREE.Vector3(x, y, 0));
            polyPoints.push(polyPoints[0].clone()); // close the loop
            const lineGeo = new THREE.BufferGeometry().setFromPoints(polyPoints);
            const lineMat = new THREE.LineBasicMaterial({ color: 0xa5b4c8, transparent: true, opacity: 0.5 });
            const polyLine = new THREE.LineLoop(lineGeo, lineMat);
            polyLine.position.copy(layout.origin);
            modelGroup.add(polyLine);
        }
```

Note: This step requires reading the actual `createWallBaffleEdgeHelpers` implementation to determine the exact insertion point. The subagent should read the function and adapt accordingly.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(polygon-floor): render polygon floors via ShapeGeometry

renderPlanarModelWithPatches uses model.polygon as base shape when
available. Falls back to rectToPoly for traditional rectangular floors.
Edge helpers draw polygon outline for polygon floors."
```

---

### Task 3: Inspector — read-only polygon info for floor

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add polygon info section in `renderWallBaffleInspector` for floor (~line 7088)**

Find the floor-specific section in `renderWallBaffleInspector` (the `if (isFloor)` block that builds `sizeHtml`). Replace the block with:

```javascript
        if (isFloor) {
            if (model.polygon && model.polygon.length >= 3) {
                // Polygon floor: read-only polygon info
                const area = computePolygonArea(model.polygon);
                sizeHtml = `<div class="prop-row"><label>${t('label.floor_shape')}</label><span class="badge" style="border-color:rgba(0,212,255,0.4);color:var(--accent3)">${t('label.polygon_vertices', {count: model.polygon.length})}</span></div>
                    <div class="prop-row"><label>${t('label.floor_area')}</label><span>${formatLength(area)} ${getLengthUnitLabel()}²</span></div>
                    <div class="prop-row"><label>${withUnit(t('label.bbox_w'))}</label><span>${formatLength(model.base.W)}</span></div>
                    <div class="prop-row"><label>${withUnit(t('label.bbox_d'))}</label><span>${formatLength(model.base.D)}</span></div>
                    <div style="font-size:0.78rem; color:var(--text-dim); line-height:1.55; margin-top:4px;">${t('hint.polygon_floor_edit')}</div>`;
            } else {
                sizeHtml = `<div class="prop-row"><label>${withUnit(t('label.length_w_x'))}</label><input type="number" step="${getLengthStep()}" value="${formatLength(model.base.W)}" onchange="updateModel('${model.id}', 'W', this.value)"></div>
                    <div class="prop-row"><label>${withUnit(t('label.width_d_y'))}</label><input type="number" step="${getLengthStep()}" value="${formatLength(model.base.D)}" onchange="updateModel('${model.id}', 'D', this.value)"></div>
                    <div style="font-size:0.78rem; color:var(--text-dim); line-height:1.55;">${t('hint.floor_zero_thickness')}</div>`;
            }
        }
```

- [ ] **Step 2: Add i18n keys**

In `web/i18n/zh-CN.json`, add:
```json
  "label.floor_shape": "地板形状",
  "label.polygon_vertices": "多边形 ({count} 个顶点)",
  "label.floor_area": "面积",
  "label.bbox_w": "包围盒宽度 (W)",
  "label.bbox_d": "包围盒深度 (D)",
  "hint.polygon_floor_edit": "地板形状由多边形顶点定义。请使用 2D 平面编辑器修改形状。",
```

In `web/i18n/en-US.json`, add:
```json
  "label.floor_shape": "Floor Shape",
  "label.polygon_vertices": "Polygon ({count} vertices)",
  "label.floor_area": "Area",
  "label.bbox_w": "Bounding Box W",
  "label.bbox_d": "Bounding Box D",
  "hint.polygon_floor_edit": "Floor shape defined by polygon vertices. Use the 2D Floor Plan Editor to modify.",
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat(polygon-floor): read-only polygon info in floor Inspector

Polygon floors show vertex count, area (shoelace formula), and
bounding box W/D as read-only values. Hint directs users to 2D editor.
Rectangular floors show existing editable W/D fields unchanged.
i18n keys added for zh-CN and en-US."
```

---

### Task 4: Manual testing with Tri_angle case

- [ ] **Step 1: Start server**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir dctwin/case &
```

- [ ] **Step 2: Test programmatically via browser console**

Load any project, then in browser DevTools console:

```javascript
// Find a floor model
const floor = state.models.find(m => m.kind === 'floor');
// Set polygon (triangle)
floor.polygon = [[0,0],[10,0],[5,8.66]];
syncFloorBaseFromPolygon(floor);
rebuildGeometry();
// Verify: should see triangular floor in 3D viewport
```

- [ ] **Step 3: Test with rectangle polygon**

```javascript
floor.polygon = [[0,0],[10,0],[10,8],[0,8]];
syncFloorBaseFromPolygon(floor);
rebuildGeometry();
// Verify: renders identically to W=10, D=8 rectangle
```

- [ ] **Step 4: Test L-shape (concave)**

```javascript
floor.polygon = [[0,0],[6,0],[6,3],[3,3],[3,6],[0,6]];
syncFloorBaseFromPolygon(floor);
rebuildGeometry();
// Verify: L-shaped floor renders correctly
```

- [ ] **Step 5: Test backward compatibility**

```javascript
floor.polygon = null;
rebuildGeometry();
// Verify: falls back to rectangular floor (base.W × base.D)
```

- [ ] **Step 6: Test save/load**

Save project → reload → verify polygon floor persists.

- [ ] **Step 7: Test STL export**

Export STL with polygon floor → open in text editor → verify triangulated facets with correct normals.

- [ ] **Step 8: Test undo/redo**

Set polygon → Ctrl+Z → verify floor reverts to previous state.
