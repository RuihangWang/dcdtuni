/**
 * The live solver monitor: what the floating chart window shows.
 *
 * A module store rather than component state, for the reason the legacy keeps
 * `window._solverMonitor` global: the window has to outlive the panel that
 * started the run. The user presses 求解 on the solver tab and then goes to look
 * at something else, and a chart that unmounted with the panel would throw away
 * a run's whole residual history at the moment it became interesting.
 *
 * The data comes off `jobStream`, a second reader of the job log — see that
 * module for why it is not the session store's copy.
 *
 * Ported from `_solverMonitor` / `_smSensorPoller` / `_smWindowState`
 * (STL_generate.html:25042-25101, :23576).
 */

import { addJobStreamHandler, watchJobStream } from './jobStream'
import { getMonitoringConfig } from './monitoring'
import {
  absorbProbeSample,
  emptyMonitorData,
  parseSolverLogLine,
  type MonitorData,
} from './solverMonitorParse'
import type { ChartTab } from './solverChart'

export interface WindowGeometry {
  top: number
  left: number
  width: number
  height: number
}

/** The legacy defaults: 640x400, pinned 20px from the right edge (:25044). */
const DEFAULT_SIZE = { width: 640, height: 400 }
export const MIN_WIDTH = 420
export const MIN_HEIGHT = 220

interface MonitorState {
  open: boolean
  /** True while the job that opened the window is still producing lines. */
  active: boolean
  collapsed: boolean
  hasTemperature: boolean
  tab: ChartTab
  hidden: Record<string, boolean>
  fontScale: number
  geometry: WindowGeometry
  data: MonitorData
  /** Bumped when the chart has something new to draw. */
  revision: number
}

function defaultGeometry(): WindowGeometry {
  const w = typeof window === 'undefined' ? 1280 : window.innerWidth
  // Below the toolbar: at top 60 the window opened over the toolbar's second
  // row and hid its right-hand buttons until it was dragged away.
  const bar = typeof document === 'undefined' ? null : document.getElementById('toolbar')
  const barBottom = bar ? bar.getBoundingClientRect().bottom : 0
  return {
    top: Math.max(60, Math.round(barBottom) + 8),
    left: Math.max(0, w - DEFAULT_SIZE.width - 20),
    width: DEFAULT_SIZE.width,
    height: DEFAULT_SIZE.height,
  }
}

let state: MonitorState = {
  open: false,
  active: false,
  collapsed: false,
  hasTemperature: false,
  tab: 'residual',
  hidden: {},
  fontScale: 1,
  geometry: defaultGeometry(),
  data: emptyMonitorData(),
  revision: 0,
}

