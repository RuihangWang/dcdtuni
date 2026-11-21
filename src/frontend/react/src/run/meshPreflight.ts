/**
 * The checks that run before a mesh job is submitted.
 *
 * Every one of them catches a failure that otherwise surfaces minutes later,
 * inside an OpenFOAM log, as something that reads like a mesher bug:
 *
 * - A model name starting with a digit is not a legal OpenFOAM identifier, so
 *   the patch it names cannot be declared and the run dies in `snappyHexMesh`.
 * - A floor above `room.height` is outside the enclosure the walls build, so
 *   its solid is never closed and castellation leaks into the background mesh.
 * - A room wall that stops below `room.height` leaves the shell open under the
 *   ceiling: the fluid flows over it to the mesh box, and a dcCart mesh comes
 *   out with no room wall, floor or ceiling patch at all. That run SUCCEEDS.
 * - A seed inside a rack tells the mesher the fluid is where the solid is, and
 *   the region it keeps is the wrong one — the run SUCCEEDS and the answer is
 *   nonsense.
 * - Rack doors with different open-area percentages produce porous baffles the
 *   runner cannot fold into one porosity model.
 *
 * Ported from the preflight block of `runMeshGeneration`
 * (STL_generate.html:28090-28309) and its collectors: `collectFloorHeightErrors`
 * (:27911), `collectRoomWallGapErrors`, `collectSeedCollisionWarnings` (:27772)
 * and the door-porosity scan (:28275).
 *
 * All of it is pure — project in, findings out — because the alternative is a
 * check that can only be exercised by starting a real mesh job. The dialogs that
 * present these findings live in `meshPreflightUi.ts`.
 */

import { allModels, type CfdProject, type Construction, type Vec3 } from '../schema/cfdproj'
import { stlNaming } from '../legacy/stlNaming'
import { collectSeedSurfaces, modelToWorld, seedEndpoints, type SeedSurface } from '../geometry/seedConfig'

export function sanitizeName(text: string | null | undefined): string {
  try {
    return stlNaming().sanitizeName(text)
  } catch {
    // The shared module attaches to `window` on import; this fallback keeps the
    // collector usable from a context that never loaded it.
    return (text || 'model').replace(/[^a-zA-Z0-9_]+/g, '_')
  }
}

// ---------------------------------------------------------------------------
// 1. Names OpenFOAM cannot parse
// ---------------------------------------------------------------------------

export interface DigitNameModel {
  uid: string
  name: string
  /** The bucket, which is what the dialog shows as the model's type. */
  bucket: string
}

/** Models whose SANITISED name starts with a digit. */
export function collectDigitNameModels(project: CfdProject | null): DigitNameModel[] {
  if (!project) return []
  const out: DigitNameModel[] = []
  for (const { bucket, model } of allModels(project)) {
    const name = model.meta?.name ?? ''
    if (/^\d/.test(sanitizeName(name))) out.push({ uid: model.uid, name, bucket })
  }
  return out
}

// ---------------------------------------------------------------------------
// 2. Floors above the ceiling
// ---------------------------------------------------------------------------

export interface FloorHeightError {
  name: string
  y: number
  roomHeight: number
}

/**
 * The room height the .cfdrun will carry.
 *
 * Three sources in priority order, copied from `inferRoomHeightFromWalls`
 * (js/cfdrun_build.js:1497). Reading `geometry.height` alone would be wrong for
 * most projects in this repo: fifteen of the twenty-three carry a
 * `planeHeight` that differs from it, and eleven have a ceiling floor bound as
 * the room's top — Base_leakage's ceiling sits at 3.633 while `geometry.height`
 * still says 3.6, so the naive read would report a false conflict on a project
 * that meshes fine.
 */
export function inferRoomHeight(project: CfdProject | null): number {
  if (!project) return 0
  const tops: number[] = []
  for (const { bucket, model } of allModels(project)) {
    if (bucket !== 'floors') continue
    const cooling = model.cooling as { roomBoundaryLocation?: string } | undefined
    const y = (model.geometry?.location as Vec3 | undefined)?.y
    if (cooling?.roomBoundaryLocation === 'top' && Number.isFinite(y)) tops.push(Number(y))
  }
  if (tops.length) return Math.max(...tops)

  const geometry = project.geometry as { plane?: unknown; planeHeight?: number; height?: number } | undefined
  if (geometry?.plane) {
    const planeH = geometry.planeHeight != null ? geometry.planeHeight : geometry.height
    if (Number.isFinite(planeH) && Number(planeH) > 0) return Number(planeH)
  }
  return Number(geometry?.height) || 0
}

