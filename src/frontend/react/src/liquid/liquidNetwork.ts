/**
 * The liquid network as the post-processing panels read it: one row per
 * solved edge, the key operating figures, the heat and heat-exchanger
 * summary, per-object time series and the selection mapping.
 *
 * Pure: a result document in, plain values out. Every number is either copied
 * from the result or derived by one stated rule, so a panel can be checked
 * against `result.json` value by value:
 *
 *   flow, direction   liquidFlowFrame (liquidFlow.ts, shared with Kit): signed
 *                     flow as solved, upstream/downstream by the ACTUAL flow,
 *                     |flow|, L/min = |m|/rho*60000, velocity = |m|/(rho*pi*D^2/4)
 *   static Δp         P(upstream) - P(downstream); both are absolute node
 *                     pressures and include the hydrostatic head
 *   loss              static Δp + rho*g*(y_up - y_down): the drop with the
 *                     elevation change removed. Positive on every passive
 *                     element (pipe, valve, cold plate, HX) and negative on a
 *                     pump, whose head is -loss. g = 9.81 m/s^2 along -y, the
 *                     gravity the liquid graph compiler writes
 *                     (liquid_graphgen.js)
 *   inlet / outlet T  node temperature at the upstream node / the edge's
 *                     solved outlet temperature (downstream end, actual flow)
 *
 * Stagnant edges (`edge_stagnant`) carry no flow, no direction and no
 * temperatures; nodes flagged in `node_observable` have no defined
 * temperature. Their rows are kept and marked, never dropped.
 *
 * Results written before the result topology (2026-10-21) have no edge ends,
 * types or bores: the rows still carry flow and outlet temperature, the type
 * is read from what the file does say (cold-plate details, HX list, pipe
 * cells, CDU and valve bindings), and direction, Δp and velocity are null.
 */

import { liquidFlowFrame, type LiquidFlowFrame } from './liquidFlow'
import { parseLiquidResults, toCelsius, type LiquidResults, type ResultObjectBinding } from './liquidResults'

type Bag = Record<string, unknown>

/** The gravity the liquid graph compiler writes: [0, -9.81, 0] m/s^2. */
export const LIQUID_GRAVITY_M_S2 = 9.81

export type NetworkEdgeType = 'pipe' | 'valve' | 'pump' | 'heat_exchanger' | 'cold_plate' | 'other'
export const NETWORK_EDGE_TYPES: NetworkEdgeType[] = ['pipe', 'valve', 'pump', 'heat_exchanger', 'cold_plate', 'other']

/** What a row selects in the app: a model, optionally narrowed to a server or a fitting. */
export interface NetworkSelection {
  uid: string
  node: { type: 'equipment'; uid: string; equipmentId: string } | { type: 'fitting'; uid: string; fittingId: string } | null
}

export interface NetworkRow {
  edgeId: string
  index: number
  type: NetworkEdgeType
  /** The `object_bindings` key that owns the edge, its label and the edge's own part of it. */
  owner: string | null
  ownerLabel: string | null
  /** '' when the edge IS the object (a valve, a feed pipe); otherwise e.g. 'seg3', 'branch_in'. */
  part: string
  select: NetworkSelection | null
  /** As solved: positive from the modelled `from` to `to`. Null when the sample has no value. */
  flowKgS: number | null
  massFlowKgS: number | null
  volumeFlowLpm: number | null
  velocityMS: number | null
  diameterM: number | null
  from: string | null
  to: string | null
  upstream: string | null
  downstream: string | null
  reversed: boolean
  stagnant: boolean
  /** Endpoints whose temperature is undefined at this sample. */
  unobservable: string[]
  upstreamPressurePa: number | null
  downstreamPressurePa: number | null
  staticDropPa: number | null
  lossPa: number | null
  inletK: number | null
  outletK: number | null
}

export interface NetworkTable {
  sample: number
  timeSeconds: number
  rhoKgM3: number | null
  /** Whether the file carries the result topology (edge ends, types, bores). */
  hasTopology: boolean
  rows: NetworkRow[]
  stagnantEdges: string[]
  unobservableNodes: string[]
}

// ── small readers ──────────────────────────────────────────────────────────

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const at = (row: number[] | undefined, s: number): number | null => {
  const v = row?.[s]
  return finite(v) ? v : null
}

interface Indexed {
  raw: Bag
  parsed: LiquidResults
  edgeIndex: Map<string, number>
  nodeIndex: Map<string, number>
  bindings: Record<string, ResultObjectBinding>
  edgeOwners: Map<string, string>
  nodeOwners: Map<string, string>
  rho: number | null
}

const cache = new WeakMap<object, Indexed>()

function asBag(v: unknown): Bag {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Bag) : {}
}

/** Most specific owner: a sub-object ('rack/server', 'pipe/valve') before its parent, then the smaller set. */
function owners(bindings: Record<string, ResultObjectBinding>, field: 'edge_ids' | 'node_ids'): Map<string, string> {
  const out = new Map<string, string>()
  const rank = (key: string) => [key.includes('/') ? 0 : 1, bindings[key]![field].length] as const
  for (const key of Object.keys(bindings)) {
    for (const id of bindings[key]![field]) {
      const prev = out.get(id)
      if (prev === undefined) { out.set(id, key); continue }
      const [a1, a2] = rank(key), [b1, b2] = rank(prev)
      if (a1 < b1 || (a1 === b1 && a2 < b2)) out.set(id, key)
    }
  }
  return out
}

