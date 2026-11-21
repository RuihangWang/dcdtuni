# P3a: 2D Floor Plan Editor (MVP) — Design Spec

**Date**: 2026-04-04
**Files**: `STLGeneratorForOpenFoam/web/room_2d_editor.js` (new), `STLGeneratorForOpenFoam/web/STL_generate.html` (entry point + integration)
**Goal**: A 2D canvas editor for drawing room floor plans. Straight-line polygons only (MVP). "Apply" converts to Wall + Floor + Ceiling 3D models using existing geometry assets.

---

## 1. File Structure

**New file**: `web/room_2d_editor.js` (~800-1200 lines)
- Loaded via `<script src="room_2d_editor.js">` in `STL_generate.html`
- Shares `state` object, `window.*` functions, and DOM with main page
- All 2D editor functions namespaced under `window.room2d = {}`

**Entry point**: Replace "导入水密曲面 STL 进行水转印" button with "房间 2D 平面设计" button.

---

## 2. State Data

```javascript
state.roomPolygon = {
    vertices: [[0,0], [10,0], [10,8], [0,8]],  // m, counter-clockwise
    height: 3.0       // room height (m) — wall H and ceiling Z
};
// null = no room polygon defined
```

- Saved/loaded with `.cfdproj`
- Part of undo snapshots
- `normalizeState` validates: if present, must have `vertices` array with >= 3 `[number, number]` entries, `height` must be finite > 0

Generated models are tagged: `model.isRoomBoundary = true` — used to identify and replace on re-apply.

---

## 3. Mode Switching

### Enter 2D Mode
1. Push undo checkpoint
2. Hide: 3D viewport (`renderer.domElement`), 3D toolbar, right Inspector content
3. Show: 2D canvas (full center area), 2D toolbar (top), 2D right panel
4. Left model tree: stays visible (unchanged)
5. If `state.roomPolygon` exists, load vertices onto canvas
6. If not, start with empty canvas (ready to draw)

### Exit 2D Mode ("应用转换为 3D 房间")
1. If polygon not closed (< 3 vertices), warn and stay in 2D
2. Save vertices + height to `state.roomPolygon`
3. Generate Wall + Floor + Ceiling models (see Section 6)
4. Hide 2D canvas/toolbar/panel, restore 3D viewport/toolbar/Inspector
5. Call `rebuildGeometry()` + `fitCameraToScene()`

### Cancel (exit without applying)
- "取消" button — restores previous state, hides 2D UI

---

## 4. 2D Canvas (HTML5 Canvas 2D API)

