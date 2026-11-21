/**
 * Keyframes on the way into the run formats (plans T2.5 and T3).
 *
 * Liquid: a time-marching run of a transient v5 project samples every track at
 * t_i = i·dt — right-continuous (design §3), so a step at t_k takes effect at
 * the first sample with t_i ≥ t_k, as the old load step and supply schedule
 * did. The graph is compiled once per run of identical samples and diffed, so
 * every input comes out exactly as the steady compiler reads it at that time.
 * The changes are returned as events: a v2/v3 manifest carries them as such
 * (cold-plate heat and valve openings only), a v4 manifest lowers them into
 * list[N_t] series (every edge input and the return-pressure node). A DAE's
 * primary-circuit inlet temperature and flow become N_t series. An input a
 * form cannot carry, or a track whose element is not in its mode (a supply
 * set point on a physics HX…), is refused by name, never held silently.
 *
 * Air keyframes: see airSchedules.ts (cfdrun v3).
 */
import { reviveKinds } from '../legacy/cfdprojModules'
import * as store from '../projectStore'
import type { CfdProject } from '../schema/cfdproj'
import type { DynamicLiquidGraph, LiquidEvent } from '../liquid/dynamicLiquidGraph'
import { transientModel, type Track, type TransientBlock, domainModesOf } from './model'

export type LiquidForm = 'v2' | 'v3' | 'v4'
/** Edge fields each form can schedule (v2/v3: the events enum minus the PID actuator). */
const EDGE_FIELDS: Record<LiquidForm, readonly string[]> = {
  v2: ['Q_W', 'opening'],
  v3: ['Q_W', 'opening'],
  v4: ['Q_W', 'opening', 'rpm_ratio', 'flow_set_kg_s', 'dT_set_K', 'T_set_K'],
}
/** Liquid registry keys that land on graph edges or nodes, per form. */
const ON_GRAPH: Record<LiquidForm, Set<string>> = {
  v2: new Set(['cp.heat', 'valve.opening']),
  v3: new Set(['cp.heat', 'valve.opening']),
  v4: new Set(['cp.heat', 'cp.dT', 'pump.rpm', 'pump.flow', 'valve.opening', 'cdu.secondarySupplyT', 'cdu.returnPressure']),
}
const LABEL: Record<LiquidForm, string> = { v2: '.liquidrun v2', v3: '.liquidrun v3 DAE', v4: '.liquidrun v4 DAE' }
/** Liquid registry keys that land on a DAE primary circuit, `<cdu uid>_hx`. */
const ON_CIRCUITS: Record<string, 'inlet_K' | 'flow_kg_s'> = {
  'cdu.primaryInletT': 'inlet_K',
  'cdu.primaryFlow': 'flow_kg_s',
}
type CircuitSeries = Partial<Record<'inlet_K' | 'flow_kg_s', number[]>>
export interface LiquidSchedule { events: LiquidEvent[]; circuits: Map<string, CircuitSeries> }

type Bag = Record<string, unknown>
const bag = (v: unknown): Bag => (v && typeof v === 'object' ? (v as Bag) : {})
const round9 = (x: number) => Math.round(x * 1e9) / 1e9
const varies = (trk: Track) => trk.keyframes.some((k) => k.value !== trk.keyframes[0]!.value)
const modeOf = () => new Map(transientModel().registry().params.map((p) => [p.key, p.mode]))
const names = (tracks: Track[]) => [...new Set(tracks.map((t) => t.param))].join(', ')

/** The transient block a liquid run follows, or null when the liquid runs steady (E8b: its own mode). */
function transientBlock(project: CfdProject): TransientBlock | null {
  const doc = project as { schemaVersion?: unknown; transient?: TransientBlock }
  if ((Number(doc.schemaVersion ?? 0) | 0) < 5 || !doc.transient) return null
  return domainModesOf(project).liquid === 'transient' ? doc.transient : null
}