function indexed(raw: unknown): Indexed | null {
  if (!raw || typeof raw !== 'object') return null
  const hit = cache.get(raw as object)
  if (hit) return hit
  const parsed = parseLiquidResults(raw)
  if (!parsed) return null
  const bag = raw as Bag
  const bindings = parsed.object_bindings ?? {}
  const fluid = asBag(bag.fluid)
  const rho = finite(fluid.rho_kg_m3) && fluid.rho_kg_m3 > 0 ? fluid.rho_kg_m3
    : fluid.rho_kg_m3 === undefined ? 997 : null
  const out: Indexed = {
    raw: bag, parsed, bindings, rho,
    edgeIndex: new Map(parsed.edge_ids.map((e, i) => [e, i])),
    nodeIndex: new Map(parsed.node_ids.map((n, i) => [n, i])),
    edgeOwners: owners(bindings, 'edge_ids'),
    nodeOwners: owners(bindings, 'node_ids'),
  }
  cache.set(raw as object, out)
  return out
}

function sampleOf(parsed: LiquidResults, sample: number): number | null {
  const n = parsed.time_s.length
  if (n === 0) return null
  const s = sample < 0 ? n - 1 : sample
  return s < n ? s : null
}

/** The part of `id` after its owner's prefix ('m_1/e_1' owns 'm_1_e_1_branch_in' -> 'branch_in'). */
function localPart(id: string, owner: string, binding: ResultObjectBinding | undefined): string {
  if (id === owner) return ''
  const candidates = [owner.replace('/', '_') + '_']
  if (owner.includes('/')) candidates.push(owner.slice(0, owner.indexOf('/')) + '_')
  if (binding?.parent_uid) candidates.push(binding.parent_uid + '_')
  for (const prefix of candidates) if (id.startsWith(prefix)) return id.slice(prefix.length)
  return id
}

function selectionFor(owner: string | null, binding: ResultObjectBinding | undefined): NetworkSelection | null {
  if (!owner) return null
  const slash = owner.indexOf('/')
  if (slash < 0) return { uid: owner, node: null }
  const uid = owner.slice(0, slash), sub = owner.slice(slash + 1)
  if (binding?.kind === 'server') return { uid, node: { type: 'equipment', uid, equipmentId: sub } }
  if (binding?.kind === 'valve') return { uid, node: { type: 'fitting', uid, fittingId: sub } }
  return { uid, node: null }
}

function edgeOwner(ix: Indexed, edgeId: string): string | null {
  return ix.edgeOwners.get(edgeId) ?? (ix.bindings[edgeId] ? edgeId : null)
}

/** The label and part of a node, e.g. 'CDU_1' + 'supply'. Falls back to the raw id. */
export function resultNodeName(raw: unknown, nodeId: string): { ownerLabel: string | null; part: string } {
  const ix = indexed(raw)
  // A flow valve's two ends are `<pipe>_control_<valve id>_in|out`: name them by the valve.
  const valveEnd = /^(.+)_control_(.+)_(in|out)$/.exec(nodeId)
  const valve = valveEnd && ix?.bindings[`${valveEnd[1]}/${valveEnd[2]}`]
  if (valveEnd && valve) return { ownerLabel: valve.label, part: valveEnd[3]! }
  const owner = ix?.nodeOwners.get(nodeId) ?? null
  if (!ix || !owner) return { ownerLabel: null, part: nodeId }
  const binding = ix.bindings[owner]
  return { ownerLabel: binding?.label ?? owner, part: localPart(nodeId, owner, binding) }
}

/** The label and part of an edge, e.g. 'Rack_A_01-server_1' + 'cp_CP-CU_1'. */
export function resultEdgeName(raw: unknown, edgeId: string): { ownerLabel: string | null; part: string } {
  const ix = indexed(raw)
  const owner = ix ? edgeOwner(ix, edgeId) : null
  if (!ix || !owner) return { ownerLabel: null, part: edgeId }
  const binding = ix.bindings[owner]
  // A valve IS its one edge, and a feed pipe's edge carries the pipe's own uid.
  const part = edgeId === owner || binding?.kind === 'valve' ? '' : localPart(edgeId, owner, binding)
  return { ownerLabel: binding?.label ?? owner, part }
}

function inferType(ix: Indexed, edgeId: string, frameType: string | null | undefined): NetworkEdgeType {
  if (frameType === 'pipe' || frameType === 'valve' || frameType === 'pump' ||
      frameType === 'heat_exchanger' || frameType === 'cold_plate') return frameType
  if (frameType) return 'other'
  // Pre-topology file: read the type from what the file does say.
  const p = ix.parsed
  if (p.cp_details.some((c) => c.edge_id === edgeId)) return 'cold_plate'
  if (p.hx_edge_ids?.includes(edgeId)) return 'heat_exchanger'
  const owner = edgeOwner(ix, edgeId)
  const kind = owner ? ix.bindings[owner]?.kind : undefined
  if (kind === 'valve') return 'valve'
  if (kind === 'cdu') return 'pump'
  if (p.cell_edge_ids?.includes(edgeId) || kind === 'pipe_segment' || kind === 'rack') return 'pipe'
  return 'other'
}

