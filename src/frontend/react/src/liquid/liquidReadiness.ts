/**
 * Is the liquid model ready to solve? One checklist for the modelling stage
 * and the solver panel.
 *
 * Every rule here is one the compilers already enforce; this module only runs
 * them early and says which object is at fault, instead of the first failing
 * "Prepare" answering with a generic sentence or the shared graph compiler's
 * `alert()`:
 *   - racks are liquid when `rack.cooling.type === 'liquid'` (liquid_graphgen.js);
 *   - a server enters the solve through `liquidCooling.cpCuPairs`, nothing else;
 *   - the topology must compile (`compileDynamicLiquidGraph`, which also runs
 *     `validateLiquidIsolationConnections` and the hybrid budget validation);
 *   - every cold plate must lie on a closed CDU loop: a plate on a dead-end
 *     branch shares no biconnected block with a pump, so no loop flow passes it;
 *   - edge elevations must close around every loop, the backend's
 *     `_check_elevation_closure` (graphcompiling/compiler.py) run in advance.
 *
 * Pure: reads the project, never edits it.
 */
import { allModels, type CfdProject, type Construction } from '../schema/cfdproj'
import type { TranslationKey } from '../i18n'
import { projectHybridHeatBudgetErrors } from '../legacy/cfdprojModules'
import { projectAtStart } from '../transient/model'
import { compileDynamicLiquidGraph, type DynamicLiquidGraph, type LiquidEdge } from './dynamicLiquidGraph'
import { hasHybridFanServer } from './hybridFanHeatBudget'

type Bag = Record<string, any>

export type ReadinessStatus = 'ok' | 'warn' | 'error' | 'skip'
export type ReadinessId = 'cdu' | 'liquidRacks' | 'coldPlates' | 'compile' | 'loop' | 'elevation' | 'hybridBudget'

export interface ReadinessItem {
  id: ReadinessId
  status: ReadinessStatus
  key: TranslationKey
  vars?: Record<string, string | number>
  /** Object names or compiler sentences, shown under the line. */
  details: string[]
}

export interface LiquidReadiness {
  items: ReadinessItem[]
  /** True when no item is an error. */
  ready: boolean
  graph: DynamicLiquidGraph | null
}

/**
 * Run `fn` with `window.alert` collecting instead of blocking.
 *
 * The shared graph compiler reports a failed topology validation with
 * `alert()` and returns null (liquid_graphgen.js). Captured, those sentences
 * become the checklist's details; nothing pops up while the user is typing.
 */
export function captureCompilerAlerts<T>(fn: () => T): { value: T | undefined; alerts: string[]; error: unknown } {
  const alerts: string[] = []
  const w = typeof window === 'undefined' ? null : (window as unknown as { alert: (m?: unknown) => void })
  const original = w?.alert
  if (w) w.alert = (message?: unknown) => { alerts.push(String(message ?? '')) }
  try {
    return { value: fn(), alerts, error: null }
  } catch (error) {
    return { value: undefined, alerts, error }
  } finally {
    if (w && original) w.alert = original
  }
}

function nameOf(model: { meta?: { name?: string }; uid?: string } | null | undefined): string {
  return model?.meta?.name || model?.uid || '?'
}

function enabled(model: Construction): boolean {
  return model.meta?.enabled !== false
}

interface ServerView { rack: Construction; uid: string; server: Bag }

function liquidServers(rack: Construction): ServerView[] {
  const servers = ((rack as Bag).constructions?.servers ?? {}) as Record<string, Bag>
  return Object.entries(servers)
    .filter(([, s]) => s && s.type === 'liquidServer')
    .map(([key, server]) => ({ rack, uid: String(server.uid || key), server }))
}

function pairCount(server: Bag): number {
  const pairs = server.liquidCooling?.cpCuPairs
  return Array.isArray(pairs) ? pairs.length : 0
}

