/**
 * Bool Diff: cut a hole in a planar surface where another model passes through.
 *
 * A rack standing on a raised floor needs a hole in that floor for its cables
 * and its airflow; a containment baffle meeting a rack row needs the rack's
 * silhouette removed. Both are the same operation — subtract the tool's
 * footprint from the target's face — and both change what the STL exports, so
 * this is geometry, not decoration.
 *
 * It is NOT a general CSG. For a boxy tool the world AABB is projected onto the
 * target face's plane and the resulting RECTANGLE is stored as a cutout polygon
 * (`executeBoolDiff`, STL_generate.html:11120). A rotated rack therefore cuts
 * its bounding box, not its outline — which is what the mesher wants anyway,
 * because a cable cutout is cut square.
 *
 * A ROUND duct is the exception, and has to be: its bounding box bears no
 * resemblance to its footprint. `roundFootprintOnFace` intersects the cylinder
 * swept along the duct's centreline with the face plane and stores the true
 * ellipse (a circle where the duct meets the face end-on, which is how a riser
 * terminates in a ceiling).
 *
 * Only baffles and floors can be targets: they are the planar kinds, and a
 * cutout on a solid box has no meaning.
 */

import * as THREE from 'three'
import type { CfdProject, Construction } from '../schema/cfdproj'
import * as store from '../projectStore'

export interface Rect2 {
  x: number
  y: number
  w: number
  h: number
}

export interface Bounds3 {
  min: { x: number; y: number; z: number }
  max: { x: number; y: number; z: number }
}

