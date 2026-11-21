# Liquid Rack Manifold Mounting Face (Inlet ↔ Outlet) — Design

- **Date:** 2026-06-08
- **Status:** Approved design (pending spec review) → next: implementation plan
- **Area:** Frontend STL editor (liquid cooling), liquid network graph export

## 1. Summary

Add a per-rack control that lets the user mount a liquid rack's two manifolds
(歧管: one cold **supply** + one hot **return**) on either the rack's **inlet
face** (front, current behaviour) or its **outlet face** (back). The supply/return
pair is never split or duplicated — the rack always keeps exactly one inlet
manifold and one outlet manifold. This is a **geometry/placement** option, not a
flow-direction change.

## 2. Background — current behaviour

A liquid rack carries a dual manifold described by `rack.liquidManifold`:

```js
liquidManifold: { layout: 'split', supplyPipe: {diameter, offsetFromInlet},
                  returnPipe: {diameter, offsetFromInlet}, pipeSpacing }
```

- `layout` controls left/right positioning across the rack width: `both-left`,
  `both-right`, `split` — see the panel at `STL_generate.html:11044-11065`.
- Both manifolds are rendered at local **Z = −0.05**, i.e. just outside the rack's
  **inlet face**, which is hardcoded — `STL_generate.html:8792-8795`
  (`supplyZ = returnZ = -0.05;`). The inlet face normal is −Z with the front at
  local Z = 0 (`STL_generate.html:13948`), so the rack body spans Z ∈ [0, D]
  (D = `model.base.D`, rack depth) and the **outlet face is at Z = D**.
- The manifold endpoint resolver `_getManifoldEndpoint` (`STL_generate.html:8889-8935`)
  likewise hardcodes the local Z to `-0.05`.
- There is **no inlet/outlet face option** anywhere. Confirmed by code map, live
  state inspection (`liquidManifold` has only `layout`/`supplyPipe`/`returnPipe`/
  `pipeSpacing`), and the rendered UI.

Downstream reality (verified):

- **STL export skips manifolds** — `appendMeshFacets` returns early for
  `isLiquidManifold` meshes (`STL_generate.html:20052`). Manifold geometry is **not**
  exported to OpenFOAM; placement does not affect the air-side mesh.
- **Liquid network graph is face-agnostic today** — `liquid_graphgen.js:381-390`
  places the supply/return manifold nodes at `basePos[2]` (the rack anchor Z),
  ignoring even the current inlet offset.
- **No backend schema** references `liquidManifold` or manifold face; `.liquidrun`
  `NodeConfig.position_m` is a free-form `List[float]` (`liquidcooling/schema.py:42`).

## 3. Requirement (clarified with user)

> Per rack, add a dropdown in the liquid-cooling manifold settings panel to place
> the manifold pair on the rack's **inlet face** (default) or **outlet face**. The
> two manifolds stay one-cold-one-hot; the left/right `layout` setting is unchanged
> and independent. The physically invalid "both manifolds same role / no return
> path" case must never occur — and it cannot here, because roles are untouched.

## 4. Scope

**In scope** (user chose "editor placement + reflect in liquid network graph"):

1. Editor render + saved `.cfdproj` reflect the chosen mounting face.
2. The exported `.liquidrun` liquid network graph reflects the chosen face in the
   manifold node positions (and their branch-pipe waypoints).

**Out of scope:**

- Exporting manifold solids to the OpenFOAM STL (manifolds remain excluded).
- Any flow-direction / supply↔return role swap.
- Per-manifold independent face (the pair moves together).
- Changing the `layout` (left/right) behaviour or the `offset`/`diameter` controls.

## 5. Design

### 5.1 Data model

Add one field to `liquidManifold`:

```js
liquidManifold: {
  layout: 'split',
  mountFace: 'inlet',     // NEW — 'inlet' | 'outlet', default 'inlet'
  supplyPipe: {...}, returnPipe: {...}, pipeSpacing: 0.015
}
```

