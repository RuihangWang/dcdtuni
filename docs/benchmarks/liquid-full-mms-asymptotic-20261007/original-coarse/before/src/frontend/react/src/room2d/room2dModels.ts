/**
 * The models as the floor plan sees them: a footprint, a yaw and two tool flags.
 *
 * Ported from the model half of `room_2d_editor.js` — `_drawModelProjections`
 * (:857), the model hit test (:1424), and `_getRotateRingParams` (:59). The
 * plan only ever needs six numbers per model, so the whole editor works against
 * this flattened `PlanModel` rather than reaching into the .cfdproj everywhere;
 * that is what makes the hit tests and the snapping unit-testable.
 *
 * Three things here are load-bearing:
 *
 * - **`w` must be null-checked, never `q.w || 1`.** A 180° yaw has w === 0, and
 *   the falsy fallback turns it into (0, 1, 0, 1) — normalised, a 90° turn. The
 *   legacy carries this warning twice for the same reason.
 * - **The ORDER of `planModels` is the shared module's bucket order**, because
 *   the hit test takes the FIRST model under the cursor and a different order
 *   picks a different rack out of a stack.
 * - **Snapping reads the RAW anchor+size box, not the rotated one** (legacy
 *   `_applySnap`, :1214). A rotated rack snaps by its unrotated corners there,
 *   and "fixing" that would move racks differently in the two apps.
 */

import type { CfdProject, Construction, Quat, Vec3 } from '../schema/cfdproj'
import { stateV4 } from '../legacy/cfdprojModules'
import type { Point, Vertex2 } from './room2dView'

/** The kinds the plan lets you drag. Legacy `_DRAGGABLE_KINDS` (:36). */
export const DRAGGABLE_KINDS = new Set(['rack', 'acu', 'box'])

export interface PlanModel {
  uid: string
  kind: string
  name: string
  enabled: boolean
  isRoomBoundary: boolean
  location: Vec3
  size: Vec3
  quaternion: Quat
  scale: Vec3
  /** rotateTool.enabled && rotateTool.axes.yaw — the ring is drawn only then. */
  rotatable: boolean
  /** moveTool.enabled — a drag of an already-selected model needs it. */
  movable: boolean
  /** faces.front.displayName === 'inlet'; decides which way the airflow arrow points. */
  frontIsInlet: boolean
}

const IDENTITY: Quat = { x: 0, y: 0, z: 0, w: 1 }
const UNIT: Vec3 = { x: 1, y: 1, z: 1 }

function quatOf(geometry: Construction['geometry']): Quat {
  const q = geometry?.quaternion
  if (!q) return IDENTITY
  // Explicit null-check on w: `q.w || 1` mistakes a 180° turn for a 90° one.
  const w = q.w === undefined || q.w === null ? 1 : q.w
  return { x: q.x || 0, y: q.y || 0, z: q.z || 0, w }
}

function vecOf(v: Vec3 | undefined, fallback: Vec3): Vec3 {
  if (!v) return fallback
  return {
    x: Number.isFinite(v.x) ? v.x : fallback.x,
    y: Number.isFinite(v.y) ? v.y : fallback.y,
    z: Number.isFinite(v.z) ? v.z : fallback.z,
  }
}

/** Flatten one construction. Exported for tests, which build models by hand. */
export function toPlanModel(model: Construction): PlanModel {
  const geometry = model.geometry
  const rotateTool = (model as { rotateTool?: { enabled?: boolean; axes?: { yaw?: boolean } } })
    .rotateTool
  const moveTool = (model as { moveTool?: { enabled?: boolean } }).moveTool
  const faces = geometry?.faces as
    | { front?: { displayName?: string } | undefined }
    | undefined
  const front = faces?.front
  return {
    uid: model.uid,
    kind: (model as { kind?: string }).kind ?? '',
    name: model.meta?.name ?? '',
    enabled: model.meta?.enabled !== false,
    isRoomBoundary: !!model.isRoomBoundary,
    location: vecOf(geometry?.location, { x: 0, y: 0, z: 0 }),
    size: vecOf(geometry?.size, { x: 0, y: 0, z: 0 }),
    quaternion: quatOf(geometry),
    scale: vecOf(geometry?.scale, UNIT),
    // `axes.yaw !== false` matches `cloneModelRotateTool` (STL_generate.html:2609):
    // the default rotate tool has yaw on, and only an explicit false turns it off.
    rotatable: !!rotateTool?.enabled && rotateTool.axes?.yaw !== false,
    movable: !!moveTool?.enabled,
    frontIsInlet: (front?.displayName ?? 'inlet') === 'inlet',
  }
}