/** The timeline a liquid run follows: a transient project's, when the run marches in time. */
export function liquidTimeline(source: CfdProject, timeMarching: boolean): TransientBlock | null {
  return timeMarching ? transientBlock(source) : null
}

/** Why a moving v4 track would change nothing: its element is not in the matching mode. */
function modeMismatch(start: CfdProject, trk: Track): string | null {
  const model = bag(store.findConstruction(start, trk.modelUid)?.model)
  const cooling = bag(model.cooling)
  if (trk.param === 'cp.heat' || trk.param === 'cp.dT') {
    const [server, pair] = trk.target.split('/')
    const pairs = bag(bag(bag(model.constructions).servers)[server ?? '']).liquidCooling
    const plate = bag((bag(pairs).cpCuPairs as unknown[] | undefined)?.[Number(pair)])
    const rise = plate.heatMode === 'temperature_rise'
    if (trk.param === 'cp.heat' && rise) return 'its cold plate is in temperature-rise mode — key the temperature rise instead'
    if (trk.param === 'cp.dT' && !rise) return 'set the cold plate\'s heat mode to temperature rise first'
  }
  if (trk.param === 'pump.rpm' && cooling.pumpControl === 'flow') return 'the CDU pump holds a flow — key the total flow instead'
  if (trk.param === 'pump.flow' && cooling.pumpControl !== 'flow') return 'set the CDU pump to fixed flow first'
  if (trk.param === 'cdu.secondarySupplyT' && cooling.secondaryControl !== 'setpoint') {
    return 'set the CDU secondary supply to a held temperature first'
  }
  return null
}

