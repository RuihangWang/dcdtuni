import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as experiments from '../src/liquid/liquidExperiment'
import { serializeProjectState, checkProjectDocument } from '../src/io/projectFiles'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'

function shinaide(): CfdProject {
  const p=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'),'utf8')) as CfdProject
  // This suite checks the saved explicit legacy/opt-in contract.
  const legacy=experiments.readExperiment(p)
  delete legacy.engine;delete legacy.dae;delete legacy.parameterPack
  ;(p.runDefaults!.liquid as Record<string,unknown>).experiment=legacy
  return p
}

function prepare() {
  const project=shinaide()
  store.loadProject(project,null)
  const current=experiments.readExperiment(project)
  store.setRunConfig('liquid','experiment',{...current,duration_s:10,dt_s:1,
    settings:{...current.settings,loadStep:true,stepTime:5,loadFactor:1.4}})
  return store.getProject()!
}

describe('project-owned DAE definition',()=>{
  it('leaves legacy projects on v2 until explicit adoption, then saves and recompiles the same complete v3 physics',()=>{
    const project=prepare()
    expect(experiments.compileSavedExperiment(project).liquidrun.schemaVersion).toBe(2)
    expect(experiments).toHaveProperty('adoptMockDAEExperiment')
    const adopted=experiments.adoptMockDAEExperiment(project,{source_reference:'Explicit test choice, not NV telemetry'})
    expect(experiments.readExperiment(project).dae).toBeUndefined()
    expect(adopted.dae!.primary_circuits).toEqual([{
      edge_id:'m_30_hx',flow_kg_s:1,inlet_K:293.15,cp_J_kgK:4180,rho_kg_m3:992.2,
      primary_volume_m3:.02,secondary_volume_m3:.02,source:'mock',source_reference:'Explicit test choice, not NV telemetry',
    }])
    store.setRunConfig('liquid','experiment',adopted)
    const first=experiments.compileSavedExperiment(store.getProject()!)
    expect(first.liquidrun.schemaVersion).toBe(3)
    // The 14 racks have separate supply/return bottom ports. The old
    // default-end bug folded those 28 ports into their top manifold nodes.
    expect(first.liquidrun.nodes).toHaveLength(215)
    expect(first.liquidrun.edges).toHaveLength(270)
    const risers=first.liquidrun.edges.filter(e=>e.type==='pipe'&&/_mf_(supply|return)_bottom_header$/.test(e.id))
    expect(risers).toHaveLength(28)
    const nodes=new Map(first.liquidrun.nodes.map(n=>[n.id,n]))
    for(const riser of risers) {
      expect(riser.from).toMatch(/_bottom$/)
      expect(riser.to).toBe(riser.from.replace(/_bottom$/,''))
      const bottom=nodes.get(riser.from)!.position_m!,top=nodes.get(riser.to)!.position_m!
      expect(bottom).not.toEqual(top)
      expect(top[1]-bottom[1]).toBeCloseTo(1.958,10)
      if(riser.type!=='pipe')throw new Error('Expected manifold pipe')
      expect(riser.params.waypoints_m).toEqual([bottom,top])
    }
    expect(first.liquidrun.events).toHaveLength(56)
    expect(first.liquidrun).not.toHaveProperty('solver')
    expect(JSON.stringify(first.liquidrun)).not.toContain('T_secondary_K')
    const reopened=JSON.parse(serializeProjectState(store.getProject()!)) as CfdProject
    expect(experiments.compileSavedExperiment(reopened)).toEqual(first)
    if(process.env.LIQUID_DAE_SCHEMA_FIXTURE)writeFileSync(process.env.LIQUID_DAE_SCHEMA_FIXTURE,JSON.stringify(first.liquidrun,null,2))
    store.undo()
    expect(experiments.compileSavedExperiment(store.getProject()!).liquidrun.schemaVersion).toBe(2)
    store.redo()
    expect(experiments.compileSavedExperiment(store.getProject()!)).toEqual(first)
  })

  it('rejects missing HX bindings, unsupported PID and malformed saved DAE settings',()=>{
    const project=prepare()
    expect(experiments).toHaveProperty('adoptMockDAEExperiment')
    const adopted=experiments.adoptMockDAEExperiment(project)
    const missing=structuredClone(adopted)
    missing.dae!.primary_circuits=[]
    store.setRunConfig('liquid','experiment',missing)
    expect(()=>experiments.compileSavedExperiment(store.getProject()!)).toThrow(/heat.exchanger|HX|primary_circuits/)
    const pid=structuredClone(adopted)
    // The DAE runs a pump loop as PI; one without integral gain is refused.
    pid.settings.mode='temperature';pid.settings.ki=0
    store.setRunConfig('liquid','experiment',pid)
    expect(()=>experiments.compileSavedExperiment(store.getProject()!)).toThrow(/integral gain/)
    const invalid=structuredClone(project) as any
    invalid.runDefaults.liquid.experiment=structuredClone(adopted)
    invalid.runDefaults.liquid.experiment.dae.primary_circuits[0].primary_volume_m3=0
    expect(checkProjectDocument(invalid).ok).toBe(false)
    expect(()=>serializeProjectState(invalid)).toThrow(/primary_volume_m3/)
  })

  it('exports a true single-sample steady request and refuses to discard a configured load event',()=>{
    const project=prepare()
    expect(experiments).toHaveProperty('adoptMockDAEExperiment')
    const adopted=experiments.adoptMockDAEExperiment(project)
    adopted.dae!.analysis.mode='steady'
    store.setRunConfig('liquid','experiment',adopted)
    expect(()=>experiments.compileSavedExperiment(store.getProject()!)).toThrow(/steady|稳态|event/)
    adopted.settings.loadStep=false
    store.setRunConfig('liquid','experiment',adopted)
    const result=experiments.compileSavedExperiment(store.getProject()!).liquidrun
    expect(result.schemaVersion).toBe(3)
    expect(result.time.N_t).toBe(1)
    expect(result.events).toEqual([])
    if(result.schemaVersion!==3)throw new Error('Expected DAE v3')
    expect(result.analysis).toEqual({mode:'steady',initialization:'steady'})
  })

  it('persists exact primary series and refuses length errors or an unsaved equipment pack',()=>{
    const project=prepare()
    expect(experiments).toHaveProperty('adoptMockDAEExperiment')
    const adopted=experiments.adoptMockDAEExperiment(project)
    adopted.dae!.primary_circuits[0]!.inlet_K=Array.from({length:11},(_,index)=>293.15+index*.1)
    store.setRunConfig('liquid','experiment',adopted)
    const saved=JSON.parse(serializeProjectState(store.getProject()!)) as CfdProject
    const result=experiments.compileSavedExperiment(saved).liquidrun
    if(result.schemaVersion!==3)throw new Error('Expected DAE v3')
    expect(result.primary_circuits[0]!.inlet_K).toEqual(adopted.dae!.primary_circuits[0]!.inlet_K)
    adopted.dae!.primary_circuits[0]!.inlet_K=[293.15]
    store.setRunConfig('liquid','experiment',adopted)
    expect(()=>experiments.compileSavedExperiment(store.getProject()!)).toThrow(/N_t|series|samples/)
    adopted.dae!.primary_circuits[0]!.inlet_K=293.15
    delete adopted.parameterPack
    store.setRunConfig('liquid','experiment',adopted)
    expect(()=>experiments.compileSavedExperiment(store.getProject()!)).toThrow(/parameterPack|equipment pack/)
  })
})

it('preserves execution device, dtype and CPU thread budget through project save and graph export',()=>{
 const project=prepare()
 const adopted=experiments.adoptMockDAEExperiment(project)
 adopted.dae!.execution={backend:'pytorch',device:'cuda:1',dtype:'float64',max_threads:4}
 store.setRunConfig('liquid','experiment',adopted)
 const reopened=JSON.parse(serializeProjectState(store.getProject()!)) as CfdProject
 const result=experiments.compileSavedExperiment(reopened).liquidrun
 expect(result.execution).toEqual(adopted.dae!.execution)
 const bad=structuredClone(reopened) as any
 bad.runDefaults.liquid.experiment.dae.execution.max_threads=0
 expect(checkProjectDocument(bad).ok).toBe(false)
})
