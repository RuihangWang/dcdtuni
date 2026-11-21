# P1: Floor Polygon Support — Design Spec

**Date**: 2026-04-04
**File**: `STLGeneratorForOpenFoam/web/STL_generate.html`
**Goal**: Allow Floor models to have arbitrary polygon boundaries (triangles, L-shapes, etc.) instead of only rectangles, as the foundation for non-rectangular room support.

---

## 1. Data Model

Add optional `polygon` property to Floor model:

```javascript
model.polygon = [
    [0, 0],        // vertex 1 (local x, y relative to anchor)
    [10, 0],       // vertex 2
    [5, 8.66]      // vertex 3
];
// null or undefined = use traditional base.W × base.D rectangle
```

**Rules**:
- Coordinates are **local** (relative to `model.anchor`), in the XY plane
- Minimum 3 vertices, auto-closed (last vertex connects to first)
- Vertex order: counter-clockwise (Three.js ShapeGeometry default for +Z facing normal)
- Supports convex AND concave polygons (Three.js earcut triangulation handles concave shapes)
- `polygon = null` falls back to `base.W × base.D` rectangle (full backward compatibility)
- Floor and ceiling (floor copy at z=height) share the same polygon
- JSON-serializable, saved/loaded with `.cfdproj` project files
- Part of undo snapshots automatically (it's on the model object in `state`)

**base.W / base.D auto-sync**:
- When `polygon` is set, `base.W` and `base.D` are automatically updated to the polygon's axis-aligned bounding box dimensions
- This ensures existing code that reads `base.W`/`base.D` (AABB calculations, tree badges, etc.) works correctly
- Users do not manually edit W/D when polygon is set — values are derived

---

## 2. State Normalization

In `normalizeState()`, add after existing floor normalization:

```javascript
// Polygon validation
if (model.kind === 'floor' && model.polygon != null) {
    if (!Array.isArray(model.polygon) || model.polygon.length < 3
        || !model.polygon.every(v => Array.isArray(v) && v.length === 2
            && Number.isFinite(v[0]) && Number.isFinite(v[1]))) {
        model.polygon = null;  // invalid → fall back to rectangle
    }
}
```

Also add a helper to sync base.W/D from polygon bounding box:

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
```

Called after polygon is set or modified.

---

## 3. Rendering

Modify `renderFloorModel` to support polygon:

```javascript
if (model.polygon && model.polygon.length >= 3) {
    const shape = new THREE.Shape();
    shape.moveTo(model.polygon[0][0], model.polygon[0][1]);
    for (let i = 1; i < model.polygon.length; i++) {
        shape.lineTo(model.polygon[i][0], model.polygon[i][1]);
    }
    shape.closePath();
    const geo = new THREE.ShapeGeometry(shape);
    // ShapeGeometry produces XY-plane faces with +Z normals — correct for floor top face
} else {
    // Existing rectangle path
    const geo = new THREE.PlaneGeometry(model.base.W, model.base.D);
}
```

**Edge wireframe**: For polygon mode, edge outline follows polygon vertices (not rectangular border). Use `THREE.EdgesGeometry` on the ShapeGeometry — it will trace the polygon outline.

**Face structure**: Floor faces remain `top` and `bottom`. Patches on `top` face use local XY coordinates — unchanged.

**Existing floor features** (cutouts, patches, opacity) continue to work — they operate on the face geometry regardless of whether it's rectangular or polygonal.

---

## 4. STL Export

**No changes needed.** `appendMeshFacets` reads BufferGeometry position/index attributes directly. `THREE.ShapeGeometry` produces a valid triangulated BufferGeometry that is processed identically to rectangular `PlaneGeometry`.

Normal direction:
- Floor `top` face: +Z normal (ShapeGeometry default) ✓
- Ceiling `bottom` face: -Z normal (handled by existing floor export flip logic) ✓

---

## 5. Inspector (Read-Only)

When a polygon Floor is selected, the Inspector shows:
- **Polygon info**: vertex count + area (read-only, computed from polygon)
- W/D fields become read-only (derived from polygon bounding box)
- Hint text: "Floor shape is defined by polygon. Edit in 2D Floor Plan Editor."

When polygon is null (traditional rectangular floor), Inspector is unchanged.

```javascript
// In renderFloorInspector / renderWallBaffleInspector for floor:
if (model.polygon && model.polygon.length >= 3) {
    const area = computePolygonArea(model.polygon);
    html += `<div class="prop-row"><label>Shape</label><span class="badge">Polygon (${model.polygon.length} vertices)</span></div>`;
    html += `<div class="prop-row"><label>Area</label><span>${formatLength(area)} ${getLengthUnitLabel()}²</span></div>`;
    html += `<div style="font-size:0.78rem; color:var(--text-dim); margin-top:4px;">Shape defined by polygon vertices. Use 2D editor to modify.</div>`;
}
```

Area computation (shoelace formula):
```javascript
function computePolygonArea(polygon) {
    let area = 0;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        area += polygon[j][0] * polygon[i][1] - polygon[i][0] * polygon[j][1];
    }
    return Math.abs(area) / 2;
}
```

---

## 6. Backward Compatibility

- Existing `.cfdproj` files without `polygon` field: `model.polygon` is `undefined` → rectangle mode
- `normalizeState` ensures invalid polygons become `null`
- All code paths that create Floor models (`createDefaultFloorModel`, `cloneModelForDuplication`, etc.) do NOT set polygon — new floors are always rectangular by default
- Polygon is only set by P3 (2D editor) or programmatic API

---

## 7. Future Integration Points (Not In P1 Scope)

- **P2**: Wall miter at polygon corners
- **P3**: 2D editor sets `model.polygon` on Floor/Ceiling models; 3D wall moves sync back to `state.roomPolygon` → polygon vertices
- **P3**: Boundary walls are linked to polygon edges — moving wall in 3D updates corresponding polygon vertex

These are documented here for context but NOT implemented in P1.

---

## Testing

1. Create a Floor → verify it's rectangular (polygon = null), normal behavior
2. Programmatically set `model.polygon = [[0,0],[10,0],[10,8],[0,8]]` (rectangle) → verify renders identically to W=10, D=8
3. Set `model.polygon = [[0,0],[10,0],[5,8.66]]` (triangle) → verify triangular floor renders correctly
4. Set L-shape polygon → verify concave polygon renders correctly
5. Copy a polygon floor → verify polygon is cloned correctly
6. Save/load project with polygon floor → verify polygon persists
7. Undo/redo polygon changes → verify correct state restoration
8. STL export with polygon floor → verify triangulated facets in output, watertight normals
9. Inspector shows vertex count and area for polygon floor
10. Old .cfdproj files without polygon field → verify normal rectangular behavior (backward compat)
