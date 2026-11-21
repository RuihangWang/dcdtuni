import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LiquidRuntimeMonitor } from '../src/components/LiquidRuntimeMonitor'
import { LiquidExperimentPanel } from '../src/components/LiquidExperimentPanel'
vi.mock('../src/components/LiquidSolverPanel',()=>({LiquidSolverPanel:()=>null}))
import * as runtime from '../src/liquid/liquidRuntimeClient'
import { getLocale, setLocale, type Locale } from '../src/i18n'

// Controlled HTTP responses, actual runtime store and mounted monitor. No
// runtime module mock and no physical solver/native/browser acceptance claim.
function frame(id:string,seq=0):runtime.RuntimeFrame {
  return {kind:'frame',run_id:id,seq,time_s:seq*.25,node_pressure_Pa:[100000],
    node_temperature_K:[300],edge_flow_kg_s:[.1,1],edge_outlet_temp_K:[305,300],
    chip_temp_K:[310],primary_return_K:[301],secondary_supply_K:[302],
    controls:{pump_rpm_ratios:{},valve_openings:{},primary_inlet_K:{}},diagnostics:{}}
}
function session(id:string,status:runtime.RuntimeStatus='running',error?:unknown) {
  return {run_id:id,status,config_hash:'controlled-'+id,metadata:{
    solver:'differentiable_newton_dae',node_ids:['n'],edge_ids:['cp','hx'],
    cp_edge_ids:['cp'],hx_edge_ids:['hx'],total_sample_count:9,dt_s:.25,
    object_bindings:{}},frame:frame(id),error}
}
const failure={type:'LiquidRuntimeError',message:'Hydraulic solve failed: controlled nonconvergence',last_successful_seq:1}
const response=(value:unknown)=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})
let host:HTMLDivElement,root:Root,previousLocale:Locale
const alert=()=>host.querySelector('[role="alert"]')
const status=()=>host.querySelector('[data-runtime-status]')?.getAttribute('data-runtime-status')
async function create(id:string,statusValue:runtime.RuntimeStatus='running',error?:unknown) {
  vi.mocked(fetch).mockResolvedValueOnce(response(session(id,statusValue,error)))
  await act(async()=>{await runtime.createRuntimeSession({},[],1)})
}
async function poll(value:unknown) {
  vi.mocked(fetch).mockResolvedValueOnce(response(value))
  await act(async()=>{await runtime.refreshRuntimeFrames()})
}
beforeEach(()=>{
  runtime.resetRuntimeClient();previousLocale=getLocale();setLocale('zh-CN')
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
  vi.spyOn(globalThis,'fetch')
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
  act(()=>root.render(<LiquidRuntimeMonitor/>))
})
afterEach(()=>{act(()=>{root.unmount();runtime.resetRuntimeClient()});host.remove();setLocale(previousLocale);vi.restoreAllMocks();vi.unstubAllGlobals()})