/** Floors whose Y anchor pokes through the room ceiling. */
export function collectFloorHeightErrors(project: CfdProject | null): FloorHeightError[] {
  if (!project) return []
  const roomHeight = inferRoomHeight(project)
  const errors: FloorHeightError[] = []
  for (const { bucket, model } of allModels(project)) {
    if (bucket !== 'floors') continue
    const y = Number((model.geometry?.location as Vec3 | undefined)?.y) || 0
    if (y > roomHeight) errors.push({ name: model.meta?.name ?? model.uid, y, roomHeight })
  }
  return errors
}

// ---------------------------------------------------------------------------
// 2b. Room walls that stop below the ceiling
// ---------------------------------------------------------------------------

export interface RoomWallGapError {
  uid: string
  /** Sanitised, as the legacy dialog lists it: the name the wall's patch has. */
  name: string
  top: number
  roomHeight: number
}

/** A shortfall under a millimetre is rounding, not an open shell. */
export const ROOM_WALL_GAP_TOLERANCE = 1e-3

interface BoxGeometry {
  location?: Partial<Vec3>
  size?: Partial<Vec3>
  scale?: Partial<Vec3>
  quaternion?: { x?: number; y?: number; z?: number; w?: number }
}

function finiteOr(value: unknown, fallback: number): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/**
 * The world-Y top of a box model: the eight corners of [0,W]x[0,H]x[0,D],
 * scaled, turned by the quaternion and moved to the location, as the scene
 * places it. For a wall turned only about Y this is `location.y + H * scale.y`.
 *
 * Its own arithmetic rather than `modelWorldBounds`, whose rotation reads the
 * quaternion's fields bare: a stored quaternion missing `w` would turn every
 * corner into NaN and the wall would pass unmeasured. Each missing component
 * defaults here, as in the legacy `_modelTopWorldY`.
 */
export function modelTopWorldY(geometry: BoxGeometry | null | undefined): number {
  const g = geometry ?? {}
  const q = g.quaternion ?? {}
  const scale = g.scale ?? {}
  const size = g.size ?? {}
  const qx = finiteOr(q.x, 0)
  const qy = finiteOr(q.y, 0)
  const qz = finiteOr(q.z, 0)
  const qw = finiteOr(q.w, 1)
  const ex = finiteOr(size.x, 0) * finiteOr(scale.x, 1)
  const ey = finiteOr(size.y, 0) * finiteOr(scale.y, 1)
  const ez = finiteOr(size.z, 0) * finiteOr(scale.z, 1)
  let top = -Infinity
  for (let c = 0; c < 8; c++) {
    const vx = c & 1 ? ex : 0
    const vy = c & 2 ? ey : 0
    const vz = c & 4 ? ez : 0
    // v' = v + w*t + q.xyz x t, with t = 2 (q.xyz x v); only y is needed.
    const tx = 2 * (qy * vz - qz * vy)
    const ty = 2 * (qz * vx - qx * vz)
    const tz = 2 * (qx * vy - qy * vx)
    top = Math.max(top, vy + qw * ty + (qz * tx - qx * tz))
  }
  return finiteOr(g.location?.y, 0) + top
}

/**
 * Room-boundary walls whose top is below the room height.
 *
 * Only walls with `isRoomBoundary === true`: an interior wall may stop short of
 * the ceiling. Disabled walls count, because `meta.enabled` only hides a wall
 * in the view and the exporter writes it anyway (Base604's four room walls are
 * all disabled).
 *
 * The room height is `inferRoomHeight`, the one the floor check uses. The
 * legacy reads `meshGeometry.room.height` from the built manifest; the two agree
 * on every project in `case/`.
 */
