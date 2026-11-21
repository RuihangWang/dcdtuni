import * as THREE from 'three'
import {act,useEffect}from'react'
import{createRoot}from'react-dom/client'
import{afterEach,beforeEach,expect,it,vi}from'vitest'
import{RendererControl}from'../src/renderer/RendererControl'
import{setOvSession}from'../src/renderer/ovSession'
import type{CfdProject}from'../src/schema/cfdproj'
const f=vi.hoisted(()=>({project:{uid:'grid-project',meta:{name:'controlled-grid',casePath:''}},hash:'a'.repeat(64),client:null as any,lastView:null as any,packet:null as any,viewport:null as any}))
vi.mock('../src/projectStore',async(original)=>({...await original<typeof import('../src/projectStore')>(),getProject:()=>f.project,subscribe:()=>()=>{},revision:()=>0}))
vi.mock('../src/legacy/cfdrunBuild',()=>({cfdrunExport:()=>({projectContentHashSync:()=>f.hash})}))
vi.mock('../src/viewportBus',()=>({getViewportHandle:()=>f.viewport,subscribeCameraSettled:()=>()=>{}}))
vi.mock('../src/renderer/useSharedPostprocessRenderer',()=>({useSharedPostprocessRenderer(){}}))
vi.mock('../src/renderer/OvViewport',()=>({OvViewport:(props:any)=>{useEffect(()=>{f.packet=props.snapshot();f.lastView=f.packet.view;props.onReady(f.client,{ok:true,requestId:'controlled',projectHash:f.hash,sceneRevision:1},f.packet)},[]);return<div data-controlled-stream/>}}))
let host:HTMLDivElement,root:ReturnType<typeof createRoot>;const selection:string[]=[]
beforeEach(()=>{vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);vi.useFakeTimers();f.lastView=null;f.packet=null
 const camera=new THREE.PerspectiveCamera(50,1.5,.1,500);camera.position.set(0,3,8)
 f.viewport={camera,controls:{target:new THREE.Vector3(),update(){}}}
 f.client={onEditEvent(){},readView:vi.fn(async()=>({ok:true,requestId:'controlled',view:f.lastView})),updateView:vi.fn(async(v:any)=>{f.lastView=v;return{ok:true,requestId:'view',view:v}}),loadScene:vi.fn(async(p:any)=>{f.lastView=p.view;return{ok:true,requestId:'scene',projectHash:f.hash,sceneRevision:2}}),loadOverlay:vi.fn(async()=>({ok:true})),clearResultLayers:vi.fn(async()=>({ok:true}))}
 setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
 ;(window as any).globalGroup=new THREE.Group();host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(()=>{act(()=>root.unmount());setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null});delete(window as any).globalGroup;host.remove();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})
async function render(view:{showGrid:boolean;gridSpacingMm:number}){await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={selection} onSelection={()=>{}} activeTab="geometry" airMode={true} view={view}/>))}
async function mode(value:string){const select=host.querySelector<HTMLSelectElement>('select')!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,value);select.dispatchEvent(new Event('change',{bubbles:true}))})}
it('uses the same toolbar grid in initial OV snapshot, thin updates and renderer roundtrip without geometry resend',async()=>{
 await render({showGrid:true,gridSpacingMm:100});await mode('ov');await act(async()=>{await vi.advanceTimersByTimeAsync(500)})
 expect(f.packet.view.backgroundGrid).toMatchObject({visible:true,spacingM:.1})
 const sceneCalls=f.client.loadScene.mock.calls.length
 await render({showGrid:false,gridSpacingMm:600});await act(async()=>{await vi.advanceTimersByTimeAsync(500)})
 expect(f.client.updateView.mock.lastCall[0].backgroundGrid).toMatchObject({visible:false,spacingM:.6})
 expect(f.client.loadScene.mock.calls).toHaveLength(sceneCalls)
 expect(f.lastView.selectedUids).toEqual([]);expect(f.lastView.timeSeconds).toBeNull()
 await mode('three');await mode('ov');await act(async()=>{await vi.advanceTimersByTimeAsync(500)})
 expect(f.packet.view.backgroundGrid).toMatchObject({visible:false,spacingM:.6})
 expect(f.packet.primitives).toEqual([])
})
