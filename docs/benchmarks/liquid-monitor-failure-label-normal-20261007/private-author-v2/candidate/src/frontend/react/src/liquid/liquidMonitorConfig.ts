/**
 * What a liquid solve is asked to REPORT while it runs.
 *
 * `/api/liquid/solve` takes `monitor_cps`, a list of cold-plate edge ids.
 * `backend/liquidcooling/runner.py:26` returns an empty monitor dict when that
 * list is empty, so every `epoch` frame then arrives without `cp_monitor` and
 * the chip-temperature chart stays blank for the whole run — no error, just an
 * empty plot. React was sending `[]` on every solve.
 *
 * The ids are the .liquidrun EDGE ids: the runner looks each one up in
 * `graph.edge_str_ids`, and an id that is not one of them is silently skipped.
 * That is why `cpCatalogFromProject` reproduces `js/liquid_graphgen.js`'s
 * naming (`<rack>_<server>_cp<i>`) rather than inventing a readable id — a
 * prettier id would monitor nothing.
 *
 * Held in a module, not in the panel, for the reason `monitoring.ts` gives for
 * the air side: the legacy reads its checkboxes out of the DOM at solve time
 * (`lsmGetMonitoringConfig`, js/liquid_solve_monitor.js:107), which answers
 * "nothing selected" whenever the panel is not mounted.
 */

import { modelsIn, type CfdProject } from '../schema/cfdproj'
import { normalizePair, readRawPairs } from './liquidColdPlates'

export interface CpCuMonitorEntry {
  /** The .liquidrun edge id — what `monitor_cps` must contain. */
  id: string
  /** `<rack>/<server>/<pair>`, as the legacy row shows it. */
  label: string
  qW: number
}

export const CDU_METRICS = ['return_P', 'supply_P', 'total_flow'] as const
export type CduMetric = (typeof CDU_METRICS)[number]

export interface LiquidMonitorSelection {
  loss: boolean
  /**
   * Cold plates the user has UNCHECKED, not the ones they checked.
   *
   * Stored as the negative so a plate added after the panel was last opened is
   * monitored by default — which is the legacy behaviour: `lsmLoadCPList`
   * rebuilds the list `checked` on every run.
   */
  deselectedCps: string[]
  cduMetrics: CduMetric[]
}

/** The shape `lsmGetMonitoringConfig` returns (js/liquid_solve_monitor.js:107). */
export interface LiquidMonitoringConfig {
  loss: boolean
  cpList: { id: string; label: string }[]
  cduMetrics: CduMetric[]
}

export const DEFAULT_LIQUID_MONITOR_SELECTION: LiquidMonitorSelection = {
  loss: true,
  deselectedCps: [],
  cduMetrics: [...CDU_METRICS],
}

// ---------------------------------------------------------------------------
// Catalogues
// ---------------------------------------------------------------------------

function cpLabelId(pairName: string | undefined, index: number): string {
  // `cp.name || ('cp' + i)` — liquid_graphgen.js:531.
  return pairName ? pairName : `cp${index}`
}

/**
 * Every cold plate the compiler will emit, read from the project.
 *
 * The two filters are the compiler's: a rack must be liquid-cooled AND enabled
 * (liquid_graphgen.js:373), and a server is included only when it already has
 * pairs (:421). A rack the user disabled contributes no edges, so listing its
 * plates here would offer monitoring for something the solve cannot report.
 */
