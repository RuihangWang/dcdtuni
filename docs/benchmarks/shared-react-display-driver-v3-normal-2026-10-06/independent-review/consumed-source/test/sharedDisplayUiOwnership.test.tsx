import {act,useEffect} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {RendererControl} from '../src/renderer/RendererControl'
import {setOvSession} from '../src/renderer/ovSession'
import {clearResultLayerSet,publishResultLayerSet,setLiveAirResultSelection} from '../src/renderer/resultLayerRevision'
import * as meshPreview from '../src/run/meshPreviewSession'
import {PostprocessDisplayDriver} from '../src/postprocess/displaySession'
import {createOvPostprocessAdapter} from '../src/renderer/ovPostprocessAdapter'
import type {ResultLayerSet} from '../src/renderer/resultLayers'
import type {CfdProject} from '../src/schema/cfdproj'
const f=vi.hoisted(()=>({project:{uid:'project-a',meta:{name:'controlled-ui',casePath:''}},hash:'a'.repeat(64),client:null as unknown}))
vi.mock('../src/projectStore',async(original)=>({...await original<typeof import('../src/projectStore')>(),getProject:()=>f.project,subscribe:()=>()=>{},revision:()=>0}))
vi.mock('../src/legacy/cfdrunBuild',()=>({cfdrunExport:()=>({projectContentHashSync:()=>f.hash})}))
vi.mock('../src/viewportBus',()=>({getViewportHandle:()=>({camera:{},controls:{target:{},update(){}}}),subscribeCameraSettled:()=>()=>{}}))
vi.mock('../src/renderer/sceneSnapshot',()=>({buildSceneSnapshot:()=>({project:{uid:f.project.uid},primitives:[],textures:{},warnings:[],view:{timeSeconds:null,selectedUids:[]}}),captureViewState:()=>({})}))
vi.mock('../src/renderer/useSharedPostprocessRenderer',()=>({useSharedPostprocessRenderer(){}}))
vi.mock('../src/renderer/meshPreviewOverlay',()=>({buildMeshPreviewOverlay:()=>({}),meshOverlayRevision:()=>0}))
vi.mock('../src/renderer/ovEditBridge',()=>({createOvEditBridge:()=>({apply(){}})}))
vi.mock('../src/components/OvPostProcessPanel',()=>({OvPostProcessPanel:()=> <fieldset data-independent-ov-product="true"><button>second result frame selector</button></fieldset>}))
vi.mock('../src/renderer/OvViewport',()=>({OvViewport:(props:{snapshot:()=>unknown;onReady:(value:unknown,ack:unknown,packet:unknown)=>void})=>{
 useEffect(()=>{const client=f.client;props.onReady(client,{ok:true,requestId:'controlled',projectHash:f.hash,sceneRevision:1},props.snapshot())},[])
 return <div data-controlled-ov-viewport="true"/>
}}))
let host:HTMLDivElement,root:Root
beforeEach(()=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
 f.project={uid:'project-a',meta:{name:'controlled-ui',casePath:''}}
 f.client={onEditEvent(){},clearResultLayers:vi.fn(async()=>({ok:true,requestId:'controlled'})),loadResultLayers:vi.fn(async(packet:ResultLayerSet)=>({ok:true,requestId:'controlled',revision:packet.revision})),loadScene:vi.fn(async(_packet:unknown,hash:string)=>({ok:true,requestId:'controlled',projectHash:hash,sceneRevision:2})),loadOverlay:async()=>({ok:true,requestId:'controlled'}),readView:async()=>({ok:true,requestId:'controlled'})}
 setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
 ;(window as unknown as {globalGroup:unknown}).globalGroup={}
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(()=>{act(()=>root.unmount());clearResultLayerSet();setLiveAirResultSelection(null);vi.restoreAllMocks();vi.useRealTimers();host.remove();delete (window as {globalGroup?:unknown}).globalGroup;vi.unstubAllGlobals()})
it('has no independent renderer-specific product controls with Three selected',async()=>{
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="postprocess" airMode={true}/>))
 expect(host.querySelector('[data-independent-ov-product]')).toBeNull()
})
it('switching the carrier to OV does not mount a second product run/frame/settings UI',async()=>{
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="postprocess" airMode={true}/>))
 const select=host.querySelector<HTMLSelectElement>('select')!
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,'ov');select.dispatchEvent(new Event('change',{bubbles:true}))})
 expect(host.querySelector('[data-controlled-ov-viewport]')).not.toBeNull()
 expect(host.querySelector('[data-independent-ov-product]')).toBeNull()
})