export function scheduleLiquidInputs(
  source: CfdProject,
  block: TransientBlock,
  base: DynamicLiquidGraph,
  graphAt: (project: CfdProject) => DynamicLiquidGraph,
  context: { form: LiquidForm; circuits: string[]; loadStep: boolean; supplySchedule: boolean },
): LiquidSchedule | null {
  const m = transientModel()
  const mode = modeOf()
  const moving = block.tracks.filter(varies)
  const liquid = moving.filter((t) => mode.get(t.param) === 'liquid')
  if (!liquid.length) return null
  const { form } = context
  const dae = form !== 'v2'

  const homeless = liquid.filter((t) => !ON_GRAPH[form].has(t.param)
    && !(dae && ON_CIRCUITS[t.param] && context.circuits.includes(`${t.modelUid}_hx`)))
  if (homeless.length) {
    throw new Error(`Keyframes on ${names(homeless)} cannot be compiled into a ${LABEL[form]} run; `
      + 'keep them at their start value')
  }
  if (form === 'v4') {
    const start = m.materializeAt(reviveKinds(structuredClone(source)), 0)
    for (const trk of liquid) {
      const why = modeMismatch(start, trk)
      if (why) throw new Error(`Keyframes on ${trk.param} would change nothing: ${why}`)
    }
  }
  if (context.loadStep && liquid.some((t) => t.param === 'cp.heat')) {
    throw new Error('The load step and cold-plate heat keyframes both schedule the heat load; '
      + 'turn the load step off (the keyframes replace it)')
  }
  if (context.supplySchedule && liquid.some((t) => t.param === 'cdu.primaryInletT')) {
    throw new Error('The supply schedule and primary-inlet temperature keyframes both schedule the supply; '
      + 'set the schedule to fixed (the keyframes replace it)')
  }
  const { dt_s: dt, N_t: count } = base.time
  for (const trk of liquid) {
    for (const k of trk.keyframes.slice(1)) {
      if (!m.onGrid(k.t, dt) || !m.onGrid(round9(k.t - (k.rampS ?? 0)), dt)) {
        throw new Error(`The ${trk.param} keyframe at ${k.t} s is off the liquid dt grid (dt = ${dt} s)`)
      }
    }
  }

  // Samples, and the starts of runs of identical ones. Every moving track counts —
  // an air value can still decide whether the liquid graph compiles at all.
  const times = Array.from({ length: count }, (_, i) => round9(i * dt))
  const sample = (t: number) => moving.map((trk) => m.valueAt(trk.keyframes, t))
  const starts: number[] = []
  let last = sample(0)
  for (let i = 1; i < count; i++) {
    const now = sample(times[i]!)
    if (now.some((v, j) => v !== last[j])) { starts.push(i); last = now }
  }

  // The graph minus what the schedule carries: the form's edge fields, a v4
  // node pressure, and the racks' descriptive copy of each plate's Q_W (the
  // old load step left it at the start value too).
  const fields = EDGE_FIELDS[form]
  const list = (v: unknown) => (Array.isArray(v) ? (v as Bag[]) : [])
  const masked = (g: DynamicLiquidGraph) => JSON.stringify({
    ...g,
    edges: g.edges.map((e) => {
      const params = { ...e.params }
      for (const f of fields) delete params[f]
      return { ...e, params }
    }),
    nodes: form === 'v4' ? g.nodes.map((n) => (n.bc ? { ...n, bc: { ...n.bc, P_Pa: 0 } } : n)) : g.nodes,
    racks: list(g.racks).map((r) => ({
      ...r,
      servers: list(r.servers).map((srv) => ({
        ...srv,
        cp_cu_pairs: list(srv.cp_cu_pairs).map((cp) => { const { Q_W: _q, ...rest } = cp; return rest }),
      })),
    })),
  })
  const shape = masked(base)
  const events: LiquidEvent[] = []
  let prev = base
  for (const i of starts) {
    const t = times[i]!
    const graph = graphAt(m.materializeAt(reviveKinds(structuredClone(source)), t))
    if (masked(graph) !== shape) {
      throw new Error(`Keyframes at ${t} s change liquid inputs a ${LABEL[form]} run cannot schedule `
        + `(it takes ${fields.join(', ')}${dae ? ', and primary-circuit inlet and flow' : ''})`)
    }
    graph.edges.forEach((edge, n) => {
      for (const f of fields) {
        const value = edge.params[f]
        if (typeof value === 'number' && value !== prev.edges[n]!.params[f]) {
          events.push({ time_s: t, target: edge.id, field: f, value })
        }
      }
    })
    if (form === 'v4') {
      graph.nodes.forEach((node, n) => {
        const value = node.bc?.P_Pa
        if (typeof value === 'number' && value !== prev.nodes[n]!.bc?.P_Pa) {
          events.push({ time_s: t, target: node.id, field: 'P_Pa', value })
        }
      })
    }
    prev = graph
  }

  const circuits = new Map<string, CircuitSeries>()
  for (const trk of liquid) {
    const key = ON_CIRCUITS[trk.param]
    if (!key) continue
    const id = `${trk.modelUid}_hx`
    const values = times.map((t) => m.valueAt(trk.keyframes, t))
    circuits.set(id, { ...circuits.get(id), [key]: key === 'inlet_K' ? values.map((c) => round9(c + 273.15)) : values })
  }
  return { events, circuits }
}

/**
 * Lower schedule events into list[N_t] series on their edge or node (the v4
 * form has no events): an event at t sets its field from sample round(t/dt) on.
 */
export function lowerEventsToSeries(graph: DynamicLiquidGraph): void {
  const { N_t: count, dt_s: dt } = graph.time
  const ordered = [...graph.events].sort((a, b) => a.time_s - b.time_s)
  for (const event of ordered) {
    const edge = graph.edges.find((e) => e.id === event.target)
    const holder = edge ? (edge.params as Bag) : (graph.nodes.find((n) => n.id === event.target)?.bc as Bag | undefined)
    if (!holder) throw new Error(`Scheduled input targets ${event.target}, which is not in the graph`)
    const current = holder[event.field]
    if (typeof current !== 'number' && !Array.isArray(current)) {
      throw new Error(`${event.target}.${event.field} has no value on the graph to schedule from`)
    }
    const series = Array.isArray(current) ? [...(current as number[])] : Array<number>(count).fill(current)
    series.fill(event.value, Math.round(event.time_s / dt))
    holder[event.field] = series
  }
  graph.events = []
}