export function collectRoomWallGapErrors(project: CfdProject | null): RoomWallGapError[] {
  if (!project) return []
  const roomHeight = inferRoomHeight(project)
  if (!(roomHeight > 0)) return []
  const errors: RoomWallGapError[] = []
  for (const { bucket, model } of allModels(project)) {
    if (bucket !== 'walls' || model.isRoomBoundary !== true || !model.geometry) continue
    const top = modelTopWorldY(model.geometry as unknown as BoxGeometry)
    if (Number.isFinite(top) && top < roomHeight - ROOM_WALL_GAP_TOLERANCE) {
      errors.push({ uid: model.uid, name: sanitizeName(model.meta?.name), top, roomHeight })
    }
  }
  return errors
}

export interface RoomWallExtension {
  uid: string
  name: string
  from: number
  to: number
}

/**
 * The fix the room-wall dialog offers: the `size.y` that puts each wall's top
 * on the room height, `(roomHeight - location.y) / scale.y`.
 *
 * Exact for a wall turned only about Y, which every room wall in `case/` is;
 * any other wall is measured again by the next pre-flight. A wall already at
 * that size, a uid that is not a wall and a height the wall cannot reach from
 * its anchor are left out, so an empty plan means there is nothing to change.
 */
export function planRoomWallExtension(
  project: CfdProject | null,
  uids: readonly string[],
  roomHeight: number,
): RoomWallExtension[] {
  const h = Number(roomHeight)
  if (!project || !(h > 0)) return []
  const walls = project.constructions?.walls ?? {}
  const plan: RoomWallExtension[] = []
  for (const uid of uids) {
    const model = walls[uid]
    const geometry = model?.geometry as unknown as BoxGeometry | undefined
    if (!model || !geometry?.size || !geometry.location) continue
    let sy = Number(geometry.scale?.y)
    if (!Number.isFinite(sy) || sy === 0) sy = 1
    const to = (h - (Number(geometry.location.y) || 0)) / sy
    const from = Number(geometry.size.y)
    if (to > 0 && to !== from) plan.push({ uid, name: model.meta?.name ?? uid, from, to })
  }
  return plan
}

// ---------------------------------------------------------------------------
// 3. Rack doors that disagree about porosity
// ---------------------------------------------------------------------------

interface DoorFace {
  uid?: string
  role?: string
  renderDoor?: boolean
  perforation?: { openAreaPct?: number }
}

/** The default the exporter assumes when a door carries no perforation block. */
export const DEFAULT_OPEN_AREA_PCT = 64

/** Every rendered rack door, keyed `<sanitised rack>_<side>`, with its open area. */
export function collectDoorPorosities(project: CfdProject | null): Map<string, number> {
  const out = new Map<string, number>()
  if (!project) return out
  for (const { bucket, model } of allModels(project)) {
    if (bucket !== 'racks') continue
    const faces = (model.geometry as { faces?: unknown } | undefined)?.faces
    const list: DoorFace[] = Array.isArray(faces)
      ? (faces as DoorFace[])
      : (Object.values((faces ?? {}) as Record<string, DoorFace>) as DoorFace[])
    for (const side of ['inlet', 'outlet']) {
      const door = list.find((f) => f && f.uid === `${side}Door` && f.role === 'door')
      if (!door || door.renderDoor === false) continue
      const pct = door.perforation?.openAreaPct
      out.set(
        `${sanitizeName(model.meta?.name)}_${side}`,
        pct == null ? DEFAULT_OPEN_AREA_PCT : Number(pct),
      )
    }
  }
  return out
}

/** The offending door list when more than one open-area value is in use. */
export function doorPorosityMismatch(project: CfdProject | null): { name: string; pct: number }[] {
  const doors = collectDoorPorosities(project)
  if (new Set(doors.values()).size <= 1) return []
  return Array.from(doors.entries(), ([name, pct]) => ({ name, pct }))
}

// ---------------------------------------------------------------------------
// 4. Seeds inside a solid
// ---------------------------------------------------------------------------

export interface Bounds {
  min: Vec3
  max: Vec3
}

export interface SeedCollisionTarget {
  modelUid: string
  name: string
  /** rack / acu / custom / box / wallObject / heatSource / partitionWall */
  type: string
  bounds: Bounds
}

