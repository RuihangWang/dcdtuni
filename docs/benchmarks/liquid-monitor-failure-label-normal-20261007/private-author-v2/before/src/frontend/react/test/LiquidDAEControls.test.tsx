import { readFileSync, writeFileSync } from 'node:fs'
import { setSimulationMode } from '../src/transient/simulationMode'
import { resolve } from 'node:path'
import { act, useSyncExternalStore } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LiquidExperimentPanel } from '../src/components/LiquidExperimentPanel'
import { LiquidRuntimeMonitor } from '../src/components/LiquidRuntimeMonitor'
import * as store from '../src/projectStore'
import * as runtime from '../src/liquid/liquidRuntimeClient'
import { adoptMockDAEExperiment, compileSavedExperiment, expandSupplySchedule, readExperiment } from '../src/liquid/liquidExperiment'
import { mmsAnalyticCurves } from '../src/liquid/mmsAnalytic'
import { serializeProjectState } from '../src/io/projectFiles'
import type { CfdProject } from '../src/schema/cfdproj'
import { getLocale, setLocale, t, type Locale } from '../src/i18n'

vi.mock('../src/components/LiquidSolverPanel',()=>({LiquidSolverPanel:()=>null}))
let root:Root,host:HTMLDivElement
let current:runtime.RuntimeSession|null=null
let frames:runtime.RuntimeFrame[]=[]
let requests:{url:string;body:any}[]=[]
const textCalls:string[]=[]
// Hoisted so tests can assert on draw calls (setLineDash: analytic overlays are
// dashed, run data never is).
let ctx:Record<string,any>
// When set, the fake runtime emits this per-seq chip-temperature trace (K) so a
// test can replay the MMS fixture's identity, not just its time axis.
let fixtureChipTrace:number[]|null=null
let previousLocale:Locale
// Undefined simulates an older backend; [] explicitly advertises fixed-flow pumps.
let advertisedPumpControls:string[]|undefined

function App() {
  useSyncExternalStore(store.subscribe,store.revision)
  return <><LiquidExperimentPanel project={store.getProject()}/><LiquidRuntimeMonitor/></>
}
function response(value:unknown) {return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})}
function fakeSession(manifest:any):runtime.RuntimeSession {
  const cp=manifest.edges.filter((edge:any)=>edge.type==='cold_plate').map((edge:any)=>edge.id)
  const hx=manifest.edges.filter((edge:any)=>edge.type==='heat_exchanger').map((edge:any)=>edge.id)
  const pumps=manifest.edges.filter((edge:any)=>edge.type==='pump').map((edge:any)=>edge.id)
  const valves=manifest.edges.filter((edge:any)=>edge.type==='valve').map((edge:any)=>edge.id)
  const pumpValues=Object.fromEntries(pumps.map((id:string)=>[id,1]))
  const valveValues=Object.fromEntries(valves.map((id:string)=>[id,1]))
  const frame:runtime.RuntimeFrame={kind:'frame',run_id:'test-dae',seq:0,time_s:0,
    node_pressure_Pa:manifest.nodes.map(()=>150000),node_temperature_K:manifest.nodes.map(()=>300),
    edge_flow_kg_s:manifest.edges.map(()=>.1),edge_outlet_temp_K:manifest.edges.map(()=>305),
    chip_temp_K:cp.map(()=>fixtureChipTrace?fixtureChipTrace[0]!:330),
    primary_return_K:hx.map(()=>300),secondary_supply_K:hx.map(()=>310),heat_multiplier:1,
    pump_rpm_ratios:pumpValues,valve_openings:valveValues,
    primary_inlet_K:Object.fromEntries(hx.map((id:string)=>[id,290])),
    controls:{pump_rpm_ratios:pumpValues,valve_openings:valveValues,
      primary_inlet_K:Object.fromEntries(hx.map((id:string)=>[id,290]))},
    diagnostics:{heat_input_W:21000,primary_rejection_W:21000,storage_rate_W:0,energy_balance_W:1e-7}}
  return {run_id:'test-dae',status:'ready',config_hash:'hash-test-dae',frame,
    metadata:{solver:'differentiable_newton_dae',node_ids:manifest.nodes.map((node:any)=>node.id),edge_ids:manifest.edges.map((edge:any)=>edge.id),
      cp_edge_ids:cp,hx_edge_ids:hx,total_sample_count:manifest.time.N_t,dt_s:manifest.time.dt_s,
      object_bindings:manifest.object_bindings,pending_heat_multiplier:1,
      pump_edge_ids:pumps,valve_edge_ids:valves,
      ...(advertisedPumpControls===undefined?{}:{pump_rpm_control_edge_ids:advertisedPumpControls}),
      pending_pump_rpm_ratios:{},pending_valve_openings:{},pending_primary_inlet_K:{}}}
}
async function click(label:string) {
  const button=[...host.querySelectorAll('button')].find(node=>(node.getAttribute("aria-label")??node.textContent)===label)
  expect(button,`button ${label}`).toBeDefined()
  await act(async()=>button!.click())
}
function choose(label:string,value:string) {
  const select=host.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement
  expect(select,`select ${label}`).not.toBeNull()
  act(()=>{select.value=value;select.dispatchEvent(new Event('change',{bubbles:true}))})
}
function numberInput(label:string,value:number) {
  const input=host.querySelector(`input[aria-label="${label}"]`) as HTMLInputElement
  expect(input,`input ${label}`).not.toBeNull()
  const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!
  act(()=>{setter.call(input,String(value));flushSync(()=>input.dispatchEvent(new Event('input',{bubbles:true})))})
}

