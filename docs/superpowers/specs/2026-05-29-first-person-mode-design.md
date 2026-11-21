# First-Person Mode (Creative-Mode FPS Navigation + Editing)

**Date**: 2026-05-29
**Status**: Approved
**Approach**: Dedicated `js/first_person_mode.js` module + capsule-vs-BVH collision + Pointer Lock free-look + DOM HUD; delivered in two phases (navigation first, editing second)

## Goal

Add a first-person ("FPS game") mode to the STL geometry editor frontend. The user can drop into the 3D scene at human scale, walk the aisles with WASD, look freely with the mouse, jump, crouch, sprint, and toggle a free-fly mode — then manipulate the object under a center crosshair (grab-carry, nudge, rotate, delete). A single button (and the same button/hotkey) switches between the existing god view (OrbitControls) and first-person mode.

The motivating value for a data-center CFD tool: inspect a layout at human scale, check aisle clearances, demo to clients, and make quick spatial edits without leaving the immersive view.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Capability | Full first-person editing (select/move/rotate/delete) | User intent: "directly work inside the scene" |
| Movement model | Grounded walk + collision, with a free-fly toggle | User wants jump/crouch (implies gravity + floor) plus the ability to rise above the room |
| Mouse model | Always-on Pointer-Lock free-look + center crosshair | Most game-like; clicks still fire under pointer lock, so the crosshair acts as the cursor |
| Editing interaction | Both grab-carry **and** fine nudge | Grab-carry for coarse placement, arrow-key nudge for precision |
| Delivery | Two phases: Phase 1 navigation, Phase 2 editing | Navigation is independently valuable and de-risks the large feature |
| Code structure | New `js/first_person_mode.js` exposing `window.fpsMode` | Keep the 32.8k-line `STL_generate.html` lean; matches existing module pattern (`room_2d_editor.js`, `liquid_mode.js`) |
| Collision | Capsule swept against `three-mesh-bvh` | BVH already built on the scene; slides along walls; handles polygon/triangle rooms + custom STL uniformly |
| Look implementation | Native Pointer Lock API + manual euler (YXZ) | No new vendor dependency; consistent with existing manual orbit math |
| HUD / crosshair | DOM overlay (not 3D objects) | Simpler; sidesteps the tab-scoped-3D-object cleanup rules |
| Testability | `_gatherInput → _tick(dt) → _resolveCollision` split with `_injectInput()` / `_tick()` / `_setLook()` hooks | Pointer-lock + real keystrokes can't be driven headlessly |

## Rejected alternatives

- **Inlining logic into `STL_generate.html`** — rejected; the monolith is already too large (explicit user preference).
- **Per-model AABB collision** — rejected; coarse and fails on non-box (polygon/triangle) rooms. BVH is free and uniform.
- **`PointerLockControls` / `FlyControls` add-ons** — not present in the r184 vendor bundle; manual euler look avoids an add-on version hunt.
- **Hold-right-to-look (editor style)** and **pointer-lock-with-edit-toggle** — rejected in favor of always-on free-look per user choice.

## Environment facts (verified against current code)