/** The buckets whose models are solid enough for a seed to be trapped inside. */
const TARGET_BUCKETS = new Set(['racks', 'acus', 'customMeshes', 'boxes', 'partitionWalls'])

const BUCKET_TYPE: Record<string, string> = {
  racks: 'rack',
  acus: 'acu',
  customMeshes: 'custom',
  boxes: 'box',
  partitionWalls: 'partitionWall',
}

function targetType(model: Construction, bucket: string): string {
  if (bucket === 'boxes' || bucket === 'partitionWalls') {
    const role = (model.cooling as { role?: string } | undefined)?.role
    if (role === 'wallObject') return 'wallObject'
    if (role === 'heatSource') return 'heatSource'
  }
  return BUCKET_TYPE[bucket] ?? bucket
}

/**
 * The model's world AABB.
 *
 * The eight corners of the local box are transformed and re-bounded, rather
 * than the legacy's `computeApproxModelBounds` (STL_generate.html:27728), whose
 * corner list still puts DEPTH on the local Y axis and HEIGHT on Z — a
 * pre-Y-up leftover that swaps a rack's height and depth. It survives there
 * because that function is only the FALLBACK: the legacy reads the rendered
 * object's bounding box first and only computes this when the model has no
 * render root. With no scene to read, this has to be the correct box.
 */
export function modelWorldBounds(model: Construction): Bounds | null {
  const size = (model.geometry as { size?: Vec3 } | undefined)?.size
  const W = Number(size?.x) || 0
  const H = Number(size?.y) || 0
  const D = Number(size?.z) || 0
  if (W === 0 && H === 0 && D === 0) return null
  const min: Vec3 = { x: Infinity, y: Infinity, z: Infinity }
  const max: Vec3 = { x: -Infinity, y: -Infinity, z: -Infinity }
  for (const x of [0, W]) {
    for (const y of [0, H]) {
      for (const z of [0, D]) {
        const p = modelToWorld(model, { x, y, z })
        min.x = Math.min(min.x, p.x)
        min.y = Math.min(min.y, p.y)
        min.z = Math.min(min.z, p.z)
        max.x = Math.max(max.x, p.x)
        max.y = Math.max(max.y, p.y)
        max.z = Math.max(max.z, p.z)
      }
    }
  }
  return { min, max }
}

export function collectSeedCollisionTargets(project: CfdProject | null): SeedCollisionTarget[] {
  if (!project) return []
  const out: SeedCollisionTarget[] = []
  for (const { bucket, model } of allModels(project)) {
    if (!TARGET_BUCKETS.has(bucket)) continue
    const bounds = modelWorldBounds(model)
    if (!bounds) continue
    out.push({
      modelUid: model.uid,
      name: model.meta?.name ?? model.uid,
      type: targetType(model, bucket),
      bounds,
    })
  }
  return out
}

export function pointInsideBounds(point: Vec3, bounds: Bounds, padding = 1e-6): boolean {
  return (
    point.x >= bounds.min.x - padding &&
    point.x <= bounds.max.x + padding &&
    point.y >= bounds.min.y - padding &&
    point.y <= bounds.max.y + padding &&
    point.z >= bounds.min.z - padding &&
    point.z <= bounds.max.z + padding
  )
}

export interface SeedCollisionWarning {
  surfaceName: string
  surfaceOwner: string
  side: 'positive' | 'negative'
  objectName: string
  objectType: string
  point: Vec3
}

/**
 * Seeds that land inside another model's bounding box.
 *
 * A seed on the surface's OWN model is skipped — a perforated tile's seed sits
 * a couple of centimetres off the tile it belongs to, which is inside that
 * tile's own box by construction and is not a mistake.
 *
 * This is a coarse AABB pre-check, which is why the dialog it feeds offers
 * "ignore and continue": a rotated rack's box is bigger than the rack, and a
 * seed in the corner it does not fill is reported here and is fine.
 */