/**
 * Biconnected block of every edge (Tarjan, iterative, edge stack).
 *
 * Two edges share a block exactly when one simple cycle contains both, so a
 * cold plate carries loop flow only if it shares a block with a pump. A plate
 * on a dead-end branch, or on a little loop hanging off one, does not — the
 * same reasoning the backend's stagnation analysis uses. Edges whose ends are
 * not graph nodes get -1.
 */
export function edgeBlocks(nodeIds: string[], edges: { from: string; to: string }[]): number[] {
  const index = new Map(nodeIds.map((id, i) => [id, i]))
  const adjacency: [number, number][][] = nodeIds.map(() => [])
  edges.forEach((edge, e) => {
    const a = index.get(edge.from), b = index.get(edge.to)
    if (a === undefined || b === undefined) return
    adjacency[a]!.push([b, e]); if (a !== b) adjacency[b]!.push([a, e])
  })
  const disc = new Array<number>(nodeIds.length).fill(-1)
  const low = new Array<number>(nodeIds.length).fill(0)
  const block = new Array<number>(edges.length).fill(-1)
  const edgeStack: number[] = []
  let time = 0, blocks = 0
  for (let root = 0; root < nodeIds.length; root++) {
    if (disc[root] !== -1) continue
    disc[root] = low[root] = time++
    // [node, edge into it, next neighbour index]
    const frames: [number, number, number][] = [[root, -1, 0]]
    while (frames.length) {
      const top = frames[frames.length - 1]!
      const u = top[0]
      if (top[2] < adjacency[u]!.length) {
        const [v, e] = adjacency[u]![top[2]++]!
        if (e === top[1]) continue
        if (disc[v] === -1) {
          edgeStack.push(e); disc[v] = low[v] = time++; frames.push([v, e, 0])
        } else if (disc[v]! <= disc[u]!) {
          // A back edge (or a self-loop) closes a cycle with the tree path.
          edgeStack.push(e); low[u] = Math.min(low[u]!, disc[v]!)
        }
      } else {
        frames.pop()
        if (!frames.length) continue
        const p = frames[frames.length - 1]![0]
        low[p] = Math.min(low[p]!, low[u]!)
        if (low[u]! >= disc[p]!) {
          const id = blocks++
          for (;;) {
            const e = edgeStack.pop()
            if (e === undefined) break
            block[e] = id
            if (e === top[1]) break
          }
        }
      }
    }
  }
  return block
}

/**
 * The backend's elevation-closure rule on a compiled graph.
 *
 * A pipe's rise is its route's (last waypoint minus first, gravity −y, as
 * `compiler.py` integrates it); every other edge rises with its node heights.
 * Node potentials from a spanning forest must explain every remaining edge.
 * Returns the pipes whose route ends disagree with their nodes on the worst
 * failing loop, or null when every loop closes.
 */
