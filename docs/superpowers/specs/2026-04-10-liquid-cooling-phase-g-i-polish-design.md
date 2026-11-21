# Liquid Cooling Dedicated Mode — Polish Phases G-I Design Spec

**Date**: 2026-04-10
**Status**: Approved
**Scope**: Address all 4 known polish items from Phase A-F completion: T-branch junction full interaction (rotation gizmo + right-click menu), legacy project migration E2E validation, and visual consistency audit.

**Predecessor**: `2026-04-10-liquid-cooling-dedicated-mode-design.md` (Phase A-F complete)

---

## 1. Known Polish Items to Address

From the Phase A-F completion report, four items were deferred:

1. **T-branch rotation gizmo** — `_upgradeToTBranch` creates the junction model but Three.js TransformControls rotation mode is not wired up.
2. **Right-click context menu for pipe waypoints** — The upgrade function exists, but no right-click UI triggers it.
3. **Legacy project migration E2E testing** — `migrateLiquidCoolingToKinds()` is written but not verified end-to-end with real legacy `.cfdproj` files.
4. **Liquid pipe rendering CSS tuning** — Colors and radius are hardcoded; need feedback-driven tuning.

---

## 2. Design Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Scope | All 4 items in scope |
| 2 | T-branch creation flow | **A** — Right-click a pipe waypoint → upgrade to junction |
| 3 | Third port UX | **B** — 3 ports directly visible and draggable, gizmo as auxiliary |
| 4 | Legacy migration validation | **C** — browser-use automated E2E testing of all legacy projects |
| 5 | CSS tuning approach | Split into **4a automated audit** (proactive) + **4b reactive polish** (deferred) |

---

## 3. Phase G: T-Branch Junction Complete Interaction

### 3.1 Overview

Phase E implemented the `_upgradeToTBranch()` function that creates a `pipe_junction` model, but there was no way for users to invoke it and no visual interaction for the junction's third (branch) port. Phase G closes these gaps.

### 3.2 Task G.1: Right-Click Context Menu

**Goal**: When the user right-clicks a pipe waypoint sphere in the 3D viewport while in liquid mode, show a floating menu with the "Upgrade to T-Branch" option.

**Implementation location**: `src/frontend/web/js/liquid_mode.js` (extend existing module).

**Behavior**:
1. Attach a `contextmenu` event listener to `renderer.domElement` when entering liquid mode (or always, with an `isLiquidMode()` guard inside the handler).
2. On right-click:
   - Call `event.preventDefault()` to suppress the browser's native menu.
   - Use Three.js `Raycaster` to detect if the click hits a waypoint sphere (detection via `userData.isWaypoint === true`, set by the existing waypoint mesh creation in `liquid_pipe_renderer.js`).
   - If no hit, do nothing (let the event bubble for other handlers).
   - If hit, extract `userData.chainIdx` and `userData.wpIdx` (already present on waypoint meshes from Phase 6).
   - Resolve the pipe model: iterate `state.models` for `pipe_segment` models and pick the one matching the waypoint's chain origin.
3. Render the context menu:
   - Create a `<div id="liquid-pipe-context-menu">` appended to `document.body`.
   - Position at `event.clientX`, `event.clientY` with `position:fixed`.
   - Single menu item: `升级为T型分支 / Upgrade to T-Branch`.
   - On click, call `window._upgradeToTBranch(pipeId, waypointIndex)` and close the menu.
4. Menu dismissal:
   - Click outside the menu → close (global click listener registered with `{once: true}`).
   - Escape key → close.
   - Right-click elsewhere → close and re-open at the new location.

**Visual spec**:
```
┌──────────────────────────────────┐
│ 升级为T型分支 / Upgrade to T-Branch│
└──────────────────────────────────┘
  dark background (#2a2a3e)
  1px border (#555)
  12px monospace font
  hover: background lightens to #3a3a5e
```

### 3.3 Task G.2: Pipe Split on Junction Upgrade

**Goal**: When a waypoint is upgraded to a junction, the original pipe must be split into two new pipes (one before the junction, one after), and the junction becomes the logical middle.

**Current `_upgradeToTBranch` implementation**:
- Creates a `pipe_junction` model at the waypoint position.
- Adds an entry to `pipe.pipe.junctions[]` array (a marker).
- The original pipe is **not** actually split; both sides of the waypoint still belong to the same `pipe_segment`.

**New behavior**:
1. Take a snapshot of the original pipe's waypoints.
2. Find the waypoint at `waypointIndex`, call it `W`.
3. Create a new `pipe_segment` **Pipe_A** with:
   - `name`: original name + `_A`
   - `startPortId`: original `startPortId`
   - `endPortId`: `junction.id + '.port_1'`
   - `waypoints`: waypoints[0..waypointIndex], with the last waypoint at `W`
   - `color`: same as original
