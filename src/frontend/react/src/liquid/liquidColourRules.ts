/**
 * The temperature colour rules of a liquid result, pure (no scene, no store).
 *
 * Moved out of liquidTempColor.ts (which re-exports them) so the display
 * quantity rules (liquidDisplay.ts) and the scene painter can share them
 * without an import cycle. Omniverse applies the same rules (kit
 * dc.cfd_bridge liquid_colour.py), pinned by the shared contract
 * src/frontend/shared/testdata/liquid_colour_expected.json.
 */

import { colormapData, type ColorMap } from '../postprocess/ppColormaps'
import { bindingEdges } from './liquidFlow'

/** `_lcCmapPreset` (js/liquid_pipe_renderer.js:743). */
export const CMAP: ColorMap = 'coolwarm'

type Bag = Record<string, unknown>

/** Auto-scale bounds. An empty set has no range, which the callers treat as "nothing to paint". */
export function valueRange(values: number[]): { min: number; max: number } | null {
  if (values.length === 0) return null
  let min = values[0] as number
  let max = values[0] as number
  for (const v of values) {
    if (v < min) min = v
    if (v > max) max = v
  }
  return { min, max }
}

/**
 * `_tempToColor` (js/liquid_pipe_renderer.js:747), as a packed 0xRRGGBB.
 *
 * The `+1e-6` in the denominator is the legacy's, and it is load-bearing: a
 * uniform field has `Tmax === Tmin` and would otherwise divide by zero, giving
 * NaN and a black scene instead of one flat colour.
 */
export function tempToColor(T: number, tMin: number, tMax: number, lut: Uint8Array): number {
  const t = Math.max(0, Math.min(1, (T - tMin) / (tMax - tMin + 1e-6)))
  const i = Math.floor(t * 255) * 4
  return ((lut[i] as number) << 16) | ((lut[i + 1] as number) << 8) | (lut[i + 2] as number)
}

export interface RackSummaryNames {
  rack_id: string
  servers: { server_id: string; cp_edge_ids?: string[] }[]
}

export type LiquidTargetKind = 'pipe' | 'manifold' | 'tee' | 'valve' | 'cdu' | 'junction' | 'server'
export interface LiquidTarget {
  kind: LiquidTargetKind; modelUid: string; equipmentUid: string
  liquidPart: '' | 'supply-manifold' | 'return-manifold'; group: 'network' | 'chips'; T: number; color: number
}
export interface LiquidFrameColours {
  sample: number
  domains: Partial<Record<'network' | 'chips', [number, number]>>
  targets: LiquidTarget[]
}

/**
 * Every liquid part's temperature and colour at one sample — the SAME rules
 * Omniverse applies (kit dc.cfd_bridge liquid_colour.py), pinned by the shared
 * contract src/frontend/shared/testdata/liquid_colour_expected.json:
 * pipes = mean cell T over the binding's edges; supply/return manifold = mean
 * over the rack's *_branch_in / *_branch_out edges; tee = node
 * <pipe>_tee_<id>; flow valve = its <pipe>/<id> binding edge (wins over the
 * tee node); CDU = node <cdu>_supply; server = hottest chip, joined by cold
 * plate edge id; a pipe_junction (ring-feed tee) = its binding's node. Water
 * parts share one scale; chips scale over every chip.
 */
