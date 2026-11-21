/**
 * Liquid-cooling flow quantities and directions at one result sample.
 *
 * The flow counterpart of `liquidFrame` (liquidTempColor.ts): the same rules
 * Omniverse applies (kit dc.cfd_bridge liquid_flow.py), pinned to the same
 * numbers by the shared contract
 * src/frontend/shared/testdata/liquid_flow_expected.json. Pure: no scene, no
 * store, no colour — renderers and panels build on it.
 *
 * Signs. `edge_flow_kg_s` is positive from `edge_from` to `edge_to`. What is
 * shown follows the ACTUAL flow: upstream is `from` when the flow is >= 0
 * (the solver's convention) and `to` otherwise; `reversed` marks flow against
 * the modelled direction. Stagnant edges (`edge_stagnant`) have no flow, no
 * arrow and no upstream; the solver's ~1e-31 residue on them is not shown.
 *
 * Cells. A pipe edge's cells run from `from` to `to` along its waypoints
 * (graphcompiling/compiler.py). A cell's arrow is the unit difference of its
 * neighbouring cells (one-sided at the ends; from-node to to-node for a
 * one-cell edge) times sign(flow).
 *
 * Part flow scalars (|flow|, kg/s), joined like the temperature rules:
 *   pipe            cell-weighted mean over the binding's edges (edges
 *                   without cells: mean over those edges)
 *   supply/return   SUM over the rack's `*_branch_in` / `*_branch_out` edges
 *   manifold        (parallel branches add up to the manifold flow)
 *   tee             throughput of node `<pipe>_tee_<id>` (what flows in)
 *   flow valve      binding `<pipe>/<valve id>` edges (wins over the tee)
 *   CDU             throughput of node `<cdu>_supply`
 *   pump / hx       each pump / heat-exchanger edge of a CDU binding
 *   server          largest cold-plate flow, joined by cp edge id
 * Volume flow is |flow|/rho in L/min; velocity is |flow|/(rho·πD²/4) for pipes
 * and valves (edges with `edge_D_m`), otherwise null.
 *
 * A `pipe_segment` binding that lists no edges owns the edge named after its
 * uid (`bindingEdges`): a pipe without tees compiles to ONE edge whose id is
 * the pipe uid, and results compiled before 2026-10-22 left that binding empty.
 *
 * `liquidFlowArrows` lays the flow arrows out on the same data; both viewports
 * draw them (Three.js here, Kit from liquid_flow.flow_arrows).
 */

type Bag = Record<string, unknown>
export type Vec3 = [number, number, number]

export interface LiquidFlowEdge {
  edgeId: string
  type: string | null
  from: string | null
  to: string | null
  /** As solved: positive from `from` to `to`. */
  flowKgS: number
  /** |flow|; 0 on a stagnant edge. */
  massFlowKgS: number
  volumeFlowLpm: number
  velocityMS: number | null
  diameterM: number | null
  upstream: string | null
  downstream: string | null
  reversed: boolean
  stagnant: boolean
}

export interface LiquidFlowCell {
  edgeId: string
  position: number[] | null
  /** Unit vector along the actual flow; [0,0,0] when stagnant, zero or undefined. */
  direction: Vec3
  massFlowKgS: number
  volumeFlowLpm: number
  velocityMS: number | null
  stagnant: boolean
}

export type LiquidFlowTargetKind = 'pipe' | 'manifold' | 'tee' | 'valve' | 'cdu' | 'pump' | 'hx' | 'server'
export type LiquidFlowGroup = 'network' | 'servers'
export interface LiquidFlowTarget {
  kind: LiquidFlowTargetKind
  modelUid: string
  equipmentUid: string
  liquidPart: '' | 'supply-manifold' | 'return-manifold'
  group: LiquidFlowGroup
  massFlowKgS: number
  volumeFlowLpm: number
  velocityMS: number | null
}

export type LiquidFlowDomains = Partial<Record<'network' | 'servers' | 'cells' | 'velocity', [number, number]>>

