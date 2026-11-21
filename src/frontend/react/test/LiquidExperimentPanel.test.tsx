import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, useSyncExternalStore } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { LiquidExperimentPanel } from '../src/components/LiquidExperimentPanel'
import * as store from '../src/projectStore'
import { readExperiment } from '../src/liquid/liquidExperiment'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import type { CfdProject } from '../src/schema/cfdproj'
import { getLocale, setLocale } from '../src/i18n'
const mocks=vi.hoisted(()=>({create:vi.fn().mockResolvedValue(null),state:{session:null,frames:[],projectRevision:null,busy:false,error:null,connection:'idle'}}))
vi.mock('../src/liquid/liquidRuntimeClient',async (importOriginal)=>({...(await importOriginal<typeof import('../src/liquid/liquidRuntimeClient')>()),subscribeRuntime:()=>()=>{},runtimeState:()=>mocks.state,createRuntimeSession:mocks.create}))
vi.mock('../src/components/LiquidSolverPanel',()=>({LiquidSolverPanel:()=>null}))
let cleanup=()=>{}
afterEach(()=>{cleanup();mocks.create.mockClear()})
it('selects a non-first pump and cold plate before submitting the first legacy run',async()=>{
 ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
 const previousLocale=getLocale();setLocale('zh-CN')
 const project=JSON.parse(readFileSync(resolve(process.cwd(),'test/fixtures/cases/NV_PEK02_mock.cfdproj'),'utf8')) as CfdProject
 store.loadProject(project,null)
 // Read back from the loaded project: its load step is cold-plate keyframes now.
 const legacy=readExperiment(store.getProject())
 delete legacy.dae
 delete legacy.parameterPack // explicitly use the existing v2 mock curve library
 store.setRunConfig('liquid','experiment',legacy)
 const graph=compileDynamicLiquidGraph(store.getProject()!)
 const pumps=graph.edges.filter(edge=>edge.type==='pump')
 const plates=graph.edges.filter(edge=>edge.type==='cold_plate')
 expect(pumps).toHaveLength(2)
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 cleanup=()=>{act(()=>root.unmount());host.remove();setLocale(previousLocale)}
 function Harness() {
  useSyncExternalStore(store.subscribe,store.revision)
  return <LiquidExperimentPanel project={store.getProject()}/>
 }
 await act(async()=>root.render(<Harness/>))
 const select=(label:string,value:string)=>{const el=[...host.querySelectorAll('label')].find(x=>x.textContent?.includes(label))!.querySelector('select')!;act(()=>{el.value=value;el.dispatchEvent(new Event('change',{bubbles:true}))})}
 const click=async(text:string)=>{await act(async()=>{([...host.querySelectorAll('button')].find(x=>x.textContent===text)!).click()})}
 select('控制方式','temperature');await click('读取当前设备')
 expect(mocks.create).not.toHaveBeenCalled()
 // A legacy v2 experiment marches in time: its first run is initialised at t = 0.
 select('执行泵',pumps[1]!.id);select('测温冷板',plates[1]!.id);await click('仅初始化')
 expect(mocks.create.mock.calls[0]?.[0].schemaVersion).toBe(2)
 expect(mocks.create.mock.calls[0]?.[1][0]).toMatchObject({measurement:{target_id:plates[1]!.id},actuator:{edge_id:pumps[1]!.id}})
})
