/** Actual shared driver + actual OV client/channel; controlled numeric replies,
 * not CFD/Kit/video evidence. Native API-contract controls live beside bridge. */
import {act,createElement} from 'react'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {decodeSlice,decodeStreamlines} from '../src/postprocess'
import {createRoot} from 'react-dom/client'
import {afterEach,expect,it,vi} from 'vitest'
import {PostprocessDisplayDriver,type DisplayInput} from '../src/postprocess/displaySession'
import {sharedPostprocessDisplay} from '../src/postprocess/sharedDisplay'
import {createOvPostprocessAdapter} from '../src/renderer/ovPostprocessAdapter'
import {createThreePostprocessAdapter} from '../src/renderer/threePostprocessAdapter'
import {OvCommandClient,type RenderAck,type OvDisplayGrant} from '../src/renderer/ovProtocol'
import {setOvSession} from '../src/renderer/ovSession'
import {useSharedPostprocessRenderer} from '../src/renderer/useSharedPostprocessRenderer'
import {assertPreviewPacket,parsePreviewEvent,type PreviewPacket,type PreviewEvent,type PreviewAck} from '../src/renderer/ovPostprocessPreviews'
import {clearResultLayerSet,setLiveAirResultSelection} from '../src/renderer/resultLayerRevision'
import {bindNumericProduct} from '../src/postprocess/numericProducts'
import type {CommonRenderIntent,CanonicalRenderReply} from '../src/postprocess/commonRenderIntent'
const sha=(v:string)=>v.repeat(64)
const clients:OvCommandClient[]=[]
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

function reply(intent:CommonRenderIntent,sceneRevision:number,generation:number):CanonicalRenderReply {
 return {grant:{payload:{intent,sceneRevision,generation},signature:sha('2')},packetReceipt:{sha256:sha('1'),revision:intent.revision,source:intent.source,timeSeconds:intent.postprocess.timeValue!,postprocess:intent.postprocess,sceneRevision,displayGeneration:generation,runConvergence:{verdict:'NOT_CONVERGED',receiptSha256:sha('4')}}}
}
function numericAck(grant:OvDisplayGrant):Partial<RenderAck> {
 const intent=grant.payload.intent as CommonRenderIntent
 return {revision:intent.revision,source:intent.source,timeSeconds:intent.postprocess.timeValue!,sceneRevision:grant.payload.sceneRevision as number,displayGeneration:grant.payload.generation as number,packetSha256:sha('1')}
}
function transport() {
 const sent:{command_name:string;message:string}[]=[]
 let hold=false,alter:((ack:PreviewAck)=>PreviewAck)|null=null,retireFail=false
 let client!:OvCommandClient
 client=new OvCommandClient(async payload=>{
  sent.push(payload);const args=JSON.parse(payload.message)
  if(payload.command_name==='setCommonPostprocessPreviews'&&hold)return
  queueMicrotask(()=>{
   let ack:RenderAck={requestId:args.requestId,ok:true}
   if(payload.command_name==='loadVerifiedRenderResultLayers')ack={...ack,...numericAck(args.grant)}
   if(payload.command_name==='setCommonPostprocessPreviews') {
    const preview:PreviewAck={schema:'dcdtuni.ov-postprocess-previews-ack/v1',packet:structuredClone(args.packet),leaseKey:'3'.repeat(32),ready:true}
    ack.postprocessPreviews=alter?alter(preview):preview
   }
   if(payload.command_name==='retireCommonPostprocessPreviews') {ack.retiredPostprocessPreviews=true;if(retireFail)ack={requestId:args.requestId,ok:false,error:'controlled retirement failure'}}
   client.receive({event_type:'dcdtuni:renderAck',payload:ack})
  })
 });clients.push(client)
 return {client,sent,hold:(value:boolean)=>hold=value,alter:(fn:typeof alter)=>alter=fn,retireFail:()=>retireFail=true,
  packet:()=>JSON.parse([...sent].reverse().find(v=>v.command_name==='setCommonPostprocessPreviews')!.message).packet as PreviewPacket}
}
function setup() {
 const t=transport();let scene={projectUid:'p',projectHash:sha('a'),sceneRevision:7}
 let driver!:PostprocessDisplayDriver
 const requestGrant=vi.fn(async(intent:CommonRenderIntent,s:{sceneRevision:number},generation:number)=>reply(intent,s.sceneRevision,generation))
 const adapter=createOvPostprocessAdapter({client:t.client,sceneIdle:async()=>{},scene:()=>scene,requestGrant,onInteraction:event=>driver.interact(event)})
 driver=new PostprocessDisplayDriver(adapter)
 const value=input();value.layers.ring={show:true,origin:[0,1,0],normal:[0,3,0],radius:2};value.layers.seed={show:true,centre:[0,1,0],radius:1}
 const events=vi.fn();driver.onInteraction(events)
 return {...t,driver,value,events,requestGrant,scene,adapter}
}
function event(packet:PreviewPacket,kind:'ring'|'seed'='ring',sequence=1):PreviewEvent {
 return {schema:'dcdtuni.ov-postprocess-preview-event/v1',binding:structuredClone(packet.binding),leaseKey:'3'.repeat(32),sequence,kind,setId:packet.handles.find(v=>v.kind===kind)!.setId,position:[1,2,3]}
}
function receive(client:OvCommandClient,value:unknown) {client.receive({event_type:'dcdtuni:postprocessPreviewEvent',payload:value})}
async function tick(){for(let i=0;i<12;i++)await Promise.resolve()}
afterEach(()=>{setOvSession({mode:'three',ready:false,client:null,projectUid:null,projectContentSha256:null,sceneRevision:null});sharedPostprocessDisplay.activate(createThreePostprocessAdapter());sharedPostprocessDisplay.clear();clients.splice(0).forEach(c=>c.dispose());clearResultLayerSet();setLiveAirResultSelection(null);vi.unstubAllGlobals()})

