/**
 * What the liquid network is coloured BY, at one result sample.
 *
 * Pure: the value of every coloured network part and of every pipe cell in one
 * of four quantities. The same rules run in Kit (dc.cfd_bridge liquid_colour.py
 * `quantity_frame`), pinned to the same numbers by the shared contract
 * src/frontend/shared/testdata/liquid_display_expected.json.
 *
 *   temperature  K      the temperature part rules of liquidFrame
 *                       (liquidTempColor.ts); cells: cell_temp_K
 *   flow         L/min  the part flows of liquidFlowFrame (a manifold is the
 *                       sum of its branches, a tee / the CDU the throughput of
 *                       its node); cells: their edge's |flow| / rho
 *   velocity     m/s    liquidFlowFrame velocities; a manifold takes its
 *                       header's; tees and the CDU have no diameter, so no
 *                       velocity, and are left unpainted; cells: their edge's
 *   pressure     kPa    ABSOLUTE, hydrostatic head included (the solver's
 *                       node_pressure_Pa): tee, junction and CDU = their node; pipe,
 *                       manifold and valve = mean over their cells, a cell
 *                       interpolating its edge's end pressures at (k+0.5)/n
 *                       (k-th of n cells from `from`); an edge without cells =
 *                       mean of its two ends
 *
 * A pipe_junction model (a ring-feed tee) is a `junction` part: its binding's
 * node (temperature, pressure; for flow what passes through it).
 *
 * Chips are not part of this: the chip colouring is always chip temperature.
 *
 * The display domain spans the parts AND the cells: Three.js paints the cells
 * along each pipe, so a scale over the part means alone would clip the ends.
 * Kit is sent this domain by the browser, so both viewports share one scale.
 */

import { colormapData, type ColorMap } from '../postprocess/ppColormaps'
import { bindingEdges, liquidFlowFrame, nodeThroughput } from './liquidFlow'
import { liquidFrame } from './liquidColourRules'

type Bag = Record<string, unknown>

export const LIQUID_QUANTITIES = ['temperature', 'flow', 'velocity', 'pressure'] as const
export type LiquidQuantity = typeof LIQUID_QUANTITIES[number]
export const LIQUID_QUANTITY_UNITS: Record<LiquidQuantity, string> = {
  temperature: 'K', flow: 'L/min', velocity: 'm/s', pressure: 'kPa',
}
/**
 * Jet for temperature: coolwarm's blue and red ends are the drawn supply and
 * return pipe colours, so a coloured network looked uncoloured. A sequential
 * map otherwise.
 */
export const LIQUID_DEFAULT_COLORMAPS: Record<LiquidQuantity, ColorMap> = {
  temperature: 'jet', flow: 'viridis', velocity: 'viridis', pressure: 'viridis',
}

export type LiquidPartKind = 'pipe' | 'manifold' | 'tee' | 'valve' | 'cdu' | 'junction'
export interface LiquidDisplayPart {
  kind: LiquidPartKind
  modelUid: string
  equipmentUid: string
  liquidPart: '' | 'supply-manifold' | 'return-manifold'
  value: number
}
export interface LiquidQuantityFrame {
  sample: number
  timeSeconds: number
  quantity: LiquidQuantity
  unit: string
  parts: LiquidDisplayPart[]
  /** One per result cell, in result order; null where the quantity has no value. */
  cells: (number | null)[]
  domain: [number, number] | null
}

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const at = (row: unknown, s: number): number | null => {
  const v = Array.isArray(row) ? (s < row.length ? row[s] : null) : row
  return finite(v) ? v : null
}
const meanOf = (values: number[]) => { let t = 0; for (const v of values) t += v; return t / values.length }
const arr = (raw: Bag, key: string): unknown[] => (Array.isArray(raw[key]) ? (raw[key] as unknown[]) : [])

type PartRef = [LiquidPartKind, string, string, LiquidDisplayPart['liquidPart'], string[] | string]

