# Rack stacked servers — overlapping U ranges at independent depths, rack/server opacity split, and row-depth decoupling

**Date:** 2026-09-17
**Status:** approved in chat (design sections §1–§7), awaiting written-spec review
**Scope:** both frontends (React editor + legacy `STL_generate.html`), shared JS model/geometry layer, cfdrun V2 export, Python runner tolerance, one-case mesh verification. **Not** in scope: the 16-case batch regression gate.

---

## 1. Problem

Four coupled gaps in the current rack model:

1. **One server per U-slot, ever.** A rack slot can hold exactly one server (`geometry.slotOccupation` U tall). Real hardware stacks in depth: a 5 U short-depth server in U16–U20 *and* a 1 U switch at the same U16, recessed near the outlet face. The editor refuses this at four layers and the shared normalizer silently deletes any server whose U range intersects an already-placed one.
2. **One opacity knob drags everything.** Shift-click selects the rack as a whole, but the sidebar's single opacity slider fades rack body and servers together. Servers have no opacity field at all today; they inherit the rack's at render time.
3. **No UI to place a second server at an occupied U range**, and no notion of two servers' depths colliding.
4. **The rack-detail "U-slot depth" row sliders move servers.** The sliders write `rack.inletOffset` / `rack.outletOffset`; every server's depth is *derived* as `rack offset + server extra`, so dragging the row sliders repositions and can rescale every server in the rack. The user wants the row sliders to control blank-baffle / U-slot structure depth only.

## 2. Decisions taken in discussion

| # | Decision | Rationale |
|---|---|---|
| D1 | Approach **A**: per-server **absolute** depth fields; shared depth-conflict predicate. Rejected: B (keep `extra*`, compensate on row-slider change — coupled arithmetic stays, every row drag writes N servers), C (first-class depth lanes/stacks — no current need, biggest model change). | Decoupling becomes structural, not a write-time trick. |
| D2 | Delivery scope: model + both UIs + geometry/STL naming + cfdrun V2 export + Python runner wired, verified by **one real mesh run** through the browser. No 16-case batch regression this round. | User's call. |
| D3 | A short fan server's through-channel / fan BC faces span **the server's own depth only**; the rack volume in front of and behind it is open fluid (bypass path). | User's call; matches leakage physics. Already the builder's behaviour for the channel walls, now true for the whole box. |
| D4 | `.cfdproj` stays **schemaVersion 4** (repo convention: default-on-absence + load-time normalizer backfill, no bump). cfdrun is the **V2 manifest**; new depth fields go on the server objects inside its carried `meshGeometry` blob (fan and nonFan branches). V2 `ServerInput` is run-conditions only and gains no geometry. | Matches existing migration practice. |
| D5 | Both frontends get full editing parity, per user instruction, overriding `.claude/CLAUDE.md:320` ("do not add features here" for the legacy file). All model/geometry/conflict/migration logic lives **once** in `src/frontend/shared/js/`; only panel HTML + slider handlers are written twice. A CLAUDE.md wording update will be proposed after implementation (user decides). | User's call; shared-layer-once minimises the drift surface the CLAUDE.md rule exists to prevent. |
| D6 | Two servers sharing a slot conflict **only when their depth intervals intersect** (epsilon 1e-6 m; touching faces allowed). | User's call. |
| D7 | A recessed server's front/back cavity stays **open** (no sealing blanking panel). Blanking panels are only ever built on empty slot ranges (`getRackMergedEmptySlotRanges`), so this needs no new rule. | Follows from D3; avoids sealed orphan regions at mesh time. |

## 3. Data model (`.cfdproj` V4)

Current server record (`rack.constructions.servers[uid]`, uid-keyed dict):

```
{ uid, type: 'fanServer'|'nonFan'|'liquidServer',
  geometry: { slotPosition, slotOccupation },        // 0-based U start, span in U
  cooling: {...}, constructions: { chips, coldPlates },
  meta: { name, enabled },
  extraInletOffset, extraOutletOffset,               // RELATIVE to rack offsets — removed by this spec
  faces: { inlet, outlet, top, bottom, left, right }, liquidCooling }
```