function flowFrame(ix: Indexed, s: number): LiquidFlowFrame | null {
  if (ix.rho === null) return null
  const raw = asBag(ix.raw.fluid).rho_kg_m3 === undefined
    ? { ...ix.raw, fluid: { ...asBag(ix.raw.fluid), rho_kg_m3: ix.rho } } : ix.raw
  return liquidFlowFrame(raw, s)
}

// ── network table ──────────────────────────────────────────────────────────

/** Every solved edge at `sample` (-1 = last), or null when the result has no such sample. */
export function liquidNetworkTable(raw: unknown, sample = -1): NetworkTable | null {
  const ix = indexed(raw)
  if (!ix) return null
  const p = ix.parsed
  const s = sampleOf(p, sample)
  if (s === null) return null
  const frame = flowFrame(ix, s)
  const frameEdges = new Map((frame?.edges ?? []).map((e) => [e.edgeId, e]))
  const hasTopology = !!p.edge_from && !!p.edge_to && p.edge_from.length === p.edge_ids.length
  const unobservable = new Set(frame?.unobservableNodes ?? [])
  const pressure = (node: string | null) => node === null ? null : at(p.node_pressure_Pa[ix.nodeIndex.get(node) ?? -1], s)
  const temperature = (node: string | null) => node === null || unobservable.has(node)
    ? null : at(p.node_temperature_K?.[ix.nodeIndex.get(node) ?? -1], s)
  const height = (node: string | null) => {
    const pos = node === null ? undefined : p.node_positions_m?.[ix.nodeIndex.get(node) ?? -1]
    return pos && finite(pos[1]) ? pos[1] : null
  }

  const rows: NetworkRow[] = p.edge_ids.map((edgeId, index) => {
    const fe = frameEdges.get(edgeId)
    const owner = edgeOwner(ix, edgeId)
    const binding = owner ? ix.bindings[owner] : undefined
    const name = resultEdgeName(raw, edgeId)
    const signed = fe ? fe.flowKgS : at(p.edge_flow_kg_s[index], s)
    const stagnant = fe?.stagnant ?? p.edge_stagnant?.[index] === true
    const from = hasTopology ? p.edge_from![index]! : null
    const to = hasTopology ? p.edge_to![index]! : null
    const upstream = fe?.upstream ?? null, downstream = fe?.downstream ?? null
    const upP = pressure(upstream), downP = pressure(downstream)
    const staticDrop = upP !== null && downP !== null ? upP - downP : null
    const yUp = height(upstream), yDown = height(downstream)
    const loss = staticDrop !== null && yUp !== null && yDown !== null && ix.rho !== null
      ? staticDrop + ix.rho * LIQUID_GRAVITY_M_S2 * (yUp - yDown) : null
    const ends = [from, to].filter((n): n is string => n !== null && unobservable.has(n))
    const downObservable = downstream === null || !unobservable.has(downstream)
    return {
      edgeId, index,
      type: inferType(ix, edgeId, p.edge_types?.[index] ?? fe?.type),
      owner, ownerLabel: name.ownerLabel, part: name.part,
      select: selectionFor(owner, binding),
      flowKgS: signed,
      massFlowKgS: fe ? fe.massFlowKgS : signed === null ? null : stagnant ? 0 : Math.abs(signed),
      volumeFlowLpm: fe ? fe.volumeFlowLpm : signed !== null && ix.rho !== null ? (stagnant ? 0 : Math.abs(signed)) / ix.rho * 60000 : null,
      velocityMS: fe?.velocityMS ?? null,
      diameterM: fe?.diameterM ?? (finite(p.edge_D_m?.[index]) ? p.edge_D_m![index]! : null),
      from, to, upstream, downstream,
      reversed: fe ? fe.reversed : !stagnant && signed !== null && signed < 0,
      stagnant,
      unobservable: ends,
      upstreamPressurePa: upP, downstreamPressurePa: downP,
      staticDropPa: staticDrop, lossPa: loss,
      inletK: stagnant ? null : temperature(upstream),
      outletK: stagnant || !downObservable ? null : at(p.edge_outlet_temp_K?.[index], s),
    }
  })
  return {
    sample: s, timeSeconds: p.time_s[s]!, rhoKgM3: ix.rho, hasTopology, rows,
    stagnantEdges: rows.filter((r) => r.stagnant).map((r) => r.edgeId),
    unobservableNodes: [...unobservable],
  }
}

/** A row is shown greyed when it carries no flow or touches a node with no defined temperature. */
export function rowInactiveReason(row: NetworkRow): 'stagnant' | 'unobservable' | null {
  if (row.stagnant) return 'stagnant'
  if (row.unobservable.length) return 'unobservable'
  return null
}

export type NetworkSortKey = 'name' | 'type' | 'flow' | 'velocity' | 'drop' | 'loss' | 'inlet' | 'outlet'

export function rowDisplayName(row: NetworkRow): string {
  return row.ownerLabel === null ? row.edgeId : row.part ? `${row.ownerLabel} · ${row.part}` : row.ownerLabel
}

/**
 * Filter by type and by a case-insensitive search over the name (as printed,
 * `nameOf`, and as stored), the edge id and both ends (ids, and as printed by
 * `nodeNameOf`).
 */
