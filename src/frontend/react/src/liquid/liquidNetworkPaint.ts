/**
 * Paints the liquid network in the Three.js scene from a result.
 *
 * Parts get the colour of their display-quantity value (liquidDisplay.ts, the
 * rules Kit applies too). A rendered pipe tube is coloured ALONG ITS LENGTH:
 * every result cell of the pipe is placed on the tube's own centreline by
 * arc length, and each vertex takes the value interpolated between the cells
 * on either side of it — no longer one colour for a whole pipe whose water
 * warms from end to end. Cells further than the tube's radius from its
 * centreline belong to other geometry (a valve's spoke, another polyline
 * piece) and are left out; a tube with no cell on it takes its part colour.
 *
 * The centreline is the renderer's own (`userData._pipeDisplaySource`, the
 * path the tube was swept along, kept by liquid_pipe_renderer.js), so a tube
 * clipped at a closed cabinet keeps its colours where it is still drawn.
 */

import * as THREE from 'three'
import { colormapData, type ColorMap } from '../postprocess/ppColormaps'
import { liquidQuantityFrame, liquidValueColor } from './liquidDisplay'
import type { NetworkScale } from './liquidDisplayState'
import { bindingEdges } from './liquidFlow'
import type { MeshColorOverlay } from './meshColorOverlay'

type Bag = Record<string, unknown>

export interface PipeTrack {
  mesh: THREE.Mesh
  pipeUid: string
  /** Centreline samples in the coordinates of the scene root (the result's frame). */
  points: number[][]
  /** Arc length at each sample. */
  cum: number[]
  radius: number
  /** The same samples in the mesh's own frame, for its vertices. */
  local: number[][]
  segments: number
}

interface DisplaySource { context?: { path?: THREE.Curve<THREE.Vector3>; segments?: number }; radius?: number }

const tracks = new WeakMap<object, PipeTrack>()

function cumulative(points: number[][]): number[] {
  const cum = [0]
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!, b = points[i]!
    cum.push(cum[i - 1]! + Math.hypot(b[0]! - a[0]!, b[1]! - a[1]!, b[2]! - a[2]!))
  }
  return cum
}

/** The rendered pipe tubes under `root`, with their centrelines. */
export function pipeTracks(root: THREE.Object3D): PipeTrack[] {
  root.updateWorldMatrix(true, true)
  const toRoot = root.matrixWorld.clone().invert()
  const out: PipeTrack[] = []
  root.traverse(o => {
    const mesh = o as THREE.Mesh
    const source = mesh.userData?._pipeDisplaySource as DisplaySource | undefined
    const uid = mesh.userData?.pipeModelId
    if (!mesh.isMesh || !source || typeof uid !== 'string') return
    let track = tracks.get(source)
    if (!track || track.mesh !== mesh) {
      const path = source.context?.path
      const segments = Math.max(8, Math.round(source.context?.segments ?? 64))
      if (!path || typeof path.getSpacedPoints !== 'function') return
      const local = path.getSpacedPoints(segments).map(p => [p.x, p.y, p.z])
      const matrix = toRoot.clone().multiply(mesh.matrixWorld)
      const points = local.map(([x, y, z]) => new THREE.Vector3(x, y, z).applyMatrix4(matrix).toArray())
      track = { mesh, pipeUid: uid, points, cum: cumulative(points), radius: source.radius ?? 0.0125, local, segments }
      tracks.set(source, track)
    }
    out.push(track)
  })
  return out
}

/** Arc length of the point of the polyline nearest `p`, and that distance. */
export function projectOnPolyline(points: number[][], cum: number[], p: readonly number[]): { s: number; d: number } {
  let best = Infinity, bestS = 0
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!, b = points[i]!
    const ex = b[0]! - a[0]!, ey = b[1]! - a[1]!, ez = b[2]! - a[2]!
    const len2 = ex * ex + ey * ey + ez * ez
    let t = len2 > 0 ? ((p[0]! - a[0]!) * ex + (p[1]! - a[1]!) * ey + (p[2]! - a[2]!) * ez) / len2 : 0
    t = Math.max(0, Math.min(1, t))
    const dx = a[0]! + ex * t - p[0]!, dy = a[1]! + ey * t - p[1]!, dz = a[2]! + ez * t - p[2]!
    const d2 = dx * dx + dy * dy + dz * dz
    if (d2 < best) { best = d2; bestS = cum[i - 1]! + (cum[i]! - cum[i - 1]!) * t }
  }
  return { s: bestS, d: Math.sqrt(best) }
}

