/**
 * `window.__dcdtuni` — the automation contract.
 *
 * This is the surface `/api/browser/exec-js` drives, and therefore the one the
 * whole 3-agent pipeline stands on: `/dc-builder`, `/dc-evaluator`,
 * `/dc-orchestrate`, `/dc-dataset-reverse` and
 * `paper_experiment/batch_cfd_pipeline.py` all reach the editor through it.
 *
 * ## Why a narrow contract instead of the legacy's globals
 *
 * `STL_generate.html` answers **465** distinct `window.*` names. Measured
 * against the callers, the automation uses about **25** of them. Re-exporting
 * all 465 from React would weld back on the exact thing the port removed: a
 * mutation surface wide enough that `projectStore`'s "there is no way in that
 * skips the undo checkpoint" stops being true, because 465 new doors would
 * bypass it.
 *
 * So this file is the doors, enumerated. Everything here goes through
 * `store.edit` or a module that does, which is what keeps undo honest for a
 * scripted edit as much as for a clicked one.
 *
 * ## Why the legacy names
 *
 * `createDefaultRackModel`, not a tidier `createModel('rack')` — the same
 * choice `room2d/room2dApi.ts` already made, for the same reason: one SOP has
 * to drive either app while both exist, and the parity harness compares them by
 * running ONE snippet against both. Tidying the names would mean maintaining
 * two SOPs during the very window where they must agree.
 *
 * ## One namespace, no bare names
 *
 * There used to be top-level aliases here — `window.createDefaultRackModel` and
 * two dozen others — so that ONE SOP could drive either editor while both
 * existed. Only one exists now, and a global per verb is exactly the surface
 * this contract was built to avoid: 24 more doors into the document, none of
 * which a reader can tell apart from the 465 the old app had. Every caller says
 * `window.__dcdtuni.x`.
 *
 * ## Not dev-gated
 *
 * `main.tsx` used to install `__dcdtuni` only under `import.meta.env.DEV`,
 * reasoning that production has no business exposing an editing API. That was
 * right for a store handle and wrong for this: the production build is exactly
 * what the head browser drives, and `/api/browser/exec-js` can already evaluate
 * arbitrary JS in the page, so gating adds no safety and removes the pipeline.
 */

import { rebuildSceneFromProject } from '../renderer/sceneRebuild'
import * as store from '../projectStore'
import { allModels, type CfdProject } from '../schema/cfdproj'
import { modelPoseKernel } from '../domain/modelPoseKernel'
import { transformToolsModule } from '../legacy/transformTools'
import { stateV4 } from '../legacy/cfdprojModules'
import { createModel } from '../legacy/factories'
import {
  airPipeWorldPoints,
  joinAirPipes as joinAirPipesInDraft,
  setAirPipeDiameter,
  setAirPipeShape,
  setAirPipeSize,
  setAirPipeWorldPoints,
} from '../geometry/airPipe'
import { createEquipmentAtSlot, type EquipmentKind } from '../geometry/rackSlots'
import {
  updateRackHeightMode,
  updateRackSetting,
  type RackHeightMode,
  type RackSettingKey,
} from '../geometry/rackGeometry'
import { setDoorOffset } from '../geometry/rackDetail'
import {
  addRackServerQuick,
  rackServerRows,
  rackServersOf,
  updateRackServerCfd,
} from '../geometry/rackServers'
import {
  commitRackServersOpacity,
  commitServerOpacity,
  serverOpacityOf,
  type ServerRecord,
} from '../geometry/serverOpacity'
import { addServerPatchRect } from '../geometry/serverPatches'
import { addPatch, autoFillPatch, deletePatch, locatePatch, updatePatchGeometry } from '../geometry/patchActions'
import { PATCH_PHYSICS_DEFAULTS, updatePatchPhysics } from '../geometry/patchPhysics'
import { applyViewPreset, type ViewPreset } from '../viewportBus'
import { ovDisplay } from '../renderer/displayDistributor'
import { getSelection, requestSelect } from '../selectionBus'
import * as session from '../run/session'
import { liquidRunState, runLiquidSolve, type RunLiquidOptions } from '../liquid/liquidSession'
import { setPipeDiameter, setPipeSegmentPoints, setValveOpening } from '../liquid/pipeInspector'
import { setPumpSpeedRatio } from '../liquid/cduInputs'
import { currentRunSources, sceneRunDeps } from '../run/runSubmit'
import { buildStlFiles } from '../io/stlExport'
import { cfdrunFilename } from '../run/runPayload'
import { setProjectName } from '../io/projectMeta'
import { setRoomTemperature } from '../geometry/roomTemperature'
import { writeCasePath } from '../run/caseDir'
import { serializeProjectState } from '../io/projectFiles'
import type { SceneBuilder } from '../interaction/sceneBuilder'
import { setSimulationMode } from '../transient/simulationMode'
import { generateRoom } from '../room2d/room2d'