export function collectSeedCollisionWarnings(
  project: CfdProject | null,
  surfaces: SeedSurface[] = collectSeedSurfaces(project),
): SeedCollisionWarning[] {
  const targets = collectSeedCollisionTargets(project)
  if (!targets.length) return []
  const warnings: SeedCollisionWarning[] = []
  for (const surface of surfaces) {
    // Only a pinned seed can be trapped: the runner places every default seed
    // itself, measuring the clearance on both sides and moving the anchor to a
    // spot on the sheet with room when a solid stands against it (or failing
    // loudly). It keeps a user-placed anchor as placed. The editor's preview
    // of a default (centroid, display offset) is not where the mesher puts it.
    if (!surface.isCustom) continue
    const { positive, negative } = seedEndpoints(surface.seedConfig)
    for (const seed of [
      { side: 'positive' as const, point: positive },
      { side: 'negative' as const, point: negative },
    ]) {
      for (const target of targets) {
        if (target.modelUid === surface.modelUid) continue
        if (!pointInsideBounds(seed.point, target.bounds)) continue
        warnings.push({
          surfaceName: surface.label,
          surfaceOwner: surface.owner,
          side: seed.side,
          objectName: target.name,
          objectType: target.type,
          point: seed.point,
        })
      }
    }
  }
  return warnings
}

// ---------------------------------------------------------------------------
// 5. Baffle sheets that stop just short of what they should close against
// ---------------------------------------------------------------------------

/**
 * A baffle edge that stops a few millimetres short of the surface it points at.
 *
 * Containment is drawn as zero-thickness baffle sheets: doors across the aisle
 * ends, roofs on the rack tops, panels in an empty rack slot. When an edge ends
 * a few millimetres before the next sheet or solid, the mesher keeps that slit
 * open (even 1 mm: dcCart's 1.2 mm weld joins near-coincident vertices, not an
 * edge short of a face), so the
 * "sealed" aisle has a long narrow opening. With fullBlock racks the whole
 * airflow imbalance between the aisles is forced through it: jets of hundreds of
 * m/s, a turbulence field that never settles, and hot air that appears on the
 * cold side. CMI-Guichou's roof CD4 started at z=27.010 while the door CD2 in
 * the empty slot below it stood at z=27.000, and the run never converged; with
 * every slit closed it converged in 175 steps.
 *
 * An edge is measured along its length, not by its box: each sample point on it
 * is either already on another model (within `SHEET_GAP_COVERED`), or a ray from
 * it outward, in the sheet's own plane, meets another model within
 * `SHEET_GAP_MAX`. A sample where the sheet overhangs a model that meets its
 * face just inside the edge is sealed there too. Only an edge whose every open sample meets something that
 * close is reported: an edge with any sample facing open space is an opening the
 * user drew, and is left as drawn (a leakage study's gaps are wider, and are
 * not near-misses). The extension is the largest of those distances, so the
 * edge closes along its whole length. Passes repeat because closing one sheet
 * can cover another's edge (a roof moved onto a door covers the door's top).
 *
 * A sheet can also sit a few millimetres off the plane it is meant to close:
 * CMI-Guichou's aisle-end doors bridge the gap between a rack and an ACU, but
 * stand 2-14 mm in front of (or behind) their faces, so both side edges are
 * slits the full height of the door. When the models abutting a sheet's two
 * opposite edges both have a face parallel to the sheet within
 * `SHEET_GAP_MAX`, agreeing to the weld tolerance, the sheet moves onto that
 * face plane first; the edge passes then run on the moved sheet.
 */
export interface SheetGap {
  uid: string
  name: string
  /**
   * 'edge': one edge moves outward along `axis`. 'plane': the whole sheet moves
   * along its normal `axis` (toward `side`) onto the face plane of the two
   * models it bridges.
   */
  move: 'edge' | 'plane'
  axis: 'x' | 'y' | 'z'
  /** -1: the edge at the low end of `axis` (or, for a plane move, toward -axis); +1: high. */
  side: -1 | 1
  /** How far the edge moves, metres. */
  gap: number
  /** The model the widest open sample runs into. */
  targetName: string
}

/** A sample this close to another model is already closed. */
export const SHEET_GAP_COVERED = 5e-4
/** Further than this, the opening is drawn on purpose. */
export const SHEET_GAP_MAX = 0.02
/**
 * Narrower gaps are not reported. Not the 1.2 mm weld tolerance: that welds
 * near-coincident vertices, and a sheet edge 1 mm short of a face stays open
 * (CMI-Guichou's roofs, 1 mm short of the doors, left four aisles unsealed).
 */
