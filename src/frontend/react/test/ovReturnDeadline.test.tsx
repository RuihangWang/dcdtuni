import * as THREE from 'three'
import {act,useEffect} from 'react'
import {createRoot} from 'react-dom/client'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {RendererControl} from '../src/renderer/RendererControl'
import {OvCommandClient} from '../src/renderer/ovProtocol'
import {setOvSession} from '../src/renderer/ovSession'
import type {CfdProject} from '../src/schema/cfdproj'
const f=vi.hoisted(()=>({project:{uid:'return-project',meta:{name:'controlled-return',casePath:''}},hash:'a'.repeat(64),client:null as any,wire:null as any,sent:[]as any[],lastView:null as any,viewport:null as any,selection:null as any}))
vi.mock('../src/useTranslation',()=>({useTranslation:()=>({t:(key:string,vars?:any)=>key+(vars?.error?': '+vars.error:'')})}))
vi.mock('../src/projectStore',async original=>({...await original<typeof import('../src/projectStore')>(),getProject:()=>f.project,subscribe:()=>()=>{},revision:()=>0}))
vi.mock('../src/legacy/cfdrunBuild',()=>({cfdrunExport:()=>({projectContentHashSync:()=>f.hash})}))
vi.mock('../src/viewportBus',()=>({getViewportHandle:()=>f.viewport,subscribeCameraSettled:()=>()=>{}}))
vi.mock('../src/renderer/useSharedPostprocessRenderer',()=>({useSharedPostprocessRenderer(){}}))
vi.mock('../src/renderer/OvViewport',()=>({OvViewport:(props:any)=>{useEffect(()=>{
 const client=f.client,packet=props.snapshot();f.lastView=packet.view
 props.onReady(client,{ok:true,requestId:'controlled-scene',projectHash:f.hash,sceneRevision:1},packet)
 return()=>client.dispose()
},[]);return<div data-controlled-stream/>}}))
let host:HTMLDivElement,root:ReturnType<typeof createRoot>
function newClient(){
 const sent:any[]=[];const wire=new OvCommandClient(async payload=>{sent.push(payload)})
 const client={onEditEvent(){},readView:vi.fn(()=>wire.readView()),updateView:vi.fn(async(view:any)=>{f.lastView=view;return{ok:true,requestId:'controlled-update',view,sceneRevision:1,projectHash:f.hash}}),
 loadScene:vi.fn(async(packet:any)=>{f.lastView=packet.view;return{ok:true,requestId:'controlled-scene',projectHash:f.hash,sceneRevision:1}}),
 loadOverlay:vi.fn(async()=>({ok:true})),clearResultLayers:vi.fn(async()=>({ok:true})),dispose:vi.fn(()=>wire.dispose())}
 f.client=client;f.wire=wire;f.sent=sent;return{client,wire,sent}
}
beforeEach(()=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);vi.useFakeTimers()
 f.project={uid:'return-project',meta:{name:'controlled-return',casePath:''}};f.hash='a'.repeat(64);f.lastView=null;f.selection=vi.fn()
 const camera=new THREE.PerspectiveCamera(50,1.5,.1,500);camera.position.set(0,3,8)
 f.viewport={camera,controls:{target:new THREE.Vector3(),update:vi.fn()},readLineRaster:()=>null}
 newClient();setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
 ;(window as any).globalGroup=new THREE.Group();host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(()=>{
 act(()=>root.unmount());setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
 delete(window as any).globalGroup;host.remove();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()
})
async function render(){await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={f.selection} activeTab="geometry" airMode={true} view={{showGrid:true,gridSpacingMm:300}}/>))}
function selector(){return host.querySelector<HTMLSelectElement>('select')!}
async function mode(value:string){await act(async()=>{const select=selector();Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,value);select.dispatchEvent(new Event('change',{bubbles:true}))})}
async function start(){await render();await mode('ov');await act(async()=>{await vi.advanceTimersByTimeAsync(500)})}
function handoffId(){const sent=f.sent.filter((p:any)=>p.command_name==='getViewState');expect(sent).toHaveLength(1);return JSON.parse(sent[0].message).requestId}
function ack(id:string){
 const view=structuredClone(f.lastView);view.camera={...view.camera,position:[9,4,7]};view.selectedUids=['controlled-selection'];view.timeSeconds=null
 // Synthetic metadata fixture exercises lossless transport, not native proof.
 const binding={projectUid:f.project.uid,projectContentSha256:f.hash,sceneRevision:1,viewRevision:1}
 return{requestId:id,ok:true,sceneRevision:1,projectHash:f.hash,primitives:0,view,gridDisplay:{phase:'unsupported',reason:'stream-layout-unresolved',gridPixelVerified:false,nativeGeometry:{
 schema:'dcdtuni.native-grid-geometry/v1',phase:'synchronized',domain:'native-world-plane',binding,grid:structuredClone(view.backgroundGrid),
 requestedCamera:structuredClone(f.lastView.camera),acceptedCamera:structuredClone(view.camera),timeSeconds:null,
 nativeSource:{ownerSha256:'b'.repeat(64),projectionSha256:'c'.repeat(64),frameNumber:4,resolution:[1920,1080],fullResolution:[1920,1080]},carrierSha256:'d'.repeat(64),gridPixelVerified:false,encodedFrameProof:'unresolved'}}}
}
async function receive(value:unknown,wire=f.wire,serialized=true){await act(async()=>wire.receive({event_type:'dcdtuni:renderAck',payload:serialized?{renderAckJSON:JSON.stringify(value)}:value}))}
it('keeps the same viewer alive beyond1s and applies a matching wholeJSON readback at the observed4.536s delay',async()=>{
 await start();await mode('three');const id=handoffId(),wire=f.wire,client=f.client,response=ack(id)
 await act(async()=>{await vi.advanceTimersByTimeAsync(4536)})
 expect(selector().value).toBe('ov');expect(selector().disabled).toBe(true);expect(client.dispose).not.toHaveBeenCalled()
 await receive(response,wire)
 expect(selector().value).toBe('three');expect(client.dispose).toHaveBeenCalledTimes(1)
 expect(f.selection).toHaveBeenCalledTimes(1);expect(f.selection).toHaveBeenCalledWith(['controlled-selection']);expect(f.viewport.camera.position.toArray()).toEqual([9,4,7])
 expect(host.querySelector('[role=status]')).toBeNull()
 expect(response.view.timeSeconds).toBe(null);expect(response.gridDisplay.nativeGeometry.timeSeconds).toBe(null)
 expect(response.gridDisplay.nativeGeometry.gridPixelVerified).toBe(false);expect(response.gridDisplay.nativeGeometry.encodedFrameProof).toBe('unresolved')
})
it('falls back by8s then ignores an old late wholeJSON ACK after a fresh OV session starts',async()=>{
 await start();await mode('three');const id=handoffId(),old=f.wire,oldClient=f.client,response=ack(id)
 await act(async()=>{await vi.advanceTimersByTimeAsync(7999)});expect(selector().value).toBe('ov')
 await act(async()=>{await vi.advanceTimersByTimeAsync(1)});expect(selector().value).toBe('three');expect(oldClient.dispose).toHaveBeenCalledTimes(1)
 expect(host.querySelector('[role=status]')?.textContent).toContain('Omniverse view reply timed out')
 expect(f.selection).not.toHaveBeenCalled();expect(f.viewport.camera.position.toArray()).toEqual([0,3,8])
 newClient();await mode('ov');await act(async()=>{await vi.advanceTimersByTimeAsync(500)})
 await receive(response,old);expect(selector().value).toBe('ov')
 expect(f.selection).not.toHaveBeenCalled();expect(f.viewport.camera.position.toArray()).toEqual([0,3,8]);expect(f.client.dispose).not.toHaveBeenCalled()
})
it('retains acknowledged scene identity refusal despite a valid serialized response after1s',async()=>{
 await start();await mode('three');const response=ack(handoffId());response.projectHash='e'.repeat(64)
 await act(async()=>{await vi.advanceTimersByTimeAsync(2475)});await receive(response)
 expect(selector().value).toBe('three');expect(f.selection).not.toHaveBeenCalled();expect(f.viewport.camera.position.toArray()).toEqual([0,3,8])
 expect(host.querySelector('[role=status]')?.textContent).toContain('another acknowledged scene')
})
it('does not overwrite a newly changed local project with an older pending handoff',async()=>{
 await start();await mode('three');const response=ack(handoffId())
 f.project={uid:'new-return-project',meta:{name:'controlled-new',casePath:''}};f.hash='e'.repeat(64);await render()
 await act(async()=>{await vi.advanceTimersByTimeAsync(4536)});await receive(response)
 expect(selector().value).toBe('three');expect(f.selection).not.toHaveBeenCalled();expect(f.viewport.camera.position.toArray()).toEqual([0,3,8])
})
it('preserves the strict frame time fence rather than normalizing a different finite time',async()=>{
 await start();await mode('three');const response=ack(handoffId());response.view.timeSeconds=123
 await act(async()=>{await vi.advanceTimersByTimeAsync(2475)});await receive(response)
 expect(selector().value).toBe('three');expect(f.selection).not.toHaveBeenCalled();expect(f.viewport.camera.position.toArray()).toEqual([0,3,8])
})
it('keeps existing legacy object ACK compatibility at a several-second handoff delay',async()=>{
 await start();await mode('three');const response=ack(handoffId())
 await act(async()=>{await vi.advanceTimersByTimeAsync(4536)});await receive(response,f.wire,false)
 expect(selector().value).toBe('three');expect(f.selection).toHaveBeenCalledTimes(1);expect(f.selection).toHaveBeenCalledWith(['controlled-selection'])
 expect(host.querySelector('[role=status]')).toBeNull()
})
it('preserves immediate inner disconnect failure and bounded local fallback',async()=>{
 await start();await mode('three');handoffId()
 await act(async()=>f.wire.dispose())
 expect(selector().value).toBe('three');expect(f.selection).not.toHaveBeenCalled();expect(f.viewport.camera.position.toArray()).toEqual([0,3,8])
 expect(host.querySelector('[role=status]')?.textContent).toContain('Omniverse disconnected')
})
