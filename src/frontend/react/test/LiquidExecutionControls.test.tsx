import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {act,useSyncExternalStore} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {LiquidExperimentPanel} from '../src/components/LiquidExperimentPanel'
import {compileSavedExperiment,readExperiment,resolveSavedExperiment,adoptMockDAEExperiment,withDAEExecution} from '../src/liquid/liquidExperiment'
import * as store from '../src/projectStore'
import * as runtime from '../src/liquid/liquidRuntimeClient'
import {readFluid,readLiquidConfig} from '../src/liquid/liquidConfig'
import {schema} from '../src/legacy/cfdprojModules'
import {setLocale,t} from '../src/i18n'
import {setSimulationMode} from '../src/transient/simulationMode'
import type {CfdProject} from '../src/schema/cfdproj'
let root:Root,host:HTMLDivElement
function App(){useSyncExternalStore(store.subscribe,store.revision);return <LiquidExperimentPanel project={store.getProject()}/>}
function scenario(){const p=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'),'utf8')) as CfdProject;const e=readExperiment(p);delete e.dae;delete e.parameterPack;delete e.execution;e.engine='newton-dae';e.settings.mode='manual';e.settings.loadStep=false;(p.runDefaults!.liquid as any).experiment=e;return p}
function select(id:string,value:string){const el=host.querySelector('#'+id) as HTMLSelectElement;expect(el,'control '+id).not.toBeNull();act(()=>{el.value=value;el.dispatchEvent(new Event('change',{bubbles:true}))})}
function commit(id:string,value:number){const el=host.querySelector('#'+id) as HTMLInputElement;expect(el).not.toBeNull();const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!;act(()=>{el.focus();setter.call(el,String(value));el.dispatchEvent(new Event('input',{bubbles:true}))});act(()=>el.blur())}
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;setLocale('en-US');runtime.resetRuntimeClient();store.loadProject(scenario(),null);host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);act(()=>root.render(<App/>))})
afterEach(()=>{act(()=>root.unmount());host.remove();vi.restoreAllMocks();runtime.resetRuntimeClient()})
it('persists pre-adoption execution through reload and compile without adopting mock physics on device change',()=>{
 const old=structuredClone(readLiquidConfig(store.getProject()).solver)
 select('lce-dae-device','cuda');commit('lce-dae-cuda-index',1);select('lce-dae-dtype','float32');select('lce-dae-threads','4')
 let e=readExperiment(store.getProject());expect(e.dae).toBeUndefined();expect(e.parameterPack).toBeUndefined();expect(e.execution).toEqual({backend:'pytorch',device:'cuda:1',dtype:'float32',max_threads:4})
 expect(readLiquidConfig(store.getProject()).solver).toEqual(old)
 const snapshot=JSON.stringify(store.getProject());act(()=>store.loadProject(JSON.parse(snapshot),null))
 const compiled=compileSavedExperiment(store.getProject()!).liquidrun as any
 expect(compiled.execution).toEqual(e.execution);expect(compiled.solver).toBeUndefined()
 expect(JSON.stringify(store.getProject())).toBe(snapshot)
 e=resolveSavedExperiment(store.getProject()!);expect(e.execution).toBeUndefined();expect(e.dae?.execution).toEqual(compiled.execution)
 expect(schema().validateLiquidExperiment(e)).toEqual([])
})
it('uses the same secondary fluid editor for DAE compilation and keeps explicit primary fluid unchanged',()=>{
 act(()=>store.setRunConfig('liquid','experiment',adoptMockDAEExperiment(store.getProject()!)))
 const primary=structuredClone(readExperiment(store.getProject()).dae!.primary_circuits)
 expect(host.querySelectorAll('#lc-fluid-select')).toHaveLength(1);expect(host.querySelector('#lc-run-btn')).toBeNull()
 select('lc-fluid-select','glycol25');commit('lsm-fluid-rho',1040);commit('lsm-fluid-cp',3700);commit('lsm-fluid-mu',.003);commit('lsm-fluid-k',.45)
 const f=readFluid(store.getProject());expect(f).toEqual({type:'custom',rho:1040,cp:3700,mu:.003,k:.45})
 const out=compileSavedExperiment(store.getProject()!).liquidrun as any
 expect(out.fluid).toMatchObject({rho_kg_m3:1040,cp_J_kgK:3700,mu_Pa_s:.003,k_W_mK:.45})
 expect(readExperiment(store.getProject()).dae!.primary_circuits).toEqual(primary)
 expect(out.primary_circuits[0].rho_kg_m3).toBe(primary[0]!.rho_kg_m3)
})
it('writes effective Newton settings while retaining unrelated legacy BDF/optimizer configuration',()=>{
 act(()=>{store.setNestedRunConfig('liquid','solver','bdf_order',3);store.setNestedRunConfig('liquid','solver','lr',.2);store.setRunConfig('liquid','experiment',adoptMockDAEExperiment(store.getProject()!))})
 commit('lce-newton-max_iterations',70);commit('lce-newton-atol',1e-7);commit('lce-newton-rtol',2e-6);commit('lce-newton-max_backtracks',20)
 const out=compileSavedExperiment(store.getProject()!).liquidrun as any
 expect(out.newton).toEqual({max_iterations:70,atol:1e-7,rtol:2e-6,max_backtracks:20});expect(out.solver).toBeUndefined()
 expect(readLiquidConfig(store.getProject()).solver).toMatchObject({bdf_order:3,lr:.2})
})
it('keeps unmarked v2/PID inputs and their compatibility solver controls intact',()=>{
 const e=readExperiment(store.getProject());delete e.engine;e.settings={...e.settings,mode:'temperature',pumpId:'saved-pump',measurementId:'saved-cp',kp:.07,ki:.004}
 act(()=>store.setRunConfig('liquid','experiment',e))
 const before=JSON.stringify(readExperiment(store.getProject()))
 expect(host.querySelector('#lce-dae-device')).toBeNull();expect(host.querySelector('#lc-run-btn')).not.toBeNull();expect(host.querySelectorAll('#lc-fluid-select')).toHaveLength(1)
 // Adopting the DAE keeps the saved PI loop: the DAE solves it inside each step.
 expect(adoptMockDAEExperiment(store.getProject()!).settings).toMatchObject({mode:'temperature',pumpId:'saved-pump',measurementId:'saved-cp',kp:.07,ki:.004})
 expect(()=>withDAEExecution(e,{backend:'pytorch',device:'cuda',dtype:'float64',max_threads:2})).toThrow(/Legacy/)
 expect(JSON.stringify(readExperiment(store.getProject()))).toBe(before)
})
it('rejects ambiguous or ignored execution intent without normalizing saved PID',()=>{
 const e=readExperiment(store.getProject());const good={backend:'pytorch' as const,device:'cpu' as const,dtype:'float64' as const,max_threads:2}
 for(const bad of [{...good,device:'cuda:evil'},{...good,max_threads:0},{...good,max_threads:1.5},{...good,dtype:'half'},{...good,extra:1}])expect(schema().validateLiquidExperiment({...e,execution:bad}).length).toBeGreaterThan(0)
 const adopted=adoptMockDAEExperiment(store.getProject()!);expect(schema().validateLiquidExperiment({...adopted,execution:good}).length).toBeGreaterThan(0)
 // A DAE pump loop is PI: a saved loop without integral gain is refused.
 expect(schema().validateLiquidExperiment({...e,execution:good,settings:{...e.settings,mode:'temperature',ki:0}}).length).toBeGreaterThan(0)
 expect(schema().validateLiquidExperiment({...e,execution:good,settings:{...e.settings,mode:'temperature',ki:.002}})).toEqual([])
 expect(schema().validateLiquidExperiment({...e,engine:undefined,execution:good}).length).toBeGreaterThan(0)
})
it('reports unavailable actual execution as unknown rather than echoing requested CUDA',()=>{
 const metadata={solver:'differentiable_newton_dae',node_ids:[],edge_ids:[],cp_edge_ids:[],total_sample_count:1,dt_s:1,object_bindings:{},execution:{device:'cuda',dtype:'float64',max_threads:2}}
 expect(runtime.observedRuntimeExecution(metadata)).toBeNull()
 expect(runtime.observedRuntimeExecution({...metadata,execution:{...metadata.execution,actual_device:'cpu',actual_dtype:'float64',actual_threads:2}})).toEqual({device:'cpu',dtype:'float64',threads:2})
 expect(runtime.observedRuntimeExecution({...metadata,execution:{actual_device:'cuda:0',actual_dtype:'float32',actual_threads:1}})).toEqual({device:'cuda:0',dtype:'float32',threads:1})
 expect(runtime.observedRuntimeExecution({...metadata,execution:{actual_device:'cuda',actual_dtype:'float32',actual_threads:1}})).toBeNull()
 expect(runtime.observedRuntimeExecution({...metadata,execution:{actual_device:'cpu',actual_dtype:'float64',actual_threads:NaN}})).toBeNull()
})

