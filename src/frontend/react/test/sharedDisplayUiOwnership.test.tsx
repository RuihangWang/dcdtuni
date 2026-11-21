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
import {publishMeshPreviewEdges}from '../src/renderer/windResultProducer'
import {bindNumericProduct}from '../src/postprocess/numericProducts'
import type {CfdProject} from '../src/schema/cfdproj'
const f=vi.hoisted(()=>({project:{uid:'project-a',meta:{name:'controlled-ui',casePath:''}},hash:'a'.repeat(64),client:null as unknown}))
vi.mock('../src/projectStore',async(original)=>({...await original<typeof import('../src/projectStore')>(),getProject:()=>f.project,subscribe:()=>()=>{},revision:()=>0}))
vi.mock('../src/legacy/cfdrunBuild',()=>({cfdrunExport:()=>({projectContentHashSync:()=>f.hash})}))
vi.mock('../src/viewportBus',()=>({getViewportHandle:()=>({camera:{},controls:{target:{},update(){}}}),subscribeCameraSettled:()=>()=>{}}))
vi.mock('../src/renderer/sceneSnapshot',()=>({buildSceneSnapshot:()=>({project:{uid:f.project.uid},primitives:[],textures:{},warnings:[],view:{timeSeconds:null,selectedUids:[]}}),captureViewState:()=>({})}))
vi.mock('../src/renderer/useSharedPostprocessRenderer',()=>({useSharedPostprocessRenderer(){}}))
vi.mock('../src/renderer/meshPreviewOverlay',()=>({buildMeshPreviewOverlay:()=>({}),meshOverlayRevision:()=>0}))
vi.mock('../src/renderer/ovEditBridge',()=>({createOvEditBridge:()=>({apply(){}})}))
vi.mock('../src/renderer/OvViewport',()=>({OvViewport:(props:{snapshot:()=>unknown;onReady:(value:unknown,ack:unknown,packet:unknown)=>void})=>{
 useEffect(()=>{const client=f.client;props.onReady(client,{ok:true,requestId:'controlled',projectHash:f.hash,sceneRevision:1},props.snapshot())},[])
 return <div data-controlled-ov-viewport="true"/>
}}))
let host:HTMLDivElement,root:Root
beforeEach(()=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
 f.project={uid:'project-a',meta:{name:'controlled-ui',casePath:''}}
 // Controlled contract identity only: these mocked replies are NOT native run evidence.
 vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{
  if(url!=='/api/mesh-preview/render-grant')throw new Error('unexpected controlled grant URL')
  const request=JSON.parse(String(options.body)),intent=request.intent
  return new Response(JSON.stringify({grant:{payload:{kind:'rendererMesh',revision:intent.revision,source:intent.source,timeSeconds:null,sceneRevision:request.scene.revision,displayGeneration:request.displayGeneration,packetSha256:'3'.repeat(64)},signature:'2'.repeat(64)},packetReceipt:{sha256:'3'.repeat(64),revision:intent.revision,source:intent.source,timeSeconds:null,meshPreview:{meshSnapshotKey:intent.meshSnapshotKey,meshPayloadSha256:intent.meshPayloadSha256,meshMetadataSha256:'f'.repeat(64)},sceneRevision:request.scene.revision,displayGeneration:request.displayGeneration}}))
 }))
 f.client={onEditEvent(){},clearResultLayers:vi.fn(async()=>({ok:true,requestId:'controlled'})),loadResultLayers:vi.fn(()=>{throw new Error('unsigned transport is forbidden')}),loadVerifiedResultLayers:vi.fn(async(grant:any)=>signedAck(grant)),loadScene:vi.fn(async(_packet:unknown,hash:string)=>({ok:true,requestId:'controlled',projectHash:hash,sceneRevision:2})),loadOverlay:async()=>({ok:true,requestId:'controlled'}),readView:async()=>({ok:true,requestId:'controlled'})}
 setOvSession({mode:'three',ready:false,degraded:false,client:null,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
 ;(window as unknown as {globalGroup:unknown}).globalGroup={}
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(()=>{act(()=>root.unmount());clearResultLayerSet();setLiveAirResultSelection(null);vi.restoreAllMocks();vi.useRealTimers();host.remove();delete (window as {globalGroup?:unknown}).globalGroup;vi.unstubAllGlobals()})
it('has no independent renderer-specific product controls with Three selected',async()=>{
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="postprocess" airMode={true}/>))
 expect(host.querySelector('fieldset,.ov-postprocess-panel,#ov-air-time,input[name="ov-volume"],[data-ov-dataset],[data-ov-liquid-painted]')).toBeNull()
 expect(host.querySelectorAll('select')).toHaveLength(1)
})
it('switching the carrier to OV does not mount a second product run/frame/settings UI',async()=>{
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="postprocess" airMode={true}/>))
 const select=host.querySelector<HTMLSelectElement>('select')!
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,'ov');select.dispatchEvent(new Event('change',{bubbles:true}))})
 expect(host.querySelector('[data-controlled-ov-viewport]')).not.toBeNull()
 expect(host.querySelector('fieldset,.ov-postprocess-panel,#ov-air-time,input[name="ov-volume"],[data-ov-dataset],[data-ov-liquid-painted]')).toBeNull()
 expect(host.querySelectorAll('select')).toHaveLength(1)
})