beforeEach(async()=>{
  previousLocale=getLocale();setLocale('zh-CN')
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
  runtime.resetRuntimeClient();current=null;frames=[];requests=[];textCalls.length=0;fixtureChipTrace=null;advertisedPumpControls=undefined
  const project=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/Shinaide_Liquid/Shinaide_liquid.cfdproj'),'utf8')) as CfdProject
  // Keep explicit v2 adoption/control coverage after the canonical case
  // becomes a saved Newton DAE entry.
  const legacy=readExperiment(project)
  delete legacy.engine;delete legacy.dae;delete legacy.parameterPack
  ;(project.runDefaults!.liquid as Record<string,unknown>).experiment=legacy
  store.loadProject(project,null)
  const saved=readExperiment(project)
  store.setRunConfig('liquid','experiment',{...saved,duration_s:10,dt_s:1,settings:{...saved.settings,loadStep:true,stepTime:5,loadFactor:1.4}})
  // This suite exercises time-marching controls. New adoption follows this
  // explicit top-bar choice, rather than silently creating an independent clock.
  setSimulationMode('transient','liquid')
  store.edit('testTimeline','10 s control fixture',draft=>{(draft as any).transient.durationS=10})
  ctx={measureText:(text:string)=>({width:text.length*6}),setTransform:vi.fn(),clearRect:vi.fn(),fillText:(text:string)=>textCalls.push(text),beginPath:vi.fn(),moveTo:vi.fn(),lineTo:vi.fn(),stroke:vi.fn(),fillRect:vi.fn(),setLineDash:vi.fn()}
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
  vi.spyOn(globalThis,'fetch').mockImplementation(async(input,init)=>{
    const url=String(input),body=init?.body?JSON.parse(String(init.body)):undefined
    requests.push({url,body})
    if(url==='/api/liquid/runs') {current=fakeSession(body.liquidrun);frames=[structuredClone(current.frame)];return response(current)}
    if(!current)throw new Error('No test session')
    if(url.includes('/heat-audit?'))return response({format:'hybrid-cold-plate-heat-audit/v1',
      project_uid:store.getProject()!.uid,liquid_run_id:current.run_id,
      accepted_prefix_complete:false,identity_verified:true,auditable:true,handoff_ready:false,
      audited_server_count:56,checked_server_samples:56,unchecked_server_samples:0,
      max_abs_closure_residual_W:5.9e-8,time_s:[0]})
    if(url.endsWith('/inputs')) {current={...current,metadata:{...current.metadata,
      pending_heat_multiplier:body.heat_multiplier??current.metadata.pending_heat_multiplier,
      pending_pump_rpm_ratios:{...current.metadata.pending_pump_rpm_ratios,...body.pump_rpm_ratios},
      pending_valve_openings:{...current.metadata.pending_valve_openings,...body.valve_openings},
      pending_primary_inlet_K:{...current.metadata.pending_primary_inlet_K,...body.primary_inlet_K}}};return response(current)}
    if(url.endsWith('/equilibrium'))return response({...current.frame,kind:'equilibrium',time_s:undefined,evaluated_at_s:current.frame.time_s,
      primary_return_K:current.metadata.hx_edge_ids!.map(()=>310),secondary_supply_K:current.metadata.hx_edge_ids!.map(()=>320),heat_multiplier:body?.heat_multiplier??1})
    if(url.endsWith('/step')) {
      const pumpValues={...current.frame.pump_rpm_ratios,...current.metadata.pending_pump_rpm_ratios}
      const valveValues={...current.frame.valve_openings,...current.metadata.pending_valve_openings}
      const inletValues={...current.frame.primary_inlet_K,...current.metadata.pending_primary_inlet_K}
      const nextSeq=current.frame.seq+1
      const next:runtime.RuntimeFrame={...current.frame,seq:nextSeq,time_s:current.frame.time_s+1,
        heat_multiplier:current.metadata.pending_heat_multiplier,primary_return_K:current.metadata.hx_edge_ids!.map(()=>300.5),
        secondary_supply_K:current.metadata.hx_edge_ids!.map(()=>310.5),
        chip_temp_K:current.frame.chip_temp_K.map(()=>fixtureChipTrace?fixtureChipTrace[nextSeq]!:330),
        pump_rpm_ratios:pumpValues,valve_openings:valveValues,primary_inlet_K:inletValues,
        controls:{pump_rpm_ratios:pumpValues,valve_openings:valveValues,primary_inlet_K:inletValues},
        diagnostics:{heat_input_W:25200,primary_rejection_W:21000,storage_rate_W:4200,energy_balance_W:2e-7}}
      frames.push(next);current={...current,status:'paused',frame:next,metadata:{...current.metadata,pending_pump_rpm_ratios:{},pending_valve_openings:{},pending_primary_inlet_K:{}}};return response(current)
    }
    if(url.includes('/frames'))return response({status:current.status,frames})
    throw new Error('Unexpected route '+url)
  })
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
  await act(async()=>root.render(<App/>))
})
afterEach(()=>{act(()=>root.unmount());host.remove();runtime.resetRuntimeClient();vi.restoreAllMocks();setLocale(previousLocale)})