export interface LiquidFlowFrame {
  sample: number
  timeSeconds: number
  rhoKgM3: number
  domains: LiquidFlowDomains
  /** One per result cell, in result order (index = cell index). */
  cells: LiquidFlowCell[]
  edges: LiquidFlowEdge[]
  targets: LiquidFlowTarget[]
  stagnantEdges: string[]
  unobservableNodes: string[]
}

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const list = (raw: Bag, key: string): unknown[] | null => (Array.isArray(raw[key]) ? (raw[key] as unknown[]) : null)
const at = (row: unknown, s: number): number | null => {
  const v = Array.isArray(row) ? (s < row.length ? row[s] : null) : row
  return finite(v) ? v : null
}
const mean = (values: number[]) => { let t = 0; for (const v of values) t += v; return t / values.length }
const sum = (values: number[]) => { let t = 0; for (const v of values) t += v; return t }
const range = (values: number[]): [number, number] | null => {
  if (!values.length) return null
  let lo = values[0]!, hi = values[0]!
  for (const v of values) { if (v < lo) lo = v; if (v > hi) hi = v }
  return [lo, hi]
}
const lpm = (mass: number, rho: number) => mass / rho * 60000
const velocity = (mass: number, rho: number, d: number | null) => (d === null ? null : mass / (rho * (Math.PI * d * d / 4)))

/** The edges a binding owns; see the module comment for the untee'd pipe rule. */
export function bindingEdges(key: string, binding: Bag, knownEdges: { has(id: string): boolean }): string[] {
  const edges = (Array.isArray(binding.edge_ids) ? binding.edge_ids : []).map(String)
  if (!edges.length && binding.kind === 'pipe_segment' && knownEdges.has(key)) return [key]
  return edges
}

/** Flow of every edge, cell and liquid part at `sample` (-1 = last), or null
 * when the result has no flows, no positive density or no such sample. */