/**
 * The seven kinds the legacy exposes a `createDefault*Model` for.
 *
 * Spelled out one function per verb rather than generated from a name->kind map
 * with `Object.fromEntries`. The generated version compiled but typed the result
 * as an index signature, so `automation.createDefaultRackModel` was `any` to
 * every caller and to the test — which is how a `tsc -b` error survived a green
 * `tsc --noEmit` run and a green test run. Seven lines of repetition buy real
 * types on the surface the whole pipeline calls.
 */
const CREATE_KIND = {
  rack: 'rack',
  acu: 'acu',
  wall: 'wall',
  baffle: 'baffle',
  floor: 'floor',
  box: 'box',
  cdu: 'cdu',
  pipe: 'pipe',
} as const

/**
 * Create one model of `kind` and commit it.
 *
 * Returns the new uid — the legacy's creators return nothing and leave the
 * caller to diff `allModels()` before and after, which is how `/dc-builder`
 * ended up re-reading the whole project after every single create. Returning
 * the uid is additive: a caller that ignores it behaves exactly as before.
 */
function createAndAdd(kind: string): string | null {
  const project = store.getProject()
  if (!project) return null
  const created = createModel(project, kind)
  return store.addModel(created.bucket, created.model)
}

/**
 * Put a server in a rack slot.
 *
 * Four arguments to match `window.createRackEquipmentAtSlot(modelId, side,
 * slotIndex, kind)` (STL_generate.html:14100). `side` does not reach the
 * geometry in the legacy either — its only uses there are the selection it
 * leaves behind and the operation log (`:14141-14142`) — so it is accepted and
 * ignored rather than silently changing what gets built.
 */
function createRackEquipmentAtSlot(
  modelId: string,
  _side: 'front' | 'back',
  slotIndex: number,
  kind: EquipmentKind,
): string {
  return createEquipmentAtSlot(modelId, slotIndex, kind)
}

/**
 * `window.setSolverType('steady' | 'transient')` — the AIR solver's mode,
 * through the one writer, so `transient.airMode`, the derived `transient.mode`
 * and the mirrored solverType never disagree (E8b).
 */
function setSolverType(type: string): void {
  setSimulationMode(type === 'transient' ? 'transient' : 'steady', 'air')
}

/**
 * `window.__dcdtuni.rackServers(uid)` — what is mounted in a rack.
 *
 * `createRackEquipmentAtSlot` returns only whether the slot took it, matching
 * the legacy, so a caller that needs the new server's ID has to ask. The old
 * SOP read `model.rack.equipments[last]` — a V2 shape; V4 keeps servers in
 * `constructions.servers` keyed by id, so that read returned undefined and the
 * `updateRackServerCfd` that followed it silently addressed nothing.
 */
function rackServers(uid: string) {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  return rackServerRows(model ?? null).map((row) => ({
    id: row.id,
    name: row.name,
    kind: row.kind,
    startUnit: row.startUnit,
    endUnit: row.endUnit,
  }))
}

/**
 * `window.__dcdtuni.setRackServersOpacity(rackUid, percent)` — the batch knob (§5).
 *
 * One commit writing EVERY server's `meta.display.opacity` of that rack — the
 * same single undo entry the sidebar slider's release produces. The rack body's
 * own opacity is `updateModelOpacity`; since the split, neither drags the other.
 */
function setRackServersOpacity(rackUid: string, percent: number): boolean {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return false
  commitRackServersOpacity(rackUid, percent)
  return true
}

const RACK_SETTING_KEYS: readonly RackSettingKey[] = [
  'unitCount', 'unitHeight', 'slotWidthRatio', 'safeMarginTop', 'safeMarginBottom', 'defaultSpanUnits',
]

/**
 * `window.__dcdtuni.setRackLayout(rackUid, key, value)` — one U-slot layout field
 * (`unitCount`, `unitHeight`, `slotWidthRatio`, `safeMarginTop`,
 * `safeMarginBottom`, `defaultSpanUnits`; lengths in metres), through the Rack
 * Layout panel's own writer, so `syncRackModelState` clamps it exactly as the
 * panel's boxes do. False when the uid is not a rack, the key is unknown or the
 * value is not a finite number.
 */
function setRackLayout(rackUid: string, key: string, value: number): boolean {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return false
  if (!RACK_SETTING_KEYS.includes(key as RackSettingKey) || !Number.isFinite(value)) return false
  updateRackSetting(rackUid, key as RackSettingKey, value)
  return true
}

/**
 * `window.__dcdtuni.setRackHeightMode(rackUid, mode)` — `totalHeight` (H and the
 * U count given, the unit height follows) or `unitHeight` (the unit height and
 * count given, H follows), the panel's switch.
 */
function setRackHeightMode(rackUid: string, mode: string): boolean {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return false
  if (mode !== 'totalHeight' && mode !== 'unitHeight') return false
  updateRackHeightMode(rackUid, mode as RackHeightMode)
  return true
}

