import type { CfdProject, Construction } from '../schema/cfdproj'
import { createModel } from '../legacy/factories'

/**
 * Room generation from a 2D footprint — a direct port of `_generateRoomModels`
 * (web/room_2d_editor.js:259).
 *
 * A closed polygon of n vertices plus a height becomes n walls, a floor and a
 * ceiling, all tagged `isRoomBoundary` so regenerating replaces them wholesale.
 *
 * Coordinates: the plan is (plan_x, plan_depth); in the Y-up world since
 * 2026-04-18 that maps plan_x -> world X, plan_depth -> world Z, height -> world Y.
 *
 * ── ON NORMALS, which is the part that has to be right ──────────────────────
 *
 * `needsNormalFlip` comes from the polygon's SIGNED AREA: a clockwise ring
 * produces inward-facing front normals and needs reversing; counter-clockwise
 * does not. That flag lands on `faces.front.reverseSolidNormal`, and the
 * frontend STL exporter applies it in `applyModelSolidOrientationOverrides`
 * (STL_generate.html:21869) as a text post-pass — it negates each `facet normal`
 * and swaps vertices 2 and 3.
 *
 * BUT for anything that gets meshed, that flag is not what decides the answer.
 * `STLGenRunner._ensure_watertight_room_enclosure()` rewrites `room_wall.stl`
 * and `merged_floor_ceiling.stl` on EVERY mesh run, computing wall normals in
 * Python as `(dz/len, 0, -dx/len)` straight from `room.plane`'s winding, with
 * no `reverseSolidNormal` input at all. Evidence in this repo:
 * case/Base_leakage carries `reverseSolidNormal: true` on all four room walls
 * and on Room_Floor, yet the STLs written later show the UN-reversed normals.
 *
 * So the flag is written here for parity with the HTML editor and for any
 * consumer of the raw project, and the room the SOLVER sees is decided by the
 * winding of `geometry.plane`. Both point the same way for a CCW plane, which
 * is why this has not bitten anyone.
 */

export type Vertex2 = [number, number]

/** Twice the signed area. Positive = counter-clockwise in Y-up plan coords. */
export function signedArea2(vertices: Vertex2[]): number {
  let acc = 0
  for (let i = 0; i < vertices.length; i += 1) {
    const a = vertices[i]!
    const b = vertices[(i + 1) % vertices.length]!
    acc += a[0] * b[1] - b[0] * a[1]
  }
  return acc
}

export function polygonArea(vertices: Vertex2[]): number {
  return Math.abs(signedArea2(vertices)) / 2
}

export interface GeneratedRoom {
  walls: { bucket: string; model: Construction }[]
  floor: { bucket: string; model: Construction }
  ceiling: { bucket: string; model: Construction }
  /** uids of the room-boundary models this replaces. */
  replaces: string[]
}

/** Every model currently tagged as part of the room shell. */
export function roomBoundaryUids(project: CfdProject | null): string[] {
  if (!project) return []
  const out: string[] = []
  for (const items of Object.values(project.constructions ?? {})) {
    if (!items) continue
    for (const m of Object.values(items)) {
      if (m && (m as { isRoomBoundary?: unknown }).isRoomBoundary) out.push(m.uid)
    }
  }
  return out
}

/**
 * Build the wall / floor / ceiling models for a footprint.
 *
 * Nothing is written to the project here — the caller decides when, so the
 * whole regeneration lands as ONE undo checkpoint instead of 2n+2.
 */