export function liquidFlowFrame(raw: Bag, sample = -1): LiquidFlowFrame | null {
  const times = list(raw, 'time_s') ?? []
  const flows = list(raw, 'edge_flow_kg_s')
  const edgeIds = list(raw, 'edge_ids')
  const fluid = raw.fluid && typeof raw.fluid === 'object' ? (raw.fluid as Bag) : {}
  const rho = fluid.rho_kg_m3
  if (!times.length || !flows || !edgeIds || !finite(rho) || rho <= 0) return null
  const s = sample < 0 ? times.length - 1 : sample
  if (s >= times.length) return null

  const sources = list(raw, 'edge_from'), sinks = list(raw, 'edge_to')
  const types = list(raw, 'edge_types'), diameters = list(raw, 'edge_D_m'), stagnantFlags = list(raw, 'edge_stagnant')
  const nodeIds = (list(raw, 'node_ids') ?? []).map(String)
  const nodePositions = list(raw, 'node_positions_m') ?? []
  const nodePos = new Map(nodeIds.map((n, i) => [n, nodePositions[i] as number[] | undefined]))
  const topology = !!sources && !!sinks && sources.length === edgeIds.length && sinks.length === edgeIds.length

  const edges: LiquidFlowEdge[] = []
  const byId = new Map<string, LiquidFlowEdge>()
  edgeIds.forEach((rawId, i) => {
    const m = i < flows.length ? at(flows[i], s) : null
    if (m === null) return
    const edgeId = String(rawId)
    const stagnant = stagnantFlags !== null && i < stagnantFlags.length ? Boolean(stagnantFlags[i]) : false
    const d = diameters !== null && i < diameters.length ? diameters[i] : null
    const diameter = finite(d) && d > 0 ? d : null
    const mass = stagnant ? 0 : Math.abs(m)
    const from = topology ? String(sources![i]) : null
    const to = topology ? String(sinks![i]) : null
    const moving = !stagnant && topology
    const entry: LiquidFlowEdge = {
      edgeId, type: types !== null && i < types.length ? String(types[i]) : null,
      from, to, flowKgS: m, massFlowKgS: mass,
      volumeFlowLpm: lpm(mass, rho), velocityMS: velocity(mass, rho, diameter), diameterM: diameter,
      upstream: moving ? (m >= 0 ? from : to) : null, downstream: moving ? (m >= 0 ? to : from) : null,
      reversed: !stagnant && m < 0, stagnant,
    }
    edges.push(entry)
    byId.set(edgeId, entry)
  })

  const cellEdges = (list(raw, 'cell_edge_ids') ?? []).map(String)
  const positions = list(raw, 'cell_positions') ?? []
  const cellsByEdge = new Map<string, number[]>()
  const rank: number[] = []
  cellEdges.forEach((e, c) => {
    const own = cellsByEdge.get(e) ?? []
    rank.push(own.length)
    own.push(c)
    cellsByEdge.set(e, own)
  })
  const cells: LiquidFlowCell[] = cellEdges.map((e, c) => {
    const own = cellsByEdge.get(e)!
    const k = rank[c]!
    const p = Array.isArray(positions[c]) ? (positions[c] as number[]) : null
    const info = byId.get(e)
    let a: number[] | null | undefined = null, b: number[] | null | undefined = null
    if (own.length >= 2) {
      a = k > 0 ? (positions[own[k - 1]!] as number[]) : p
      b = k < own.length - 1 ? (positions[own[k + 1]!] as number[]) : p
    } else if (info && info.from !== null && info.to !== null && nodePos.get(info.from) && nodePos.get(info.to)) {
      a = nodePos.get(info.from); b = nodePos.get(info.to)
    }
    let direction: Vec3 = [0, 0, 0]
    if (info && !info.stagnant && a && b) {
      const m = info.flowKgS
      const sign = m > 0 ? 1 : m < 0 ? -1 : 0
      const tx = b[0]! - a[0]!, ty = b[1]! - a[1]!, tz = b[2]! - a[2]!
      const norm = Math.sqrt(tx * tx + ty * ty + tz * tz)
      if (norm > 0 && sign !== 0) direction = [tx / norm * sign, ty / norm * sign, tz / norm * sign]
    }
    const mass = info ? info.massFlowKgS : 0
    return {
      edgeId: e, position: p ? [...p] : null, direction, massFlowKgS: mass, volumeFlowLpm: lpm(mass, rho),
      velocityMS: info ? info.velocityMS : null, stagnant: info ? info.stagnant : false,
    }
  })

  /** Cell-weighted mean |flow| and velocity; edges without cells: edge mean. */
  const edgeStats = (edgeList: string[]): [number | null, number | null] => {
    let masses: number[] = [], speeds: number[] = []
    for (const e of edgeList) {
      const info = byId.get(e)
      if (!info) continue
      const count = cellsByEdge.get(e)?.length ?? 0
      for (let j = 0; j < count; j++) {
        masses.push(info.massFlowKgS)
        if (info.velocityMS !== null) speeds.push(info.velocityMS)
      }
    }
    if (!masses.length) {
      masses = []; speeds = []
      for (const e of edgeList) {
        const info = byId.get(e)
        if (!info) continue
        masses.push(info.massFlowKgS)
        if (info.velocityMS !== null) speeds.push(info.velocityMS)
      }
    }
    if (!masses.length) return [null, null]
    return [mean(masses), speeds.length ? mean(speeds) : null]
  }
  const nodeSet = new Set(nodeIds)
  const throughput = (node: string): number | null => {
    if (!topology || !nodeSet.has(node)) return null
    let total = 0
    for (const info of edges) {
      if (info.stagnant) continue
      const m = info.flowKgS
      if ((info.to === node && m > 0) || (info.from === node && m < 0)) total += Math.abs(m)
    }
    return total
  }

  const targets: LiquidFlowTarget[] = []
  const add = (kind: LiquidFlowTargetKind, modelUid: string, equipmentUid: string, liquidPart: LiquidFlowTarget['liquidPart'],
    group: LiquidFlowGroup, mass: number | null, speed: number | null = null) => {
    if (mass === null) return
    targets.push({ kind, modelUid, equipmentUid, liquidPart, group, massFlowKgS: mass, volumeFlowLpm: lpm(mass, rho), velocityMS: speed })
  }
  const bindings = raw.object_bindings && typeof raw.object_bindings === 'object' ? (raw.object_bindings as Record<string, Bag>) : {}
  const fittings = new Map<string, { pipe: string; vid: string; node?: string; edges?: string[] }>()
  const knownEdges = new Set(edgeIds.map(String))
  for (const [key, b] of Object.entries(bindings)) {
    const edgeList = bindingEdges(key, b, knownEdges)
    if (b.kind === 'pipe_segment') {
      add('pipe', key, '', '', 'network', ...edgeStats(edgeList))
      const prefix = `${key}_tee_`
      for (const node of nodeIds) {
        if (!node.startsWith(prefix)) continue
        const vid = node.slice(prefix.length), k = `${key}\u0000${vid}`
        if (!fittings.has(k)) fittings.set(k, { pipe: key, vid, node })
      }
    } else if (b.kind === 'valve' && key.includes('/')) {
      const pipe = key.slice(0, key.indexOf('/')), vid = key.slice(key.indexOf('/') + 1)
      fittings.set(`${pipe}\u0000${vid}`, { pipe, vid, edges: edgeList })
    } else if (b.kind === 'rack') {
      for (const [suffix, part] of [['_branch_in', 'supply-manifold'], ['_branch_out', 'return-manifold']] as const) {
        const branch = edgeList.filter(e => e.endsWith(suffix) && byId.has(e)).map(e => byId.get(e)!.massFlowKgS)
        add('manifold', key, '', part, 'network', branch.length ? sum(branch) : null)
      }
    } else if (b.kind === 'cdu') {
      add('cdu', key, '', '', 'network', throughput(`${key}_supply`))
      for (const e of edgeList) {
        const info = byId.get(e)
        if (info && (info.type === 'pump' || info.type === 'heat_exchanger')) {
          add(info.type === 'pump' ? 'pump' : 'hx', key, e, '', 'network', info.massFlowKgS, info.velocityMS)
        }
      }
    }
  }
  for (const k of [...fittings.keys()].sort()) {
    const f = fittings.get(k)!
    if (f.edges) add('valve', f.pipe, f.vid, '', 'network', ...edgeStats(f.edges))
    else add('tee', f.pipe, f.vid, '', 'network', throughput(f.node!))
  }
  for (const rack of (Array.isArray(raw.rack_summaries) ? raw.rack_summaries as Bag[] : [])) {
    for (const server of (Array.isArray(rack.servers) ? rack.servers as Bag[] : [])) {
      const plates = (Array.isArray(server.cp_edge_ids) ? server.cp_edge_ids : []).map(String)
        .filter(e => byId.has(e)).map(e => byId.get(e)!.massFlowKgS)
      add('server', String(rack.rack_id), String(server.server_id), '', 'servers', plates.length ? Math.max(...plates) : null)
    }
  }

  const domains: LiquidFlowDomains = {}
  for (const group of ['network', 'servers'] as const) {
    const found = range(targets.filter(t => t.group === group).map(t => t.massFlowKgS))
    if (found) domains[group] = found
  }
  const moving = cells.filter(c => !c.stagnant && byId.has(c.edgeId))
  const cellRange = range(moving.map(c => c.massFlowKgS))
  if (cellRange) domains.cells = cellRange
  const speedRange = range(moving.flatMap(c => (c.velocityMS === null ? [] : [c.velocityMS])))
  if (speedRange) domains.velocity = speedRange

  const observable = list(raw, 'node_observable') ?? []
  const unobservableNodes = observable.flatMap((row, i) =>
    i < nodeIds.length && Array.isArray(row) && s < row.length && row[s] === false ? [nodeIds[i]!] : [])
  return {
    sample: s, timeSeconds: Number(times[s]), rhoKgM3: rho, domains, cells, edges, targets,
    stagnantEdges: edges.filter(e => e.stagnant).map(e => e.edgeId), unobservableNodes,
  }
}

