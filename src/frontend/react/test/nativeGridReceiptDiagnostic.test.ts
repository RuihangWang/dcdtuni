/** PRIVATE controlled JSON flags. No real carb/Kit/encoder proof. */
import {expect,it,vi,afterEach} from 'vitest'
afterEach(()=>{vi.unstubAllEnvs();vi.restoreAllMocks();vi.resetModules()})
async function module(enabled=true){
 vi.stubEnv('VITE_DC_CFD_NATIVE_GRID_RECEIPT_DIAGNOSTICS',enabled?'grid-receipt-v1':'')
 vi.resetModules();return import('../src/renderer/nativeGridGeometryReceipt')
}
it('does not log when disabled, does not normalize nested absent time, and caps one window at32',async()=>{
 let m=await module(false);const log=vi.spyOn(console,'info').mockImplementation(()=>{})
 const status={phase:'unsupported',gridPixelVerified:false,nativeGeometry:{gridPixelVerified:false,encodedFrameProof:'unresolved'}}
 m.traceNativeGridReceipt('decode-input',status,{})
 expect(log).not.toHaveBeenCalled()
 m=await module()
 for(let i=0;i<64;i++){
  const n={...(i&1?{timeSeconds:null}:{}),...(i&2?{gridPixelVerified:false}:{}),...(i&4?{encodedFrameProof:'unresolved'}:{})}
  const s={...(i&8?{gridPixelVerified:false}:{}),...(i&16?{nativeGeometry:n}:{})}
  const v=i&32?{timeSeconds:null}:{}
  m.traceNativeGridReceipt('decode-input',s,v)
 }
 expect(log.mock.calls.length).toBe(32)
 expect('timeSeconds' in status.nativeGeometry).toBe(false)
 const rows=log.mock.calls.map(c=>JSON.parse(c[1] as string))
 expect(rows.every(r=>r.diagnosticAuthority===false&&r.additionalNativeReads===false)).toBe(true)
 expect(rows.every(r=>!('camera' in r)&&!('binding' in r)&&!('projectHash' in r))).toBe(true)
})

it('reserves shared window budget before a failed console sink',async()=>{
 const m=await module();const sink=vi.spyOn(console,'info').mockImplementation(()=>{throw Error('controlled sink failure')})
 const input={nativeGeometry:{gridPixelVerified:false,encodedFrameProof:'unresolved'}}
 const before=structuredClone(input)
 for(let i=0;i<64;i++){
  const n={...(i&1?{timeSeconds:null}:{}),...(i&2?{gridPixelVerified:false}:{}),...(i&4?{encodedFrameProof:'unresolved'}:{})}
  const s={...(i&8?{gridPixelVerified:false}:{}),...(i&16?{nativeGeometry:n}:{})}
  m.traceNativeGridReceipt('decode-input',s,i&32?{timeSeconds:null}:{})
 }
 expect(sink).toHaveBeenCalledTimes(32)
 m.traceNativeGridReceipt('validation',input,{},'schema-time-key-missing')
 expect(sink).toHaveBeenCalledTimes(32)
 expect(input).toEqual(before);expect('timeSeconds' in input.nativeGeometry).toBe(false)
})
function strictFixture(){
 const binding={projectUid:'controlled-project',projectContentSha256:'a'.repeat(64),sceneRevision:2,viewRevision:3}
 const grid={visible:true,spacingM:.6,divisions:4,extentM:2.4,centerColor:[.2,.3,.4],lineColor:[.1,.2,.3],opacity:.6,matrix:[1,0,0,0,0,1,0,0,0,0,1,0,0,-.005,0,1]}
 const requested={position:[0,3,8],target:[0,0,0],up:[0,1,0],fov:50,aspect:1.5,near:.1,far:100}
 const accepted={...requested,up:[0,8/Math.sqrt(73),-3/Math.sqrt(73)]}
 const view:any={backgroundGrid:grid,backgroundGridRaster:{binding},camera:requested,timeSeconds:null}
 const native={schema:'dcdtuni.native-grid-geometry/v1',phase:'synchronized',domain:'native-world-plane',binding,grid,requestedCamera:requested,acceptedCamera:accepted,timeSeconds:null,nativeSource:{ownerSha256:'b'.repeat(64),projectionSha256:'c'.repeat(64),frameNumber:4,resolution:[1920,1080],fullResolution:[1920,1080]},carrierSha256:'d'.repeat(64),gridPixelVerified:false,encodedFrameProof:'unresolved'}
 const ack:any={ok:true,requestId:'controlled',view:{...view,camera:accepted},sceneRevision:2,projectHash:binding.projectContentSha256,gridDisplay:{phase:'unsupported',reason:'stream-layout-unresolved',viewRevision:3,gridPixelVerified:false,nativeGeometry:native}}
 return{view:structuredClone(view),ack:structuredClone(ack)}
}
it('strictly declines an absent nested static time key without repair',async()=>{
 const m=await module(false),{view,ack}=strictFixture()
 expect(m.nativeGridGeometrySynchronized(ack,view)).toBe(true)
 delete ack.gridDisplay.nativeGeometry.timeSeconds
 const before=structuredClone(ack)
 expect(m.nativeGridGeometrySynchronized(ack,view)).toBe(false)
 expect('timeSeconds' in ack.gridDisplay.nativeGeometry).toBe(false);expect(ack).toEqual(before)
})
it('binds distinct requested and accepted cameras separately and declines each exact mismatch',async()=>{
 const m=await module(false),{view,ack}=strictFixture()
 expect(ack.view.camera.up).not.toEqual(view.camera.up)
 expect(m.nativeGridGeometrySynchronized(ack,view)).toBe(true)
 const wrongRequested=structuredClone(ack)
 wrongRequested.gridDisplay.nativeGeometry.requestedCamera=structuredClone(ack.view.camera)
 expect(m.nativeGridGeometrySynchronized(wrongRequested,view)).toBe(false)
 const wrongAccepted=structuredClone(ack)
 wrongAccepted.gridDisplay.nativeGeometry.acceptedCamera=structuredClone(view.camera)
 expect(m.nativeGridGeometrySynchronized(wrongAccepted,view)).toBe(false)
 const wrongEcho=structuredClone(ack);wrongEcho.view.camera=structuredClone(view.camera)
 expect(m.nativeGridGeometrySynchronized(wrongEcho,view)).toBe(false)
 expect(ack.gridDisplay.nativeGeometry.gridPixelVerified).toBe(false)
 expect(ack.gridDisplay.nativeGeometry.encodedFrameProof).toBe('unresolved')
})
