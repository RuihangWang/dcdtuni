/**
 * Creating, deleting and reshaping patches — the writes behind the patch panels.
 *
 * `patchPhysics.ts` already edits a patch that EXISTS. This module is the other
 * half: bringing one into being, taking it away, and the two operations that
 * change its footprint rather than one of its numbers (auto-fill and the
 * projection-vector reset). Without it a project can only ever carry the
 * boundary openings it was loaded with.
 *
 * Ports `addPatch` (STL_generate.html:14508), `autoFill` (:14634),
 * `togglePatchVectorVisibility` (:14664), `resetPatchVector` (:14674),
 * `deletePatch` (:14686), `deletePatchWithCavity` (:14701) and `updatePatchCfd`
 * (:14721).
 */

import type { CfdProject, Construction } from '../schema/cfdproj'
import {
  applyProjectionDefaults,
  autoFillRect,
  circleToPoly,
  createPatchRecord,
  customPatchDefaultSize,
  defaultPatchVector,
  dominantFaceFromNormal,
  enforceCustomPatchSafety,
  enforcePatchSafety,
  nextPatchSeq,
  partitionWallEditableFaceId,
  PATCH_PALETTE,
  rectToPoly,
  type FaceRecord,
  type PatchRecord,
  type PatchType,
  type Vec3Like,
} from './patchGeometry'
import * as store from '../projectStore'

type Bag = Record<string, unknown>

export interface PatchActionResult {
  ok: boolean
  faceId?: string
  patchId?: string
  /**
   * Set when a clamp moved the user's numbers, or a validation refused the edit.
   *
   * The legacy flashes `#safety-alert` for the first and `alert()`s the second;
   * both are reported back to the caller here so the panel decides, and so a
   * test can assert on the refusal rather than on a global side effect.
   */
  clamped?: boolean
  error?: string
}

/**
 * The shared modules, looked up on `window` at CALL time.
 *
 * Only the partition-wall and custom-STL paths need them, and importing
 * `legacy/geometryBuilder` here would drag Three.js and polygon-clipping into
 * every module that writes a patch — including the tests for the arithmetic,
 * which have no business loading a renderer. The legacy resolves the same two
 * modules the same lazy way (`_geomScene_()`, STL_generate.html:5555).
 */
interface GeometryPureLike {
  syncPartitionWallModelState(model: unknown): void
  getAverageNormalForGeometry(geometry: unknown): Vec3Like
}

interface SceneApiLike {
  getRegionGeometryEntry(
    model: unknown,
    regionName: string | null,
  ): { geometry?: { boundingBox?: { min: Vec3Like; max: Vec3Like } } } | null
  ensureCustomPatch3DDefaults(
    model: unknown,
    faceId: string,
    patch: unknown,
    regionName: string | null,
  ): void
}

interface StateV4Like {
  deletePatchPhysics(model: unknown, patchId: string): void
}

function geometryPureOrNull(): GeometryPureLike | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { geometryPure?: GeometryPureLike }).geometryPure ?? null
}

function sceneApiOrNull(): SceneApiLike | null {
  if (typeof window === 'undefined') return null
  return (
    (window as unknown as { __sceneBuilder?: { api?: SceneApiLike } }).__sceneBuilder?.api ?? null
  )
}

function stateV4OrNull(): StateV4Like | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { cfdprojStateV4?: StateV4Like }).cfdprojStateV4 ?? null
}

function facesOf(model: Construction | undefined): Record<string, FaceRecord> | null {
  const faces = (model?.geometry as Bag | undefined)?.faces
  if (!faces || Array.isArray(faces)) return null
  return faces as Record<string, FaceRecord>
}

/** Find a patch by id, and the face holding it. */
export function locatePatch(
  project: CfdProject | null,
  uid: string,
  patchId: string,
): { model: Construction; faceId: string; face: FaceRecord; patch: PatchRecord } | null {
  if (!project) return null
  const hit = store.findConstruction(project, uid)
  if (!hit) return null
  const faces = facesOf(hit.model)
  if (!faces) return null
  for (const [faceId, face] of Object.entries(faces)) {
    const patch = (face.patches ?? []).find((p) => p.id === patchId)
    if (patch) return { model: hit.model, faceId, face, patch }
  }
  return null
}

/** Every patch on a model, across every face — the colour index for a custom patch. */
function countModelPatches(model: Construction): number {
  const faces = facesOf(model)
  if (!faces) return 0
  return Object.values(faces).reduce((n, face) => n + (face.patches ?? []).length, 0)
}

/**
 * A custom-STL patch's starting size, from the bounds of the region it mounts on.
 *
 * Falls back to the model's own box when the imported geometry is not in the
 * scene cache — the same fallback `getRegionBounds` takes
 * (STL_generate.html:6104), and the reason a patch still appears on a model
 * whose STL has not finished parsing.
 */
