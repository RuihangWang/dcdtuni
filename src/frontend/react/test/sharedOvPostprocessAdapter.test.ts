import {afterEach,expect,it,vi} from 'vitest'
import {PostprocessDisplayDriver,type DisplayInput} from '../src/postprocess/displaySession'
import type {RenderAck,OvDisplayGrant} from '../src/renderer/ovProtocol'
import type {CommonRenderIntent,CanonicalRenderReply} from '../src/postprocess/commonRenderIntent'
import {bindNumericProduct} from '../src/postprocess/numericProducts'
import {createOvPostprocessAdapter} from '../src/renderer/ovPostprocessAdapter'
import {clearResultLayerSet,setLiveAirResultSelection} from '../src/renderer/resultLayerRevision'
const sha=(v:string)=>v.repeat(64)
function input(): DisplayInput {
 const session={runId:sha('9'),timeValue:0.015,revision:'pp-current'}
 const source={projectUid:'p',projectContentSha256:sha('a'),cfdrunSha256:sha('b'),meshReceiptSha256:sha('c'),meshQuality:'accepted' as const,run:{uid:'run-execution',receiptSha256:sha('d')}}
 const owned=<T extends object>(value:T,kind:'mesh'|'field')=>bindNumericProduct(value,new Response(null,{headers:{'X-PP-Product':JSON.stringify({schema:'dcdtuni.numeric-product-receipt/v1',token:sha(kind==='mesh'?'e':'f'),kind,postprocess:session,payloadSha256:sha('6'),nativeInputSha256:sha('5')})}}),kind,session)
 const mesh=owned({positions:new Float32Array([0,0,0,1,0,0,0,1,0]),indices:new Uint32Array([0,1,2]),edges:new Uint32Array([0,1,1,2,2,0]),patches:[{name:'wall',nPoints:3,nTriangles:1,nEdges:3,defaultVisible:true}],totals:{points:3,triangles:1,edges:3}},'mesh')
 setLiveAirResultSelection({owner:'air-postprocess',session,surfaceField:'T',meshReceiptSha256:sha('c')})
 return {layers:{surface:{visible:true,colorMap:'viridis',climMin:299,climMax:303,autoScale:false}},
  windFrame:{session,descriptor:{schema:'dcdtuni.verified-air-result-source/v2',postprocess:session,source,fieldUnits:{T:'K'},nativeInputSha256:sha('5'),runConvergence:{verdict:'NOT_CONVERGED',receiptSha256:sha('4')},boundaryProvenanceByPatch:{wall:{schema:'dcdtuni.native-boundary-provenance/v1',patchRanges:[{bindingKind:'meshBoundaryOnly',rank:0,patchIndex:0,nativePatchName:'wall',startFace:2,nFaces:1,boundarySha256:sha('3'),sourceRegions:[]}]}}},
    currentProject:{uid:'p',contentSha256:sha('a')},mesh,activeSurfaceField:'T',surface:{field:'T',values:owned(new Float32Array([300,301,302]),'field'),colorMap:'viridis',range:[299,303],opacity:0.23,visible:true,hidden:new Set()},slices:[],streamlines:[]},
  volume:{field:'T',unit:'K',visible:false,payload:null,range:[299,303],palette:'viridis',opacity:0.23,resolution:256},internalVisible:true,handles:{ringSetId:42,seedSetId:9},isCurrent:()=>true}
}
function setup() {
 let scene:{projectUid:string;projectHash:string;sceneRevision:number}|null={projectUid:'p',projectHash:sha('a'),sceneRevision:7}
 const requestGrant=vi.fn(async(intent:CommonRenderIntent,ackScene:{sceneRevision:number},generation:number,_signal:AbortSignal):Promise<CanonicalRenderReply>=>({grant:{payload:{intent,generation,sceneRevision:ackScene.sceneRevision},signature:sha('2')},packetReceipt:{sha256:sha('1'),revision:intent.revision,source:intent.source,timeSeconds:intent.postprocess.timeValue!,postprocess:intent.postprocess,sceneRevision:ackScene.sceneRevision,displayGeneration:generation,runConvergence:{verdict:'NOT_CONVERGED',receiptSha256:sha('4')}}}))
 const ack=(grant:OvDisplayGrant):RenderAck=>{const intent=grant.payload.intent as CommonRenderIntent;return {requestId:'ack',ok:true,revision:intent.revision,source:intent.source,timeSeconds:intent.postprocess.timeValue!,sceneRevision:grant.payload.sceneRevision as number,displayGeneration:grant.payload.generation as number,packetSha256:sha('1')}}
 const client={loadVerifiedResultLayers:vi.fn(async(grant:OvDisplayGrant)=>ack(grant)),clearResultLayers:vi.fn(async(_generation?:number)=>({requestId:'ack',ok:true}))}
 const idle=vi.fn(async()=>{})
 const adapter=createOvPostprocessAdapter({client,sceneIdle:idle,scene:()=>scene,requestGrant})
 return {client,idle,adapter,requestGrant,ack,setScene:(value:typeof scene)=>scene=value}
}
afterEach(()=>{clearResultLayerSet();setLiveAirResultSelection(null)})
it('lowers exactly the public accepted source/time and raw LUT without requesting another run',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);d.present(input());await d.whenIdle()
 const [intent,scene,generation]=s.requestGrant.mock.calls[0]!
 expect(intent.postprocess.timeValue).toBe(0.015);expect(scene.sceneRevision).toBe(7);expect(generation).toBe(d.current()!.generation)
 expect(intent.surface).toMatchObject({field:'T',lut:{palette:'viridis',range:[299,303],opacity:0.23}})
 expect(s.client.loadVerifiedResultLayers.mock.calls[0]![0]).toMatchObject({signature:sha('2')})
 expect(d.current()!.packet).toMatchObject({schema:'dcdtuni.render-result-layers/v2',runConvergence:{verdict:'NOT_CONVERGED'}})
 expect(d.status().status).toBe('displayed')
})
it('does not bypass missing accepted mesh/run provenance for a diagnostic frame',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();v.windFrame=null;d.present(v);await d.whenIdle()
 expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled();expect(d.status()).toMatchObject({status:'rejected'})
})
it('requires the currently acknowledged project content and scene generation',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);s.setScene({projectUid:'other',projectHash:sha('a'),sceneRevision:7})
 d.present(input());await d.whenIdle();expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled()
 s.setScene({projectUid:'p',projectHash:sha('f'),sceneRevision:7});d.present(input());await d.whenIdle();expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled()
})
it('refuses an independently changed accepted session before any OV transfer',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();d.present(v)
 setLiveAirResultSelection({owner:'air-postprocess',session:{...v.windFrame!.session,revision:'pp-new'},surfaceField:'T',meshReceiptSha256:sha('c')})
 await d.whenIdle();expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled();expect(d.status().status).toBe('rejected')
})
it('sends the receipt-bound shared volume parameters through the canonical grant and verifies its ACK',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();v.volume.visible=true;v.volume.resolution=16
 const payload={dimensions:[16,16,16]as [number,number,number],origin:[1,2,3]as [number,number,number],spacing:[.1,.2,.3]as [number,number,number],scalarRange:[299,303]as [number,number],sentinel:-9999,scalars:new Float32Array(16**3).fill(300)}
 bindNumericProduct(payload,new Response(null,{headers:{'X-PP-Product':JSON.stringify({schema:'dcdtuni.numeric-product-receipt/v1',token:sha('8'),kind:'volume',postprocess:v.windFrame!.session,payloadSha256:sha('6'),nativeInputSha256:sha('5')})}}),'volume',v.windFrame!.session)
 v.volume.payload=payload
 d.present(v);await d.whenIdle()
 expect(s.requestGrant.mock.calls[0]![0].volume).toEqual({field:'T',unit:'K',token:sha('8'),resolution:16,lut:{palette:'viridis',range:[299,303],opacity:0.23},visible:true})
 expect(s.client.loadVerifiedResultLayers).toHaveBeenCalledOnce();expect(d.status().status).toBe('displayed');expect(d.current()!.volume.payload).toBe(payload)
 v.volume.visible=false;d.present(v);await d.whenIdle()
 expect(s.requestGrant.mock.calls[1]![0].volume).toMatchObject({token:sha('8'),visible:false})
 expect(s.client.loadVerifiedResultLayers).toHaveBeenCalledTimes(2);expect(d.status().status).toBe('displayed')
})
it('rejects a volume without an actual matching response receipt before requesting a signature',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();v.volume.visible=true;v.volume.resolution=16
 v.volume.payload={dimensions:[16,16,16],origin:[0,0,0],spacing:[1,1,1],scalarRange:[299,303],sentinel:-9999,scalars:new Float32Array(16**3).fill(300)}
 d.present(v);await d.whenIdle();expect(d.status().status).toBe('rejected');expect(s.requestGrant).not.toHaveBeenCalled();expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled()
})
it('reports pending preview conversion with concrete list identity without re-tracing a singleton',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();v.layers.ring={show:true,origin:[0,1,0],normal:[0,1,0],radius:2};v.layers.seed={show:true,centre:[0,1,0],radius:1}
 d.present(v);await d.whenIdle();expect(d.status().message).toContain('handle 42');expect(d.status().message).toContain('handle 9')
 expect(s.client.loadVerifiedResultLayers).toHaveBeenCalledOnce()
})
it('keeps Kit provenance rejection visible instead of reporting an optimistically displayed frame',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);s.client.loadVerifiedResultLayers.mockRejectedValueOnce(new Error('Trusted renderer provenance is unavailable'))
 d.present(input());await d.whenIdle();expect(d.status()).toMatchObject({status:'rejected',message:'Trusted renderer provenance is unavailable'})
})
it('does not certify a late ACK after a new project stage is acknowledged',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);let resolve!:(value:{requestId:string;ok:boolean;revision:number})=>void
 s.client.loadVerifiedResultLayers.mockImplementationOnce(()=>new Promise(done=>resolve=done));d.present(input())
 for(let i=0;i<12;i++)await Promise.resolve();const revision=d.current()!.packet!.revision
 s.setScene({projectUid:'p',projectHash:sha('a'),sceneRevision:8});resolve({requestId:'ack',ok:true,revision});await d.whenIdle()
 expect(d.status().status).toBe('rejected')
})
it('makes a disposed adapter inert while waiting for scene idle, with no new mutation',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);let release!:()=>void;s.idle.mockImplementationOnce(()=>new Promise(done=>release=done));d.present(input())
 await Promise.resolve();s.adapter.dispose();release();await d.whenIdle();expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled()
})

