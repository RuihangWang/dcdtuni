import * as THREE from 'three'
import {act,useEffect,useState} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import type {CfdProject} from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import {cfdrunExport} from '../src/legacy/cfdrunBuild'
import {buildSceneSnapshot,type SceneSnapshot} from '../src/renderer/sceneSnapshot'
import {RendererControl} from '../src/renderer/RendererControl'
import {createOvEditBridge} from '../src/renderer/ovEditBridge'
import {OvCommandClient} from '../src/renderer/ovProtocol'
import {setOvSession} from '../src/renderer/ovSession'

// Only the transport/native-window carrier is controlled. RendererControl,
// onEditEvent decoding, schema, project hash, whitelist and selection bridge run.
const f=vi.hoisted(()=>({client:null as unknown,packet:null as unknown,camera:null as unknown}))
vi.mock('../src/viewportBus',()=>({getViewportHandle:()=>({camera:f.camera,controls:{target:new THREE.Vector3(),update(){}}}),subscribeCameraSettled:()=>()=>{}}))
vi.mock('../src/renderer/useSharedPostprocessRenderer',()=>({useSharedPostprocessRenderer(){}}))
vi.mock('../src/renderer/OvViewport',()=>({OvViewport:(props:{onReady:(value:unknown,ack:unknown,packet:unknown)=>void})=>{
 useEffect(()=>{const project=store.getProject()!;props.onReady(f.client,{ok:true,requestId:'controlled',projectHash:cfdrunExport().projectContentHashSync(project),sceneRevision:7},f.packet)},[])
 return <div data-controlled-video-carrier="true"/>
}}))
let host:HTMLDivElement,root:Root,client:OvCommandClient,packet:SceneSnapshot,project:CfdProject
function payload(selection:{primId:string;modelUid:string}[],extra:Record<string,unknown>={}){
 return {schema:'dcdtuni.ov-edit/v1',sceneRevision:7,projectHash:cfdrunExport().projectContentHashSync(store.getProject()),coordinateSystem:{upAxis:'Y',handedness:'right',metersPerUnit:1},action:{type:'selection'},selection,...extra}
}
const ups={primId:'UPS_Rack-face0',modelUid:'UPS_Rack'},cdu={primId:'CDU_1-face0',modelUid:'CDU_1'}
function CommonSelection(){
 const [uids,setUids]=useState(['UPS_Rack'])
 return <><RendererControl project={project} selectedUids={uids} onSelection={setUids} activeTab="geometry" airMode={false}/><output data-common-selection="true">{uids.join(',')}</output></>
}
beforeEach(()=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
 project={uid:'selection-project',constructions:{racks:{UPS_Rack:{uid:'UPS_Rack',meta:{name:'UPS'},geometry:{location:{x:0,y:0,z:0},size:{x:1,y:1,z:1}}}},cdus:{CDU_1:{uid:'CDU_1',meta:{name:'CDU'},geometry:{location:{x:2,y:0,z:0},size:{x:1,y:1,z:1}}}}}} as CfdProject
 store.loadProject(project,null);project=store.getProject()!
 const group=new THREE.Group()
 for(const uid of ['UPS_Rack','CDU_1']){const model=new THREE.Group();model.userData.modelId=uid;for(let i=0;i<2;i++)model.add(new THREE.Mesh(new THREE.BoxGeometry(),new THREE.MeshStandardMaterial()));group.add(model)}
 packet=buildSceneSnapshot({root:group,project});packet.primitives.forEach((p,i)=>p.id=p.modelUid+'-face'+(i%2))
 f.packet=packet;f.camera=new THREE.PerspectiveCamera();(window as unknown as {globalGroup:unknown}).globalGroup=group
 client=new OvCommandClient(async()=>{})
 vi.spyOn(client,'updateView').mockResolvedValue({ok:true,requestId:'controlled',gridDisplay:{phase:'unsupported',reason:'stream-layout-unresolved'}} as Awaited<ReturnType<OvCommandClient['updateView']>>)
 f.client=client;setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(async()=>{await act(async()=>{root.unmount();client.dispose();await Promise.resolve()});setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null});host.remove();delete(window as {globalGroup?:unknown}).globalGroup;vi.restoreAllMocks();vi.unstubAllGlobals()})
async function mount(){
 await act(async()=>root.render(<CommonSelection/>))
 const select=host.querySelector<HTMLSelectElement>('select')!
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,'ov');select.dispatchEvent(new Event('change',{bubbles:true}))})
}
async function receive(value:unknown){await act(async()=>{client.receive({event_type:'dcdtuni:ovEditEvent',payload:value});await Promise.resolve()})}
it('mounted native Shift snapshot reaches both whole objects with unsupported grid proof',async()=>{
 await mount();await receive(payload([ups,cdu]));expect(host.querySelector('output')!.textContent).toBe('UPS_Rack,CDU_1');expect(store.undoDepth()).toBe(0)
})
it('mounted native empty snapshot clears common selection',async()=>{
 await mount();await receive(payload([]));expect(host.querySelector('output')!.textContent).toBe('')
})
it('mounted invalid/stale mixed snapshots never partially select or clear',async()=>{
 await mount()
 for(const value of [payload([ups,{primId:'unknown',modelUid:'CDU_1'}]),payload([cdu],{sceneRevision:6}),payload([cdu],{projectHash:'0'.repeat(64)}),payload([ups,ups]),payload([cdu],{extra:true})]){await receive(value);expect(host.querySelector('output')!.textContent).toBe('UPS_Rack')}
})
it('keeps legacy single pick compatibility through the mounted common driver',async()=>{
 await mount();const {selection:_,...event}=payload([]);await receive({...event,primId:cdu.primId,modelUid:cdu.modelUid,action:{type:'pick'}});expect(host.querySelector('output')!.textContent).toBe('CDU_1')
})
it('rejects a hidden source, bad UID, excessive list and frozen host before common selection',()=>{
 const selected=vi.fn(),options={snapshot:packet,sceneRevision:7,currentSceneRevision:()=>7,pickablePrimitiveIds:new Set(packet.primitives.map(p=>p.id)),editablePrimitiveIds:new Set<string>(),canEdit:()=>true,onSelection:selected}
 packet.primitives.find(p=>p.id===cdu.primId)!.visible=false
 const bridge=createOvEditBridge(options)
 expect(bridge.apply(payload([cdu]))).toMatchObject({ok:false,reason:'not_editable'})
 expect(bridge.apply(payload([{...ups,modelUid:'CDU_1'}]))).toMatchObject({ok:false,reason:'model_mismatch'})
 expect(bridge.apply(payload(Array.from({length:1025},()=>ups)))).toMatchObject({ok:false,reason:'invalid_event'})
 expect(createOvEditBridge({...options,canEdit:()=>false}).apply(payload([]))).toMatchObject({ok:false,reason:'editing_disabled'})
 expect(selected).not.toHaveBeenCalled();expect(store.undoDepth()).toBe(0)
})