/**
 * Every model, in the shared module's bucket order.
 *
 * `cfdproj.allModels` exists too but returns the order the TREE wants; the plan
 * has to use this one so a click lands on the same model in both apps.
 */
export function planModels(project: CfdProject | null): PlanModel[] {
  if (!project?.constructions) return []
  const list = stateV4().allModels(project) as Construction[]
  return list.filter((m) => m && typeof m === 'object' && m.uid).map(toPlanModel)
}

/** The models the plan draws: enabled, and not part of the room shell. */
export function drawableModels(models: PlanModel[]): PlanModel[] {
  return models.filter((m) => m.enabled && !m.isRoomBoundary)
}

/** True once any component of the vector part is non-zero. Legacy `hasModelRotation`. */
export function hasRotation(q: Quat): boolean {
  return Math.abs(q.x) > 1e-6 || Math.abs(q.y) > 1e-6 || Math.abs(q.z) > 1e-6
}

/**
 * Rotate a vector by a quaternion — `THREE.Vector3.applyQuaternion`, written out.
 *
 * Written out rather than imported so the geometry here stays testable without
 * a WebGL-shaped dependency; a test asserts it agrees with three.js to 1e-12.
 * The quaternion is normalised first, as three.js's own `Quaternion.normalize`
 * does in the legacy call sites — an unnormalised quaternion would scale the
 * footprint as well as turn it.
 */
export function applyQuat(v: Vec3, q: Quat): Vec3 {
  const n = Math.hypot(q.x, q.y, q.z, q.w) || 1
  const qx = q.x / n
  const qy = q.y / n
  const qz = q.z / n
  const qw = q.w / n
  const ix = qw * v.x + qy * v.z - qz * v.y
  const iy = qw * v.y + qz * v.x - qx * v.z
  const iz = qw * v.z + qx * v.y - qy * v.x
  const iw = -qx * v.x - qy * v.y - qz * v.z
  return {
    x: ix * qw + iw * -qx + iy * -qz - iz * -qy,
    y: iy * qw + iw * -qy + iz * -qx - ix * -qz,
    z: iz * qw + iw * -qz + ix * -qy - iy * -qx,
  }
}

/** The yaw a quaternion carries, as the plan reads it: 2*atan2(y, w). */
export function modelYaw(q: Quat): number {
  return 2 * Math.atan2(q.y || 0, q.w === undefined || q.w === null ? 1 : q.w)
}

/**
 * The four plan corners of a model's footprint, in world (x, z).
 *
 * Only the BOTTOM face is projected. The legacy used all eight OBB corners and
 * an axis-aligned box around them, and the drawn rectangle then pulsed and
 * squashed as the model turned (:872).
 */
export function bottomCorners(m: PlanModel): Vertex2[] {
  const w = m.size.x * (m.scale.x || 1)
  const d = m.size.z * (m.scale.z || 1)
  const local: Vec3[] = [
    { x: 0, y: 0, z: 0 },
    { x: w, y: 0, z: 0 },
    { x: w, y: 0, z: d },
    { x: 0, y: 0, z: d },
  ]
  return local.map((lc) => {
    const wc = applyQuat(lc, m.quaternion)
    return [m.location.x + wc.x, m.location.z + wc.z] as Vertex2
  })
}

/** The plan footprint of a model: its rotated quad, or its plain anchor box. */
export function planFootprint(m: PlanModel): Vertex2[] {
  if (hasRotation(m.quaternion)) return bottomCorners(m)
  const { x, z } = m.location
  return [
    [x, z],
    [x + m.size.x, z],
    [x + m.size.x, z + m.size.z],
    [x, z + m.size.z],
  ]
}

export interface PlanBox {
  minX: number
  maxX: number
  minY: number
  maxY: number
}

