/**
 * The imported-STL workflow: bringing a mesh in, and cutting patches into it.
 *
 * Three writes live here, and they are the ones that make a `custom` model more
 * than a grey box:
 *
 * - **Import.** `stlAssets.readStlFile` parses the file; this turns the result
 *   into a V4 construction and puts it in the project. The seed is the LEGACY
 *   `createModelFromRaw` seed for `kind: 'custom'` field for field, key order
 *   included — a custom model carries `rack`, `polygon`, `isRoomBoundary` and a
 *   null `rotateTool` that the other factories never emit, because
 *   `createModelFromRaw` is the only path the legacy import takes and it fills
 *   all of them. Dropping them would make a React-imported STL diff against a
 *   legacy-imported one for no reason.
 * - **Patch on a region.** A multi-solid STL names one OpenFOAM boundary per
 *   `solid` block, so a patch belongs to a REGION, not to one of the six
 *   projection faces. `patchActions.addPatch` only ever passes a null region;
 *   this is `window.addPatchToRegion` (STL_generate.html:14562).
 * - **Bool Diff.** A custom patch starts as a beam floating in front of the
 *   mesh. Applying it intersects the beam with the planar shell regions and
 *   turns the result into solids; un-applying brings the beam back so it can be
 *   moved. That is `applied`, and it is the whole "water transfer printing"
 *   workflow — without it a patch on an imported STL exports nothing.
 *
 * The intersection itself is NOT reimplemented. `getAppliedCustomPatchFragments`
 * lives in the shared `js/geometry_scene.js`, which is also what draws the
 * result, so asking it is the only way to know that applying will produce
 * anything before committing the flag.
 */

import type { Bucket, CfdProject, Construction } from '../schema/cfdproj'
import { bucketForKind, finalizeNewModel, type V3Seed } from '../legacy/cfdprojModules'
import { nextModelUid } from '../legacy/factories'
import {
  applyProjectionDefaults,
  customPatchDefaultSize,
  dominantFaceFromNormal,
  enforceCustomPatchSafety,
  nextPatchSeq,
  PATCH_PALETTE,
  type FaceRecord,
  type PatchRecord,
  type PatchType,
  type Vec3Like,
} from './patchGeometry'
import * as store from '../projectStore'
import { allModelPatches, customRegionGroups, solidRegionsOf } from './customRegions'
import type { ImportedStl } from '../io/stlAssets'

type Bag = Record<string, unknown>

export interface CustomMeshResult {
  ok: boolean
  uid?: string
  faceId?: string
  patchId?: string
  /**
   * Which refusal, as a key rather than a sentence.
   *
   * The panel picks the dictionary entry; a test asserting on English prose
   * would break the first time the wording changed.
   */
  reason?: 'no-model' | 'not-custom' | 'no-patch' | 'not-planar' | 'no-intersection' | 'no-face'
  error?: string
}

// ---------------------------------------------------------------------------
// The shared modules, looked up on `window` at CALL time
// ---------------------------------------------------------------------------

/**
 * Resolved lazily, exactly as `patchActions.ts` does: importing
 * `legacy/geometryBuilder` here would drag Three.js and polygon-clipping into
 * the tests for the arithmetic, which have no business loading a renderer.
 */
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
  getAppliedCustomPatchFragments(
    model: unknown,
    faceId: string,
    patch: unknown,
  ): { regionName: string }[]
}

interface GeometryPureLike {
  getAverageNormalForGeometry(geometry: unknown): Vec3Like
  canUseSurfaceProjectionFallback(cacheEntry: unknown): boolean
}

interface RackStateLike {
  createFacesState(labelOverrides?: Record<string, string>): unknown
  cloneRackState(raw?: unknown): unknown
  getDefaultModelTransform(): unknown
  getDefaultModelMoveTool(): unknown
  getDefaultCfd(kind: string): unknown
}

