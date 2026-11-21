import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect,it,vi} from 'vitest'
const sdk=vi.hoisted(()=>({config:null as null|{onStop:(event:unknown)=>void}}))
vi.mock('@nvidia/omniverse-webrtc-streaming-library',()=>({
  StreamStatus:{none:0},AppStreamer:{connect:vi.fn((options:{streamConfig:typeof sdk.config})=>{sdk.config=options.streamConfig;return new Promise(()=>{})}),terminate:vi.fn(),sendMessage:vi.fn()},
}))
vi.mock('../src/useTranslation',()=>({useTranslation:()=>({t:(key:string)=>key})}))
vi.mock('../src/renderer/ovProtocol',()=>({OvCommandClient:class{dispose=vi.fn()},ovAckTimeoutDegraded:()=>false}))
vi.mock('../src/renderer/displayRaster',()=>({observeDecodedRaster:()=>({dispose:vi.fn()}),newRasterOwner:()=> 'controlled-raster'}))
import {OvViewport} from '../src/renderer/OvViewport'
import {ovVideoFrameStats} from '../src/renderer/viewportFrameStats'
it('the actual OvViewport binds each mounted video and retires it on failure and unmount',async()=>{
  vi.useFakeTimers();const log=vi.spyOn(console,'info').mockImplementation(()=>{})
  ;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
  const callbacks=new Map<HTMLVideoElement,Map<number,VideoFrameRequestCallback>>()
  let id=0
  const originalRequest=Object.getOwnPropertyDescriptor(HTMLVideoElement.prototype,'requestVideoFrameCallback')
  const originalCancel=Object.getOwnPropertyDescriptor(HTMLVideoElement.prototype,'cancelVideoFrameCallback')
  Object.defineProperties(HTMLVideoElement.prototype,{requestVideoFrameCallback:{configurable:true,value:()=>0},cancelVideoFrameCallback:{configurable:true,value:()=>{}}})
  const request=vi.spyOn(HTMLVideoElement.prototype,'requestVideoFrameCallback').mockImplementation(function(this:HTMLVideoElement,cb){
    const queue=callbacks.get(this)??new Map();callbacks.set(this,queue);queue.set(++id,cb);return id
  })
  const cancel=vi.spyOn(HTMLVideoElement.prototype,'cancelVideoFrameCallback').mockImplementation(function(this:HTMLVideoElement,key){callbacks.get(this)?.delete(key)})
  const paused=vi.spyOn(HTMLVideoElement.prototype,'paused','get').mockReturnValue(false)
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
  const frame=(video:HTMLVideoElement,time:number,count:number)=>{const queue=callbacks.get(video)!;const [key,cb]=Array.from(queue)[0]!;queue.delete(key);cb(time,{presentedFrames:count} as VideoFrameCallbackMetadata)}
  try {
    await act(async()=>root.render(<OvViewport server="not-contacted.invalid" snapshot={()=>({}) as never} projectHash={()=>'hash'}/>))
    const first=host.querySelector('video')!;expect(callbacks.has(first)).toBe(true)
    console.error('ROOT_ACTUAL_FPS_PREDICATES',JSON.stringify({paused:first.paused,ended:first.ended,visibility:first.ownerDocument.visibilityState,clock:performance.now(),callbacks:callbacks.get(first)!.size,sdkInitialized:!!sdk.config}));frame(first,0,0);console.error('ROOT_AFTER_FRAME0',JSON.stringify(ovVideoFrameStats()));frame(first,1000,30);console.error('ROOT_AFTER_FRAME1000',JSON.stringify({stats:ovVideoFrameStats(),paused:first.paused,ended:first.ended,visibility:first.ownerDocument.visibilityState,clock:performance.now(),callbacks:callbacks.get(first)!.size}));expect(ovVideoFrameStats().fps).toBe(30)
    act(()=>sdk.config!.onStop({status:'success'}));expect(ovVideoFrameStats().fps).toBeNull();expect(callbacks.get(first)!.size).toBe(0)
    await act(async()=>root.unmount());expect(ovVideoFrameStats().fps).toBeNull()
    const replacement=createRoot(host)
    await act(async()=>replacement.render(<OvViewport server="not-contacted.invalid" snapshot={()=>({}) as never} projectHash={()=>'hash'}/>))
    const second=host.querySelector('video')!;expect(second).not.toBe(first)
    frame(second,1100,0);frame(second,2100,60);expect(ovVideoFrameStats().fps).toBe(60)
    await act(async()=>replacement.unmount());expect(ovVideoFrameStats().fps).toBeNull();expect(callbacks.get(second)!.size).toBe(0)
    expect(request).toHaveBeenCalled();expect(cancel).toHaveBeenCalledTimes(2)
  }finally{paused.mockRestore();request.mockRestore();cancel.mockRestore();
    if(originalRequest)Object.defineProperty(HTMLVideoElement.prototype,'requestVideoFrameCallback',originalRequest);else Reflect.deleteProperty(HTMLVideoElement.prototype,'requestVideoFrameCallback')
    if(originalCancel)Object.defineProperty(HTMLVideoElement.prototype,'cancelVideoFrameCallback',originalCancel);else Reflect.deleteProperty(HTMLVideoElement.prototype,'cancelVideoFrameCallback')
    log.mockRestore();host.remove();vi.useRealTimers()}
})