export function liquidFrame(raw: Bag, sample = -1): LiquidFrameColours | null {
  const times = Array.isArray(raw.time_s) ? raw.time_s : []
  const bindings = raw.object_bindings && typeof raw.object_bindings === 'object' ? raw.object_bindings as Record<string, Bag> : null
  if (!times.length || !bindings || !Array.isArray(raw.cell_edge_ids)) return null
  const s = sample < 0 ? times.length - 1 : sample
  if (s >= times.length) return null
  const at = (row: unknown): number | undefined => {
    const v = Array.isArray(row) ? row[s] : row
    return typeof v === 'number' && Number.isFinite(v) ? v : undefined
  }
  const cellRows = Array.isArray(raw.cell_temp_K) ? raw.cell_temp_K : []
  const cellsByEdge = new Map<string, number[]>()
  ;(raw.cell_edge_ids as unknown[]).forEach((e, i) => { const k = String(e); cellsByEdge.set(k, [...(cellsByEdge.get(k) ?? []), i]) })
  const index = (list: unknown) => new Map((Array.isArray(list) ? list : []).map((v, i) => [String(v), i]))
  const edgeIndex = index(raw.edge_ids), nodeIndex = index(raw.node_ids)
  const cpIndex = new Map((Array.isArray(raw.cp_details) ? raw.cp_details as Bag[] : []).map((c, i) => [String(c.edge_id), i]))
  const edgeRows = Array.isArray(raw.edge_outlet_temp_K) ? raw.edge_outlet_temp_K : []
  const nodeRows = Array.isArray(raw.node_temperature_K) ? raw.node_temperature_K : []
  const chipRows = Array.isArray(raw.chip_temp_K) ? raw.chip_temp_K : []
  const mean = (v: number[]) => v.length ? v.reduce((a, b) => a + b, 0) / v.length : undefined
  const edgeMean = (edges: unknown[]) => {
    const cells = edges.flatMap(e => (cellsByEdge.get(String(e)) ?? []).map(c => at(cellRows[c])).filter((v): v is number => v !== undefined))
    if (cells.length) return mean(cells)
    return mean(edges.map(e => { const i = edgeIndex.get(String(e)); return i === undefined ? undefined : at(edgeRows[i]) }).filter((v): v is number => v !== undefined))
  }
  const nodeT = (n: string) => { const i = nodeIndex.get(n); return i === undefined ? undefined : at(nodeRows[i]) }
  type Pending = Omit<LiquidTarget, 'color' | 'T'> & { T: number | undefined }
  const out: Pending[] = []
  const fittings = new Map<string, { pipe: string; vid: string; T: () => number | undefined; edge: boolean }>()
  for (const [key, b] of Object.entries(bindings)) {
    // An untee'd pipe's one edge is named after it (liquidFlow.bindingEdges).
    const edges: unknown[] = bindingEdges(key, b, edgeIndex)
    if (b.kind === 'pipe_segment') {
      out.push({ kind: 'pipe', modelUid: key, equipmentUid: '', liquidPart: '', group: 'network', T: edgeMean(edges) })
      const prefix = `${key}_tee_`
      for (const node of nodeIndex.keys()) {
        if (!node.startsWith(prefix)) continue
        const vid = node.slice(prefix.length), k = `${key}\u0000${vid}`
        if (!fittings.has(k)) fittings.set(k, { pipe: key, vid, T: () => nodeT(node), edge: false })
      }
    } else if (b.kind === 'valve' && key.includes('/')) {
      const [pipe, vid] = [key.slice(0, key.indexOf('/')), key.slice(key.indexOf('/') + 1)]
      fittings.set(`${pipe}\u0000${vid}`, { pipe, vid, T: () => edgeMean(edges), edge: true })
    } else if (b.kind === 'rack') {
      const sup = edges.filter(e => String(e).endsWith('_branch_in')), ret = edges.filter(e => String(e).endsWith('_branch_out'))
      if (sup.length) out.push({ kind: 'manifold', modelUid: key, equipmentUid: '', liquidPart: 'supply-manifold', group: 'network', T: edgeMean(sup) })
      if (ret.length) out.push({ kind: 'manifold', modelUid: key, equipmentUid: '', liquidPart: 'return-manifold', group: 'network', T: edgeMean(ret) })
    } else if (b.kind === 'cdu') {
      out.push({ kind: 'cdu', modelUid: key, equipmentUid: '', liquidPart: '', group: 'network', T: nodeT(`${key}_supply`) })
    } else if (b.kind === 'pipe_junction' && Array.isArray(b.node_ids) && b.node_ids.length) {
      // A ring-feed tee model: its binding's node.
      out.push({ kind: 'junction', modelUid: key, equipmentUid: '', liquidPart: '', group: 'network', T: nodeT(String(b.node_ids[0])) })
    }
  }
  for (const k of [...fittings.keys()].sort()) {
    const f = fittings.get(k)!
    out.push({ kind: f.edge ? 'valve' : 'tee', modelUid: f.pipe, equipmentUid: f.vid, liquidPart: '', group: 'network', T: f.T() })
  }
  for (const rack of (Array.isArray(raw.rack_summaries) ? raw.rack_summaries as RackSummaryNames[] : [])) {
    for (const srv of rack.servers ?? []) {
      const chips = (srv.cp_edge_ids ?? []).map(e => { const i = cpIndex.get(String(e)); return i === undefined ? undefined : at(chipRows[i]) }).filter((v): v is number => v !== undefined)
      out.push({ kind: 'server', modelUid: String(rack.rack_id), equipmentUid: String(srv.server_id), liquidPart: '', group: 'chips', T: chips.length ? Math.max(...chips) : undefined })
    }
  }
  const known = out.filter((t): t is Pending & { T: number } => t.T !== undefined)
  const domains: LiquidFrameColours['domains'] = {}
  const net = valueRange(known.filter(t => t.group === 'network').map(t => t.T))
  if (net) domains.network = [net.min, net.max]
  const chipRange = valueRange(chipRows.map(at).filter((v): v is number => v !== undefined))
  if (chipRange) domains.chips = [chipRange.min, chipRange.max]
  const lut = colormapData(CMAP)
  return { sample: s, domains, targets: known.map(t => {
    const d = domains[t.group]!
    return { ...t, color: tempToColor(t.T, d[0], d[1], lut) }
  }) }
}

/** Min..max of each scale over EVERY sample: a fixed colour domain, so an
 * animation shows the time change instead of re-normalising each frame. */
export function liquidGlobalDomains(raw: Bag): LiquidFrameColours['domains'] {
  const times = Array.isArray(raw.time_s) ? raw.time_s.length : 0
  const out: LiquidFrameColours['domains'] = {}
  for (let s = 0; s < times; s++) {
    const fr = liquidFrame(raw, s)
    for (const g of ['network', 'chips'] as const) {
      const d = fr?.domains[g]
      if (!d) continue
      const cur = out[g]
      out[g] = cur ? [Math.min(cur[0], d[0]), Math.max(cur[1], d[1])] : [d[0], d[1]]
    }
  }
  return out
}