it('lowers v4 DAE to backward Euler without editing a saved legacy BDF3 request',()=>{
 act(()=>{store.setNestedRunConfig('liquid','solver','bdf_order',3);store.setRunConfig('liquid','experiment',adoptMockDAEExperiment(store.getProject()!));setSimulationMode('transient','liquid')})
 const before=JSON.stringify(store.getProject())
 const out=compileSavedExperiment(store.getProject()!).liquidrun as any
 expect(out.schemaVersion).toBe(4);expect(out.time.bdf_order).toBe(1);expect(out.solver).toBeUndefined();expect(JSON.stringify(store.getProject())).toBe(before)
 expect(readLiquidConfig(store.getProject()).solver.bdf_order).toBe(3)
})
it('renders actual backend tensor execution separately and disables configuration during an active run',()=>{
 act(()=>store.setRunConfig('liquid','experiment',adoptMockDAEExperiment(store.getProject()!)))
 const metadata:runtime.RuntimeMetadata={solver:'differentiable_newton_dae',node_ids:[],edge_ids:[],cp_edge_ids:[],total_sample_count:2,dt_s:1,object_bindings:{},execution:{device:'cuda',dtype:'float64',max_threads:2}}
 let snapshot={...runtime.runtimeState(),session:{run_id:'controlled-display',config_hash:'fixture',status:'ready',metadata,frame:{seq:0,time_s:0,heat_multiplier:1}}} as runtime.RuntimeClientState
 vi.spyOn(runtime,'runtimeState').mockImplementation(()=>snapshot)
 act(()=>root.render(<App/>))
 expect(host.querySelector('#lce-actual-execution')?.textContent).toContain('have not been reported')
 expect((host.querySelector('#lce-dae-device') as HTMLSelectElement).matches(':disabled')).toBe(true)
 expect((host.querySelector('#lc-fluid-select') as HTMLSelectElement).matches(':disabled')).toBe(true)
 snapshot={...snapshot,session:{...snapshot.session!,metadata:{...metadata,execution:{...metadata.execution,actual_device:'cpu',actual_dtype:'float64',actual_threads:2}}}}
 act(()=>root.render(<App/>))
 expect(host.querySelector('#lce-actual-execution')?.textContent).toContain('Actual solve: cpu · float64 · 2 threads')
 expect(host.querySelector('#lce-actual-execution')?.textContent).not.toContain('cuda')
})

it('submits the main Prepare command with the selected GPU intent after saving its physical inputs',async()=>{
 const create=vi.spyOn(runtime,'createRuntimeSession').mockResolvedValue(null)
 select('lce-dae-device','cuda');commit('lce-dae-cuda-index',1)
 // The run section names the action by analysis mode (steady / transient).
 const button=[...host.querySelectorAll('button')].find(el=>el.textContent===t('lq_ux.solve_steady')||el.textContent===t('lq_ux.init_only'))
 expect(button).toBeDefined();await act(async()=>button!.click())
 expect(create).toHaveBeenCalledTimes(1)
 const out=create.mock.calls[0]![0] as any
 expect(out.execution).toEqual({backend:'pytorch',device:'cuda:1',dtype:'float64',max_threads:2})
 const saved=readExperiment(store.getProject());expect(saved.execution).toBeUndefined();expect(saved.dae?.execution).toEqual(out.execution)
 expect(host.querySelector('[role=alert]')).toBeNull()
})