/**
 * `window.__dcdtuni.setRackDoorOffset(rackUid, side, metres)` — the rack's slot
 * plane on the inlet and/or outlet side (`'inlet' | 'outlet' | 'both'`): where the
 * blanking panels of empty U stand and the depth a server added without explicit
 * offsets takes. The rack-detail panel's slot-depth writer.
 */
function setRackDoorOffset(rackUid: string, side: string, metres: number): boolean {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return false
  if (side !== 'inlet' && side !== 'outlet' && side !== 'both') return false
  if (!Number.isFinite(metres) || metres < 0) return false
  setDoorOffset(rackUid, side, metres, false)
  return true
}

/** `window.__dcdtuni.setServerOpacity(rackUid, serverUid, percent)` — one server. */
function setServerOpacity(rackUid: string, serverUid: string, percent: number): boolean {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return false
  if (!rackServersOf(model)[serverUid]) return false
  commitServerOpacity(rackUid, serverUid, percent)
  return true
}

/**
 * `window.__dcdtuni.addRackServerAtRange(rackUid, startU, span, kind, inletOffset, outletOffset)`
 * — place a server at an explicit 0-based U range and ABSOLUTE depth (spec §10).
 *
 * The offsets are optional; omitted means the rack plane
 * (`defaultServerDepthOffsets`). Returns the new server's uid, or `{ error }`
 * with the refusal reason — `occupied` is the depth-conflict answer: a taken U
 * range only blocks when the depth intervals intersect.
 */
function addRackServerAtRange(
  rackUid: string,
  startU: number,
  span: number,
  kind: EquipmentKind,
  inletOffset?: number,
  outletOffset?: number,
): string | { error: string } {
  const depth =
    inletOffset === undefined && outletOffset === undefined
      ? undefined
      : {
          ...(inletOffset === undefined ? {} : { inletOffset }),
          ...(outletOffset === undefined ? {} : { outletOffset }),
        }
  const result = addRackServerQuick(rackUid, startU, span, kind, depth)
  return result.ok ? result.uid : { error: result.error }
}

/**
 * `window.__dcdtuni.addServerPatch(rackUid, serverUid, face, rect?)` -- an opening on a server face.
 *
 * `face` is `inlet` or `outlet`; `rect = {x, y, w, h}` in fractions of the face (default the
 * editor's fan-exhaust strip `{x: 0.75, y: 0, w: 0.25, h: 1}`, the opening a fanServer's fans
 * blow through, as in the editor-built projects). Returns the patch id, or `{ error }`.
 */
function addServerPatch(
  rackUid: string,
  serverUid: string,
  face: string,
  rect?: { x: number; y: number; w: number; h: number },
): string | { error: string } {
  if (face !== 'inlet' && face !== 'outlet') return { error: `face must be inlet or outlet, got ${face}` }
  const r = rect ?? { x: 0.75, y: 0, w: 0.25, h: 1 }
  const id = addServerPatchRect(rackUid, serverUid, face, r)
  return id ?? { error: `no server ${serverUid} on rack ${rackUid}` }
}

/**
 * `window.__dcdtuni.rackServerDepths(rackUid)` — the read-back for assertions.
 *
 * One row per server: who it is, which U's it covers (0-based, inclusive), its
 * absolute setbacks and its opacity — everything §12's browser checks compare
 * after a UI gesture, from the document rather than from the scene.
 */
function rackServerDepths(rackUid: string) {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, rackUid)?.model : null
  if (!model || model.kind !== 'rack') return []
  const servers = rackServersOf(model)
  return rackServerRows(model).map((row) => {
    const srv = (servers[row.id] ?? {}) as ServerRecord
    return {
      uid: row.id,
      name: row.name ?? row.id,
      startU: row.startUnit,
      endU: row.endUnit,
      inletOffset: Number(srv.inletOffset ?? 0),
      outletOffset: Number(srv.outletOffset ?? 0),
      opacity: serverOpacityOf(srv),
    }
  })
}

/** `window.setMeshConfig(key, value)` — one `meshConfig` field. */
function setMeshConfig(key: string, value: unknown): void {
  store.setRunConfig('meshConfig', key, value)
}

/** `window.setSolverConfig(key, value)` — one `simulationConfig` field.
 * solverType is the air mode's mirror, so it goes through the mode writer. */
function setSolverConfig(key: string, value: unknown): void {
  if (key === 'solverType') return setSolverType(String(value))
  store.setRunConfig('simulationConfig', key, value)
}

/**
 * `window.runMeshGeneration(mode)`.
 *
 * The legacy takes a mode and defaults it to `'mesh'`; `session.runJob` is
 * already the one path that rebuilds the geometry on screen, exports it and
 * POSTs, so the mode only chooses between the mesh job and the checkMesh one.
 */
async function runMeshGeneration(mode: 'mesh' | 'checkMesh' = 'mesh'): Promise<void> {
  if (mode === 'checkMesh') return session.runCheckMesh()
  return session.runJob('mesh')
}