const vertexArcs = new WeakMap<THREE.BufferGeometry, { key: PipeTrack; s: Float32Array }>()
/** Arc length of every vertex of the tube's current geometry along its centreline. */
export function vertexArcLengths(track: PipeTrack, geometry: THREE.BufferGeometry): Float32Array {
  const cached = vertexArcs.get(geometry)
  if (cached && cached.key === track) return cached.s
  const position = geometry.getAttribute('position')
  const n = position.count
  const out = new Float32Array(n)
  const params = (geometry as unknown as { parameters?: { tubularSegments?: number; radialSegments?: number } }).parameters
  const tubular = params?.tubularSegments, radial = params?.radialSegments
  const localCum = cumulative(track.local)
  if (tubular === track.segments && radial !== undefined && n === (tubular + 1) * (radial + 1)) {
    // A TubeGeometry: ring i was swept at getPointAt(i / tubularSegments),
    // the i-th spaced sample of the same path.
    for (let v = 0; v < n; v++) out[v] = localCum[Math.floor(v / (radial + 1))]!
  } else {
    // A clipped display geometry: project each vertex onto the centreline.
    const p = [0, 0, 0]
    for (let v = 0; v < n; v++) {
      p[0] = position.getX(v); p[1] = position.getY(v); p[2] = position.getZ(v)
      out[v] = projectOnPolyline(track.local, localCum, p).s
    }
  }
  vertexArcs.set(geometry, { key: track, s: out })
  return out
}

/** The result's identity for its static layout: cell positions do not move within a run. */
function layoutKey(raw: Bag): string {
  const cells = Array.isArray(raw.cell_edge_ids) ? raw.cell_edge_ids.length : 0
  const first = Array.isArray(raw.cell_positions) ? JSON.stringify(raw.cell_positions[0] ?? null) : ''
  return `${String(raw.run_id ?? '')}|${cells}|${first}`
}

const onTrack = new WeakMap<PipeTrack, { key: string; cells: { cell: number; s: number }[] }>()
/** The result cells of the tube's pipe that lie on this tube, by arc length. */
export function cellsOnTrack(track: PipeTrack, raw: Bag): { cell: number; s: number }[] {
  const key = layoutKey(raw)
  const cached = onTrack.get(track)
  if (cached && cached.key === key) return cached.cells
  const bindings = raw.object_bindings && typeof raw.object_bindings === 'object' ? (raw.object_bindings as Record<string, Bag>) : {}
  const known = new Set((Array.isArray(raw.edge_ids) ? raw.edge_ids : []).map(String))
  const binding = bindings[track.pipeUid]
  const edges = new Set(binding ? bindingEdges(track.pipeUid, binding, known) : [])
  const cellEdges = Array.isArray(raw.cell_edge_ids) ? raw.cell_edge_ids : []
  const positions = Array.isArray(raw.cell_positions) ? raw.cell_positions : []
  const tolerance = Math.max(track.radius, 0.005)
  const cells: { cell: number; s: number }[] = []
  cellEdges.forEach((e, c) => {
    const p = positions[c]
    if (!edges.has(String(e)) || !Array.isArray(p)) return
    const hit = projectOnPolyline(track.points, track.cum, p as number[])
    if (hit.d <= tolerance) cells.push({ cell: c, s: hit.s })
  })
  cells.sort((a, b) => a.s - b.s)
  onTrack.set(track, { key, cells })
  return cells
}

/** Linear interpolation of (s, value) samples sorted by s; clamped at the ends. */
export function interpolateAlong(samples: readonly { s: number; v: number }[], s: number): number {
  const n = samples.length
  if (s <= samples[0]!.s) return samples[0]!.v
  if (s >= samples[n - 1]!.s) return samples[n - 1]!.v
  let lo = 0, hi = n - 1
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (samples[mid]!.s <= s) lo = mid; else hi = mid }
  const a = samples[lo]!, b = samples[hi]!
  return b.s > a.s ? a.v + (b.v - a.v) * (s - a.s) / (b.s - a.s) : a.v
}

const linearLuts = new Map<ColorMap, Float32Array>()
/** liquidValueColor, as linear RGB (vertex colours are linear; setHex converts like a material colour). */
function linearLut(colorMap: ColorMap, domain: readonly [number, number]): (value: number) => [number, number, number] {
  let lut = linearLuts.get(colorMap)
  if (!lut) {
    const bytes = colormapData(colorMap)
    lut = new Float32Array(256 * 3)
    const c = new THREE.Color()
    for (let i = 0; i < 256; i++) {
      c.setHex((bytes[i * 4]! << 16) | (bytes[i * 4 + 1]! << 8) | bytes[i * 4 + 2]!)
      lut[i * 3] = c.r; lut[i * 3 + 1] = c.g; lut[i * 3 + 2] = c.b
    }
    linearLuts.set(colorMap, lut)
  }
  const table = lut
  const [lo, hi] = domain
  return (value: number) => {
    const t = Math.max(0, Math.min(1, (value - lo) / (hi - lo + 1e-6)))
    const i = Math.floor(t * 255) * 3
    return [table[i]!, table[i + 1]!, table[i + 2]!]
  }
}