export function filterNetworkRows(
  rows: NetworkRow[], types: ReadonlySet<NetworkEdgeType> | null, query: string,
  nameOf: (row: NetworkRow) => string = rowDisplayName,
  nodeNameOf: (node: string) => string = (node) => node,
): NetworkRow[] {
  const q = query.trim().toLowerCase()
  return rows.filter((row) => {
    if (types && !types.has(row.type)) return false
    if (!q) return true
    const ends = [row.upstream, row.downstream, row.from, row.to].filter((n): n is string => n !== null)
    return [nameOf(row), rowDisplayName(row), row.edgeId, ...ends, ...ends.map(nodeNameOf)]
      .some((text) => text.toLowerCase().includes(q))
  })
}

/** Stable sort; missing values always go last whichever the direction. */
export function sortNetworkRows(
  rows: NetworkRow[], key: NetworkSortKey, direction: 'asc' | 'desc',
  nameOf: (row: NetworkRow) => string = rowDisplayName,
): NetworkRow[] {
  const value = (row: NetworkRow): number | string | null => {
    switch (key) {
      case 'name': return nameOf(row).toLowerCase()
      case 'type': return NETWORK_EDGE_TYPES.indexOf(row.type)
      case 'flow': return row.massFlowKgS
      case 'velocity': return row.velocityMS
      case 'drop': return row.staticDropPa
      case 'loss': return row.lossPa
      case 'inlet': return row.inletK
      case 'outlet': return row.outletK
    }
  }
  const sign = direction === 'asc' ? 1 : -1
  return rows.map((row, i) => ({ row, i, v: value(row) })).sort((a, b) => {
    if (a.v === null || b.v === null) return a.v === null ? (b.v === null ? a.i - b.i : 1) : -1
    const c = typeof a.v === 'string' ? a.v.localeCompare(b.v as string) : (a.v as number) - (b.v as number)
    return c !== 0 ? c * sign : a.i - b.i
  }).map((x) => x.row)
}

// ── key figures ────────────────────────────────────────────────────────────

export interface PumpFigure {
  edgeId: string
  ownerLabel: string | null
  massFlowKgS: number | null
  volumeFlowLpm: number | null
  /** Pump head with the elevation change removed (Pa), = -loss; and in metres of coolant. */
  headPa: number | null
  headM: number | null
  speedRatio: number | null
}

export interface LoopFigure {
  cduUid: string
  label: string
  supplyK: number | null
  returnK: number | null
}

export interface NetworkMetrics {
  /** Sum of the pump flows: what leaves the CDUs. */
  totalFlowKgS: number | null
  totalFlowLpm: number | null
  pumps: PumpFigure[]
  maxChip: { valueK: number; rackId: string; serverId: string } | null
  loops: LoopFigure[]
  maxVelocity: { edgeId: string; row: NetworkRow; valueMS: number } | null
  /** The server branch with the least flow. */
  minFlowBranch: { rackId: string; serverId: string; massFlowKgS: number; volumeFlowLpm: number | null } | null
}

export function liquidNetworkMetrics(raw: unknown, sample = -1): NetworkMetrics | null {
  const ix = indexed(raw)
  const table = liquidNetworkTable(raw, sample)
  if (!ix || !table) return null
  const p = ix.parsed, s = table.sample
  const pumps: PumpFigure[] = table.rows.filter((r) => r.type === 'pump').map((r) => ({
    edgeId: r.edgeId, ownerLabel: r.ownerLabel,
    massFlowKgS: r.massFlowKgS, volumeFlowLpm: r.volumeFlowLpm,
    headPa: r.lossPa === null ? null : -r.lossPa,
    headM: r.lossPa === null || ix.rho === null ? null : -r.lossPa / (ix.rho * LIQUID_GRAVITY_M_S2),
    speedRatio: p.controls?.[s]?.pump_rpm_ratios[r.edgeId] ?? null,
  }))
  const pumpFlows = pumps.map((x) => x.massFlowKgS).filter(finite)
  const totalFlow = pumpFlows.length ? pumpFlows.reduce((a, b) => a + b, 0) : null

  let maxChip: NetworkMetrics['maxChip'] = null
  let minBranch: NetworkMetrics['minFlowBranch'] = null
  for (const rack of p.rack_summaries) {
    for (const server of rack.servers) {
      const chip = at(server.max_chip_temp_K, s)
      if (chip !== null && (!maxChip || chip > maxChip.valueK)) maxChip = { valueK: chip, rackId: rack.rack_id, serverId: server.server_id }
      const flow = at(server.flow_kg_s, s)
      if (flow !== null && (!minBranch || Math.abs(flow) < minBranch.massFlowKgS)) {
        minBranch = { rackId: rack.rack_id, serverId: server.server_id, massFlowKgS: Math.abs(flow),
          volumeFlowLpm: ix.rho === null ? null : Math.abs(flow) / ix.rho * 60000 }
      }
    }
  }

  const loops: LoopFigure[] = Object.entries(ix.bindings).filter(([, b]) => b.kind === 'cdu').map(([key, b]) => {
    const node = (suffix: string) => {
      const id = `${key}_${suffix}`
      return table.unobservableNodes.includes(id) ? null : at(p.node_temperature_K?.[ix.nodeIndex.get(id) ?? -1], s)
    }
    return { cduUid: key, label: b.label, supplyK: node('supply'), returnK: node('return') }
  })

  let maxVelocity: NetworkMetrics['maxVelocity'] = null
  for (const row of table.rows) {
    if (row.velocityMS !== null && !row.stagnant && (!maxVelocity || row.velocityMS > maxVelocity.valueMS)) {
      maxVelocity = { edgeId: row.edgeId, row, valueMS: row.velocityMS }
    }
  }
  return {
    totalFlowKgS: totalFlow,
    totalFlowLpm: totalFlow === null || ix.rho === null ? null : totalFlow / ix.rho * 60000,
    pumps, maxChip, loops, maxVelocity, minFlowBranch: minBranch,
  }
}