it('exposes control mode and playback speed by their visible labels in both locales',()=>{
  for(const locale of ['zh-CN','en-US'] as const) {
    act(()=>setLocale(locale))
    for(const [id,key] of [['lce-control-mode','lce.control_mode'],['lce-playback-speed','lce.playback_speed']] as const) {
      const select=host.querySelector(`select[aria-label="${t(key)}"]`) as HTMLSelectElement
      expect(select).not.toBeNull()
      expect(select.id).toBe(id)
      expect(select.labels?.[0]?.htmlFor).toBe(id)
    }
    choose(t('lce.playback_speed'),'fast')
    expect((host.querySelector('#lce-playback-speed') as HTMLSelectElement).value).toBe('fast')
  }
})

it('explicitly adopts DAE mock into the actual project and transparently cancels events when switching to steady',async()=>{
  expect(readExperiment(store.getProject()).dae).toBeUndefined()
  await click('采用 DAE mock 参数')
  const saved=readExperiment(store.getProject())
  expect(saved.dae?.primary_circuits[0]).toMatchObject({edge_id:'m_30_hx',flow_kg_s:1,inlet_K:293.15,primary_volume_m3:.02,secondary_volume_m3:.02,source:'mock'})
  expect(saved.dae?.execution).toEqual({backend:'pytorch',device:'cpu',dtype:'float64',max_threads:2})
  expect(host.textContent).toContain('一次流量')
  expect(host.textContent).toContain('CPU')
  act(()=>{setSimulationMode('steady','liquid')})  // the top bar in liquid view (E8b)
  expect(readExperiment(store.getProject()).settings.loadStep).toBe(false)
  expect(readExperiment(store.getProject()).dae?.analysis.mode).toBe('steady')
  expect(host.querySelector('#lce-analysis-mode')!.textContent).toContain('稳态')
  const checkbox=[...host.querySelectorAll('input[type=checkbox]')].find(input=>input.parentElement?.textContent?.includes('添加冷板负载阶跃')) as HTMLInputElement
  expect(checkbox.disabled).toBe(true)
  const reopened=JSON.parse(serializeProjectState(store.getProject()!))
  expect(readExperiment(reopened).dae?.analysis.mode).toBe('steady')
  expect(requests).toHaveLength(0) // adopting/saving settings never starts a solve
})