### Visual Elements
- Dark background (#1a1e26) matching app theme
- Grid lines: major (every 1m) + minor (every 0.1m), configurable spacing
- Coordinate axes: X (red), Y (green) with labels
- Polygon edges: cyan (#00d4ff) solid lines, 2px
- Vertices: cyan circles, 6px radius, white fill when selected
- Current drawing line: dashed line from last vertex to mouse cursor
- Close indicator: when mouse near first vertex, show snap circle

### View Controls
- Mouse wheel: zoom (centered on cursor)
- Middle button / right button drag: pan
- Zoom range: 0.1x to 50x
- View state: `{ offsetX, offsetY, scale }` — transforms between canvas pixels and world meters

### Coordinate Transform
```javascript
// World (meters) → Canvas (pixels)
canvasX = (worldX - offsetX) * scale + canvas.width / 2
canvasY = canvas.height / 2 - (worldY - offsetY) * scale  // Y flipped

// Canvas → World (inverse)
worldX = (canvasX - canvas.width / 2) / scale + offsetX
worldY = (canvas.height / 2 - canvasY) / scale + offsetY
```

---

## 5. Drawing & Editing Interactions

### Drawing Mode (polygon not yet closed)
| Action | Behavior |
|--------|----------|
| Left click empty area | Add vertex at cursor position |
| Left click near first vertex | Close polygon (connect last to first) |
| Enter key | Close polygon |
| Right click / Escape | Cancel current drawing, remove all vertices |
| Mouse move | Preview line from last vertex to cursor |

### Editing Mode (polygon closed)
| Action | Behavior |
|--------|----------|
| Left click vertex | Select vertex (highlight, show coords in panel) |
| Left click edge | Select edge (highlight, show length/angle in panel) |
| Left click empty | Deselect |
| Drag vertex | Move vertex (with snap if enabled) |
| Drag edge | Move edge (both endpoints translate equally) |
| Delete / Backspace | Remove selected vertex (polygon auto-reconnects; if < 3 vertices, delete all) |
| Double-click edge | Insert new vertex at click point (split edge) |

### Snap System (2D)
- **Grid snap**: vertex snaps to nearest grid intersection (when enabled)
- **Vertex snap**: vertex snaps to other existing vertices within radius
- **Edge alignment**: horizontal (dy=0) or vertical (dx=0) constraint when edge is nearly H/V (within 5°)
- Toggle buttons in 2D toolbar

---

## 6. "Apply to 3D" — Model Generation

### Wall Generation
For each edge `(vertices[i], vertices[(i+1) % n])`:

```javascript
const a = vertices[i], b = vertices[(i+1) % n];
const dx = b[0] - a[0], dy = b[1] - a[1];
const length = Math.hypot(dx, dy);
const angle = Math.atan2(dy, dx);

const wall = createWallModel('Room_Wall_' + (i + 1));
wall.isRoomBoundary = true;
wall.anchor = { x: a[0], y: a[1], z: 0 };
wall.base = { W: length, D: 0, H: state.roomPolygon.height };
wall.transform = cloneModelTransform({
    quaternion: { x: 0, y: 0, z: Math.sin(angle/2), w: Math.cos(angle/2) },
    scale: { x: 1, y: 1, z: 1 }
});
```

### Floor Generation
```javascript
const floor = createFloorModel('Room_Floor');
floor.isRoomBoundary = true;
floor.anchor = { x: 0, y: 0, z: 0 };
floor.polygon = state.roomPolygon.vertices.map(v => [v[0], v[1]]);
syncFloorBaseFromPolygon(floor);
```

### Ceiling Generation
```javascript
const ceiling = createFloorModel('Room_Ceiling');
ceiling.isRoomBoundary = true;
ceiling.anchor = { x: 0, y: 0, z: state.roomPolygon.height };
ceiling.polygon = state.roomPolygon.vertices.map(v => [v[0], v[1]]);
syncFloorBaseFromPolygon(ceiling);
```

### Wall Miter at Corners
At each vertex where two walls meet at angle θ ≠ 180°:
- Compute the bisector line of the two edges meeting at this vertex
- Extend or trim each wall's endpoint to the intersection with the adjacent wall's line
- This ensures walls seal at corners without gaps or overlaps

Implementation: for each wall, compute the intersection point of its line with the previous and next wall's line. Adjust `wall.anchor` and `wall.base.W` so the wall spans from one intersection to the next.

```javascript
function computeWallMiterPoints(vertices) {
    const n = vertices.length;
    const miterPoints = [];
    for (let i = 0; i < n; i++) {
        const prev = vertices[(i - 1 + n) % n];
        const curr = vertices[i];
        const next = vertices[(i + 1) % n];
        // Wall i goes from curr to next
        // Wall i-1 goes from prev to curr
        // Miter point at curr = intersection of the two wall lines
        // For zero-thickness walls, the miter point IS the vertex itself
        miterPoints.push(curr);
    }
    return miterPoints;
}
```

For zero-thickness walls (D=0), the miter point is simply the vertex position — no extension needed. Walls connect at their shared vertex. The only issue is that the wall's "front" face extends from one vertex to the next along a straight line, and the anchor rotation handles the angle correctly.

### Re-apply Behavior
When applying from 2D editor and room boundary models already exist:
1. Remove all models with `isRoomBoundary = true`
2. Clean up `_modelMap`, groups, etc.
3. Generate fresh Wall + Floor + Ceiling models
4. Preserve non-boundary models (racks, ACUs, etc.)

---

## 7. 2D Toolbar (replaces 3D toolbar when in 2D mode)

```
[ 🖊 Draw ] [ ✋ Select ] [ 📐 Grid Snap: ON ] [ ⊞ Vertex Snap: ON ] [ ═ Edge Align: ON ]
[ Grid: 0.1m ▾ ] [ Clear All ]
```

- **Draw mode**: click to add vertices
- **Select mode**: click to select/drag vertices or edges
- Toggle buttons for snap options
- Grid spacing dropdown

---

## 8. 2D Right Panel

```
┌──────────────────────────┐
│ 🏠 Room Settings         │
│ Height: [3.00] m         │
│                          │
│ 📐 Selected Vertex       │  (when vertex selected)
│ X: [5.00] m              │
│ Y: [3.00] m              │
│                          │
│ 📏 Selected Edge         │  (when edge selected)
│ Length: 4.50 m            │
│ Angle: 45.0°             │
│                          │
│ 📊 Polygon Info          │
│ Vertices: 6              │
│ Perimeter: 24.3 m        │
│ Area: 38.5 m²            │
│                          │
│ [✅ 应用转换为 3D 房间]   │
│ [❌ 取消]                 │
└──────────────────────────┘
```

---

## 9. normalizeState Integration

```javascript
// In normalizeState:
if (next.roomPolygon != null) {
    if (!next.roomPolygon.vertices || !Array.isArray(next.roomPolygon.vertices)
        || next.roomPolygon.vertices.length < 3
        || !next.roomPolygon.vertices.every(v => Array.isArray(v) && v.length >= 2
            && Number.isFinite(v[0]) && Number.isFinite(v[1]))
        || !Number.isFinite(next.roomPolygon.height) || next.roomPolygon.height <= 0) {
        next.roomPolygon = null;
    }
}

// isRoomBoundary preservation in createModelFromRaw:
model.isRoomBoundary = !!rawModel.isRoomBoundary;
```

---

## 10. i18n Keys

```
"btn.room_2d_editor": "房间 2D 平面设计",
"btn.apply_to_3d": "✅ 应用转换为 3D 房间",
"btn.cancel_2d": "❌ 取消",
"label.room_height": "房间高度",
"label.selected_vertex": "选中顶点",
"label.selected_edge": "选中边",
"label.edge_length": "长度",
"label.edge_angle": "角度",
"label.polygon_perimeter": "周长",
"label.draw_mode": "绘制",
"label.select_mode": "选择",
"label.grid_snap": "网格吸附",
"label.vertex_snap": "顶点吸附",
"label.edge_align": "边对齐",
"label.clear_all": "清除全部",
"hint.close_polygon": "点击起始顶点或按 Enter 闭合多边形",
"hint.2d_editor_intro": "点击画布放置顶点绘制房间轮廓",
"warn.polygon_not_closed": "请先闭合多边形（至少 3 个顶点）",
"warn.replace_room": "将替换现有房间边界模型，是否继续？"
```

---

## Non-Goals (P3a scope)

- Bezier curves, arc segments (P3b)
- U/L shape templates (P3b)
- 3D→2D bidirectional sync on wall drag (P3c)
- Dimension annotation overlays (P3b)
- Multiple rooms / multiple polygons per project

---

## Testing

1. Enter 2D mode → canvas visible, 3D hidden, model tree still visible
2. Draw rectangle (4 clicks + close) → verify closed polygon
3. "Apply" → verify 4 walls + 1 floor + 1 ceiling generated in 3D
4. Walls correctly rotated at each edge angle
5. Floor has polygon matching drawn vertices
6. Re-enter 2D → existing polygon loaded on canvas
7. Modify a vertex → re-apply → old boundary models replaced with new ones
8. Cancel → no changes to 3D models
9. Draw triangle → apply → 3 walls + triangular floor + ceiling
10. Draw L-shape (concave) → apply → 6 walls + L-shaped floor
11. Save/load project → roomPolygon + isRoomBoundary preserved
12. Undo after apply → room models removed
13. Grid snap, vertex snap, edge align all work during drawing
14. Zoom/pan canvas works smoothly