it('the retained mesh route never clears the air owner when the common selection is null',async()=>{
 vi.useFakeTimers()
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="postprocess" airMode={true}/>))
 const select=host.querySelector<HTMLSelectElement>('select')!
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,'ov');select.dispatchEvent(new Event('change',{bubbles:true}))})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect((f.client as {clearResultLayers:ReturnType<typeof vi.fn>}).clearResultLayers).not.toHaveBeenCalled()
})

function meshFixture() {
 const key='e'.repeat(64),source={projectUid:f.project.uid,projectContentSha256:f.hash,cfdrunSha256:'b'.repeat(64),meshReceiptSha256:'c'.repeat(64),meshQuality:'diagnostic' as const,run:null}
 const packet:ResultLayerSet={schema:'dcdtuni.render-result-layers/v1',revision:1,source,timeSeconds:null,coordinateSystem:{upAxis:'Y',metersPerUnit:1,handedness:'right'},
  layers:[{kind:'meshEdges',layerId:'wall',visible:true,meshPatchName:'wall',sourceFaceUids:['project-a/model-a/front'],positions:[0,0,0,1,0,0],indices:[0,1],opacity:.5}]}
 const original=meshPreview.getMeshPreviewSession(),state={...original,visible:true,built:{} as NonNullable<typeof original.built>,meshSnapshotKey:key}
 const preview=vi.spyOn(meshPreview,'getMeshPreviewSession').mockReturnValue(state)
 publishResultLayerSet(packet,source,null,undefined,'mesh-preview',{owner:'mesh-preview',meshSnapshotKey:key})
 return {preview,state,packet,source,key}
}
async function mountOv() {
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="mesh" airMode={true}/>))
 const select=host.querySelector<HTMLSelectElement>('select')!
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,'ov');select.dispatchEvent(new Event('change',{bubbles:true}))})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
}
it('clears the actual numeric mesh carrier after the public mesh preview is hidden',async()=>{
 vi.useFakeTimers();const m=meshFixture(),client=f.client as {loadResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>}
 await mountOv();expect(client.loadResultLayers).toHaveBeenCalledOnce()
 await act(async()=>{m.preview.mockReturnValue({...m.state,visible:false});clearResultLayerSet('mesh-preview')})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect(client.clearResultLayers).toHaveBeenCalledOnce()
})