function sceneApi(): SceneApiLike | null {
  if (typeof window === 'undefined') return null
  return (
    (window as unknown as { __sceneBuilder?: { api?: SceneApiLike } }).__sceneBuilder?.api ?? null
  )
}

function geometryPure(): GeometryPureLike | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { geometryPure?: GeometryPureLike }).geometryPure ?? null
}

function rackState(): RackStateLike | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { rackState?: RackStateLike }).rackState ?? null
}

/** The live scene's parsed-STL cache, or null before the viewport has mounted. */
export function geometryCache(): Map<string, unknown> | null {
  if (typeof window === 'undefined') return null
  return (
    (window as unknown as { __viewportRegistries?: { geometryCache?: Map<string, unknown> } })
      .__viewportRegistries?.geometryCache ?? null
  )
}

// ---------------------------------------------------------------------------
// Regions
// ---------------------------------------------------------------------------

function facesOf(model: Construction | undefined): Record<string, FaceRecord> | null {
  const faces = (model?.geometry as Bag | undefined)?.faces
  if (!faces || Array.isArray(faces)) return null
  return faces as Record<string, FaceRecord>
}

/** The bounds of one region, falling back to the model's own box. */
function regionSize(model: Construction, regionName: string | null): Vec3Like {
  const box = sceneApi()?.getRegionGeometryEntry(model, regionName)?.geometry?.boundingBox
  if (box) {
    return { x: box.max.x - box.min.x, y: box.max.y - box.min.y, z: box.max.z - box.min.z }
  }
  const size = (model.geometry?.size ?? {}) as Partial<Vec3Like>
  return { x: Number(size.x) || 0, y: Number(size.y) || 0, z: Number(size.z) || 0 }
}

/**
 * Which of the six projection faces a region's patch hangs on.
 *
 * The face is not cosmetic: it seeds the beam's default direction and its
 * orientation, so a patch on a downward-facing solid that landed on `front`
 * would project along the wrong axis and cut nothing.
 */
export function inferHostFace(model: Construction, regionName: string | null): string {
  const api = sceneApi()
  const pure = geometryPure()
  if (!api || !pure) return 'front'
  const region = api.getRegionGeometryEntry(model, regionName)
  if (!region?.geometry) return 'front'
  return dominantFaceFromNormal(pure.getAverageNormalForGeometry(region.geometry))
}

// ---------------------------------------------------------------------------
// Patches on a region
// ---------------------------------------------------------------------------

/**
 * Build the patch record a region gets, without touching the store.
 *
 * Separate from the store write so the numbers can be tested: everything here
 * is a function of the model and the region's bounds.
 */
export function buildRegionPatch(
  model: Construction,
  faceId: string,
  type: PatchType,
  seq: number,
  regionName: string | null,
): PatchRecord {
  const defaults = customPatchDefaultSize(regionSize(model, regionName))
  const patch: PatchRecord = {
    id: `p_${seq}`,
    type,
    regionName: regionName || null,
    name: type === 'circle' ? `Circle_Patch_${seq}` : `Rect_Patch_${seq}`,
    color: PATCH_PALETTE[allModelPatches(model).length % PATCH_PALETTE.length] as string,
    applied: false,
    depth: defaults.depth,
  }
  if (type === 'circle') patch.r = defaults.radius
  else {
    patch.w = defaults.width
    patch.h = defaults.height
  }
  applyProjectionDefaults(faceId, patch)
  sceneApi()?.ensureCustomPatch3DDefaults(model, faceId, patch, regionName)
  enforceCustomPatchSafety(patch)
  return patch
}

/**
 * Add a projected circle or rectangle to ONE solid region of an imported mesh.
 *
 * `regionName` null means the whole model, which is what a single-solid STL
 * gets. Ports `window.addPatchToRegion` (STL_generate.html:14562).
 */