/** Min..max of each flow scale over EVERY sample: a fixed domain, so an
 * animation shows the change over time instead of re-normalising. */
export function liquidFlowGlobalDomains(raw: Bag): LiquidFlowDomains {
  const out: LiquidFlowDomains = {}
  const count = Array.isArray(raw.time_s) ? raw.time_s.length : 0
  for (let s = 0; s < count; s++) {
    const fr = liquidFlowFrame(raw, s)
    if (!fr) continue
    for (const [group, d] of Object.entries(fr.domains) as [keyof LiquidFlowDomains, [number, number]][]) {
      const cur = out[group]
      out[group] = cur ? [Math.min(cur[0], d[0]), Math.max(cur[1], d[1])] : [d[0], d[1]]
    }
  }
  return out
}

/** |flow| entering `node` along the actual flow (what passes through it), or
 * null without topology or for a node no edge touches (Kit: node_throughput). */
export function nodeThroughput(frame: LiquidFlowFrame, node: string): number | null {
  if (!frame.edges.length || frame.edges[0]!.from === null) return null
  let total = 0, seen = false
  for (const info of frame.edges) {
    if (info.from === node || info.to === node) seen = true
    if (info.stagnant) continue
    const m = info.flowKgS
    if ((info.to === node && m > 0) || (info.from === node && m < 0)) total += Math.abs(m)
  }
  return seen ? total : null
}

