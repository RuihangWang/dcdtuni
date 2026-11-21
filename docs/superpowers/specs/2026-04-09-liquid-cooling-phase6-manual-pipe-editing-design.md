# Phase 6: Manual Pipe Editing — Design Spec

**Date**: 2026-04-09
**Status**: Approved
**Scope**: Two levels of manual liquid cooling pipe editing: (1) server-internal cold plate wiring via drag-connect in Equipment Level View, (2) CDU-Rack pipe routing via 3D waypoint editing

**Depends on**: Phase 4 (pipe renderer), Phase 5 (Equipment Level View)

---

## 1. Design Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Editing scope | Both server-internal wiring AND CDU-Rack pipe routing |
| 2 | CDU-Rack editing interaction | 3D waypoint dragging (TransformControls) |
| 3 | Data persistence format | `piping.chains` (port-component alternating sequence with waypoints) |
| 4 | Right-click insertable components | Full set: valve, pump, quick connect, heat exchanger |

---

## 2. Part A: Server-Internal Drag-Connect (Equipment Level View)

### 2.1 What Changes

Extend `liquid_topology_editor.js` (the existing Equipment Level View Canvas) to support freeform port-to-port drag-connect, upgrading from preset-only (Series/Parallel) to full custom wiring.

### 2.2 Port System

Each cold plate has two ports rendered as circles on the Canvas:
- **Inlet** (left side, blue filled circle, radius 6px): water enters
- **Outlet** (right side, red filled circle, radius 6px): water exits

Each liquid-cooled server has two manifold connection ports:
- **Manifold supply out** (top of server row, blue): water from supply manifold enters
- **Manifold return in** (bottom of server row, red): water exits to return manifold

Port states:
- **Unconnected**: hollow circle with dashed border
- **Connected**: filled circle with solid border
- **Hovered**: enlarged (radius 9px) with glow effect
- **Dragging source**: pulsing animation

### 2.3 Drag-Connect Interaction

1. User clicks and holds on an **outlet** port (red circle or manifold supply)
2. A bezier curve follows the mouse from the source port
3. As mouse approaches an **inlet** port (blue circle or manifold return), that port highlights with snap attraction (within 20px)
4. User releases on a valid inlet port → connection created
5. Releasing on empty space → drag cancelled, no change

