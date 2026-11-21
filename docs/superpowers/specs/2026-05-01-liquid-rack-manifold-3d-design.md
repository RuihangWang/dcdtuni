# Liquid-Cooled Rack: Manifold 3D Rendering — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add liquid cooling rack support to the STL_generate.html frontend — rack cooling type switch, vertical manifold pipe rendering, and server connection stubs.

**Architecture:** Manifold pipes are rendered as Three.js child meshes of the rack geometry group (Approach A). No new model types are created. Physical parameters are stored in `rack.liquidManifold` for future Modelica-style solver integration.

**Tech Stack:** Three.js r128 (existing), vanilla JS in STL_generate.html

---

## 1. Scope

This spec covers **Step 1** of the liquid cooling 3D modeling feature:

- Rack `coolingType` field and UI dropdown
- Vertical manifold pipe 3D rendering inside liquid-cooled racks
- Server connection stub rendering
- Manifold layout options (both-left / both-right / split)

**Out of scope** (future steps):
- CP-CU pair management and server slicing visualization (Step 2)
- Physics parameter solver / Modelica constraint equations (Step 3)
- CDU model type (already partially exists)
- Liquid-air coupling

## 2. Data Model Changes

### 2.1 rack.coolingType

New field on the rack model object:

```js
rack: {
  coolingType: "air",  // "air" | "liquid" — default "air"
  // ... existing fields (equipments, blankingPanelType, etc.)
}
```

- Persisted in `.cfdproj` and `.cfdrun` JSON
- `"air"` preserves all existing behavior unchanged
- `"liquid"` enables manifold rendering and liquid-specific UI

### 2.2 rack.liquidManifold

New field, only meaningful when `coolingType === "liquid"`:

```js
rack: {
  coolingType: "liquid",
  liquidManifold: {
    layout: "both-left",       // "both-left" | "both-right" | "split"
    supplyPipe: {
      diameter: 0.025,         // 25mm in meters
      offsetFromInlet: 0.10,   // 100mm offset from rack inlet face
    },
    returnPipe: {
      diameter: 0.025,
      offsetFromInlet: 0.10,
    },
    pipeSpacing: 0.015,        // 15mm between pipe centers (for "both-*" layouts)
  }
}
```

Default values are applied when `coolingType` is switched to `"liquid"` for the first time.

### 2.3 Equipment connection data

Each equipment (server) in a liquid-cooled rack automatically gets connection stubs. No new data fields are needed on the equipment — stub positions are computed from the equipment's U-slot position and the manifold layout.

## 3. UI Changes

### 3.1 Outer Rack Property Panel

When in liquid cooling display mode (`state.displayMode === 'liquid'`):

- A **dropdown** labeled "冷却类型 / Cooling Type" appears **above** the "RACK 详细设计" button
- Options: "风冷机柜 (Air-Cooled)" / "液冷机柜 (Liquid-Cooled)"
- Switching to "液冷机柜" initializes `rack.liquidManifold` with defaults if not present
- Switching back to "风冷机柜" preserves `liquidManifold` data (non-destructive) but hides manifold rendering

### 3.2 Rack Detail Design Panel (inside detailed editor)

When inside the rack detail editor AND `rack.coolingType === "liquid"`:

- A **dropdown** labeled "歧管布局 / Manifold Layout" appears in the right sidebar
  - Options: "左侧并排 (Both Left)" / "右侧并排 (Both Right)" / "左冷右热 (Split)"
- Numeric inputs for pipe diameter and offset (optional, can use defaults)
- These controls are **hidden** when `coolingType === "air"`

## 4. 3D Rendering

### 4.1 Manifold Pipes

Two vertical `THREE.CylinderGeometry` pipes rendered as **children of the rack's Three.js group**:

