# Liquid Cooling Pipe: Interactive Creation & Snap System — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add free-form water pipe creation to the liquid cooling mode — draggable multi-waypoint pipes with endpoint gizmo control and auto-snap to CDU/RACK manifold ports.

**Architecture:** Extends the existing `pipe_segment` model type and `liquid_pipe_renderer.js`. New gizmo interaction reuses the `TransformControls` pattern from the streamline seed sphere. Snap detection is a new subsystem with proximity-based port matching and visual feedback.

**Tech Stack:** Three.js r128 (existing), vanilla JS in STL_generate.html + liquid_*.js modules

---

## 1. Scope

This spec covers:

- Liquid toolbar merge (air buttons + liquid-specific buttons in one column)
- Free-form pipe creation ("New Pipe" → pipe appears at scene center)
- Multi-waypoint control point system with gizmo drag
- Auto-snap to CDU ports and RACK liquid manifold endpoints
- Color inheritance (brown → blue/red based on connection)
- Right-click context menu for adding/removing intermediate control points

**Out of scope:**

- Pipe physics simulation (Modelica solver integration)
- Pipe-to-pipe connections (T-branch snap)
- 2D topology editor changes (liquid_topology_editor.js)
- CDU model creation changes (already works)
- Existing port-to-port pipe creation flow (preserved unchanged)

## 2. Toolbar Changes

### 2.1 Merged Liquid Toolbar

`_renderLiquidToolbar()` (STL_generate.html line 13396) is modified to include all air-mode buttons followed by liquid-specific buttons:

```
[Air buttons: Box, Rack, ACU, Wall, Partition Wall, Baffle, Floor]
─── separator ───
[Liquid buttons: CDU, 送水管道 (New Pipe)]
─── separator ───
[Contextual: CRH, ColdPlate, HeatSource (selection-dependent)]
```

Implementation: call `_renderAirToolbar()` to get air buttons HTML, strip its outer `<div>` wrapper, concatenate with liquid buttons and contextual section.

### 2.2 "新建管道" Button Behavior Change

The existing "新建管道 / New Pipe" button (line 13405) currently calls `_enterPipeCreationMode()` (port-to-port flow). This button is **replaced** with a new function `window.createFreePipe()` that creates a standalone pipe immediately.

The existing port-to-port flow is preserved via the "Edit Pipe Network" button in the liquid cooling solver group (line 401).

## 3. Data Model

### 3.1 Pipe Segment Model Changes

The existing `createPipeSegmentModel()` factory (line 2968) is extended with a new convenience wrapper:

```js
window.createFreePipe = function() {
    pushUndoCheckpoint('createFreePipe');
    const pipeCount = state.models.filter(m => m.kind === 'pipe_segment').length;
    const pipe = createPipeSegmentModel('Pipe_' + (pipeCount + 1));

    // Override defaults for free-form pipe
    pipe.pipe.spec = 'pipe_100mm';
    pipe.pipe.diameter = 0.10;          // 10cm
    pipe.pipe.startPortId = '';         // unconnected
    pipe.pipe.endPortId = '';           // unconnected
    pipe.pipe.color = 'unconnected';    // brown
    pipe.pipe.waypoints = [
        { x: 0,   y: 0.5, z: 0 },     // start
        { x: 0,   y: 0.5, z: 0.5 },   // middle
        { x: 0,   y: 0.5, z: 1.0 }    // end (1m total)
    ];

    state.models.push(pipe);
    _modelMap.set(pipe.id, pipe);
    rebuildGeometry();
    updateUI();
    logOperation('createFreePipe', 'Create free pipe', { pipeId: pipe.id }, 'create');
};
```

### 3.2 New `pipe.diameter` Field

Added to the pipe sub-object. Default: `0.10` (meters) for free pipes, `0.025` for auto-routed pipes. Used by the renderer to set `TubeGeometry` radius.

### 3.3 Color Values

The `pipe.color` field accepts a new value:

| Value | Hex | Meaning |
|-------|-----|---------|
| `'unconnected'` | `#8B6914` | No port connection (brown) |
| `'supply'` | `#2196F3` | Connected to supply/cold port (blue) |
| `'return'` | `#F44336` | Connected to return/hot port (red) |

