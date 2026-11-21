// Portable controlled event / SDK consumer regression; no native browser gesture proof.
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect,it,vi} from 'vitest'
const sdk=vi.hoisted(()=>({config:null as null|{videoElementId:string;onStop:(e:unknown)=>void}}))
vi.mock('@nvidia/omniverse-webrtc-streaming-library',()=>({StreamStatus:{none:0},AppStreamer:{
 connect:vi.fn((o:{streamConfig:typeof sdk.config})=>{sdk.config=o.streamConfig;return Promise.resolve({})}),terminate:vi.fn(),sendMessage:vi.fn()}}))
vi.mock('../src/useTranslation',()=>({useTranslation:()=>({t:(key:string)=>key})}))
vi.mock('../src/renderer/ovProtocol',()=>({OvCommandClient:class{dispose=vi.fn()},ovAckTimeoutDegraded:()=>false}))
vi.mock('../src/renderer/displayRaster',()=>({observeDecodedRaster:()=>({dispose:vi.fn()}),newRasterOwner:()=> 'controlled'}))
vi.mock('../src/renderer/ovStreamLease',()=>({OV_CANCELED_START_BUDGET_MS:1,createOvStartReceipt:()=>({record:()=>false}),
 ovStreamLeases:{open:(start:()=>unknown)=>{start();return{ready:Promise.resolve(),close:()=>Promise.resolve()}}},terminateOvViewer:vi.fn()}))
import {OvViewport} from '../src/renderer/OvViewport'
it('actual mounted OvViewport installs and retires the real pointer adapter on fail and unmount',async()=>{
 ;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
 const log=vi.spyOn(console,'info').mockImplementation(()=>{}),host=document.createElement('div');document.body.append(host)
 const root=createRoot(host),add=vi.spyOn(HTMLVideoElement.prototype,'addEventListener'),remove=vi.spyOn(HTMLVideoElement.prototype,'removeEventListener')
 try{
  await act(async()=>root.render(<OvViewport server='not-contacted.invalid' snapshot={()=>({})as never} projectHash={()=>'a'.repeat(64)}/>))
  expect(add.mock.calls.some(c=>c[0]==='pointerdown'&&c[2]===true)).toBe(true)
  act(()=>sdk.config!.onStop({status:'success'}))
  expect(remove.mock.calls.some(c=>c[0]==='pointerdown'&&c[2]===true)).toBe(true)
  await act(async()=>root.unmount())
 }finally{log.mockRestore();add.mockRestore();remove.mockRestore();host.remove()}
})
