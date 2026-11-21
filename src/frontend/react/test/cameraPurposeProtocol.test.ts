import {expect,it,vi} from 'vitest'
import {OvCommandClient} from '../src/renderer/ovProtocol'
it('requests a camera handoff explicitly and waits for its matching ACK',async()=>{
 const sent:{command_name:string;message:string}[]=[]
 const client=new OvCommandClient(async p=>{sent.push(p)})
 const promise=client.readView('camera-handoff');void promise.catch(()=>{})
 let settled=false;void promise.then(()=>{settled=true},()=>{})
 try{
  await Promise.resolve();expect(sent).toHaveLength(1)
  const args=JSON.parse(sent[0]!.message)
  expect(sent[0]!.command_name).toBe('getViewState')
  expect(Object.keys(args).sort()).toEqual(['purpose','requestId'])
  expect(args.purpose).toBe('camera-handoff')
  client.receive({event_type:'dcdtuni:renderAck',payload:{requestId:'wrong-request',ok:true}})
  await Promise.resolve();expect(settled).toBe(false)
  const ack={requestId:args.requestId,ok:true,sceneRevision:19,projectHash:'a'.repeat(64),primitives:2152,
   sceneRequestId:'last-authored',view:{selectedUids:['rack'],timeSeconds:null,
    camera:{position:[1,2,3],target:[0,0,0],up:[0,1,0],fov:45,aspect:2,near:.1,far:400}}}
  client.receive({event_type:'dcdtuni:renderAck',payload:ack})
  expect(await promise).toEqual(ack);expect((await promise).gridDisplay).toBeUndefined()
 }finally{client.dispose();await promise.catch(()=>{})}
})
it('keeps ordinary view reads without a selector and preserves their full grid receipt',async()=>{
 const sent:{command_name:string;message:string}[]=[]
 const client=new OvCommandClient(async p=>{sent.push(p)}),p=client.readView();void p.catch(()=>{})
 try{
  await Promise.resolve();const args=JSON.parse(sent[0]!.message)
  expect(Object.keys(args)).toEqual(['requestId'])
  const gridDisplay={phase:'unsupported',gridPixelVerified:false,nativeGeometry:{timeSeconds:null}}
  client.receive({event_type:'dcdtuni:renderAck',payload:{renderAckJSON:JSON.stringify({
   requestId:args.requestId,ok:true,gridDisplay,view:{selectedUids:[],timeSeconds:null}})}})
  expect((await p).gridDisplay).toEqual(gridDisplay)
 }finally{client.dispose();await p.catch(()=>{})}
})
it('rejects unknown purposes before any transport submission',async()=>{
 vi.useFakeTimers()
 const send=vi.fn(async()=>{}),client=new OvCommandClient(send,10)
 const p=client.readView('grid-proof' as never);void p.catch(()=>{})
 try{await vi.advanceTimersByTimeAsync(25);await expect(p).rejects.toThrow('Invalid Omniverse view purpose');expect(send).not.toHaveBeenCalled()}
 finally{client.dispose();vi.useRealTimers()}
})
it('does not resurrect a retired camera query from a late matching ACK',async()=>{
 const sent:{command_name:string;message:string}[]=[]
 const client=new OvCommandClient(async p=>{sent.push(p)}),p=client.readView('camera-handoff')
 await Promise.resolve();const id=JSON.parse(sent[0]!.message).requestId
 client.dispose();await expect(p).rejects.toThrow('disconnected')
 client.receive({event_type:'dcdtuni:renderAck',payload:{requestId:id,ok:true,sceneRevision:1,projectHash:'a'.repeat(64)}})
 await Promise.resolve()
})
