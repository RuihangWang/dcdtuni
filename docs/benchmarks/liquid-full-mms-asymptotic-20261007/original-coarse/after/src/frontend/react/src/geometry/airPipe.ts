/**
 * The air-side pipe / duct: a swept solid along a polyline.
 *
 * Kind `pipe`, bucket `pipes`. Its shape lives in `geometry.waypoints`, which
 * are LOCAL to `geometry.location` (the anchor, the same field the move tool
 * drags) so moving the anchor moves the whole run. Round pipes carry
 * `diameterM`; rectangular ones `widthM` / `heightM`. Two pipes whose ends
 * were snapped together record the pairing in `geometry.joins` on BOTH sides,
 * and the renderer draws a reducer where the bores differ.
 *
 * Everything the viewport handles and the inspector do to a pipe goes through
 * here, so the two cannot disagree on what a waypoint means.
 */

import type { CfdProject, Construction, Quat, Vec3 } from '../schema/cfdproj'
import * as store from '../projectStore'

export type PipeShape = 'round' | 'rect'
export type PipeEnd = 'start' | 'end'

export interface PipeJoin {
  /** Which of THIS pipe's ends is joined. */
  end: PipeEnd
  pipeUid: string
  pipeEnd: PipeEnd
}

export interface AirPipeGeometry {
  location: Vec3
  quaternion: Quat
  shape: PipeShape
  diameterM: number
  widthM: number
  heightM: number
  waypoints: Vec3[]
  joins: PipeJoin[]
}

export const DEFAULT_PIPE_DIAMETER_M = 0.15
export const DEFAULT_PIPE_WIDTH_M = 0.2
export const DEFAULT_PIPE_HEIGHT_M = 0.2
/** How close a dragged end has to come to another pipe's free end to snap. */
export const PIPE_SNAP_DISTANCE_M = 0.12
/** Moving a joined end further than this away breaks the join. */
export const PIPE_UNJOIN_DISTANCE_M = 0.05
export const MIN_PIPE_BORE_M = 0.01

export function isAirPipe(model: Construction | null | undefined): boolean {
  return !!model && (model as { kind?: unknown }).kind === 'pipe'
}

type Bag = Record<string, unknown>

function num(v: unknown, fallback: number): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

function vec(v: unknown): Vec3 {
  const b = (v ?? {}) as Partial<Vec3>
  return { x: num(b.x, 0), y: num(b.y, 0), z: num(b.z, 0) }
}

/** The pipe's geometry with every field present and numeric. */
export function airPipeGeometry(model: Construction): AirPipeGeometry {
  const g = (model.geometry ?? {}) as Bag
  const q = (g.quaternion ?? {}) as Partial<Quat>
  const joins = Array.isArray(g.joins) ? (g.joins as Bag[]) : []
  return {
    location: vec(g.location),
    quaternion: { x: num(q.x, 0), y: num(q.y, 0), z: num(q.z, 0), w: num(q.w, 1) },
    shape: g.shape === 'rect' ? 'rect' : 'round',
    diameterM: num(g.diameterM, DEFAULT_PIPE_DIAMETER_M),
    widthM: num(g.widthM, DEFAULT_PIPE_WIDTH_M),
    heightM: num(g.heightM, DEFAULT_PIPE_HEIGHT_M),
    waypoints: (Array.isArray(g.waypoints) ? g.waypoints : []).map(vec),
    joins: joins
      .filter((j) => j && typeof j.pipeUid === 'string')
      .map((j) => ({
        end: j.end === 'start' ? 'start' : 'end',
        pipeUid: String(j.pipeUid),
        pipeEnd: j.pipeEnd === 'start' ? 'start' : 'end',
      })),
  }
}

/** Half the bore, or half the larger rectangular side. */
export function airPipeRadius(model: Construction): number {
  const g = airPipeGeometry(model)
  return g.shape === 'round' ? g.diameterM / 2 : Math.max(g.widthM, g.heightM) / 2
}