type Listener = () => void
const listeners = new Set<Listener>()

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribeSolverMonitor(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getSolverMonitor(): Readonly<MonitorState> {
  return state
}

export function setMonitorTab(tab: ChartTab) {
  state = { ...state, tab, revision: state.revision + 1 }
  emit()
}

export function toggleMonitorSeries(name: string) {
  state = {
    ...state,
    hidden: { ...state.hidden, [name]: !state.hidden[name] },
    revision: state.revision + 1,
  }
  emit()
}

/** Clamped to the legacy 0.5x-2.0x range (:25229). */
export function bumpMonitorFont(delta: number) {
  const next = Math.min(2, Math.max(0.5, Math.round((state.fontScale + delta) * 10) / 10))
  state = { ...state, fontScale: next, revision: state.revision + 1 }
  emit()
}

export function setMonitorCollapsed(collapsed: boolean) {
  state = { ...state, collapsed }
  emit()
}

/**
 * Remember where the window was put.
 *
 * Position and size survive a close and reopen, as they do in the legacy
 * (`_smRememberWindowState`): a user who dragged the plot off their geometry
 * should not have to do it again on the next solve.
 */
export function setMonitorGeometry(geometry: WindowGeometry) {
  state = { ...state, geometry, revision: state.revision + 1 }
  emit()
}

export function closeSolverMonitor() {
  state = { ...state, open: false, active: false }
  emit()
}

/**
 * Sensor probe names to poll for, set by the panel that starts the run.
 *
 * Held here rather than read from the project inside the poller, because the
 * poller indexes `fields.T[i][j]` by the ORDER the names were asked in — a list
 * rebuilt from a project edited mid-solve would attach one probe's curve to
 * another probe's name.
 */
let sensorNames: string[] = []
let sensorTimer: ReturnType<typeof setInterval> | null = null
let sensorSince = 0
let sensorInFlight = false

function stopSensorPoller() {
  if (sensorTimer) clearInterval(sensorTimer)
  sensorTimer = null
}

async function fetchProbesNow(): Promise<void> {
  if (sensorInFlight || !sensorNames.length) return
  sensorInFlight = true
  try {
    const resp = await fetch(`/api/probes-latest?since=${sensorSince}`)
    const payload = (await resp.json()) as { steps?: number[]; fields?: { T?: (number | null)[][] } }
    if (!payload?.steps) return
    sensorSince = absorbProbeSample(state.data, sensorNames, payload, sensorSince)
    if (state.tab === 'sensor') {
      state = { ...state, revision: state.revision + 1 }
      emit()
    }
  } catch {
    // A missed poll is a gap in one curve, not a reason to stop the run.
  } finally {
    sensorInFlight = false
  }
}

function startSensorPoller(names: string[]) {
  sensorNames = names
  sensorSince = 0
  stopSensorPoller()
  if (!names.length) return
  sensorTimer = setInterval(() => void fetchProbesNow(), 2000)
}

/**
 * Should a solve open the monitor at all?
 *
 * The legacy condition (STL_generate.html:28237): residuals asked for, or any
 * temperature patch picked, or any sensor placed. With none of those the plot
 * would have no series to draw and the window would only cover the viewport.
 */
export function shouldOpenMonitor(sensorCount: number): boolean {
  const cfg = getMonitoringConfig()
  return cfg.residuals || cfg.serverTemperature.length > 0 || sensorCount > 0
}

/**
 * Arm the monitor for a solve that is about to start.
 *
 * Called BEFORE the job is submitted so the window is up while the server is
 * still starting the solver — that wait is minutes on a large case, and a plot
 * that only appears with the first residual reads as a feature that failed.
 */
export function openSolverMonitor(probeNames: string[]): void {
  watchJobStream()
  const cfg = getMonitoringConfig()
  const hasTemperature =
    cfg.serverTemperature.length > 0 || cfg.acuReturnTemperature === 'all' || cfg.acuReturnTemperature.length > 0
  state = {
    ...state,
    open: true,
    active: true,
    collapsed: false,
    hasTemperature,
    // A tab that only existed for the previous run's temperature patches would
    // otherwise stay selected and show an empty plot.
    tab: state.tab === 'temperature' && !hasTemperature ? 'residual' : state.tab,
    hidden: {},
    data: emptyMonitorData(),
    revision: state.revision + 1,
  }
  startSensorPoller(probeNames)
  emit()
}

/** The run ended: keep the plot, stop collecting. */
function deactivate() {
  stopSensorPoller()
  if (!state.active) return
  state = { ...state, active: false, revision: state.revision + 1 }
  emit()
}

let handlerInstalled = false

/**
 * Feed the monitor from the job log.
 *
 * Installed at module load rather than from a component, because the panel that
 * starts a solve is not guaranteed to be mounted when the first line arrives —
 * and a handler registered late loses the beginning of the curve.
 */
export function installSolverMonitorFeed(): void {
  if (handlerInstalled) return
  handlerInstalled = true
  watchJobStream()
  addJobStreamHandler({
    onLine: (kind, line) => {
      if (kind !== 'solve' || !state.active) return
      const outcome = parseSolverLogLine(state.data, line)
      if (outcome === 'probe-poll') {
        if (sensorTimer) void fetchProbesNow()
        return
      }
      if (outcome === 'redraw') {
        state = { ...state, revision: state.revision + 1 }
        emit()
      }
    },
    onDone: (kind) => {
      if (kind !== 'solve') return
      deactivate()
    },
  })
}

/**
 * Publish the monitor where the legacy app publishes its own.
 *
 * `window._solverMonitor` and `window._smParseLogLine` are the handles the
 * parity harness drives: the same log lines can be pushed into both apps and
 * the resulting `residuals` compared number for number. Without them the only
 * way to compare the two charts is to look at them.
 *
 * A getter, not a snapshot: `openSolverMonitor` installs a FRESH data object per
 * run, and a copied reference would keep answering with the previous run's.
 */
export function installSolverMonitorBridge(): void {
  const w = window as unknown as Record<string, unknown>
  Object.defineProperty(w, '_solverMonitor', {
    configurable: true,
    get: () => ({ active: state.active, ...state.data }),
  })
  // Same names as the legacy's, so one harness snippet drives either app:
  // open the window, push log lines through it, read `_solverMonitor` back.
  w._smCreateMonitorWindow = (hasTemperature?: boolean) => {
    openSolverMonitor([])
    // The ARGUMENT decides, as it does in the legacy: a harness that opens both
    // windows with `false` must get the same two tabs in both, whatever the
    // monitoring panel happens to have ticked.
    state = {
      ...state,
      hasTemperature: !!hasTemperature,
      tab: !hasTemperature && state.tab === 'temperature' ? 'residual' : state.tab,
      revision: state.revision + 1,
    }
    emit()
  }
  w._smDestroyMonitorWindow = closeSolverMonitor
  // The legacy paints on demand; this one paints from state, so the equivalent
  // of "draw now" is telling the window its data moved.
  w._smDrawChart = () => {
    state = { ...state, revision: state.revision + 1 }
    emit()
  }
  w._smParseLogLine = (line: string) => {
    const outcome = parseSolverLogLine(state.data, line)
    if (outcome === 'redraw') {
      state = { ...state, revision: state.revision + 1 }
      emit()
    }
    return outcome
  }
}

/** Test seam: drop every series and close the window. */
export function resetSolverMonitor(): void {
  stopSensorPoller()
  sensorNames = []
  state = {
    open: false,
    active: false,
    collapsed: false,
    hasTemperature: false,
    tab: 'residual',
    hidden: {},
    fontScale: 1,
    geometry: defaultGeometry(),
    data: emptyMonitorData(),
    revision: 0,
  }
  emit()
}
