import { readFileSync, writeFileSync } from 'node:fs'
import { liveProject } from '../src/transient/model'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as experiment from '../src/liquid/liquidExperiment'
import { serializeProjectState, checkProjectDocument, readProjectFileText } from '../src/io/projectFiles'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'

function shinaide(): CfdProject {
  const p=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'),'utf8')) as CfdProject
  // This suite checks the saved explicit legacy/opt-in contract.
  const legacy=experiment.readExperiment(p)
  delete legacy.engine;delete legacy.dae;delete legacy.parameterPack
  ;(p.runDefaults!.liquid as Record<string,unknown>).experiment=legacy
  return p
}
describe('saved project owns liquid experiment',()=>{
  it('recompiles the same physics after saving and reopening the mixed Shinaide case',()=>{
    const p=shinaide()
    const saved={...experiment.readExperiment(p),duration_s:10,dt_s:1}
    saved.settings={...saved.settings,loadStep:true,stepTime:5,loadFactor:1.4}
    store.loadProject(p,null)
    store.setRunConfig('liquid','experiment',saved)
    const original=experiment.compileSavedExperiment(store.getProject()!)
    const reopened=JSON.parse(serializeProjectState(store.getProject()!)) as CfdProject
    const again=experiment.compileSavedExperiment(reopened)
    expect(again).toEqual(original)
    if(process.env.LIQUID_SCHEMA_FIXTURE)writeFileSync(process.env.LIQUID_SCHEMA_FIXTURE,JSON.stringify(again.liquidrun,null,2))
    expect(again.liquidrun.racks).toHaveLength(14)
    expect(again.liquidrun.events).toHaveLength(56)
    expect(again.liquidrun.time.N_t).toBe(11)
    expect(experiment.readExperiment(reopened).settings.loadFactor).toBe(1.4)
    store.undo()
    expect(experiment.readExperiment(store.getProject()!).settings.loadStep).toBe(false)
    store.redo()
    expect(experiment.readExperiment(store.getProject()!).settings.loadStep).toBe(true)
  })
  it('uses a saved equipment pack rather than replacing it with default mock curves',()=>{
    const p=shinaide()
    const initial=experiment.compileSavedExperiment(p)
    const pack=structuredClone(initial.liquidrun.parameter_pack!)
    pack.id='explicit-test-pack';pack.revision='r2'
    store.loadProject(p,null)
    store.setRunConfig('liquid','experiment',{...experiment.readExperiment(p),parameterPack:pack})
    expect(experiment.compileSavedExperiment(store.getProject()!).liquidrun.parameter_pack?.id).toBe('explicit-test-pack')
  })
  it('the canonical cfdproj schema rejects a saved scenario with invalid time or control fields',()=>{
    const p=shinaide() as any
    const schema=(window as any).cfdprojSchema
    p.runDefaults.liquid.experiment={...experiment.readExperiment(p),dt_s:0}
    expect(schema.validateDocument(p).some((e:{path:string})=>e.path.includes('experiment'))).toBe(true)
    expect(checkProjectDocument(p).ok).toBe(false)
    expect(()=>serializeProjectState(p)).toThrow(/dt_s/)
    p.runDefaults.liquid.experiment={...experiment.readExperiment(null),settings:{...experiment.readExperiment(null).settings,mode:'invented'}}
    expect(schema.validateDocument(p).some((e:{path:string})=>e.path.includes('settings.mode'))).toBe(true)
  })
  it('keeps an explicit mock hybrid heat budget across load, save and liquid compilation',()=>{
    // The project as the editor holds it (cfdproj v5, live at t = 0): save and
    // reopen must not change what it compiles to.
    const p=liveProject(shinaide(),0)
    const original=experiment.compileSavedExperiment(p).liquidrun
    p.runDefaults!.hybridHeatBudget={version:1,source:'mock',source_reference:'synthetic-test-fixture',
      load_mapping:'shared_heat_multiplier',air_surface_allocation:{front_fraction:0.5,rear_fraction:0.5},
      servers:{'m_1/e_1':{total_it_power_W:1500}}}
    const accepted=checkProjectDocument(p)
    expect(accepted.ok).toBe(true)
    const saved=serializeProjectState(p)
    const reopened=readProjectFileText(saved)
    expect(reopened.ok).toBe(true)
    if(!reopened.ok)return
    expect(reopened.project.runDefaults?.hybridHeatBudget).toEqual(p.runDefaults?.hybridHeatBudget)
    expect(experiment.compileSavedExperiment(reopened.project).liquidrun).toEqual(original)
  })
  it('rejects an invalid hybrid heat budget at load, save and both liquid compile entries',()=>{
    const p=shinaide() as any
    p.runDefaults.hybridHeatBudget={version:1,source:'mock',source_reference:'synthetic-test-fixture',
      load_mapping:'shared_heat_multiplier',air_surface_allocation:{front_fraction:0.5,rear_fraction:0.5},
      servers:{'m_1/e_1':{total_it_power_W:0}}}
    expect(checkProjectDocument(p).ok).toBe(false)
    expect(readProjectFileText(JSON.stringify(p)).ok).toBe(false)
    expect(()=>serializeProjectState(p)).toThrow(/total_it_power_W/)
    expect(()=>experiment.compileSavedExperiment(p)).toThrow(/total_it_power_W/)
    expect(()=>compileDynamicLiquidGraph(p,{duration_s:10,dt_s:1})).toThrow(/total_it_power_W/)
    p.runDefaults.hybridHeatBudget.servers={'m_1/not-a-server':{total_it_power_W:1500}}
    expect(checkProjectDocument(p).ok).toBe(false)
    expect(()=>serializeProjectState(p)).toThrow(/m_1\/not-a-server/)
    expect(()=>experiment.compileSavedExperiment(p)).toThrow(/m_1\/not-a-server/)
    p.runDefaults.hybridHeatBudget.servers={'m_1/e_1':{total_it_power_W:1500}}
    p.runDefaults.hybridHeatBudget.air_surface_allocation={front_fraction:0.7,rear_fraction:0.2}
    expect(checkProjectDocument(p).ok).toBe(false)
    expect(()=>serializeProjectState(p)).toThrow(/air_surface_allocation/)
    expect(()=>experiment.compileSavedExperiment(p)).toThrow(/air_surface_allocation/)
  })
})