Existing `'supply'` and `'return'` values are unchanged. The renderer maps `'unconnected'` to brown.

## 4. 3D Rendering

### 4.1 Pipe Tube

Rendered via existing `createTubeMesh()` in `liquid_pipe_renderer.js`, modified to:

- Use `pipe.diameter / 2` as tube radius instead of fixed `PIPE_RADIUS` (0.015)
- Map `'unconnected'` color to `#8B6914`
- Material: `MeshStandardMaterial` with `metalness: 0.4`, `roughness: 0.4`, `transparent: true`, `opacity: 0.85`

### 4.2 Control Point Spheres

Shown only when the pipe is selected (`state.selection.modelId === pipe.id`):

| Point Type | Color | Radius |
|------------|-------|--------|
| Start endpoint | Same as pipe color | `pipe.diameter * 1.5` |
| End endpoint | Same as pipe color | `pipe.diameter * 1.5` |
| Intermediate | `#FFEB3B` (yellow) | `pipe.diameter * 1.2` |

Material: `MeshBasicMaterial`, `transparent: true`, `opacity: 0.6`, `depthTest: false`, `renderOrder: 998`.

Each sphere stores `userData`:
```js
{
    isPipeControlPoint: true,
    pipeId: pipe.id,
    waypointIndex: i,        // 0 = start, len-1 = end
    isEndpoint: true/false
}
```

### 4.3 Rendering Lifecycle

- Control point spheres are created/destroyed in a new `_updatePipeControlPoints(pipeId)` function
- Called from `updateUI()` when selection changes to/from a pipe_segment
- Spheres are added to `globalGroup` (not the pipe group) so TransformControls can access them
- Tagged with `userData.isPipeControlPoint = true` for cleanup

## 5. Gizmo Interaction

### 5.1 TransformControls Instance

A single shared `TransformControls` instance for pipe waypoint dragging:

```js
// Lazy-initialized in liquid_mode.js or liquid_pipe_renderer.js
let _pipeWaypointTC = null;
let _pipeWaypointTarget = null;  // Currently attached sphere

function _ensurePipeWaypointTC() {
    if (_pipeWaypointTC) return _pipeWaypointTC;
    _pipeWaypointTC = new THREE.TransformControls(camera, renderer.domElement);
    _pipeWaypointTC.setMode('translate');
    _pipeWaypointTC.setSize(0.7);
    scene.add(_pipeWaypointTC);

    _pipeWaypointTC.addEventListener('dragging-changed', function(event) {
        controls.enabled = !event.value;
        if (event.value) {
            pushUndoCheckpoint('dragPipeWaypoint');
        }
        if (!event.value) {
            // Drag ended → check snap
            _checkSnapOnDragEnd();
        }
    });

    _pipeWaypointTC.addEventListener('change', function() {
        _syncWaypointFromSphere();
        requestRender();
    });

    return _pipeWaypointTC;
}
```

### 5.2 Interaction Flow

1. **User clicks pipe tube mesh** → pipe selected, control point spheres appear
2. **User clicks a control point sphere** → `_ensurePipeWaypointTC().attach(sphere)`, gizmo arrows appear
3. **User drags gizmo** → `'change'` event fires → `_syncWaypointFromSphere()`:
   - Read `sphere.position` → write back to `pipe.pipe.waypoints[i]`
   - Call `updatePipeGeometry()` to rebuild tube curve
   - Call `_updateSnapPreview()` for real-time snap feedback
4. **User releases drag** → `'dragging-changed'` (value=false) → `_checkSnapOnDragEnd()`:
   - If within snap range: lock position, set portId, update color
   - If not: keep current position
5. **User clicks empty space** → `_pipeWaypointTC.detach()`, hide control point spheres

### 5.3 Raycast Integration

Pipe control point click detection is added to the existing click handler:

- Raycast against meshes with `userData.isPipeControlPoint === true`
- Priority: control point spheres > pipe tube mesh > other scene objects
- Only active when a pipe is selected

## 6. Auto-Snap System

### 6.1 Snap Targets

