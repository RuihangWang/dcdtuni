import{act}from'react'
import{createRoot}from'react-dom/client'
import{expect,it,vi}from'vitest'
const sdk=vi.hoisted(()=>({options:null as any}))
vi.mock('@nvidia/omniverse-webrtc-streaming-library',()=>({StreamStatus:{none:0},AppStreamer:{connect:async(options:any)=>{sdk.options=options;return{status:'inProgress'}},terminate:async()=>({status:'success'}),streamStatus:0}}))
vi.mock('../src/useTranslation',()=>({useTranslation:()=>({t:(key:string)=>key})}))
vi.mock('../src/renderer/ovProtocol',()=>({OvCommandClient:class{dispose(){}},ovAckTimeoutDegraded:()=>false}))
import{OvViewport}from'../src/renderer/OvViewport'
it('real View observes decoded video events even when connect caps are1600x900; disposal prevents late resize publication',async()=>{
 const host=document.createElement('div'),root=createRoot(host),events:any[]=[];document.body.append(host);const old=(globalThis as any).IS_REACT_ACT_ENVIRONMENT;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
 let unmounted=false,resizeNotice:ResizeObserverCallback=()=>{},cssHeight=796;const disconnected=vi.fn();vi.stubGlobal('ResizeObserver',class{constructor(cb:ResizeObserverCallback){resizeNotice=cb}observe(){}disconnect=disconnected})
 try{await act(async()=>root.render(<OvViewport server="not-contacted.invalid" snapshot={()=>{throw Error('not accepted media')}} projectHash={()=>'controlled'}onDecodedRaster={e=>events.push(e)}/>));const video=host.querySelector('video')!;video.style.padding='0px';video.style.border='0px solid';Object.defineProperties(video,{clientWidth:{get:()=>1992},clientHeight:{get:()=>cssHeight}});video.getBoundingClientRect=()=>({width:1992,height:cssHeight}as DOMRect);let w=1600,h=638;Object.defineProperties(video,{videoWidth:{get:()=>w},videoHeight:{get:()=>h}})
 expect(sdk.options.streamConfig).toMatchObject({width:1600,height:900,fitStreamResolution:true});expect(events).toHaveLength(1);expect(events[0].type).toBe('owner');video.dispatchEvent(new Event('loadedmetadata'));w=960;h=382;video.dispatchEvent(new Event('resize'));expect(events.filter(e=>e.type==='raster').map(e=>e.raster.decoded)).toEqual([[1600,638],[960,382]]);cssHeight=736;resizeNotice([],{}as ResizeObserver);expect(events.at(-1).raster).toMatchObject({revision:3,decoded:[960,382],css:[1992,736],layoutCss:[1992,736]})
 await act(async()=>root.unmount());unmounted=true;sdk.options.streamConfig.onStart({status:'canceled'});await Promise.resolve();const count=events.length;w=1600;h=590;video.dispatchEvent(new Event('resize'));resizeNotice([],{}as ResizeObserver);expect(disconnected).toHaveBeenCalledTimes(1);expect(events).toHaveLength(count);expect(events.at(-1).type).toBe('retired')
 }finally{if(!unmounted)await act(async()=>root.unmount());sdk.options?.streamConfig.onStart({status:'canceled'});await Promise.resolve();await Promise.resolve();host.remove();vi.unstubAllGlobals();(globalThis as any).IS_REACT_ACT_ENVIRONMENT=old}
})
