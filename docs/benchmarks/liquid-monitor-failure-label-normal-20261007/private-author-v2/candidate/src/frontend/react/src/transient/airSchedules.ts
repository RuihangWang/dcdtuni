/**
 * Air keyframes into a cfdrun v3 manifest (transient design §4, plan T4b).
 *
 * A transient air run (the solver is transient) of a v5 project whose air
 * tracks move gets, on each boundary binding a keyframe changes, a
 * `parameters.schedules {param: [[t, v], …]}` of corner rows. The manifest's
 * bindings are compiled just before and at every corner time of the union of
 * the moving air tracks (geometry is frozen off t = 0, so every compile has the
 * same bindings) and compared: exact at the corners, linear between them — a
 * wall-mounted jet whose flow and angle ramp together is the one approximation
 * (design §4). Steps are two rows at one time. A change to a parameter a
 * schedule cannot carry, or a moving track that changes nothing (server power
 * on a heat-flux server, which reads its face powers), is refused by name.
 */
import { reviveKinds } from '../legacy/cfdprojModules'
import type { CfdProject } from '../schema/cfdproj'
import { transientModel, type Track, type TransientBlock, domainModesOf } from './model'

/** Binding parameters a cfdrun v3 schedule may drive (cfdrun_schema.py). */
export const SCHEDULABLE = new Set(['temperatureC', 'flowRateM3s', 'directionWorld', 'supplyAngleDeg', 'powerW', 'heatFluxWm2'])

type Bag = Record<string, unknown>
type Binding = { patchName?: string; bcType?: string; parameters?: Bag }
type Bindings = Record<string, Binding>
export interface AirManifest { schemaVersion: number; boundaryBindings: Bindings; transient?: unknown; [key: string]: unknown }

const round9 = (x: number) => Math.round(x * 1e9) / 1e9
/** How far before a step its left-hand value is sampled: clear of the evaluator's EPS (1e-9 s). */
const BEFORE_STEP_S = 1e-6
const varies = (trk: Track) => trk.keyframes.some((k) => k.value !== trk.keyframes[0]!.value)
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function airTracks(block: TransientBlock): Track[] {
  const mode = new Map(transientModel().registry().params.map((p) => [p.key, p.mode]))
  return block.tracks.filter((t) => mode.get(t.param) === 'air' && varies(t))
}

/** The timeline of a transient air run (air mode transient, mirrored in solverType), keyframes or not. */
export function airTransientRun(project: CfdProject): TransientBlock | null {
  const doc = project as { schemaVersion?: unknown; transient?: TransientBlock; runDefaults?: { simulationConfig?: { solverType?: unknown } } }
  if ((Number(doc.schemaVersion ?? 0) | 0) < 5 || !doc.transient || domainModesOf(project).air !== 'transient') return null
  if (doc.runDefaults?.simulationConfig?.solverType !== 'transient') return null
  return doc.transient
}

/** The timeline an air export samples: a transient air run whose air tracks move. */
export function airTimeline(project: CfdProject): TransientBlock | null {
  const run = airTransientRun(project)
  return run && airTracks(run).length ? run : null
}

/**
 * Add to `manifest` (compiled at t = 0) the schedules on the bindings the
 * keyframes change and the transient block (the exporter sets the version).
 */
export async function addAirSchedules(
  manifest: AirManifest,
  source: CfdProject,
  timeline: TransientBlock,
  compile: (project: CfdProject) => Promise<AirManifest>,
): Promise<void> {
  const m = transientModel()
  const moving = airTracks(timeline)
  const corners = new Set<number>([0, timeline.durationS])
  const steps = new Set<number>()
  for (const trk of moving) {
    for (const k of trk.keyframes.slice(1)) {
      corners.add(k.t)
      corners.add(round9(k.t - (k.rampS ?? 0)))
      if (!((k.rampS ?? 0) > 0)) steps.add(k.t)
    }
  }
  const times = [...corners].filter((t) => t >= 0 && t <= timeline.durationS).sort((a, b) => a - b)
  const bindingsAt = async (t: number) => (await compile(m.materializeAt(reviveKinds(structuredClone(source)), t))).boundaryBindings
  // One sample per corner, two at a step: [time, bindings] in time order.
  const samples: [number, Bindings][] = []
  for (const t of times) {
    if (steps.has(t) && t > 0) samples.push([t, await bindingsAt(t - BEFORE_STEP_S)])
    samples.push([t, t === 0 ? manifest.boundaryBindings : await bindingsAt(t)])
  }

  const keys = Object.keys(manifest.boundaryBindings)
  const changed = new Set<number>()
  const schedules = new Map<string, Record<string, [number, unknown][]>>()
  for (const [t, bindings] of samples) {
    if (!same(Object.keys(bindings), keys)) {
      throw new Error(`Air keyframes at ${t} s change which boundaries exist; that is fixed off t = 0`)
    }
  }
  for (const key of keys) {
    const base = manifest.boundaryBindings[key]!
    const params = Object.keys(base.parameters ?? {})
    for (const name of params) {
      const series = samples.map(([t, b]) => [t, b[key]!.parameters?.[name]] as [number, unknown])
      if (series.every(([, v]) => same(v, series[0]![1]))) continue
      if (!SCHEDULABLE.has(name)) {
        const t = series.find(([, v]) => !same(v, series[0]![1]))![0]
        throw new Error(`Air keyframes at ${t} s change ${base.patchName ?? key}.${name}, which a cfdrun v3 run cannot schedule`)
      }
      series.forEach(([t, v], i) => { if (i && !same(v, series[i - 1]![1])) changed.add(t) })
      schedules.set(key, { ...schedules.get(key), [name]: series })
    }
  }
  for (const trk of moving) {
    const own = trk.keyframes.slice(1).flatMap((k) => [k.t, round9(k.t - (k.rampS ?? 0))])
    const ramps = trk.keyframes.slice(1).filter((k) => (k.rampS ?? 0) > 0)
    const touched = own.some((t) => changed.has(t))
      || ramps.some((k) => [...changed].some((t) => t > k.t - k.rampS! && t < k.t))
    if (!touched) {
      throw new Error(`Keyframes on ${trk.param} (${trk.target || trk.modelUid}) change nothing in the air run — `
        + 'this boundary does not take that input (a heat-flux server reads its face powers)')
    }
  }
  for (const [key, params] of schedules) {
    const parameters = manifest.boundaryBindings[key]!.parameters ?? {}
    parameters.schedules = Object.fromEntries(Object.entries(params).map(([name, rows]) => [name, rows.map(([t, v]) => [t, v])]))
    manifest.boundaryBindings[key]!.parameters = parameters
  }
  manifest.transient = JSON.parse(JSON.stringify({ durationS: timeline.durationS, start: timeline.start }))
}
