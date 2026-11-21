import * as THREE from 'three'
import {act,useEffect}from'react'
import{createRoot}from'react-dom/client'
import{afterEach,beforeEach,expect,it,vi}from'vitest'
import{RendererControl}from'../src/renderer/RendererControl'
import{setOvSession}from'../src/renderer/ovSession'
import type{CfdProject}from'../src/schema/cfdproj'
const f=vi.hoisted(()=>({project:{uid:'grid-project',meta:{name:'controlled-grid',casePath:''}},hash:'a'.repeat(64),client:null as any,lastView:null as any,packet:null as any,viewport:null as any,props:null as any,blocked:false,tasks:[]as any[]}))
vi.mock('../src/useTranslation',()=>({useTranslation:()=>({t:(key:string)=>key})}))
vi.mock('../src/projectStore',async(original)=>({...await original<typeof import('../src/projectStore')>(),getProject:()=>f.project,subscribe:()=>()=>{},revision:()=>0}))
vi.mock('../src/legacy/cfdrunBuild',()=>({cfdrunExport:()=>({projectContentHashSync:()=>f.hash})}))
vi.mock('../src/viewportBus',()=>({getViewportHandle:()=>f.viewport,subscribeCameraSettled:()=>()=>{}}))
vi.mock('../src/renderer/useSharedPostprocessRenderer',()=>({useSharedPostprocessRenderer(){}}))
vi.mock('../src/renderer/OvViewport',()=>({OvViewport:(props:any)=>{f.props=props;useEffect(()=>{f.packet=props.snapshot();f.lastView=f.packet.view;props.onReady(f.client,{ok:true,requestId:'controlled',projectHash:f.hash,sceneRevision:1},f.packet)},[]);return<div data-controlled-stream/>}}))
function ack(view:any){return{ok:true,requestId:'controlled-view',view,sceneRevision:view.backgroundGridRaster?.binding.sceneRevision??1,projectHash:f.hash,gridDisplay:view.backgroundGridRaster?{phase:'unsupported',reason:'stream-layout-unresolved'}:undefined}}
let host:HTMLDivElement,root:ReturnType<typeof createRoot>;const selection:string[]=[]
beforeEach(()=>{vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);vi.useFakeTimers();f.lastView=null;f.packet=null;f.blocked=false;f.tasks=[];f.project={uid:'grid-project',meta:{name:'controlled-grid',casePath:''}};f.hash='a'.repeat(64)
 const camera=new THREE.PerspectiveCamera(50,1.5,.1,500);camera.position.set(0,3,8)
 f.viewport={camera,controls:{target:new THREE.Vector3(),update(){}},readLineRaster:()=>({owner:'CONTROLLED_GL',drawingBuffer:[1992,796],css:[1992,796],pixelRatio:1,aliasedRange:[1,1],effectiveWidthPx:1,coordinateFrame:'scene-world',worldToClip:camera.projectionMatrix.clone().multiply(camera.matrixWorldInverse).elements})}
 f.client={onEditEvent(){},readView:vi.fn(async()=>({ok:true,requestId:'controlled',view:f.lastView})),updateView:vi.fn(async(v:any)=>{f.lastView=v;if(f.blocked&&v.backgroundGridRaster)return new Promise((resolve,reject)=>f.tasks.push({view:v,resolve,reject}));return ack(v)}),loadScene:vi.fn(async(p:any)=>{f.lastView=p.view;return{ok:true,requestId:'scene',projectHash:f.hash,sceneRevision:2}}),loadOverlay:vi.fn(async()=>({ok:true})),clearResultLayers:vi.fn(async()=>({ok:true}))}
 setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
 ;(window as any).globalGroup=new THREE.Group();host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(()=>{act(()=>root.unmount());setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null});delete(window as any).globalGroup;host.remove();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})