/** `window.runSolveOnly()` — re-exports the run first, as the legacy does. */
async function runSolveOnly(): Promise<void> {
  return session.runJob('solve')
}

/**
 * `window.buildCfdrunJsonV2()` — the V2 .cfdrun manifest for what is on screen.
 *
 * The server's `/api/browser/export-cfdrun` evaluates this by name, and it is
 * how every scripted run gets its manifest: `/dc-run-cfd`, the paper batch
 * driver, and `/dc-orchestrate`'s scoring loop all go through that endpoint.
 *
 * Delegates to the SAME `sceneRunDeps().buildManifest()` the mesh button uses.
 * A separate build path here would be a second definition of what gets
 * exported, and the two would agree right up until one of them was changed —
 * which is the failure the whole cutover is trying not to repeat. It also means
 * the world-matrix flush the legacy does with `wrapperGroup.updateMatrixWorld`
 * happens here too, inside `buildManifest`, rather than being the caller's job
 * to remember.
 */
async function buildCfdrunJsonV2(): Promise<unknown> {
  return sceneRunDeps(currentRunSources()).buildManifest()
}

/** The live scene builder, or null before the viewport has mounted one. */
function sceneBuilder(): SceneBuilder | null {
  return (window as unknown as { __sceneBuilder?: SceneBuilder }).__sceneBuilder ?? null
}

/**
 * `window.buildGeometryData()` — make sure the scene matches the project. ASYNC.
 *
 * The legacy's is the function that BUILDS the scene, so callers invoke it
 * defensively before anything that reads meshes; `/api/browser/export-stl`
 * opens with it.
 *
 * ## Why it has to await
 *
 * Calling `builder.rebuild()` straight after a store edit rebuilds the PREVIOUS
 * project. The builder reads its input through `host.project()`, which closes
 * over the viewport component's prop — and that prop is only replaced when React
 * re-renders, a tick after the store changed. Measured: create a floor and an
 * ACU, call rebuild, get 0 meshes and an empty export; wait one beat and the
 * same scene holds 7.
 *
 * That failure is silent in the worst way. An export against an unbuilt scene
 * writes a valid, EMPTY .cfdrun — no error anywhere, and every downstream step
 * runs on no geometry. So this waits two animation frames (one to let React
 * commit, one to let the effect that follows the commit run) before rebuilding.
 *
 * Returns the resulting mesh count. Callers must `await` it — `page.evaluate`
 * resolves promises, so `(async () => await buildGeometryData())()` is the shape
 * from `exec-js`.
 */
function buildGeometryData(): Promise<number> {
  return rebuildSceneFromProject()
}

/**
 * `window.buildGroupedStlMap(modelIds, includeLeakage)` — the STL set to write.
 *
 * Same name and argument order as the legacy's, because
 * `/api/browser/export-stl` calls it by name and writes whatever comes back
 * straight into `Mesh/constant/triSurface/`.
 *
 * `modelIds` is accepted for signature parity but the project's OWN model list
 * is what gets exported when the caller passes every uid, which is the only
 * thing any caller does. Passing a subset still works — it goes to the builder
 * unchanged.
 */
function buildGroupedStlMap(
  modelIds?: string[],
  includeLeakage = true,
): Record<string, string> {
  const builder = sceneBuilder()
  const project = store.getProject()
  if (!builder || !project) throw new Error('the 3D scene is not ready yet')
  if (modelIds && modelIds.length) {
    return builder.api.buildGroupedStlMap(modelIds, includeLeakage)
  }
  return buildStlFiles(builder, project, includeLeakage).files
}

export interface Placement {
  name?: string
  location?: { x: number; y: number; z: number }
  /** Legacy W/D/H: W=x, D=z, H=y. The builder's datasheets are in these. */
  size?: { W: number; D: number; H: number }
  /** Euler degrees, YXZ. Nobody writes quaternions into a prompt. */
  rotationDeg?: { x: number; y: number; z: number }
}

/**
 * `window.__dcdtuni.placeModel(uid, {...})` — name, put, size and turn a model.
 *
 * The builder sets all four for every model it creates, and the old SOP did it
 * by REACHING INTO the document: `state.models[state.models.length-1].name = ...`
 * then a manual `rebuildGeometrySingle`. That was already stale — the document
 * has been V4 since b821e27, where models are `constructions.<bucket>{uid}` with
 * `meta.name` and `geometry.location/size`, not an array of `.name/.anchor/.base`
 * — and in React it would be worse than stale: writing through the store's
 * object skips `edit()`, so nothing re-renders and undo never sees it.
 *
 * So this is the door. Every field goes through its own store setter, which is
 * where the V4 field names and the undo checkpoint both live.
 *
 * ONE undo entry per call would be nicer than four, but the four setters are the
 * ones the panels use, and a fifth path that writes the same fields differently
 * is exactly the drift this contract exists to avoid.
 */