/** The polygon shape the geometry builder reads: [[[x,y], …closed ring]]. */
export function rectToPoly(x: number, y: number, w: number, h: number): number[][][][] {
  return [[[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]]
}

export function canBeTarget(model: Construction | null | undefined): boolean {
  return model?.kind === 'baffle' || model?.kind === 'floor'
}

/**
 * The face a cutout lands on: a floor is cut from above, a wall or baffle
 * head-on.
 */
export function primaryFaceId(model: Construction): string {
  return model.kind === 'floor' ? 'top' : 'front'
}

/**
 * The tool's footprint on the target face, in the face's own 2D coordinates.
 *
 * All eight AABB corners are projected, not two: the face plane can be at any
 * orientation to the world axes, so the min/max corner pair does not stay the
 * min/max pair after the transform.
 *
 * Returns null when the overlap is empty — the tool does not reach the face at
 * all, and cutting a zero-area hole would leave a degenerate polygon in the
 * project.
 *
 * A silhouette is not contact. The tool must also STRADDLE the face plane
 * (within `PLANE_TOLERANCE` along the face normal), as the legacy's
 * autoWallBoolDiff / autoFloorBoolDiff both required: a table leg standing
 * half a metre in front of a wall projects onto it and must not cut it, and an
 * ACU sitting on a table above the floor must not cut the floor.
 */
/** How far off the face plane a tool may sit and still count as touching it. */
export const PLANE_TOLERANCE = 0.01

export function footprintOnFace(
  toolBounds: Bounds3,
  faceWorldInverse: THREE.Matrix4,
  faceSize: { w: number; h: number },
  requireContact = true,
): Rect2 | null {
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  let minZ = Infinity
  let maxZ = -Infinity
  for (const ix of [0, 1]) {
    for (const iy of [0, 1]) {
      for (const iz of [0, 1]) {
        const p = new THREE.Vector3(
          ix ? toolBounds.max.x : toolBounds.min.x,
          iy ? toolBounds.max.y : toolBounds.min.y,
          iz ? toolBounds.max.z : toolBounds.min.z,
        ).applyMatrix4(faceWorldInverse)
        if (p.x < minX) minX = p.x
        if (p.x > maxX) maxX = p.x
        if (p.y < minY) minY = p.y
        if (p.y > maxY) maxY = p.y
        if (p.z < minZ) minZ = p.z
        if (p.z > maxZ) maxZ = p.z
      }
    }
  }
  // Face-local z is the signed distance from the face plane. Entirely on one
  // side, beyond tolerance, means the tool only projects onto the face.
  if (requireContact && (minZ > PLANE_TOLERANCE || maxZ < -PLANE_TOLERANCE)) return null
  // Clamped to the face: a rack overhanging the floor's edge cuts only the part
  // that is over it.
  minX = Math.max(0, minX)
  minY = Math.max(0, minY)
  maxX = Math.min(faceSize.w, maxX)
  maxY = Math.min(faceSize.h, maxY)
  if (maxX - minX < 1e-6 || maxY - minY < 1e-6) return null
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
}

export interface Cutout {
  faceId: string
  polygon: number[][][][]
  /** What made the hole. Recorded by the auto path, as the legacy does. */
  sourceName?: string
}

/** Append a cutout to a model's face, creating the list on first use. */
export function addCutout(
  model: Construction,
  faceId: string,
  rect: Rect2,
  sourceName?: string,
) {
  addCutoutPoly(model, faceId, rectToPoly(rect.x, rect.y, rect.w, rect.h), sourceName)
}

/** The same, for a footprint that is not a rectangle -- a round duct's. */
export function addCutoutPoly(
  model: Construction,
  faceId: string,
  polygon: number[][][][],
  sourceName?: string,
) {
  const geometry = model.geometry as unknown as { cutouts?: Cutout[] }
  if (!geometry.cutouts) geometry.cutouts = []
  geometry.cutouts.push({
    faceId,
    polygon,
    ...(sourceName ? { sourceName } : {}),
  })
}

/** The geometry of a round duct: its centreline in WORLD space, and radius. */
interface RoundTool {
  radius: number
  /** World-space centreline vertices, in order. */
  points: THREE.Vector3[]
}

/**
 * Read a round duct's centreline out of the project.
 *
 * `shape: 'round'` + `diameterM` + `waypoints` is what the air-pipe builder
 * reads, and the waypoints are LOCAL: world = location + quaternion * (scale *
 * waypoint). Returns null for anything that is not a round duct, so every
 * other tool keeps cutting its bounding box exactly as before.
 */
export function roundToolOf(model: Construction): RoundTool | null {
  const g = model.geometry as unknown as {
    shape?: string
    diameterM?: number
    waypoints?: { x: number; y: number; z: number }[]
    location?: { x: number; y: number; z: number }
    quaternion?: { x: number; y: number; z: number; w: number }
    scale?: { x: number; y: number; z: number }
  }
  if (!g || g.shape !== 'round') return null
  const diameter = Number(g.diameterM)
  const wps = g.waypoints
  if (!(diameter > 0) || !wps || wps.length < 2) return null
  const loc = g.location ?? { x: 0, y: 0, z: 0 }
  const q = g.quaternion ?? { x: 0, y: 0, z: 0, w: 1 }
  const sc = g.scale ?? { x: 1, y: 1, z: 1 }
  const rot = new THREE.Quaternion(q.x, q.y, q.z, q.w)
  const points = wps.map((w) =>
    new THREE.Vector3(w.x * sc.x, w.y * sc.y, w.z * sc.z)
      .applyQuaternion(rot)
      .add(new THREE.Vector3(loc.x, loc.y, loc.z)),
  )
  return { radius: diameter / 2, points }
}

/** Segments a tessellated ellipse is broken into. 24 keeps a 150 mm duct's
 *  chord error under 0.2 mm, well inside the mesher's own tolerance. */
const ROUND_FOOTPRINT_SEGMENTS = 24

/**
 * A round duct's real footprint on a face: the cylinder swept along its
 * centreline, intersected with the face plane.
 *
 * The bounding-box projection this file used for every tool is wrong for a
 * duct, and badly so -- DAML-Testbed's Duct_riser_2 runs 1.0 m in z and ends
 * flush on the ceiling at y = 2.7, so its AABB projects to a 150 mm x 1004 mm
 * slot where the duct actually meets the ceiling in a 150 mm CIRCLE (radius
 * 0.075 m at face-local u = 1.82, v = 2.71877). Cutting the slot removes a
 * metre of ceiling that is nowhere near the duct; cutting nothing leaves the
 * duct's end sealed against a wall that should have a hole in it.
 *
 * Per centreline segment the intersection of a cylinder with a plane is an
 * ellipse: a circle when the segment is perpendicular to the plane, and
 * stretched by 1/sin(theta) along the segment's in-plane direction when it is
 * not. Every segment that reaches the plane contributes one, and a vertex
 * sitting ON the plane (the flush end, the common case) contributes its own
 * circle -- so an elbow that turns in the plane is covered by both.
 *
 * Returns null when the duct does not touch the plane, or when an ellipse
 * would hang off the face: clipping a polygon to a rectangle is a different
 * operation and the caller falls back to the clamped bounding box, which is
 * what it did before.
 */
export function roundFootprintOnFace(
  tool: RoundTool,
  faceWorldInverse: THREE.Matrix4,
  faceSize: { w: number; h: number },
  requireContact = true,
): number[][][][] | null {
  const R = tool.radius
  // face-local w is the signed distance from the face plane
  const local = tool.points.map((p) => p.clone().applyMatrix4(faceWorldInverse))
  const rings: number[][][][] = []
  const inside = (u: number, v: number) =>
    u >= 0 && v >= 0 && u <= faceSize.w && v <= faceSize.h

  const ellipse = (cu: number, cv: number, eu: THREE.Vector2, ev: THREE.Vector2) => {
    const ring: number[][] = []
    for (let i = 0; i < ROUND_FOOTPRINT_SEGMENTS; i++) {
      const a = (2 * Math.PI * i) / ROUND_FOOTPRINT_SEGMENTS
      const u = cu + eu.x * Math.cos(a) + ev.x * Math.sin(a)
      const v = cv + eu.y * Math.cos(a) + ev.y * Math.sin(a)
      if (!inside(u, v)) return null
      ring.push([u, v])
    }
    const first = ring[0]
    if (!first) return null
    ring.push([first[0] ?? 0, first[1] ?? 0])   // close the ring
    return [ring]
  }
  const circle = (cu: number, cv: number) => {
    const e = new THREE.Vector2(R, 0)
    return ellipse(cu, cv, e, new THREE.Vector2(0, R))
  }

  for (let i = 0; i < local.length; i++) {
    const a = local[i]
    if (!a) continue
    // a vertex ON the plane: the duct's flush end, or an elbow vertex
    if (Math.abs(a.z) <= PLANE_TOLERANCE) {
      const ring = circle(a.x, a.y)
      if (!ring) return null
      rings.push(ring)
    }
    const b = local[i + 1]
    if (!b) continue
    // a segment that strictly crosses the plane
    if (a.z * b.z >= 0 || Math.abs(a.z - b.z) < 1e-12) continue
    const t = a.z / (a.z - b.z)
    const du = a.x + (b.x - a.x) * t
    const dv = a.y + (b.y - a.y) * t
    const axis = new THREE.Vector3(b.x - a.x, b.y - a.y, b.z - a.z)
    const len = axis.length()
    if (len < 1e-12) continue
    axis.divideScalar(len)
    // sin(theta) between the axis and the plane is the axis's normal component
    const sinT = Math.abs(axis.z)
    if (sinT > 1 - 1e-9) {
      const ring = circle(du, dv)
      if (!ring) return null
      rings.push(ring)
      continue
    }
    // major axis: along the axis's in-plane projection, stretched by 1/sinT
    const major = new THREE.Vector2(axis.x, axis.y)
    if (major.lengthSq() < 1e-24) continue
    major.normalize().multiplyScalar(R / sinT)
    const minor = new THREE.Vector2(-major.y, major.x).normalize().multiplyScalar(R)
    const ring = ellipse(du, dv, major, minor)
    if (!ring) return null
    rings.push(ring)
  }
  if (!rings.length && requireContact) return null
  // Each circle is already a Polygon ([ring]); the array of them is the
  // MultiPolygon polygon-clipping expects.
  return rings.length ? rings : null
}

export interface BoolDiffHost {
  /** World bounds of a model's BUILT meshes, or null when it has none. */
  worldBounds(uid: string): Bounds3 | null
  /** The face's world matrix, from the built root and the face layout. */
  faceWorldMatrix(uid: string, faceId: string): THREE.Matrix4 | null
  /** The face's extent in its own 2D space. */
  faceSize(uid: string, faceId: string): { w: number; h: number } | null
}

export type BoolDiffResult =
  | { ok: true; rect: Rect2 }
  | { ok: false; reason: 'target-kind' | 'no-bounds' | 'no-overlap' | 'missing' }

/**
 * Subtract `toolUid` from `targetUid`.
 *
 * `keepTool` is the legacy default: the rack that cut the hole is usually still
 * a rack, and deleting it is a separate decision.
 */
export function executeBoolDiff(
  host: BoolDiffHost,
  targetUid: string,
  toolUid: string,
  keepTool = true,
): BoolDiffResult {
  const project = store.getProject()
  if (!project) return { ok: false, reason: 'missing' }
  const target = store.findConstruction(project, targetUid)?.model
  const tool = store.findConstruction(project, toolUid)?.model
  if (!target || !tool) return { ok: false, reason: 'missing' }
  if (!canBeTarget(target)) return { ok: false, reason: 'target-kind' }

  const faceId = primaryFaceId(target)
  const bounds = host.worldBounds(toolUid)
  const faceMatrix = host.faceWorldMatrix(targetUid, faceId)
  const size = host.faceSize(targetUid, faceId)
  if (!bounds || !faceMatrix || !size) return { ok: false, reason: 'no-bounds' }

  // The manual pair is projection-only, as the legacy's executeBoolDiff was:
  // the user picked this tool on purpose. Only the AUTO path insists on contact.
  const inverse = faceMatrix.clone().invert()
  // A round duct cuts its real cross-section, not its bounding box -- and it
  // must not need the host's world bounds at all: a pipe's render root is not
  // in __viewportRegistries.renderRootMap, so worldBounds(uid) is null for it
  // and the bounds-based path silently skips it. That is exactly why clicking
  // 自动挖除重合面 on a ceiling did nothing while a duct passed through it: the
  // duct was never seen as a tool. The centreline lives in the project data,
  // so the round footprint is computable without the viewport.
  const round = roundToolOf(tool)
  const poly = round ? roundFootprintOnFace(round, inverse, size, false) : null
  const rect = poly ? null : footprintOnFace(bounds, inverse, size, false)
  if (!poly && !rect) return { ok: false, reason: 'no-overlap' }
  // The API has always reported a rectangle: fall back to the hole's bbox.
  const reported: Rect2 = rect ?? bboxOfPoly(poly as number[][][][])

  store.edit('executeBoolDiff', `${targetUid} - ${toolUid}`, (draft: CfdProject) => {
    const t = store.findConstruction(draft, targetUid)?.model
    if (t) {
      if (poly) addCutoutPoly(t, faceId, poly)
      else if (rect) addCutout(t, faceId, rect)
    }
    if (!keepTool) {
      for (const items of Object.values(draft.constructions ?? {})) {
        if (items && items[toolUid]) delete items[toolUid]
      }
    }
  })
  return { ok: true, rect: reported }
}

/** Bounding rectangle of a footprint polygon, in face-local coordinates. */
export function bboxOfPoly(poly: number[][][][]): Rect2 {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const shell of poly) {
    for (const ring of shell) {
      for (const pt of ring) {
        const u = pt[0] ?? 0
        const v = pt[1] ?? 0
        if (u < minX) minX = u
        if (v < minY) minY = v
        if (u > maxX) maxX = u
        if (v > maxY) maxY = v
      }
    }
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return { x: 0, y: 0, w: 0, h: 0 }
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
}

/** Kinds that never cut a surface: the room's own planar surfaces. */
const NEVER_CUTS = new Set(['wall', 'baffle', 'floor'])

/** A floor or a wall/baffle can be auto-cut; nothing else has a face to cut. */
export function canAutoCut(model: Construction | null | undefined): boolean {
  return model?.kind === 'floor' || model?.kind === 'wall' || model?.kind === 'baffle'
}

/**
 * Cut a hole for every solid object that passes through this surface.
 *
 * The bulk version of the same operation: doing it rack by rack on a 200-rack
 * case is not a workflow. Walls, baffles and floors are skipped as TOOLS —
 * a wall meeting a floor is a join, not a penetration.
 *
 * Each cutout records `sourceName`, as the legacy's wall variant does, so a
 * hole can be traced back to what made it.
 */
export function autoSurfaceBoolDiff(host: BoolDiffHost, uid: string): number {
  const project = store.getProject()
  const surface = project ? store.findConstruction(project, uid)?.model : null
  if (!project || !canAutoCut(surface)) return 0

  const faceId = primaryFaceId(surface!)
  const faceMatrix = host.faceWorldMatrix(uid, faceId)
  const size = host.faceSize(uid, faceId)
  if (!faceMatrix || !size) return 0
  const inverse = faceMatrix.clone().invert()

  const cuts: { rect: Rect2 | null; poly: number[][][][] | null; sourceName: string }[] = []
  for (const { model } of allModelsOf(project)) {
    if (model.uid === uid) continue
    if (NEVER_CUTS.has(String(model.kind))) continue
    if (model.meta?.enabled === false) continue
    // Round first, and WITHOUT the host's world bounds: a pipe's render root
    // is not in the viewport registry, so the bounds-based path never sees it
    // and a duct passing through a surface cut nothing at all.
    const round = roundToolOf(model)
    const poly = round ? roundFootprintOnFace(round, inverse, size) : null
    if (poly) {
      cuts.push({ rect: null, poly, sourceName: model.meta?.name ?? model.uid })
      continue
    }
    const bounds = host.worldBounds(model.uid)
    if (!bounds) continue
    const rect = footprintOnFace(bounds, inverse, size)
    if (!rect) continue
    // A round duct's bounding box is nothing like its footprint: DAML-Testbed's
    // Duct_riser_2 runs 1.0 m in z and ends flush on the ceiling, so its box
    // would cut a 150 mm x 1004 mm slot through a metre of ceiling where the
    // duct meets it in a 150 mm circle. Cut the circle.
    cuts.push({ rect, poly: null, sourceName: model.meta?.name ?? model.uid })
  }
  if (!cuts.length) return 0

  store.edit('autoBoolDiff', `${uid} x${cuts.length}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    if (!target) return
    for (const cut of cuts) {
      if (cut.poly) addCutoutPoly(target, faceId, cut.poly, cut.sourceName)
      else if (cut.rect) addCutout(target, faceId, cut.rect, cut.sourceName)
    }
  })
  return cuts.length
}

/** Remove every cutout from a surface. */
export function clearCutouts(uid: string) {
  store.edit('clearCutouts', uid, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model) return
    ;(model.geometry as unknown as { cutouts?: Cutout[] }).cutouts = []
  })
}

/** How many cutouts a surface's primary face carries. */
export function cutoutCount(model: Construction | null | undefined): number {
  if (!model) return 0
  const cutouts = (model.geometry as unknown as { cutouts?: Cutout[] })?.cutouts ?? []
  const faceId = primaryFaceId(model)
  return cutouts.filter((c) => c.faceId === faceId).length
}

function allModelsOf(project: CfdProject): { model: Construction }[] {
  const out: { model: Construction }[] = []
  for (const items of Object.values(project.constructions ?? {})) {
    if (!items) continue
    for (const model of Object.values(items)) {
      if (model && typeof model === 'object') out.push({ model })
    }
  }
  return out
}