function regionSize(model: Construction, regionName: string | null): Vec3Like {
  const entry = sceneApiOrNull()?.getRegionGeometryEntry(model, regionName)
  const box = entry?.geometry?.boundingBox
  if (box) {
    return {
      x: box.max.x - box.min.x,
      y: box.max.y - box.min.y,
      z: box.max.z - box.min.z,
    }
  }
  const size = (model.geometry?.size ?? {}) as Partial<Vec3Like>
  return { x: Number(size.x) || 0, y: Number(size.y) || 0, z: Number(size.z) || 0 }
}

/** Which face an imported region's patch hangs on, when the caller has not said. */
function inferHostFace(model: Construction, regionName: string | null): string {
  const api = sceneApiOrNull()
  const pure = geometryPureOrNull()
  if (!api || !pure) return 'front'
  const region = api.getRegionGeometryEntry(model, regionName)
  if (!region?.geometry) return 'front'
  return dominantFaceFromNormal(pure.getAverageNormalForGeometry(region.geometry))
}

function addCustomPatch(
  model: Construction,
  faceId: string | null,
  type: PatchType,
  seq: number,
  regionName: string | null,
): { faceId: string; patch: PatchRecord } | null {
  const hostFace = faceId || inferHostFace(model, regionName)
  const faces = facesOf(model)
  const face = faces?.[hostFace]
  if (!face) return null
  const defaults = customPatchDefaultSize(regionSize(model, regionName))
  const patch: PatchRecord = {
    id: `p_${seq}`,
    type,
    regionName: regionName || null,
    name: type === 'circle' ? `Circle_Patch_${seq}` : `Rect_Patch_${seq}`,
    color: PATCH_PALETTE[countModelPatches(model) % PATCH_PALETTE.length] as string,
    applied: false,
    depth: defaults.depth,
  }
  if (type === 'circle') patch.r = defaults.radius
  else {
    patch.w = defaults.width
    patch.h = defaults.height
  }
  applyProjectionDefaults(hostFace, patch)
  sceneApiOrNull()?.ensureCustomPatch3DDefaults(model, hostFace, patch, regionName)
  enforceCustomPatchSafety(patch)
  if (!Array.isArray(face.patches)) face.patches = []
  face.patches.push(patch)
  return { faceId: hostFace, patch }
}

/**
 * Put a new circle or rectangle patch on a model face.
 *
 * Racks are refused: their openings are U-slots derived from the equipment in
 * them, and a free-floating patch on a rack face would export a boundary the
 * slot geometry knows nothing about.
 *
 * Partition walls redirect to their front face whatever face was asked for,
 * because a hole in a 50 mm plate is one hole seen from both sides — the legacy
 * keeps a single store and mirrors it. That path also VALIDATES: two holes that
 * overlap are refused outright, and the refusal has to leave the project exactly
 * as it was, which is why the whole thing runs inside one `store.edit` (a throw
 * there discards the draft before any checkpoint is pushed).
 */
export function addPatch(uid: string, faceId: string, type: PatchType): PatchActionResult {
  const project = store.getProject()
  const hit = project ? store.findConstruction(project, uid) : null
  if (!hit) return { ok: false, error: 'no such model' }
  if (hit.model.kind === 'rack') return { ok: false, error: 'rack' }

  const isCustom = hit.model.kind === 'custom'
  // Resolved BEFORE the edit, so a refusal costs no undo step: `store.edit`
  // checkpoints unconditionally, and a no-op mutation inside it would leave the
  // user pressing undo once for something that never happened.
  const targetFace = isCustom
    ? faceId || inferHostFace(hit.model, null)
    : hit.model.kind === 'partitionWall'
      ? partitionWallEditableFaceId(faceId)
      : faceId
  if (!targetFace) return { ok: false, error: 'face not editable' }
  if (!facesOf(hit.model)?.[targetFace]) return { ok: false, error: 'no such face' }

  const seq = nextPatchSeq(project)
  let landedFace: string | null = null
  let landedId: string | null = null
  let clamped = false

  try {
    store.edit('addPatch', `${uid}/${targetFace} ${type}`, (draft) => {
      const model = store.findConstruction(draft, uid)?.model
      if (!model) return
      if (model.kind === 'custom') {
        const created = addCustomPatch(model, targetFace, type, seq, null)
        if (!created) return
        landedFace = created.faceId
        landedId = created.patch.id
        return
      }
      const actualFaceId = targetFace
      const face = facesOf(model)?.[actualFaceId]
      if (!face) return
      if (!Array.isArray(face.patches)) face.patches = []
      const patch = createPatchRecord({
        model,
        faceId: actualFaceId,
        face,
        type,
        seq,
        colorIndex: face.patches.length,
      })
      applyProjectionDefaults(actualFaceId, patch)
      face.patches.push(patch)
      if (model.kind === 'partitionWall') {
        // Throws on overlap, and the throw is the rollback: `store.edit` has not
        // touched the live project or the undo stack at this point.
        geometryPureOrNull()?.syncPartitionWallModelState(model)
      } else {
        clamped = enforcePatchSafety(model, actualFaceId, face, patch)
      }
      landedFace = actualFaceId
      landedId = patch.id
    })
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }

  if (!landedId) return { ok: false, error: 'face not editable' }
  return { ok: true, faceId: landedFace ?? faceId, patchId: landedId, clamped }
}