function placeModel(uid: string, placement: Placement): boolean {
  const project = store.getProject()
  if (!project || !store.findConstruction(project, uid)) return false
  if (placement.name !== undefined) store.renameModel(uid, placement.name)
  if (placement.location) store.setModelLocation(uid, placement.location)
  if (placement.size) {
    // W/D/H -> x/y/z. The legacy convention maps D to z and H to y, and getting
    // it backwards makes a rack lie down instead of failing.
    store.setGeometryComponent(uid, 'size', 'x', placement.size.W)
    store.setGeometryComponent(uid, 'size', 'y', placement.size.H)
    store.setGeometryComponent(uid, 'size', 'z', placement.size.D)
  }
  if (placement.rotationDeg) store.setRotationDegrees(uid, placement.rotationDeg)
  return true
}

/**
 * `window.__dcdtuni.setRoomPolygon(plane, height)` — the room footprint.
 *
 * The footprint lives on the PROJECT (`geometry.plane` / `geometry.height`), not
 * on any model: the mesh pipeline reads it to build the blockMesh box and the
 * watertight shell. `replaceRoom` is the one writer, and it also takes model
 * churn — none here, so both lists are empty.
 *
 * `plane` is `[[x, z], ...]` in metres, counter-clockwise. CCW is not a
 * preference: the wall normals are derived from the winding, and a clockwise
 * polygon builds a room whose surfaces all face outward.
 */
function setRoomPolygon(plane: [number, number][], height: number): boolean {
  if (!Array.isArray(plane) || plane.length < 3 || !Number.isFinite(height)) return false
  store.replaceRoom(plane, height, [], [])
  return true
}

/**
 * `window.__dcdtuni.applyRoom(plane, height)` — the room shell, as the 2D editor's Apply builds it.
 *
 * `setRoomPolygon` stores the footprint only. The room's walls, floor and
 * ceiling are models too, and their flags are what the export reads: the floor
 * and ceiling are ROOM BOUNDARIES (`isRoomBoundary`, `cooling.roomBoundaryLocation`
 * bottom/top, `defaultBoundaryType: 'wall'`, the ground's normal turned to -Y),
 * while a floor from `createDefaultFloorModel` is an interior sheet — a
 * createBaffles wall baffle with air on both sides. Placed at y = 0 that sheet
 * has no fluid below it and the runner cannot seed it, so a room built from
 * default floors does not mesh. This verb is the Apply button: `generateRoom`
 * then `replaceRoom`, ONE undo step, replacing any earlier shell.
 *
 * `plane` is `[[x, z], ...]` in metres (counter-clockwise; a clockwise ring is
 * handled the way the editor handles it). Returns the new uids, or false.
 */
function applyRoom(
  plane: [number, number][],
  height: number,
): { walls: string[]; floor: string; ceiling: string } | false {
  const project = store.getProject()
  if (!project || !Array.isArray(plane) || plane.length < 3 || !Number.isFinite(height) || height <= 0) return false
  const ring = plane.map((v) => [Number(v[0]), Number(v[1])] as [number, number])
  if (ring.some((v) => !Number.isFinite(v[0]) || !Number.isFinite(v[1]))) return false
  const room = generateRoom(project, ring, height)
  store.replaceRoom(ring, height, room.replaces, [...room.walls, room.floor, room.ceiling])
  return { walls: room.walls.map((w) => w.model.uid), floor: room.floor.model.uid, ceiling: room.ceiling.model.uid }
}

/**
 * `window.addPatch(uid, faceId, type)` — an opening on a model's face, RETURNING its id.
 *
 * An ACU's supply and return are patches; so are floor tiles, wall leakage and
 * baffle openings. The legacy's `addPatch` returns nothing and leaves the caller
 * to fish the new one out of `model.faces[face].patches[last]` — a V2 shape that
 * is now `geometry.faces`, so every builder doing that read `undefined` and then
 * addressed a patch that did not exist.
 *
 * Returns the id on success, `null` on refusal. `type` is `rect` or `circle`.
 */
function addFacePatch(uid: string, faceId: string, type: 'rect' | 'circle' = 'rect'): string | null {
  const res = addPatch(uid, faceId, type)
  return res.ok ? (res.patchId ?? null) : null
}

/** `window.__dcdtuni.deletePatch(uid, patchId)` — remove an opening; false when there is no such patch. */
function deleteFacePatch(uid: string, patchId: string): boolean {
  return deletePatch(uid, patchId).ok
}

/**
 * `window.__dcdtuni.fillFacePatch(uid, patchId)` — stretch a patch over its whole face.
 *
 * The one thing every ACU supply/return needs and the fiddliest to do by hand:
 * the old builders computed `w`/`h` from `m.base[face.wType]`, which needs the
 * face's own axis mapping. `autoFillPatch` already knows it — both directions,
 * one call each.
 */
