/**
 * The liquid solver's result document, typed.
 *
 * The shape is taken from a real `liquid_result.json` and from what
 * `js/liquid_results_panel.js` reads, not from the Python dataclasses: the
 * server serialises through `/api/liquid/results` and the panel is what has to
 * agree with the wire format.
 *
 * Every per-component quantity is a TIME SERIES of length `time_s.length`, even
 * for a steady run — a steady solve is just `N_t` steps that stop changing. So
 * there is one code path for both, and "the steady answer" is the last sample.
 * Reading index 0 instead would report the initial condition as the result.
 *
 * Temperatures are KELVIN on the wire and Celsius in the UI. The conversion
 * lives here and nowhere else; a stray 273.15 in a component is how a panel
 * ends up showing 35 °C next to another showing 308 °C.
 */

export const KELVIN_OFFSET = 273.15

/** Water density used by the legacy panel to turn kg/s into LPM. */
const RHO_LPM = 997.0

export interface ServerSummary {
  server_id: string
  flow_kg_s: number[]
  max_chip_temp_K: number[]
  heat_removed_W: number[]
  cp_edge_ids: string[]
  /**
   * The branch flow WITH its sign (positive in the modelled direction), from
   * results written since the result topology landed; `flow_kg_s` is |flow|.
   * Null on older files.
   */
  signed_flow_kg_s?: number[] | null
}

/** One `object_bindings` entry: which graph nodes and edges a project object owns. */
export interface ResultObjectBinding {
  label: string
  kind: string
  node_ids: string[]
  edge_ids: string[]
  parent_uid: string | null
}

/** Per-sample solver diagnostics of a DAE run (W, kg/s, Pa; scaled residual is dimensionless). */
export type ResultDiagnostics = Partial<Record<
  'heat_input_W' | 'primary_rejection_W' | 'storage_rate_W' | 'boundary_exchange_W' |
  'energy_balance_W' | 'mass_residual_kg_s' | 'momentum_residual_Pa' | 'scaled_residual', number>>

/** Per-sample actuator state of a DAE run. */
export interface ResultControls {
  pump_rpm_ratios: Record<string, number>
  valve_openings: Record<string, number>
  primary_inlet_K: Record<string, number>
}

export interface RackSummary {
  rack_id: string
  total_flow_kg_s: number[]
  supply_temp_K: number[]
  return_temp_K: number[]
  servers: ServerSummary[]
}

export interface ColdPlateDetail {
  edge_id: string
  chip_temp_K: number[]
  water_inlet_temp_K: number[]
  water_outlet_temp_K: number[]
  inlet_pressure_Pa: number[]
  outlet_pressure_Pa: number[]
}

export interface LiquidResults {
  converged: boolean
  /** Lifecycle fields are independent of numerical convergence of accepted samples. */
  status?: string | null
  completed?: boolean | null
  analysis_mode?: 'steady' | 'transient' | null
  expected_duration_s?: number | null
  final_loss: number | null
  epochs: number | null
  time_s: number[]
  rack_summaries: RackSummary[]
  cp_details: ColdPlateDetail[]
  /**
   * The compiled network's own arrays — one row per edge or node, each row a
   * time series.
   *
   * The summaries above are per RACK and per SERVER; these are per PIPE and per
   * JUNCTION, and they are the only place a pipe's mass flow and a node's
   * pressure exist. Nothing in the results PANEL reads them, which is why they
   * were dropped — but they are exactly what the post-processing chart's flow
   * and pressure tabs plot, so a parse that discards them leaves two of the
   * three tabs permanently empty.
   */
  edge_ids: string[]
  edge_flow_kg_s: number[][]
  node_ids: string[]
  node_pressure_Pa: number[][]
  /**
   * The pre-`cp_details` flat arrays, indexed by the order the cold plates
   * appear across `rack_summaries[].servers[].cp_edge_ids`.
   *
   * Kept because a result written before `cp_details` existed still has to
   * plot: the chart falls back to these, and without them an older run opens a
   * window with no curves and no explanation.
   */
  chip_temp_K: number[][]
  water_outlet_temp_K: number[][]

