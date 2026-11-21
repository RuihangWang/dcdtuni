/**
 * The liquid post-processing view: which solve is being read, and what a
 * selected CDU or cold plate plots.
 *
 * Two things the legacy keeps apart and this module keeps together, because
 * they are the same question asked twice:
 *
 *   - the RUN selector (`_lsmRefreshPPRuns` / `_lsmOnPPRunChange`,
 *     js/liquid_solve_monitor.js:192-206), which decides WHICH
 *     `liquid_result.json` under `case/<name>/log` is being read, and
 *   - the CHART (`_lsmShowPPWindow`, :798), which turns that document into
 *     temperature, flow and pressure series for one object.
 *
 * Without the selector only the run the session happened to fetch last can be
 * looked at, and an earlier solve sitting on disk is unreachable without a page
 * reload.
 */

import { getCaseStatus } from '../run/session'
import { fetchLiquidResults } from './liquidSession'
import { type LiquidResults, parseLiquidResults } from './liquidResults'

export type PpTab = 'temperature' | 'flow' | 'pressure'

export const PP_TABS: PpTab[] = ['temperature', 'flow', 'pressure']

/** `_lsmColors` (js/liquid_solve_monitor.js:56), cycled by series index. */
export const PP_COLORS = [
  '#e06c75',
  '#e5c07b',
  '#61afef',
  '#c678dd',
  '#56b6c2',
  '#98c379',
  '#d19a66',
  '#ff6b9d',
  '#4ecdc4',
  '#f7dc6f',
]

export interface PpSeries {
  label: string
  color: string
  points: number[]
}

export type PpDatasets = Record<PpTab, PpSeries[]>

export function emptyDatasets(): PpDatasets {
  return { temperature: [], flow: [], pressure: [] }
}

// ---------------------------------------------------------------------------
// run selection
// ---------------------------------------------------------------------------

export interface LiquidRunOption {
  /** What goes to `/api/liquid/results?run=`. */
  value: string
  label: string
}

interface RawLiquidRun {
  name?: unknown
  path?: unknown
}

/**
 * The solve records offered by the run selector.
 *
 * `scan-case-status` already lists them — a directory under `case/<name>/log` or
 * `case/<name>/Liquid/log` counts as a liquid run when it holds a
 * `liquid_result.json`. The value is `path || name`, as the legacy option is
 * built: the server resolves `?run=` under its own log bases, and an absolute
 * path joined onto a base still lands on the same file, so both forms work and
 * this one matches the legacy DOM.
 *
 * An empty list is NOT an empty selector. `hasResults` distinguishes "there is
 * a result loaded but no run directory was found" — which offers "Latest", the
 * unqualified endpoint — from "nothing has ever been solved here".
 */
export function liquidRunOptions(
  runs: unknown[] | null | undefined,
  hasResults: boolean,
  noRecordsLabel: string,
  latestLabel = 'Latest',
): LiquidRunOption[] {
  const list = (runs ?? []).filter((r): r is RawLiquidRun => !!r && typeof r === 'object')
  const records = list.map((run) => {
    const name = typeof run.name === 'string' ? run.name : ''
    const path = typeof run.path === 'string' ? run.path : ''
    return { value: path || name, label: name || path }
  }).filter((option) => option.value !== '')
  if (records.length === 0) {
    return [{ value: '', label: hasResults ? latestLabel : noRecordsLabel }]
  }
  // The unqualified endpoint can hold a newer partial or cancelled run. Keep
  // its empty selection explicit instead of displaying a historical record
  // while the left panel reads that newer result.
  return [{ value: '', label: latestLabel }, ...records]
}

/** The liquid runs the last `scan-case-status` found. */
export function knownLiquidRuns(): unknown[] {
  return getCaseStatus()?.liquidRuns ?? []
}

// ---------------------------------------------------------------------------
// what a click is asking to plot
// ---------------------------------------------------------------------------

export type PpTarget =
  | { kind: 'cdu'; label: string }
  | {
      kind: 'cp'
      label: string
      /** The cold plate's own `edge_id`, when the project carries one. */
      edgeId: string | null
      /** Rack and server NAMES, which is what the result document keys by. */
      rackId: string
      serverId: string
      rackUid?: string
      serverUid?: string
      cpName: string
    }

type Bag = Record<string, unknown>

/**
 * Which cold-plate edge in the RESULT this target is.
 *
 * The legacy derives `rack/server/CP-name` (STL_generate.html:12892) and the
 * compiler emits `rack_server_cp0`, so that derived string matches nothing in
 * any result this repo has on disk — a cold-plate click opens a window with an
 * empty temperature tab. Resolving against the result's own
 * `servers[].cp_edge_ids` instead is not a different convention, it is reading
 * the id the solver actually wrote. The derived string stays as the last
 * fallback so a result that predates `cp_edge_ids` still resolves.
 */