A snap target is any connectable port in the scene:

```js
function _collectSnapTargets() {
    const targets = [];

    // CDU ports
    state.models.forEach(m => {
        if (m.kind !== 'cdu' || !m.enabled) return;
        targets.push({
            modelId: m.id,
            portType: 'supply',
            portId: m.id + '.supply',
            position: getCduPort(m, 'supply'),
            color: 'supply'
        });
        targets.push({
            modelId: m.id,
            portType: 'return',
            portId: m.id + '.return',
            position: getCduPort(m, 'return'),
            color: 'return'
        });
    });

    // RACK liquid manifold pipe endpoints
    state.models.forEach(m => {
        if (m.kind !== 'rack' || !m.enabled) return;
        if (!m.rack || m.rack.coolingType !== 'liquid') return;
        const manifold = m.rack.liquidManifold;
        if (!manifold) return;

        // Supply manifold top/bottom endpoints
        // (positions computed from manifold pipe mesh positions)
        targets.push({
            modelId: m.id,
            portType: 'supply',
            portId: m.id + '.manifold_supply_top',
            position: _getManifoldEndpoint(m, 'supply', 'top'),
            color: 'supply'
        });
        targets.push({
            modelId: m.id,
            portType: 'supply',
            portId: m.id + '.manifold_supply_bottom',
            position: _getManifoldEndpoint(m, 'supply', 'bottom'),
            color: 'supply'
        });
        // Return manifold top/bottom
        targets.push({
            modelId: m.id,
            portType: 'return',
            portId: m.id + '.manifold_return_top',
            position: _getManifoldEndpoint(m, 'return', 'top'),
            color: 'return'
        });
        targets.push({
            modelId: m.id,
            portType: 'return',
            portId: m.id + '.manifold_return_bottom',
            position: _getManifoldEndpoint(m, 'return', 'bottom'),
            color: 'return'
        });
    });

    return targets;
}
```

### 6.2 Snap Detection (During Drag)

```js
const SNAP_PREVIEW_DISTANCE = 0.15;  // meters

function _updateSnapPreview(endpointPosition) {
    const targets = _collectSnapTargets();
    let closest = null;
    let closestDist = Infinity;

    targets.forEach(t => {
        const d = endpointPosition.distanceTo(t.position);
        if (d < SNAP_PREVIEW_DISTANCE && d < closestDist) {
            closest = t;
            closestDist = d;
        }
    });

    if (closest) {
        _showSnapHighlight(closest);     // pulsing ring at target
        _previewPipeColor(closest.color); // real-time color change
    } else {
        _hideSnapHighlight();
        _previewPipeColor('unconnected');
    }
}
```

### 6.3 Snap Lock (On Drag End)

```js
function _checkSnapOnDragEnd() {
    const sphere = _pipeWaypointTarget;
    if (!sphere || !sphere.userData.isEndpoint) return;

    const targets = _collectSnapTargets();
    let closest = null;
    let closestDist = Infinity;

    targets.forEach(t => {
        const d = sphere.position.distanceTo(t.position);
        if (d < SNAP_PREVIEW_DISTANCE && d < closestDist) {
            closest = t;
            closestDist = d;
        }
    });

    const pipe = _modelMap.get(sphere.userData.pipeId);
    if (!pipe) return;
    const wpIdx = sphere.userData.waypointIndex;
    const isStart = wpIdx === 0;

    if (closest) {
        // Snap: move to exact port position
        sphere.position.copy(closest.position);
        pipe.pipe.waypoints[wpIdx] = {
            x: closest.position.x,
            y: closest.position.y,
            z: closest.position.z
        };
        if (isStart) pipe.pipe.startPortId = closest.portId;
        else         pipe.pipe.endPortId = closest.portId;

        // Update color based on connections
        _updatePipeColor(pipe);
    } else {
        // No snap — disconnect if was previously connected
        if (isStart && pipe.pipe.startPortId) pipe.pipe.startPortId = '';
        if (!isStart && pipe.pipe.endPortId)  pipe.pipe.endPortId = '';
        _updatePipeColor(pipe);
    }

    _hideSnapHighlight();
    updatePipeGeometry();
}
```