export function elevationMismatch(graph: { nodes: { id: string; position_m: number[] }[]; edges: LiquidEdge[] }):
  { mismatchM: number; pipes: string[] } | null {
  const index = new Map(graph.nodes.map((n, i) => [n.id, i]))
  const height = graph.nodes.map(n => Number(n.position_m?.[1] ?? 0))
  const rows = graph.edges.map(edge => {
    const a = index.get(edge.from), b = index.get(edge.to)
    if (a === undefined || b === undefined) return null
    const route = edge.type === 'pipe' && Array.isArray(edge.params?.waypoints_m) && edge.params.waypoints_m.length >= 2
      ? edge.params.waypoints_m as number[][] : null
    const rise = route ? Number(route[route.length - 1]![1]) - Number(route[0]![1]) : height[b]! - height[a]!
    return { a, b, rise, id: edge.id, pipe: !!route }
  })
  const adjacency: [number, number, number][][] = height.map(() => [])
  rows.forEach((row, e) => {
    if (!row) return
    adjacency[row.a]!.push([row.b, e, row.rise]); adjacency[row.b]!.push([row.a, e, -row.rise])
  })
  const potential: (number | null)[] = height.map(() => null)
  const tree = new Set<number>()
  for (let root = 0; root < height.length; root++) {
    if (potential[root] !== null) continue
    potential[root] = 0
    const stack = [root]
    while (stack.length) {
      const node = stack.pop()!
      for (const [other, edge, rise] of adjacency[node]!) {
        if (potential[other] !== null) continue
        potential[other] = potential[node]! + rise
        tree.add(edge); stack.push(other)
      }
    }
  }
  const scale = Math.max(1, ...potential.map(v => Math.abs(v ?? 0)))
  const tolerance = 1e-6 + 1e-9 * scale
  let worst: number | null = null
  rows.forEach((row, e) => {
    if (!row || tree.has(e)) return
    const mismatch = potential[row.b]! - potential[row.a]! - row.rise
    if (Math.abs(mismatch) > tolerance && (worst === null || Math.abs(mismatch) > Math.abs(worst))) worst = mismatch
  })
  if (worst === null) return null
  const pipes = rows.filter((row): row is NonNullable<typeof row> =>
    !!row && row.pipe && Math.abs(row.rise - (height[row.b]! - height[row.a]!)) > tolerance).map(row => row.id)
  return { mismatchM: Math.abs(worst), pipes }
}

const ITEM_KEYS: Record<ReadinessId, Record<Exclude<ReadinessStatus, 'skip'> | 'skip', TranslationKey>> = {
  cdu: { ok: 'lq_ux.ready_cdu_ok', warn: 'lq_ux.ready_cdu_ok', error: 'lq_ux.ready_cdu_missing', skip: 'lq_ux.ready_skipped' },
  liquidRacks: { ok: 'lq_ux.ready_racks_ok', warn: 'lq_ux.ready_racks_ok', error: 'lq_ux.ready_racks_missing', skip: 'lq_ux.ready_skipped' },
  coldPlates: { ok: 'lq_ux.ready_plates_ok', warn: 'lq_ux.ready_plates_partial', error: 'lq_ux.ready_plates_missing', skip: 'lq_ux.ready_skipped' },
  compile: { ok: 'lq_ux.ready_compile_ok', warn: 'lq_ux.ready_compile_ok', error: 'lq_ux.ready_compile_failed', skip: 'lq_ux.ready_skipped' },
  loop: { ok: 'lq_ux.ready_loop_ok', warn: 'lq_ux.ready_loop_ok', error: 'lq_ux.ready_loop_open', skip: 'lq_ux.ready_loop_skipped' },
  elevation: { ok: 'lq_ux.ready_elevation_ok', warn: 'lq_ux.ready_elevation_ok', error: 'lq_ux.ready_elevation_open', skip: 'lq_ux.ready_elevation_skipped' },
  hybridBudget: { ok: 'lq_ux.ready_hybrid_ok', warn: 'lq_ux.ready_hybrid_missing', error: 'lq_ux.ready_hybrid_invalid', skip: 'lq_ux.ready_skipped' },
}

function item(id: ReadinessId, status: ReadinessStatus, details: string[] = [], vars?: Record<string, string | number>): ReadinessItem {
  return { id, status, key: ITEM_KEYS[id][status], details, ...(vars ? { vars } : {}) }
}