// ── local ⇄ world ───────────────────────────────────────────────────────────

function rotate(q: Quat, v: Vec3): Vec3 {
  // v' = q v q*
  const { x, y, z, w } = q
  const ix = w * v.x + y * v.z - z * v.y
  const iy = w * v.y + z * v.x - x * v.z
  const iz = w * v.z + x * v.y - y * v.x
  const iw = -x * v.x - y * v.y - z * v.z
  return {
    x: ix * w + iw * -x + iy * -z - iz * -y,
    y: iy * w + iw * -y + iz * -x - ix * -z,
    z: iz * w + iw * -z + ix * -y - iy * -x,
  }
}

export function pipeLocalToWorld(model: Construction, p: Vec3): Vec3 {
  const g = airPipeGeometry(model)
  const r = rotate(g.quaternion, p)
  return { x: r.x + g.location.x, y: r.y + g.location.y, z: r.z + g.location.z }
}

export function pipeWorldToLocal(model: Construction, p: Vec3): Vec3 {
  const g = airPipeGeometry(model)
  const q = g.quaternion
  const conj = { x: -q.x, y: -q.y, z: -q.z, w: q.w }
  return rotate(conj, { x: p.x - g.location.x, y: p.y - g.location.y, z: p.z - g.location.z })
}

/** Every waypoint in world coordinates, in order. */
export function airPipeWorldPoints(model: Construction): Vec3[] {
  return airPipeGeometry(model).waypoints.map((p) => pipeLocalToWorld(model, p))
}

export function airPipeEndWorld(model: Construction, end: PipeEnd): Vec3 | null {
  const pts = airPipeWorldPoints(model)
  if (!pts.length) return null
  return end === 'start' ? pts[0]! : pts[pts.length - 1]!
}

export function endIndex(model: Construction, end: PipeEnd): number {
  const n = airPipeGeometry(model).waypoints.length
  return end === 'start' ? 0 : Math.max(0, n - 1)
}

export function endOfIndex(model: Construction, index: number): PipeEnd | null {
  const n = airPipeGeometry(model).waypoints.length
  if (index === 0) return 'start'
  if (index === n - 1) return 'end'
  return null
}

function dist(a: Vec3, b: Vec3): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)
}

// ── in-place mutation (drags call these under mutateLive) ─────────────────

function geometryBag(model: Construction): Bag {
  const g = (model.geometry ?? (model.geometry = {})) as Bag
  if (!Array.isArray(g.waypoints)) g.waypoints = []
  return g
}

/** Put waypoint `index` at a WORLD position. */
export function writeAirPipePointWorld(model: Construction, index: number, world: Vec3): void {
  const g = geometryBag(model)
  const wps = g.waypoints as Vec3[]
  if (index < 0 || index >= wps.length) return
  wps[index] = pipeWorldToLocal(model, world)
}

/** Shift the whole pipe: the anchor moves, the local waypoints stay. */
export function translateAirPipe(model: Construction, delta: Vec3): void {
  const g = geometryBag(model)
  const loc = vec(g.location)
  g.location = { x: loc.x + delta.x, y: loc.y + delta.y, z: loc.z + delta.z }
}

/** Insert a waypoint AFTER `index`, on the segment's midpoint (or extended past the end). */
export function insertAirPipePoint(model: Construction, index: number): number {
  const g = geometryBag(model)
  const wps = g.waypoints as Vec3[]
  if (!wps.length) return -1
  const i = Math.max(0, Math.min(index, wps.length - 1))
  let point: Vec3
  if (i < wps.length - 1) {
    const a = wps[i]!
    const b = wps[i + 1]!
    point = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 }
  } else {
    const a = wps[i]!
    const prev = wps[i - 1] ?? { x: a.x, y: a.y - 0.3, z: a.z }
    const d = { x: a.x - prev.x, y: a.y - prev.y, z: a.z - prev.z }
    const len = Math.hypot(d.x, d.y, d.z) || 1
    point = { x: a.x + (d.x / len) * 0.3, y: a.y + (d.y / len) * 0.3, z: a.z + (d.z / len) * 0.3 }
  }
  wps.splice(i + 1, 0, point)
  return i + 1
}