/** Remove a patch, leaving the surface it was cut from solid again. */
export function deletePatch(uid: string, patchId: string): PatchActionResult {
  const project = store.getProject()
  const found = locatePatch(project, uid, patchId)
  if (!found) return { ok: false, error: 'no such patch' }
  try {
    store.edit('deletePatch', `${uid}/${patchId}`, (draft) => {
      const model = store.findConstruction(draft, uid)?.model
      const face = model ? facesOf(model)?.[found.faceId] : null
      if (!model || !face) return
      face.patches = (face.patches ?? []).filter((p) => p.id !== patchId)
      if (model.kind === 'partitionWall') {
        // The physics can outlive the patch: it is also kept in
        // `cooling.unboundPatchPhysics`, and a leftover entry there would be
        // re-bound to the next patch that happens to reuse the id.
        stateV4OrNull()?.deletePatchPhysics(model, patchId)
        geometryPureOrNull()?.syncPartitionWallModelState(model)
      }
    })
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
  return { ok: true, faceId: found.faceId }
}

/**
 * Remove a patch and cut its outline out of the surface for good.
 *
 * The difference from a plain delete is what happens to the AREA: a plain delete
 * gives the wall back, this leaves a hole. On a raised floor that is the
 * difference between a sealed tile and an open cable cut-out, so the two are
 * separate commands rather than a flag.
 */
export function deletePatchWithCavity(uid: string, patchId: string): PatchActionResult {
  const project = store.getProject()
  const found = locatePatch(project, uid, patchId)
  if (!found) return { ok: false, error: 'no such patch' }
  const { faceId, patch } = found
  const polygon =
    patch.type === 'circle'
      ? circleToPoly(patch.cx as number, patch.cy as number, patch.r as number)
      : rectToPoly(patch.x as number, patch.y as number, patch.w as number, patch.h as number)

  store.edit('deletePatchWithCavity', `${uid}/${patchId}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model) return
    const geometry = (model.geometry ?? ((model as Bag).geometry = {})) as Bag
    const cutouts = (geometry.cutouts ?? (geometry.cutouts = [])) as Bag[]
    cutouts.push({ faceId, polygon })
    const face = facesOf(model)?.[faceId]
    if (face) face.patches = (face.patches ?? []).filter((p) => p.id !== patchId)
  })
  return { ok: true, faceId }
}

/** Stretch a rectangular patch to the full width or height of its face. */
export function autoFillPatch(
  uid: string,
  patchId: string,
  direction: 'H' | 'V',
): PatchActionResult {
  const project = store.getProject()
  const found = locatePatch(project, uid, patchId)
  if (!found) return { ok: false, error: 'no such patch' }
  if (found.model.kind === 'custom' || found.patch.type !== 'rect') {
    return { ok: false, error: 'not a parametric rect patch' }
  }
  const isPartitionWall = found.model.kind === 'partitionWall'
  let clamped = false
  try {
    store.edit('autoFill', `${uid}/${patchId} ${direction}`, (draft) => {
      const model = store.findConstruction(draft, uid)?.model
      const face = model ? facesOf(model)?.[found.faceId] : null
      const patch = (face?.patches ?? []).find((p) => p.id === patchId)
      if (!model || !face || !patch) return
      autoFillRect(model, found.faceId, face, patch, direction)
      if (isPartitionWall) geometryPureOrNull()?.syncPartitionWallModelState(model)
      else clamped = enforcePatchSafety(model, found.faceId, face, patch)
    })
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
  return { ok: true, faceId: found.faceId, patchId, clamped }
}

/**
 * Write one geometric field of a patch, then pull it back inside the face.
 *
 * `patchPhysics.updatePatch` writes the value and stops, which is right for a
 * name or a colour and wrong for a radius: the legacy runs `enforceSafety` after
 * EVERY patch edit (STL_generate.html:14591), so a typed 5 m radius on a 0.6 m
 * face is clamped as it is entered rather than exported as a boundary hanging
 * off the surface.
 */
export function updatePatchGeometry(
  uid: string,
  patchId: string,
  field: string,
  value: number,
): PatchActionResult {
  if (!Number.isFinite(value)) return { ok: false, error: 'not a number' }
  const found = locatePatch(store.getProject(), uid, patchId)
  if (!found) return { ok: false, error: 'no such patch' }
  let clamped = false
  try {
    store.edit('updatePatch', `${uid}/${patchId} ${field}=${value}`, (draft) => {
      const target = locatePatch(draft, uid, patchId)
      if (!target) return
      target.patch[field] = value
      if (target.model.kind === 'custom') enforceCustomPatchSafety(target.patch)
      else if (target.model.kind === 'partitionWall') {
        geometryPureOrNull()?.syncPartitionWallModelState(target.model)
      } else {
        clamped = enforcePatchSafety(target.model, target.faceId, target.face, target.patch)
      }
    })
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
  return { ok: true, faceId: found.faceId, patchId, clamped }
}

/** Show or hide the arrow that says which way the patch is projected. */
export function togglePatchVectorVisibility(uid: string, patchId: string): PatchActionResult {
  const found = locatePatch(store.getProject(), uid, patchId)
  if (!found) return { ok: false, error: 'no such patch' }
  store.edit('togglePatchVectorVisibility', `${uid}/${patchId}`, (draft) => {
    const target = locatePatch(draft, uid, patchId)
    if (!target) return
    applyProjectionDefaults(target.faceId, target.patch)
    target.patch.showVector = !target.patch.showVector
  })
  return { ok: true, faceId: found.faceId, patchId }
}

/**
 * Point the projection back along the face's own inward normal.
 *
 * The vector decides where the cut lands on an imported mesh, so a hand-typed
 * one that has drifted off-axis can put the opening on a surface the user never
 * meant. This is the way back.
 */
export function resetPatchVector(uid: string, patchId: string): PatchActionResult {
  const found = locatePatch(store.getProject(), uid, patchId)
  if (!found) return { ok: false, error: 'no such patch' }
  store.edit('resetPatchVector', `${uid}/${patchId}`, (draft) => {
    const target = locatePatch(draft, uid, patchId)
    if (!target) return
    const v = defaultPatchVector(target.faceId)
    target.patch.vx = v.x
    target.patch.vy = v.y
    target.patch.vz = v.z
  })
  return { ok: true, faceId: found.faceId, patchId }
}

/**
 * Set one field of a patch's CFD block, or remove it.
 *
 * `null` DELETES the key rather than storing a null, because the .cfdrun builder
 * tests `patchCfd.fixedTemperature != null` to decide whether the patch becomes
 * a heat patch at all (js/cfdrun_build.js:637). A stored null would read the
 * same as absent there but would still travel in the saved project, so removing
 * the key is what makes "off" mean off.
 */
export function updatePatchCfd(
  uid: string,
  faceId: string,
  patchId: string,
  field: string,
  value: unknown,
): PatchActionResult {
  store.edit('updatePatchCfd', `${uid}/${faceId}/${patchId} ${field}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    const face = model ? facesOf(model)?.[faceId] : null
    const patch = (face?.patches ?? []).find((p) => p.id === patchId)
    if (!patch) return
    const cfd = (patch.cfd ?? (patch.cfd = {})) as Bag
    if (value === null) delete cfd[field]
    else cfd[field] = value
  })
  return { ok: true, faceId, patchId }
}

/** The patch's CFD block, or an empty one — for reading in the inspector. */
export function patchCfd(project: CfdProject | null, uid: string, patchId: string): Bag {
  const found = locatePatch(project, uid, patchId)
  return ((found?.patch.cfd ?? {}) as Bag) ?? {}
}

/**
 * The patch writers, on `window`, under the names the legacy app publishes.
 *
 * Not for the app — components import them. This exists so the two editors can
 * be driven by the SAME browser snippet and their output diffed: the legacy has
 * `window.addPatch(modelId, faceId, type)` and friends as page globals
 * (STL_generate.html:14508), and without a counterpart here every parity check
 * of a patch would have to click its way through two different panels and hope
 * it pressed the equivalent button. `__sceneBuilder` and `__transformTools` are
 * published for the same reason.
 */
if (typeof window !== 'undefined') {
  ;(window as unknown as { __patchActions?: unknown }).__patchActions = {
    addPatch,
    deletePatch,
    deletePatchWithCavity,
    autoFillPatch,
    togglePatchVectorVisibility,
    resetPatchVector,
    updatePatchCfd,
    updatePatchGeometry,
    locatePatch,
    patchCfd,
  }
}