4. Create a new `pipe_segment` **Pipe_B** with:
   - `name`: original name + `_B`
   - `startPortId`: `junction.id + '.port_2'`
   - `endPortId`: original `endPortId`
   - `waypoints`: waypoints[waypointIndex..end], with the first waypoint at `W`
   - `color`: same as original
5. Delete the original `pipe_segment` from `state.models`.
6. Create the `pipe_junction` model at `W`:
   - `anchor`: `{x: W.x, y: W.y, z: W.z}`
   - `junction.type`: `'T_branch'`
   - `junction.quaternion`: identity `{x:0, y:0, z:0, w:1}`
   - `junction.ports`: array of 3 port definitions (see G.3)
   - `junction.connectedPipes`: `[Pipe_A.id, Pipe_B.id, null]` — the null slot is Port_3 (unconnected).
7. Push all new models to `state.models`; call `rebuildGeometry()`; set selection to the junction.

### 3.4 Task G.3: Three-Port Visualization

**Goal**: Render the junction as a 3D cube with three visible port spheres. Users can directly click/drag Port_3 to start a new pipe.

**Data model extension** for `pipe_junction`:
```javascript
junction: {
  type: 'T_branch',
  quaternion: { x: 0, y: 0, z: 0, w: 1 },
  ports: [
    // Port positions are in LOCAL coordinates relative to junction anchor
    { id: 'port_1', offset: { x: -0.04, y: 0, z: 0 }, type: 'inout', connectedPipeId: null },
    { id: 'port_2', offset: { x:  0.04, y: 0, z: 0 }, type: 'inout', connectedPipeId: null },
    { id: 'port_3', offset: { x: 0, y: 0, z:  0.04 }, type: 'out',   connectedPipeId: null },
  ],
  connectedPipes: []  // Deprecated, kept for backward compat
}
```

**Rendering**:
1. `liquid_pipe_renderer.js` gains a new function `renderPipeJunction(junctionModel)`.
2. For each junction model:
   - Create a `THREE.BoxGeometry(0.06, 0.06, 0.06)` mesh at `anchor` with `quaternion` applied.
   - For each of the 3 ports:
     - Compute world-space position: `anchor + quaternion.rotate(port.offset)`.
     - Create a `THREE.SphereGeometry(0.015)` sphere.
     - Color logic:
       - `port.connectedPipeId === null` → lime green `#00ff88` (free, draggable)
       - `port.connectedPipeId !== null` → gray `#666666` (already connected)
     - Set `sphere.userData = { isJunctionPort: true, junctionId: model.id, portId: port.id }`.
   - Add all 4 meshes (cube + 3 spheres) to `_pipeGroup`.

**Interaction**:
- When pipe creation mode is active (`_isPipeCreationMode() === true`), junction port spheres are also raycast targets (extend `_pickPortAtMouse` to search both `_portIndicators` and junction ports).
- Clicking a free junction port (Port_3 where `connectedPipeId === null`) starts a new pipe from that port.
- The new pipe's `startPortId` is `junctionId + '.' + portId`.
- After successful connection, update the junction's `ports[i].connectedPipeId` to the new pipe's ID.

### 3.5 Task G.4: Rotation Gizmo

**Goal**: When a `pipe_junction` is selected (left-click in liquid mode), attach Three.js TransformControls in rotation-only mode, restricted to the Z axis.

**Implementation**:
1. `liquid_mode.js` adds a function `_attachJunctionGizmo(junctionModel)`.
2. Called from a selection hook: when `setSelection('model', id)` is called and the selected model's `kind === 'pipe_junction'`, invoke `_attachJunctionGizmo()`.
3. Inside the function:
   - Find the junction's root mesh in the scene (by traversing `_pipeGroup.children` for `userData.junctionId === model.id`).
   - Call `transformControls.setMode('rotate')`.
   - Call `transformControls.attach(mesh)`.
   - Restrict axes: `transformControls.showX = false; transformControls.showY = false; transformControls.showZ = true;`.
4. Listen for rotation change events:
   - On `objectChange`, extract the junction mesh's current quaternion and write it back to `model.junction.quaternion`.
   - Call `rebuildGeometry()` to re-render port sphere positions with the new orientation.
   - Any pipe connected to Port_1/Port_2/Port_3 should recompute its endpoint waypoint.
5. When the selection changes away from the junction (or mode switches back to air), detach the gizmo.

**Hook location**: Junction selection and gizmo attach should happen alongside the existing transform gizmo logic. Look for `transformControls.attach` in `STL_generate.html` and add a branch before the existing code path for air models (air models are read-only in liquid mode per Phase C).