it('the retained mesh route never clears the air owner when the common selection is null',async()=>{
 vi.useFakeTimers()
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="postprocess" airMode={true}/>))
 const select=host.querySelector<HTMLSelectElement>('select')!
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,'ov');select.dispatchEvent(new Event('change',{bubbles:true}))})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect((f.client as {clearResultLayers:ReturnType<typeof vi.fn>}).clearResultLayers).not.toHaveBeenCalled()
})

function signedAck(grant:any) {return {requestId:'controlled',ok:true,...grant.payload}}
function meshFixture() {
 const key='e'.repeat(64),source={projectUid:f.project.uid,projectContentSha256:f.hash,cfdrunSha256:'b'.repeat(64),meshReceiptSha256:'c'.repeat(64),meshQuality:'accepted' as const,run:null}
 const mesh={positions:new Float32Array([0,0,0,1,0,0,0,1,0]),indices:new Uint32Array([0,1,2]),edges:new Uint32Array([0,1,1,2,2,0]),patches:[{name:'wall',nPoints:3,nTriangles:1,nEdges:3,defaultVisible:true}],totals:{points:3,triangles:1,edges:3}}
 const proof={schema:'dcdtuni.native-boundary-provenance/v1' as const,patchRanges:[{bindingKind:'meshBoundaryOnly' as const,rank:0,patchIndex:0,nativePatchName:'wall',startFace:2,nFaces:1,boundarySha256:'1'.repeat(64),sourceRegions:[]}]}
 const packet=publishMeshPreviewEdges({descriptor:{schema:'dcdtuni.verified-mesh-preview-source/v2',meshSnapshotKey:key,meshPayloadSha256:'d'.repeat(64),meshMetadataSha256:'f'.repeat(64),source,boundaryProvenanceByPatch:{wall:proof}},meshSnapshotKey:key,currentProject:{uid:f.project.uid,contentSha256:f.hash},mesh,hidden:new Set(),opacity:.5})
 const original=meshPreview.getMeshPreviewSession(),state={...original,visible:true,built:{} as NonNullable<typeof original.built>,meshSnapshotKey:key}
 const preview=vi.spyOn(meshPreview,'getMeshPreviewSession').mockReturnValue(state)
 return {preview,state,packet,source,key,mesh,proof}
}
async function mountOv() {
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="mesh" airMode={true}/>))
 const select=host.querySelector<HTMLSelectElement>('select')!
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,'ov');select.dispatchEvent(new Event('change',{bubbles:true}))})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
}
it('clears the actual numeric mesh carrier after the public mesh preview is hidden',async()=>{
 vi.useFakeTimers();const m=meshFixture(),client=f.client as {loadVerifiedResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>}
 await mountOv();expect(client.loadVerifiedResultLayers).toHaveBeenCalledOnce()
 await act(async()=>{m.preview.mockReturnValue({...m.state,visible:false});clearResultLayerSet('mesh-preview')})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect(client.clearResultLayers).toHaveBeenCalledOnce()
})

