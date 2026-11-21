// Keyframed liquid inputs reach the v2/v3 compilers (plan T2.5). A migrated
// load step compiles to the step the v4 settings produced, sampled
// right-continuously on the run's dt grid; an input no v2/v3 manifest can
// schedule is refused, never dropped. Air keyframes are refused until T4.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'
import { compileSavedExperiment, readExperiment } from '../src/liquid/liquidExperiment'
import { transientOf, type Track } from '../src/transient/model'
import { setSimulationMode } from '../src/transient/simulationMode'
import { addKeyframe, applyTimelineOp, trackKeyOf } from '../src/transient/trackEdits'
import type { CfdProject } from '../src/schema/cfdproj'

const ROOT = resolve(process.cwd(), '../../..')
const NV = 'src/frontend/react/test/fixtures/cases/NV_PEK02_mock.cfdproj'
const DAE = 'case/verification/shinaide-legacy-projects/Shinaide_dae_cpu.json'
const HYBRID = 'case/verification/shinaide-legacy-projects/Shinaide_hybrid_cpu.json'
const v4 = (rel: string) => JSON.parse(readFileSync(resolve(ROOT, rel), 'utf8'))
function open(rel: string): CfdProject {
  const r = readProjectFileText(readFileSync(resolve(ROOT, rel), 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  return store.getProject()!
}
type Edge = { id: string; type: string; params: Record<string, unknown> }
type Ev = { time_s: number; target: string; field: string; value: number }
type NodeT = { id: string; bc?: { P_Pa: number | number[] } }
const compiled = () => compileSavedExperiment(store.getProject()!).liquidrun as unknown as {
  schemaVersion: number; edges: Edge[]; nodes: NodeT[]; events: Ev[]; time: { dt_s: number; N_t: number }
  primary_circuits?: { edge_id: string; inlet_K: number | number[] }[]
  transient?: { durationS: number; start: { kind: string }; tracks: unknown[] }
}
/** Sample i's value of a scalar-or-series input. */
const at = (value: unknown, i: number) => (Array.isArray(value) ? value[i] : value) as number
const tracks = (param: string): Track[] => transientOf(store.getProject()).tracks.filter(t => t.param === param)
const editTransient = (fn: (tr: { tracks: Track[] }) => void) =>
  store.edit('test', '', d => { fn((d as unknown as { transient: { tracks: Track[] } }).transient) })

describe('a migrated load step is compiled from its keyframes', () => {
  for (const rel of [NV, DAE, HYBRID]) {
    it(`${rel.split('/').pop()}: every heated cold plate steps at stepTime by loadFactor`, () => {
      const { stepTime, loadFactor } = v4(rel).runDefaults.liquid.experiment.settings
      open(rel)
      expect(transientOf(store.getProject()).mode).toBe('transient')
      expect(readExperiment(store.getProject()).settings.loadStep).toBe(false)
      const run = compiled()
      const plates = run.edges.filter(e => e.type === 'cold_plate' && at(e.params.Q_W, 0) > 0)
      expect(plates.length).toBeGreaterThan(0)
      if (rel === HYBRID) {
        // A v2 graph run carries the step as events.
        const qEvents = run.events.filter(e => e.field === 'Q_W')
        expect(qEvents).toHaveLength(plates.length)
        for (const plate of plates) {
          const ev = qEvents.find(e => e.target === plate.id)!
          expect(ev.time_s).toBe(stepTime)
          expect(ev.value).toBeCloseTo(Number(plate.params.Q_W) * loadFactor, 6)
        }
        return
      }
      // A DAE run the timeline drives is .liquidrun v4: every plate's Q_W is a
      // series stepping at the first sample on or after stepTime.
      expect(run.schemaVersion).toBe(4)
      expect(run.events).toEqual([])
      expect(run.transient!.start.kind).toBe('steadyConverged')
      const step = Math.round(stepTime / run.time.dt_s)
      for (const plate of plates) {
        const q = plate.params.Q_W as number[]
        expect(q).toHaveLength(run.time.N_t)
        expect(q[step - 1]).toBe(q[0])
        expect(q[step]).toBeCloseTo(q[0]! * loadFactor, 6)
        expect(q.at(-1)).toBeCloseTo(q[0]! * loadFactor, 6)
      }
    })
  }
})

describe('schedules follow the timeline, the dt grid and the mode', () => {
  beforeEach(() => { open(NV) })

  it('the timeline duration sets N_t', () => {
    store.edit('dur', '', d => { (d as unknown as { transient: { durationS: number } }).transient.durationS = 20 })
    const run = compiled()
    expect(run.time.N_t).toBe(20 / run.time.dt_s + 1)
  })

  it('a ramp is sampled into the series one value per sample', () => {
    const trk = tracks('cp.heat').find(t => t.keyframes[0]!.value > 0)!
    const [k0, k1] = trk.keyframes
    editTransient(tr => {
      const t = tr.tracks.find(x => trackKeyOf(x) === trackKeyOf(trk))!
      t.keyframes.push({ t: 8, value: k1!.value + 100, rampS: 2 })
    })
    const series = compiled().edges.map(e => e.params.Q_W).filter(Array.isArray) as number[][]
    const mine = series.find(q => q.some(v => Math.abs(v - (k1!.value + 100)) < 1e-6))!
    expect(mine.slice(5)).toEqual([k1!.value, k1!.value, k1!.value + 50, k1!.value + 100, k1!.value + 100, k1!.value + 100].slice(0, mine.length - 5))
    expect(k0!.t).toBe(0)
  })

  it('a primary-inlet keyframe becomes an inlet_K series on the DAE circuit', () => {
    const trk = tracks('cdu.primaryInletT')[0]!
    const base = trk.keyframes[0]!.value
    applyTimelineOp('inlet', addKeyframe(trackKeyOf(trk), 3, base + 5))
    const run = compiled()
    const circuit = run.primary_circuits!.find(c => c.edge_id === `${trk.modelUid}_hx`)!
    expect(Array.isArray(circuit.inlet_K)).toBe(true)
    const series = circuit.inlet_K as number[]
    expect(series).toHaveLength(run.time.N_t)
    expect(series[2]).toBeCloseTo(base + 273.15, 9)
    expect(series[3]).toBeCloseTo(base + 5 + 273.15, 9)
  })

  it('the DAE primary circuit takes its inlet and flow start values from the CDU tracks', () => {
    // ISPO data-hall evaluator (2026-10-21): edited start values never reached the circuit binding
    for (const mode of ['steady', 'transient'] as const) {
      open(NV)
      setSimulationMode(mode, 'liquid')
      for (const [param, value] of [['cdu.primaryInletT', 27.5], ['cdu.primaryFlow', 1.7]] as const) {
        const trk = tracks(param)[0]!
        editTransient(tr => { tr.tracks.find(x => trackKeyOf(x) === trackKeyOf(trk))!.keyframes[0]!.value = value })
      }
      const uid = tracks('cdu.primaryInletT')[0]!.modelUid
      const run = compiled() as unknown as { primary_circuits: { edge_id: string; inlet_K: unknown; flow_kg_s: unknown }[];
        time: { N_t: number } }
      const circuit = run.primary_circuits.find(c => c.edge_id === `${uid}_hx`)!
      expect(at(circuit.inlet_K, 0)).toBeCloseTo(27.5 + 273.15, 9)
      expect(at(circuit.flow_kg_s, 0)).toBeCloseTo(1.7, 12)
      expect(at(circuit.inlet_K, run.time.N_t - 1)).toBeCloseTo(27.5 + 273.15, 9)
    }
  })

  it('steady mode compiles the start values', () => {
    setSimulationMode('steady', 'liquid')
    const run = compiled()
    expect(run.schemaVersion).toBe(3)
    expect(run.events.filter(e => e.field === 'Q_W')).toEqual([])
    expect(run.edges.every(e => !Array.isArray(e.params.Q_W))).toBe(true)
  })

  it('refuses a keyframe off the liquid dt grid', () => {
    const exp = readExperiment(store.getProject())
    store.setRunConfig('liquid', 'experiment', { ...exp, dt_s: 2 })      // step at 5 s is off a 2 s grid
    expect(() => compiled()).toThrow(/dt grid/)
  })

  it('a return-pressure keyframe becomes the reference node\'s P_Pa series', () => {
    const trk = tracks('cdu.returnPressure')[0]!
    applyTimelineOp('p', addKeyframe(trackKeyOf(trk), 4, 170))
    const run = compiled()
    const node = run.nodes.find(n => n.id === `${trk.modelUid}_return`)!
    expect(node.bc!.P_Pa).toEqual([150000, 150000, 150000, 150000, 170000, 170000, 170000, 170000, 170000, 170000, 170000])
  })

  it('refuses a secondary-supply keyframe on an HX that is not holding a set point', () => {
    const trk = tracks('cdu.secondarySupplyT')[0]!
    applyTimelineOp('s', addKeyframe(trackKeyOf(trk), 4, trk.keyframes[0]!.value - 2))
    expect(() => compiled()).toThrow(/cdu\.secondarySupplyT would change nothing: set the CDU secondary supply to a held temperature/)
    store.edit('hold', '', d => {
      const cdu = store.findConstruction(d, trk.modelUid)!.model as unknown as { cooling: Record<string, unknown> }
      cdu.cooling.secondaryControl = 'setpoint'
    })
    const hx = compiled().edges.find(e => e.id === `${trk.modelUid}_hx`)!
    expect(hx.params.mode).toBe('setpoint')
    expect(at(hx.params.T_set_K, 3)).toBeCloseTo(trk.keyframes[0]!.value + 273.15, 9)
    expect(at(hx.params.T_set_K, 4)).toBeCloseTo(trk.keyframes[0]!.value - 2 + 273.15, 9)
  })

  it('refuses a legacy load step on top of cold-plate keyframes', () => {
    const exp = readExperiment(store.getProject())
    store.setRunConfig('liquid', 'experiment', { ...exp, settings: { ...exp.settings, loadStep: true } })
    expect(() => compiled()).toThrow(/load step/i)
  })
})

describe('a v2 graph run takes neither the v4 inputs nor the v4 forms', () => {
  it('refuses a return-pressure keyframe and a held secondary supply by name', () => {
    open(HYBRID)
    const trk = tracks('cdu.returnPressure')[0]!
    applyTimelineOp('p', addKeyframe(trackKeyOf(trk), 4, 170))
    expect(() => compiled()).toThrow(/cdu\.returnPressure cannot be compiled into a \.liquidrun v2 run/)
    open(HYBRID)
    store.edit('hold', '', d => {
      const cdu = store.findConstruction(d, trk.modelUid)!.model as unknown as { cooling: Record<string, unknown> }
      cdu.cooling.secondaryControl = 'setpoint'
    })
    expect(() => compiled()).toThrow(/needs the DAE solver/)
  })
})
