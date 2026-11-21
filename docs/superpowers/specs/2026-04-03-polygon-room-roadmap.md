# Non-Rectangular Room Support — Roadmap

**Date**: 2026-04-03
**Goal**: Support arbitrary polygon rooms (L-shape, U-shape, triangles, etc.) with a 2D floor plan editor that generates 3D Wall/Floor/Ceiling models.

---

## Background

Current system assumes axis-aligned rectangular models. This causes:
- Auto-cutout fails on rotated walls (AABB projection covers wrong area)
- Snap doesn't work with rotated models (compares AABB, not OBB)
- Walls can't form non-90° joints (no miter/bevel at corners)
- Floor/ceiling are always rectangular (can't be triangular/L-shaped)

Test case: `STLGeneratorForOpenFoam/projects/Tri_angle.cfdproj`

---

## Sub-Projects (Sequential)

### P1: Floor Polygon Support
**Status**: 🔲 Not started
**Depends on**: Nothing
**Scope**:
- Add `polygon` property to Floor model (array of [x,y] vertices defining the floor boundary)
- When `polygon` is set, render with `THREE.ShapeGeometry` from polygon vertices instead of rectangular `PlaneGeometry`
- STL export: triangulate polygon for watertight multi-solid ASCII STL output
- Keep backward compatibility: if `polygon` is absent, use existing `base.W × base.D` rectangle
- Floor inspector: show polygon vertex editor (or at minimum, accept polygon from P3)
- Ceiling = Floor copy at `z = room_height`, same polygon

### P2: Wall Miter + Rotated Model Interactions
**Status**: 🔲 Not started
**Depends on**: P1
**Scope**:
- **Wall miter at corners**: When two walls meet at a non-90° angle, automatically compute miter (bevel cut) at the junction so walls seal without overlap or gap
- **Auto-cutout fix for rotated models**: Replace AABB-based projection with OBB (oriented bounding box) or actual rotated geometry projection when computing cutout regions on walls
- **Snap for rotated models**: Extend snap system to compare OBB faces/edges, not just AABB min/max. Allow face-to-face snap between rotated walls and other models
- **Collision detection for rotated models**: Replace `_analyticalCollisionCheck` AABB with OBB or SAT (Separating Axis Theorem) for rotated models

### P3: 2D Floor Plan Editor
**Status**: 🔲 Not started
**Depends on**: P1 + P2
**Scope**:
- **Separate file**: `web/room_2d_editor.html` (or `web/room_2d_editor.js` loaded by main page)
- **Entry point**: Replace current "导入水密曲面 STL 进行水转印" button with "房间 2D 平面设计" button
- **Page layout when active**:
  - Left panel: model tree (reuse existing code)
  - Center: 2D canvas (XY top-down view, grid, pan/zoom)
  - Right panel: 2D tools (add line, add Bezier curve, add U-shape, add L-shape, etc.)
  - Top toolbar: replaced with 2D-specific tools (point snap, edge align, dimension labels, etc.)
- **Drawing tools**:
  - Click vertices to define closed polygon (room boundary)
  - Straight line segments between vertices
  - Bezier curve segments for curved walls (discretized to polyline on export)
  - Preset shapes: U-shape, L-shape templates
  - Point snapping (to grid, to other vertices, to edge midpoints)
  - Edge alignment (horizontal/vertical constraint)
  - Dimension annotations
- **Apply / Convert to 3D**:
  - "应用转换为 3D 房间" button in top-left
  - Each polygon edge → one Wall model (position = edge midpoint, rotation = edge angle, width = edge length, D = 0, H = user-specified room height)
  - Closed polygon interior → one Floor model (with `polygon` property from P1)
  - Floor copy at z = room height → Ceiling
  - Wall corners auto-mitered (using P2)
  - Switch back to 3D view with generated models in model tree
  - User can freely edit individual Wall/Floor in 3D after generation
- **Persistence**: 2D polygon data saved in `state.roomPolygon` (or similar) so re-entering 2D editor shows the original drawing
- **Re-entry**: Entering 2D editor again shows existing polygon; user can modify and re-apply (warns about overwriting existing walls)

---

## Implementation Order

```
P1 (Floor polygon)  →  P2 (Wall miter + rotated interactions)  →  P3 (2D editor)
     ~1 day                    ~2-3 days                             ~3-5 days
```

Each sub-project gets its own spec → plan → implementation cycle.

---

## Technical Notes

- Floor polygon vertices are in local coordinates (relative to floor anchor)
- Polygon must be closed (last vertex connects to first)
- For convex polygons: `THREE.ShapeGeometry` handles triangulation directly
- For concave polygons: `THREE.ShapeGeometry` uses earcut algorithm (built into Three.js) which handles concave shapes
- Wall miter: compute intersection of two wall planes at the corner, trim each wall to the bisector line
- OBB for snap/collision: compute from model anchor + base + quaternion rotation
- 2D canvas: use standard HTML5 Canvas 2D API or SVG (no Three.js needed for 2D view)
