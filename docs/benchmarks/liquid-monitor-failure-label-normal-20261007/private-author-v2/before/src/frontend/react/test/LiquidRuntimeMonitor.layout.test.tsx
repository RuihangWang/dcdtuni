import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LiquidRuntimeMonitor } from '../src/components/LiquidRuntimeMonitor'
import { getLocale, setLocale, type Locale } from '../src/i18n'
import { leftTabLabelKey } from '../src/ui/leftTabs'
import type { RuntimeClientState, RuntimeFrame } from '../src/liquid/liquidRuntimeClient'

const fixture=vi.hoisted(()=>({state:null as unknown as RuntimeClientState}))
vi.mock('../src/liquid/liquidRuntimeClient',()=>({
  runtimeState:()=>fixture.state,
  subscribeRuntime:()=>()=>{},
}))
let host:HTMLDivElement,root:Root,previousLocale:Locale
function sample(seq=1):RuntimeFrame {
  return {kind:'frame',run_id:'layout-proof',seq,time_s:seq*.25,
    node_pressure_Pa:[100000],node_temperature_K:[300],
    edge_flow_kg_s:[.125,.25,1],edge_outlet_temp_K:[301.15,304.15,300],
    chip_temp_K:[303.15,313.15],primary_return_K:[300.5],secondary_supply_K:[310.5],
    controls:{pump_rpm_ratios:{},valve_openings:{},primary_inlet_K:{}},
    diagnostics:{heat_input_W:21000,primary_rejection_W:20000,storage_rate_W:1000,energy_balance_W:1e-7}}
}
function state():RuntimeClientState {
  const frame=sample()
  return {session:{run_id:'layout-proof',status:'paused',config_hash:'layout-only',
    frame,metadata:{solver:'differentiable_newton_dae',node_ids:['n'],
    edge_ids:['cp-a','cp-b','hx'],cp_edge_ids:['cp-a','cp-b'],hx_edge_ids:['hx'],
    total_sample_count:33,dt_s:.25,object_bindings:{
      a:{label:'Rack A / CPU',kind:'server',node_ids:[],edge_ids:['cp-a']},
      b:{label:'Rack B / GPU',kind:'server',node_ids:[],edge_ids:['cp-b']},
      h:{label:'CDU A',kind:'cdu',node_ids:[],edge_ids:['hx']}}}},
    frames:[sample(0),frame],projectRevision:4,busy:false,error:null,connection:'connected'}
}
function render() {act(()=>root.render(<LiquidRuntimeMonitor/>))}
function display(id:string) {return host.querySelector('[data-runtime-value="'+id+'"]')}
beforeEach(()=>{
  previousLocale=getLocale();setLocale('zh-CN')
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
  fixture.state=state()
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(()=>{act(()=>root.unmount());host.remove();setLocale(previousLocale);vi.restoreAllMocks();vi.unstubAllGlobals()})

it('restores the same solver label in liquid and air without changing gates',()=>{
  expect(leftTabLabelKey('solver','liquid')).toBe('nav.solver')
  expect(leftTabLabelKey('solver','air')).toBe('nav.solver')
  const source=readFileSync(resolve(process.cwd(),'src/App.tsx'),'utf8')
  expect(source).toContain('const compactKey = '+String.fromCharCode(96)+'nav.compact.$'+'{tab.id}'+String.fromCharCode(96)+' as TranslationKey')
})
it('mounts one linked monitor with no legacy navigation or legacy panel',()=>{
  render()
  expect(host.textContent).toContain('联动监视')
  expect([...host.querySelectorAll('button')].some(b=>b.textContent==='旧版监视')).toBe(false)
  expect(host.querySelector('.subtab-bar')).toBeNull()
  expect(host.querySelector('#liquid-runtime-monitor')?.classList.contains('liquid-monitor')).toBe(true)
})
it('separates status, subsecond simulation time, current frame and accepted count',()=>{
  render()
  expect(host.querySelector('[data-runtime-status]')?.textContent).toContain('暂停')
  expect(display('time')?.textContent).toBe('0.25 s')
  expect(display('time')?.getAttribute('data-time-s')).toBe('0.25')
  expect(display('frame')?.textContent).toBe('2 / 33')
  expect(display('frame')?.getAttribute('data-frame-seq')).toBe('1')
  expect(host.querySelector('[data-runtime-samples]')?.textContent).toContain('2')
})
it('keeps status and reconnecting distinct and shows a transport error accessibly',()=>{
  fixture.state={...state(),connection:'reconnecting',error:'Connection interrupted'}
  render()
  expect(host.querySelector('[data-runtime-status]')?.textContent).toContain('暂停')
  expect(host.querySelector('[data-runtime-connection]')?.textContent).toContain('正在重连')
  expect(host.querySelector('[role="alert"]')?.textContent).toBe('Connection interrupted')
})
it('reads current solid, outlet and mass flow by actual edge ids and labels',()=>{
  render()
  expect(display('solid')?.textContent).toBe('30.00 °C')
  expect(display('outlet')?.textContent).toBe('28.00 °C')
  expect(display('flow')?.textContent).toBe('0.1250 kg/s')
  const select=host.querySelector('select[aria-label="冷板"]') as HTMLSelectElement
  expect(select?.options[0]?.textContent).toContain('Rack A / CPU')
  expect(select?.options[0]?.value).toBe('cp-a')
  act(()=>{select.value='cp-b';select.dispatchEvent(new Event('change',{bubbles:true}))})
  expect(display('solid')?.textContent).toBe('40.00 °C')
  expect(display('outlet')?.textContent).toBe('31.00 °C')
  expect(display('flow')?.textContent).toBe('0.2500 kg/s')
})
it('groups exchanger and energy values without changing their sampled physical values',()=>{
  const original=JSON.stringify(fixture.state)
  render()
  const hx=host.querySelector('[data-runtime-hx="hx"]')
  expect(hx?.textContent).toContain('CDU A')
  expect(hx?.textContent).toContain('27.35 °C')
  expect(hx?.textContent).toContain('37.35 °C')
  expect(display('heat-input')?.textContent).toBe('21.00 kW')
  expect(display('heat-rejection')?.textContent).toBe('20.00 kW')
  expect(display('heat-storage')?.textContent).toBe('1.00 kW')
  expect(host.textContent).toContain('热平衡残差 1.00e-7 W')
  expect(JSON.stringify(fixture.state)).toBe(original)
})
it('shows unavailable readings as missing rather than numerical NaN or false zero',()=>{
  const missing=state()
  missing.session!.frame={...missing.session!.frame,diagnostics:{},chip_temp_K:[NaN,313.15]}
  fixture.state=missing;render()
  expect(display('solid')?.textContent).toBe('—')
  expect(display('heat-input')?.textContent).toBe('—')
  expect(host.textContent).not.toContain('NaN')
})
it('renders an empty state and keeps the linked monitor title visible',()=>{
  fixture.state={...state(),session:null,frames:[],connection:'idle'};render()
  expect(host.textContent).toContain('联动监视')
  expect(host.querySelector('[data-runtime-empty]')).not.toBeNull()
  expect(host.querySelector('[data-runtime-status]')?.textContent).toContain('未准备')
  expect(host.querySelector('select')).toBeNull()
})
it('renders the hierarchy and identical data in both supported locales',()=>{
  for(const [locale,status,time,frame,live,curves] of [
    ['zh-CN','暂停','仿真时间','当前帧','实时数值','运行曲线'],
    ['en-US','Paused','Simulation time','Current frame','Live values','Run history'],
  ] as const) {
    act(()=>setLocale(locale));render()
    expect(host.querySelector('[data-runtime-status]')?.textContent).toContain(status)
    for(const label of [time,frame,live,curves])expect(host.textContent).toContain(label)
    expect(display('time')?.textContent).toBe('0.25 s')
    expect(display('solid')?.textContent).toBe('30.00 °C')
  }
})
it('keeps the existing MMS incomparable gate visible instead of adding an unbound reference',()=>{
  render()
  const checkbox=host.querySelector('input[type="checkbox"]') as HTMLInputElement
  act(()=>checkbox.click())
  expect(host.textContent).toContain('解析参考与当前运行不可比')
  expect(host.querySelector('[data-runtime-reference]')?.getAttribute('data-comparable')).toBe('false')
})