function fillFacePatch(uid: string, patchId: string): boolean {
  return autoFillPatch(uid, patchId, 'H').ok && autoFillPatch(uid, patchId, 'V').ok
}

/** `window.__dcdtuni.setPatchGeometry(uid, patchId, field, value)` — x/y/w/h/cx/cy/r. */
function setPatchGeometry(uid: string, patchId: string, field: string, value: number): boolean {
  return updatePatchGeometry(uid, patchId, field, value).ok
}

/**
 * `window.updateAcuPatchPhysics(uid, patchId, path, value)` — a patch's boundary condition.
 *
 * Same name the builders already call. `path` is dotted, e.g. `role`,
 * `temperature`, `volumeFlowRate`.
 */
function updateAcuPatchPhysics(uid: string, patchId: string, path: string, value: unknown): boolean {
  if (!locatePatch(store.getProject(), uid, patchId)) return false
  // The defaults are load-bearing, not cosmetic: a patch whose physics lacks
  // `velocity` exports NO boundary binding, and nothing complains. The first
  // version of this verb passed none and produced an ACU with role, temperature
  // and flow all set and a .cfdrun with zero bindings.
  updatePatchPhysics(uid, patchId, path, value, { ...PATCH_PHYSICS_DEFAULTS })
  return true
}

/**
 * `window.saveProjectJson()` — the .cfdproj bytes, exactly as the save button writes.
 *
 * The old SOP's last step was `JSON.stringify(JSON.parse(JSON.stringify(state)))`,
 * which is a RAW dump of the live object. The real save is a whitelist of the
 * nine canonical V4 keys, because anything a panel parks on the project at
 * runtime would otherwise land in the file and fail the Python schema on the way
 * back in. Two-space JSON, the same bytes either editor writes.
 */
function saveProjectJson(): string | null {
  const project = store.getProject()
  return project ? serializeProjectState(project) : null
}

/**
 * `window.__dcdtuni.newProject()` — start from an empty document.
 *
 * Every builder needs this first and none of them had it. The old SOPs opened
 * with `state.construction = []`, which was their reset; nothing replaced it,
 * so a build run against a page that already held a project ADDED to it. Caught
 * by scoring one: 14 models built, 28 in the file, every name twice, and a 50%
 * score whose deductions all read "extra".
 *
 * A fresh document, not a cleared one: `newDocumentV4` seeds the meta,
 * geometry and runDefaults a new project is supposed to have, and a project
 * with an empty `constructions` but no `runDefaults` is not the same thing.
 */
function newProject(): boolean {
  const fresh = stateV4().normalizeStateV4(stateV4().newDocumentV4()) as unknown as CfdProject
  store.loadProject(fresh, null)
  return true
}

/**
 * `window.__dcdtuni.setProjectMeta({...})` — name, input unit, ambient temperature,
 * case directory.
 *
 * `casePath` is stored as given, the form the save button writes: relative to
 * the repository (`case/<name>`) for a case inside it. It goes through
 * `writeCasePath`, the case-directory picker's writer, so opening the saved file
 * on any checkout points the editor at that case without asking.
 *
 * The room temperature is the initial condition the whole domain starts at, and
 * it is not a `simulationConfig` field — it lives on the project geometry with a
 * Kelvin mirror the solver reads. `setRoomTemperature` owns that pair; writing
 * one without the other is the shape of bug this contract exists to prevent.
 */
function setProjectMeta(meta: {
  name?: string
  inputUnit?: 'mm' | 'cm' | 'm'
  roomTemperatureC?: number
  casePath?: string
}): void {
  if (meta.name !== undefined) setProjectName(meta.name)
  if (typeof meta.casePath === 'string' && meta.casePath.trim()) writeCasePath(meta.casePath.trim())
  if (meta.inputUnit) store.setInputUnit(meta.inputUnit)
  if (typeof meta.roomTemperatureC === 'number') setRoomTemperature(meta.roomTemperatureC)
}

/**
 * `window.getCfdrunFilename()` — what the exported manifest is called.
 *
 * The server writes the .cfdrun under this exact name
 * (`/api/browser/export-case`, `/api/browser/full-pipeline`), and the runner
 * finds the case's model by it, so a different answer here does not fail — it
 * writes a second manifest beside the first and leaves the runner reading the
 * stale one.
 */
function getCfdrunFilename(): string {
  return cfdrunFilename(store.getProject(), store.getPath())
}

/**
 * `window.__dcdtuni.geometryStatus()` — what the scene actually holds.
 *
 * The legacy answers this from free variables (`renderMeshes`, `renderer`,
 * `webglAvailable`) that only exist because everything there is a global. Here
 * the scene lives on the builder, so the diagnostic has to ask it.
 *
 * `webglAvailable` is PROBED rather than remembered: the point of the endpoint
 * is to tell a screenshot showing nothing apart from a screenshot that could
 * not have shown anything, and a flag set at boot cannot do that.
 */