it('edits an existing DAE project CPU budget through save, reopen and v3 export',async()=>{
  const project=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/Shinaide_Liquid/Shinaide_dae_cpu.cfdproj'),'utf8')) as CfdProject
  await act(async()=>store.loadProject(project,null))
  expect(readExperiment(store.getProject()).dae?.execution?.max_threads).toBe(1)
  choose('DAE CPU 线程数','4')
  expect(readExperiment(store.getProject()).dae?.execution?.max_threads).toBe(4)
  const reopened=JSON.parse(serializeProjectState(store.getProject()!)) as CfdProject
  expect(readExperiment(reopened).dae?.execution?.max_threads).toBe(4)
  const compiled=compileSavedExperiment(reopened).liquidrun
  expect(compiled.execution.max_threads).toBe(4)
  if(process.env.SHINAIDE_DAE_CPU_THREADS_OUTPUT)
    writeFileSync(process.env.SHINAIDE_DAE_CPU_THREADS_OUTPUT,JSON.stringify(compiled,null,2)+'\n')
  await click('准备实验')
  expect(requests.find(request=>request.url==='/api/liquid/runs')?.body.liquidrun.execution.max_threads).toBe(4)
  expect(host.querySelector('select[aria-label="DAE CPU 线程数"]')?.matches(':disabled')).toBe(true)
})

it('queues live power for the next accepted step and keeps equilibrium outside transient history',async()=>{
  await click('采用 DAE mock 参数');await click('准备实验')
  expect(requests[0]!.body.liquidrun.schemaVersion).toBe(4)
  const before=structuredClone(runtime.runtimeState().frames)
  await click('120%（+20%）')
  expect(requests.find(request=>request.url.endsWith('/inputs'))?.body).toEqual({heat_multiplier:1.2})
  expect(runtime.runtimeState().frames).toEqual(before)
  expect(host.textContent).toContain('下一接受步')
  await click('独立稳态查询')
  expect(requests.find(request=>request.url.endsWith('/equilibrium'))?.body).toEqual({heat_multiplier:1.2})
  expect(runtime.runtimeState().frames).toEqual(before)
  expect(host.querySelector('#liquid-dae-equilibrium')?.textContent).toContain('36.85')
  await click('单步')
  expect(runtime.runtimeState().frames.map(frame=>frame.heat_multiplier)).toEqual([1,1.2])
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('27.35')
  expect(host.querySelector('#liquid-dae-equilibrium')?.textContent).toContain('独立稳态')
})

it('queues pump and positive-valve settings without rewriting accepted frames',async()=>{
  await click('采用 DAE mock 参数');await click('准备实验')
  const manifest=compileSavedExperiment(store.getProject()!).liquidrun
  const pump=manifest.edges.find(edge=>edge.type==='pump')
  const valve=manifest.edges.find(edge=>edge.type==='valve')
  expect(pump).toBeDefined();expect(valve).toBeDefined()
  const before=structuredClone(runtime.runtimeState().frames)
  await act(async()=>{await runtime.runtimeControlInputs({pump_rpm_ratios:{[pump!.id]:.8},valve_openings:{[valve!.id]:.7}})})
  expect(requests.find(request=>request.url.endsWith('/inputs'))?.body).toEqual({
    pump_rpm_ratios:{[pump!.id]:.8},valve_openings:{[valve!.id]:.7},
  })
  expect(runtime.runtimeState().frames).toEqual(before)
  await act(async()=>{await expect(runtime.runtimeControlInputs({valve_openings:{[valve!.id]:0}})).rejects.toThrow(/opening|positive/i)})
})

