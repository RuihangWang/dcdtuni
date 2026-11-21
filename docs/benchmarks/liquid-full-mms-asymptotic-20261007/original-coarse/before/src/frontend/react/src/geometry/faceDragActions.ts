/**
 * The project writes a direct-manipulation drag makes.
 *
 * Split from the pointer handling in `faceDrag.ts` because these are the only
 * parts that touch the store, and they carry the rule that makes a drag one
 * undoable step: the drag mutates the live project every frame through
 * `store.mutateLive`, puts the STARTING values back on release, and commits
 * once through `store.edit`. Committing per frame would push sixty undo
 * entries and clone the whole project sixty times a second — the same reason
 * `moveGizmo.ts` works this way.
 */

import type { CfdProject, Construction } from '../schema/cfdproj'
import { createModel } from '../legacy/factories'
import {
  enforceCustomPatchSafety,
  enforcePatchSafety,
  type FaceRecord,
  type PatchRecord,
} from './patchGeometry'
import * as store from '../projectStore'
import type { Vec3 } from './faceDragMath'

interface GeometryPureLike {
  syncPartitionWallModelState(model: unknown): void
  rebasePolygonSheet(model: unknown, rotate?: (v: Vec3) => Vec3): boolean
}

/**
 * Looked up on `window` at CALL time, as `patchActions` does.
 *
 * Only the partition-wall branch needs it, and importing the geometry builder
 * here would pull Three.js into the tests for the arithmetic.
 */
function geometryPureOrNull(): GeometryPureLike | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { geometryPure?: GeometryPureLike }).geometryPure ?? null
}

export interface PatchSite {
  model: Construction
  faceId: string
  face: FaceRecord
  patch: PatchRecord
}

function facesOf(model: Construction | undefined): Record<string, FaceRecord> | null {
  const faces = (model?.geometry as { faces?: unknown } | undefined)?.faces
  if (!faces || Array.isArray(faces)) return null
  return faces as Record<string, FaceRecord>
}

/** Find a patch on a MODEL, without needing the project it belongs to. */
export function findPatchOnModel(
  model: Construction | undefined,
  patchId: string,
): PatchSite | null {
  if (!model) return null
  const faces = facesOf(model)
  if (!faces) return null
  for (const [faceId, face] of Object.entries(faces)) {
    const patch = (face.patches ?? []).find((p) => p.id === patchId)
    if (patch) return { model, faceId, face, patch }
  }
  return null
}

/**
 * Pull a patch back inside its face, by whatever rule its kind follows.
 *
 * Run on every drag frame, not only on release: the legacy runs `enforceSafety`
 * inside `updatePatchDrag` (STL_generate.html:17108), which is what stops a
 * patch being dragged off the edge of the surface it cuts and exported as a
 * boundary hanging in the air.
 */
export function enforceSiteSafety(site: PatchSite): boolean {
  if (site.model.kind === 'custom') {
    enforceCustomPatchSafety(site.patch)
    return false
  }
  if (site.model.kind === 'partitionWall') {
    geometryPureOrNull()?.syncPartitionWallModelState(site.model)
    return false
  }
  return enforcePatchSafety(site.model, site.faceId, site.face, site.patch)
}

/** The geometric fields a drag writes, by patch type. */
export type PatchGeometryValues = Partial<
  Pick<PatchRecord, 'x' | 'y' | 'w' | 'h' | 'cx' | 'cy' | 'r'>
>

export function readPatchGeometry(patch: PatchRecord): PatchGeometryValues {
  return patch.type === 'circle'
    ? { cx: patch.cx, cy: patch.cy, r: patch.r }
    : { x: patch.x, y: patch.y, w: patch.w, h: patch.h }
}

/** Write those fields onto a patch in place — the live half of a drag frame. */
export function writePatchGeometry(patch: PatchRecord, values: PatchGeometryValues): void {
  for (const [key, value] of Object.entries(values)) {
    if (typeof value === 'number' && Number.isFinite(value)) patch[key] = value
  }
}

/**
 * Commit a finished patch drag as ONE undo entry.
 *
 * Returns false when the patch has gone — a rebuild between the drag and the
 * release can only happen if the model was deleted, and writing into a stale
 * draft would resurrect it.
 */
export function commitPatchGeometry(
  uid: string,
  patchId: string,
  values: PatchGeometryValues,
  action = 'patchDrag',
): boolean {
  const project = store.getProject()
  if (!project) return false
  const found = findPatchOnModel(store.findConstruction(project, uid)?.model, patchId)
  if (!found) return false
  const summary = Object.entries(values)
    .map(([key, value]) => `${key}=${(value as number).toFixed(4)}`)
    .join(' ')
  store.edit(action, `${uid}/${patchId} ${summary}`, (draft) => {
    const target = findPatchOnModel(store.findConstruction(draft, uid)?.model, patchId)
    if (!target) return
    writePatchGeometry(target.patch, values)
    enforceSiteSafety(target)
  })
  return true
}

/**
 * Commit a finished model resize: the size AND the anchor, in one entry.
 *
 * Both, because shrinking from the left or bottom edge holds the far edge still
 * by moving the anchor — writing only the size would slide the model.
 */
export function commitModelBox(
  uid: string,
  size: Vec3,
  location: Vec3,
  action = 'wallEdgeResize',
): boolean {
  const project = store.getProject()
  if (!project || !store.findConstruction(project, uid)) return false
  if (![size.x, size.y, size.z, location.x, location.y, location.z].every(Number.isFinite)) {
    return false
  }
  store.edit(
    action,
    `${uid} size ${size.x.toFixed(4)}x${size.y.toFixed(4)}x${size.z.toFixed(4)}`,
    (draft) => {
      const model = store.findConstruction(draft, uid)?.model
      if (!model) return
      const geometry = (model.geometry ?? (model.geometry = {})) as Record<string, unknown>
      geometry.size = { x: size.x, y: size.y, z: size.z }
      geometry.location = { x: location.x, y: location.y, z: location.z }
      // A partition wall's hole store is derived from its box, so a resize that
      // skipped this would leave the openings where the old wall was.
      if (model.kind === 'partitionWall') geometryPureOrNull()?.syncPartitionWallModelState(model)
    },
  )
  return true
}