export function generateRoom(
  project: CfdProject,
  vertices: Vertex2[],
  height: number,
): GeneratedRoom {
  const n = vertices.length
  const needsNormalFlip = signedArea2(vertices) < 0 // clockwise ring

  // The factories derive names and uids from what the project already holds, so
  // a working copy accumulates the models as they are made; otherwise every wall
  // would be handed the same uid.
  const draft: CfdProject = {
    ...project,
    constructions: Object.fromEntries(
      Object.entries(project.constructions ?? {}).map(([k, v]) => [k, { ...(v ?? {}) }]),
    ),
  }
  const remember = (bucket: string, model: Construction) => {
    const items = draft.constructions![bucket] ?? (draft.constructions![bucket] = {})
    items[model.uid] = model
  }

  const walls: { bucket: string; model: Construction }[] = []
  for (let i = 0; i < n; i += 1) {
    const a = vertices[i]!
    const b = vertices[(i + 1) % n]!
    const dx = b[0] - a[0]
    const dy = b[1] - a[1]
    const length = Math.hypot(dx, dy)
    // A degenerate edge would produce a zero-area wall and a NaN yaw. Skipping
    // it leaves a gap in the Room_Wall_<n> numbering, exactly as the HTML editor
    // does — the name follows the VERTEX index, not the count of walls emitted.
    if (length < 1e-6) continue
    const angle = Math.atan2(dy, dx)

    const { bucket, model } = createModel(draft, 'wall')
    model.meta = { ...model.meta, name: `Room_Wall_${i + 1}` }
    ;(model as { isRoomBoundary?: boolean }).isRoomBoundary = true
    model.geometry = model.geometry ?? {}
    model.geometry.location = { x: a[0], y: 0, z: a[1] }
    model.geometry.size = { x: length, y: height, z: 0 }
    // Negative Y component: a positive plan angle (CCW from +X toward +Y in
    // plan) must map local +X to world (cos, 0, +sin). A right-hand +Y rotation
    // sends +X -> (cos, 0, -sin), so the rotation is about -Y.
    model.geometry.quaternion = { x: 0, y: -Math.sin(angle / 2), z: 0, w: Math.cos(angle / 2) }
    model.geometry.scale = { x: 1, y: 1, z: 1 }
    if (needsNormalFlip) {
      const faces = model.geometry.faces as Record<string, { reverseSolidNormal?: boolean }> | undefined
      if (faces?.front) faces.front.reverseSolidNormal = true
    }
    remember(bucket, model)
    walls.push({ bucket, model })
  }

  // Floor polygon in LOCAL coordinates. The top-face layout has up = (0,0,-1),
  // so face-local y grows toward world -Z; plan vertex (px, py) therefore has to
  // land at face-local y = (polyMaxY - py) to end up at world (px, 0, py).
  const xs = vertices.map((v) => v[0])
  const ys = vertices.map((v) => v[1])
  const polyMinX = Math.min(...xs)
  const polyMinY = Math.min(...ys)
  const polyMaxY = Math.max(...ys)
  const localPoly: Vertex2[] = vertices.map((v) => [v[0] - polyMinX, polyMaxY - v[1]])

  const floor = makeSlab(draft, 'Room_Floor', 'bottom', { x: polyMinX, y: 0, z: polyMinY }, localPoly, true)
  remember(floor.bucket, floor.model)
  const ceiling = makeSlab(draft, 'Room_Ceiling', 'top', { x: polyMinX, y: height, z: polyMinY }, localPoly, false)
  remember(ceiling.bucket, ceiling.model)

  return { walls, floor, ceiling, replaces: roomBoundaryUids(project) }
}

function makeSlab(
  draft: CfdProject,
  name: string,
  where: 'bottom' | 'top',
  location: { x: number; y: number; z: number },
  polygon: Vertex2[],
  reverseTop: boolean,
): { bucket: string; model: Construction } {
  const { bucket, model } = createModel(draft, 'floor')
  model.meta = { ...model.meta, name }
  ;(model as { isRoomBoundary?: boolean }).isRoomBoundary = true

  const cooling = ((model as { cooling?: Record<string, unknown> }).cooling ??=
    {} as Record<string, unknown>)
  cooling.roomBoundaryLocation = where
  // 'wall', not 'wallBaffle': isWallFloorOrientationEligible gates the normal
  // flip on this being exactly 'wall', so anything else makes
  // reverseSolidNormal inert on a floor.
  cooling.defaultBoundaryType = 'wall'

  model.geometry = model.geometry ?? {}
  model.geometry.location = location
  ;(model.geometry as { polygon?: Vertex2[] }).polygon = polygon

  if (reverseTop) {
    // The ground floor sits at y=0 and its normal must point -Y, away from the
    // room. The 'top' face layout has normal +Y, so it is reversed.
    const faces = model.geometry.faces as Record<string, { reverseSolidNormal?: boolean }> | undefined
    if (faces?.top) faces.top.reverseSolidNormal = true
  }
  syncFloorBaseFromPolygon(model)
  return { bucket, model }
}

/** Floor size follows its polygon's bounding box. size.y stays 0 — it is a sheet. */
export function syncFloorBaseFromPolygon(model: Construction): void {
  const polygon = (model.geometry as { polygon?: Vertex2[] } | undefined)?.polygon
  if (!polygon || polygon.length < 3) return
  const xs = polygon.map((p) => p[0])
  const ys = polygon.map((p) => p[1])
  const size = model.geometry!.size ?? (model.geometry!.size = { x: 0, y: 0, z: 0 })
  size.x = Math.max(...xs) - Math.min(...xs)
  size.z = Math.max(...ys) - Math.min(...ys)
}

/** A rectangle, counter-clockwise, which is the winding that needs no flip. */
export function rectangleFootprint(width: number, depth: number): Vertex2[] {
  return [
    [0, 0],
    [width, 0],
    [width, depth],
    [0, depth],
  ]
}