/** The per-vertex colours of one tube, or null when no cell of its pipe lies on it. */
export function tubeVertexColors(track: PipeTrack, raw: Bag, cellValues: readonly (number | null)[],
  scale: Pick<NetworkScale, 'domain' | 'colorMap'>): Float32Array | null {
  if (!scale.domain) return null
  const samples = cellsOnTrack(track, raw).flatMap(({ cell, s }) => {
    const v = cellValues[cell]
    return v === null || v === undefined || !Number.isFinite(v) ? [] : [{ s, v }]
  })
  if (!samples.length) return null
  const arcs = vertexArcLengths(track, track.mesh.geometry)
  const colour = linearLut(scale.colorMap, scale.domain)
  const out = new Float32Array(arcs.length * 3)
  for (let i = 0; i < arcs.length; i++) {
    const [r, g, b] = colour(interpolateAlong(samples, arcs[i]!))
    out[i * 3] = r; out[i * 3 + 1] = g; out[i * 3 + 2] = b
  }
  return out
}

/** The tee/valve id on the nearest ancestor below `root` that is a fitting body. */
function fittingOf(o: THREE.Object3D, root: THREE.Object3D): string | null {
  for (let p = o.parent; p && p !== root; p = p.parent) {
    if (p.userData?.isValve === true && typeof p.userData.valveId === 'string') return p.userData.valveId
  }
  return null
}

/**
 * Paint the network at `sample` on `scale`. Returns how many meshes were
 * painted; meshes this overlay painted before and that have no value now
 * (a tee under "velocity") get their own material back.
 */
export function paintLiquidNetwork(root: THREE.Object3D, raw: Bag, sample: number, scale: NetworkScale,
  overlay: MeshColorOverlay): number {
  const frame = liquidQuantityFrame(raw, sample, scale.quantity)
  const keep = new Set<THREE.Mesh>()
  if (frame && scale.domain) {
    const colour = (v: number) => liquidValueColor(v, scale.domain!, scale.colorMap)
    const pipes = new Map<string, number>(), manifolds = new Map<string, number>(), fittings = new Map<string, number>(), cdus = new Map<string, number>()
    const junctions = new Map<string, number>()
    for (const p of frame.parts) {
      if (p.kind === 'pipe') pipes.set(p.modelUid, colour(p.value))
      else if (p.kind === 'junction') junctions.set(p.modelUid, colour(p.value))
      else if (p.kind === 'manifold') manifolds.set(`${p.modelUid}|${p.liquidPart}`, colour(p.value))
      else if (p.kind === 'tee' || p.kind === 'valve') fittings.set(p.equipmentUid, colour(p.value))
      else if (p.kind === 'cdu') cdus.set(p.modelUid, colour(p.value))
    }
    const byMesh = new Map(pipeTracks(root).map(t => [t.mesh, t]))
    root.traverse(child => {
      const mesh = child as THREE.Mesh
      const material = mesh.material as THREE.MeshStandardMaterial | undefined
      if (!mesh.isMesh || !material) return
      const d = child.userData
      // A tee or valve body is a group of meshes; the id is on the group
      // (liquid_mode.js), so it is looked up through the parents.
      const fittingId = typeof d.valveId === 'string' ? d.valveId : fittingOf(child, root)
      const track = typeof d.pipeModelId === 'string' && fittingId === null ? byMesh.get(mesh) : undefined
      if (track) {
        const colours = tubeVertexColors(track, raw, frame.cells, scale)
        if (colours) { overlay.paintVertices(mesh, colours); keep.add(mesh); return }
      }
      if (!material.color) return
      const side = d.isLiquidManifold ? (d.pipeType ?? d.stubType) : undefined
      // A junction model's body and legs carry junctionId (its port spheres are helpers).
      const junction = typeof d.junctionId === 'string' && !d.isJunctionPort ? junctions.get(d.junctionId) : undefined
      const c = junction !== undefined ? junction
        : fittingId !== null ? fittings.get(fittingId)
        : typeof d.pipeModelId === 'string' ? pipes.get(d.pipeModelId)
          : side && typeof d.modelId === 'string' ? manifolds.get(`${d.modelId}|${side === 'supply' ? 'supply-manifold' : 'return-manifold'}`)
            : typeof d.modelId === 'string' && !d.equipmentId ? cdus.get(d.modelId) : undefined
      if (c === undefined) return
      overlay.paint(mesh, c)
      keep.add(mesh)
    })
  }
  overlay.retain(keep)
  return keep.size
}