it('hiding during the actual mesh carrier transfer clears it after the mesh ACK',async()=>{
 vi.useFakeTimers();const m=meshFixture(),client=f.client as {loadResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>}
 let release!:(value:unknown)=>void;client.loadResultLayers.mockImplementationOnce(()=>new Promise(done=>release=done))
 await mountOv();expect(client.loadResultLayers).toHaveBeenCalledOnce()
 await act(async()=>{m.preview.mockReturnValue({...m.state,visible:false});clearResultLayerSet('mesh-preview')})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect(client.clearResultLayers).not.toHaveBeenCalled()
 await act(async()=>{release({ok:true,requestId:'mesh',revision:1});await vi.advanceTimersByTimeAsync(0)})
 expect(client.clearResultLayers).toHaveBeenCalledOnce()
})
function presentAir() {
 const session={runId:'9'.repeat(64),timeValue:.015,revision:'accepted-r1'},source={projectUid:f.project.uid,projectContentSha256:f.hash,cfdrunSha256:'b'.repeat(64),meshReceiptSha256:'c'.repeat(64),meshQuality:'accepted' as const,run:{uid:'physical-run',receiptSha256:'d'.repeat(64)}}
 const mesh={positions:new Float32Array([0,0,0,1,0,0,0,1,0]),indices:new Uint32Array([0,1,2]),edges:new Uint32Array([0,1,1,2,2,0]),patches:[{name:'wall',nPoints:3,nTriangles:1,nEdges:3,defaultVisible:true}],totals:{points:3,triangles:1,edges:3}}
 setLiveAirResultSelection({owner:'air-postprocess',session,surfaceField:'T',meshReceiptSha256:source.meshReceiptSha256})
 const d=new PostprocessDisplayDriver(createOvPostprocessAdapter({client:f.client as Parameters<typeof createOvPostprocessAdapter>[0]['client'],sceneIdle:async()=>{},scene:()=>({projectUid:f.project.uid,projectHash:f.hash,sceneRevision:1})}))
 d.present({layers:{surface:{visible:true,colorMap:'coolwarm',climMin:290,climMax:310,autoScale:false}},
  windFrame:{session,descriptor:{schema:'dcdtuni.verified-air-result-source/v1',postprocess:session,source,sourceFacesByPatch:{wall:['project-a/model-a/front']},fieldUnits:{T:'K'}},currentProject:{uid:f.project.uid,contentSha256:f.hash},mesh,activeSurfaceField:'T',surface:{field:'T',values:new Float32Array([299,300,301]),visible:true,colorMap:'coolwarm',range:[290,310],opacity:1,hidden:new Set()},slices:[],streamlines:[]},
  volume:{field:'T',unit:'K',visible:false,payload:null,range:[290,310],palette:'coolwarm',opacity:.5,resolution:128},internalVisible:true,handles:{ringSetId:'draft',seedSetId:'draft'},isCurrent:()=>true})
 return d
}
it('air replaces an ACKed numeric mesh without a late mesh route clear',async()=>{
 vi.useFakeTimers();meshFixture();const client=f.client as {loadResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>}
 await mountOv();expect(client.loadResultLayers).toHaveBeenCalledOnce()
 let d!:PostprocessDisplayDriver;await act(async()=>{d=presentAir();await d.whenIdle();await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadResultLayers).toHaveBeenCalledTimes(2);expect(client.clearResultLayers).not.toHaveBeenCalled();expect(d.status().status).toBe('displayed')
})
it('air waits behind the actual in-flight mesh and supersedes its pending hide without an air clear',async()=>{
 vi.useFakeTimers();const m=meshFixture(),client=f.client as {loadResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>}
 let release!:(value:unknown)=>void;client.loadResultLayers.mockImplementationOnce(()=>new Promise(done=>release=done));await mountOv()
 await act(async()=>{m.preview.mockReturnValue({...m.state,visible:false});clearResultLayerSet('mesh-preview')})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 let d!:PostprocessDisplayDriver;await act(async()=>{d=presentAir();for(let i=0;i<5;i++)await Promise.resolve()})
 expect(client.loadResultLayers).toHaveBeenCalledOnce();expect(client.clearResultLayers).not.toHaveBeenCalled()
 await act(async()=>{release({ok:true,requestId:'mesh',revision:1});await d.whenIdle();await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadResultLayers).toHaveBeenCalledTimes(2);expect(client.clearResultLayers).not.toHaveBeenCalled();expect(d.status().status).toBe('displayed')
})

it('a newly ACKed scene retires the old in-flight mesh owner before replaying its new mesh',async()=>{
 vi.useFakeTimers();meshFixture();const client=f.client as {loadResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>;loadScene:ReturnType<typeof vi.fn>}
 let release!:(value:unknown)=>void;client.loadResultLayers.mockImplementationOnce(()=>new Promise(done=>release=done));await mountOv()
 f.project={...f.project,uid:'project-b'}
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="mesh" airMode={true}/>))
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadScene).toHaveBeenCalledOnce()
 await act(async()=>{clearResultLayerSet();meshFixture()})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadResultLayers).toHaveBeenCalledOnce()
 await act(async()=>{release({ok:true,requestId:'old-mesh',revision:1});await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadResultLayers).toHaveBeenCalledTimes(2);expect(client.clearResultLayers).not.toHaveBeenCalled()
 expect((client.loadResultLayers.mock.calls[1]![0] as ResultLayerSet).source.projectUid).toBe('project-b')
})