  /** Which solver wrote the file: 'differentiable_newton_dae' for the DAE workbench. */
  solver?: string | null
  run_id?: string | null
  /** [N][t] K and [E][t] K; the edge value is at the DOWNSTREAM end of the actual flow. */
  node_temperature_K?: number[][]
  edge_outlet_temp_K?: number[][]
  /**
   * Result topology (2026-10-21 onward; null on older files): each edge's
   * modelled ends, type and bore, each node's scene position (m, y up) and the
   * flags marking stagnant edges and nodes whose temperature is undefined.
   */
  edge_from?: string[] | null
  edge_to?: string[] | null
  edge_types?: string[] | null
  edge_D_m?: (number | null)[] | null
  edge_stagnant?: boolean[] | null
  node_positions_m?: number[][] | null
  node_observable?: boolean[][] | null
  /** Heat exchangers: [HX][t] K. */
  hx_edge_ids?: string[]
  primary_return_K?: number[][]
  secondary_supply_K?: number[][]
  /** One entry per sample. */
  controls?: ResultControls[]
  diagnostics?: ResultDiagnostics[]
  heat_multipliers?: number[]
  cell_edge_ids?: string[]
  cell_temp_K?: number[][]
  object_bindings?: Record<string, ResultObjectBinding>
  /** Recorded solver laws, e.g. `hx_models` {edge id: 'counterflow-ntu' | 'mixed-volumes'}. */
  solver_laws?: { hx_models?: Record<string, string>; pump_law?: string; friction_law?: string; hydrostatics?: string
    /** Laws a run saved before they were recorded gets back from the backend (saved_run.py). */
    inferred?: string[] } | null
  /** Primary circuits as adopted at export: flow kg/s, inlet K, cp, rho, source. */
  primary_circuits?: { edge_id: string; flow_kg_s?: number; inlet_K?: number; rho_kg_m3?: number; source?: string }[]
}

type Dict = Record<string, unknown>

function asDict(v: unknown): Dict {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Dict) : {}
}

function numArray(v: unknown): number[] {
  return Array.isArray(v) ? v.filter((x): x is number => typeof x === 'number') : []
}

function strArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

/** One row per edge or node; a non-array row becomes an empty series, not a hole. */
function numMatrix(v: unknown): number[][] {
  return Array.isArray(v) ? v.map(numArray) : []
}

/**
 * Ids that INDEX into a matrix, so position has to be preserved.
 *
 * `strArray` drops what it cannot use, and `edge_flow_kg_s[edge_ids.indexOf(id)]`
 * would then read one row off for every id after a dropped one — a plausible
 * flow curve belonging to the wrong pipe. A bad entry becomes '' instead, which
 * matches no lookup.
 */
function idArray(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => (typeof x === 'string' ? x : '')) : []
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}

/** A positional array that must keep its length (it indexes edges or nodes), or null when absent. */
function positional<T>(v: unknown, item: (x: unknown) => T): T[] | null {
  return Array.isArray(v) ? v.map(item) : null
}

/** A row of numbers that keeps its positions: a non-number becomes NaN, not a shift. */
function numRow(v: unknown): number[] {
  return Array.isArray(v) ? v.map((x) => (typeof x === 'number' ? x : NaN)) : []
}

function numRecord(v: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [key, value] of Object.entries(asDict(v))) if (typeof value === 'number') out[key] = value
  return out
}

function parseBindings(v: unknown): Record<string, ResultObjectBinding> {
  const out: Record<string, ResultObjectBinding> = {}
  for (const [key, raw] of Object.entries(asDict(v))) {
    const b = asDict(raw)
    out[key] = {
      label: str(b.label, key), kind: str(b.kind),
      node_ids: strArray(b.node_ids), edge_ids: strArray(b.edge_ids),
      parent_uid: typeof b.parent_uid === 'string' ? b.parent_uid : null,
    }
  }
  return out
}