- Camera: `THREE.PerspectiveCamera(45, aspect, 0.1, 100)`, Y-up (`WORLD_UP = (0,1,0)`), scene units in **meters**. ([STL_generate.html:1416-1418](../../../src/frontend/web/STL_generate.html#L1416))
- God view: `THREE.OrbitControls`, left-mouse reserved for select/drag, right=rotate, middle=pan, wheel=zoom. ([STL_generate.html:1435-1445](../../../src/frontend/web/STL_generate.html#L1435))
- Smooth camera moves: `startCameraTransition()` / `window.applyViewPreset()`. ([STL_generate.html:1673](../../../src/frontend/web/STL_generate.html#L1673), [:32438](../../../src/frontend/web/STL_generate.html#L32438))
- Rendering is **on-demand** via `requestRender()` → single `requestAnimationFrame(_doRender)`, not a continuous loop. ([STL_generate.html:32391-32426](../../../src/frontend/web/STL_generate.html#L32391))
- Shared handles already exposed: `window.__three = { camera, controls, renderer, scene, globalGroup }`. ([STL_generate.html:32457](../../../src/frontend/web/STL_generate.html#L32457))
- Lights are parented to the camera (LightKit), so the first-person view stays lit. ([STL_generate.html:1484-1502](../../../src/frontend/web/STL_generate.html#L1484))
- BVH is built on the scene after geometry builds (`computeSceneBVH(globalGroup)` at [:9805](../../../src/frontend/web/STL_generate.html#L9805), [:16629](../../../src/frontend/web/STL_generate.html#L16629)); `acceleratedRaycast` patched in at [:13-17](../../../src/frontend/web/STL_generate.html#L13).
- Existing selection/drag system: `state.selection` (model/patch/face/slot), `dragState`, `rebuildSingleModelForDrag`.
- Module includes live at [:1145-1155](../../../src/frontend/web/STL_generate.html#L1145); toolbar buttons at [:118-121](../../../src/frontend/web/STL_generate.html#L118); static `onclick` works (innerHTML-onclick bug does not apply to static HTML).

## Architecture

### Module & lifecycle

`window.fpsMode` reads the shared Three.js handles from `window.__three`. Public API: `enter()`, `exit()`, `toggle()`, `isActive()`.

**`enter()`**
1. Guard: refuse (log a hint) if `room2d`, liquid mode, or rack-detail mode is active, if a mesh/solve UI freeze is in effect (`state.dctwinConfig._uiFrozen`), or if WebGL is unavailable.
2. Save god-view state: `camera.position`, `controls.target`, `camera.up`, `camera.fov`.
3. `controls.enabled = false`; set `window._fpsActive = true`.
4. Compute spawn (below); set `camera.up = WORLD_UP`.
5. Request pointer lock on the renderer canvas (valid: inside the button-click user gesture).
6. Refresh BVH if stale, attach capture-phase key/mouse listeners, show crosshair + HUD, start the continuous rAF loop.

**`exit()`**: stop loop → exit pointer lock → remove listeners → hide HUD → restore saved camera + `controls.enabled = true` → `controls.update()` → `requestRender()` (back to on-demand rendering).

### Render integration

While active, the module's rAF loop runs each frame: `_gatherInput → _tick(dt) → place camera → render`. This replaces the on-demand `requestRender` path for the duration. The frame still routes through the existing render path so the ViewCube, corner-axes gizmo, FPS counter and frustum culling keep working. On `exit()`, rendering returns to on-demand.

## Phase 1 — Navigation

### Controls

| Input | Action |
|-------|--------|
| Mouse (locked) | Free-look: yaw about world-Y, pitch clamped ±89°. Crosshair fixed at screen center. |
| W / A / S / D | Move along the *horizontal* facing (pitch ignored when walking). |
| Shift | Sprint. |
| Space | Jump (only when grounded). |
| C (hold) | Crouch (lower eye height + slower). Chosen over Ctrl to avoid clashing with Ctrl+Z/Y. |
| F | Toggle fly mode (gravity off, 6-DOF; W/A/S/D follow full look direction, Space = up, C = down, collision off). |
| Esc | Exit to god view (browser unlocks pointer; detected via `pointerlockchange`). |
| Toolbar button | Enter / toggle FPS. |

### Physics & collision (meters)

- Capsule: radius ≈ 0.3 m, height ≈ 1.8 m, eye at top. Standing eye height ≈ 1.7 m, crouch ≈ 1.0 m.
- Speeds: walk ≈ 1.5 m/s, sprint ≈ 4 m/s, crouch-walk ≈ 0.8 m/s, fly ≈ 6 m/s. Gravity 9.81 m/s²; jump apex ≈ 0.7 m.
- Grounded collision: each frame, apply intended displacement, then a capsule-vs-BVH `shapecast` gathers nearby triangles and pushes the capsule out of penetration (standard three-mesh-bvh character-controller resolve). "Grounded" = a resolved contact whose normal points mostly up. Sub-step at high speed to prevent tunneling; cap resolve iterations.
- Floor fallback: if no floor mesh is under the player, clamp eye to `0 + eyeHeight`.
- Spawn: horizontal center of the `globalGroup` bounding box, dropped onto the floor via a downward ray, facing the scene center. Empty scene → origin with floor fallback.

### Edge cases

- Pointer-lock denied/unsupported → catch `pointerlockerror`, show "click the viewport to look", keep WASD working.
- Window/tab blur → clear all held-key state (no stuck movement).
- Resize → existing handler updates aspect; the loop renders regardless.
- Large scenes (200+ racks) → BVH query bounded to the capsule's AABB; AABB broadphase prefilter as a fallback if it ever gets heavy.

## Phase 2 — Editing (crosshair)

Under pointer lock, mouse **clicks** still fire (only motion is captured), so the crosshair behaves like a fixed center cursor; no mouse-dragging.

### Targeting loop

Every frame, a ray from the camera through screen-center raycasts the scene (BVH-accelerated). The hit model gets a highlight tint ("targeted"). Reuses `state.selection` + existing highlight rendering.

### Interaction

| Input | Action |
|-------|--------|
| Look at object | Highlights as "targeted" |
| Left-click (or E) | Grab — object locks a fixed distance ahead of the crosshair |
| While carried | Follows look/walk, snapped to floor + grid; collision-free placement (like god view) |
| Q / R | Rotate carried object yaw by snap steps (e.g. 15°) |
| Left-click / E again | Drop (commit position) |
| Arrow keys | Fine nudge selected object by a small grid step (e.g. 0.1 m) along world X/Z |
| Delete | Delete targeted/selected model (same key as god view) |

### Reuse & undo

The module **calls** existing editing internals rather than duplicating them. Each mutating action follows the mandatory rules: `pushUndoCheckpoint()` first, mutate, then `logOperation()` — **one checkpoint per grab-drop**, not per frame. Selection state is shared, so an object selected in FPS stays selected on return to god view.

### Scope boundary

In scope: select, grab-carry move, yaw rotate, fine nudge, delete. Out of scope (stays in god view): property-panel edits, create-new, multi-axis/scale gizmos.

## Cross-cutting

### Mode exclusivity

`window._fpsActive` flag. `enter()` refuses (logged hint) when another exclusive mode is active or during a mesh/solve UI freeze. Other exclusive modes already check `controls.enabled`.

### Files touched

| File | Change |
|------|--------|
| `js/first_person_mode.js` *(new)* | All logic: lifecycle, input, physics, collision, look, HUD, crosshair editing |
| `STL_generate.html` | +1 script include after [line 1155](../../../src/frontend/web/STL_generate.html#L1155); +1 toolbar button near [line 120](../../../src/frontend/web/STL_generate.html#L120) (`onclick="fpsMode.toggle()"`, `data-i18n="btn.first_person"`) |
| `i18n/*.json` | +1 label key (`btn.first_person`) per language |

No business logic is added to `STL_generate.html`.

### Test hooks (for browser-verify)

`fpsMode._state` (active, mode, position, velocity, grounded, eyeHeight, targetedModelId, carriedModelId), `fpsMode._tick(dt)`, `fpsMode._injectInput({forward,back,left,right,jump,crouch,sprint,fly})`, `fpsMode._setLook(yaw, pitch)`.

### browser-verify plan (per the Iron Law: screenshot + eval before any completion claim)

- **Phase 1**: enter → assert active + `controls.enabled===false` + spawn; inject forward + tick → moved; walk into a rack → blocked (not inside geometry); jump → y rises then settles; fly → rises above room; screenshot first-person view (crosshair visible) + read it; exit → god view restored + screenshot.
- **Phase 2**: aim at a rack → targeted + highlighted; grab → carry → tick → moved, on floor, grid-snapped; rotate key → yaw stepped; drop → committed + undo checkpoint present; nudge → small step; delete → removed + undoable. Screenshot each.

## Acceptance criteria

**Phase 1**
1. Toolbar button enters FPS; pointer locks; crosshair + HUD appear; OrbitControls disabled.
2. WASD walks at eye height; Shift sprints; Space jumps; C crouches; mouse looks freely (pitch clamped).
3. Player cannot pass through racks/walls/floor (capsule-vs-BVH collision); never falls through the floor.
4. F toggles free-fly (no gravity/collision, Space/C = up/down) and back.
5. Esc / button exits; god view restored exactly (position/target/up/fov); rendering returns to on-demand.
6. Live browser-verify evidence (screenshots + eval) for each of the above.

**Phase 2**
7. The object under the crosshair highlights as targeted.
8. Grab-carry moves an object (floor + grid snapped); Q/R rotate yaw; drop commits; arrow keys nudge.
9. Delete removes the targeted object.
10. Every spatial edit is a single undo step (`pushUndoCheckpoint` + `logOperation`); selection carries over to god view.
11. Live browser-verify evidence for each of the above.

## Future work (out of scope)

- First-person property editing (would need a pointer-unlock sub-mode or an in-view HUD).
- Create-new / scale / multi-axis gizmos in FPS.
- Configurable keybindings + a settings panel.
- Optional minimap or compass HUD.
- Optional collision in fly mode.
