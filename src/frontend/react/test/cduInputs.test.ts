import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { readProjectFileText } from '../src/io/projectFiles'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  cduFieldUse, effectiveHxModel, lpmToKgS, savedPrimaryCircuit, setHxModel, setPrimaryInput, setPumpSpeedRatio,
  setReturnPressure, withCduPrimaryInputs,
} from '../src/liquid/cduInputs'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { compileSavedExperiment, readExperiment, resolveSavedExperiment } from '../src/liquid/liquidExperiment'
import { setValveOpening, valveSummary } from '../src/liquid/pipeInspector'

type Bag = Record<string, any>
const root = resolve(process.cwd(), '../../..')
function load(path: string): CfdProject {
  const result = readProjectFileText(readFileSync(resolve(root, path), 'utf8'))
  if (!result.ok) throw new Error(result.error)
  store.loadProject(result.project, null)
  return store.getProject()!
}
const cduOf = (p: CfdProject) => Object.values((p as Bag).constructions.cdus)[0] as Bag
const hxOf = (p: CfdProject) => compileDynamicLiquidGraph(p).edges.find(e => e.type === 'heat_exchanger')!
const pumpOf = (p: CfdProject) => compileDynamicLiquidGraph(p).edges.find(e => e.type === 'pump')!
const returnBc = (p: CfdProject, uid: string) => compileDynamicLiquidGraph(p).nodes.find(n => n.id === `${uid}_return`)!.bc

describe('what the CDU panel says each field does', () => {
  it('reads an unstated HX model as two mixed volumes, a stated one as stated', () => {
    expect(effectiveHxModel({})).toBe('mixed-volumes')
    expect(effectiveHxModel({ hxModel: 'counterflow-ntu' })).toBe('counterflow-ntu')
    expect(effectiveHxModel({ hxModel: 'bogus' })).toBe('mixed-volumes')
  })
  it('enables the set-point temperature and the fixed flow only in their modes', () => {
    expect(cduFieldUse({})).toEqual({ supplyTemperature: false, totalFlow: false })
    expect(cduFieldUse({ secondaryControl: 'setpoint', pumpControl: 'flow' })).toEqual({ supplyTemperature: true, totalFlow: true })
  })
  it('converts L/min to kg/s with the fluid density, as the compiler does', () => {
    expect(lpmToKgS(60, 997)).toBeCloseTo(0.997, 12)
  })
})

describe('CDU edits reach the compiled DAE input', () => {
  it('return reference pressure becomes the return node pressure', () => {
    const p = load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj'), uid = cduOf(p).uid
    expect(returnBc(p, uid)).toEqual({ P_Pa: 150000 })
    expect(setReturnPressure(uid, 180)).toBe(true)
    expect(returnBc(store.getProject()!, uid)).toEqual({ P_Pa: 180000 })
    expect(setReturnPressure(uid, 0)).toBe(false)
  })
  it('pump speed ratio becomes the curve pump rpm_ratio', () => {
    const p = load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj'), uid = cduOf(p).uid
    expect(setPumpSpeedRatio(uid, 0.8)).toBe(true)
    expect(pumpOf(store.getProject()!).params).toEqual({ rpm_ratio: 0.8 })
    expect(setPumpSpeedRatio(uid, -1)).toBe(false)
  })
  it('HX model is written on the HX edge', () => {
    const p = load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj'), uid = cduOf(p).uid
    expect(setHxModel(uid, 'counterflow-ntu')).toBe(true)
    expect(hxOf(store.getProject()!).params.hxModel).toBe('counterflow-ntu')
  })
  it('steady primary inlet/flow edits move the saved DAE circuit, so every compile path reads them', () => {
    const p = load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj'), uid = cduOf(p).uid
    // This project has never been prepared: the first primary edit adopts the
    // DAE inputs, with the edited value in them.
    expect(readExperiment(p).dae).toBeUndefined()
    expect(setPrimaryInput(uid, 'inletC', 24)).toBe(true)
    expect(readExperiment(store.getProject()).dae).toBeTruthy()
    expect(setPrimaryInput(uid, 'flowKgS', 1.6)).toBe(true)
    const now = store.getProject()!
    expect(cduOf(now).cooling).toMatchObject({ primaryInletTemperatureC: 24, primaryFlowKgS: 1.6 })
    expect(savedPrimaryCircuit(now, uid)).toMatchObject({ inlet_K: 24 + 273.15, flow_kg_s: 1.6 })
    const circuit = (compileSavedExperiment(now).liquidrun as Bag).primary_circuits.find((c: Bag) => c.edge_id === `${uid}_hx`)
    expect(circuit).toMatchObject({ inlet_K: 24 + 273.15, flow_kg_s: 1.6 })
    expect(setPrimaryInput(uid, 'inletC', 120)).toBe(false)
  })
  it('applies stated CDU primary values to circuits adopted later', () => {
    const p = load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj') as Bag
    const uid = cduOf(p).uid
    cduOf(p).cooling.primaryInletTemperatureC = 18
    const experiment = resolveSavedExperiment(p as CfdProject)
    const next = withCduPrimaryInputs(p as CfdProject, experiment)
    expect(next.dae!.primary_circuits.find(c => c.edge_id === `${uid}_hx`)!.inlet_K).toBeCloseTo(291.15, 12)
    expect(withCduPrimaryInputs(p as CfdProject, next)).toBe(next)
  })
})

describe('valve opening', () => {
  it('edits a control valve opening that the compiler then uses, refusing a tee and out-of-range values', () => {
    const p = load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj') as Bag
    const pipe = Object.values(p.constructions.pipeSegments as Record<string, Bag>)
      .find(m => (m.cooling?.valves ?? []).some((v: Bag) => v.type === 'flow_control'))!
    const valve = pipe.cooling.valves.find((v: Bag) => v.type === 'flow_control')
    expect(valveSummary(pipe as never, valve.id)).toMatchObject({ adjustable: true })
    expect(setValveOpening(pipe.uid, valve.id, 0.4)).toBe(true)
    const edge = compileDynamicLiquidGraph(store.getProject()!).edges.find(e => e.id === `${pipe.uid}_control_${valve.id}`)!
    expect(edge.params.opening).toBe(0.4)
    expect(setValveOpening(pipe.uid, valve.id, 0)).toBe(false)
    expect(setValveOpening(pipe.uid, valve.id, 1.2)).toBe(false)
    const tee = (pipe.cooling.valves as Bag[]).find(v => v.type === 'tee_junction')
    if (tee) expect(setValveOpening(pipe.uid, tee.id, 0.5)).toBe(false)
  })
})