### 3.6 Task G.5: Pipe Recomputation on Junction Rotate

When the junction rotates:
1. For each port, compute its new world position: `anchor + quaternion.rotate(port.offset)`.
2. For each pipe in `state.models` where `pipe.startPortId === junctionId + '.port_X'` or `pipe.endPortId === junctionId + '.port_X'`:
   - Update the pipe's first or last waypoint to match the new port position.
   - Call `rebuildGeometry()` to re-render the tube geometry.

---

## 4. Phase H: Legacy Project Migration E2E Validation

### 4.1 Overview

Run all legacy `.cfdproj` files through the migration pipeline and confirm the resulting liquid model tree is correct. Fix any bugs uncovered.

### 4.2 Task H.1: Discover Legacy Liquid Projects

```bash
grep -l 'rdhx\|liquidCooling\|coldPlates' /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/projects/*.cfdproj
```

Expected output: 3-5 files. Save to a list.

### 4.3 Task H.2: Automated E2E Test Loop

For each liquid project:

1. Stop any running server on port 8005 (`fuser -k 8005/tcp`).
2. Create a fresh `/tmp/migration_test_<N>/model/` directory.
3. Start the server pointing to that directory.
4. Use browser-use to:
   - Open `http://localhost:8005/STL_generate.html`
   - Login with admin credentials
   - Click "加载工程 / Load Project" button
   - Select the legacy project file (use file upload API if drag-drop is unreliable)
   - Wait for geometry to load (2-3 seconds)
5. Verify in the browser:
   - Take a screenshot of the geometry tab showing the air cooling tree.
   - Click "液冷模拟" button to switch to liquid mode.
   - Take a screenshot of the liquid geometry tree.
   - Scroll through the tree and verify CRH / cold plate / pipe models appear.
6. Export `cfd_export.json` via the existing export button.
7. Read the exported JSON on the host side and verify:
   - `state.schemaVersion === 2` (or whatever indicator we pick)
   - No legacy `rdhx` fields inside rack models
   - No legacy `liquidCooling.coldPlates` inside equipments
   - `state.liquidCooling.piping.chains` is empty
8. Record pass/fail per project.

### 4.4 Task H.3: Fix Bugs, Re-test

For any failing project:
1. Read the browser console for JS errors.
2. Read the migration function output.
3. Fix the bug in `migrateLiquidCoolingToKinds()`.
4. Re-run the test for that specific project.

Common edge cases to check:
- Rack with `rdhx: {enabled: false}` — should NOT create a CRH model.
- Rack with no `rdhx` field at all — should not throw.
- Equipment with `liquidCooling: {enabled: true, coldPlates: []}` — empty array, should not create models.
- Equipment with `liquidCooling` but missing `thermalModel` on a cold plate — should use defaults.
- `state.liquidCooling` entirely absent — migration should skip the chains section gracefully.
- Project already migrated (`schemaVersion: 2`) — migration should be idempotent.

### 4.5 Task H.4: Commit Fixes + Document Validated Projects

Once all legacy projects pass:
- Commit any migration fixes with message `fix(liquid): Phase H — migration E2E fixes for legacy project X`.
- Write a brief test report to `/tmp/migration_test_report.md` (not committed) listing which projects were validated and any quirks.

---

## 5. Phase I: Visual Consistency Audit (Item 4a)

### 5.1 Overview

Systematically screenshot every liquid cooling view and identify visual bugs, inconsistencies, and polish issues. Apply fixes inline.

### 5.2 Task I.1: Build the Audit Scene

Use browser-use to create a standardized test scene:

1. New project
2. Create 2 Rack models (Rack_1, Rack_2) via air mode toolbar
3. Enter each rack and add 3 fanServer equipments
4. Switch to liquid mode
5. Create 1 CDU from the toolbar
6. Set CDU.servedRacks = [Rack_1, Rack_2] via inspector
7. Select Rack_1, click "为Rack_1建CRH" → creates CRH
8. For Rack_1's first server: click "为Server建热源" → creates heat source
9. For Rack_1's second server: create a cold plate via the context menu
10. Create a pipe: CDU supply port → Rack_1 manifold in
11. Create a return pipe: Rack_1 manifold out → CDU return

Save this as `/tmp/audit_scene_setup.sh` for reuse.

### 5.3 Task I.2: Capture Audit Screenshots

Capture these 6 screenshots:

