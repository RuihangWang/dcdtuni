import {act,useSyncExternalStore} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {Inspector} from '../src/components/Inspector'
import {createPipeJunction,createPipeSegment} from '../src/legacy/factories'
import {stateV4} from '../src/legacy/cfdprojModules'
import {serializeProjectState,readProjectFileText} from '../src/io/projectFiles'
import {setFittingDiameter} from '../src/liquid/pipeJunctionInspector'
import * as store from '../src/projectStore'
import {setLocale,t} from '../src/i18n'
import type {CfdProject} from '../src/schema/cfdproj'
type Bag=Record<string,any>
let root:Root,host:HTMLDivElement,uid:string
function App(){useSyncExternalStore(store.subscribe,store.revision);return <Inspector project={store.getProject()} selectedUid={uid}/>}
function current(){return store.findConstruction(store.getProject()!,uid)!.model as Bag}
function create(cooling?:(c:Bag)=>void){const p=stateV4().getInitialStateV4() as unknown as CfdProject;p.meta={...(p.meta||{}),inputUnit:'mm'};const made=createPipeJunction(p);uid=made.model.uid;if(cooling)cooling(made.model.cooling as Bag);(stateV4() as any).addConstruction(p,made.model);store.loadProject(p,'private-fitting.cfdproj')}
function mount(){act(()=>root.render(<App/>))}
function commit(value:number){const input=host.querySelector<HTMLInputElement>('#fitting-diameter')!;expect(input).not.toBeNull();const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!;act(()=>{input.focus();setter.call(input,String(value));input.dispatchEvent(new Event('input',{bubbles:true}))});act(()=>input.blur())}
function declaration(){return [...host.querySelectorAll<HTMLButtonElement>('button')].find(button=>button.textContent===t('liquid.t_fitting_declare'))!}
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;setLocale('en-US');host=document.createElement('div');document.body.appendChild(host);root=createRoot(host)})
afterEach(()=>{act(()=>root.unmount());host.remove();vi.restoreAllMocks()})
it('the actual common Inspector edits physical bore in display mm, preserves arbitrary saved ports/frame and roundtrips through the codec',()=>{
 create(c=>{c.fittingDiameterM=.073;c.quaternion={x:0,y:Math.SQRT1_2,z:0,w:Math.SQRT1_2};c.ports[0].offset.x=-.12;c.ports[1].offset.x=.13;c.ports[2].offset.z=.14;c.ports[0].connectedPipeId='pipe_A';c.ports[1].id='saved_port_B';c.extra={retained:true}})
 const before=structuredClone(current().cooling);mount();expect(host.querySelector<HTMLInputElement>('#fitting-diameter')!.value).toBe('73.0000');commit(32)
 expect(current().cooling).toEqual({...before,fittingDiameterM:.032});expect(declaration()).toBeUndefined()
 const check=readProjectFileText(serializeProjectState(store.getProject()!));expect(check.ok).toBe(true);if(!check.ok)throw new Error(check.error)
 expect((store.findConstruction(check.project,uid)!.model.cooling as Bag)).toEqual({...before,fittingDiameterM:.032})
 act(()=>store.undo());expect(current().cooling).toEqual(before)
})
it('does not author old missing physics on load/render/save or on draft input; the explicit declaration is the single authoring action',()=>{
 create(c=>{delete c.type;delete c.quaternion;delete c.ports;delete c.fittingDiameterM});const before=JSON.stringify(current());mount()
 expect(host.querySelector<HTMLInputElement>('#fitting-diameter')!.value).toBe('');expect(declaration().disabled).toBe(true);expect(JSON.stringify(current())).toBe(before)
 const saved=JSON.parse(serializeProjectState(store.getProject()!));expect(saved.constructions.pipeJunctions[uid].cooling.fittingDiameterM).toBeUndefined()
 commit(40);expect(JSON.stringify(current())).toBe(before);expect(declaration().disabled).toBe(false)
 act(()=>declaration().click());expect(current().cooling.fittingDiameterM).toBe(.04);expect(current().cooling.ports.map((p:Bag)=>p.direction)).toEqual([{x:-1,y:0,z:0},{x:1,y:0,z:0},{x:0,y:0,z:1}]);expect(declaration()).toBeUndefined()
 act(()=>store.undo());expect(JSON.stringify(current())).toBe(before)
})
it('explicitly reauthors a legacy standard layout without deleting IDs, connections, roles or saved orientation',()=>{
 create(c=>{delete c.fittingDiameterM;c.quaternion={x:0,y:1,z:0,w:0};for(const port of c.ports)delete port.direction;c.ports[0].connectedPipeId='pipe_A';c.ports[1].connectedPipeId='pipe_B';c.ports[2].type='inout'})
 const before=structuredClone(current().cooling);mount();commit(50);act(()=>declaration().click())
 expect(current().cooling.quaternion).toEqual(before.quaternion);expect(current().cooling.ports.map((p:Bag)=>({id:p.id,connectedPipeId:p.connectedPipeId,type:p.type}))).toEqual(before.ports.map((p:Bag)=>({id:p.id,connectedPipeId:p.connectedPipeId,type:p.type})))
 expect(current().cooling.fittingDiameterM).toBe(.05)
})
it('refuses unknown legacy port identities instead of severing a saved topology',()=>{
 create(c=>{delete c.fittingDiameterM;delete c.ports[0].direction;c.ports[0].id='saved_unknown';c.ports[0].connectedPipeId='pipe_A'});mount();const before=JSON.stringify(current());commit(40);act(()=>declaration().click())
 expect(host.querySelector('[role=alert]')?.textContent).toMatch(/Unrecognised port IDs/);expect(JSON.stringify(current())).toBe(before)
})
it('refuses zero/negative/nonfinite edits and wrong kinds without writing a checkpoint',()=>{
 create();mount();const before=JSON.stringify(current());commit(0);expect(host.querySelector('[role=alert]')).not.toBeNull();commit(-20)
 for(const value of [0,-1,NaN,Infinity])expect(setFittingDiameter(uid,value)).toBe(false)
 expect(setFittingDiameter('missing',.03)).toBe(false);expect(JSON.stringify(current())).toBe(before)
 const pipe=createPipeSegment(store.getProject()!);act(()=>store.addModel(pipe.bucket,pipe.model));expect(setFittingDiameter(pipe.model.uid,.03)).toBe(false);expect((store.findConstruction(store.getProject()!,pipe.model.uid)!.model.cooling as Bag).fittingDiameterM).toBeUndefined()
})
