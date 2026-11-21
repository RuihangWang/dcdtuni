/**
 * Turns the flow-arrow layout of a result (liquidFlowArrows, the rule Kit
 * shares) into what the shared arrow renderer draws: tracks to ride on and
 * the arrows on them.
 *
 * Where an arrow rides — the C1 zero-training check on Shinaide put every
 * result cell within 0.05 mm of a rendered centreline, so:
 *  - a cell of a pipe edge rides that pipe's rendered tube (its display
 *    path), moving along its bends; its stretch runs halfway to the next cell
 *    of the same edge on that tube;
 *  - any other cell (a manifold header, a valve's spoke, a feed without a
 *    tube) rides its edge's own polyline: from-node, its cells, to-node;
 *  - a server branch rides the hose the editor draws between the manifold
 *    and that server (`stubServerId`), from the manifold end (the one nearer
 *    the branch's tap) to the server end, in the direction it actually flows.
 * Arrows inside a closed opaque cabinet are left out (the tube is clipped
 * there; an arrow would float inside the cabinet).
 *
 * Size and colour, on the scales of liquidDisplayState.arrowScales: an
 * arrow's size encodes its mass flow alone (the square root of its place in
 * the mass-flow range, between ARROW_RADIUS_M), whatever pipe it is on; it
 * rides just off its pipe on the side facing the camera (`lift`), so it is
 * never buried in the tube and the tube keeps its own colour; it is coloured
 * by velocity or plain, and moves at its velocity times the display scale.
 */

import * as THREE from 'three'
import { liquidValueColor } from './liquidDisplay'
import { PLAIN_ARROW_COLOR, type ArrowScales, type LiquidDisplayOptions } from './liquidDisplayState'
import { bindingEdges, liquidFlowArrows, type LiquidFlowArrows } from './liquidFlow'
import { pipeTracks, projectOnPolyline, type PipeTrack } from './liquidNetworkPaint'

type Bag = Record<string, unknown>

export interface LiquidArrowTrack { points: number[][] }
export interface LiquidArrowSpecItem {
  track: number; s: number; sMin: number; sMax: number; dir: 1 | -1
  radius: number; length: number; color: number; speed: number; lift: number
  /** What the arrow shows, for the panel and the browser check. */
  kind: 'cell' | 'header' | 'branch'; edgeId: string; massFlowKgS: number; velocityMS: number | null
  position: number[]; direction: number[]
}
export interface LiquidArrowSpec {
  tracks: LiquidArrowTrack[]
  arrows: LiquidArrowSpecItem[]
  density: number
  animate: boolean
  speedScale: number
  /** Arrows the layout had but that were not drawn, and why. */
  skipped: { hidden: number; noTrack: number; noStub: number }
}

/** A speed for an arrow without a velocity (no diameter): slow, but moving. */
const FALLBACK_SPEED = 0.05
const DEFAULT_RADIUS = 0.0125
/** Arrow cone radius at the smallest and the largest mass flow. */
export const ARROW_RADIUS_M: readonly [number, number] = [0.007, 0.028]
const ARROW_LENGTH = 2.8

const sub = (a: readonly number[], b: readonly number[]) => [a[0]! - b[0]!, a[1]! - b[1]!, a[2]! - b[2]!]
const dot = (a: readonly number[], b: readonly number[]) => a[0]! * b[0]! + a[1]! * b[1]! + a[2]! * b[2]!

function cumulative(points: number[][]): number[] {
  const cum = [0]
  for (let i = 1; i < points.length; i++) cum.push(cum[i - 1]! + Math.hypot(...sub(points[i]!, points[i - 1]!) as [number, number, number]))
  return cum
}
/** Unit tangent of a polyline at arc length s. */
function tangentAt(points: number[][], cum: number[], s: number): number[] {
  for (let i = 1; i < points.length; i++) {
    if (s <= cum[i]! || i === points.length - 1) {
      const d = sub(points[i]!, points[i - 1]!)
      const n = Math.hypot(d[0]!, d[1]!, d[2]!)
      if (n > 0) return d.map(v => v / n)
    }
  }
  return [0, 1, 0]
}