it('serializes an already in-flight old clear before the new accepted air author on the same client',async()=>{
 const s=setup();let release!:(value:{requestId:string;ok:boolean})=>void;const stage:string[]=[]
 s.client.clearResultLayers.mockImplementationOnce(async()=>{await new Promise<{requestId:string;ok:boolean}>(done=>release=done);stage.push('old-clear');return {requestId:'clear',ok:true}})
 s.client.loadVerifiedResultLayers.mockImplementation(async(grant)=>{stage.push('new-air');return s.ack(grant)})
 const previous=new PostprocessDisplayDriver(s.adapter);previous.present(input());await previous.whenIdle();s.client.loadVerifiedResultLayers.mockClear();stage.length=0
 const cleared=s.adapter.clear({isCurrent:()=>true});for(let i=0;i<4;i++)await Promise.resolve()
 const d=new PostprocessDisplayDriver(s.adapter);d.present(input());for(let i=0;i<6;i++)await Promise.resolve()
 expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled()
 release({requestId:'clear',ok:true});await cleared;await d.whenIdle()
 expect(stage).toEqual(['old-clear','new-air']);expect(d.status().status).toBe('displayed')
})

it('rejects actual numeric responses from a different native frame before requesting a signature',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();v.windFrame!.surface.values=new Float32Array([300,301,302]);d.present(v);await d.whenIdle()
 expect(s.requestGrant).not.toHaveBeenCalled();expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled();expect(d.status().status).toBe('rejected')
})
it('a pending grant cannot submit after selection changed, while the next common input can render',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);let release!:(value:CanonicalRenderReply)=>void
 const normal=s.requestGrant.getMockImplementation()!;s.requestGrant.mockImplementationOnce(()=>new Promise(done=>release=done));d.present(input());for(let i=0;i<8;i++)await Promise.resolve()
 const previous=s.requestGrant.mock.calls[0]!;d.present(input());release(await normal(...previous));await d.whenIdle()
 expect(s.client.loadVerifiedResultLayers).toHaveBeenCalledOnce();expect(d.status().status).toBe('displayed')
})
it('checks canonical packet hash, source and frame acknowledgement rather than only the revision',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);s.client.loadVerifiedResultLayers.mockImplementationOnce(async(grant)=>({...s.ack(grant),packetSha256:sha('0')}))
 d.present(input());await d.whenIdle();expect(d.status()).toMatchObject({status:'rejected'});expect(d.status().message).toContain('acknowledgement differs')
})
it('common clear carries the public generation and disposals abort the pending grant request',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);d.present(input());await d.whenIdle();d.clear();await d.whenIdle()
 expect(s.client.clearResultLayers).toHaveBeenCalledWith(2)
 s.adapter.dispose()
})

