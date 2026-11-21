import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { adoptMockDAEExperiment, compileSavedExperiment, readExperiment } from '../src/liquid/liquidExperiment'
import { createModel } from '../src/legacy/factories'
import { assertLiquidrunDAEV3 } from '../src/schema/liquidrunDAE'

// Owner decision 2026-10-21: "new projects default to counterflow; saved runs
// without the field replay as mixed-volumes, so old runs stay reproducible".
// A CDU made by the editor's factory states cooling.hxModel='counterflow-ntu';
// a saved CDU without the field compiles exactly as before (no hxModel on the
// HX edge, which the DAE reads as mixed-volumes), so re-running an existing
// project does not silently change its physics.
function project(): any {
  return JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/LiquidSingle/LiquidSingle.cfdproj'), 'utf8'))
}
const hxOf = (graph: {edges: {type: string; params: Record<string, unknown>}[]}) =>
  graph.edges.filter(e => e.type === 'heat_exchanger').map(e => e.params.hxModel)

describe('per-HX model selection in the dynamic compiler', () => {
  it('compiles a saved CDU without cooling.hxModel exactly as before: no field, mixed-volumes by schema', () => {
    const p = project()
    expect(p.constructions.cdus.m_1.cooling.hxModel).toBeUndefined()
    const hx = compileDynamicLiquidGraph(p).edges.filter(e => e.type === 'heat_exchanger')
    expect(hx.map(e => 'hxModel' in e.params)).toEqual([false])
  })
  it('gives a CDU created by the editor factory the counterflow reading', () => {
    const made = createModel(project(), 'cdu').model as any
    expect(made.cooling.hxModel).toBe('counterflow-ntu')
  })
  it('writes the model a CDU states, either one', () => {
    for (const model of ['counterflow-ntu', 'mixed-volumes']) {
      const p = project()
      p.constructions.cdus.m_1.cooling.hxModel = model
      expect(hxOf(compileDynamicLiquidGraph(p))).toEqual([model])
    }
  })
  it('rejects an unknown model instead of guessing', () => {
    const p = project()
    p.constructions.cdus.m_1.cooling.hxModel = 'counterflow'
    expect(() => compileDynamicLiquidGraph(p)).toThrow(/hxModel/)
  })
})

describe('hxModel through the saved-experiment exports', () => {
  function adopted(p: any) {
    const experiment = adoptMockDAEExperiment(p as CfdProject, {source_reference: 'unit test, not site data'})
    p.runDefaults = {...p.runDefaults, liquid: {...p.runDefaults?.liquid, experiment}}
    return p
  }
  it('leaves a saved CDU without the field out of the DAE manifest, which the shared schema admits', () => {
    const {liquidrun} = compileSavedExperiment(adopted(project()))
    expect(liquidrun.schemaVersion).toBe(3)
    expect(JSON.stringify(liquidrun)).not.toContain('hxModel')
    expect(() => assertLiquidrunDAEV3(liquidrun)).not.toThrow()
  })
  it('writes a stated counterflow-ntu into a DAE manifest that the shared schema admits', () => {
    const p = project()
    p.constructions.cdus.m_1.cooling.hxModel = 'counterflow-ntu'
    const {liquidrun} = compileSavedExperiment(adopted(p))
    expect(hxOf(liquidrun as any)).toEqual(['counterflow-ntu'])
    expect(() => assertLiquidrunDAEV3(liquidrun)).not.toThrow()
  })
  it('keeps a legacy v2 export free of the DAE-only field', () => {
    const p = project()
    p.constructions.cdus.m_1.cooling.hxModel = 'counterflow-ntu'
    const legacy = readExperiment(p)
    delete legacy.engine; delete legacy.dae; delete legacy.parameterPack
    p.runDefaults = {...p.runDefaults, liquid: {...p.runDefaults?.liquid, experiment: legacy}}
    const {liquidrun} = compileSavedExperiment(p)
    expect(liquidrun.schemaVersion).toBe(2)
    expect(JSON.stringify(liquidrun)).not.toContain('hxModel')
  })
})