export const SHEET_GAP_REPORT_MIN = 5e-4
/** The two models a sheet bridges must agree on the plane to this (dcCart's weld). */
export const SHEET_PLANE_AGREE = 1.2e-3

interface GapBox {
  uid: string
  name: string
  sheet: boolean
  lo: [number, number, number]
  hi: [number, number, number]
}

const AXES = ['x', 'y', 'z'] as const

/** World image of each local unit axis (scaled), or null when the model is turned off-axis. */
function localAxes(model: Construction): Vec3[] | null {
  const o = modelToWorld(model, { x: 0, y: 0, z: 0 })
  const dirs = [
    { x: 1, y: 0, z: 0 },
    { x: 0, y: 1, z: 0 },
    { x: 0, y: 0, z: 1 },
  ].map((e) => {
    const p = modelToWorld(model, e)
    return { x: p.x - o.x, y: p.y - o.y, z: p.z - o.z }
  })
  for (const d of dirs) {
    const c = [Math.abs(d.x), Math.abs(d.y), Math.abs(d.z)]
    const big = c.filter((v) => v > 1e-9).length
    if (big > 1 || !c.every(Number.isFinite)) return null
  }
  return dirs
}

function gapBoxes(project: CfdProject): GapBox[] {
  const out: GapBox[] = []
  for (const { bucket, model } of allModels(project)) {
    // A custom mesh's box is not its surface: an edge "on" the box may be in the open.
    if (bucket === 'customMeshes' || !model.geometry) continue
    if (!localAxes(model)) continue
    const b = modelWorldBounds(model)
    if (!b) continue
    const lo: [number, number, number] = [b.min.x, b.min.y, b.min.z]
    const hi: [number, number, number] = [b.max.x, b.max.y, b.max.z]
    if (![...lo, ...hi].every(Number.isFinite)) continue
    const thin = [0, 1, 2].filter((a) => hi[a]! - lo[a]! < 1e-9).length
    out.push({
      uid: model.uid,
      name: model.meta?.name ?? model.uid,
      sheet: bucket === 'baffles' && thin === 1,
      lo,
      hi,
    })
  }
  return out
}

function distanceToBox(p: number[], b: GapBox): number {
  let s = 0
  for (let a = 0; a < 3; a++) {
    const d = Math.max(0, b.lo[a]! - p[a]!, p[a]! - b.hi[a]!)
    s += d * d
  }
  return Math.sqrt(s)
}

/** Distance along `a` (direction `s`) from `p` to where it enters `b`, or null. */
function rayEntry(p: number[], a: number, s: number, b: GapBox): number | null {
  for (let k = 0; k < 3; k++) {
    if (k === a) continue
    if (p[k]! < b.lo[k]! - SHEET_GAP_COVERED || p[k]! > b.hi[k]! + SHEET_GAP_COVERED) return null
  }
  const t = s > 0 ? b.lo[a]! - p[a]! : p[a]! - b.hi[a]!
  return t > -SHEET_GAP_COVERED ? Math.max(0, t) : null
}

/**
 * Whether `o` meets the sheet's face (normal axis `n`) somewhere between the edge sample `p` and
 * `SHEET_GAP_MAX` inward from it (the edge is the `s` end of axis `a`). Air cannot pass between the
 * sheet and that model, so the sample only overhangs a seal.
 */
function meetsFaceInside(p: number[], n: number, a: number, s: number, o: GapBox): boolean {
  if (o.lo[n]! > p[n]! + SHEET_GAP_COVERED || o.hi[n]! < p[n]! - SHEET_GAP_COVERED) return false
  const b = 3 - n - a
  if (o.lo[b]! > p[b]! + SHEET_GAP_COVERED || o.hi[b]! < p[b]! - SHEET_GAP_COVERED) return false
  const from = s < 0 ? p[a]! : p[a]! - SHEET_GAP_MAX
  const to = s < 0 ? p[a]! + SHEET_GAP_MAX : p[a]!
  return o.hi[a]! >= from && o.lo[a]! <= to
}