/** Every coloured network part, in the order of the temperature targets. */
function networkParts(raw: Bag): PartRef[] {
  const edgeIds = new Set(arr(raw, 'edge_ids').map(String))
  const nodeIds = arr(raw, 'node_ids').map(String)
  const bindings = raw.object_bindings && typeof raw.object_bindings === 'object' ? (raw.object_bindings as Record<string, Bag>) : {}
  const out: PartRef[] = []
  const fittings = new Map<string, { pipe: string; vid: string; kind: 'tee' | 'valve'; ref: string[] | string }>()
  for (const [key, b] of Object.entries(bindings)) {
    const edges = bindingEdges(key, b, edgeIds)
    if (b.kind === 'pipe_segment') {
      out.push(['pipe', key, '', '', edges])
      const prefix = `${key}_tee_`
      for (const node of nodeIds) {
        if (!node.startsWith(prefix)) continue
        const vid = node.slice(prefix.length), k = `${key}\u0000${vid}`
        if (!fittings.has(k)) fittings.set(k, { pipe: key, vid, kind: 'tee', ref: node })
      }
    } else if (b.kind === 'valve' && key.includes('/')) {
      const pipe = key.slice(0, key.indexOf('/')), vid = key.slice(key.indexOf('/') + 1)
      fittings.set(`${pipe}\u0000${vid}`, { pipe, vid, kind: 'valve', ref: edges })
    } else if (b.kind === 'rack') {
      const sup = edges.filter(e => e.endsWith('_branch_in')), ret = edges.filter(e => e.endsWith('_branch_out'))
      if (sup.length) out.push(['manifold', key, '', 'supply-manifold', sup])
      if (ret.length) out.push(['manifold', key, '', 'return-manifold', ret])
    } else if (b.kind === 'cdu') {
      out.push(['cdu', key, '', '', `${key}_supply`])
    } else if (b.kind === 'pipe_junction' && Array.isArray(b.node_ids) && b.node_ids.length) {
      out.push(['junction', key, '', '', String(b.node_ids[0])])
    }
  }
  // Sorted like Python's sorted((pipe, vid)) tuples: by pipe, then valve id.
  const keys = [...fittings.keys()].sort((a, b) => {
    const [pa, va] = a.split('\u0000'), [pb, vb] = b.split('\u0000')
    return pa! < pb! ? -1 : pa! > pb! ? 1 : va! < vb! ? -1 : va! > vb! ? 1 : 0
  })
  for (const k of keys) { const f = fittings.get(k)!; out.push([f.kind, f.pipe, f.vid, '', f.ref]) }
  return out
}

function cellPressuresKpa(raw: Bag, s: number): (number | null)[] {
  const cellEdges = arr(raw, 'cell_edge_ids').map(String)
  const edgeIds = arr(raw, 'edge_ids').map(String)
  const sources = raw.edge_from, sinks = raw.edge_to
  if (!Array.isArray(sources) || !Array.isArray(sinks) || sources.length !== edgeIds.length) return cellEdges.map(() => null)
  const nodeIndex = new Map(arr(raw, 'node_ids').map((n, i) => [String(n), i]))
  const rows = arr(raw, 'node_pressure_Pa')
  const edgeIndex = new Map(edgeIds.map((e, i) => [e, i]))
  const nodePa = (node: unknown) => { const i = nodeIndex.get(String(node)); return i === undefined || i >= rows.length ? null : at(rows[i], s) }
  const counts = new Map<string, number>(), rank: number[] = []
  for (const e of cellEdges) { rank.push(counts.get(e) ?? 0); counts.set(e, (counts.get(e) ?? 0) + 1) }
  return cellEdges.map((e, c) => {
    const i = edgeIndex.get(e)
    const pa = i === undefined ? null : nodePa(sources[i]), pb = i === undefined ? null : nodePa(sinks[i])
    return pa === null || pb === null ? null : (pa + (pb - pa) * (rank[c]! + 0.5) / counts.get(e)!) / 1000
  })
}