| # | Scenario | Expected Observations |
|---|----------|----------------------|
| 1 | Liquid mode full view — all panels visible | 3D viewport + liquid tree + liquid toolbar |
| 2 | Liquid mode — CDU selected | Right-side inspector shows CDU parameters |
| 3 | Liquid mode — Rack_1 selected | Contextual buttons (CRH, cold plate) active |
| 4 | Air mode — liquid models dimmed | Rack at full opacity, CDU/CRH at 35% |
| 5 | Liquid mode — pipe creation in progress | Port indicators visible, rubber-band line following cursor |
| 6 | Liquid mode — junction rotation gizmo attached | Z-axis rotation ring visible on junction cube |

### 5.4 Task I.3: Common Issues to Find + Fix

The audit should proactively check:

- **Color consistency**: CDU blue (`#00b4dc`) vs CRH blue vs cold plate blue — are they distinct enough?
- **Spacing**: Right-side toolbar button gaps, padding
- **Z-fighting**: Semi-transparent CRH panel overlapping Rack back face
- **Icon alignment**: Tree icons and labels baseline
- **Text truncation**: Long rack/server names in the tree
- **Contextual button text**: Does "为Rack_1建CRH" truncate on short panels?
- **Scroll overflow**: When too many cold plates/pipes exist, does the tree scroll?
- **Port indicator size**: Are blue/red port spheres visible without being too large?
- **Pipe-geometry intersection**: Do pipes pass through rack/ACU geometry visibly?
- **Mode switch transition**: Any flicker or stale state after `toggleDisplayMode()`?

For each issue found, apply a direct fix (CSS/constant change) and re-screenshot to verify.

### 5.5 Task I.4: Commit Polish Fixes

Each logical group of fixes gets its own commit:
- `polish(liquid): Phase I — fix junction Z-fighting with rack back face`
- `polish(liquid): Phase I — increase port indicator visibility`
- etc.

---

## 6. Phase J: Reactive CSS Polish (Item 4b, DEFERRED)

**Not in this spec. Not in the implementation plan.**

After Phases G/H/I land, the user will run the application and report subjective visual preferences. Each feedback item produces a small, targeted commit without going through brainstorming again.

---

## 7. File Impact Summary

| File | Phase | Change Type |
|------|-------|-------------|
| `web/js/liquid_mode.js` | G.1, G.2, G.4 | Modify — add context menu, rewrite `_upgradeToTBranch`, add gizmo attachment |
| `web/js/liquid_pipe_renderer.js` | G.3, G.5, I | Modify — render junction as cube + 3 ports, recompute pipes on rotate, visual polish |
| `web/STL_generate.html` | G.4, H | Modify — selection hook for junction gizmo, `migrateLiquidCoolingToKinds` fixes |
| `/tmp/migration_test_*/` | H | Ephemeral — test fixtures, not committed |

No backend changes in this phase.

---

## 8. Acceptance Criteria

### Phase G

- Right-clicking a pipe waypoint in liquid mode shows a context menu with "Upgrade to T-Branch".
- Clicking the upgrade menu item splits the pipe at that waypoint into two new pipes and creates a junction model at the split point.
- The junction renders as a cube with 3 visible port spheres: 2 gray (connected) and 1 green (free).
- Selecting a junction attaches a Z-axis-only rotation gizmo.
- Rotating the junction moves the green port sphere around the Z axis.
- Clicking the free green port starts a new pipe creation from that port.
- Connected pipes re-route their endpoints when the junction rotates.

### Phase H

- All legacy `.cfdproj` files with liquid cooling data (`rdhx`, `liquidCooling.coldPlates`, `piping.chains`) load successfully.
- After load, legacy data is removed from the state (`rdhx` field absent, nested cold plates empty).
- Corresponding `crh`, `cold_plate`, `heat_source`, `pipe_segment` models appear in `state.models`.
- `state.schemaVersion === 2` after migration.
- Liquid geometry tree shows the migrated models under correct parent racks.
- No JS console errors during migration.
- Idempotent: loading a migrated project a second time does not create duplicate models.

### Phase I

- 6 audit screenshots captured and reviewed.
- Any visual issue listed in §5.4 is either fixed or documented as acceptable.
- CDU blue, CRH blue, and cold plate blue are visually distinct.
- Toolbar buttons have consistent spacing and alignment.
- No Z-fighting on CRH panels.
- Tree labels do not truncate for typical project names.

---

## 9. Open Questions (Deferred)

- **Multi-axis junction rotation**: Initially restricted to Z-axis. Full XYZ rotation is a future enhancement once users report needing it.
- **Junction with 4+ ports**: Out of scope. Only T-branch (3 ports) in this spec.
- **Pipe re-routing algorithm**: When a junction rotates, the connected pipe's first/last waypoint is updated. Whether intermediate waypoints should also auto-adjust (to keep the pipe smooth) is deferred — current design just snaps the endpoint.
- **CSS polish Phase J**: Completely reactive, no upfront plan.