/** Parse a `/api/liquid/results` payload. Returns null when it carries no run. */
export function parseLiquidResults(raw: unknown): LiquidResults | null {
  const d = asDict(raw)
  const racks = Array.isArray(d.rack_summaries) ? d.rack_summaries : []
  const cps = Array.isArray(d.cp_details) ? d.cp_details : []
  // A payload with neither is not a result — the legacy panel shows its
  // "run a solve first" placeholder for exactly this case rather than
  // rendering a converged banner over an empty table.
  if (racks.length === 0 && cps.length === 0) return null

  const analysis = asDict(d.analysis ?? asDict(d.metadata).analysis)
  const mode = analysis.mode === 'steady' || analysis.mode === 'transient' ? analysis.mode : null
  const duration = d.expected_duration_s
  // Old cancelled DAE documents carry their requested horizon in provenance.
  // Explicit steady mode wins over provenance retained from the source project.
  const provenanceDuration = asDict(asDict(d.parameter_provenance).transient).durationS
  const fallbackDuration = mode !== 'steady' && (mode === 'transient' || d.completed === false)
    ? provenanceDuration : null
  const expectedDuration = typeof duration === 'number' && Number.isFinite(duration) && duration >= 0
    ? duration : typeof fallbackDuration === 'number' && Number.isFinite(fallbackDuration) && fallbackDuration >= 0
      ? fallbackDuration : null

  return {
    status: typeof d.status === 'string' ? d.status : null,
    completed: typeof d.completed === 'boolean' ? d.completed : null,
    analysis_mode: mode,
    expected_duration_s: expectedDuration,
    converged: d.converged === true,
    final_loss: typeof d.final_loss === 'number' ? d.final_loss : null,
    epochs: typeof d.epochs === 'number' ? d.epochs : null,
    time_s: numArray(d.time_s),
    edge_ids: idArray(d.edge_ids),
    edge_flow_kg_s: numMatrix(d.edge_flow_kg_s),
    node_ids: idArray(d.node_ids),
    node_pressure_Pa: numMatrix(d.node_pressure_Pa),
    chip_temp_K: numMatrix(d.chip_temp_K),
    water_outlet_temp_K: numMatrix(d.water_outlet_temp_K),
    solver: typeof d.solver === 'string' ? d.solver : null,
    run_id: typeof d.run_id === 'string' ? d.run_id : null,
    node_temperature_K: positional(d.node_temperature_K, numRow) ?? [],
    edge_outlet_temp_K: positional(d.edge_outlet_temp_K, numRow) ?? [],
    edge_from: positional(d.edge_from, (x) => (typeof x === 'string' ? x : '')),
    edge_to: positional(d.edge_to, (x) => (typeof x === 'string' ? x : '')),
    edge_types: positional(d.edge_types, (x) => (typeof x === 'string' ? x : '')),
    edge_D_m: positional(d.edge_D_m, (x) => (typeof x === 'number' && Number.isFinite(x) ? x : null)),
    edge_stagnant: positional(d.edge_stagnant, (x) => x === true),
    node_positions_m: positional(d.node_positions_m, numRow),
    node_observable: positional(d.node_observable, (row) => (Array.isArray(row) ? row.map((x) => x !== false) : [])),
    hx_edge_ids: idArray(d.hx_edge_ids),
    primary_return_K: positional(d.primary_return_K, numRow) ?? [],
    secondary_supply_K: positional(d.secondary_supply_K, numRow) ?? [],
    controls: Array.isArray(d.controls) ? d.controls.map((c) => {
      const cd = asDict(c)
      return {
        pump_rpm_ratios: numRecord(cd.pump_rpm_ratios),
        valve_openings: numRecord(cd.valve_openings),
        primary_inlet_K: numRecord(cd.primary_inlet_K),
      }
    }) : [],
    diagnostics: Array.isArray(d.diagnostics) ? d.diagnostics.map(numRecord) : [],
    heat_multipliers: numRow(d.heat_multipliers),
    cell_edge_ids: idArray(d.cell_edge_ids),
    cell_temp_K: positional(d.cell_temp_K, numRow) ?? [],
    object_bindings: parseBindings(d.object_bindings),
    solver_laws: (() => {
      const laws = asDict(asDict(d.parameter_provenance).solver_laws)
      if (!Object.keys(laws).length) return null
      const hx = asDict(laws.hx_models)
      const models: Record<string, string> = {}
      for (const [edge, model] of Object.entries(hx)) if (typeof model === 'string') models[edge] = model
      return {
        hx_models: models,
        ...(typeof laws.pump_law === 'string' ? { pump_law: laws.pump_law } : {}),
        ...(typeof laws.friction_law === 'string' ? { friction_law: laws.friction_law } : {}),
        ...(typeof laws.hydrostatics === 'string' ? { hydrostatics: laws.hydrostatics } : {}),
        ...(Array.isArray(laws.inferred)
          ? { inferred: laws.inferred.filter((k): k is string => typeof k === 'string') } : {}),
      }
    })(),
    primary_circuits: (Array.isArray(asDict(d.parameter_provenance).primary_circuits)
      ? asDict(d.parameter_provenance).primary_circuits as unknown[] : []).map((c) => {
      const cd = asDict(c)
      return {
        edge_id: str(cd.edge_id),
        ...(typeof cd.flow_kg_s === 'number' ? { flow_kg_s: cd.flow_kg_s } : {}),
        ...(typeof cd.inlet_K === 'number' ? { inlet_K: cd.inlet_K } : {}),
        ...(typeof cd.rho_kg_m3 === 'number' ? { rho_kg_m3: cd.rho_kg_m3 } : {}),
        ...(typeof cd.source === 'string' ? { source: cd.source } : {}),
      }
    }),
    rack_summaries: racks.map((r) => {
      const rd = asDict(r)
      const servers = Array.isArray(rd.servers) ? rd.servers : []
      return {
        rack_id: str(rd.rack_id, '(unnamed rack)'),
        total_flow_kg_s: numArray(rd.total_flow_kg_s),
        supply_temp_K: numArray(rd.supply_temp_K),
        return_temp_K: numArray(rd.return_temp_K),
        servers: servers.map((s) => {
          const sd = asDict(s)
          return {
            server_id: str(sd.server_id, '(unnamed server)'),
            flow_kg_s: numArray(sd.flow_kg_s),
            max_chip_temp_K: numArray(sd.max_chip_temp_K),
            heat_removed_W: numArray(sd.heat_removed_W),
            cp_edge_ids: strArray(sd.cp_edge_ids),
            signed_flow_kg_s: Array.isArray(sd.signed_flow_kg_s) ? numRow(sd.signed_flow_kg_s) : null,
          }
        }),
      }
    }),
    cp_details: cps.map((c) => {
      const cd = asDict(c)
      return {
        edge_id: str(cd.edge_id, '(unnamed cold plate)'),
        chip_temp_K: numArray(cd.chip_temp_K),
        water_inlet_temp_K: numArray(cd.water_inlet_temp_K),
        water_outlet_temp_K: numArray(cd.water_outlet_temp_K),
        inlet_pressure_Pa: numArray(cd.inlet_pressure_Pa),
        outlet_pressure_Pa: numArray(cd.outlet_pressure_Pa),
      }
    }),
  }
}