export function resolveColdPlateEdgeId(
  result: LiquidResults,
  target: Extract<PpTarget, { kind: 'cp' }>,
): string {
  if (target.edgeId) return target.edgeId
  const rack = result.rack_summaries.find((r) => r.rack_id === target.rackUid)
    ?? result.rack_summaries.find((r) => r.rack_id === target.rackId)
  const server = rack?.servers.find((s) => s.server_id === target.serverUid)
    ?? rack?.servers.find((s) => s.server_id === target.serverId)
  const first = server?.cp_edge_ids[0]
  if (first) return first
  return `${target.rackId}/${target.serverId}/${target.cpName}`
}

function series(label: string, points: number[], index: number): PpSeries {
  return { label, points, color: PP_COLORS[index % PP_COLORS.length] as string }
}

/**
 * The system boundary pressures, from `_lsmAddBCPressure`
 * (js/liquid_solve_monitor.js:891).
 *
 * Only the nodes whose id names a supply, a return or a BC: every internal node
 * in a 200-rack network would otherwise be a line on one chart.
 */
function bcPressureSeries(result: LiquidResults, from: number): PpSeries[] {
  const out: PpSeries[] = []
  result.node_ids.forEach((nid, i) => {
    if (
      nid.indexOf('supply') < 0 &&
      nid.indexOf('return') < 0 &&
      nid.indexOf('_bc') < 0 &&
      nid.indexOf('BC') < 0
    ) {
      return
    }
    const points = result.node_pressure_Pa[i]
    if (points && points.length) out.push(series(nid, points, from + out.length))
  })
  return out
}

/** The three tabs' series for one object. A port of `_lsmShowPPWindow`. */
export function buildPpDatasets(result: LiquidResults, target: PpTarget): PpDatasets {
  const out = emptyDatasets()

  if (target.kind === 'cdu') {
    result.rack_summaries.forEach((rack) => {
      if (rack.supply_temp_K.length) {
        out.temperature.push(
          series(`Supply T (${rack.rack_id})`, rack.supply_temp_K, out.temperature.length),
        )
      }
      if (rack.return_temp_K.length) {
        out.temperature.push(
          series(`Return T (${rack.rack_id})`, rack.return_temp_K, out.temperature.length),
        )
      }
      if (rack.total_flow_kg_s.length) {
        out.flow.push(series(`Flow (${rack.rack_id})`, rack.total_flow_kg_s, out.flow.length))
      }
    })
    out.pressure.push(...bcPressureSeries(result, 0))
    return out
  }

  const edgeId = resolveColdPlateEdgeId(result, target)
  const detail = result.cp_details.find((d) => d.edge_id === edgeId)
  if (detail) {
    const add = (label: string, points: number[]) => {
      if (points.length) out.temperature.push(series(label, points, out.temperature.length))
    }
    add('T_inlet', detail.water_inlet_temp_K)
    add('T_chip', detail.chip_temp_K)
    add('T_outlet', detail.water_outlet_temp_K)
    if (detail.inlet_pressure_Pa.length) {
      out.pressure.push(series('P_inlet', detail.inlet_pressure_Pa, 0))
    }
    if (detail.outlet_pressure_Pa.length) {
      out.pressure.push(series('P_outlet', detail.outlet_pressure_Pa, out.pressure.length))
    }
  } else {
    // Pre-`cp_details` result: the flat arrays, indexed by the cold plate's
    // position across every server of every rack.
    const order: string[] = []
    for (const rack of result.rack_summaries) {
      for (const srv of rack.servers) order.push(...srv.cp_edge_ids)
    }
    const idx = order.indexOf(edgeId)
    if (idx >= 0) {
      const chip = result.chip_temp_K[idx]
      if (chip?.length) out.temperature.push(series('T_chip', chip, 0))
      const water = result.water_outlet_temp_K[idx]
      if (water?.length) out.temperature.push(series('T_outlet', water, out.temperature.length))
    }
    out.pressure.push(...bcPressureSeries(result, 0))
  }

  const edgeIdx = result.edge_ids.indexOf(edgeId)
  const flow = edgeIdx >= 0 ? result.edge_flow_kg_s[edgeIdx] : undefined
  if (flow?.length) out.flow.push(series('m', flow, 0))

  return out
}

// ---------------------------------------------------------------------------
// resolving a model-tree click, the legacy `_lsmOnTreeClickPostProcess`
// ---------------------------------------------------------------------------

export interface TreeClick {
  /** The model the tree selected. */
  model: { uid: string; kind?: string; meta?: { name?: string } } | null
  /** A server inside that model, when the tree selected one. */
  equipmentId?: string | null
}

/**
 * What clicking a model in the tree should plot, or null for "nothing".
 *
 * A port of `window._lsmOnTreeClickPostProcess` (STL_generate.html:12853),
 * including its two odd rules: a model whose NAME contains "cdu" counts as a
 * CDU even if its kind is not, and a rack itself plots nothing — only a server
 * inside it does, because a rack's curve is the CDU's rack row and is already
 * on that chart.
 */