- Default `'inlet'` ⇒ existing projects render and export identically.
- Must be set in every `liquidManifold` default-object literal
  (`STL_generate.html:~2525`, `~11039`, `~15549`) and backfilled in the project
  sanitizer (`STL_generate.html:~2691-2700`) so older `.cfdproj` files gain the
  field on load. Value is a plain string ⇒ JSON-serializable (state rule #5).

### 5.2 UI

In the manifold panel (`STL_generate.html:11044-11065`), add a dropdown row
directly under the existing **Layout** row, above diameter/offset:

```
Manifold layout:        [ Split             ▾ ]
Manifold mounting face: [ Inlet face (front) ▾ ]   ← NEW; other option: Outlet face (back)
Manifold diameter (mm): [ 25 ]
Manifold offset (mm):   [ 100 ]
```

- `onchange` → new global handler `window.setManifoldMountFace(modelId, value)`,
  mirroring `setManifoldLayout` (`STL_generate.html:11293`):
  `pushUndoCheckpoint()` first → set `rack.liquidManifold.mountFace = value` →
  re-render the rack/scene → `logOperation('setManifoldMountFace', …)`
  (mandatory STL_generate.html rules #1).
- Selection-only re-render uses the same path `setManifoldLayout` uses; no extra
  checkpoint logic beyond the single checkpoint.

### 5.3 Rendering (3D editor)

Replace the hardcoded `-0.05` Z with a face-derived value in both functions:

- `_renderLiquidManifold` (`STL_generate.html:8764-8887`): compute
  `faceZ = (mountFace === 'outlet') ? (D + 0.05) : -0.05`, where `D = model.base.D`
  (rack depth). Apply `faceZ` to `supplyZ`, `returnZ`, and the supply/return
  connection-stub Z (`sMesh`/`rMesh`).
- `_getManifoldEndpoint` (`STL_generate.html:8889-8935`): replace the local
  `Vector3(pipeX, pipeY, -0.05)` Z with the same `faceZ`, so pipe snap targets
  follow the manifold.
- Roles and colours are unchanged: supply = blue (`0x2196F3`), return = red
  (`0xF44336`). Left/right X positions (`layout`) unchanged.

### 5.4 Liquid network graph (`liquid_graphgen.js`)

Make the manifold nodes face-aware in Z, mirroring the editor:

- Compute `depthOffset_m = (mountFace === 'outlet') ? rackDepth_m : 0`, where
  `rackDepth_m` is derived consistently with `rackHeight`
  (`(rack.base.D) * _unitScale()` — see `liquid_graphgen.js:373`).
- Supply/return manifold nodes (`liquid_graphgen.js:381-390`): set
  `position_m[2] = basePos[2] + depthOffset_m`.
- The manifold-side waypoint of each branch pipe (`branch_in` source / `branch_out`
  target, `liquid_graphgen.js:435-438` and `453-456`) uses the same offset so the
  branch routes from the front-mounted equipment node to the (front or back)
  manifold. Equipment inlet/outlet nodes stay at the front (`basePos[2]`).
- Net effect: for `outlet`, branch pipes gain a ~rack-depth horizontal segment and
  the manifold headers sit at the back — a meaningful, correct length change. For
  `inlet`, output is byte-identical to today.

### 5.5 Backend

No change. `mountFace` is frontend-only; the `.liquidrun` carries the result purely
as node `position_m` values, which already validate against the free-form schema.

### 5.6 i18n

Add keys to every locale file under `src/frontend/web/i18n/`:
`liquid.manifold_mount_face`, `liquid.face_inlet`, `liquid.face_outlet`
(English + the other shipped locales, matching the existing `liquid.manifold_*`
key set).

### 5.7 Coding-rules compliance

- State mutation via `setManifoldMountFace` follows the checkpoint→mutate→log rule.
- `mountFace` is a string (serializable); no functions/DOM/Three.js objects.
- Manifold helper meshes remain tagged `isLiquidManifold` (tab-scoped visibility
  and STL-skip behaviour unchanged).

## 6. Backward compatibility

- Default `'inlet'` reproduces current geometry and current `.liquidrun` output
  exactly (offset 0).
- Sanitizer backfills `mountFace='inlet'` for projects saved before this change.
- `.cfdproj` gains one extra string key; no migration needed.

## 7. Testing & verification

- **Unit / regression (backend, pytest):** add a `liquid_graphgen`-level check or a
  fixture-based assertion that an `outlet` rack shifts manifold node Z by the rack
  depth while an `inlet` rack is unchanged. (If graphgen is only exercised in-browser,
  use the browser eval path below as the authoritative check and add a JS-level
  assertion via `/api/browser/exec-js`.)
- **Browser verification (`/browser-verify`, mandatory for frontend/WebGL):**
  1. Load a liquid project (e.g. `LiquidSingle.cfdproj`).
  2. Confirm default render unchanged (`mountFace` absent/`inlet`) — screenshot.
  3. Set `mountFace='outlet'` via the new dropdown; eval manifold mesh Z ≈ `D+0.05`
     and screenshot the pipes now on the back face.
  4. Export `.liquidrun` (or eval graphgen output); assert manifold node Z shifted
     by rack depth for `outlet`, unchanged for `inlet`.
  5. Reload the saved `.cfdproj`; confirm `mountFace` round-trips.

## 8. Open items to verify during implementation

1. **Rack depth axis & units.** Render uses `model.base.W`/`.D` as metres directly;
   graphgen uses `base.H * _unitScale()`. Confirm `model.base.D` (render) vs
   `rack.base.D * _unitScale()` (graphgen) give the same metre depth, and confirm
   the local +Z direction corresponds to "toward the outlet face".
2. **Anchor reference point.** Confirm `basePos`/`model.anchor` sits at the rack
   front so `+D` lands at the outlet face (vs. centre-anchored, which would need
   `±D/2`).
3. **Rack orientation.** The graph currently ignores rack orientation for node
   positions; this design keeps that simplification (adds the depth offset along
   world +Z). Confirm acceptable, or apply the rack quaternion if positions must be
   world-correct.
4. **`offsetFromInlet` interaction.** The render hardcodes `-0.05` and does not use
   `offsetFromInlet` for Z today; confirm the new face logic should likewise be a
   fixed ±0.05 from the chosen face (recommended) and leave `offsetFromInlet`
   semantics untouched.

## 9. Future (not now)

- Per-manifold or arbitrary-side placement.
- Exporting manifold solids to STL so the face affects OpenFOAM meshing.
- Honouring `offsetFromInlet` as a true face-distance in the 3D render.