function geometryStatus() {
  const builder = sceneBuilder()
  let webglAvailable: boolean | null = null
  try {
    const canvas = document.createElement('canvas')
    webglAvailable = !!(canvas.getContext('webgl2') ?? canvas.getContext('webgl'))
  } catch {
    webglAvailable = false
  }
  return {
    webglAvailable,
    renderMeshesCount: builder ? builder.registries.renderMeshes.length : -1,
    modelsLoaded: allModelsNow().length,
    rendererExists: !!builder,
  }
}

/**
 * The project's models, flat — BARE models, not `{bucket, model}` pairs.
 *
 * The unwrap is the whole point. `schema/allModels` returns pairs because the
 * feature tree needs the bucket; every existing caller of `window.allModels`
 * wants the model itself:
 *
 *   server.py:2534/2602/2669   `window.allModels().map(m => m.uid)`
 *   js/cfdrun_build.js:171/368 `window.allModels().forEach(m => ...)`
 *
 * Handing those the pair shape does not throw — it yields `undefined` uids and
 * an export whose patch list is silently empty. Caught in the browser, not by
 * the first version of this file's test, which only asserted the name was a
 * function. The test now asserts the shape.
 */
/**
 * A rack's or CDU's liquid port frames in WORLD space, as the liquid compiler
 * places its manifold and CDU nodes: `{supply_top, supply_bottom, return_top,
 * return_bottom}` from the shared pose kernel (`model_pose_ports.js`), or null
 * for a uid that is neither. Read-only.
 */
function equipmentPortFrames(uid: string) {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!model || !['rack', 'cdu'].includes(String(model.kind))) return null
  const frames: Record<string, unknown> = {}
  for (const role of ['supply', 'return']) {
    for (const end of ['top', 'bottom']) {
      frames[`${role}_${end}`] = modelPoseKernel.equipmentPortFrame(
        model as unknown as Record<string, unknown>, String(model.kind), role, end)
    }
  }
  return frames
}

/**
 * Nominal world boxes `{uid: {kind, enabled, min, max}}` of the given models
 * (all models when omitted): location, size and rotation, the box the export
 * writes and the move tools snap, from `transform_tools.js`. Read-only.
 */
function modelBounds(uids?: string[]) {
  const project = store.getProject()
  if (!project) return {}
  const tools = transformToolsModule()
  const wanted = uids ? new Set(uids) : null
  const out: Record<string, { kind: string; enabled: boolean; min: number[]; max: number[] }> = {}
  for (const { model } of allModels(project as CfdProject)) {
    if (wanted && !wanted.has(model.uid)) continue
    const box = tools.getModelAABB(model)
    if (!box) continue
    out[model.uid] = { kind: String(model.kind), enabled: model.meta?.enabled !== false,
      min: box.min.toArray(), max: box.max.toArray() }
  }
  return out
}

function allModelsNow() {
  const project = store.getProject()
  if (!project) return []
  return allModels(project as CfdProject).map((entry) => entry.model)
}