// ── heat and heat exchangers ───────────────────────────────────────────────

export interface HxFigure {
  edgeId: string
  ownerLabel: string | null
  /** 'counterflow-ntu' | 'mixed-volumes' as recorded; null when the run predates the record. */
  model: string | null
  /** The backend filled `model` in for a run saved before HX models were recorded. */
  modelInferred: boolean
  primaryInletK: number | null
  primaryFlowKgS: number | null
  /** L/min with the PRIMARY fluid's density as recorded with the circuit. */
  primaryFlowLpm: number | null
  primaryReturnK: number | null
  secondaryInletK: number | null
  secondarySupplyK: number | null
  secondaryFlowKgS: number | null
}

export interface HeatSummary {
  sample: number
  timeSeconds: number
  diagnostics: {
    heatInputW: number | null
    primaryRejectionW: number | null
    storageRateW: number | null
    energyBalanceW: number | null
    boundaryExchangeW: number | null
    massResidualKgS: number | null
    momentumResidualPa: number | null
    scaledResidual: number | null
  }
  hx: HxFigure[]
  heatMultiplier: number | null
  pumpSpeeds: { edgeId: string; ratio: number }[]
  valveOpenings: { edgeId: string; opening: number }[]
  solver: { name: string | null; newtonDae: boolean; maxScaledResidual: number | null; iterations: number | null }
  /** The constitutive laws the run was solved with; `inferred` lists those a pre-record run got back. */
  laws: { pump: string | null; friction: string | null; hydrostatics: string | null; inferred: string[] } | null
}

export function liquidHeatSummary(raw: unknown, sample = -1): HeatSummary | null {
  const ix = indexed(raw)
  if (!ix) return null
  const p = ix.parsed
  const s = sampleOf(p, sample)
  if (s === null) return null
  const table = liquidNetworkTable(raw, s)
  const d = p.diagnostics?.[s] ?? {}
  const get = (key: keyof typeof d) => (finite(d[key]) ? d[key] as number : null)
  const controls = p.controls?.[s]
  const hx = (p.hx_edge_ids ?? []).map((edgeId, k): HxFigure => {
    const row = table?.rows.find((r) => r.edgeId === edgeId)
    const circuit = p.primary_circuits?.find((c) => c.edge_id === edgeId)
    const inlet = controls?.primary_inlet_K[edgeId]
    return {
      edgeId, ownerLabel: row?.ownerLabel ?? resultEdgeName(raw, edgeId).ownerLabel,
      model: p.solver_laws?.hx_models?.[edgeId] ?? null,
      modelInferred: !!p.solver_laws?.inferred?.includes('hx_models'),
      primaryInletK: finite(inlet) ? inlet : circuit?.inlet_K ?? null,
      primaryFlowKgS: circuit?.flow_kg_s ?? null,
      primaryFlowLpm: finite(circuit?.flow_kg_s) && finite(circuit?.rho_kg_m3) && circuit!.rho_kg_m3! > 0
        ? circuit!.flow_kg_s! / circuit!.rho_kg_m3! * 60000 : null,
      primaryReturnK: at(p.primary_return_K?.[k], s),
      secondaryInletK: row?.inletK ?? null,
      secondarySupplyK: at(p.secondary_supply_K?.[k], s),
      secondaryFlowKgS: row?.massFlowKgS ?? null,
    }
  })
  const newtonDae = p.solver === 'differentiable_newton_dae'
  return {
    sample: s, timeSeconds: p.time_s[s]!,
    diagnostics: {
      heatInputW: get('heat_input_W'), primaryRejectionW: get('primary_rejection_W'),
      storageRateW: get('storage_rate_W'), energyBalanceW: get('energy_balance_W'),
      boundaryExchangeW: get('boundary_exchange_W'), massResidualKgS: get('mass_residual_kg_s'),
      momentumResidualPa: get('momentum_residual_Pa'), scaledResidual: get('scaled_residual'),
    },
    hx,
    heatMultiplier: at(p.heat_multipliers, s),
    pumpSpeeds: Object.entries(controls?.pump_rpm_ratios ?? {}).map(([edgeId, ratio]) => ({ edgeId, ratio })),
    valveOpenings: Object.entries(controls?.valve_openings ?? {}).map(([edgeId, opening]) => ({ edgeId, opening })),
    solver: { name: p.solver ?? null, newtonDae, maxScaledResidual: p.final_loss, iterations: p.epochs },
    laws: p.solver_laws ? {
      pump: p.solver_laws.pump_law ?? null, friction: p.solver_laws.friction_law ?? null,
      hydrostatics: p.solver_laws.hydrostatics ?? null, inferred: p.solver_laws.inferred ?? [],
    } : null,
  }
}