export function cpCatalogFromProject(project: CfdProject | null): CpCuMonitorEntry[] {
  if (!project) return []
  const out: CpCuMonitorEntry[] = []
  for (const rack of modelsIn(project, 'racks')) {
    const cooling = rack.cooling as { type?: string } | undefined
    if (cooling?.type !== 'liquid') continue
    if (!rack.meta?.enabled) continue
    const rackName = rack.meta?.name ?? ''
    const servers = (rack.constructions as { servers?: Record<string, Record<string, unknown>> })
      ?.servers
    if (!servers) continue
    for (const server of Object.values(servers)) {
      const pairs = readRawPairs(server)
      if (pairs.length === 0) continue
      const meta = server.meta as { name?: string } | undefined
      const serverName = meta?.name ?? ''
      pairs.forEach((raw, i) => {
        const pair = normalizePair(raw, i)
        const name = typeof raw.name === 'string' && raw.name ? raw.name : undefined
        out.push({
          id: `${rackName}_${serverName}_cp${i}`,
          label: `${rackName}/${serverName}/${cpLabelId(name, i)}`,
          qW: pair.Q_W,
        })
      })
    }
  }
  return out
}

interface GraphCp {
  id?: unknown
  edge_id?: unknown
  Q_W?: unknown
}
interface GraphServer {
  id?: unknown
  cp_cu_pairs?: GraphCp[]
}
interface GraphRack {
  id?: unknown
  servers?: GraphServer[]
}

/**
 * The same catalogue read off a COMPILED graph, the way `lsmLoadCPList` does.
 *
 * Used for the solve request itself: these ids came out of the compiler that
 * wrote the .liquidrun the solver is about to read, so they cannot disagree
 * with it. The project scan above is for showing the list BEFORE a graph
 * exists; a test asserts the two produce the same ids.
 */
export function cpCatalogFromGraph(graph: unknown): CpCuMonitorEntry[] {
  const racks = (graph as { racks?: GraphRack[] } | null)?.racks
  if (!Array.isArray(racks)) return []
  const out: CpCuMonitorEntry[] = []
  for (const rack of racks) {
    for (const server of rack.servers ?? []) {
      for (const cp of server.cp_cu_pairs ?? []) {
        if (typeof cp.edge_id !== 'string') continue
        out.push({
          id: cp.edge_id,
          label: `${String(rack.id ?? '')}/${String(server.id ?? '')}/${String(cp.id ?? '')}`,
          qW: typeof cp.Q_W === 'number' ? cp.Q_W : 0,
        })
      }
    }
  }
  return out
}

// ---------------------------------------------------------------------------
// The config a solve is started with
// ---------------------------------------------------------------------------

export function isCpMonitored(selection: LiquidMonitorSelection, id: string): boolean {
  return !selection.deselectedCps.includes(id)
}

export function buildMonitoringConfig(
  catalog: CpCuMonitorEntry[],
  selection: LiquidMonitorSelection,
): LiquidMonitoringConfig {
  return {
    loss: selection.loss,
    cpList: catalog
      .filter((cp) => isCpMonitored(selection, cp.id))
      .map((cp) => ({ id: cp.id, label: cp.label })),
    // Ordered by CDU_METRICS, not by the order the user clicked: the request is
    // compared byte-for-byte against the legacy's in the parity sweep.
    cduMetrics: CDU_METRICS.filter((m) => selection.cduMetrics.includes(m)),
  }
}

/** Exactly what goes into `monitor_cps` (js/liquid_cooling.js:192). */
export function monitorCpIds(
  catalog: CpCuMonitorEntry[],
  selection: LiquidMonitorSelection,
): string[] {
  return buildMonitoringConfig(catalog, selection).cpList.map((cp) => cp.id)
}

// ---------------------------------------------------------------------------
// Module state — retained by the solver panel and workspace reset
// ---------------------------------------------------------------------------

let selection: LiquidMonitorSelection = { ...DEFAULT_LIQUID_MONITOR_SELECTION }

type Listener = () => void
const listeners = new Set<Listener>()

export function subscribeLiquidMonitor(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getLiquidMonitorSelection(): LiquidMonitorSelection {
  return selection
}

export function setLiquidMonitorSelection(next: LiquidMonitorSelection) {
  selection = next
  listeners.forEach((fn) => fn())
}

/** Reset to "monitor everything", as a fresh project would start. */
export function resetLiquidMonitorSelection() {
  setLiquidMonitorSelection({ ...DEFAULT_LIQUID_MONITOR_SELECTION })
}

