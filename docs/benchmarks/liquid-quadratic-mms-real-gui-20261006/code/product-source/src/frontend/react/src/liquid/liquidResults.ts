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
  emoji: string
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
  if (tempC < 75) return { emoji: '✅', color: '#ccc' }
  if (tempC < 85) return { emoji: '⚠️', color: '#ffcc00' }
  return { emoji: '🔴', color: '#ff4444' }
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