/** Heat rates over every sample, in W. */
export function liquidHeatSeries(raw: unknown): { time: number[]; input: number[]; rejection: number[]; storage: number[]; balance: number[] } | null {
  const ix = indexed(raw)
  if (!ix) return null
  const p = ix.parsed
  const pick = (key: 'heat_input_W' | 'primary_rejection_W' | 'storage_rate_W' | 'energy_balance_W') =>
    p.time_s.map((_, s) => { const v = p.diagnostics?.[s]?.[key]; return finite(v) ? v : NaN })
  return { time: [...p.time_s], input: pick('heat_input_W'), rejection: pick('primary_rejection_W'),
    storage: pick('storage_rate_W'), balance: pick('energy_balance_W') }
}

// ── selection ──────────────────────────────────────────────────────────────

/**
 * The edges behind an app selection, through `object_bindings`.
 *
 * A model owns its binding's edges; a server node narrows a rack to
 * `rack/server`; a fitting node narrows a pipe to its valve (`pipe/valve`) or,
 * for a T, to the edges meeting at node `<pipe>_tee_<id>`. A pipe whose own
 * binding lists no edges but which IS an edge (the CDU feeds) maps to it.
 */
export function focusEdgeIdsFor(
  raw: unknown,
  uids: readonly string[],
  node: { type: string; uid: string; equipmentId?: string; fittingId?: string } | null,
): string[] {
  const ix = indexed(raw)
  if (!ix) return []
  const out = new Set<string>()
  const addBinding = (key: string) => {
    for (const e of ix.bindings[key]?.edge_ids ?? []) out.add(e)
    if (ix.edgeIndex.has(key)) out.add(key)
  }
  const narrowed = node && (node.type === 'equipment' || node.type === 'serverFace' || node.type === 'serverPatch') && node.equipmentId
    ? `${node.uid}/${node.equipmentId}`
    : node && node.type === 'fitting' && node.fittingId ? `${node.uid}/${node.fittingId}` : null
  if (narrowed && ix.bindings[narrowed]) addBinding(narrowed)
  else if (narrowed && node?.type === 'fitting') {
    const tee = `${node.uid}_tee_${node.fittingId}`
    const p = ix.parsed
    if (p.edge_from && p.edge_to) p.edge_ids.forEach((e, i) => { if (p.edge_from![i] === tee || p.edge_to![i] === tee) out.add(e) })
  }
  if (!narrowed || out.size === 0) for (const uid of uids) addBinding(uid)
  return [...out]
}

// ── per-object series for the charts ───────────────────────────────────────

export type SeriesQuantity = 'temperature' | 'flow' | 'pressure'
export type SeriesTarget =
  | { kind: 'server'; key: string }
  | { kind: 'edge'; edgeId: string }
  | { kind: 'cdu'; key: string }

export type SeriesId =
  | 'chip' | 'inlet' | 'outlet' | 'flow' | 'p_inlet' | 'p_outlet'
  | 'supply' | 'return' | 'primary_return' | 'primary_inlet' | 'pump_flow' | 'p_supply' | 'p_return' | 'p_discharge'

export interface ObjectSeries {
  id: SeriesId
  /** °C for temperature, L/min for flow, kPa for pressure (absolute or gauge). */
  points: number[]
  /** Flow only: the same samples in kg/s. */
  kgS?: number[]
}

export interface ObjectSeriesSet {
  quantity: SeriesQuantity
  unit: '°C' | 'L/min' | 'kPa'
  gauge: boolean
  /** Pa subtracted per sample for the gauge option; null when no reference is known. */
  referencePa: number[] | null
  series: ObjectSeries[]
}

/** Connected parts of the network, by edge ends, each mapped to its CDU return node. */
function loopReferences(ix: Indexed): Map<string, string> {
  const p = ix.parsed
  const parent = new Map<string, string>()
  const find = (n: string): string => {
    let r = n
    while (parent.get(r) !== undefined && parent.get(r) !== r) r = parent.get(r)!
    parent.set(n, r)
    return r
  }
  for (const n of p.node_ids) parent.set(n, n)
  if (p.edge_from && p.edge_to) {
    p.edge_ids.forEach((_, i) => {
      const a = find(p.edge_from![i]!), b = find(p.edge_to![i]!)
      if (a !== b) parent.set(a, b)
    })
  }
  const cduReturns = Object.entries(ix.bindings).filter(([, b]) => b.kind === 'cdu')
    .map(([key]) => `${key}_return`).filter((n) => ix.nodeIndex.has(n))
  const out = new Map<string, string>()
  if (!(p.edge_from && p.edge_to)) {
    // No topology: one CDU means one reference; several cannot be told apart.
    if (cduReturns.length === 1) for (const n of p.node_ids) out.set(n, cduReturns[0]!)
    return out
  }
  const rootRef = new Map<string, string>()
  for (const ret of cduReturns) rootRef.set(find(ret), ret)
  // With one CDU every node is on its loop; several CDUs are told apart by connectivity.
  for (const n of p.node_ids) {
    const ref = rootRef.get(find(n)) ?? (cduReturns.length === 1 ? cduReturns[0] : undefined)
    if (ref) out.set(n, ref)
  }
  return out
}