Rack depth convention (unchanged): rack-local Z, inlet face `z = 0`, outlet face `z = D = geometry.size.z`. Rack-level row depth = `rack.inletOffset` / `rack.outletOffset` (setbacks from the two faces), applied to blanking panels, slot guides, cavity walls and doors.

### 3.1 Added / changed server fields

| Field | Type | Meaning |
|---|---|---|
| `inletOffset` | number (m), top-level on the server | **Absolute** setback of the server's inlet face from the rack inlet plane (`z0 = inletOffset`). |
| `outletOffset` | number (m), top-level on the server | **Absolute** setback of the server's outlet face from the rack outlet plane (`z1 = D - outletOffset`). |
| `meta.display.opacity` | number 0..1 | Per-server opacity. The only storage for server opacity; the rack sidebar's second slider is a batch writer over it, never a separate variable. |
| `extraInletOffset` / `extraOutletOffset` | — | **Removed.** Migration input only (see 3.2). |

Constraints, enforced by the normalizer and by every writer:

```
0 <= inletOffset ;  0 <= outletOffset ;  inletOffset + outletOffset <= 0.9 * D
```

(the 0.9 factor preserves today's over-depth protection, `rack_state.js:531-543`).

`geometry.slotPosition` / `slotOccupation` semantics are unchanged; "4 U fused" remains one record with `slotOccupation: 4`. Overlapping servers are simply two records whose slot intervals intersect.

### 3.2 Migration / load-time backfill (no version bump)

In `syncRackModelState` (`src/frontend/shared/js/rack_state.js`), per server, when `inletOffset`/`outletOffset` are absent or non-finite:

```
srv.inletOffset  = (rack.inletOffset  || 0) + (srv.extraInletOffset  || 0)
srv.outletOffset = (rack.outletOffset || 0) + (srv.extraOutletOffset || 0)
delete srv.extraInletOffset; delete srv.extraOutletOffset
```

then clamp per 3.1. When `meta.display.opacity` is absent: backfill with the **rack's current** `meta.display.opacity` (old documents therefore look exactly as they do today on first open), and delete nothing else. New servers are created with `meta.display.opacity = 1`.

The v3↔V4 proxy `_rackServerView` (`rack_state.js:357-389`, mapping set `:111`) must map the new fields both ways and drop the extras from the mapping.

Old documents keep coupled-looking values frozen at load: after backfill, row sliders no longer move them (that is the point of §6).

## 4. Shared model & geometry layer (implemented once)

All of this lives in `src/frontend/shared/js/`; both editors already import these modules (React via `src/frontend/react/src/legacy/cfdprojModules.ts` and `legacy/geometryBuilder.ts`; legacy via `<script>` tags).

### 4.1 Depth derivation

- `getSlotDepthRange(model, slotIndex)` (`geometry_pure.js:530-541`) returns the **rack-level** range `[inletOffset, D - outletOffset]` unconditionally. Consumers that need "the structural row depth" — blanking panels, slot guides (`geometry_scene.js:1846-1878`), partitions (`getPartitionDepthRange`, `geometry_pure.js:543-549`) — keep working unchanged and stop following servers.
- New pure helper `getServerDepthRange(model, server)` → `{ z0: server.inletOffset, z1: D - server.outletOffset }`.
- Server box construction switches to it: `renderRackModel`'s serverBox (`geometry_scene.js:1898-1930`, today `eqSlotStart = slotDepthStart + eqExtraIn`), the legacy drag-path `getRackServerBox` (`STL_generate.html:7409-7426`), and any other reader of the extras.

This is the structural decoupling of problem 4: no code path adds rack offsets to server offsets any more.

### 4.2 Normalizer (`syncRackModelState`, `rack_state.js:391-583`)

- Delete the extras re-clamp/rescale block (`:531-543`); replace with the 3.1 clamps on the absolute fields.
- Replace the single-occupant model (`occupied[slot] = uid`, `:510-523`) with a per-slot list of placed depth intervals. Servers are sorted by `(slotPosition, inletOffset)`. A server is dropped **only** if, on some shared slot, its depth interval intersects an already-placed one (D6). The drop is no longer silent: the rack gains a diagnostic array `constructions.serverConflicts` (list of `{ aUid, bUid, slots, overlapM }`), rebuilt on every sync, never consumed by geometry — UI badges read it.
- Backfill per 3.2 in the same pass.

### 4.3 Conflict predicate (shared, pure)

```
findServerDepthConflicts(model) -> [{ aUid, bUid, slots: [i...], overlapM }]
```

Two servers conflict iff their slot intervals intersect **and** their depth intervals intersect by more than 1e-6 m. Used by: the add-server UI (pre-write blocking, including a "what if I add this" dry run), the normalizer (last-resort drop), and a validation badge on the rack panel.

### 4.4 Occupancy queries and guards

- `getRackEquipmentForSlot` (`geometry_pure.js:426-436`, returns first) stays for back-compat. New `getRackServersForSlot(model, i)` returns all covering servers sorted by `inletOffset`.
- The "occupied ⇒ refuse" guards become conflict-aware: `createEquipmentAtSlot` / `planQuickAdd` / `mergeSlotsIntoEquipment` (React `geometry/rackSlots.ts`, `geometry/rackServers.ts`; legacy `STL_generate.html:15001-15130`) refuse only when the *new* server's depth interval intersects an existing covering server's; `setSlotDeleted` keeps refusing slots that have any server (a deleted face under a server box is still nonsense).
- Extension/trim/split operations (`removeSlotsFromEquipment`) keep their current U-range semantics; they now also re-validate depth conflicts after rewriting ranges.

### 4.5 Opacity in the builder

`renderRackModel` passes one opacity into `buildServerGeometry` today (`geometry_scene.js:1721`, `model.meta.display.opacity * _dimMult`). Change to per-server: `srv.meta.display.opacity ?? rack.meta.display.opacity`, so server meshes carry their own material opacity. Rack structural meshes keep the rack value. Server meshes already carry `userData.equipmentId` (legacy picking depends on it), which is what the preview fade filters on (§5.1).

## 5. Opacity: rack body vs all servers (problem 2)

Storage is §3.1 only. Both sliders are writers over existing per-object fields; neither introduces a new stored variable.

### 5.1 React

- New module `src/frontend/react/src/geometry/serverOpacity.ts`, mirroring `modelOpacity.ts`'s preview/commit seam exactly (one `pushUndoCheckpoint` per drag via `preview…`, `mutateLive` for the material fade, single `notifyLiveEdit` on commit):
  - `previewRackServersOpacity(rackUid, percent)` / `commitRackServersOpacity(rackUid, percent)` — batch: sets **every** server's `meta.display.opacity` of that rack to the value.
  - `previewServerOpacity(rackUid, serverUid, percent)` / `commit…` — single server.
  - Material fade targets only meshes whose `userData.equipmentId` matches (all servers of the rack for the batch call); every model-root fade path in `modelOpacity.ts` (single-model and multi-select group) gains the inverse filter (skip meshes with an `equipmentId`) so rack fades no longer touch servers. This also makes the group-opacity *preview* match its commit, which never wrote server fields (servers are not models).
- UI: `ModelProperties` (`components/Inspector.tsx:297-549`, the whole-rack panel) renders, for `kind === 'rack'`, the existing `ModelDisplayGroup` rack slider plus a new `RackServerOpacityGroup` with `#rack-server-opacity-slider`. Initial value: common value if all servers agree, else the mean plus a "mixed" hint (reuse `averageOpacityPercent`). `EquipmentInspector` gains a per-server slider bound to that server's field.

### 5.2 Legacy

- `renderRackModelInspector` (`STL_generate.html:9855`, rack branch) gains a second slider row after the existing `label.model_opacity` row; handlers `window.previewRackServersOpacity` / `window.updateRackServersOpacity` follow the established first-call-flag undo pattern (`opacityPreviewCheckpointPushed`, `:14383-14397`). `renderRackEquipmentInspector` (`:10816`) gains the per-server row.
- The legacy preview helper fades only meshes tagged with the rack's server uids; the existing `applyModelOpacityState` (`:14343-14358`) is left alone (it cascades rack faces, never servers).

### 5.3 Behaviour change to state plainly

After this change the rack slider affects frame / blanking panels / doors / partitions only. Old documents open with servers backfilled to the rack's opacity, so first-open appearance is unchanged; from then on the two knobs are independent.

## 6. Row-depth decoupling (problem 4) and per-server depth editing

- The U-slot Depth group keeps writing `rack.inletOffset` / `rack.outletOffset` only: React `RackDetailPanel.tsx:212-303` via `setDoorOffset` (`geometry/rackDetail.ts:227-241`); legacy `STL_generate.html:9757-9803` via `_rdSet*Offset*` (`:12759-12809`). Because of §4.1 nothing downstream touches servers any more. The group's explanatory line gains an i18n'd note: the row sliders control blank-baffle / U-slot structural depth, not added servers.
- The per-server "extra" sliders are replaced by **absolute** editors:
  - React: `RackDetailPanel.tsx:377-431` per-server rows and `EquipmentInspector`'s `ServerDepthControls` (`:266-315`) show and write 距 inlet (m) / 距 outlet (m) with a read-only derived depth; writers `setServerDepth` / `setServerExtras` (`rackDetail.ts:244-308`) are replaced by one `setServerDepthAbsolute(rackUid, serverUid, inletOffset, outletOffset)` in the shared layer (clamps per 3.1, conflict check per 4.3, refuses with an error result on conflict).
  - Legacy: `renderServerDepthGroup` (`:10660-10686`), `setRackServerDepthAbs` (`:10714-10807`), `_rdSetServerDepth` (`:12811-12821`) repointed to the same shared writer; drag/undo semantics (one checkpoint per drag, `window._sdCheckpointPushed`) preserved.
- Blank-baffle / U-slot structure follows the row sliders exactly as today; servers do not. Regression test asserts server fields are byte-identical across a row-slider drag.

## 7. Adding a second server at an occupied U range (problems 1 + 3)

### 7.1 React

New component `components/RackStackedServerSection.tsx`, rendered by `RackSlotInspector` (single slot) and its `MultiSlotPanel` (slot range) whenever the selected U range already holds ≥1 server. The rack's own server panel (`RackServersPanel`) does **not** host this section — it only gains the two optional depth boxes on its existing quick-add:

- **Existing servers at this range**: rows of name, U range, kind, 距 inlet / 距 outlet, and a normalised horizontal depth bar (0..D).
- **"＋ add server at same U"** disclosure with: kind select (fan / non-fan / liquid), 距 inlet (m number box + % slider of D), 距 outlet (same), a **live depth-bar preview** drawing the prospective server highlighted and tinting any overlap with existing bars red, a conflict line fed by a dry run of `findServerDepthConflicts`, an **"auto-fit into largest free gap"** button (largest empty depth interval across the selected slots; places the new server centred in it, depth = min(free width, 0.3 m)), and a confirm button disabled while conflicting or out of bounds.
- Confirm → one `store.edit` creating the record with `slotPosition`/`slotOccupation` from the current selection and the absolute offsets; selection moves to the new server.
- `RackServersPanel` quick-add (Start U / Span / kind) gains two **optional** depth boxes; empty = rack plane (`inletOffset = rack.inletOffset`, `outletOffset = rack.outletOffset`), i.e. today's behaviour.

### 7.2 Legacy

Same section as inline HTML in `renderRackSlotInspector` / `renderRackMultiSlotInspector` (`STL_generate.html:10223` region) plus the two optional boxes in `_rackServerPanelHtml` (`:9924-9972`); handlers `window._rsAddStackedServer`, `window._rsAutoFitGap` etc. through the shared writers, first-call-flag undo per house rules.

### 7.3 Blocking rules

U range off the rack top/bottom, or a depth-interval intersection with any covering server (D6) → confirm disabled + `error.*` message. The normalizer's conflict drop remains only as a last-resort guard for documents edited outside the UI.

## 8. Export (cfdrun V2) and Python runner

- `buildCfdrunJson` (`cfdrun_build.js:415-579`): both the fanServer object (`:417-455`) and the nonFan object (`:459-498`) carry `inletOffsetM` / `outletOffsetM` (absolute, m); the old `extraInletOffset` / `extraOutletOffset` keys are no longer emitted on either branch (no backend code ever read them). The V2 manifest (`react/src/legacy/cfdrunBuild.ts:95-110`) carries them inside `meshGeometry`; `boundaryBindings` are patch-name keyed and unchanged.
- `cfdrun_schema.py` `RackServer` (`:93-104`) gains `inletOffsetM: float | None = None`, `outletOffsetM: float | None = None`. The runner consumes server geometry as exported STL surfaces, so true depths arrive automatically; the declared fields are for diagnostics and future use.
- Runner tolerance for shared U ranges:
  - `_detect_thin_gap_regions` (`stlgen_runner.py:266-345`) keeps its per-U **union** occupancy; a slot marked twice is harmless.
  - `solid_to_span` (`:4363-4424`) is keyed by solid name; names are unique by §8 next bullet.
  - `fields.py` fan inlet/outlet pairing (`_find_server_inlet_patch` / `_find_server_outlet_patch`, `:1406-1451`) changes to **exact `{stem}_inlet_patch` match first**, prefix scan only as fallback — removes the cross-pairing hazard between `X` and `X_2` style names that stacked-server auto-naming makes likely.
  - `mesh_acceptance` needs no change: interpenetration is prevented upstream (D6); coplanar duplicate slot boundaries are already tolerated.
- **Solid/patch naming** (`stl_naming.js:145-160`): same-name disambiguation currently appends `_u{a}_u{b}` from U positions only, so two same-named servers at the *same* U range would collide (merged STL solid, backend validator rejection, BC registry overwrite). Extend: when sanitized names and U ranges coincide, append `_z{z0 in mm}`. The backend uniqueness validator (`cfdrun_schema.py:567-599`) stays as the final gate. BCs remain patch-name keyed, so unique names ⇒ distinct BCs with no `bridge_bc.py` change.

## 9. i18n

Both `src/frontend/shared/i18n/zh-CN.json` and `en-US.json` gain the new keys at matching lines (`test/i18n.test.ts` enforces parity and usage): `label.rack_server_opacity`, `hint.rack_server_opacity_desc`, `label.server_opacity`, `rack.stacked_servers_title`, `rack.stacked_add`, `rack.stacked_auto_fit`, `rack.stacked_conflict`, `rack.stacked_mixed`, `rack.slot_depth_decoupled_hint`, `error.server_depth_overlap`, plus whatever the panels end up needing. Existing depth labels (`label.server_depth`, `label.server_outlet_distance`, `equipment.extra_inlet/outlet`) are re-pointed to absolute semantics; `equipment.extra_*` keys are retired only if no other consumer remains.

## 10. Automation contract

New verbs in `src/frontend/react/src/automation/contract.ts` (each going through `store.edit`-based modules, per the file's contract), exposed on `window.__dcdtuni` for browser-use verification:

- `setRackServersOpacity(rackUid, percent)` — batch commit (§5).
- `setServerOpacity(rackUid, serverUid, percent)`.
- `addRackServerAtRange(rackUid, startU, span, kind, inletOffset, outletOffset)` — returns the created uid or `{ error }`.
- `rackServerDepths(rackUid)` → `[{ uid, name, startU, endU, inletOffset, outletOffset, opacity }]` — read-back for assertions.

## 11. Tests (vitest, `npm test` in `src/frontend/react`)

New:

- `test/rackDepthConflicts.test.ts` — predicate matrix: intersecting, touching (allowed), disjoint, partial slot overlap, different slots, epsilon boundary.
- `test/rackStackedServers.test.ts` — normalizer keeps disjoint stacked servers, drops intersecting ones and records `constructions.serverConflicts`; migration backfill (extras → absolute, delete extras; opacity backfill from rack); clamps; `getRackServersForSlot` ordering.
- `test/rackServerOpacity.test.ts` — batch commit writes every server and produces one undo entry per drag; mixed initial value = mean; per-server write leaves siblings untouched; rack-body fade filter excludes server meshes.
- `test/components/RackStackedServerSection.test.tsx` — confirm disabled on conflict; auto-fit lands in the largest free gap; preview overlap tinting state.
- `test/rackExportNaming.test.ts` — same name + same U range ⇒ distinct stems (`_z` suffix); cfdrun server objects carry `inletOffsetM`/`outletOffsetM` on both branches.

Rewritten:

- `test/components/ServerDepthControls.test.tsx` — absolute semantics; **row-slider drag leaves every server field byte-identical** (the §6 regression assertion); drag-anchoring and single-undo semantics preserved.
- `test/rackServers.test.ts` / `test/rackDetail.test.ts` — updated to the new writer signatures and occupancy rules.

## 12. End-to-end verification (browser-verify per-point loop)

GPU `browser-use` against the React app, then the legacy app; each point = drive UI → `eval` read-back → screenshot → read screenshot → judge.

1. React: create rack; add 5 U fan server at U16–U20 recessed from the outlet; add 1 U nonFan switch at U16 near the inlet via the new stacked section. Assert via `rackServerDepths` that both exist with disjoint absolute depths; screenshot shows two boxes at different depths in the same U band.
2. Rack opacity slider: assert server `meta.display.opacity` unchanged and rack `meta.display.opacity` changed; screenshot shows structure translucent, servers solid. Server opacity slider: assert all server fields equal the value; screenshot shows servers translucent.
3. Row-depth sliders (U-slot Depth group): drag inlet/outlet/both; assert both servers' `inletOffset`/`outletOffset` unchanged and blanking/guide structure moved; screenshot.
4. Conflict: drive the switch's 距 inlet into the fan server's interval; assert the confirm button is disabled and the conflict message names the other server. The `serverConflicts` badge path (externally-edited documents) is covered by the unit tests in §11, not through the UI, because every UI/verb write path refuses conflicts before they reach the document.
5. Legacy `--app html`: repeat 1–3 with the legacy selectors/handlers.
6. Export + mesh: `buildCfdrunJsonV2` eval — both servers present with absolute fields and unique solid names; save; `/api/run-mesh` once; assert job returncode 0, `Mesh/constant/polyMesh` on disk, and both servers' patches present in `polyMesh/boundary` (mesh acceptance gate). Screenshot of the solver/mesh panel.

Out of scope this round: the 16-case batch gate (`regen_all_meshes` / `run_all_cases`).

## 13. Risks and open edges

- **Partitions** adjacent to a recessed server now use the rack-level depth range and may visually float next to a shallow server. Accepted; partitions are a niche feature and their current intersect-with-server behaviour was part of the coupling being removed.
- **Protruding servers** (`inletOffset` below the rack plane is impossible by the `0 <=` clamp; between 0 and `rack.inletOffset` the server sticks out in front of the row plane). Geometrically valid, physically odd; allowed, no special-casing.
- **`_rackServerView` proxy** must stay consistent both directions or legacy v3-shaped writers will silently lose the new fields; covered by the migration tests.
- **Liquid servers** (`liquidServer`) inherit the same absolute depth fields; the liquid manifold/pipe generators read slot ranges, not depths, so no change expected — verified by the existing liquid tests staying green.
- Line numbers cited here were re-grepped on 2026-09-17 and are volatile; implementers re-grep before editing (CLAUDE.md's own numbers for this file are already stale).