it.each(['physical time','source run','source project','scene generation','display generation'] as const)(
 'rejects a valid-revision/hash ACK from another %s',async(mismatch)=>{
  const s=setup(),d=new PostprocessDisplayDriver(s.adapter)
  s.client.loadVerifiedResultLayers.mockImplementationOnce(async(grant)=>{
   const ack=s.ack(grant)
   if(mismatch==='physical time')return {...ack,timeSeconds:0.02}
   if(mismatch==='source run')return {...ack,source:{...ack.source!,run:{uid:'different-execution',receiptSha256:sha('d')}}}
   if(mismatch==='source project')return {...ack,source:{...ack.source!,projectUid:'different-project'}}
   if(mismatch==='scene generation')return {...ack,sceneRevision:8}
   return {...ack,displayGeneration:(ack.displayGeneration??0)+1}
  })
  d.present(input());await d.whenIdle()
  expect(s.client.loadVerifiedResultLayers).toHaveBeenCalledOnce()
  expect(d.status()).toMatchObject({status:'rejected'})
  expect(d.status().message).toContain('acknowledgement differs')
 })
it('aborts its actual pending canonical grant on disposal and never authors the late reply',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);let release!:(value:CanonicalRenderReply)=>void
 const normal=s.requestGrant.getMockImplementation()!
 s.requestGrant.mockImplementationOnce(()=>new Promise(done=>release=done))
 d.present(input());for(let i=0;i<12;i++)await Promise.resolve()
 expect(s.requestGrant).toHaveBeenCalledOnce()
 const args=s.requestGrant.mock.calls[0]!,signal=args[3]
 expect(signal.aborted).toBe(false);s.adapter.dispose();expect(signal.aborted).toBe(true)
 release(await normal(...args));await d.whenIdle()
 expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled()
 expect(d.status().status).toBe('rejected')
})