/** The CDU return pressure (the loop's fixed reference) for `node`, per sample; null when unknown. */
export function pressureReferencePa(raw: unknown, node: string): number[] | null {
  const ix = indexed(raw)
  if (!ix) return null
  const ref = loopReferences(ix).get(node)
  return ref ? ix.parsed.node_pressure_Pa[ix.nodeIndex.get(ref)!]?.map((v) => (finite(v) ? v : NaN)) ?? null : null
}

export function objectSeries(raw: unknown, target: SeriesTarget, quantity: SeriesQuantity, gauge = false): ObjectSeriesSet | null {
  const ix = indexed(raw)
  if (!ix) return null
  const p = ix.parsed
  const n = p.time_s.length
  const samples = Array.from({ length: n }, (_, s) => s)
  const nodeRow = (matrix: number[][] | undefined, node: string) => matrix?.[ix.nodeIndex.get(node) ?? -1]
  const val = (row: readonly number[] | undefined, s: number) => { const v = row?.[s]; return finite(v) ? v : NaN }
  const observable = (node: string, s: number) => p.node_observable?.[ix.nodeIndex.get(node) ?? -1]?.[s] !== false
  /** °C at `node` per sample; NaN where the node has no defined temperature. */
  const celsius = (nodes: string[]) => nodes.map((node, s) =>
    observable(node, s) ? toCelsius(val(nodeRow(p.node_temperature_K, node), s)) : NaN)
  const toLpm = (kg: number[]) => kg.map((m) => (ix.rho === null ? NaN : Math.abs(m) / ix.rho * 60000))
  const reference = { pa: null as number[] | null }
  const series: ObjectSeries[] = []
  const flowSeries = (id: SeriesId, kg: number[]) => series.push({ id, points: toLpm(kg), kgS: kg.map(Math.abs) })
  /** kPa at `nodes[s]` per sample, minus the loop's CDU return pressure for the gauge option. */
  const pressureSeries = (id: SeriesId, nodes: string[]) => {
    const ref = gauge ? pressureReferencePa(raw, nodes[0] ?? '') : null
    if (ref) reference.pa = ref
    series.push({ id, points: nodes.map((node, s) => (val(nodeRow(p.node_pressure_Pa, node), s) - (ref ? ref[s] ?? NaN : 0)) / 1000) })
  }
  /** Upstream/downstream per sample, by the sign at THAT sample: a transient may reverse. */
  const directed = (i: number): [string, string][] | null => {
    if (!p.edge_from || !p.edge_to) return null
    const from = p.edge_from[i]!, to = p.edge_to[i]!, flow = p.edge_flow_kg_s[i] ?? []
    return samples.map((s) => (val(flow, s) >= 0 ? [from, to] : [to, from]))
  }

  if (target.kind === 'edge') {
    const found = ix.edgeIndex.get(target.edgeId)
    const cp = p.cp_details.find((c) => c.edge_id === target.edgeId)
    // A cold plate is plotted from its own details even when the file lists no edges.
    if (found === undefined && !cp) return null
    const i = found ?? -1
    const stagnant = p.edge_stagnant?.[i] === true
    const dir = found === undefined ? null : directed(i)
    if (quantity === 'flow') {
      if (found !== undefined) flowSeries('flow', samples.map((s) => (stagnant ? 0 : val(p.edge_flow_kg_s[i], s))))
    } else if (quantity === 'temperature') {
      if (cp) {
        series.push({ id: 'chip', points: cp.chip_temp_K.map(toCelsius) })
        series.push({ id: 'inlet', points: cp.water_inlet_temp_K.map(toCelsius) })
        series.push({ id: 'outlet', points: cp.water_outlet_temp_K.map(toCelsius) })
      } else {
        if (dir) series.push({ id: 'inlet', points: stagnant ? samples.map(() => NaN) : celsius(dir.map(([up]) => up)) })
        series.push({ id: 'outlet', points: samples.map((s) => (stagnant || (dir && !observable(dir[s]![1], s)) ? NaN : toCelsius(val(p.edge_outlet_temp_K?.[i], s)))) })
      }
    } else if (dir) {
      pressureSeries('p_inlet', dir.map(([up]) => up))
      pressureSeries('p_outlet', dir.map(([, down]) => down))
    } else if (cp) {
      // No topology: the plate's own recorded pressures (already by actual flow).
      const ref = gauge ? pressureReferencePa(raw, p.node_ids[0] ?? '') : null
      if (ref) reference.pa = ref
      series.push({ id: 'p_inlet', points: cp.inlet_pressure_Pa.map((v, s) => (v - (ref ? ref[s] ?? NaN : 0)) / 1000) })
      series.push({ id: 'p_outlet', points: cp.outlet_pressure_Pa.map((v, s) => (v - (ref ? ref[s] ?? NaN : 0)) / 1000) })
    }
  } else if (target.kind === 'server') {
    const slash = target.key.indexOf('/')
    const rackId = target.key.slice(0, slash), serverId = target.key.slice(slash + 1)
    const server = p.rack_summaries.find((r) => r.rack_id === rackId)?.servers.find((x) => x.server_id === serverId)
    if (!server) return null
    const binding = ix.bindings[target.key]
    const inNode = binding?.node_ids.find((x) => x.endsWith('_in'))
    const outNode = binding?.node_ids.find((x) => x.endsWith('_out'))
    const cpEdge = server.cp_edge_ids.find((e) => ix.edgeIndex.has(e))
    const signed = server.signed_flow_kg_s ?? (cpEdge ? p.edge_flow_kg_s[ix.edgeIndex.get(cpEdge)!] ?? null : null)
    const ends = inNode && outNode && signed
      ? samples.map((s) => (val(signed, s) >= 0 ? [inNode, outNode] : [outNode, inNode]) as [string, string])
      : inNode && outNode ? samples.map(() => [inNode, outNode] as [string, string]) : null
    if (quantity === 'temperature') {
      series.push({ id: 'chip', points: server.max_chip_temp_K.map(toCelsius) })
      if (ends) {
        series.push({ id: 'inlet', points: celsius(ends.map(([a]) => a)) })
        series.push({ id: 'outlet', points: celsius(ends.map(([, b]) => b)) })
      }
    } else if (quantity === 'flow') {
      flowSeries('flow', server.flow_kg_s)
    } else if (ends) {
      pressureSeries('p_inlet', ends.map(([a]) => a))
      pressureSeries('p_outlet', ends.map(([, b]) => b))
    }
  } else {
    const key = target.key
    const supply = `${key}_supply`, ret = `${key}_return`, discharge = `${key}_pump_discharge`
    const has = (node: string) => ix.nodeIndex.has(node)
    const cduEdges = ix.bindings[key]?.edge_ids ?? []
    if (quantity === 'temperature') {
      if (has(supply)) series.push({ id: 'supply', points: celsius(samples.map(() => supply)) })
      if (has(ret)) series.push({ id: 'return', points: celsius(samples.map(() => ret)) })
      ;(p.hx_edge_ids ?? []).forEach((hx, k) => {
        if (!cduEdges.includes(hx)) return
        const circuit = p.primary_circuits?.find((c) => c.edge_id === hx)
        series.push({ id: 'primary_inlet', points: samples.map((s) => {
          const v = p.controls?.[s]?.primary_inlet_K[hx] ?? circuit?.inlet_K
          return finite(v) ? toCelsius(v) : NaN
        }) })
        series.push({ id: 'primary_return', points: samples.map((s) => toCelsius(val(p.primary_return_K?.[k], s))) })
      })
    } else if (quantity === 'flow') {
      const pump = cduEdges.find((e) => {
        const i = ix.edgeIndex.get(e)
        return i !== undefined && (p.edge_types ? p.edge_types[i] === 'pump' : !(p.hx_edge_ids ?? []).includes(e))
      })
      if (pump) flowSeries('pump_flow', samples.map((s) => val(p.edge_flow_kg_s[ix.edgeIndex.get(pump)!], s)))
    } else {
      for (const [id, node] of [['p_supply', supply], ['p_return', ret], ['p_discharge', discharge]] as const) {
        if (has(node)) pressureSeries(id, samples.map(() => node))
      }
    }
  }
  const unit = quantity === 'temperature' ? '°C' : quantity === 'flow' ? 'L/min' : 'kPa'
  return { quantity, unit, gauge: gauge && reference.pa !== null, referencePa: reference.pa, series }
}

