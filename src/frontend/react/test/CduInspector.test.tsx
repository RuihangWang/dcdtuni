import {act,useSyncExternalStore} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {CduProperties} from '../src/components/CduInspector'
import {readProjectFileText} from '../src/io/projectFiles'
import {readFluid} from '../src/liquid/liquidConfig'
import * as store from '../src/projectStore'
import {setLocale,t} from '../src/i18n'
import type {CfdProject,Construction} from '../src/schema/cfdproj'

// The CDU panel says what the DAE does with each value (report 3.B1-B5): the
// return side is an editable reference pressure, the supply pressure is the
// legacy solver's, mode-bound fields are greyed with their reason, the primary
// flow is typed in L/min with kg/s beside it, and with pipes the served racks
// are derived, not a switch.

type Bag=Record<string,any>
const root=resolve(process.cwd(),'../../..')
let host:HTMLDivElement,reactRoot:Root,uid:string
function load(path:string){const r=readProjectFileText(readFileSync(resolve(root,path),'utf8'));if(!r.ok)throw new Error(r.error);store.loadProject(r.project,null);uid=(Object.values((store.getProject() as Bag).constructions.cdus)[0] as Bag).uid}
function cdu(){return store.findConstruction(store.getProject()!,uid)!.model as Bag}
function Panel(){useSyncExternalStore(store.subscribe,store.revision);const p=store.getProject() as CfdProject;return <CduProperties project={p} model={store.findConstruction(p,uid)!.model as Construction}/>}
function mount(){act(()=>reactRoot.render(<Panel/>))}
function input(id:string){const el=host.querySelector<HTMLInputElement>('#'+id);expect(el,id).not.toBeNull();return el!}
function commit(id:string,value:number){const el=input(id),setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!;act(()=>{el.focus();setter.call(el,String(value));el.dispatchEvent(new Event('input',{bubbles:true}))});act(()=>el.blur())}
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;setLocale('zh-CN');host=document.createElement('div');document.body.appendChild(host);reactRoot=createRoot(host)})
afterEach(()=>{act(()=>reactRoot.unmount());host.remove();vi.restoreAllMocks();setLocale('zh-CN')})

it('edits the return reference pressure and never claims an atmospheric fixed return',()=>{
  load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj');mount()
  expect(host.textContent).not.toContain('101.325')
  expect(host.textContent).toContain(t('lq_ux.cdu_return_pressure'))
  expect(input('cdu-return-pressure').value).toBe('150')
  commit('cdu-return-pressure',180)
  expect(cdu().cooling.returnPressureKpa).toBe(180)
  // The supply pressure is the legacy solver's only, folded away.
  expect(input('cdu-supply-pressure').closest('details')?.id).toBe('cdu-legacy-params')
})

it('greys the set-point temperature and fixed flow outside their modes, with the reason',()=>{
  load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj');mount()
  expect(input('cdu-supply-temp').disabled).toBe(true)
  expect(host.querySelector('#cdu-supply-temp-reason')?.textContent).toBe(t('lq_ux.cdu_supply_temp_disabled'))
  expect(input('cdu-flow').disabled).toBe(true)
  expect(host.querySelector('#cdu-flow-kgs')?.textContent).toContain(t('lq_ux.cdu_flow_disabled'))
  act(()=>{store.setModelCooling(uid,'secondaryControl','setpoint');store.setModelCooling(uid,'pumpControl','flow')})
  expect(input('cdu-supply-temp').disabled).toBe(false)
  expect(input('cdu-flow').disabled).toBe(false)
  expect(host.querySelector('#cdu-supply-temp-reason')).toBeNull()
  // A fixed-flow pump has no speed to set.
  expect(input('cdu-pump-speed').disabled).toBe(true)
})

it('types the primary flow in L/min, stores kg/s, and shows kg/s beside it',()=>{
  load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj');mount()
  const rho=readFluid(store.getProject()).rho
  expect(host.textContent).toContain(t('lq_ux.cdu_primary_flow'))
  expect(t('lq_ux.cdu_primary_flow')).toContain('L/min')
  expect(Number(input('cdu-primary-flow').value)).toBeCloseTo(1*60000/rho,3)
  commit('cdu-primary-flow',90)
  expect(cdu().cooling.primaryFlowKgS).toBeCloseTo(90*rho/60000,12)
  expect(host.querySelector('#cdu-primary-flow-kgs')?.textContent).toContain((90*rho/60000).toFixed(3))
  commit('cdu-primary-inlet',24)
  expect(cdu().cooling.primaryInletTemperatureC).toBe(24)
})

it('chooses the HX model and says what an unstated one runs as',()=>{
  load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj');mount()
  const select=host.querySelector<HTMLSelectElement>('#cdu-hx-model')!
  expect(select.value).toBe('mixed-volumes')
  expect(host.querySelector('#cdu-hx-unset')).not.toBeNull()
  act(()=>{select.value='counterflow-ntu';select.dispatchEvent(new Event('change',{bubbles:true}))})
  expect(cdu().cooling.hxModel).toBe('counterflow-ntu')
  expect(host.querySelector('#cdu-hx-unset')).toBeNull()
})

it('shows the served racks as derived and read-only once pipes exist',()=>{
  load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj');mount()
  expect(host.querySelector('#cdu-served-derived')).not.toBeNull()
  expect(host.querySelector('#cdu-served input[type="checkbox"]')).toBeNull()
  expect(host.textContent).not.toContain('CDU — Cooling Distribution Unit')
  expect(host.textContent).not.toContain('No rack models in scene')
})