it('hiding during the actual mesh carrier transfer clears it after the mesh ACK',async()=>{
 vi.useFakeTimers();const m=meshFixture(),client=f.client as {loadVerifiedResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>}
 let release!:(value:unknown)=>void;client.loadVerifiedResultLayers.mockImplementationOnce(()=>new Promise(done=>release=done))
 await mountOv();expect(client.loadVerifiedResultLayers).toHaveBeenCalledOnce()
 await act(async()=>{m.preview.mockReturnValue({...m.state,visible:false});clearResultLayerSet('mesh-preview')})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect(client.clearResultLayers).not.toHaveBeenCalled()
 await act(async()=>{release(signedAck(client.loadVerifiedResultLayers.mock.calls[0]![0]));await vi.advanceTimersByTimeAsync(0)})
 expect(client.clearResultLayers).toHaveBeenCalledOnce()
})
function presentAir() {
 const session={runId:'9'.repeat(64),timeValue:.015,revision:'accepted-r1'},source={projectUid:f.project.uid,projectContentSha256:f.hash,cfdrunSha256:'b'.repeat(64),meshReceiptSha256:'c'.repeat(64),meshQuality:'accepted' as const,run:{uid:'physical-run',receiptSha256:'d'.repeat(64)}}
 const mesh={positions:new Float32Array([0,0,0,1,0,0,0,1,0]),indices:new Uint32Array([0,1,2]),edges:new Uint32Array([0,1,1,2,2,0]),patches:[{name:'wall',nPoints:3,nTriangles:1,nEdges:3,defaultVisible:true}],totals:{points:3,triangles:1,edges:3}}
 setLiveAirResultSelection({owner:'air-postprocess',session,surfaceField:'T',meshReceiptSha256:source.meshReceiptSha256})
 const requestGrant=vi.fn(async(intent:any,scene:any,generation:number)=>({grant:{payload:{kind:'rendererResult',revision:intent.revision,source,timeSeconds:.015,sceneRevision:scene.sceneRevision,displayGeneration:generation,packetSha256:'3'.repeat(64)},signature:'2'.repeat(64)},packetReceipt:{sha256:'3'.repeat(64),revision:intent.revision,source,timeSeconds:.015,postprocess:session,sceneRevision:scene.sceneRevision,displayGeneration:generation,runConvergence:{verdict:'NOT_CONVERGED' as const,receiptSha256:'1'.repeat(64)}}}))
 const owned=<T extends object>(value:T,kind:'mesh'|'field')=>bindNumericProduct(value,new Response(null,{headers:{'X-PP-Product':JSON.stringify({schema:'dcdtuni.numeric-product-receipt/v1',token:(kind==='mesh'?'5':'6').repeat(64),kind,postprocess:session,payloadSha256:'7'.repeat(64),nativeInputSha256:'8'.repeat(64)})}}),kind,session)
 const proof={schema:'dcdtuni.native-boundary-provenance/v1' as const,patchRanges:[{bindingKind:'meshBoundaryOnly' as const,rank:0,patchIndex:0,nativePatchName:'wall',startFace:2,nFaces:1,boundarySha256:'1'.repeat(64),sourceRegions:[]}]}
 const d=new PostprocessDisplayDriver(createOvPostprocessAdapter({client:f.client as Parameters<typeof createOvPostprocessAdapter>[0]['client'],sceneIdle:async()=>{},scene:()=>({projectUid:f.project.uid,projectHash:f.hash,sceneRevision:1}),requestGrant}))
 d.present({layers:{surface:{visible:true,colorMap:'coolwarm',climMin:290,climMax:310,autoScale:false}},
  windFrame:{session,descriptor:{schema:'dcdtuni.verified-air-result-source/v2',postprocess:session,source,nativeInputSha256:'8'.repeat(64),runConvergence:{verdict:'NOT_CONVERGED',receiptSha256:'1'.repeat(64)},boundaryProvenanceByPatch:{wall:proof},fieldUnits:{T:'K'}},currentProject:{uid:f.project.uid,contentSha256:f.hash},mesh:owned(mesh,'mesh'),activeSurfaceField:'T',surface:{field:'T',values:owned(new Float32Array([299,300,301]),'field'),visible:true,colorMap:'coolwarm',range:[290,310],opacity:1,hidden:new Set()},slices:[],streamlines:[]},
  volume:{field:'T',unit:'K',visible:false,payload:null,range:[290,310],palette:'coolwarm',opacity:.5,resolution:128},internalVisible:true,handles:{ringSetId:'draft',seedSetId:'draft'},isCurrent:()=>true})
 return d
}
it('air replaces an ACKed numeric mesh without a late mesh route clear',async()=>{
 vi.useFakeTimers();meshFixture();const client=f.client as {loadVerifiedResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>}
 await mountOv();expect(client.loadVerifiedResultLayers).toHaveBeenCalledOnce()
 let d!:PostprocessDisplayDriver;await act(async()=>{d=presentAir();await d.whenIdle();await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadVerifiedResultLayers).toHaveBeenCalledTimes(2);expect(client.clearResultLayers).not.toHaveBeenCalled();expect(d.status().status).toBe('displayed')
})
it('air waits behind the actual in-flight mesh and supersedes its pending hide without an air clear',async()=>{
 vi.useFakeTimers();const m=meshFixture(),client=f.client as {loadVerifiedResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>}
 let release!:(value:unknown)=>void;client.loadVerifiedResultLayers.mockImplementationOnce(()=>new Promise(done=>release=done));await mountOv()
 await act(async()=>{m.preview.mockReturnValue({...m.state,visible:false});clearResultLayerSet('mesh-preview')})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 let d!:PostprocessDisplayDriver;await act(async()=>{d=presentAir();for(let i=0;i<5;i++)await Promise.resolve()})
 expect(client.loadVerifiedResultLayers).toHaveBeenCalledOnce();expect(client.clearResultLayers).not.toHaveBeenCalled()
 await act(async()=>{release(signedAck(client.loadVerifiedResultLayers.mock.calls[0]![0]));await d.whenIdle();await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadVerifiedResultLayers).toHaveBeenCalledTimes(2);expect(client.clearResultLayers).not.toHaveBeenCalled();expect(d.status().status).toBe('displayed')
})

it('a newly ACKed scene retires the old in-flight mesh owner before replaying its new mesh',async()=>{
 vi.useFakeTimers();meshFixture();const client=f.client as {loadVerifiedResultLayers:ReturnType<typeof vi.fn>;clearResultLayers:ReturnType<typeof vi.fn>;loadScene:ReturnType<typeof vi.fn>}
 let release!:(value:unknown)=>void;client.loadVerifiedResultLayers.mockImplementationOnce(()=>new Promise(done=>release=done));await mountOv()
 f.project={...f.project,uid:'project-b'}
 await act(async()=>root.render(<RendererControl project={f.project as CfdProject} selectedUids={[]} onSelection={()=>{}} activeTab="mesh" airMode={true}/>))
 // The resync keys the scene through crypto.subtle before loading it; that
 // settles on its own clock, not within one fake-timer step.
 await act(async()=>{await vi.advanceTimersByTimeAsync(350);await vi.waitFor(()=>expect(client.loadScene).toHaveBeenCalledOnce())})
 await act(async()=>{clearResultLayerSet();meshFixture()})
 await act(async()=>{await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadVerifiedResultLayers).toHaveBeenCalledOnce()
 await act(async()=>{release(signedAck(client.loadVerifiedResultLayers.mock.calls[0]![0]));await vi.advanceTimersByTimeAsync(350)})
 expect(client.loadVerifiedResultLayers).toHaveBeenCalledTimes(2);expect(client.clearResultLayers).not.toHaveBeenCalled()
 expect((client.loadVerifiedResultLayers.mock.calls[1]![0] as any).payload.source.projectUid).toBe('project-b')
})

it('the former unsigned v1 mesh fixture remains rejected by the actual mounted route',async()=>{
 vi.useFakeTimers();const m=meshFixture();const source={...m.source,meshQuality:'diagnostic' as const}
 const old:ResultLayerSet={schema:'dcdtuni.render-result-layers/v1',revision:1,source,timeSeconds:null,coordinateSystem:{upAxis:'Y',metersPerUnit:1,handedness:'right'},layers:[{kind:'meshEdges',layerId:'wall',visible:true,meshPatchName:'wall',sourceFaceUids:['controlled-legacy-face'],positions:[0,0,0,1,0,0],indices:[0,1],opacity:.5}]}
 publishResultLayerSet(old,source,null,undefined,'mesh-preview',{owner:'mesh-preview',meshSnapshotKey:m.key})
 await mountOv()
 expect((f.client as any).loadVerifiedResultLayers).not.toHaveBeenCalled();expect((f.client as any).loadResultLayers).not.toHaveBeenCalled();expect(fetch).not.toHaveBeenCalled()
})