// ---------------------------------------------------------------------------
// flow arrows
// ---------------------------------------------------------------------------

export interface LiquidFlowArrow {
  edgeId: string
  /** The result cell the arrow stands on. */
  cell: number
  /** 'header': a manifold header cell carrying the distributed manifold flow. */
  kind: 'cell' | 'header'
  position: number[]
  /** Unit vector along the actual flow. */
  direction: number[]
  massFlowKgS: number
  volumeFlowLpm: number
  velocityMS: number | null
}

export interface LiquidFlowBranch {
  rackUid: string
  serverUid: string
  side: 'supply' | 'return'
  edgeId: string
  /** As solved (positive: supply header -> server, return server -> header); 0 when stagnant. */
  flowKgS: number
  massFlowKgS: number
  volumeFlowLpm: number
  velocityMS: number | null
  reversed: boolean
  stagnant: boolean
  /** The server's node on the manifold axis, where its stub meets the header. */
  tap: number[] | null
}

export interface LiquidFlowArrows {
  sample: number
  timeSeconds: number
  arrows: LiquidFlowArrow[]
  branches: LiquidFlowBranch[]
  domains: Partial<Record<'mass' | 'velocity', [number, number]>>
}

const BRANCH_SUFFIXES = [['_branch_in', 'supply'], ['_branch_out', 'return']] as const
function headerSide(edgeId: string): 'supply' | 'return' | null {
  if (edgeId.endsWith('_branch_in') || edgeId.endsWith('_branch_out')) return null
  if (edgeId.includes('_mf_supply')) return 'supply'
  if (edgeId.includes('_mf_return')) return 'return'
  return null
}

/**
 * Where the flow arrows go at `sample`, and what each carries (Kit:
 * liquid_flow.flow_arrows, the same numbers by the shared contract).
 *
 * Every moving pipe cell gets an arrow at its position along its cell
 * direction, carrying its edge's |flow| and velocity, except:
 *  - server branches (`*_branch_in` / `*_branch_out` of a rack): the compiled
 *    graph routes each from the top of the manifold down its axis, so their
 *    cells lie ON the header, against it and on top of each other. They are
 *    listed in `branches` with `tap` (the server's node on the manifold axis)
 *    for the renderer to draw on the stub it shows between manifold and server;
 *  - manifold headers (rack edges `*_mf_supply*` / `*_mf_return*`) are drawn
 *    as the distributed manifold they represent: a header cell carries what is
 *    still to be delivered (supply: branch flows of the taps downstream of it)
 *    or already collected (return: the taps upstream). A header cell with
 *    nothing to carry gets no arrow.
 */