const automationVerbs = {
  // ── creation ────────────────────────────────────────────────────────────
  createDefaultRackModel: () => createAndAdd(CREATE_KIND.rack),
  createDefaultAcuModel: () => createAndAdd(CREATE_KIND.acu),
  createDefaultWallModel: () => createAndAdd(CREATE_KIND.wall),
  createDefaultBaffleModel: () => createAndAdd(CREATE_KIND.baffle),
  createDefaultFloorModel: () => createAndAdd(CREATE_KIND.floor),
  createDefaultBaseModel: () => createAndAdd(CREATE_KIND.box),
  createDefaultCduModel: () => createAndAdd(CREATE_KIND.cdu),
  createDefaultPipeModel: () => createAndAdd(CREATE_KIND.pipe),
  /** Replace an air pipe's polyline with WORLD points (breaks its joins). */
  setAirPipeWorldPoints,
  setAirPipeDiameter,
  setAirPipeShape,
  setAirPipeSize,
  /** Snap `end` of `uid` onto `otherEnd` of `otherUid` and join them. */
  joinAirPipes: (uid: string, end: 'start' | 'end', otherUid: string, otherEnd: 'start' | 'end') => {
    let ok = false
    store.edit('joinAirPipes', `${uid}.${end} -> ${otherUid}.${otherEnd}`, (draft) => {
      ok = joinAirPipesInDraft(draft, uid, end, otherUid, otherEnd)
    })
    return ok
  },
  airPipeWorldPoints: (uid: string) => {
    const project = store.getProject()
    const model = project ? store.findConstruction(project, uid)?.model : null
    return model ? airPipeWorldPoints(model) : null
  },

  // ── liquid ──────────────────────────────────────────────────────────────
  /** A liquid pipe's world waypoints and named valve positions; topology unchanged. */
  setPipeSegmentPoints,
  /** A curve pump's speed ratio, as the CDU inspector writes it. */
  setCduPumpSpeed: setPumpSpeedRatio,
  /** A liquid pipe's bore in metres, as the pipe inspector writes it. */
  setPipeDiameter,
  /** A flow-control or isolation valve's opening (0.001..1), as the pipe inspector writes it. */
  setValveOpening,

  // ── placement ───────────────────────────────────────────────────────────
  newProject,
  placeModel,
  setRoomPolygon,
  applyRoom,
  setProjectMeta,
  // ── transient timeline (setPlayhead/playheadTime/sceneFrozen come with the store)
  setSimulationMode,

  // ── patches ─────────────────────────────────────────────────────────────
  addPatch: addFacePatch,
  deletePatch: deleteFacePatch,
  fillFacePatch,
  setPatchGeometry,
  updateAcuPatchPhysics,

  // ── rack interior ───────────────────────────────────────────────────────
  createRackEquipmentAtSlot,
  updateRackServerCfd,
  rackServers,
  // Stacked servers (spec §10): the batch and per-server opacity knobs, an
  // explicit-range add at an absolute depth, and the read-back the browser
  // verification asserts against.
  setRackServersOpacity,
  setServerOpacity,
  addRackServerAtRange,
  addServerPatch,
  rackServerDepths,
  // The U-slot layout (unit count, margins, height mode), the Rack Layout panel's writers.
  setRackLayout,
  setRackHeightMode,
  setRackDoorOffset,

  // ── selection ───────────────────────────────────────────────────────────
  // The Evaluator's job is to look at what the Builder made, and looking means
  // selecting: the inspector shows the PROJECT panel until a model is picked.
  // There was no way to do that from a script, so every check of a model's
  // panel had to read the document instead of the UI — which cannot catch a
  // panel that fails to render what the document holds.
  select: (uid: string) => {
    requestSelect(uid)
    return true
  },
  getSelection: () => getSelection(),

  // ── view ────────────────────────────────────────────────────────────────
  applyViewPreset: (preset: ViewPreset) => applyViewPreset(preset),
  /** The Omniverse session's six display channels (scene, view, overlay,
   * result, postprocess, liquid) and what each delivered; null in Three.js. */
  ovDisplay: () => ovDisplay()?.channels() ?? null,

  // ── run ─────────────────────────────────────────────────────────────────
  setCaseDir: (dir: string) => session.setCaseDir(dir),
  refreshServerInfo: () => session.refreshServerInfo(),
  runMeshGeneration,
  runSolveOnly,
  setSolverType,
  setSolverConfig,
  setMeshConfig,
  runLiquidSolve: (opts?: RunLiquidOptions) =>
    runLiquidSolve(opts ?? ({} as RunLiquidOptions)),

  // ── export ──────────────────────────────────────────────────────────────
  saveProjectJson,
  buildCfdrunJsonV2,
  buildGeometryData,
  buildGroupedStlMap,
  getCfdrunFilename,
  geometryStatus,

  // ── observation ─────────────────────────────────────────────────────────
  // A script that starts a run has to be able to WATCH it. `runSolveOnly()`
  // resolves as soon as the POST is accepted — the job runs for minutes after
  // that — so without these the only way to follow it is to guess the job id or
  // scrape the case directory. Both were tried; both are worse.
  // The liquid solve's stage, progress and results. Same reason as getJob: the
  // legacy kept these at `state.liquidCooling.*` on the document, React keeps
  // them in the session module, and a verification script has to read them
  // somewhere. `runLiquidSolve` takes the device as an ARGUMENT here — it is not
  // a field to poke before firing.
  liquidRunState: () => liquidRunState(),

  getJob: () => {
    // WITHOUT `lines`. The job carries its whole streamed log — 142 KB and
    // climbing on a real mesh — and a status probe that returns the log is a
    // status probe nothing can poll. The line COUNT is what a caller actually
    // wants from it ("is it moving?"); the text belongs to /api/log-stream.
    const job = session.getJob()
    if (!job) return null
    const { lines, ...rest } = job
    return { ...rest, lineCount: lines.length, lastLine: lines[lines.length - 1] ?? null }
  },
  getServerInfo: () => session.getServerInfo(),
  getCaseStatus: () => session.getCaseStatus(),
  isValidated: () => session.isValidated(),

  // ── project ─────────────────────────────────────────────────────────────
  allModels: allModelsNow,
  equipmentPortFrames,
  modelBounds,
}

/**
 * The contract: the store's own methods plus the verbs above.
 *
 * The store half is not decoration — `parity/react_probe.py` and
 * `parity/liquid_drive.py` both evaluate `window.__dcdtuni.getProject()`, and
 * dropping it would make the harness report "no models" instead of an error.
 */
export const automation = { ...store, ...automationVerbs }

export type Automation = typeof automation

/** Install on `window`. Imported for this side effect by `main.tsx`. */
export function installAutomationContract(target: Window = window): void {
  const w = target as unknown as Record<string, unknown>
  w.__dcdtuni = automation
}

if (typeof window !== 'undefined') installAutomationContract(window)