it('offers pump and valve controls for the next accepted step in the real DAE panel',async()=>{
  await click('采用 DAE mock 参数');await click('准备实验')
  const ids=runtime.runtimeState().session!.metadata
  expect(ids.pump_edge_ids?.length).toBeGreaterThan(0)
  expect(ids.valve_edge_ids?.length).toBeGreaterThan(0)
  const before=structuredClone(runtime.runtimeState().frames)
  numberInput('泵相对转速',.8)
  await click('排队泵速')
  expect(requests.filter(request=>request.url.endsWith('/inputs')).at(-1)?.body).toEqual({
    pump_rpm_ratios:{[ids.pump_edge_ids![0]!]:.8},
  })
  numberInput('阀门开度',.7)
  await click('排队阀位')
  expect(requests.filter(request=>request.url.endsWith('/inputs')).at(-1)?.body).toEqual({
    valve_openings:{[ids.valve_edge_ids![0]!]:.7},
  })
  expect(runtime.runtimeState().frames).toEqual(before)
  expect(host.textContent).toContain('下一步生效')
  await click('单步')
  expect(runtime.runtimeState().session?.frame.pump_rpm_ratios?.[ids.pump_edge_ids![0]!]).toBe(.8)
  expect(runtime.runtimeState().session?.frame.valve_openings?.[ids.valve_edge_ids![0]!]).toBe(.7)
  expect(host.textContent).toContain('当前已接受')
  expect(host.textContent).not.toContain('控制输出') // DAE maps are not legacy PID outputs
})

it('honours an explicit empty RPM actuator list even when fixed-flow pumps exist',async()=>{
  advertisedPumpControls=[]
  await click('采用 DAE mock 参数');await click('准备实验')
  const metadata=runtime.runtimeState().session!.metadata
  expect(metadata.pump_edge_ids?.length).toBeGreaterThan(0)
  expect(metadata.pump_rpm_control_edge_ids).toEqual([])
  expect(host.querySelector('input[aria-label="泵相对转速"]')).toBeNull()
  expect(host.querySelector('select[aria-label="泵"]')).toBeNull()
  expect([...host.querySelectorAll('button')].some(button=>button.textContent==='排队泵速')).toBe(false)
  expect(host.querySelector('input[aria-label="阀门开度"]')).not.toBeNull()
  expect(requests.some(request=>request.url.endsWith('/inputs'))).toBe(false)
})

it('offers only advertised curve pumps and sends their RPM command',async()=>{
  await click('采用 DAE mock 参数')
  const pump=compileSavedExperiment(store.getProject()!).liquidrun.edges.find(edge=>edge.type==='pump')!
  advertisedPumpControls=[pump.id]
  await click('准备实验')
  expect(runtime.runtimeState().session!.metadata.pump_rpm_control_edge_ids).toEqual([pump.id])
  const select=host.querySelector('select[aria-label="泵"]') as HTMLSelectElement
  expect([...select.options].map(option=>option.value)).toEqual([pump.id])
  numberInput('泵相对转速',.85)
  await click('排队泵速')
  expect(requests.filter(request=>request.url.endsWith('/inputs')).at(-1)?.body).toEqual({pump_rpm_ratios:{[pump.id]:.85}})
})