**Validation rules:**
- Only outlet → inlet connections (not inlet → inlet or outlet → outlet)
- No self-connections (same cold plate's outlet to its own inlet)
- Each inlet port accepts at most one incoming connection
- Each outlet port sends at most one outgoing connection
- No cycles (A.out → B.in → B.out → A.in is forbidden)

### 2.4 Connection Management

- **Create**: drag-connect as above
- **Delete**: click an existing connection line → it highlights yellow → press Delete key or click "Disconnect" toolbar button → connection removed
- **Custom preset**: clicking "Custom" preset button clears all connections for the server, entering freeform mode
- **Series/Parallel presets**: clicking these replaces all connections with the preset wiring

### 2.5 Data Model

When connections are manually drawn, the routing type becomes `"custom"`:

```json
{
  "routing": {
    "type": "custom",
    "connections": [
      { "from": "manifold_in", "to": "cp_cpu.in" },
      { "from": "cp_cpu.out", "to": "cp_gpu.in" },
      { "from": "cp_gpu.out", "to": "manifold_out" }
    ]
  }
}
```

Port naming convention:
- `manifold_in`: supply manifold port for this server
- `manifold_out`: return manifold port for this server
- `{coldPlateId}.in`: cold plate inlet
- `{coldPlateId}.out`: cold plate outlet

### 2.6 Canvas Rendering Updates

The existing `render()` function in `liquid_topology_editor.js` must be extended to:
- Draw ports as interactive circles (filled/hollow based on connection state)
- Draw existing connections as bezier curves between connected ports
- Draw the in-progress drag line when user is dragging
- Highlight hovered ports and selected connections
- Show "Custom" badge on servers with custom routing

### 2.7 Toolbar Updates

Add to the Equipment Level View header toolbar:
```
[Back to 3D] | [Rack_1] [Rack_2] | [Series] [Parallel] [Custom] | [Disconnect] | [← Undo] [→ Redo]
```

- **Custom**: clears connections for selected server's LC, sets type to "custom"
- **Disconnect**: removes the currently selected (highlighted) connection
- Undo/Redo: use existing pushUndoCheckpoint/popUndo system (already wired)

---

## 3. Part B: CDU-Rack 3D Pipe Editing

### 3.1 What Changes

Extend `liquid_pipe_renderer.js` to support a manual editing mode where the user can:
- Drag waypoints to reroute pipes in 3D
- Double-click pipe segments to insert new waypoints
- Delete waypoints
- Right-click pipe segments to insert components (valve, pump, quick connect, heat exchanger)

### 3.2 Mode Toggle

In the Solver tab's "Pipe Network" section:
```
Mode: (○) Auto  (●) Manual
[Edit Pipe Network]     ← enabled only in Manual mode
```

- **Auto mode** (default): pipes auto-generated from CDU.servedRacks, not editable
- **Manual mode**: user clicks "Edit Pipe Network" to enter editing
- Switching from Manual back to Auto: warns "This will discard manual edits. Continue?"

### 3.3 Entering Edit Mode

When user clicks "Edit Pipe Network":

1. If `piping.chains` is empty (first time manual), auto-generate initial chains from current auto-routing and convert to `piping.chains` format
2. Display waypoint spheres (yellow, radius 0.03m) at each waypoint along every pipe
3. Display component icons (small colored cubes) at each inserted component
4. Pipe segments between waypoints become click-selectable (highlight green on hover)
5. A floating toolbar appears at top of 3D viewport:
```
┌─ Pipe Editor ──────────────────────────────────────┐
│ [✓ Done Editing] [↩ Undo] [↪ Redo]                │
│ Selected: pipe segment CDU1→Rack1 #3               │
└────────────────────────────────────────────────────┘
```

### 3.4 Waypoint Interaction

**Drag waypoint**: 
- Click a yellow waypoint sphere → it turns orange (selected)
- Drag to move it in 3D space (constrained to horizontal plane by default, hold Shift for vertical)
- Uses Three.js TransformControls in translate mode
- On release: pipe re-renders through new waypoint position

**Add waypoint**:
- Double-click a pipe segment → new waypoint inserted at click position
- Or right-click → "Add Waypoint"

**Delete waypoint**:
- Select waypoint → press Delete key
- Pipe segment reconnects between the adjacent waypoints

### 3.5 Right-Click Context Menu

Right-click on a pipe segment shows:

```
┌───────────────────────────┐
│ Insert Valve              │
│ Insert Pump               │
│ Insert Quick Connect      │
│ Insert Heat Exchanger     │
│ ─────────────────────── │
│ Add Waypoint Here         │
│ Delete Segment            │
└───────────────────────────┘
```

**Insert component**:
1. User selects component type from menu
2. A modal dialog shows spec selection (dropdown of available specs from `state.liquidCooling.specifications`)
3. Component is inserted at the click point, splitting the pipe segment into two
4. Component renders as a small 3D icon:
   - Valve: green cube with "V" label
   - Pump: blue cube with "P" label
   - Quick connect: gray cube with "QC" label
   - Heat exchanger: orange cube with "HX" label
5. Component is click-selectable → right panel shows its properties (spec, opening for valve, rpm for pump)

**Delete segment**:
- Removes the pipe segment and any component on it
- Adjacent waypoints remain (user can reconnect or delete them)

### 3.6 Data Persistence — piping.chains Format

When `piping.autoGenerated = false`, the manual pipe network is stored in `piping.chains`:

```json
{
  "piping": {
    "autoGenerated": false,
    "mainPipeSpec": "pipe_25mm",
    "branchPipeSpec": "pipe_25mm",
    "chains": [
      {
        "id": "manual_CDU_1_Rack_1_supply",
        "path": [
          "CDU_1_supply",
          { "pipe": "pipe_25mm", "waypoints": [
            {"x": 1.0, "y": 2.0, "z": 1.5},
            {"x": 3.0, "y": 2.0, "z": 1.5}
          ]},
          { "valve": "valve_default", "opening": 0.8 },
          { "pipe": "pipe_25mm", "waypoints": [
            {"x": 3.0, "y": 2.0, "z": 1.5},
            {"x": 3.0, "y": 4.0, "z": 1.5}
          ]},
          "Rack_1_manifold_in"
        ]
      },
      {
        "id": "manual_CDU_1_Rack_1_return",
        "path": [
          "Rack_1_manifold_out",
          { "pipe": "pipe_25mm", "waypoints": [
            {"x": 3.0, "y": 4.0, "z": 1.44},
            {"x": 1.0, "y": 2.0, "z": 1.44}
          ]},
          "CDU_1_return"
        ]
      }
    ]
  }
}
```

**Path format**: alternating between port ID strings and component objects:
- Port ID string: named endpoint (e.g., `"CDU_1_supply"`, `"Rack_1_manifold_in"`)
- Component object: `{"pipe": specName, "waypoints": [...]}` or `{"valve": specName, "opening": float}` or `{"pump": specName, "rpm": float}` or `{"quick_connect": specName}` or `{"heat_exchanger": specName}`

### 3.7 Exiting Edit Mode

Click "Done Editing":
1. Waypoint spheres and component icons removed from 3D viewport
2. Pipes re-rendered as smooth tubes using the edited waypoints
3. `piping.chains` saved into `state.liquidCooling.piping.chains`
4. Normal 3D interaction (model selection, orbit) restored

### 3.8 Auto-to-Manual Conversion

When user first switches to Manual mode with no existing chains:

```python
# Pseudo-code for initial chain generation
for each CDU:
  for each servedRack:
    supply_chain = {
      id: f"auto_{cdu}_{rack}_supply",
      path: [
        f"{cdu}_supply",
        {pipe: mainPipeSpec, waypoints: [current auto-route waypoints from pipe renderer]},
        f"{rack}_manifold_in"
      ]
    }
    return_chain = { ... similar ... }
    chains.push(supply_chain, return_chain)
```

This gives the user a starting point that matches the current auto-routing, which they can then modify.

---

## 4. Backend Integration

### 4.1 topology.py Update

`topology.py:build_network()` already handles `piping.autoGenerated = true`. For `false`, add chain parsing:

```python
if not liquid_cfg.piping.autoGenerated and liquid_cfg.piping.chains:
    _build_from_chains(G, components, liquid_cfg.piping.chains, specs, fluid)
else:
    # existing auto-generation logic
```

`_build_from_chains()` iterates each chain's path, creating graph edges:
- Port strings → graph nodes
- `{"pipe": spec, "waypoints": [...]}` → PressureDrivenPipe edge (waypoints ignored by solver)
- `{"valve": spec, "opening": o}` → ControlValveKv edge
- `{"pump": spec, "rpm": r}` → PumpWithSpeed edge
- `{"quick_connect": spec}` → QuickConnector edge
- `{"heat_exchanger": spec}` → HeatExchanger edge

### 4.2 Schema Update

Add chain-level Pydantic models to `liquid/schema.py`:

```python
class PipeChainSegment(BaseModel):
    """A pipe segment in a chain with 3D waypoints."""
    pipe: str  # spec name
    waypoints: List[Point3D] = Field(default_factory=list)

class ValveChainSegment(BaseModel):
    valve: str  # spec name
    opening: float = 1.0

class PumpChainSegment(BaseModel):
    pump: str  # spec name
    rpm: float = 3000.0

class QuickConnectChainSegment(BaseModel):
    quick_connect: str  # spec name

class HxChainSegment(BaseModel):
    heat_exchanger: str  # spec name

class PipingChain(BaseModel):
    id: str
    path: List  # alternating port strings and component segment objects
```

---

## 5. File Changes Summary

| File | Change Type | What |
|------|-------------|------|
| `web/js/liquid_topology_editor.js` | Modify | Add drag-connect, connection rendering, Custom mode, Disconnect button |
| `web/js/liquid_pipe_renderer.js` | Modify | Manual mode: waypoint spheres, component icons, edit interaction, chains→3D |
| `web/STL_generate.html` | Modify | Manual mode toggle, right-click context menu, pipe editor toolbar, component property panel |
| `src/backend/liquidcooling/topology.py` | Modify | Add `_build_from_chains()` for manual chain parsing |
| `src/backend/liquidcooling/schema.py` | Modify | Add chain segment Pydantic models |
| `tests/test_liquid/test_topology.py` | Modify | Add tests for chain-based topology building |

---

## 6. Acceptance Criteria

### Part A (Equipment Level View drag-connect):
- User can drag from outlet port to inlet port → connection created
- Invalid connections rejected (self-loop, duplicate inlet, cycle)
- "Custom" preset clears connections and enables freeform mode
- "Disconnect" removes selected connection
- Series/Parallel presets still work (override custom connections)
- Custom routing saved in `routing.connections` and persists in undo/redo/save

### Part B (3D pipe editing):
- Manual mode toggle works (Auto ↔ Manual with warning)
- Waypoint spheres appear on pipes in edit mode
- Waypoints draggable via TransformControls
- Double-click adds new waypoint
- Delete key removes selected waypoint
- Right-click menu shows 4 component types + Add Waypoint + Delete Segment
- Inserted components render as labeled cubes in 3D
- Component click → property panel shows spec/params
- "Done Editing" exits mode and saves chains
- Auto-to-manual conversion creates initial chains from auto-routing
- Saved chains consumed by topology.py solver correctly

### Backend:
- `topology.py` builds correct NetworkX graph from `piping.chains`
- All 4 component types (valve, pump, quick_connect, heat_exchanger) create correct nn.Module edges
- Unit tests pass for chain-based topology