it('shows the real structured failed-step reason from a successful current frames response',async()=>{
  await create('A')
  await poll({run_id:'A',status:'failed',frames:[frame('A',1)],error:failure})
  expect(runtime.runtimeState().error).toBeNull()
  expect(runtime.runtimeState().session?.error).toEqual(failure)
  expect(status()).toBe('failed')
  expect(alert()?.textContent).toBe(failure.message)
  expect(alert()?.getAttribute('data-runtime-error-kind')).toBe('solver')
  expect(runtime.runtimeState().frames.map(f=>[f.run_id,f.seq,f.time_s])).toEqual([['A',0,0],['A',1,.25]])
})
it('shows a failed session returned by initial creation without rendering its error object',async()=>{
  await create('A','failed',{...failure,last_successful_seq:0})
  expect(alert()?.textContent).toBe(failure.message)
  expect(host.textContent).not.toContain('[object Object]')
})
it('keeps request failure ahead of solver failure and restores solver reason after reconnect',async()=>{
  await create('A');await poll({status:'failed',frames:[frame('A',1)],error:failure})
  vi.mocked(fetch).mockRejectedValueOnce(new Error('Connection interrupted'))
  await act(async()=>{await expect(runtime.refreshRuntimeFrames()).rejects.toThrow('Connection interrupted')})
  expect(status()).toBe('failed')
  expect(alert()?.textContent).toBe('Connection interrupted')
  const requestKind=alert()?.getAttribute('data-runtime-error-kind')
  expect(host.querySelector('[data-runtime-connection]')?.getAttribute('data-runtime-connection')).toBe('reconnecting')
  await poll({status:'failed',frames:[],error:failure})
  expect(alert()?.textContent).toBe(failure.message)
  expect(alert()?.getAttribute('data-runtime-error-kind')).toBe('solver')
  expect(runtime.runtimeState().connection).toBe('connected')
  expect(requestKind).toBe('request')
})
it('does not attach a late old-run failed poll or error to a newer run',async()=>{
  await create('A')
  let finish!:(value:Response)=>void
  vi.mocked(fetch).mockReturnValueOnce(new Promise<Response>(resolve=>{finish=resolve}))
  const old=runtime.refreshRuntimeFrames()
  await create('B')
  await act(async()=>{finish(response({run_id:'A',status:'failed',frames:[frame('A',1)],error:failure}));await old})
  expect(runtime.runtimeState().session?.run_id).toBe('B')
  expect(status()).toBe('running');expect(alert()).toBeNull()
  expect(host.textContent).not.toContain(failure.message)
})
it('does not show obsolete session errors in a completed, cancelled or healthy new session',async()=>{
  await create('A','failed',failure);expect(alert()?.textContent).toBe(failure.message)
  for(const next of ['completed','cancelled','running'] as const) {
    await create('new-'+next,next,failure)
    expect(status()).toBe(next);expect(alert()).toBeNull()
  }
})
it('clears prior-run failure when a new run is accepted or runtime is reset',async()=>{
  await create('A','failed',failure)
  await create('B');expect(alert()).toBeNull();expect(runtime.runtimeState().session?.run_id).toBe('B')
  act(()=>runtime.resetRuntimeClient());expect(alert()).toBeNull();expect(status()).toBe('empty')
})
it('uses a truthful failed fallback for missing, empty and unknown error shapes',async()=>{
  for(const [index,error] of [null,{}, {message:{secret:'not a display message'}}, {message:''}].entries()) {
    await create('unknown-'+index,'failed',error)
    expect(alert()?.textContent).toBe('失败');expect(status()).toBe('failed')
    expect(host.textContent).not.toContain('secret');expect(host.textContent).not.toContain('[object Object]')
  }
})
it('preserves a legacy string reason as literal text, not HTML',async()=>{
  await create('legacy','failed','<img src=x onerror=alert(1)>')
  expect(alert()?.textContent).toBe('<img src=x onerror=alert(1)>')
  expect(alert()?.querySelector('img')).toBeNull()
})
it('names the reference scenario selector accurately in both locales',async()=>{
  await create('A')
  act(()=>{(host.querySelector('input[type="checkbox"]') as HTMLInputElement).click()})
  expect(host.querySelector('select[aria-label="参考场景"]')).not.toBeNull()
  act(()=>setLocale('en-US'))
  expect(host.querySelector('select[aria-label="Reference scenario"]')).not.toBeNull()
  expect(host.querySelector('input[aria-label="MMS analytic reference"]')).not.toBeNull()
})

it('keeps the current solve card safe for the native structured failure and then clears it on new run',async()=>{
  act(()=>root.render(<LiquidExperimentPanel project={null}/>))
  await create('A','failed',{...failure,last_successful_seq:0})
  expect(alert()?.textContent).toBe(failure.message)
  await create('B')
  expect(alert()).toBeNull();expect(host.textContent).not.toContain(failure.message)
})
it('does not render obsolete solve-card session errors when the accepted run is completed',async()=>{
  act(()=>root.render(<LiquidExperimentPanel project={null}/>))
  await create('completed','completed',failure)
  expect(alert()).toBeNull();expect(host.textContent).not.toContain(failure.message)
})