/**
 * The steady value of a series: its LAST sample.
 *
 * Matches `_lastVal` (js/liquid_results_panel.js:14). Zero for an empty series
 * rather than NaN, so an absent quantity renders as "0.0" instead of poisoning
 * a total that is summed across racks.
 */
export function lastVal(series: number[]): number {
  return series.length ? (series[series.length - 1] as number) : 0
}

export function toCelsius(kelvin: number): number {
  return kelvin - KELVIN_OFFSET
}

/** kg/s to litres per minute, the unit the panel shows flows in. */
export function toLPM(kgPerS: number): number {
  return (kgPerS / RHO_LPM) * 60000
}

/** A transient initializer is still transient, even before the first time step. */
export function isTransient(results: LiquidResults): boolean {
  if (results.analysis_mode) return results.analysis_mode === 'transient'
  return results.time_s.length > 1
    || (results.completed === false && (results.expected_duration_s ?? 0) > 0)
}

export type LiquidResultLifecycle = 'cancelled' | 'failed' | 'incomplete'
  | 'transient-completed' | 'steady-converged' | 'numerical-converged' | 'not-converged'

/** Completion describes the requested run; convergence describes its accepted samples. */
export function liquidResultLifecycle(results: LiquidResults): LiquidResultLifecycle {
  if (results.status === 'cancelled') return 'cancelled'
  if (results.status === 'failed' || results.status === 'error') return 'failed'
  const duration = results.expected_duration_s
  const terminal = results.time_s.at(-1)
  const horizonMissing = isTransient(results) && duration != null
    && (terminal == null || !Number.isFinite(terminal)
      || Math.abs(terminal - duration) > 1e-8 * Math.max(Math.abs(duration), 1e-12))
  if (results.completed === false || (results.status != null && results.status !== 'completed') || horizonMissing) {
    return 'incomplete'
  }
  if (isTransient(results) && (results.completed === true || results.status === 'completed')) {
    return 'transient-completed'
  }
  if (results.analysis_mode === 'steady' && results.converged) return 'steady-converged'
  return results.converged ? 'numerical-converged' : 'not-converged'
}

export interface ChipStatus {
  status: 'ok' | 'warn' | 'error'
  color: string
}

/**
 * Chip temperature banding, from `_chipStatus` (js/liquid_results_panel.js:19).
 *
 * The thresholds are the legacy ones and are deliberately not made
 * configurable here: they exist in the .cfdproj nowhere, so a second set of
 * numbers in React would be a second opinion on what "too hot" means.
 */
export function chipStatus(tempC: number): ChipStatus {
  if (tempC < 75) return { status: 'ok', color: '#ccc' }
  if (tempC < 85) return { status: 'warn', color: '#ffcc00' }
  return { status: 'error', color: '#ff4444' }
}

export interface SystemOverview {
  totalFlowLPM: number
  maxChipC: number
  rackCount: number
  serverCount: number
}

/** Totals across every rack, as the legacy "系统概览 / System Overview" block computes them. */
export function systemOverview(results: LiquidResults): SystemOverview {
  let totalFlow = 0
  let maxChipC = 0
  let serverCount = 0
  for (const rack of results.rack_summaries) {
    totalFlow += lastVal(rack.total_flow_kg_s)
    for (const srv of rack.servers) {
      serverCount += 1
      const c = toCelsius(lastVal(srv.max_chip_temp_K))
      if (c > maxChipC) maxChipC = c
    }
  }
  return {
    totalFlowLPM: toLPM(totalFlow),
    maxChipC,
    rackCount: results.rack_summaries.length,
    serverCount,
  }
}