| Property | Supply Pipe | Return Pipe |
|----------|------------|-------------|
| Color | `#2196F3` (blue) | `#F44336` (red) |
| Material | MeshStandardMaterial, metalness=0.6, roughness=0.3, transparent, opacity=0.85 | Same |
| Diameter | `supplyPipe.diameter` (default 25mm) | `returnPipe.diameter` (default 25mm) |
| Height | Rack internal height (from bottom rail to top rail) | Same |
| Glow | emissive with low intensity for visibility | Same |

#### Position by layout:

**both-left:**
- Supply: X = rack.anchor.x + offsetFromInlet, Z = rack.anchor.z + pipe center offset
- Return: X = rack.anchor.x + offsetFromInlet, Z = supply.z + pipeSpacing
- Both on the left side of rack inlet face

**both-right:**
- Mirror of both-left — pipes on the right side of rack inlet face

**split:**
- Supply on left side, Return on right side of rack inlet face

All pipes are positioned at Y center = rack.anchor.y + rackHeight/2.

### 4.2 Server Connection Stubs

For each equipment in `rack.equipments`:

- Two horizontal `THREE.CylinderGeometry` (rotated 90°) connecting from the equipment's inlet face to the nearest manifold pipe
- Supply stub: blue, from equipment inlet face center-left to supply manifold
- Return stub: red, from equipment inlet face center-right to return manifold (or same side for "both-*" layouts)
- Stub diameter: 8mm (visual, smaller than manifold)
- Stub length: computed from equipment edge to manifold pipe center

### 4.3 Rendering Lifecycle

- Manifold meshes are created/destroyed in `rebuildGeometry()` (or a dedicated `_updateLiquidManifold()` called from it)
- Meshes are added to the rack's model group (`getRenderRootByModelId(rackId)`)
- They are tagged with `userData.isLiquidManifold = true` for easy identification and cleanup
- When `coolingType` switches back to `"air"`, all meshes with `isLiquidManifold` are removed
- Manifold meshes are **not exported to STL** — they are visual-only overlays for the liquid cooling design view

### 4.4 Visual Style

- Pipes have a slight glow (emissive) to stand out from the dark rack frame
- Connection stubs are thinner and less bright than manifold pipes
- When the rack is NOT selected, manifold pipes are still visible but at reduced opacity (0.4)
- When the rack IS selected (in detail view), full opacity (0.85)

## 5. Interaction Rules

1. Switching `coolingType` to `"liquid"` triggers immediate manifold rendering
2. Changing `layout` dropdown triggers re-render of manifold positions
3. Adding/removing equipment in the rack detail editor triggers re-render of connection stubs
4. Manifold state survives undo/redo (stored in rack model, covered by pushUndoCheckpoint)
5. Manifold rendering is independent of `state.displayMode` — if `coolingType === "liquid"`, manifolds show regardless of air/liquid mode toggle

## 6. Modelica Interface (Reserved — Not Implemented)

Each liquid component will eventually have a `ports` array for the 1D hydraulic solver:

```js
// Future interface — NOT implemented in Step 1
port: {
  P: null,      // Pressure (Pa)
  T: null,      // Temperature (K)
  mdot: null,   // Mass flow rate (kg/s)
}

// Each component has:
// - One or more ports
// - A constraint equation: f(ports) = 0
// - Assembled into PyTorch loss for differentiable solving
```

The data model in Section 2 is designed to be forward-compatible with this interface. The `supplyPipe` and `returnPipe` objects can later receive a `ports` array without schema breaks.

## 7. Design Mockups

See saved screenshots:
- `docs/superpowers/specs/liquid_rack_manifold_design_v1.png` — front view with manifolds and connection stubs
- `docs/superpowers/specs/liquid_manifold_layout_options.png` — three layout options and right panel UI

## 8. Testing Strategy

- Visual verification via browser-use: create rack → switch to liquid → verify manifold renders
- Switch layout dropdown → verify pipe positions update
- Add server equipment → verify connection stubs appear
- Switch back to air-cooled → verify manifolds disappear
- Save/load .cfdproj → verify liquidManifold data persists
- Undo/redo → verify manifold state is restored