/** Every near-miss sheet edge or plane wider than `SHEET_GAP_REPORT_MIN`. */
export function collectSheetGaps(project: CfdProject | null): SheetGap[] {
  if (!project) return []
  const boxes = gapBoxes(project)
  const moves = new Map<string, SheetGap>()
  const solids = boxes.filter((o) => [0, 1, 2].every((k) => o.hi[k]! - o.lo[k]! >= 1e-9))
  for (const box of boxes) {
    if (!box.sheet) continue
    const n = [0, 1, 2].find((a) => box.hi[a]! - box.lo[a]! < 1e-9)!
    const c = box.lo[n]!
    let target: { f: number; name: string } | null = null
    for (const a of [0, 1, 2].filter((k) => k !== n)) {
      const b = 3 - n - a
      const sides: ({ d: number; f: number; name: string } | null)[] = [-1, 1].map((s) => {
        const e = s < 0 ? box.lo[a]! : box.hi[a]!
        let best: { d: number; f: number; name: string } | null = null
        for (const o of solids) {
          if (o.uid === box.uid) continue
          // abuts the edge from outside the sheet
          if (e < o.lo[a]! - SHEET_GAP_COVERED || e > o.hi[a]! + SHEET_GAP_COVERED) continue
          if (s < 0 && o.hi[a]! > e + SHEET_GAP_COVERED) continue
          if (s > 0 && o.lo[a]! < e - SHEET_GAP_COVERED) continue
          if (o.hi[b]! < box.lo[b]! - SHEET_GAP_COVERED || o.lo[b]! > box.hi[b]! + SHEET_GAP_COVERED) continue
          for (const f of [o.lo[n]!, o.hi[n]!]) {
            const d = Math.abs(f - c)
            if (d > SHEET_GAP_COVERED && d <= SHEET_GAP_MAX && (!best || d < best.d)) best = { d, f, name: o.name }
          }
        }
        return best
      })
      const [lo, hi] = sides
      if (lo && hi && Math.abs(lo.f - hi.f) <= SHEET_PLANE_AGREE) {
        target = lo.d <= hi.d ? lo : hi
        break
      }
    }
    if (!target) continue
    const delta = target.f - c
    box.lo[n] = target.f
    box.hi[n] = target.f
    moves.set(`${box.uid}|plane`, {
      uid: box.uid,
      name: box.name,
      move: 'plane',
      axis: AXES[n]!,
      side: delta < 0 ? -1 : 1,
      gap: Math.abs(delta),
      targetName: target.name,
    })
  }
  for (let pass = 0; pass < 4; pass++) {
    let moved = false
    for (const box of boxes) {
      if (!box.sheet) continue
      const normal = [0, 1, 2].find((a) => box.hi[a]! - box.lo[a]! < 1e-9)!
      for (const a of [0, 1, 2].filter((k) => k !== normal)) {
        const b = 3 - normal - a
        for (const s of [-1, 1] as const) {
          const edge = s < 0 ? box.lo[a]! : box.hi[a]!
          // Only models that can reach this edge within the search distance.
          const near = boxes.filter((o) => {
            if (o.uid === box.uid) return false
            for (let k = 0; k < 3; k++) {
              const lo = k === a ? edge : box.lo[k]!
              const hi = k === a ? edge : box.hi[k]!
              if (o.hi[k]! < lo - SHEET_GAP_MAX || o.lo[k]! > hi + SHEET_GAP_MAX) return false
            }
            return true
          })
          const length = box.hi[b]! - box.lo[b]!
          const n = Math.min(400, Math.max(8, Math.ceil(length / 0.05)))
          let worst = 0
          let target = ''
          let open = 0
          let closable = true
          for (let i = 0; i < n && closable; i++) {
            const p = [0, 0, 0]
            p[normal] = box.lo[normal]!
            p[a] = edge
            p[b] = box.lo[b]! + ((i + 0.5) / n) * length
            if (near.some((o) => distanceToBox(p, o) <= SHEET_GAP_COVERED)) continue
            // Sealed just inside the edge: a model meets the sheet's face within the search distance, so the
            // edge only overhangs it. CMI-Guichou's roof IJ4 runs 2 mm past the door IJ2 under it, over an empty
            // rack slot; read as open space, that skipped the whole edge and its 6 mm slits over J10 and J13.
            if (near.some((o) => meetsFaceInside(p, normal, a, s, o))) continue
            open += 1
            let best = Infinity
            let bestName = ''
            for (const o of near) {
              const t = rayEntry(p, a, s, o)
              if (t !== null && t <= SHEET_GAP_MAX && t < best) {
                best = t
                bestName = o.name
              }
            }
            if (!Number.isFinite(best)) closable = false
            else if (best > worst) {
              worst = best
              target = bestName
            }
          }
          if (!open || !closable || worst <= 0) continue
          if (s < 0) box.lo[a] = box.lo[a]! - worst
          else box.hi[a] = box.hi[a]! + worst
          moved = true
          const key = `${box.uid}|${a}|${s}`
          const prior = moves.get(key)
          moves.set(key, {
            uid: box.uid,
            name: box.name,
            move: 'edge',
            axis: AXES[a]!,
            side: s,
            gap: (prior?.gap ?? 0) + worst,
            targetName: target || prior?.targetName || '',
          })
        }
      }
    }
    if (!moved) break
  }
  return [...moves.values()].filter((g) => g.gap > SHEET_GAP_REPORT_MIN)
}