export interface ArrowSceneOptions extends Pick<LiquidDisplayOptions, 'density' | 'animate' | 'speedScale' | 'arrowColor'> {}

export function buildLiquidArrowSpec(raw: Bag, sample: number, root: THREE.Object3D, opts: ArrowSceneOptions,
  scales: ArrowScales, hidden: (p: readonly number[]) => boolean = () => false, layout?: LiquidFlowArrows | null): LiquidArrowSpec {
  const spec: LiquidArrowSpec = {
    tracks: [], arrows: [], density: opts.density, animate: opts.animate, speedScale: opts.speedScale,
    skipped: { hidden: 0, noTrack: 0, noStub: 0 },
  }
  const fa = layout ?? liquidFlowArrows(raw, sample)
  if (!fa) return spec
  root.updateWorldMatrix(true, true)
  const toRoot = root.matrixWorld.clone().invert()

  const edgeIds = (Array.isArray(raw.edge_ids) ? raw.edge_ids : []).map(String)
  const known = new Set(edgeIds)
  const edgeIndex = new Map(edgeIds.map((e, i) => [e, i]))
  const diameters = Array.isArray(raw.edge_D_m) ? raw.edge_D_m : []
  const diameter = (e: string) => { const i = edgeIndex.get(e); const d = i === undefined ? null : diameters[i]; return typeof d === 'number' && d > 0 ? d : null }
  const nodeIds = (Array.isArray(raw.node_ids) ? raw.node_ids : []).map(String)
  const nodePositions = Array.isArray(raw.node_positions_m) ? raw.node_positions_m : []
  const nodePos = new Map(nodeIds.map((n, i) => [n, Array.isArray(nodePositions[i]) ? nodePositions[i] as number[] : null]))
  const sources = Array.isArray(raw.edge_from) ? raw.edge_from : [], sinks = Array.isArray(raw.edge_to) ? raw.edge_to : []
  const cellEdges = (Array.isArray(raw.cell_edge_ids) ? raw.cell_edge_ids : []).map(String)
  const cellPositions = Array.isArray(raw.cell_positions) ? raw.cell_positions as number[][] : []
  const cellsOf = new Map<string, number[]>()
  cellEdges.forEach((e, c) => { const own = cellsOf.get(e) ?? []; own.push(c); cellsOf.set(e, own) })

  // Which pipe owns each edge, and that pipe's rendered tubes.
  const bindings = raw.object_bindings && typeof raw.object_bindings === 'object' ? (raw.object_bindings as Record<string, Bag>) : {}
  const pipeOf = new Map<string, string>()
  for (const [key, b] of Object.entries(bindings)) if (b.kind === 'pipe_segment') for (const e of bindingEdges(key, b, known)) pipeOf.set(e, key)
  const tubes = new Map<string, PipeTrack[]>()
  for (const t of pipeTracks(root)) {
    if (!t.mesh.visible) continue
    tubes.set(t.pipeUid, [...(tubes.get(t.pipeUid) ?? []), t])
  }

  const trackIds = new Map<object, number>()
  const addTrack = (key: object, points: number[][]) => {
    let id = trackIds.get(key)
    if (id === undefined) { id = spec.tracks.length; spec.tracks.push({ points }); trackIds.set(key, id) }
    return id
  }

  // Each cell's place on its track: [track key, points, cum, s, sMin, sMax].
  type Placement = { key: object; points: number[][]; cum: number[]; s: number; sMin: number; sMax: number; radius: number }
  const placements = new Map<number, Placement>()
  const stretch = (ss: number[], k: number, ends: [number, number]) => {
    const lo = k > 0 ? (ss[k - 1]! + ss[k]!) / 2 : ends[0]
    const hi = k < ss.length - 1 ? (ss[k]! + ss[k + 1]!) / 2 : ends[1]
    return [Math.min(lo, ss[k]!), Math.max(hi, ss[k]!)] as const
  }
  const edgePolylines = new Map<string, object>()
  const placeEdge = (e: string) => {
    const cells = cellsOf.get(e) ?? []
    if (!cells.length) return
    const i = edgeIndex.get(e)
    const from = i !== undefined ? nodePos.get(String(sources[i])) ?? null : null
    const to = i !== undefined ? nodePos.get(String(sinks[i])) ?? null : null
    const owner = pipeOf.get(e)
    const own = owner ? tubes.get(owner) ?? [] : []
    // On a rendered tube: every cell within the tube's radius of its centreline.
    const onTube = new Map<PipeTrack, { c: number; s: number }[]>()
    const offTube: number[] = []
    for (const c of cells) {
      const p = cellPositions[c]
      if (!Array.isArray(p)) continue
      let best: { t: PipeTrack; s: number; d: number } | null = null
      for (const t of own) {
        const hit = projectOnPolyline(t.points, t.cum, p)
        if (hit.d <= Math.max(t.radius, 0.005) && (!best || hit.d < best.d)) best = { t, s: hit.s, d: hit.d }
      }
      if (best) onTube.set(best.t, [...(onTube.get(best.t) ?? []), { c, s: best.s }])
      else offTube.push(c)
    }
    for (const [t, list] of onTube) {
      list.sort((a, b) => a.s - b.s)
      const ss = list.map(x => x.s)
      const total = t.cum[t.cum.length - 1]!
      const nodeS = [from, to].flatMap(n => (n ? [projectOnPolyline(t.points, t.cum, n).s] : []))
      const gap = ss.length > 1 ? (ss[ss.length - 1]! - ss[0]!) / (ss.length - 1) : 0
      const lo = gap > 0 ? ss[0]! - gap / 2 : nodeS.length ? Math.min(...nodeS) : ss[0]! - 0.1
      const hi = gap > 0 ? ss[ss.length - 1]! + gap / 2 : nodeS.length ? Math.max(...nodeS) : ss[0]! + 0.1
      list.forEach(({ c }, k) => {
        const [a, b] = stretch(ss, k, [Math.max(0, lo), Math.min(total, hi)])
        placements.set(c, { key: t, points: t.points, cum: t.cum, s: ss[k]!, sMin: a, sMax: b, radius: t.radius })
      })
    }
    if (offTube.length) {
      // The edge's own polyline, through all its cells in order.
      const points = [...(from ? [from] : []), ...cells.map(c => cellPositions[c]!).filter(Array.isArray), ...(to ? [to] : [])]
      const key = edgePolylines.get(e) ?? {}
      edgePolylines.set(e, key)
      const cum = cumulative(points)
      const offset = from ? 1 : 0
      const ss = cells.map((_c, k) => cum[k + offset]!)
      const ends: [number, number] = [0, cum[cum.length - 1]!]
      const d = diameter(e)
      cells.forEach((c, k) => {
        if (!offTube.includes(c)) return
        let [a, b] = stretch(ss, k, ends)
        if (b - a <= 1e-9) { a = Math.max(0, ss[k]! - 0.05); b = Math.min(ends[1], ss[k]! + 0.05) }
        placements.set(c, { key, points, cum, s: ss[k]!, sMin: a, sMax: b, radius: d ? d / 2 : DEFAULT_RADIUS })
      })
    }
  }
  const placed = new Set<string>()

  const massRange = scales.mass
  const sizeOf = (pipeRadius: number, mass: number) => {
    const n = massRange && massRange[1] > massRange[0] ? Math.max(0, Math.min(1, (mass - massRange[0]) / (massRange[1] - massRange[0]))) : 1
    const r = ARROW_RADIUS_M[0] + (ARROW_RADIUS_M[1] - ARROW_RADIUS_M[0]) * Math.sqrt(n)
    return { radius: r, length: r * ARROW_LENGTH, lift: pipeRadius + 0.6 * r }
  }
  const colourOf = (v: number | null) =>
    opts.arrowColor === 'velocity' && v !== null && scales.velocity ? liquidValueColor(v, scales.velocity, scales.colorMap) : PLAIN_ARROW_COLOR

  for (const a of fa.arrows) {
    if (!placed.has(a.edgeId)) { placed.add(a.edgeId); placeEdge(a.edgeId) }
    const at = placements.get(a.cell)
    if (!at) { spec.skipped.noTrack++; continue }
    if (hidden(a.position)) { spec.skipped.hidden++; continue }
    const tangent = tangentAt(at.points, at.cum, at.s)
    const size = sizeOf(at.radius, a.massFlowKgS)
    spec.arrows.push({
      track: addTrack(at.key, at.points), s: at.s, sMin: at.sMin, sMax: at.sMax, dir: dot(tangent, a.direction) >= 0 ? 1 : -1,
      ...size, color: colourOf(a.velocityMS), speed: a.velocityMS ?? FALLBACK_SPEED,
      kind: a.kind, edgeId: a.edgeId, massFlowKgS: a.massFlowKgS, velocityMS: a.velocityMS,
      position: [...a.position], direction: [...a.direction],
    })
  }

  // Server branches, on the hoses between manifold and server.
  const stubs = new Map<string, THREE.Mesh>()
  root.traverse(o => {
    const d = o.userData
    if ((o as THREE.Mesh).isMesh && d?.isLiquidManifold && d.stubType && typeof d.stubServerId === 'string' && typeof d.modelId === 'string') {
      stubs.set(`${d.modelId}|${d.stubServerId}|${d.stubType}`, o as THREE.Mesh)
    }
  })
  for (const b of fa.branches) {
    if (b.stagnant || b.flowKgS === 0) continue
    const stub = stubs.get(`${b.rackUid}|${b.serverUid}|${b.side}`)
    if (!stub || !b.tap || !stub.visible) { spec.skipped.noStub++; continue }
    const params = (stub.geometry as THREE.CylinderGeometry).parameters
    const h = params?.height
    if (!(typeof h === 'number' && h > 0)) { spec.skipped.noStub++; continue }
    const m = toRoot.clone().multiply(stub.matrixWorld)
    const end = (x: number) => new THREE.Vector3(x, 0, 0).applyMatrix4(m).toArray()
    let [manifold, server] = [end(-h / 2), end(h / 2)]
    if (Math.hypot(...sub(server, b.tap) as [number, number, number]) < Math.hypot(...sub(manifold, b.tap) as [number, number, number])) [manifold, server] = [server, manifold]
    const mid = manifold.map((v, i) => (v + server[i]!) / 2)
    if (hidden(mid)) { spec.skipped.hidden++; continue }
    const length = Math.hypot(...sub(server, manifold) as [number, number, number])
    // Supply runs manifold -> server when the flow is positive, return the other way.
    const outward = b.side === 'supply' ? b.flowKgS > 0 : b.flowKgS < 0
    const size = sizeOf(params.radiusTop ?? 0.008, b.massFlowKgS)
    spec.arrows.push({
      track: addTrack(stub, [manifold, server]), s: length / 2, sMin: length * 0.1, sMax: length * 0.9, dir: outward ? 1 : -1,
      radius: size.radius, length: Math.min(size.length, length * 0.4), lift: size.lift, color: colourOf(b.velocityMS),
      speed: b.velocityMS ?? FALLBACK_SPEED, kind: 'branch', edgeId: b.edgeId, massFlowKgS: b.massFlowKgS,
      velocityMS: b.velocityMS, position: mid, direction: sub(outward ? server : manifold, outward ? manifold : server).map(v => v / length),
    })
  }
  return spec
}