async function render(view:{showGrid:boolean;gridSpacingMm:number}){await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={selection} onSelection={()=>{}} activeTab="geometry" airMode={true} view={view}/>))}
async function mode(value:string){const select=host.querySelector<HTMLSelectElement>('select')!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,value);select.dispatchEvent(new Event('change',{bubbles:true}))})}
function emit(owner:string,revision:number,decoded:[number,number]){f.props.onDecodedRaster({type:'raster',raster:{owner,revision,decoded,css:[1992,796],layoutCss:[1992,796],objectFit:'contain'}})}
async function start(){await render({showGrid:true,gridSpacingMm:600});await mode('ov');await act(async()=>{await vi.advanceTimersByTimeAsync(500)});await act(async()=>f.props.onDecodedRaster({type:'owner',owner:'CONTROLLED_MEDIA'}))}
it('actual common controller binds current intrinsic decoder and measured line metric to accepted scene, preserves grid and performs only thin update',async()=>{
 await start();const scenes=f.client.loadScene.mock.calls.length;await act(async()=>emit('CONTROLLED_MEDIA',1,[1600,638]));const v=f.client.updateView.mock.lastCall[0]
 expect(v.backgroundGridRaster).toMatchObject({line:{drawingBuffer:[1992,796],effectiveWidthPx:1},output:{owner:'CONTROLLED_MEDIA',decoded:[1600,638]},binding:{projectUid:f.project.uid,projectContentSha256:f.hash}})
 expect(v.backgroundGrid.spacingM).toBe(.6);expect(f.client.loadScene.mock.calls).toHaveLength(scenes);expect(host.querySelector('[role=status]')?.textContent).toBe('renderer.grid_unavailable')
 await act(async()=>emit('CONTROLLED_MEDIA',2,[960,382]));expect(f.client.updateView.mock.lastCall[0].backgroundGridRaster.output.decoded).toEqual([960,382]);const count=f.client.updateView.mock.calls.length
 await act(async()=>{emit('OLD_MEDIA',3,[1600,900]);emit('CONTROLLED_MEDIA',1,[1600,900])});expect(f.client.updateView.mock.calls).toHaveLength(count)
})
it('a truly obsolete wrong ACK is ignored but the current wrong source ACK remains a visible verification failure',async()=>{
 await start();f.blocked=true;await act(async()=>emit('CONTROLLED_MEDIA',1,[1600,638]));expect(f.tasks).toHaveLength(1);await act(async()=>emit('CONTROLLED_MEDIA',2,[960,382]));expect(f.tasks).toHaveLength(1)
 await act(async()=>f.tasks[0].resolve({...ack(f.tasks[0].view),projectHash:'b'.repeat(64)}));expect(f.tasks).toHaveLength(2);await act(async()=>f.tasks[1].resolve({...ack(f.tasks[1].view),projectHash:'b'.repeat(64)}));expect(host.querySelector('[role=status]')?.textContent).toBe('renderer.grid_unavailable');expect(host.querySelector('[data-controlled-stream]')).not.toBeNull()
})
it('the newest delivered decoder raster is queued after a hung command and old rejection never terminates the shared viewer',async()=>{
 await start();f.blocked=true;await act(async()=>emit('CONTROLLED_MEDIA',1,[1600,638]));await act(async()=>{emit('CONTROLLED_MEDIA',2,[960,382]);emit('CONTROLLED_MEDIA',3,[1600,590])});expect(f.tasks).toHaveLength(1)
 await act(async()=>f.tasks[0].reject(Error('CONTROLLED obsolete response failure')));expect(f.tasks).toHaveLength(2);expect(f.tasks[1].view.backgroundGridRaster.output.decoded).toEqual([1600,590]);await act(async()=>f.tasks[1].resolve({...ack(f.tasks[1].view),gridDisplay:{phase:'rendered'}}));expect(host.querySelector('[role=status]')).toBeNull();expect(host.querySelector('[data-controlled-stream]')).not.toBeNull()
})
it('a newly accepted scene rebinds retained decoder dimensions even with no decoder/camera event; no new source is attached to the old ACK',async()=>{
 await start();await act(async()=>emit('CONTROLLED_MEDIA',1,[1600,638]));const old=f.client.updateView.mock.lastCall[0].backgroundGridRaster.binding
 f.project={uid:'grid-next-project',meta:{name:'controlled-next',casePath:''}};f.hash='b'.repeat(64);await render({showGrid:true,gridSpacingMm:600});await act(async()=>{await vi.advanceTimersByTimeAsync(500)})
 const next=f.client.updateView.mock.lastCall[0];expect(next.backgroundGridRaster.binding).toMatchObject({projectUid:'grid-next-project',projectContentSha256:'b'.repeat(64),sceneRevision:2});expect(next.backgroundGridRaster.output.decoded).toEqual([1600,638]);expect(next.backgroundGridRaster.binding.viewRevision).toBeGreaterThan(old.viewRevision)
})