export function addPatchToRegion(
  uid: string,
  regionName: string | null,
  type: PatchType,
): CustomMeshResult {
  const project = store.getProject()
  const hit = project ? store.findConstruction(project, uid) : null
  if (!hit) return { ok: false, reason: 'no-model' }
  if (hit.model.kind !== 'custom') return { ok: false, reason: 'not-custom' }

  // Resolved BEFORE the edit so a refusal costs no undo step — `store.edit`
  // checkpoints unconditionally.
  const faceId = inferHostFace(hit.model, regionName)
  if (!facesOf(hit.model)?.[faceId]) return { ok: false, reason: 'no-face' }
  const seq = nextPatchSeq(project)
  let landed: string | null = null

  store.edit('addPatchToRegion', `${uid}/${regionName ?? '__whole__'} ${type}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    const face = model ? facesOf(model)?.[faceId] : null
    if (!model || !face) return
    const patch = buildRegionPatch(model, faceId, type, seq, regionName)
    if (!Array.isArray(face.patches)) face.patches = []
    face.patches.push(patch)
    landed = patch.id
  })

  if (!landed) return { ok: false, reason: 'no-face' }
  return { ok: true, uid, faceId, patchId: landed }
}

// ---------------------------------------------------------------------------
// Bool Diff
// ---------------------------------------------------------------------------

function locate(
  project: CfdProject | null,
  uid: string,
  patchId: string,
): { model: Construction; faceId: string; patch: PatchRecord } | null {
  if (!project) return null
  const hit = store.findConstruction(project, uid)
  const faces = facesOf(hit?.model)
  if (!hit || !faces) return null
  for (const [faceId, face] of Object.entries(faces)) {
    const patch = (face.patches ?? []).find((p) => p.id === patchId)
    if (patch) return { model: hit.model, faceId, patch }
  }
  return null
}

/**
 * Whether this mesh can take a Bool Diff at all.
 *
 * The stable workflow needs every solid to be a PLANAR shell — the beam is
 * intersected with each region's plane — so a curved or closed STL is refused
 * up front rather than producing an empty cut nobody can explain.
 */
export function canBoolDiff(uid: string): boolean {
  const entry = geometryCache()?.get(uid)
  return geometryPure()?.canUseSurfaceProjectionFallback(entry) === true
}

/**
 * How many solid regions this patch would produce, or produces now.
 *
 * The panel shows it as the applied/not-applied status, and `apply` uses it as
 * the go/no-go: a beam that misses every region must not be committed, because
 * the patch would then be marked applied while nothing was drawn or exported.
 */
export function appliedFragmentCount(uid: string, patchId: string): number {
  const found = locate(store.getProject(), uid, patchId)
  const api = sceneApi()
  if (!found || !api) return 0
  if (!canBoolDiff(uid)) return 0
  return api.getAppliedCustomPatchFragments(found.model, found.faceId, {
    ...found.patch,
    applied: true,
  }).length
}

/** Execute the projection: the beam becomes solid regions on the imported mesh. */
export function applyCustomPatchBoolDiff(uid: string, patchId: string): CustomMeshResult {
  const found = locate(store.getProject(), uid, patchId)
  if (!found) return { ok: false, reason: 'no-patch' }
  if (found.model.kind !== 'custom') return { ok: false, reason: 'not-custom' }
  if (!canBoolDiff(uid)) return { ok: false, reason: 'not-planar' }
  if (appliedFragmentCount(uid, patchId) === 0) return { ok: false, reason: 'no-intersection' }

  store.edit('applyCustomPatchBoolDiff', `${uid}/${patchId}`, (draft) => {
    const target = locate(draft, uid, patchId)
    if (target) target.patch.applied = true
  })
  return { ok: true, uid, faceId: found.faceId, patchId }
}

/** Undo the projection: the cut regions go away and the beam comes back. */
export function unapplyCustomPatch(uid: string, patchId: string): CustomMeshResult {
  const found = locate(store.getProject(), uid, patchId)
  if (!found) return { ok: false, reason: 'no-patch' }
  if (found.model.kind !== 'custom') return { ok: false, reason: 'not-custom' }
  store.edit('unapplyCustomPatch', `${uid}/${patchId}`, (draft) => {
    const target = locate(draft, uid, patchId)
    if (target) target.patch.applied = false
  })
  return { ok: true, uid, faceId: found.faceId, patchId }
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

/**
 * The v3 seed a legacy import produces, field for field.
 *
 * `createModelFromRaw` (STL_generate.html:3326) is the ONLY path the legacy
 * import takes, and it fills fields the kind-specific factories never touch:
 * `fromModel`, a full default `rack` block, `polygon`, `isRoomBoundary` and a
 * null `rotateTool`. They all survive the v3->v4 migrator as top-level keys, so
 * a seed without them produces a model that diffs against a legacy-imported one
 * in six places. The order below is that function's own literal order, because
 * JSON.stringify writes keys in insertion order and the parity check is a byte
 * diff.
 */
export function customImportSeed(project: CfdProject, imported: ImportedStl): V3Seed {
  const rack = rackState()
  if (!rack) throw new Error('window.rackState is missing — the shared modules did not load.')
  return {
    id: nextModelUid(project),
    fromModel: null,
    kind: 'custom',
    name: imported.name,
    enabled: true,
    showAnchorMarker: true,
    opacity: 1,
    anchor: { x: 0, y: 0, z: 0 },
    base: { ...imported.base },
    transform: rack.getDefaultModelTransform(),
    moveTool: rack.getDefaultModelMoveTool(),
    faces: rack.createFacesState(),
    solidRegions: imported.solidRegions,
    customSTLBase64: imported.customSTLBase64,
    customOffset: { ...imported.customOffset },
    rack: rack.cloneRackState(),
    cutouts: [],
    polygon: null,
    isRoomBoundary: false,
    // Null HERE so the key lands in the legacy's position; `buildCustomModel`
    // fills it in afterwards. The legacy seed is null too and its inspector
    // materialises the block on the first render after the import selects the
    // model, so a file saved from either app carries the filled block.
    rotateTool: null,
    cfd: rack.getDefaultCfd('custom'),
  }
}

/**
 * Build the V4 construction for a parsed STL, ready to drop into the project.
 *
 * `rotateTool` is filled AFTER the migrator, exactly as `legacy/factories.ts`
 * does for every other kind. The legacy import leaves it null and its inspector
 * materialises the block when the new model is selected, so a legacy-imported
 * model that has been looked at once carries `{enabled:false, axes:{yaw:true,
 * pitch:false, roll:false}}` — measured, by importing the same file in both
 * apps and diffing: that field was the only difference, and this closes it.
 */
export function buildCustomModel(
  project: CfdProject,
  imported: ImportedStl,
): { bucket: Bucket; model: Construction } {
  const model = finalizeNewModel(customImportSeed(project, imported))
  ;(model as unknown as Bag).rotateTool = {
    enabled: false,
    axes: { yaw: true, pitch: false, roll: false },
  }
  return { bucket: bucketForKind('custom'), model }
}

/**
 * The imported-mesh writers on `window`, under the legacy app's own names.
 *
 * Same reason `patchActions.ts` publishes its own: the two editors have to be
 * drivable by the SAME browser snippet so their output can be diffed, and the
 * legacy has `addPatchToRegion`, `applyCustomPatchBoolDiff` and
 * `unapplyCustomPatch` as page globals.
 */
if (typeof window !== 'undefined') {
  ;(window as unknown as { __customMesh?: unknown }).__customMesh = {
    addPatchToRegion,
    applyCustomPatchBoolDiff,
    unapplyCustomPatch,
    appliedFragmentCount,
    canBoolDiff,
    customRegionGroups,
    buildCustomModel,
    customImportSeed,
    solidRegionsOf,
  }
}