/** What a chart can be drawn for: servers, cold plates, pipe edges and CDUs of this result. */
export interface ChartCatalog {
  servers: { key: string; label: string }[]
  coldPlates: { edgeId: string; label: string }[]
  pipes: { edgeId: string; label: string }[]
  cdus: { key: string; label: string }[]
}

export function chartCatalog(raw: unknown): ChartCatalog | null {
  const ix = indexed(raw)
  const table = liquidNetworkTable(raw, -1)
  if (!ix || !table) return null
  const name = (row: NetworkRow) => rowDisplayName(row)
  return {
    servers: ix.parsed.rack_summaries.flatMap((rack) => rack.servers.map((server) => {
      const key = `${rack.rack_id}/${server.server_id}`
      return { key, label: ix.bindings[key]?.label ?? key }
    })),
    coldPlates: table.rows.filter((r) => r.type === 'cold_plate').map((r) => ({ edgeId: r.edgeId, label: name(r) })),
    pipes: table.rows.filter((r) => r.type !== 'cold_plate').map((r) => ({ edgeId: r.edgeId, label: name(r) })),
    cdus: Object.entries(ix.bindings).filter(([, b]) => b.kind === 'cdu').map(([key, b]) => ({ key, label: b.label })),
  }
}

/**
 * What the charts plot for the current focus: the picked edge; else the
 * server or CDU whose edges ARE the selection; else the selection's first edge.
 */
export function seriesTargetForFocus(raw: unknown, focusEdgeIds: readonly string[] | undefined, focusedEdge: string | null): SeriesTarget | null {
  const ix = indexed(raw)
  if (!ix) return null
  if (focusedEdge && ix.edgeIndex.has(focusedEdge)) return { kind: 'edge', edgeId: focusedEdge }
  const focus = focusEdgeIds ?? []
  if (!focus.length) return null
  const same = (edges: string[]) => edges.length === focus.length && edges.every((e) => focus.includes(e))
  for (const [key, b] of Object.entries(ix.bindings)) {
    if (b.kind === 'server' && same(b.edge_ids)) return { kind: 'server', key }
    if (b.kind === 'cdu' && same(b.edge_ids)) return { kind: 'cdu', key }
  }
  const first = focus.find((e) => ix.edgeIndex.has(e))
  return first ? { kind: 'edge', edgeId: first } : null
}
