import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {afterEach,expect,it,vi} from 'vitest'
const sdk=vi.hoisted(()=>({config:null as null|{videoElementId:string;fitStreamResolution:boolean;width:number;height:number;onStart:(e:unknown)=>void;onStop:(e:unknown)=>void},resize:vi.fn(async()=>({status:'success'}))}))
vi.mock('@nvidia/omniverse-webrtc-streaming-library',()=>({StreamStatus:{none:0},AppStreamer:{connect:vi.fn((o:{streamConfig:typeof sdk.config})=>{sdk.config=o.streamConfig;const v=document.getElementById(o.streamConfig!.videoElementId)!;const p=document.createElement('div');p.id=`${v.id}-input-parent`;v.replaceWith(p);p.append(v);return Promise.resolve({})}),resize:sdk.resize,terminate:vi.fn(),sendMessage:vi.fn()}}))
vi.mock('../src/useTranslation',()=>({useTranslation:()=>({t:(key:string)=>key})}))
vi.mock('../src/renderer/ovProtocol',()=>({OvCommandClient:class{dispose=vi.fn();onSceneProgress=vi.fn();awaitOutboundReady=()=>new Promise(()=>{})},ovAckTimeoutDegraded:()=>false}))
vi.mock('../src/renderer/displayRaster',()=>({observeDecodedRaster:()=>({dispose:vi.fn()}),newRasterOwner:()=> 'controlled'}))
vi.mock('../src/renderer/ovStreamLease',()=>({OV_CANCELED_START_BUDGET_MS:1,createOvStartReceipt:()=>({record:()=>true}),ovStreamLeases:{open:(start:()=>unknown)=>{start();return{ready:Promise.resolve(),close:()=>Promise.resolve()}}},terminateOvViewer:vi.fn()}))
import {OvViewport} from '../src/renderer/OvViewport'
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();document.body.replaceChildren()})
it('actual OvViewport uses public fit=false/resize after its own success and retires sizing on stream failure',async()=>{
 ;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
 let callback:ResizeObserverCallback=()=>{};const disconnect=vi.fn()
 vi.stubGlobal('ResizeObserver',class{constructor(cb:ResizeObserverCallback){callback=cb}observe=vi.fn();disconnect=disconnect})
 const descriptors=['clientWidth','clientHeight'].map(k=>Object.getOwnPropertyDescriptor(HTMLVideoElement.prototype,k))
 Object.defineProperties(HTMLVideoElement.prototype,{clientWidth:{configurable:true,get:()=>1191},clientHeight:{configurable:true,get:()=>796}})
 const log=vi.spyOn(console,'info').mockImplementation(()=>{}),host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 try{
  await act(async()=>root.render(<OvViewport server='not-contacted.invalid' snapshot={()=>({})as never} projectHash={()=>'a'.repeat(64)}/>))
  expect(sdk.config).toMatchObject({fitStreamResolution:false,width:1600,height:900});expect(sdk.resize).not.toHaveBeenCalled()
  await act(async()=>sdk.config!.onStart({status:'success'}));expect(sdk.resize).toHaveBeenCalledTimes(1);expect(sdk.resize).toHaveBeenCalledWith(1192,796)
  act(()=>sdk.config!.onStop({status:'success'}));expect(disconnect).toHaveBeenCalledTimes(1);callback([],{}as ResizeObserver)
  expect(sdk.resize).toHaveBeenCalledTimes(1);await act(async()=>root.unmount())
 }finally{log.mockRestore();['clientWidth','clientHeight'].forEach((k,i)=>{if(descriptors[i])Object.defineProperty(HTMLVideoElement.prototype,k,descriptors[i]!);else delete (HTMLVideoElement.prototype as unknown as Record<string,unknown>)[k]});host.remove()}
})