export function removeAirPipePoint(model: Construction, index: number): boolean {
  const g = geometryBag(model)
  const wps = g.waypoints as Vec3[]
  // Two points are the least a pipe can be drawn from.
  if (wps.length <= 2 || index < 0 || index >= wps.length) return false
  wps.splice(index, 1)
  return true
}

// ── joins ───────────────────────────────────────────────────────────────────

function joinsOf(model: Construction): PipeJoin[] {
  const g = geometryBag(model)
  if (!Array.isArray(g.joins)) g.joins = []
  return g.joins as PipeJoin[]
}

export function joinAt(model: Construction, end: PipeEnd): PipeJoin | null {
  return airPipeGeometry(model).joins.find((j) => j.end === end) ?? null
}

/** Break the join on `end` of `uid`, on both pipes. */
export function unjoinAirPipeEnd(project: CfdProject, uid: string, end: PipeEnd): void {
  const model = store.findConstruction(project, uid)?.model
  if (!model) return
  const mine = joinsOf(model)
  const j = mine.find((x) => x.end === end)
  if (!j) return
  mine.splice(mine.indexOf(j), 1)
  const other = store.findConstruction(project, j.pipeUid)?.model
  if (!other) return
  const theirs = joinsOf(other)
  for (let i = theirs.length - 1; i >= 0; i--) {
    if (theirs[i]!.pipeUid === uid && theirs[i]!.end === j.pipeEnd) theirs.splice(i, 1)
  }
}

/**
 * Join `end` of pipe `uid` to `otherEnd` of pipe `otherUid`: the moving end is
 * put exactly on the other pipe's end, and both pipes record the pairing.
 * Any previous join on either end is broken first.
 */
export function joinAirPipes(
  project: CfdProject,
  uid: string,
  end: PipeEnd,
  otherUid: string,
  otherEnd: PipeEnd,
): boolean {
  if (uid === otherUid) return false
  const model = store.findConstruction(project, uid)?.model
  const other = store.findConstruction(project, otherUid)?.model
  if (!model || !other || !isAirPipe(model) || !isAirPipe(other)) return false
  const target = airPipeEndWorld(other, otherEnd)
  if (!target) return false
  unjoinAirPipeEnd(project, uid, end)
  unjoinAirPipeEnd(project, otherUid, otherEnd)
  writeAirPipePointWorld(model, endIndex(model, end), target)
  joinsOf(model).push({ end, pipeUid: otherUid, pipeEnd: otherEnd })
  joinsOf(other).push({ end: otherEnd, pipeUid: uid, pipeEnd: end })
  return true
}

export interface FreePipeEnd {
  uid: string
  end: PipeEnd
  world: Vec3
  radius: number
}

/** Every unjoined end of every OTHER enabled pipe. */
export function freeAirPipeEnds(project: CfdProject, exceptUid: string | null): FreePipeEnd[] {
  const out: FreePipeEnd[] = []
  const pipes = (project.constructions?.pipes ?? {}) as Record<string, Construction>
  for (const [uid, model] of Object.entries(pipes)) {
    if (uid === exceptUid || model.meta?.enabled === false) continue
    for (const end of ['start', 'end'] as const) {
      if (joinAt(model, end)) continue
      const world = airPipeEndWorld(model, end)
      if (world) out.push({ uid, end, world, radius: airPipeRadius(model) })
    }
  }
  return out
}

