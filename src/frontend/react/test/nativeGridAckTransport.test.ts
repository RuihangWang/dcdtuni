import {afterEach,expect,it} from 'vitest'
import {OvCommandClient,type RenderAck} from '../src/renderer/ovProtocol'
import {nativeGridGeometrySynchronized} from '../src/renderer/nativeGridGeometryReceipt'
import type {RenderViewState} from '../src/renderer/sceneSnapshot'
const clients:OvCommandClient[]=[]
afterEach(()=>clients.splice(0).forEach(client=>client.dispose()))
function fixture(){
 const binding={projectUid:'controlled-project',projectContentSha256:'a'.repeat(64),sceneRevision:2,viewRevision:3}
 const grid={visible:true,spacingM:.6,divisions:4,extentM:2.4,centerColor:[.2,.3,.4],lineColor:[.1,.2,.3],opacity:.6,matrix:[1,0,0,0,0,1,0,0,0,0,1,0,0,-.005,0,1]}
 const requested={position:[0,3,8],target:[0,0,0],up:[0,1,0],fov:50,aspect:1.5,near:.1,far:100}
 const accepted={...requested,up:[0,8/Math.sqrt(73),-3/Math.sqrt(73)]}
 const view={backgroundGrid:grid,backgroundGridRaster:{binding},camera:requested,timeSeconds:null}as unknown as RenderViewState
 const native={schema:'dcdtuni.native-grid-geometry/v1',phase:'synchronized',domain:'native-world-plane',binding,grid,requestedCamera:requested,acceptedCamera:accepted,timeSeconds:null,nativeSource:{ownerSha256:'b'.repeat(64),projectionSha256:'c'.repeat(64),frameNumber:4,resolution:[1920,1080],fullResolution:[1920,1080]},carrierSha256:'d'.repeat(64),gridPixelVerified:false,encodedFrameProof:'unresolved'}
 const ack={ok:true,requestId:'controlled',view:{...view,camera:accepted,selectedUids:[]},sceneRevision:2,projectHash:binding.projectContentSha256,gridDisplay:{phase:'unsupported',reason:'stream-layout-unresolved',viewRevision:3,gridPixelVerified:false,nativeGeometry:native}}as unknown as RenderAck
 return{view:structuredClone(view),ack:structuredClone(ack)}
}
function request(){
 const sent:{command_name:string;message:string}[]=[]
 const client=new OvCommandClient(async p=>{sent.push(p)},1000);clients.push(client)
 const result:{ack?:RenderAck;error?:string}={}
 const promise=client.readView().then(ack=>{result.ack=ack},error=>{result.error=error.message})
 const id=JSON.parse(sent.at(-1)!.message).requestId as string
 return{client,id,result,promise}
}
const envelope=(payload:unknown)=>({event_type:'dcdtuni:renderAck',payload})
const carrier=(ack:unknown)=>({renderAckJSON:JSON.stringify(ack)})
async function notSettledThenLegacy(q:ReturnType<typeof request>){
 await Promise.resolve();expect(q.result.ack).toBeUndefined();expect(q.result.error).toBeUndefined()
 q.client.receive(envelope({requestId:q.id,ok:true}));await q.promise
 expect(q.result.ack).toEqual({requestId:q.id,ok:true})
}
it('preserves whole explicit-null native receipt and distinct requested/accepted cameras',async()=>{
 const q=request(),{view,ack}=fixture();ack.requestId=q.id
 q.client.receive(envelope(carrier(ack)));await q.promise
 const decoded=q.result.ack!
 expect(Object.prototype.hasOwnProperty.call(decoded.gridDisplay!.nativeGeometry!,'timeSeconds')).toBe(true)
 expect(decoded.gridDisplay!.nativeGeometry!.timeSeconds).toBe(null)
 expect(decoded.view!.timeSeconds).toBe(null);expect(decoded.view!.selectedUids).toEqual([])
 expect(nativeGridGeometrySynchronized(decoded,view)).toBe(true)
 expect(decoded.gridDisplay!.phase).toBe('unsupported')
 expect(decoded.gridDisplay!.nativeGeometry!.encodedFrameProof).toBe('unresolved')
 expect(decoded.gridDisplay!.nativeGeometry!.gridPixelVerified).toBe(false)
})
it('does not repair a missing nested time key or mint native success',async()=>{
 const q=request(),{view,ack}=fixture();ack.requestId=q.id
 delete (ack.gridDisplay!.nativeGeometry! as any).timeSeconds
 delete (ack.view! as any).timeSeconds
 q.client.receive(envelope(carrier(ack)));await q.promise
 expect(Object.prototype.hasOwnProperty.call(q.result.ack!.gridDisplay!.nativeGeometry!,'timeSeconds')).toBe(false)
 expect(q.result.ack!.view!.timeSeconds).toBe(null) // unchanged legacy top-view normalization
 expect(nativeGridGeometrySynchronized(q.result.ack!,view)).toBe(false)
})
it.each(['malformed','null','array','number','json-string','nan','nested-carrier','mixed','inherited-carrier','nonstring','oversize'])('refuses %s without consuming pending or accepting a second representation',async kind=>{
 const q=request(),valid={requestId:q.id,ok:true}
 let payload:unknown={renderAckJSON:JSON.stringify(valid)}
 if(kind==='malformed')payload={renderAckJSON:'{'}
 if(kind==='null')payload={renderAckJSON:'null'}
 if(kind==='array')payload={renderAckJSON:'[]'}
 if(kind==='number')payload={renderAckJSON:'4'}
 if(kind==='json-string')payload={renderAckJSON:JSON.stringify(JSON.stringify(valid))}
 if(kind==='nan')payload={renderAckJSON:'{"requestId":"'+q.id+'","ok":true,"x":NaN}'}
 if(kind==='nested-carrier')payload=carrier({...valid,renderAckJSON:JSON.stringify(valid)})
 if(kind==='mixed')payload={...carrier(valid),...valid}
 if(kind==='inherited-carrier')payload=Object.assign(Object.create({renderAckJSON:JSON.stringify(valid)}),{unrelated:true})
 if(kind==='nonstring')payload={renderAckJSON:valid}
 if(kind==='oversize')payload={renderAckJSON:JSON.stringify({...valid,pad:'\u00e9'.repeat(524_288)})}
 q.client.receive(envelope(payload));await notSettledThenLegacy(q)
})
it('keeps the existing four receive iterations: three message wrappers accepted, four refused',async()=>{
 const q=request(),{ack}=fixture();ack.requestId=q.id
 let event:unknown=envelope(carrier(ack))
 for(let i=0;i<4;i++)event={message:event}
 q.client.receive(event)
 await Promise.resolve();expect(q.result.ack).toBeUndefined()
 event=envelope(carrier(ack));for(let i=0;i<3;i++)event={message:event}
 q.client.receive(event);await q.promise
 expect(q.result.ack?.gridDisplay?.nativeGeometry?.timeSeconds).toBe(null)
})
it('keeps the raw outer string byte cap even when its carrier text would be smaller',async()=>{
 const q=request()
 const text=JSON.stringify({message:{message:{event_type:'dcdtuni:renderAck',payload:carrier({requestId:q.id,ok:true}),pad:'x'.repeat(1_050_625)}}})
 q.client.receive(text);await notSettledThenLegacy(q)
})
it('uses existing multipart group/pending correlation before a single carrier parse',async()=>{
 const q=request(),{view,ack}=fixture();ack.requestId=q.id
 const text=JSON.stringify(carrier(ack)),parts=[]
 for(let at=0;at<text.length;at+=500)parts.push({event_type:'dcdtuni:renderAck',id:'controlled-carrier-parts',part:parts.length+1,numParts:Math.ceil(text.length/500),payload:text.slice(at,at+500)})
 parts.reverse().forEach(part=>q.client.receive({message:JSON.stringify(part)}))
 await q.promise;expect(nativeGridGeometrySynchronized(q.result.ack!,view)).toBe(true)
})
it('never correlates a serialized unrelated request or changes legacy ordinary object ACKs',async()=>{
 const q=request()
 q.client.receive(envelope(carrier({requestId:'unrelated',ok:true})))
 await notSettledThenLegacy(q)
})