it('hides stale queued hints when accepted frames catch up before the metadata snapshot',async()=>{
  await click('采用 DAE mock 参数');await click('准备实验')
  const metadata=runtime.runtimeState().session!.metadata
  const pump=metadata.pump_edge_ids![0]!,valve=metadata.valve_edge_ids![0]!,hx=metadata.hx_edge_ids![0]!
  await click('120%（+20%）')
  numberInput('泵相对转速',.95);await click('排队泵速')
  numberInput('阀门开度',.9);await click('排队阀位')
  numberInput('供水温度（K）',294.15);await click('排队供水温度')
  expect(host.querySelector('#lce-pending-heat')?.textContent).toContain('120%')
  for(const [id,value] of [[pump,.95],[valve,.9],[hx,294.15]]) {
    expect(host.textContent).toContain(`下一步生效：${id} = ${value}`)
  }
  const queued=structuredClone(runtime.runtimeState().session!.metadata)
  const accepted:runtime.RuntimeFrame={...current!.frame,seq:1,time_s:1,heat_multiplier:1.2,
    pump_rpm_ratios:{[pump]:.95},valve_openings:{[valve]:.9},primary_inlet_K:{[hx]:294.15},
    controls:{pump_rpm_ratios:{[pump]:.95},valve_openings:{[valve]:.9},primary_inlet_K:{[hx]:294.15}}}
  frames.push(accepted)
  current={...current!,status:'paused',frame:accepted}
  // The frame endpoint updates samples/status only, matching a live stream
  // arriving while the last command acknowledgment's metadata is still shown.
  await act(async()=>{await runtime.refreshRuntimeFrames()})
  expect(runtime.runtimeState().session!.metadata).toEqual(queued)
  expect(host.querySelector('#lce-pending-heat')).toBeNull()
  expect(host.querySelector('#lce-accepted-heat')?.textContent).toContain('120%')
  for(const [id,value] of [[pump,.95],[valve,.9],[hx,294.15]]) {
    expect(host.textContent).not.toContain(`下一步生效：${id} = ${value}`)
    expect(host.textContent).toContain(`当前已接受：${id} = ${value}`)
  }
  // A later distinct target must still appear while its accepted value lags.
  numberInput('泵相对转速',.9);await click('排队泵速')
  expect(host.textContent).toContain(`下一步生效：${pump} = 0.9`)
  expect(host.textContent).not.toContain(`下一步生效：${valve} = 0.9`)
  act(()=>setLocale('en-US'))
  expect(host.textContent).toContain(`Effective next step: ${pump} = 0.9`)
  expect(host.textContent).toContain('Accepted load multiplier: 120%')
})

it('shows the read-only accepted liquid heat ledger without claiming air coupling',async()=>{
  await click('采用 DAE mock 参数');await click('准备实验')
  const before=structuredClone(runtime.runtimeState().frames)
  await click('核查液冷热量守恒')
  expect(requests.find(request=>request.url.includes('/heat-audit?project_uid=proj_Shinaide'))).toBeDefined()
  expect(runtime.runtimeState().frames).toEqual(before)
  expect(host.textContent).toContain('已核查 56 台服务器')
  expect(host.textContent).toContain('未计算空气侧余热')
})

it('renders each HX and flat DAE heat diagnostics through the actual time-series chart',async()=>{
  await click('采用 DAE mock 参数');await click('准备实验');await click('120%（+20%）');await click('单步')
  expect(host.querySelector('[data-runtime-hx="m_30_hx"]')).not.toBeNull()
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('一次回水')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('二次供水')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('热输入')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('一次侧带走')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('储能率')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('2.00e-7')
})

async function openSavedOwnClockDAE() {
  await act(async()=>{
    setSimulationMode('steady','liquid')
    const saved=adoptMockDAEExperiment(store.getProject()!,{analysis:{mode:'transient',initialization:'steady'}})
    store.setRunConfig('liquid','experiment',saved)
  })
}