it('lowers both exact shared forms after numeric ACK and END events reach the same driver without recomputing',async()=>{
 const s=setup();s.driver.present(s.value);await s.driver.whenIdle();const packet=s.packet(),intent=structuredClone(s.requestGrant.mock.calls[0]![0])
 expect(packet.handles).toEqual([{kind:'ring',setId:42,origin:[0,1,0],normal:[0,3,0],radius:2},{kind:'seed',setId:9,centre:[0,1,0],radius:1}])
 expect(packet.binding).toMatchObject({source:s.driver.current()!.packet!.source,packetSha256:sha('1'),timeSeconds:.015,sceneRevision:7,displayGeneration:s.driver.current()!.generation,scopeKey:s.driver.current()!.scopeKey})
 receive(s.client,event(packet));receive(s.client,event(packet,'seed',2))
 expect(s.events.mock.calls.map(c=>c[0])).toEqual([{kind:'ring',setId:42,position:[1,2,3]},{kind:'seed',setId:9,position:[1,2,3]}])
 expect(s.requestGrant).toHaveBeenCalledOnce();expect(s.requestGrant.mock.calls[0]![0]).toEqual(intent)
 expect(s.sent.map(v=>v.command_name)).toEqual(['loadVerifiedRenderResultLayers','setCommonPostprocessPreviews'])
})
it('keeps all numerical slice/trace list entries and exact IDs while dragging just one preview',async()=>{
 const s=setup(),fixture=JSON.parse(readFileSync(resolve(__dirname,'fixtures/constant_common_lut/input.json'),'utf8'))
 const buffer=(value:string)=>Uint8Array.from(Buffer.from(value,'base64')).buffer
 const slice=decodeSlice(buffer(fixture.slice.base64)),stream=decodeStreamlines(buffer(fixture.streamline.base64)),session=s.value.windFrame!.session
 for(const [value,kind,token]of [[slice,'slice','7'],[stream,'streamline','8']]as const)bindNumericProduct(value,new Response(null,{headers:{'X-PP-Product':JSON.stringify({schema:'dcdtuni.numeric-product-receipt/v1',token:sha(token),kind,postprocess:session,payloadSha256:sha('6'),nativeInputSha256:sha('5')})}}),kind,session)
 s.value.windFrame!.slices=[42,43].map(id=>({id,payload:slice,origin:[0,1,0],normal:[0,1,0],visible:true,colorMap:'viridis',range:[299,303],opacity:.4}))
 s.value.windFrame!.streamlines=[9,10].map(id=>({id,set:stream,tubeRadius:.01,visible:true,colorMap:'viridis',range:[299,303],opacity:.4}))
 s.driver.present(s.value);await s.driver.whenIdle()
 const intent=structuredClone(s.requestGrant.mock.calls[0]![0]),numeric=structuredClone(s.driver.current()!.packet)
 expect(intent.slices.map(x=>x.setId)).toEqual([42,43]);expect(intent.streamlines.map(x=>x.setId)).toEqual([9,10])
 receive(s.client,event(s.packet()));expect(s.events).toHaveBeenCalledWith({kind:'ring',setId:42,position:[1,2,3]})
 expect(s.driver.current()!.numericIntent).toEqual(intent);expect(s.driver.current()!.packet).toEqual(numeric);expect(s.requestGrant).toHaveBeenCalledOnce()
})
it('uses draft identities and keeps numerical set lists independent from preview positions',async()=>{
 const s=setup();s.value.handles={ringSetId:'draft',seedSetId:'draft'};s.driver.present(s.value);await s.driver.whenIdle()
 const before=structuredClone(s.driver.current()!.numericIntent);receive(s.client,event(s.packet(),'seed'))
 expect(s.events).toHaveBeenCalledWith({kind:'seed',setId:'draft',position:[1,2,3]});expect(s.driver.current()!.numericIntent).toEqual(before)
})
it('keeps unsupported geometry appearance honest while exact ACK enables only this preview interaction',async()=>{
 const s=setup();s.value.appearance={ao:true,aoStrength:1,lightIntensity:1,modelOpacity:1,fieldOpacity:1,renderOrder:['volume','surface','slice','streamline']}
 s.driver.present(s.value);await s.driver.whenIdle();expect(s.driver.status().status).toBe('unsupported');expect(s.driver.status().message).toContain('depth enhancement')
 receive(s.client,event(s.packet()));expect(s.events).toHaveBeenCalledOnce()
})
it.each(['source','packetSha256','timeSeconds','sceneRevision','displayGeneration','scopeKey','ownerKey','leaseKey','setId','kind'] as const)('rejects readback with a different %s',async key=>{
 const s=setup();s.driver.present(s.value);await s.driver.whenIdle();const e=event(s.packet())
 if(key==='source')e.binding.source.run!.receiptSha256=sha('8')
 else if(key==='leaseKey')e.leaseKey='8'.repeat(32)
 else if(key==='setId')e.setId=43
 else if(key==='kind')e.kind='seed'
 else if(key==='packetSha256')e.binding[key]=sha('8')
 else if(key==='scopeKey')e.binding[key]='other-scope'
 else if(key==='ownerKey')e.binding[key]='8'.repeat(32)
 else e.binding[key]+=1
 receive(s.client,e);expect(s.events).not.toHaveBeenCalled()
})
it('ignores replayed/out-of-order events, malformed extra fields and source-edit channel payloads',async()=>{
 const s=setup();s.driver.present(s.value);await s.driver.whenIdle();const e=event(s.packet(),'ring',2)
 receive(s.client,{...e,extra:true});receive(s.client,{...e,position:[Infinity,1,2]});s.client.receive({event_type:'dcdtuni:ovEditEvent',payload:e});expect(s.events).not.toHaveBeenCalled()
 receive(s.client,e);receive(s.client,e);receive(s.client,{...e,sequence:1});expect(s.events).toHaveBeenCalledOnce()
})
it('refuses current readback if accepted source or acknowledged scene changes outside the packet',async()=>{
 const s=setup();s.driver.present(s.value);await s.driver.whenIdle();const e=event(s.packet());s.scene.sceneRevision++
 receive(s.client,e);expect(s.events).not.toHaveBeenCalled()
 s.scene.sceneRevision--;setLiveAirResultSelection(null);receive(s.client,e);expect(s.events).not.toHaveBeenCalled()
})
it.each(['packet','lease','ready'])('rejects a preview ACK with invalid %s and never enables interaction',async key=>{
 const s=setup();s.alter(ack=>{if(key==='packet')ack.packet.handles[0]!.setId=43;else if(key==='lease')ack.leaseKey='invalid';else (ack as unknown as {ready:boolean}).ready=false;return ack})
 s.driver.present(s.value);await s.driver.whenIdle();expect(s.driver.status().status).toBe('rejected');receive(s.client,event(s.packet()));expect(s.events).not.toHaveBeenCalled()
})
it('clear aborts owned pending ACK immediately, sends retirement, and late ACK/event cannot unlock it',async()=>{
 const s=setup();s.hold(true);s.driver.present(s.value);await tick();const packet=s.packet(),sent=JSON.parse(s.sent.at(-1)!.message)
 receive(s.client,event(packet));expect(s.events).not.toHaveBeenCalled()
 s.driver.clear();await s.driver.whenIdle();expect(s.sent.some(v=>v.command_name==='retireCommonPostprocessPreviews')).toBe(true)
 s.client.receive({event_type:'dcdtuni:renderAck',payload:{requestId:sent.requestId,ok:true,postprocessPreviews:{schema:'dcdtuni.ov-postprocess-previews-ack/v1',packet,leaseKey:'3'.repeat(32),ready:true}}})
 receive(s.client,event(packet));expect(s.events).not.toHaveBeenCalled();expect(s.driver.status().status).toBe('idle')
})
it('new frame immediately rejects the old lease and renderer replacement uses a new owner on the same frame',async()=>{
 const s=setup();s.driver.present(s.value);await s.driver.whenIdle();const old=s.packet();s.driver.present(s.value)
 receive(s.client,event(old));expect(s.events).not.toHaveBeenCalled();await s.driver.whenIdle();expect(s.packet().binding.displayGeneration).toBeGreaterThan(old.binding.displayGeneration)
 const old2=s.packet();const t=transport();const adapter=createOvPostprocessAdapter({client:t.client,sceneIdle:async()=>{},scene:()=>s.scene,requestGrant:s.requestGrant,onInteraction:e=>s.driver.interact(e)})
 s.driver.activate(adapter);await s.driver.whenIdle();expect(t.packet().binding.ownerKey).not.toBe(old2.binding.ownerKey);expect(t.packet().binding.displayGeneration).toBe(old2.binding.displayGeneration)
 receive(s.client,event(old2));expect(s.events).not.toHaveBeenCalled();receive(t.client,event(t.packet()));expect(s.events).toHaveBeenCalledOnce();adapter.dispose()
})
it('retirement transport rejection is surfaced for the next frame without an unhandled promise',async()=>{
 const s=setup();s.driver.present(s.value);await s.driver.whenIdle();s.retireFail();s.driver.present(s.value);await s.driver.whenIdle()
 expect(s.driver.status()).toMatchObject({status:'rejected',message:'OV preview retirement failed: controlled retirement failure'});expect(s.requestGrant).toHaveBeenCalledOnce()
})
it('requires a concrete native retirement acknowledgement before allowing another frame',async()=>{
 const s=setup();s.driver.present(s.value);await s.driver.whenIdle()
 vi.spyOn(s.client,'retireCommonPostprocessPreviews').mockResolvedValueOnce({requestId:'controlled',ok:true})
 s.driver.present(s.value);await s.driver.whenIdle()
 expect(s.driver.status()).toMatchObject({status:'rejected',message:'OV preview retirement failed: OV preview retirement was not acknowledged'})
 expect(s.requestGrant).toHaveBeenCalledOnce()
})
it('protocol cancellation retires its pending request, rejects late ACK and disposal removes event listeners',async()=>{
 const t=transport();t.hold(true);const s=setup();s.driver.present(s.value);await s.driver.whenIdle();const packet=s.packet(),controller=new AbortController()
 const pending=t.client.setCommonPostprocessPreviews(packet,controller.signal),rejection=expect(pending).rejects.toThrow('owned request aborted');controller.abort();await rejection
 const args=JSON.parse(t.sent[0]!.message);t.client.receive({event_type:'dcdtuni:renderAck',payload:{requestId:args.requestId,ok:true}})
 const heard=vi.fn(),edit=vi.fn();t.client.onEditEvent(edit);t.client.subscribePostprocessPreviewEvents(heard);receive(t.client,event(packet));expect(heard).toHaveBeenCalledOnce();expect(edit).not.toHaveBeenCalled()
 t.client.dispose();receive(t.client,event(packet));expect(heard).toHaveBeenCalledOnce()
})
it('validates strict bounded packets before sending any request',()=>{
 const value={schema:'dcdtuni.ov-postprocess-previews/v1',binding:{source:{projectUid:'p',projectContentSha256:sha('a'),cfdrunSha256:sha('b'),meshReceiptSha256:sha('c'),meshQuality:'accepted',run:{uid:'r',receiptSha256:sha('d')}},packetSha256:sha('1'),timeSeconds:.015,sceneRevision:7,displayGeneration:1,scopeKey:'scope',ownerKey:'2'.repeat(32)},handles:[{kind:'ring',setId:42,origin:[0,0,0],normal:[0,0,0],radius:2}]}
 expect(()=>assertPreviewPacket(value)).toThrow('Invalid');expect(parsePreviewEvent({...value,schema:'dcdtuni.ov-postprocess-preview-event/v1'})).toBeNull()
})
it('the mounted production hook forwards the dedicated client event to the existing common interaction listener',async()=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
 const t=transport();vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{
  expect(url).toBe('/api/pp/render-grant');const request=JSON.parse(String(options.body)),r=reply(request.intent,request.scene.revision,request.displayGeneration)
  return new Response(JSON.stringify(r),{headers:{'X-PP-Revision':request.intent.postprocess.revision}})
 }))
 const host=document.createElement('div'),root=createRoot(host),heard=vi.fn(),unsubscribe=sharedPostprocessDisplay.onInteraction(heard)
 function Hook(){useSharedPostprocessRenderer();return null}
 try {
  await act(async()=>{setOvSession({mode:'ov',ready:true,degraded:false,client:t.client,sceneIdle:async()=>{},projectUid:'p',projectContentSha256:sha('a'),sceneRevision:7});root.render(createElement(Hook));await tick()})
  const value=input();value.layers.seed={show:true,centre:[0,1,0],radius:1};value.handles.seedSetId=9
  await act(async()=>{sharedPostprocessDisplay.present(value);await sharedPostprocessDisplay.whenIdle()})
  expect(sharedPostprocessDisplay.status().status).toBe('displayed')
  await act(async()=>{receive(t.client,event(t.packet(),'seed'));await tick()})
  expect(heard).toHaveBeenCalledWith({kind:'seed',setId:9,position:[1,2,3]})
 }finally{unsubscribe();await act(async()=>root.unmount());host.remove()}
})
