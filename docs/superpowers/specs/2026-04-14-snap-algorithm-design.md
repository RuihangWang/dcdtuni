# 3D Object Snap Algorithm Redesign

**Date:** 2026-04-14
**Status:** Design — awaiting approval
**Owner:** TBD

## Problem Statement

When dragging a baffle (or any movable object) in the 3D viewport, especially one
whose `model.transform.quaternion` is non-identity (rotated baffles in e.g.
`case/Tri_angle`), the object **jitters** and fails to reliably snap to target
surfaces or edges of neighbors.

The current implementation in `STL_generate.html` has four systemic issues:

1. **Cumulative snap application** — `item.anchor.x += snap.x` (snap is layered
   on top of each frame's existing anchor, causing positional drift).
2. **No hysteresis** — once snapped, the algorithm re-evaluates every frame with
   no stickiness, so the nearest candidate can flip-flop.
3. **Face snap disabled for rotated models** — `if (!_hasRot)` gate strips the
   most common alignment type the moment any rotation is introduced.
4. **No screen-space / camera-aware threshold** — threshold fixed at
   `avg_size × 0.08` at drag-start, never updated; back-facing candidates are
   not excluded.

Additionally the current module lives inline in the 26k-line HTML, making it
difficult to unit-test and reason about in isolation.

## Goals

1. Eliminate jitter during drag of rotated (and un-rotated) objects.
2. Support face-to-face, edge-to-edge, and vertex-to-vertex snap, prioritized
   **F > E > V**.
3. Support **rotation snap** — when enabled together with an active rotation
   gizmo, align the moving object's face normal to the nearest peer face normal.
4. Provide four independent toolbar toggles (Face / Edge / Vertex / Rotation).
5. Scale to scenes with several thousand peer models (typical DC case).
6. Preserve the existing `shift`-to-15°-step behavior on the rotation gizmo.
7. Extract the snap module to a standalone file (`web/js/snap.js`) to keep
   `STL_generate.html` manageable and enable isolated unit testing.

## Non-Goals

- Arbitrary midpoint / perpendicular / tangent snap modes (future work).
- Changing the rotation gizmo interaction or the `shift` step key.
- 2D (top-down) snap behavior — this design only covers the 3D viewport drag.
- Automatic re-orientation when rotation snap is off.

---

## 1. Architecture

### 1.1 File layout

```
STLGeneratorForOpenFoam/web/
├── STL_generate.html                 # slimmed: remove computeProximitySnap,
│                                     #   initSnapCache, and modelMove-branch
│                                     #   snap logic
└── js/
    └── snap.js                       # new (~600-800 LOC)
```

`snap.js` is loaded via an ordinary `<script src="/js/snap.js"></script>` after
Three.js and before the main inline script.

### 1.2 Module shape — injection-based factory

`snap.js` registers a single symbol on `window`:

```js
window.createSnapModule({
    getModelById:  (id)  => Model | null,
    getModelOBB:   (id)  => { center, axes, half } | null,
    getAllModels:  ()    => Model[],
    getCameraDir:  ()    => THREE.Vector3,
    THREE:         THREE
});
```

Returns an API object:

```
{
    setConfig(partial):              // { face?, edge?, vertex?, rotation? }
    getConfig():                     // current config snapshot
    beginDrag(movingIds, opts):      // opts.rotationGizmoActive
    updateDrag(pointerDelta): {
        snapDelta:  Vector3 | null,  // additive correction applied ONCE per frame
                                     //   on top of anchorStart + pointerDelta
        snapQuat:   Quaternion | null,  // rotation target when all rotation-snap
                                        //   conditions met; else null
        highlight:  { kind, world, peerModelId } | null
    }
    endDrag():                       // clear per-drag state
    getHighlight():                  // latest highlight for overlay renderer
    invalidate():                    // called by host on peer/model add/delete/transform
}
```

### 1.3 Replacing existing code

| Current (to remove) | Replaced by |
|---|---|
| `computeProximitySnap(movingIds, snapFace, snapEdge)` (~L12545-L12652) | `snapModule.updateDrag(delta)` |
| `initSnapCache()` / `querySnapCandidates()` (~L12480-L12543) | Internal `snap.js` spatial grid |
| `state.snapFace`, `state.snapEdge` bool fields | `state.snapConfig = { face, edge, vertex, rotation }` (migration shim reads old keys once) |
| snap-applied-cumulatively block in `updatePatchDrag` modelMove branch (~L15367-L15377) | Single call to `snapModule.updateDrag`, apply `snapDelta` once to anchor, apply `snapQuat` if present |

### 1.4 Candidate data model

```js
SnapCandidate = {
    kind:         'face' | 'edge' | 'vertex',
    sourceModelId: string,
    localIndex:    number,          // 0..5 / 0..11 / 0..7
    world: {
        // face:   { center: Vec3, normal: Vec3, u: Vec3, v: Vec3, sizeU: num, sizeV: num }
        // edge:   { p0: Vec3, p1: Vec3, dir: Vec3, length: num }
        // vertex: Vec3
    }
};

SnapActiveState = {
    movingIds:          string[],
    rotationGizmoActive: boolean,
    currentCandidate:   SnapCandidate | null,
    currentMovingPrim:  { kind, localIndex, world }  | null,
    entryDistance:      number,
    snappedDelta:       Vec3   | null,
    snappedQuat:        Quat   | null
};
```

---

## 2. Algorithm

### 2.1 Constants (tunable)

```js
const SNAP_ENTRY_THRESHOLD     = 0.10;   // 10 cm, world units
const SNAP_EXIT_MULT           = 1.5;    // exit_radius = entry * 1.5
const SNAP_PARALLEL_TOLERANCE  = 0.97;   // cos ≈ 0.97 ≈ 14° — parallel enough
const SNAP_SWAP_SCORE_RATIO    = 1.3;    // candidate switch needs new score > old × 1.3
const CAMERA_FACING_EPSILON    = 0.01;
const GRID_CELL_SIZE           = 0.30;   // broad-phase 3D hash cell = entry × 3
const C_FALLBACK               = 150;    // if neighbors > 150, degrade to face-only
const SNAP_FACE_DOMINANT_SCORE = 3.8;    // early-terminate edge/vertex scan above this
```

### 2.2 OBB → primitives

```js
function expandOBBToPrimitives({ center, axes, half }) {
    // 8 corners, 12 edges (4 bottom + 4 top + 4 vertical), 6 face planes
    // Each face: center + normal + two in-plane tangents + size along each
    // Vertices produced from the corner enumeration; edges reference vertex
    // indices; faces derived from axis + signed half extent.
}
```

Both moving and peer objects go through the same expansion. Peer expansion is
cached per-drag (peers don't move during drag).

### 2.3 Broad phase — spatial hash grid (critical for N ≈ thousands)

Built once per `beginDrag()`:

```js
const grid = new Map();                        // cellKey → ModelId[]
for (const peer of getAllModels().filter(m => !movingIds.has(m.id))) {
    const aabb = getWorldAABB(peer);           // expand OBB to world AABB
    for each cell intersecting aabb:
        grid.get(cellKey).push(peer.id)
}
```

Per frame:
```js
const movingAABB    = getWorldAABB(moving);
const expandedAABB  = movingAABB.expandByScalar(SNAP_ENTRY_THRESHOLD * SNAP_EXIT_MULT);
const candidateIds  = union of grid cells touching expandedAABB;
// typical |candidateIds| = 5-50
```

### 2.4 Narrow phase — filter + score

For each candidate peer, expand its OBB to primitives (lazy + cached), then:

**Face candidate accepted iff:**
- `|movingFace.normal · peerFace.normal| > SNAP_PARALLEL_TOLERANCE`
- `|signedDistance(movingFace.center, peerFace plane)| < SNAP_ENTRY_THRESHOLD`
- movingFace center projection onto peerFace lies within peerFace rectangle
  (allowing ½ overhang on each side)
- `peerFace.normal · cameraDir < -CAMERA_FACING_EPSILON` (camera-facing only)

**Edge candidate accepted iff:**
- `|movingEdge.dir · peerEdge.dir| > SNAP_PARALLEL_TOLERANCE`
- `shortestSegmentSegmentDistance(movingEdge, peerEdge) < SNAP_ENTRY_THRESHOLD`

**Vertex candidate accepted iff:**
- Euclidean distance < SNAP_ENTRY_THRESHOLD

**Scoring:**
```
baseWeight = face 3.0, edge 2.0, vertex 1.0
distanceScore = 1 - (dist / SNAP_ENTRY_THRESHOLD)         # 0..1
overlapBonus  = face only, bonus for large projected overlap
score = baseWeight + distanceScore [+ 0.5 × overlapBonus]
```

Candidates sorted by score; pool only includes kinds enabled via `state.snapConfig`.

### 2.5 Hysteresis state machine

```
State: UNSNAPPED | SNAPPED(C)

Transitions:
    UNSNAPPED   → SNAPPED(C)     if best candidate C within entry_radius (pickBest returns non-null)
    SNAPPED(C)  → UNSNAPPED      if current-distance(C) > entry_radius × SNAP_EXIT_MULT
    SNAPPED(C)  → SNAPPED(C')    only if score(C') > score(C) × SNAP_SWAP_SCORE_RATIO
    SNAPPED(C)  → SNAPPED(C)     otherwise (stickiness)
```

Implemented inside `pickBest()`. The state carries over per-frame via
`state.snapActive.currentCandidate`.

### 2.6 Application — single-frame correction (not cumulative)

Critical fix. In each `updateDrag(pointerDelta)`:

```js
// Host already did:
//   for (id of movingIds) model.anchor = anchorStart[id] + deltaLocal
// Host then calls updateDrag(pointerDelta); snap returns a one-shot correction.
function computeSnapDelta(cand, moving, peer) {
    // face:   translate along peerFace.normal to zero signedDistance
    // edge:   perpendicular translation to coincide edges
    // vertex: straight translation to coincide vertices
    return Vector3;
}
const snapDelta = state.snapActive.currentCandidate
    ? computeSnapDelta(state.snapActive.currentCandidate, movingPrim, peerPrim)
    : null;
```

Host applies `snapDelta` **once** per frame:
```js
model.anchor.x += snapDelta.x;  // this is THIS frame's correction,
model.anchor.y += snapDelta.y;  //   not a running accumulator
model.anchor.z += snapDelta.z;
```

Because anchor is reset to `anchorStart + deltaLocal` each frame BEFORE snap, no
drift accumulates across frames.

### 2.7 Rotation snap

Triggered iff **all four** conditions hold:

1. `state.snapConfig.rotation === true`
2. `opts.rotationGizmoActive === true` (passed at `beginDrag`)
3. `state.snapConfig.face === true`
4. Exists a peer face candidate with normal parallelism in `[0.85, 0.97)`
   (i.e., "almost aligned but not quite")

When all met, select the same `movingFace` that produced the best accepted
face candidate (i.e. the one that scored highest and is the active hysteresis
target) and compute `snapQuat` = shortest-arc quaternion that rotates
`movingFace.normal` onto `peerFace.normal`. Host applies:

```js
model.transform.quaternion = snapQuat.toArray();
syncModelRenderTransform(model);
```

If already parallel (score ≥ 0.97), `snapQuat = null` (no further rotation
needed).

Rotation snap is **suppressed during active rotation-gizmo drag** — the user
shift-step behavior takes precedence.

### 2.8 Early termination (performance)

In `pickBest()`:

```js
if (config.face) {
    const best = filtered.faces[0];
    if (best.score > SNAP_FACE_DOMINANT_SCORE) return best;
}
// ... else scan edges, then vertices
```

---

## 3. UX / Toolbar Integration

### 3.1 Top toolbar buttons

Add a 4-button group to the main toolbar:

| Button | Default | Purpose |
|---|---|---|
| 🔲 **F** (Face) | **ON**  | Snap face-to-face |
| 📏 **E** (Edge) | **ON**  | Snap edge-to-edge |
| 📍 **V** (Vertex) | OFF | Snap vertex-to-vertex |
| 🔄 **R** (Rotation) | OFF | Rotation-snap when gizmo active |

- Button style: gray baseline; orange border + subtle glow when active.
- Click toggles; writes to `snapModule.setConfig({ [kind]: bool })`.
- Persisted to `localStorage['stlgen.snapConfig']` and restored on reload.
- Tooltip explains each mode (Chinese + English).

### 3.2 Rotation-snap activation rule

```
Rotation snap fires iff:
    1. R toggle ON
    2. Rotation gizmo active (user selected baffle + clicked rotate tool)
    3. F toggle ON (face is the rotation reference)
    4. Best face candidate parallelism in [0.85, 0.97) — 已接近但未对齐
```

`shift`-to-15° behavior on the rotation gizmo itself is untouched. When the
user is actively dragging the rotation gizmo, snap's rotation correction is
suppressed (so shift steps aren't overridden).

### 3.3 Visual feedback

| Element | Color | Form |
|---|---|---|
| Target face | `rgba(255,204,0,0.25)` fill + `#ff9900` border | `THREE.Mesh` quad overlay |
| Target edge | `#ffcc00`, 3-px line | `THREE.LineSegments` |
| Target vertex | `#ffcc00`, 6-px radius | `THREE.Points` |
| Rotation-snap indicator | dashed yellow line from baffle center along target normal | `THREE.Line` with `LineDashedMaterial` |
| Cursor hint | small 14-px circular badge showing F / E / V / R | DOM overlay, position follows pointer |

Render target: a dedicated `snapHighlightGroup` child of the scene, cleared and
rebuilt each frame based on `snapModule.getHighlight()`.

### 3.4 Undo / redo integration

Snap corrections are not separately undoable. The existing `pushUndoCheckpoint`
at drag-start captures the full pre-drag state; a single Ctrl+Z reverts
position AND rotation together. No new checkpoint calls are needed.

---

## 4. Performance & Scalability

### 4.1 Scale assumption

- N = up to several thousand peer models.
- Typical baffle-drag frame sees 5-50 peers within snap radius; rare worst
  case 150-300 (inside dense racks).

### 4.2 Complexity

| Phase | Op | Cost |
|---|---|---|
| `beginDrag` | Build 3D spatial grid | O(N × avgCellsPerModel) ≈ O(5N) |
| `updateDrag` broad phase | Grid query over expanded AABB | O(cellsTouched) ≈ O(1-8) |
| `updateDrag` narrow phase | Filter candidates (C peers × 26² pair tests) | O(C) |

Target: `updateDrag` P99 < 8 ms, P50 < 2 ms for C ≤ 50.

### 4.3 Caches

| Cache | Scope | Invalidation |
|---|---|---|
| Peer AABB | per drag | `invalidate()` or new drag |
| Peer OBB primitives (lazy) | per drag | same |
| Spatial grid | per drag | same |

### 4.4 Grid-rebuild triggers (outside drag)

`snapModule.invalidate()` is called by host on:

1. Model added / deleted / imported
2. Any peer's `anchor` or `transform.quaternion` changed (programmatically,
   via numeric input, via gizmo)
3. `undo` / `redo`

The host wires these to existing hooks (`rebuildGeometry`, `updateModel`,
`undo/redo` stacks).

### 4.5 Fallback mode for dense scenes

When broad-phase returns > `C_FALLBACK` (150) candidates for the current frame:

- Skip 12-edge and 8-vertex expansion per peer (for THIS frame only; the user's
  toolbar toggles for E/V are unchanged).
- Compare only each peer's 6 face centers against moving's 6 face centers.
- Edge + vertex snap produce no candidates **this frame** (algorithm degraded,
  not the user's configuration).
- Cursor badge shows `F*` to indicate degraded mode is active.
- Once neighbor count drops back below `C_FALLBACK`, full algorithm resumes.

User acceptance: dense interior scenes already have poor visual separation,
face-only snap is sufficient for placement in practice.

### 4.6 Memory envelope

- N = 3000 peers: AABB cache ≈ 144 KB; grid ≈ 240 KB; lazy-primitive cache up
  to ≈ 7.5 MB worst case. All acceptable in a browser tab.

---

## 5. Testing

### 5.1 Unit tests (`web/js/snap.test.js`)

Runs under `vitest` in Node (no browser); `deps` are mocked with plain objects.

| Group | Cases |
|---|---|
| OBB expansion | axis-aligned, rotated 45°, degenerate (0-extent) |
| Face filter | parallel @ 5 cm → snap; @ 20 cm → no; parallel 0.95 → no; back-face → no; no-overlap → no |
| Edge filter | parallel @ 3 cm → snap; skew → no |
| Vertex filter | 2 cm → snap; 20 cm → no |
| Hysteresis | UNSNAPPED → SNAPPED; small jitter stays locked; exceed exit → release; swap needs 1.3× ratio |
| Rotation snap | all four conditions → snapQuat; gizmo off → null; already-parallel → null |
| Fallback | C > C_FALLBACK → face-only path works, no crash |

### 5.2 Integration tests (`browser-use` headless)

**Scenario 1 — flush placement**

1. Create rack (unrotated) + baffle (unrotated).
2. Drag baffle to ~8 cm from rack side face.
3. Expect: baffle face coplanar with rack face; face overlay drawn.

**Scenario 2 — rotation alignment**

1. Create rack rotated 45° (Z axis) + baffle rotated 44°.
2. Enable F + R in toolbar; select baffle; activate rotation gizmo.
3. Switch back to move tool; drag baffle toward rack face.
4. Expect: baffle quaternion corrects to exactly 45°; baffle face snaps flush.

**Scenario 3 — anti-jitter**

1. Create two adjacent racks 12 cm apart.
2. Drag baffle into the 6-cm midpoint between them.
3. Oscillate pointer ±2 cm horizontally.
4. Expect: baffle stays locked to one rack (no ping-pong); releases only when
   pointer moves > 15 cm away.

**Scenario 4 — large-scene performance**

1. Programmatically create 5000 random boxes in a 20×20×5 m region.
2. Drag a baffle across the scene center.
3. Measure `updateDrag` timing (enable `window.__SNAP_PROFILE`).
4. Expect: P99 < 8 ms, P50 < 2 ms.

### 5.3 Manual smoke-test checklist (every PR)

```
☐ Open Tri_angle.cfdproj; create baffle next to the 45°-rotated rack.
☐ Select baffle → activate rotation gizmo → manually rotate ~45°.
☐ Enable F + R in toolbar.
☐ Switch to move; drag baffle toward rack side face.
☐ Verify: baffle rotation corrects to exactly 45°.
☐ Verify: baffle face snaps flush to rack face.
☐ Verify: yellow face overlay drawn on rack side.
☐ Verify: cursor shows F badge.
☐ Wiggle pointer; baffle remains stable (no jitter).
☐ Drag far (>15 cm) → baffle releases and follows cursor again.
☐ Ctrl+Z once → reverts both position and rotation in one step.
☐ Disable R; retry → position snaps flush, rotation unchanged.
☐ Disable F, enable E → only edges snap.
☐ Enable V → three types independently gateable.
```

### 5.4 Performance instrumentation (opt-in)

```js
if (window.__SNAP_PROFILE) {
    const t0 = performance.now();
    // ... updateDrag work ...
    (window.__snapTimings ??= []).push(performance.now() - t0);
}
```

Off by default. Developer enables via console, drags several times, inspects
`__snapTimings` distribution.

---

## 6. Edge Cases

| Scenario | Expected behaviour | Enforcement |
|---|---|---|
| Empty scene (only the moving model) | No snap; baffle follows cursor | `filterCandidates` returns empty |
| Moving and peer exactly coincident | signedDist = 0, full score, locks cleanly | No divide-by-zero in `computeSnapDelta` |
| Degenerate peer (0-extent axis) | Skipped, no primitives generated | `expandOBB` guards `half[i] > 1e-6` |
| Giant room wall peer | AABB covers many cells; still bounded work | Grid query linear in cells |
| Multi-select drag | Primary (first) model drives snap; others follow | `snap` tracks only `movingIds[0]` |
| Moving a rack (has children) | Use rack outer OBB, don't recurse into equipments | `getModelOBB(rackId)` already top-level |
| Camera rotates during drag | Candidate visibility recomputed next frame | `getCameraDir()` called per frame |
| Peer transform changed externally during drag | Host calls `invalidate()`, grid rebuilds | `beginDrag` checks `dirty` flag |
| Rotation snap vs shift-15° conflict | shift-15° wins (explicit user intent) | Snap suppressed while gizmo drag active |
| NaN / Inf in geometry | Filtered out, no snap proposed | `expandOBB` runs `Number.isFinite` checks |
| Camera exactly edge-on (normal · dir = 0) | Treated as visible | `CAMERA_FACING_EPSILON = 0.01` guard |

---

## 7. Migration

The existing `state.snapFace` / `state.snapEdge` boolean fields are superseded
by `state.snapConfig`. On load (in the order stated, each earlier source wins):

```js
// 1. localStorage explicit value (user's last session)
// 2. legacy state.snapFace / state.snapEdge (pre-migration projects)
// 3. defaults: F=true, E=true, V=false, R=false
function readStoredBool(key, legacy, dflt) {
    const ls = localStorage.getItem('stlgen.snapConfig.' + key);
    if (ls === 'true')  return true;
    if (ls === 'false') return false;
    if (typeof legacy === 'boolean') return legacy;
    return dflt;
}
state.snapConfig = {
    face:     readStoredBool('face',     state.snapFace, true),
    edge:     readStoredBool('edge',     state.snapEdge, true),
    vertex:   readStoredBool('vertex',   undefined,      false),
    rotation: readStoredBool('rotation', undefined,      false)
};
delete state.snapFace; delete state.snapEdge;
```

Each toggle click writes back to `localStorage['stlgen.snapConfig.<key>']`.
The two legacy fields are read once during migration, then removed from
`state`. A follow-up cleanup commit removes any other lingering references
after this lands.

---

## 8. Open Questions

None at spec-approval time. Tunables (thresholds, ratios, fallback count) will
be adjusted after real-world testing in `case/Tri_angle` and large-scene
synthetic cases.

---

## 9. Implementation Plan Sketch

(Full plan to be produced by the `writing-plans` skill after this spec is
approved.)

1. Scaffold `web/js/snap.js` with public API + empty internals.
2. Implement `expandOBBToPrimitives`, spatial grid builder.
3. Implement filter + score + hysteresis.
4. Implement one-shot `snapDelta` / `snapQuat`.
5. Replace host-side `computeProximitySnap` call site with `snapModule`.
6. Wire toolbar buttons + localStorage persistence.
7. Implement visual feedback overlay.
8. Write unit tests + integration scenarios.
9. Performance instrumentation + benchmark run.
10. Migration cleanup (remove legacy `state.snapFace`/`snapEdge`).