it('compiles the creation-phase supply schedule into the .liquidrun inlet_K while the saved binding stays scalar',async()=>{
  // without a DAE config the editor must not render at all: the v2 compile
  // path cannot express an inlet_K series and would silently drop it
  expect(host.querySelector('select[aria-label="供水温度方案"]')).toBeNull()
  await openSavedOwnClockDAE()
  expect(host.querySelector('select[aria-label="供水温度方案"]')).not.toBeNull()
  // fixed is the default: no schedule parameter fields are rendered
  expect(host.querySelector('input[aria-label="变化时刻（s）"]')).toBeNull()
  choose('供水温度方案','step')
  expect(host.querySelector('input[aria-label="变化时刻（s）"]')).not.toBeNull()
  expect(readExperiment(store.getProject()).settings.supplySchedule).toMatchObject({mode:'step',from:290,to:295,stepTime:5})
  numberInput('目标温度（K）',296)
  expect(readExperiment(store.getProject()).settings.supplySchedule).toMatchObject({mode:'step',from:290,to:296,stepTime:5})
  await click('准备实验')
  const liquidrun=requests.find(request=>request.url==='/api/liquid/runs')!.body.liquidrun
  expect(liquidrun.schemaVersion).toBe(3)
  // beforeEach pins duration 10 s / dt 1 s; expansion semantics must match the
  // acceptance script exactly (sample i carries the value active on (t_{i-1},t_i])
  const schedule=readExperiment(store.getProject()).settings.supplySchedule
  expect(liquidrun.primary_circuits[0].inlet_K).toEqual(
    expandSupplySchedule(schedule,293.15,liquidrun.time.N_t,liquidrun.time.dt_s))
  expect(Array.isArray(liquidrun.primary_circuits[0].inlet_K)).toBe(true)
  // the project-owned binding keeps its scalar value (the hint promises this)
  expect(readExperiment(store.getProject()).dae!.primary_circuits[0]!.inlet_K).toBe(293.15)
  // switching back to fixed removes the fields again
  choose('供水温度方案','fixed')
  expect(host.querySelector('input[aria-label="变化时刻（s）"]')).toBeNull()
  expect(readExperiment(store.getProject()).settings.supplySchedule).toEqual({mode:'fixed'})
})

it('disables the schedule editor in steady mode and compiles the scalar binding untouched',async()=>{
  await openSavedOwnClockDAE()
  choose('供水温度方案','step')
  act(()=>{setSimulationMode('steady','liquid')})
  const select=host.querySelector('select[aria-label="供水温度方案"]') as HTMLSelectElement
  expect(select.disabled).toBe(true)
  await click('准备实验')
  const liquidrun=requests.find(request=>request.url==='/api/liquid/runs')!.body.liquidrun
  expect(liquidrun.time.N_t).toBe(1)
  // steady has no time axis: the schedule is ignored, the scalar binding stays
  expect(liquidrun.primary_circuits[0].inlet_K).toBe(293.15)
})

it('queues online supply temperature for the next accepted step like pump and valve',async()=>{
  await click('采用 DAE mock 参数');await click('准备实验')
  const ids=runtime.runtimeState().session!.metadata
  expect(ids.hx_edge_ids).toHaveLength(1)
  const hx=ids.hx_edge_ids![0]!
  const before=structuredClone(runtime.runtimeState().frames)
  // frame 0 already carries primary_inlet_K=290, so assert VALUES, not just
  // the note prefix — a bare '当前已接受' would pass from session start
  expect(host.textContent).toContain(`当前已接受：${hx} = 290`)
  numberInput('供水温度（K）',296)
  await click('排队供水温度')
  expect(requests.filter(request=>request.url.endsWith('/inputs')).at(-1)?.body).toEqual({
    primary_inlet_K:{[hx]:296},
  })
  // queueing never rewrites accepted history
  expect(runtime.runtimeState().frames).toEqual(before)
  expect(host.textContent).toContain(`下一步生效：${hx} = 296`)
  await click('单步')
  expect(runtime.runtimeState().session?.frame.primary_inlet_K?.[hx]).toBe(296)
  expect(host.textContent).toContain(`当前已接受：${hx} = 296`)
})