/** The checklist for `project`, in modelling order. */
export function liquidReadiness(project: CfdProject | null): LiquidReadiness {
  if (!project) return { items: [], ready: false, graph: null }
  let start: CfdProject
  try {
    start = projectAtStart(project)
  } catch (error) {
    // A timeline that no longer matches its objects: nothing below can be
    // read at t = 0, and saying so beats a panel that throws.
    const message = error instanceof Error ? error.message : String(error)
    return { items: [item('compile', 'error', [message])], ready: false, graph: null }
  }
  const models = allModels(start).map(m => m.model)
  const items: ReadinessItem[] = []

  const cdus = models.filter(m => m.kind === 'cdu' && enabled(m))
  items.push(item('cdu', cdus.length ? 'ok' : 'error', cdus.map(nameOf), { count: cdus.length }))

  const racks = models.filter(m => m.kind === 'rack' && enabled(m) && (m as Bag).cooling?.type === 'liquid')
  items.push(item('liquidRacks', racks.length ? 'ok' : 'error', racks.map(nameOf), { count: racks.length }))

  const servers = racks.flatMap(liquidServers)
  const racksWithoutPlates = racks.filter(rack => !liquidServers(rack).some(s => pairCount(s.server) > 0))
  const serversWithoutPlates = servers.filter(s => pairCount(s.server) === 0)
  const pairs = servers.reduce((sum, s) => sum + pairCount(s.server), 0)
  items.push(item('coldPlates',
    !racks.length || racksWithoutPlates.length === racks.length ? 'error' : racksWithoutPlates.length || serversWithoutPlates.length ? 'warn' : 'ok',
    [...racksWithoutPlates.map(r => `${nameOf(r)}: 0`), ...serversWithoutPlates.map(s => `${nameOf(s.rack)} / ${s.server.meta?.name || s.uid}: 0`)],
    { pairs, servers: servers.length }))

  let graph: DynamicLiquidGraph | null = null
  if (cdus.length && racks.length) {
    const compiled = captureCompilerAlerts(() => compileDynamicLiquidGraph(start, { duration_s: 1, dt_s: 1 }))
    if (compiled.value) {
      graph = compiled.value
      items.push(item('compile', 'ok', [], { nodes: graph.nodes.length, edges: graph.edges.length }))
    } else {
      const message = compiled.error instanceof Error ? compiled.error.message : String(compiled.error ?? '')
      const lines = compiled.alerts.flatMap(a => a.split('\n')).map(s => s.trim())
        .filter(s => s && !/^Liquid graph validation failed:?$/.test(s))
      items.push(item('compile', 'error', [...lines, ...(message ? [message] : [])]))
    }
  } else {
    items.push(item('compile', 'skip'))
  }

  if (graph) {
    const nodeIds = graph.nodes.map(n => n.id)
    const plates = graph.edges.map((e, i) => [e, i] as const).filter(([e]) => e.type === 'cold_plate')
    const blocks = edgeBlocks(nodeIds, graph.edges)
    const pumped = new Set(graph.edges.map((e, i) => e.type === 'pump' ? blocks[i] : -1).filter(b => b !== -1))
    // Which bound object owns an edge, for a name the user recognises.
    const owner = new Map<string, string>()
    for (const binding of Object.values(graph.object_bindings ?? {})) {
      if (binding.kind !== 'server') continue
      for (const id of binding.edge_ids) owner.set(id, binding.label)
    }
    const open = plates.filter(([, i]) => !pumped.has(blocks[i]!)).map(([e]) => owner.get(e.id) ?? e.id)
    const unique = [...new Set(open)]
    items.push(item('loop', !plates.length || unique.length ? 'error' : 'ok', unique, { plates: plates.length }))
    const elevation = elevationMismatch(graph)
    items.push(elevation
      ? item('elevation', 'error', elevation.pipes, { mismatch: elevation.mismatchM.toPrecision(3) })
      : item('elevation', 'ok'))
  } else {
    items.push(item('loop', 'skip'), item('elevation', 'skip'))
  }

  if (hasHybridFanServer(project)) {
    const budget = (project as Bag).runDefaults?.hybridHeatBudget
    const errors = projectHybridHeatBudgetErrors(project)
    items.push(budget?.version !== 2 ? item('hybridBudget', 'warn')
      : errors.length ? item('hybridBudget', 'error', errors.map(e => `${e.path}: ${e.msg}`))
        : item('hybridBudget', 'ok', [], { servers: Object.keys(budget.servers ?? {}).length }))
  }

  return { items, ready: items.every(i => i.status !== 'error'), graph }
}