/** The nearest free end of another pipe within the snap distance, if any. */
export function findAirPipeJoinTarget(
  project: CfdProject,
  uid: string,
  world: Vec3,
  maxDistance = PIPE_SNAP_DISTANCE_M,
): FreePipeEnd | null {
  let best: FreePipeEnd | null = null
  let bestD = maxDistance
  for (const cand of freeAirPipeEnds(project, uid)) {
    const d = dist(cand.world, world)
    if (d <= bestD) {
      best = cand
      bestD = d
    }
  }
  return best
}

/** True when a joined end has drifted away from its partner. */
export function joinBroken(project: CfdProject, model: Construction, end: PipeEnd): boolean {
  const j = joinAt(model, end)
  if (!j) return false
  const other = store.findConstruction(project, j.pipeUid)?.model
  const mine = airPipeEndWorld(model, end)
  const theirs = other ? airPipeEndWorld(other, j.pipeEnd) : null
  if (!mine || !theirs) return true
  return dist(mine, theirs) > PIPE_UNJOIN_DISTANCE_M
}

// ── committed edits (the inspector) ─────────────────────────────────────────

function editPipe(action: string, uid: string, fn: (model: Construction, draft: CfdProject) => void) {
  store.edit(action, uid, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (model && isAirPipe(model)) fn(model, draft)
  })
}

export function setAirPipeShape(uid: string, shape: PipeShape): void {
  editPipe('setAirPipeShape', uid, (m) => {
    geometryBag(m).shape = shape === 'rect' ? 'rect' : 'round'
  })
}

export function setAirPipeDiameter(uid: string, diameterM: number): void {
  if (!Number.isFinite(diameterM) || diameterM < MIN_PIPE_BORE_M) return
  editPipe('setAirPipeDiameter', uid, (m) => {
    geometryBag(m).diameterM = diameterM
  })
}

export function setAirPipeSize(uid: string, size: { widthM?: number; heightM?: number }): void {
  editPipe('setAirPipeSize', uid, (m) => {
    const g = geometryBag(m)
    if (Number.isFinite(size.widthM) && (size.widthM as number) >= MIN_PIPE_BORE_M) g.widthM = size.widthM
    if (Number.isFinite(size.heightM) && (size.heightM as number) >= MIN_PIPE_BORE_M) g.heightM = size.heightM
  })
}

/** Type a waypoint's WORLD coordinate. A joined end that is moved away is unjoined. */
export function setAirPipePointWorld(uid: string, index: number, world: Vec3): void {
  editPipe('setAirPipePoint', uid, (m, draft) => {
    writeAirPipePointWorld(m, index, world)
    const end = endOfIndex(m, index)
    if (end && joinBroken(draft, m, end)) unjoinAirPipeEnd(draft, uid, end)
  })
}

/** Replace the whole polyline with WORLD points. Breaks every join. */
export function setAirPipeWorldPoints(uid: string, points: Vec3[]): void {
  if (points.length < 2) return
  editPipe('setAirPipePoints', uid, (m, draft) => {
    unjoinAirPipeEnd(draft, uid, 'start')
    unjoinAirPipeEnd(draft, uid, 'end')
    const g = geometryBag(m)
    g.waypoints = points.map((p) => pipeWorldToLocal(m, p))
  })
}

export function insertAirPipePointAfter(uid: string, index: number): void {
  editPipe('insertAirPipePoint', uid, (m) => {
    insertAirPipePoint(m, index)
  })
}

export function deleteAirPipePoint(uid: string, index: number): void {
  editPipe('deleteAirPipePoint', uid, (m, draft) => {
    // Deleting an end hands the join to nothing: break it.
    const end = endOfIndex(m, index)
    if (end) unjoinAirPipeEnd(draft, uid, end)
    removeAirPipePoint(m, index)
  })
}

/** Break the join on one end from the inspector. */
export function detachAirPipeEnd(uid: string, end: PipeEnd): void {
  editPipe('unjoinAirPipe', uid, (_m, draft) => unjoinAirPipeEnd(draft, uid, end))
}