export interface CornerOffset {
  x: number
  y: number
}

/** Read a model's or a patch's corner offsets (four {x, y}, zeros when unset). */
export function readCornerOffsets(holder: Record<string, unknown> | undefined): CornerOffset[] {
  const raw = holder?.cornerOffsets
  const out: CornerOffset[] = []
  for (let i = 0; i < 4; i++) {
    const o = Array.isArray(raw) ? (raw[i] as Partial<CornerOffset> | undefined) : undefined
    out.push({ x: Number(o?.x) || 0, y: Number(o?.y) || 0 })
  }
  return out
}

export function writeCornerOffsets(
  holder: Record<string, unknown>,
  offsets: CornerOffset[] | null,
): void {
  const zero = !offsets || offsets.every((o) => Math.abs(o.x) < 1e-9 && Math.abs(o.y) < 1e-9)
  if (zero) delete holder.cornerOffsets
  else holder.cornerOffsets = offsets.map((o) => ({ x: o.x, y: o.y }))
}

/**
 * Commit dragged corners: a sheet's (`geometry.cornerOffsets`) or a patch's
 * (`patch.cornerOffsets`). One undo entry per drag, like the other commits.
 */
export function commitCornerOffsets(
  uid: string,
  patchId: string | null,
  offsets: CornerOffset[],
): boolean {
  const project = store.getProject()
  if (!project || !store.findConstruction(project, uid)) return false
  if (!offsets.every((o) => Number.isFinite(o.x) && Number.isFinite(o.y))) return false
  store.edit('cornerDrag', `${uid}${patchId ? ' ' + patchId : ''} corners`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model) return
    if (patchId) {
      const site = findPatchOnModel(model, patchId)
      if (site) writeCornerOffsets(site.patch as unknown as Record<string, unknown>, offsets)
      return
    }
    const geometry = (model.geometry ?? (model.geometry = {})) as Record<string, unknown>
    writeCornerOffsets(geometry, offsets)
  })
  return true
}

/** `v` turned by the model's rotation quaternion (identity when it has none). */
function rotateByModel(model: Construction): (v: Vec3) => Vec3 {
  const q = (model.geometry as { quaternion?: { x?: number; y?: number; z?: number; w?: number } } | undefined)
    ?.quaternion
  const qx = Number(q?.x) || 0
  const qy = Number(q?.y) || 0
  const qz = Number(q?.z) || 0
  const qw = q && Number.isFinite(Number(q.w)) ? Number(q.w) : 1
  return (v) => {
    // v + 2w(q x v) + 2 q x (q x v)
    const tx = 2 * (qy * v.z - qz * v.y)
    const ty = 2 * (qz * v.x - qx * v.z)
    const tz = 2 * (qx * v.y - qy * v.x)
    return {
      x: v.x + qw * tx + (qy * tz - qz * ty),
      y: v.y + qw * ty + (qz * tx - qx * tz),
      z: v.z + qw * tz + (qx * ty - qy * tx),
    }
  }
}

/**
 * Commit a 2D-designed floor's new outline (a vertex or edge drag), then
 * re-base it to its own bounding box so `size` stays that box and nothing
 * moves in the world. One undo entry per drag.
 */
export function commitSheetPolygon(uid: string, polygon: number[][]): boolean {
  const project = store.getProject()
  if (!project || !store.findConstruction(project, uid)) return false
  if (polygon.length < 3 || !polygon.every((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))) {
    return false
  }
  store.edit('polygonDrag', `${uid} outline ${polygon.length} vertices`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model) return
    const geometry = (model.geometry ?? (model.geometry = {})) as Record<string, unknown>
    geometry.polygon = polygon.map((p) => [p[0]!, p[1]!])
    geometryPureOrNull()?.rebasePolygonSheet(model, rotateByModel(model))
  })
  return true
}

/**
 * Create a model for a drag-to-place gesture, and put it in the project.
 *
 * The move tool is switched on the way the legacy does it
 * (`updateDragCreate`, STL_generate.html:17967): the gesture that just placed
 * the model is a move, so the arrows to keep moving it should already be there.
 *
 * One `addModel` — one undo entry — and the position that follows is written
 * live on top of it, so undoing a drag-create removes the model rather than
 * leaving it at the origin.
 */
export function beginDragCreatedModel(
  project: CfdProject,
  kind: string,
): { uid: string; model: Construction } | null {
  const created = createModel(project, kind)
  ;(created.model as unknown as { moveTool?: { enabled: boolean } }).moveTool = {
    ...((created.model as unknown as { moveTool?: { enabled: boolean } }).moveTool ?? {
      enabled: false,
    }),
    enabled: true,
  }
  const uid = store.addModel(created.bucket, created.model)
  return { uid, model: created.model }
}

/**
 * Take back a drag-create that cannot fit inside the project bounds.
 *
 * `undo()` rather than `removeModel()`: the create is the entry on top of the
 * stack, so undoing it leaves the project exactly as it was AND leaves no undo
 * entries behind. Removing instead would cost the user two presses of Ctrl+Z to
 * get back to where they already are. This is the legacy's
 * `removeModelByUid` + `popLastUndoCheckpoint` pair (STL_generate.html:18016).
 */
export function cancelDragCreatedModel() {
  store.undo()
}