export function resolveTreeClick(click: TreeClick): PpTarget | null {
  const model = click.model
  if (!model) return null
  const name = model.meta?.name ?? ''
  if (model.kind === 'cdu' || name.toLowerCase().indexOf('cdu') >= 0) {
    return { kind: 'cdu', label: 'CDU' }
  }
  if (model.kind !== 'rack' || !click.equipmentId) return null

  const servers = ((model as unknown as Bag).constructions as Bag | undefined)?.servers as
    | Record<string, Bag>
    | undefined
  const server = servers?.[click.equipmentId]
  if (!server) return null
  // The pairs are the server's cold plates. `constructions.coldPlates` is a
  // copy the v3→v4 migration left behind (empty on a server the editor made).
  const liquid = (server.liquidCooling ?? {}) as Bag
  const pairs = Array.isArray(liquid.cpCuPairs) ? (liquid.cpCuPairs as Bag[]) : []
  if (pairs.length === 0) return null
  const cp = pairs[0] as Bag
  const serverId = String((server.meta as Bag | undefined)?.name ?? server.uid ?? click.equipmentId)
  const cpName = String(cp.name ?? 'CP-0')
  return {
    kind: 'cp',
    label: `${serverId} / ${cpName}`,
    edgeId: typeof cp.edge_id === 'string' ? cp.edge_id : null,
    rackId: String(name || model.uid),
    serverId,
    rackUid: model.uid,
    serverUid: String(server.uid ?? click.equipmentId),
    cpName,
  }
}

// ---------------------------------------------------------------------------
// the open window
// ---------------------------------------------------------------------------

export interface PpWindowState {
  open: boolean
  title: string
  loading: boolean
  error: string | null
  tab: PpTab
  time: number[]
  datasets: PpDatasets
  /**
   * Series the legend has been clicked to hide, keyed by label.
   *
   * Hidden, not removed: a rack with twenty cold plates draws twenty
   * overlapping curves, and the only way back from thinning them out is the
   * legend entry that hid one — so the entry stays, dimmed
   * (`_lsmUpdatePPLegend`, js/liquid_solve_monitor.js:1255-1270).
   */
  hidden: Record<string, boolean>
  /** `window._lsmFontScale` — the A− / A+ header buttons, 0.6 to 2.0. */
  fontScale: number
}

const CLOSED: PpWindowState = {
  open: false,
  title: '',
  loading: false,
  error: null,
  tab: 'temperature',
  time: [],
  datasets: emptyDatasets(),
  hidden: {},
  fontScale: 1,
}

export const PP_FONT_STEP = 0.1
export const PP_FONT_MIN = 0.6
export const PP_FONT_MAX = 2

let windowState: PpWindowState = CLOSED
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribePpWindow(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function ppWindowState(): PpWindowState {
  return windowState
}

export function setPpWindowTab(tab: PpTab) {
  if (windowState.tab === tab) return
  windowState = { ...windowState, tab }
  emit()
}

/** Flip one series between drawn and dimmed. */
export function togglePpSeries(label: string) {
  const hidden = { ...windowState.hidden, [label]: !windowState.hidden[label] }
  windowState = { ...windowState, hidden }
  emit()
}

/** Nudge the chart font, clamped as `_lsmFontScale` is (js/liquid_solve_monitor.js:1038-1048). */
export function nudgePpFontScale(delta: number) {
  const next = Math.min(PP_FONT_MAX, Math.max(PP_FONT_MIN, windowState.fontScale + delta))
  if (next === windowState.fontScale) return
  windowState = { ...windowState, fontScale: next }
  emit()
}

/**
 * The current tab's series, minus the hidden ones.
 *
 * The legend must be built from `datasets[tab]` instead, so an entry survives
 * being switched off.
 */
export function visiblePpSeries(state: PpWindowState): PpSeries[] {
  return state.datasets[state.tab].filter((s) => !state.hidden[s.label])
}

export function closePpWindow() {
  if (!windowState.open) return
  windowState = CLOSED
  emit()
}

/** Which run the selector is pointing at; '' means the newest one. */
let selectedRun = ''

export function selectedLiquidRun(): string {
  return selectedRun
}

export function setSelectedLiquidRun(run: string) {
  selectedRun = run
}

/**
 * Open the chart window for `target`, fetching the selected run's results.
 *
 * Opens FIRST, with `loading` set, rather than after the fetch: the request goes
 * to disk through the server and a click that appears to do nothing for a second
 * gets clicked again, which starts a second fetch.
 */
export async function openPpWindow(target: PpTarget): Promise<void> {
  windowState = {
    ...CLOSED,
    open: true,
    loading: true,
    title: target.label,
    tab: 'temperature',
  }
  emit()
  const raw = await fetchLiquidResults(selectedRun || undefined)
  const parsed = raw ? parseLiquidResults(raw) : null
  if (!parsed) {
    windowState = { ...windowState, loading: false, error: 'no liquid results for this run' }
    emit()
    return
  }
  windowState = {
    ...windowState,
    loading: false,
    error: null,
    time: parsed.time_s,
    datasets: buildPpDatasets(parsed, target),
  }
  emit()
}