export function liquidFlowArrows(raw: Bag, sample = -1, frame?: LiquidFlowFrame | null): LiquidFlowArrows | null {
  const fr = frame ?? liquidFlowFrame(raw, sample)
  if (!fr) return null
  const rho = fr.rhoKgM3
  const byId = new Map(fr.edges.map(e => [e.edgeId, e]))
  const nodeIds = (list(raw, 'node_ids') ?? []).map(String)
  const nodePositions = list(raw, 'node_positions_m') ?? []
  const nodePos = new Map(nodeIds.map((n, i) => [n, nodePositions[i] as number[] | undefined]))
  const bindings = raw.object_bindings && typeof raw.object_bindings === 'object' ? (raw.object_bindings as Record<string, Bag>) : {}

  const branchEdges = new Set<string>()
  for (const b of Object.values(bindings)) {
    if (b.kind !== 'rack' && b.kind !== 'server') continue
    for (const e of (Array.isArray(b.edge_ids) ? b.edge_ids : []).map(String)) {
      if (BRANCH_SUFFIXES.some(([suffix]) => e.endsWith(suffix))) branchEdges.add(e)
    }
  }
  const branches: LiquidFlowBranch[] = []
  for (const [key, b] of Object.entries(bindings)) {
    if (b.kind !== 'server' || !b.parent_uid) continue
    const rack = String(b.parent_uid)
    const server = key.includes('/') ? key.slice(key.indexOf('/') + 1) : key
    for (const e of (Array.isArray(b.edge_ids) ? b.edge_ids : []).map(String)) {
      const side = BRANCH_SUFFIXES.find(([suffix]) => e.endsWith(suffix))?.[1]
      const info = byId.get(e)
      if (!side || !info) continue
      const node = side === 'supply' ? info.to : info.from
      const tap = node !== null ? nodePos.get(node) : undefined
      branches.push({
        rackUid: rack, serverUid: server, side, edgeId: e,
        flowKgS: info.stagnant ? 0 : info.flowKgS, massFlowKgS: info.massFlowKgS, volumeFlowLpm: info.volumeFlowLpm,
        velocityMS: info.velocityMS, reversed: info.reversed, stagnant: info.stagnant,
        tap: Array.isArray(tap) ? [...tap] : null,
      })
    }
  }
  const taps = new Map<string, LiquidFlowBranch[]>()
  for (const b of branches) {
    if (!b.tap) continue
    const k = `${b.rackUid}\u0000${b.side}`
    taps.set(k, [...(taps.get(k) ?? []), b])
  }
  const headerRack = new Map<string, string>()
  for (const [key, b] of Object.entries(bindings)) {
    if (b.kind === 'rack') for (const e of (Array.isArray(b.edge_ids) ? b.edge_ids : []).map(String)) headerRack.set(e, key)
  }

  const arrows: LiquidFlowArrow[] = []
  fr.cells.forEach((cell, c) => {
    const e = cell.edgeId
    const info = byId.get(e)
    if (branchEdges.has(e) || cell.stagnant || !info) return
    const d = cell.direction, p = cell.position
    if (!p || (d[0] === 0 && d[1] === 0 && d[2] === 0)) return
    const side = headerRack.has(e) ? headerSide(e) : null
    const own = side ? taps.get(`${headerRack.get(e)}\u0000${side}`) : undefined
    if (own && own.length) {
      let total = 0
      for (const b of own) {
        const t = b.tap!
        const along = (t[0]! - p[0]!) * d[0] + (t[1]! - p[1]!) * d[1] + (t[2]! - p[2]!) * d[2]
        if ((side === 'supply' && along > 0) || (side === 'return' && along < 0)) total += b.flowKgS
      }
      if (total === 0) return
      const mass = Math.abs(total)
      arrows.push({
        edgeId: e, cell: c, kind: 'header', position: [...p], direction: total > 0 ? [...d] : [-d[0], -d[1], -d[2]],
        massFlowKgS: mass, volumeFlowLpm: lpm(mass, rho), velocityMS: velocity(mass, rho, info.diameterM),
      })
    } else {
      arrows.push({
        edgeId: e, cell: c, kind: 'cell', position: [...p], direction: [...d],
        massFlowKgS: cell.massFlowKgS, volumeFlowLpm: cell.volumeFlowLpm, velocityMS: cell.velocityMS,
      })
    }
  })

  const domains: LiquidFlowArrows['domains'] = {}
  const moving = branches.filter(b => !b.stagnant)
  const mass = range([...arrows.map(a => a.massFlowKgS), ...moving.map(b => b.massFlowKgS)])
  if (mass) domains.mass = mass
  const speeds = range([...arrows.flatMap(a => (a.velocityMS === null ? [] : [a.velocityMS])),
    ...moving.flatMap(b => (b.velocityMS === null ? [] : [b.velocityMS]))])
  if (speeds) domains.velocity = speeds
  return { sample: fr.sample, timeSeconds: fr.timeSeconds, arrows, branches, domains }
}

/** liquidFlowArrows domains over EVERY sample (arrow sizes do not re-normalise). */
export function liquidArrowGlobalDomains(raw: Bag): LiquidFlowArrows['domains'] {
  const out: LiquidFlowArrows['domains'] = {}
  const count = Array.isArray(raw.time_s) ? raw.time_s.length : 0
  for (let s = 0; s < count; s++) {
    const fr = liquidFlowArrows(raw, s)
    if (!fr) continue
    for (const [key, d] of Object.entries(fr.domains) as ['mass' | 'velocity', [number, number]][]) {
      const cur = out[key]
      out[key] = cur ? [Math.min(cur[0], d[0]), Math.max(cur[1], d[1])] : [d[0], d[1]]
    }
  }
  return out
}