/** The plan-space bounding box of a footprint. */
export function boundsOf(points: Vertex2[]): PlanBox {
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (const [x, y] of points) {
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  return { minX, maxX, minY, maxY }
}

/** The OBB centre, in plan coordinates. Matches `transformTools.getModelOBB().center`. */
export function obbCentre(m: PlanModel): Point {
  const half: Vec3 = {
    x: (m.size.x / 2) * (m.scale.x || 1),
    y: (m.size.y / 2) * (m.scale.y || 1),
    z: (m.size.z / 2) * (m.scale.z || 1),
  }
  const c = applyQuat({ x: half.x, y: half.y, z: half.z }, m.quaternion)
  return { x: m.location.x + c.x, y: m.location.z + c.z }
}

export type ToCanvas = (wx: number, wy: number) => Point

export interface RotateRing {
  centre: Point
  radius: number
  angle: number
  /** The handle dot, in canvas pixels. */
  hx: number
  hy: number
  /** The OBB centre in PLAN coordinates — what the rotation turns about. */
  pivot: Point
}

/**
 * The rotate ring for a model, in canvas pixels.
 *
 * One function for both drawing and hit-testing, exactly as the legacy insists
 * (:57): computing the handle twice is how a ring ends up drawn where it cannot
 * be grabbed.
 *
 * The handle is placed with `+sin`, not `-sin`. `worldToCanvas` flips plan Y, so
 * a model that turns counter-clockwise in the world turns CLOCKWISE on screen;
 * the earlier `-sin` sent the dot the opposite way to the rack under it.
 */
export function rotateRing(m: PlanModel, toCanvas: ToCanvas): RotateRing | null {
  if (!m.rotatable) return null
  const pivot = obbCentre(m)
  const halfX = Math.abs((m.size.x / 2) * (m.scale.x || 1))
  const halfZ = Math.abs((m.size.z / 2) * (m.scale.z || 1))
  const centre = toCanvas(pivot.x, pivot.y)
  const tl = toCanvas(pivot.x - halfX, pivot.y + halfZ)
  const br = toCanvas(pivot.x + halfX, pivot.y - halfZ)
  const diagPx = Math.hypot(br.x - tl.x, br.y - tl.y)
  const radius = Math.max(25, diagPx * 0.6)
  const angle = modelYaw(m.quaternion)
  return {
    centre,
    radius,
    angle,
    hx: centre.x + radius * Math.cos(angle),
    hy: centre.y + radius * Math.sin(angle),
    pivot,
  }
}

/** Ring hit tolerances, in pixels — the legacy's, split so the tests can name them. */
export const RING_GRAB_PX = 10
export const RING_HOVER_PX = 12
export const HANDLE_GRAB_PX = 14

/** The first model whose ring or handle is under the cursor. */
export function hitRotateRing(
  models: PlanModel[],
  cx: number,
  cy: number,
  toCanvas: ToCanvas,
  ringTolerance = RING_GRAB_PX,
): { model: PlanModel; ring: RotateRing } | null {
  for (const m of models) {
    const ring = rotateRing(m, toCanvas)
    if (!ring) continue
    const toCentre = Math.hypot(cx - ring.centre.x, cy - ring.centre.y)
    const toRing = Math.abs(toCentre - ring.radius)
    const toHandle = Math.hypot(cx - ring.hx, cy - ring.hy)
    if (toRing < ringTolerance || toHandle < HANDLE_GRAB_PX) return { model: m, ring }
  }
  return null
}

/**
 * The first draggable model under the cursor.
 *
 * The test is against the SCREEN-ALIGNED box of the footprint, with 5 px of
 * slack for unrotated models — the legacy's, and the slack is what makes a
 * thin ACU clickable at all.
 */
export function hitModel(
  models: PlanModel[],
  cx: number,
  cy: number,
  toCanvas: ToCanvas,
): PlanModel | null {
  for (const m of models) {
    if (!m.enabled || m.isRoomBoundary || !DRAGGABLE_KINDS.has(m.kind)) continue
    const rotated = hasRotation(m.quaternion)
    const b = boundsOf(planFootprint(m))
    const tl = toCanvas(b.minX, b.maxY)
    const br = toCanvas(b.maxX, b.minY)
    const pad = rotated ? 0 : 5
    const x1 = Math.min(tl.x, br.x) - pad
    const x2 = Math.max(tl.x, br.x) + pad
    const y1 = Math.min(tl.y, br.y) - pad
    const y2 = Math.max(tl.y, br.y) + pad
    if (cx >= x1 && cx <= x2 && cy >= y1 && cy <= y2) return m
  }
  return null
}

/** Every plan point a zoom-to-fit should frame when there is no footprint yet. */
export function modelExtentPoints(models: PlanModel[]): Vertex2[] {
  const pts: Vertex2[] = []
  for (const m of models) {
    if (!m.enabled) continue
    pts.push([m.location.x, m.location.z])
    pts.push([m.location.x + (m.size.x || 0), m.location.z + (m.size.z || 0)])
  }
  return pts
}