### 6.4 Snap Visual Feedback

**Pulsing ring** at snap target position:

- Geometry: `THREE.RingGeometry(0.06, 0.08, 32)`
- Material: `MeshBasicMaterial`, color matches port type (blue/red), `transparent: true`
- Animation: `opacity = 0.3 + 0.5 * Math.abs(Math.sin(Date.now() * 0.005))`
- Created on snap preview start, destroyed on snap preview end
- Uses `requestAnimationFrame` loop while active

### 6.5 Disconnect on Drag Away

When a connected endpoint is dragged > 0.20m from its connected port:

```js
const SNAP_DISCONNECT_DISTANCE = 0.20;

// Inside _syncWaypointFromSphere(), check:
if (isStart && pipe.pipe.startPortId) {
    const connectedPort = _findPortPosition(pipe.pipe.startPortId);
    if (connectedPort && sphere.position.distanceTo(connectedPort) > SNAP_DISCONNECT_DISTANCE) {
        pipe.pipe.startPortId = '';
        _updatePipeColor(pipe);
    }
}
```

## 7. Color Inheritance Rules

```js
function _updatePipeColor(pipe) {
    const startPort = pipe.pipe.startPortId;
    const endPort = pipe.pipe.endPortId;

    if (!startPort && !endPort) {
        pipe.pipe.color = 'unconnected';
    } else {
        // Derive color from whichever port is connected
        const portId = startPort || endPort;
        const target = _findSnapTarget(portId);
        pipe.pipe.color = target ? target.color : 'unconnected';
    }
}
```

When both ends are connected, they should agree on color (both supply or both return). If they disagree, the start port takes precedence (this prevents nonsensical supply→return pipes).

## 8. Right-Click Context Menu for Control Points

When right-clicking a control point sphere:

**On intermediate point (not endpoint):**
- "删除此控制点 / Delete Control Point" → removes waypoint from array, rebuilds geometry

**On any point:**
- "在此后添加控制点 / Add Control Point After" → inserts new waypoint at midpoint between this and next point

**On endpoint:**
- "断开连接 / Disconnect" (only if connected) → clears portId, resets color

Implementation: reuse the existing `_showWaypointContextMenu()` pattern from `liquid_mode.js`.

## 9. File Organization

All new code goes into existing files:

| Change | File |
|--------|------|
| `_renderLiquidToolbar()` merge | `STL_generate.html` (line 13396) |
| `createFreePipe()` | `STL_generate.html` (near createPipeSegmentModel) |
| `pipe.diameter` field | `STL_generate.html` (line 2968, createPipeSegmentModel) |
| `'unconnected'` color mapping | `liquid_pipe_renderer.js` (createTubeMesh) |
| Diameter-based tube radius | `liquid_pipe_renderer.js` (createTubeMesh) |
| Control point sphere rendering | `liquid_pipe_renderer.js` (new function) |
| Waypoint gizmo TC | `liquid_mode.js` (new functions, parallel to junction TC) |
| Snap target collection | `liquid_mode.js` (new functions) |
| Snap preview + highlight | `liquid_mode.js` (new functions) |
| Raycast for control points | `liquid_mode.js` (extend handlePipeClick) |
| Right-click context menu | `liquid_mode.js` (extend handleContextMenu) |

## 10. Testing Strategy

- **Toolbar**: browser-use verify all buttons present in liquid mode
- **Pipe creation**: `createFreePipe()` → verify model in state with correct defaults
- **Control points**: select pipe → verify spheres in scene with correct userData
- **Gizmo attach**: click sphere → verify TransformControls attached
- **Waypoint sync**: drag sphere → verify `pipe.waypoints[i]` updated
- **Snap preview**: drag endpoint near CDU port → verify highlight ring appears
- **Snap lock**: release near port → verify endpoint position matches port exactly
- **Color change**: snap to supply port → verify `pipe.color === 'supply'`
- **Disconnect**: drag connected endpoint away → verify portId cleared, color brown
- **Context menu**: right-click intermediate point → verify "Delete" option works
- **Undo/redo**: drag + snap → undo → verify previous state restored
