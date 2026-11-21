import {readFileSync} from 'node:fs'
import {decodeVolume} from '../src/postprocess/ppVolume'
import type{CommonRenderIntent,CanonicalRenderReply}from '../src/postprocess/commonRenderIntent'
import{bindNumericProduct}from '../src/postprocess/numericProducts'
import{createOvPostprocessAdapter}from '../src/renderer/ovPostprocessAdapter'
import{setLiveAirResultSelection,currentResultLayerOwner}from '../src/renderer/resultLayerRevision'
import type{RenderAck}from '../src/renderer/ovProtocol'
import {afterEach,expect,it,vi}from 'vitest'
import {webcrypto}from 'node:crypto'
import {publishMeshPreviewEdges}from '../src/renderer/windResultProducer'
import {currentResultLayerContext,clearResultLayerSet}from '../src/renderer/resultLayerRevision'
import {parseResultLayerSet,serializeResultLayerSet}from '../src/renderer/resultLayers'
import {assertMeshPreviewBytes,transferMeshPreview,type MeshPreviewIntent,type MeshPreviewGrantReply}from '../src/renderer/meshPreviewGrant'
import {setOvResultStage,clearOwnedOvResultLayers,enqueueOvResultMutation}from '../src/renderer/ovResultMutationQueue'
import {PostprocessDisplayDriver,type DisplayInput}from '../src/postprocess/displaySession'
const sha=(v:string)=>v.repeat(64)
const source={projectUid:'p',projectContentSha256:sha('a'),cfdrunSha256:sha('b'),meshReceiptSha256:sha('c'),meshQuality:'accepted'as const,run:null}
const scene={projectUid:'p',projectHash:sha('a'),sceneRevision:7}
function fixture(){
 const mesh={positions:new Float32Array([0,0,0,1,0,0,0,1,0]),indices:new Uint32Array([0,1,2]),edges:new Uint32Array([0,1,1,2,2,0]),patches:[{name:'nativeBoundary',nPoints:3,nTriangles:1,nEdges:3,defaultVisible:true}],totals:{points:3,triangles:1,edges:3}}
 const packet=publishMeshPreviewEdges({descriptor:{schema:'dcdtuni.verified-mesh-preview-source/v2',meshSnapshotKey:sha('d'),meshPayloadSha256:sha('e'),meshMetadataSha256:sha('f'),source,boundaryProvenanceByPatch:{nativeBoundary:{schema:'dcdtuni.native-boundary-provenance/v1',patchRanges:[{bindingKind:'meshBoundaryOnly',rank:0,patchIndex:0,nativePatchName:'nativeBoundary',startFace:2,nFaces:1,boundarySha256:sha('1'),sourceRegions:[]}]}}},meshSnapshotKey:sha('d'),currentProject:{uid:'p',contentSha256:sha('a')},mesh,hidden:new Set(),opacity:.23})
 const selection=currentResultLayerContext()!.selection!;if(selection.owner!=='mesh-preview'||!selection.intent)throw new Error('missing common mesh intent')
 const intent=selection.intent
 const requestGrant=vi.fn(async(value:MeshPreviewIntent,_scene:typeof scene,generation:number):Promise<MeshPreviewGrantReply>=>({grant:{payload:{kind:'rendererMesh',generation},signature:sha('2')},packetReceipt:{sha256:sha('3'),revision:value.revision,source,timeSeconds:null,meshPreview:{meshSnapshotKey:sha('d'),meshPayloadSha256:sha('e'),meshMetadataSha256:sha('f')},sceneRevision:7,displayGeneration:generation}}))
 const client={loadVerifiedResultLayers:vi.fn(async():Promise<RenderAck>=>({requestId:'ack',ok:true,revision:packet.revision,source,timeSeconds:null,sceneRevision:7,displayGeneration:2,packetSha256:sha('3')})),clearResultLayers:vi.fn(async()=>({requestId:'clear',ok:true}))}
 setOvResultStage(client,scene)
 return {packet,intent,client,requestGrant,mesh}
}
afterEach(()=>{clearResultLayerSet();setLiveAirResultSelection(null);vi.unstubAllGlobals()})
it('public mesh preview preserves native faces/edges and presentation without scalar or invented OO face identities',()=>{
 const {packet,intent}=fixture();expect(packet.schema).toBe('dcdtuni.render-result-layers/v4');expect(packet.source.run).toBeNull();expect(packet.timeSeconds).toBeNull();expect(packet).not.toHaveProperty('runConvergence')
 expect(packet.layers.map(l=>l.kind)).toEqual(['meshFaces','meshEdges']);expect(packet.layers.map(l=>l.kind==='meshFaces'||l.kind==='meshEdges'?l.opacity:0)).toEqual([.23,.35])
 for(const layer of packet.layers){expect(layer).not.toHaveProperty('values');expect(layer).not.toHaveProperty('field');expect(layer).not.toHaveProperty('sourceFaceUids')}
 expect(intent.opacity).toBe(.23);expect(intent.hidden).toEqual([]);expect(parseResultLayerSet(serializeResultLayerSet(packet))).toEqual(packet)
})
it('uses signed-only load on the same mesh/air mutation queue and checks source, null-time, hash and current generation',async()=>{
 const s=fixture();const ack=await transferMeshPreview({...s,scene,generation:2,isCurrent:()=>true})
 expect(s.requestGrant).toHaveBeenCalledWith(s.intent,scene,2);expect(s.client.loadVerifiedResultLayers).toHaveBeenCalledOnce();expect(ack?.timeSeconds).toBeNull()
 s.client.loadVerifiedResultLayers.mockResolvedValueOnce({...ack!,displayGeneration:1})
 await expect(transferMeshPreview({...s,scene,generation:2,isCurrent:()=>true})).rejects.toThrow(/acknowledgement/)
})
it('a delayed old grant never authors after mesh hide or mesh-to-air selection replaces its public source',async()=>{
 const s=fixture();let current=true;let release!:(value:MeshPreviewGrantReply)=>void
 const normal=s.requestGrant.getMockImplementation()!;s.requestGrant.mockImplementationOnce(()=>new Promise(done=>release=done))
 const pending=transferMeshPreview({...s,scene,generation:2,isCurrent:()=>current});await Promise.resolve();current=false
 release(await normal(s.intent,scene,2));expect(await pending).toBeUndefined();expect(s.client.loadVerifiedResultLayers).not.toHaveBeenCalled()
})
it('in-flight mesh author precedes its owned hide clear and the following air author in the same actual FIFO',async()=>{
 const s=fixture();const order:string[]=[];let release!:(value:Awaited<ReturnType<typeof s.client.loadVerifiedResultLayers>>)=>void
 s.client.loadVerifiedResultLayers.mockImplementationOnce(()=>new Promise(done=>release=done))
 const pending=transferMeshPreview({...s,scene,generation:2,isCurrent:()=>true});for(let i=0;i<8;i++)await Promise.resolve()
 const clear=clearOwnedOvResultLayers(s.client,'mesh-preview',()=>true,async()=>{order.push('mesh-hide');return {requestId:'clear',ok:true}})
 const air=enqueueOvResultMutation(s.client,()=>true,async()=>{order.push('air-author');return {requestId:'air',ok:true}},'air-postprocess')
 release({requestId:'mesh',ok:true,revision:s.packet.revision,source,timeSeconds:null,sceneRevision:7,displayGeneration:2,packetSha256:sha('3')});await pending;await clear;await air
 expect(order).toEqual(['mesh-hide','air-author'])
})
it('signed mesh preview bytes are checked against the actual downloaded numeric buffer',async()=>{
 // Node's webcrypto refuses an ArrayBuffer made in the jsdom realm (Node 20: "not instance of
 // ArrayBuffer"); a browser has one realm. Buffer.alloc gives Node-realm bytes of exact length.
 vi.stubGlobal('crypto',webcrypto);const data=Buffer.alloc(12);[1,2,3].forEach((v,i)=>data.writeFloatLE(v,i*4))
 const bytes=data.buffer as ArrayBuffer;const digest=Array.from(new Uint8Array(await webcrypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('')
 const descriptor={schema:'dcdtuni.verified-mesh-preview-source/v2'as const,meshSnapshotKey:sha('d'),meshPayloadSha256:digest,meshMetadataSha256:sha('f'),source,boundaryProvenanceByPatch:{}}
 await assertMeshPreviewBytes(bytes,descriptor);data.writeFloatLE(10,0);await expect(assertMeshPreviewBytes(bytes,descriptor)).rejects.toThrow(/bytes differ/)
})
it('shared display generation retires the former air input for mesh and remains monotonic for the next public input',async()=>{
 const adapter={kind:'three'as const,render:vi.fn(()=>({status:'displayed'as const})),clear:vi.fn(),dispose:vi.fn()};const driver=new PostprocessDisplayDriver(adapter)
 const input:DisplayInput={layers:{surface:{visible:false,colorMap:'jet',climMin:290,climMax:310,autoScale:false}},windFrame:null,volume:{field:'T',unit:'K',visible:false,payload:null,range:[290,310],palette:'jet',opacity:.2,resolution:16},internalVisible:false,handles:{ringSetId:'draft',seedSetId:'draft'},isCurrent:()=>true}
 expect(driver.present(input).generation).toBe(1);expect(driver.takeMeshDisplayOwnership()).toBe(2);expect(driver.current()).toBeNull();expect(driver.present(input).generation).toBe(3)
})
it('native mesh-only schema rejects a scalar layer, accepted run or invented physical time',()=>{
 const {packet}=fixture()
 for(const change of ['scalar','run','time']){
  const next=structuredClone(packet)as unknown as Record<string,unknown>
  if(change==='scalar')(next.layers as Record<string,unknown>[])[0]!.kind='surfaceField'
  else if(change==='run')(next.source as Record<string,unknown>).run={uid:'fake',receiptSha256:sha('1')}
  else next.timeSeconds=1
  expect(()=>parseResultLayerSet(JSON.stringify(next))).toThrow()
 }
})

it.each([false,true])('mesh-to-air and common masked volume=%s use the same public generation and actual shared adapter/FIFO',async(withVolume)=>{
 const s=fixture();const driver=new PostprocessDisplayDriver({kind:'three',render:()=>({status:'displayed'}),clear:()=>{},dispose:()=>{}})
 const diagnostic:DisplayInput={layers:{surface:{visible:false,colorMap:'jet',climMin:290,climMax:310,autoScale:false}},windFrame:null,volume:{field:'T',unit:'K',visible:false,payload:null,range:[290,310],palette:'jet',opacity:.2,resolution:16},internalVisible:false,handles:{ringSetId:'draft',seedSetId:'draft'},isCurrent:()=>true}
 driver.present(diagnostic);const meshGeneration=driver.takeMeshDisplayOwnership();expect(meshGeneration).toBe(2)
 await transferMeshPreview({...s,scene,generation:meshGeneration,isCurrent:()=>true})
 const session={runId:sha('9'),timeValue:.02,revision:'controlled-air-after-mesh'},airSource={...source,run:{uid:'CONTROLLED execution only',receiptSha256:sha('4')}}
 const owned=<T extends object>(value:T,kind:'mesh'|'field'|'volume')=>bindNumericProduct(value,new Response(null,{headers:{'X-PP-Product':JSON.stringify({schema:'dcdtuni.numeric-product-receipt/v1',token:sha(kind==='mesh'?'5':kind==='field'?'6':'a'),kind,postprocess:session,payloadSha256:sha('7'),nativeInputSha256:sha('8')})}}),kind,session)
 const first=s.packet.layers[0]!;if(first.kind!=='meshFaces'||!first.boundaryProvenance)throw new Error('missing native mesh proof');const proof=first.boundaryProvenance
 const requestGrant=vi.fn(async(intent:CommonRenderIntent,ackScene:typeof scene,generation:number):Promise<CanonicalRenderReply>=>({grant:{payload:{kind:'rendererResult',generation,intent},signature:sha('2')},packetReceipt:{sha256:sha('3'),revision:intent.revision,source:airSource,timeSeconds:.02,postprocess:session,sceneRevision:ackScene.sceneRevision,displayGeneration:generation,runConvergence:{verdict:'NOT_CONVERGED'as const,receiptSha256:sha('1')}}}))
 s.client.loadVerifiedResultLayers.mockImplementation(async()=>({requestId:'air',ok:true,revision:requestGrant.mock.calls[0]![0].revision,source:airSource,timeSeconds:.02,sceneRevision:7,displayGeneration:3,packetSha256:sha('3')}))
 const adapter=createOvPostprocessAdapter({client:s.client,sceneIdle:async()=>{},scene:()=>scene,requestGrant})
 driver.activate(adapter)
 setLiveAirResultSelection({owner:'air-postprocess',session,surfaceField:'T',meshReceiptSha256:source.meshReceiptSha256})
 const input:DisplayInput={...diagnostic,windFrame:{session,currentProject:{uid:'p',contentSha256:sha('a')},descriptor:{schema:'dcdtuni.verified-air-result-source/v2',postprocess:session,source:airSource,nativeInputSha256:sha('8'),runConvergence:{verdict:'NOT_CONVERGED',receiptSha256:sha('1')},fieldUnits:{T:'K',p:'Pa'},boundaryProvenanceByPatch:{nativeBoundary:proof}},mesh:owned(s.mesh,'mesh'),activeSurfaceField:'T',surface:{field:'T',values:owned(new Float32Array([300,301,302]),'field'),colorMap:'jet',range:[290,310],opacity:.23,hidden:new Set(),visible:true},slices:[],streamlines:[]}}
 if(withVolume){
  const raw=readFileSync('test/fixtures/common-volume-original-mask/native-controlled-p-collision.volume');const buffer=raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)as ArrayBuffer
  input.volume={field:'p',unit:'Pa',visible:true,payload:owned(decodeVolume(buffer),'volume'),range:[-9999,-9999],palette:'jet',opacity:.31,resolution:16}
 }
 expect(driver.present(input).generation).toBe(3);await driver.whenIdle()
 expect(requestGrant.mock.calls[0]![2]).toBe(3)
 const volumeIntent=requestGrant.mock.calls[0]![0].volume
 if(withVolume)expect(volumeIntent).toEqual({field:'p',unit:'Pa',visible:true,token:sha('a'),resolution:16,lut:{palette:'jet',range:[-9999,-9999],opacity:.31}})
 else expect(volumeIntent).toBeNull()
 expect(driver.status().status).toBe('displayed');expect(currentResultLayerOwner()).toBe('air-postprocess')
 expect(s.client.loadVerifiedResultLayers).toHaveBeenCalledTimes(2)
})

it.each(['hide','scene','grant-rejection','ack-rejection']as const)('an actually obsolete %s operation cannot end the newer public scene/session',async(change)=>{
 const s=fixture();let current=true;let release!:()=>void
 if(change==='grant-rejection')s.requestGrant.mockImplementationOnce(()=>new Promise((_,reject)=>release=()=>reject(new Error('late refused source'))))
 else s.client.loadVerifiedResultLayers.mockImplementationOnce(()=>new Promise((done,reject)=>release=()=>change==='ack-rejection'?reject(new Error('late Kit scene refusal')):done({requestId:'old-mesh',ok:true,revision:s.packet.revision,source,timeSeconds:null,sceneRevision:7,displayGeneration:2,packetSha256:sha('3')})))
 const pending=transferMeshPreview({...s,scene,generation:2,isCurrent:()=>current})
 for(let i=0;i<12;i++)await Promise.resolve()
 expect(release).toBeTypeOf('function');current=false
 if(change==='scene')setOvResultStage(s.client,{...scene,sceneRevision:8})
 const assertion=expect(pending).resolves.toBeUndefined();release();await assertion
})
it.each(['revision','time','scene','generation','packetSha','source','rejection']as const)('a still-current %s failure remains rejected by the exact signed carrier contract',async(change)=>{
 const s=fixture();const base={requestId:'ack',ok:true,revision:s.packet.revision,source,timeSeconds:null,sceneRevision:7,displayGeneration:2,packetSha256:sha('3')}
 if(change==='rejection')s.client.loadVerifiedResultLayers.mockRejectedValueOnce(new Error('current Kit refuses signature/source'))
 else {
  const ack={...base}
  if(change==='revision')ack.revision++
  if(change==='time')(ack as unknown as {timeSeconds:number}).timeSeconds=1
  if(change==='scene')ack.sceneRevision++
  if(change==='generation')ack.displayGeneration++
  if(change==='packetSha')ack.packetSha256=sha('4')
  if(change==='source')ack.source={...source,projectContentSha256:sha('4')}
  s.client.loadVerifiedResultLayers.mockResolvedValueOnce(ack)
 }
 await expect(transferMeshPreview({...s,scene,generation:2,isCurrent:()=>true})).rejects.toThrow()
})