it('overlays the sha-bound MMS analytic reference only on a matching fixture replay',async()=>{
  await click('采用 DAE mock 参数')
  // stretch the run to the fixture horizon: dt=1 s over 60 s → N_t=61 (t=0..60)
  act(()=>{
    store.setRunConfig('liquid','experiment',{...readExperiment(store.getProject()),duration_s:60,dt_s:1})
    store.edit('testTimeline','60 s MMS identity replay',draft=>{(draft as any).transient.durationS=60})
  })
  // replay the fixture's IDENTITY too: the honesty gate checks the solid trace
  // against the receipt's DAE curve, not just the time axis
  const daeSolid=mmsAnalyticCurves.load.dae_solid_K
  fixtureChipTrace=[daeSolid[0]!,...daeSolid]
  await click('准备实验')
  expect(runtime.runtimeState().session!.metadata.total_sample_count).toBe(61)
  // one step so every chart has time.length>=2 and actually draws — with a
  // single frame the 'no data' early return would make the dash assertions
  // below vacuously true (zero setLineDash calls)
  await click('单步')
  const box=[...host.querySelectorAll('input[type=checkbox]')]
    .find(input=>input.getAttribute('aria-label')==='MMS 解析参考') as HTMLInputElement
  expect(box).toBeDefined()
  // two frames at t=0..1: nothing is comparable, charts draw, nothing dashed
  act(()=>box.click())
  expect(host.textContent).toContain('解析参考与当前运行不可比')
  expect(ctx.setLineDash.mock.calls.length).toBeGreaterThan(0)
  expect(ctx.setLineDash.mock.calls.every((call:any[])=>(call[0] as number[]).length===0)).toBe(true)
  act(()=>box.click())
  // replay the rest of the fixture horizon; frame times become 0..60 s
  for(let step=0;step<59;step++)await click('单步')
  expect(runtime.runtimeState().frames).toHaveLength(61)
  ctx.setLineDash.mockClear();textCalls.length=0
  act(()=>box.click())
  expect(host.textContent).toContain('闭式解析解')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('解析 solid 温度')
  // the HX-chart overlays exist only for the single-HX fixture the receipt covers
  expect(runtime.runtimeState().session!.metadata.hx_edge_ids).toHaveLength(1)
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('解析一次回水')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('解析二次供水')
  expect(ctx.setLineDash.mock.calls.some((call:any[])=>JSON.stringify(call[0])==='[6,4]')).toBe(true)
  // scenario identity is enforced too: this run replayed the LOAD step, so the
  // supply-step reference must refuse to compare against it (the solid traces
  // diverge by ~0.5 K, far above the 0.02 K tolerance)…
  choose('MMS 解析参考','supply')
  expect(host.textContent).toContain('解析参考与当前运行不可比')
  // …and switching back to the matching scenario restores the overlay
  choose('MMS 解析参考','load')
  expect(host.textContent).toContain('闭式解析解')
  expect(ctx.setLineDash.mock.calls.some((call:any[])=>JSON.stringify(call[0])==='[6,4]')).toBe(true)
})

it('switches existing notices, controls and plotted legends with locale without changing saved physics or accepted history',async()=>{
  await click('采用 DAE mock 参数');await click('准备实验');await click('单步')
  const projectBefore=serializeProjectState(store.getProject()!)
  const framesBefore=structuredClone(runtime.runtimeState().frames)
  const requestCount=requests.length
  expect(host.textContent).toContain('已将显式 mock 一次侧参数加入工程')
  textCalls.length=0
  act(()=>setLocale('en-US'))
  expect(host.textContent).toContain('Explicit mock primary-side parameters have been added')
  expect(host.querySelector('[aria-label="DAE analysis mode"]')).not.toBeNull()
  expect(host.textContent).toContain('Independent steady-state query')
  expect(host.textContent).toContain('Primary return 27.35 °C')
  expect(host.textContent).toContain('Paused')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('Heat input')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('Primary heat rejection')
  expect(host.querySelector('#liquid-runtime-monitor')?.textContent).toContain('Energy-storage rate')
  act(()=>setLocale('zh-CN'))
  expect(host.textContent).toContain('已将显式 mock 一次侧参数加入工程')
  expect(host.querySelector('[aria-label="DAE 分析模式"]')).not.toBeNull()
  expect(serializeProjectState(store.getProject()!)).toBe(projectBefore)
  expect(runtime.runtimeState().frames).toEqual(framesBefore)
  expect(requests).toHaveLength(requestCount)
})