export interface SheetGapEdit {
  uid: string
  location: Vec3
  size: Vec3
}

/**
 * The geometry that moves each reported edge onto its target: the sheet's local
 * axis lying along the world axis grows by the gap, and its corner `location`
 * moves when the growing end is the one the corner sits at.
 */
export function planSheetGapClosure(project: CfdProject | null, gaps: readonly SheetGap[]): SheetGapEdit[] {
  if (!project) return []
  const baffles = (project.constructions?.baffles ?? {}) as Record<string, Construction>
  const edits = new Map<string, SheetGapEdit>()
  for (const gap of gaps) {
    const model = baffles[gap.uid]
    const geometry = model?.geometry as { location?: Vec3; size?: Vec3 } | undefined
    if (!model || !geometry?.location || !geometry.size) continue
    const dirs = localAxes(model)
    if (!dirs) continue
    const a = gap.axis
    const edit = edits.get(gap.uid) ?? {
      uid: gap.uid,
      location: { ...geometry.location },
      size: { ...geometry.size },
    }
    edits.set(gap.uid, edit)
    if (gap.move === 'plane') {
      // The sheet's normal: its corner moves with the whole sheet.
      edit.location[a] = Number(edit.location[a]) + gap.side * gap.gap
      continue
    }
    const i = dirs.findIndex((d) => Math.abs(d[a]) > 1e-9)
    if (i < 0) continue
    const k = AXES[i]!
    const scale = Math.abs(dirs[i]![a])
    const sign = Math.sign(dirs[i]![a])
    edit.size[k] = Number(edit.size[k]) + gap.gap / scale
    if (gap.side < 0 && sign > 0) edit.location[a] = Number(edit.location[a]) - gap.gap
    if (gap.side > 0 && sign < 0) edit.location[a] = Number(edit.location[a]) + gap.gap
  }
  return [...edits.values()]
}

// ---------------------------------------------------------------------------
// The whole pre-flight, as data
// ---------------------------------------------------------------------------

export interface PreflightReport {
  digitNames: DigitNameModel[]
  floorHeights: FloorHeightError[]
  roomWallGaps: RoomWallGapError[]
  doorPorosity: { name: string; pct: number }[]
  seedCollisions: SeedCollisionWarning[]
  sheetGaps: SheetGap[]
}

/**
 * Every finding, in the order the legacy stops on them.
 *
 * Order matters because each finding is a modal: reporting the seed collisions
 * of a project whose names OpenFOAM cannot read wastes the user's first
 * decision on the second problem.
 */
export function runPreflightChecks(project: CfdProject | null): PreflightReport {
  return {
    digitNames: collectDigitNameModels(project),
    floorHeights: collectFloorHeightErrors(project),
    roomWallGaps: collectRoomWallGapErrors(project),
    doorPorosity: doorPorosityMismatch(project),
    seedCollisions: collectSeedCollisionWarnings(project),
    sheetGaps: collectSheetGaps(project),
  }
}