/** The value of every network part and pipe cell in `quantity` at `sample` (-1 = last). */
export function liquidQuantityFrame(raw: Bag, sample: number, quantity: LiquidQuantity): LiquidQuantityFrame | null {
  const times = arr(raw, 'time_s')
  if (!times.length) return null
  const s = sample < 0 ? times.length - 1 : sample
  if (s >= times.length) return null
  const cellEdges = arr(raw, 'cell_edge_ids').map(String)
  const cellsByEdge = new Map<string, number[]>()
  cellEdges.forEach((e, c) => { const own = cellsByEdge.get(e) ?? []; own.push(c); cellsByEdge.set(e, own) })
  const parts = networkParts(raw)
  let cells: (number | null)[]
  let values: (number | null)[]
  if (quantity === 'temperature') {
    const fr = liquidFrame(raw, s)
    if (!fr) return null
    const key = (k: string, m: string, q: string, p: string) => `${k}|${m}|${q}|${p}`
    const T = new Map(fr.targets.filter(t => t.group === 'network').map(t => [key(t.kind, t.modelUid, t.equipmentUid, t.liquidPart), t.T]))
    cells = arr(raw, 'cell_temp_K').map(row => at(row, s))
    values = parts.map(([k, m, q, p]) => T.get(key(k, m, q, p)) ?? null)
  } else if (quantity === 'flow' || quantity === 'velocity') {
    const fr = liquidFlowFrame(raw, s)
    if (!fr) return null
    const pick = (t: { volumeFlowLpm: number; velocityMS: number | null }) => (quantity === 'flow' ? t.volumeFlowLpm : t.velocityMS)
    cells = fr.cells.map(pick)
    const targets = new Map(fr.targets.map(t => [`${t.kind}|${t.modelUid}|${t.equipmentUid}|${t.liquidPart}`, t]))
    const edges = new Map(fr.edges.map(e => [e.edgeId, e]))
    const bindings = raw.object_bindings && typeof raw.object_bindings === 'object' ? (raw.object_bindings as Record<string, Bag>) : {}
    values = parts.map(([k, m, q, p, ref]) => {
      if (k === 'junction') {
        const through = quantity === 'flow' ? nodeThroughput(fr, ref as string) : null
        return through === null ? null : through / fr.rhoKgM3 * 60000
      }
      if (quantity === 'velocity' && k === 'manifold') {
        const marker = p === 'supply-manifold' ? '_mf_supply' : '_mf_return'
        const speeds = bindingEdges(m, bindings[m] ?? {}, edges)
          .filter(e => e.includes(marker) && !e.endsWith('_branch_in') && !e.endsWith('_branch_out'))
          .flatMap(e => { const v = edges.get(e)?.velocityMS; return v === null || v === undefined ? [] : [v] })
        return speeds.length ? meanOf(speeds) : null
      }
      const t = targets.get(`${k}|${m}|${q}|${p}`)
      return t ? pick(t) : null
    })
  } else {
    cells = cellPressuresKpa(raw, s)
    const edgeIds = arr(raw, 'edge_ids').map(String)
    const edgeIndex = new Map(edgeIds.map((e, i) => [e, i]))
    const sources = arr(raw, 'edge_from'), sinks = arr(raw, 'edge_to')
    const nodeIndex = new Map(arr(raw, 'node_ids').map((n, i) => [String(n), i]))
    const rows = arr(raw, 'node_pressure_Pa')
    const nodeKpa = (node: unknown) => {
      const i = nodeIndex.get(String(node))
      const v = i === undefined || i >= rows.length ? null : at(rows[i], s)
      return v === null ? null : v / 1000
    }
    const edgeKpa = (list: string[]) => {
      const found: number[] = []
      for (const e of list) for (const c of cellsByEdge.get(e) ?? []) { const v = cells[c]; if (v !== null && v !== undefined) found.push(v) }
      if (!found.length) {
        for (const e of list) {
          const i = edgeIndex.get(e)
          if (i === undefined || i >= sources.length || i >= sinks.length) continue
          const a = nodeKpa(sources[i]), b = nodeKpa(sinks[i])
          if (a !== null && b !== null) found.push((a + b) / 2)
        }
      }
      return found.length ? meanOf(found) : null
    }
    values = parts.map(([k, , , , ref]) => (k === 'cdu' || k === 'tee' || k === 'junction' ? nodeKpa(ref) : edgeKpa(ref as string[])))
  }
  const out: LiquidDisplayPart[] = []
  parts.forEach(([kind, modelUid, equipmentUid, liquidPart], i) => {
    const value = values[i]
    if (value !== null && value !== undefined) out.push({ kind, modelUid, equipmentUid, liquidPart, value })
  })
  let lo = Infinity, hi = -Infinity
  for (const v of [...out.map(p => p.value), ...cells.filter((v): v is number => v !== null)]) { if (v < lo) lo = v; if (v > hi) hi = v }
  return {
    sample: s, timeSeconds: Number(times[s]), quantity, unit: LIQUID_QUANTITY_UNITS[quantity],
    parts: out, cells, domain: lo <= hi ? [lo, hi] : null,
  }
}

/** liquidQuantityFrame domains over EVERY sample: the default, so an animation shows the change over time. */
export function liquidQuantityGlobalDomain(raw: Bag, quantity: LiquidQuantity): [number, number] | null {
  let out: [number, number] | null = null
  const count = arr(raw, 'time_s').length
  for (let s = 0; s < count; s++) {
    const d = liquidQuantityFrame(raw, s, quantity)?.domain
    if (d) out = out ? [Math.min(out[0], d[0]), Math.max(out[1], d[1])] : [d[0], d[1]]
  }
  return out
}

/** A scale Kit accepts (it refuses lo == hi); a uniform field keeps its single colour. */
export function displayDomain(domain: readonly [number, number] | null | undefined): [number, number] | null {
  if (!domain) return null
  return domain[1] > domain[0] ? [domain[0], domain[1]] : [domain[0], domain[0] + 1e-6]
}

const luts = new Map<ColorMap, Uint8Array>()
/** tempToColor for any scalar and colour map, as a packed 0xRRGGBB (Kit: value_to_rgb8). */
export function liquidValueColor(value: number, domain: readonly [number, number], colorMap: ColorMap): number {
  let lut = luts.get(colorMap)
  if (!lut) { lut = colormapData(colorMap); luts.set(colorMap, lut) }
  const t = Math.max(0, Math.min(1, (value - domain[0]) / (domain[1] - domain[0] + 1e-6)))
  const i = Math.floor(t * 255) * 4
  return ((lut[i] as number) << 16) | ((lut[i + 1] as number) << 8) | (lut[i + 2] as number)
}
