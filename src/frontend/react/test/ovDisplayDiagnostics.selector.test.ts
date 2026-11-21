import {afterEach,expect,it,vi} from 'vitest'
import {act,createElement} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import * as THREE from 'three'
import * as store from '../src/projectStore'
import {setViewportHandle} from '../src/viewportBus'
import {OvCommandClient} from '../src/renderer/ovProtocol'
import {diagnosticReturnEnabled} from '../src/renderer/ovDisplayDiagnostics'
import {RendererControl} from '../src/renderer/RendererControl'
import {ovSession,setOvSession} from '../src/renderer/ovSession'
import type {OvViewportProps} from '../src/renderer/OvViewport'
const carrier=vi.hoisted(()=>({props:null as OvViewportProps|null}))
vi.mock('../src/renderer/OvViewport',()=>({OvViewport:(props:OvViewportProps)=>{carrier.props=props;return null}}))
;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
let host:HTMLDivElement,root:Root,c:OvCommandClient
const receipts:any[]=[]
afterEach(async()=>{if(root)await act(async()=>root.unmount());host?.remove();c?.dispose();setViewportHandle(null);setOvSession({mode:'three',ready:false,client:null,liquidActive:false});vi.useRealTimers();vi.restoreAllMocks();receipts.length=0;carrier.props=null})
// Kit's view reply carries the authored primitive count (extension.py view_state);
// the handoff refuses a reply whose count differs from the scene it acknowledged.
function reply(id:string,hash:string,primitives:number,revision=7):any{return {requestId:id,ok:true,sceneRevision:revision,projectHash:hash,primitives,view:{selectedUids:['returned'],timeSeconds:null},diagnostics:{schema:'dcdtuni.render-display-diagnostics/v1',profile:'bounded-display-v1',sampleSequence:1,sampleElapsedMs:.1,pixelVerified:false,renderer:{settingsScope:'effective-carbs-settings; not compiled-shader proof',settings:Object.fromEntries(['translucencyAsOpacity','translucencyEnabled','raytracingFractionalCutoutOpacity','pathtracingFractionalCutoutOpacity'].map(x=>[x,{status:'unavailable',reason:'missing-setting'}]))},viewport:{status:'pending',reason:'layout-unresolved',layoutProof:'unresolved',textureToEncodedMapping:'unresolved'},roof:{status:'unavailable',reason:'target-missing',targetSource:'missing'}}}}
async function mounted(){
 store.loadProject({uid:'diag-project',name:'Before'},null);const project=store.getProject()!
 const camera=new THREE.PerspectiveCamera(45,1,.1,100);camera.position.set(4,5,6)
 ;(window as any).globalGroup=new THREE.Group();setViewportHandle({camera,controls:{target:new THREE.Vector3(),update:vi.fn()} as any})
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
 const selected=vi.fn();await act(async()=>root.render(createElement(RendererControl,{project,selectedUids:[],onSelection:selected,activeTab:'mesh',airMode:false})))
 const sent:any[]=[];c=new OvCommandClient(async wire=>{sent.push(wire);if(wire.command_name!=='getViewState'){const v=JSON.parse(wire.message);queueMicrotask(()=>c.receive({event_type:'dcdtuni:renderAck',payload:{requestId:v.requestId,ok:true}}))}},50)
 const change=async(value:string)=>{await act(async()=>{const s=host.querySelector('select')!;s.value=value;s.dispatchEvent(new Event('change',{bubbles:true}))})}
 await change('ov');const props=carrier.props!,packet=props.snapshot(),hash=props.projectHash()
 await act(async()=>props.onReady!(c,{requestId:'scene',ok:true,sceneRevision:7,projectHash:hash},packet))
 return {selected,sent,change,hash,camera,primitives:packet.primitives.length}
}
it('only the explicit OV-to-Three selector adds the strict flag readback, once',async()=>{
 const {selected,sent,change,hash,primitives}=await mounted();expect(sent.filter(v=>v.command_name==='getViewState')).toHaveLength(0)
 vi.spyOn(console,'info').mockImplementation((...args)=>receipts.push(args))
 await change('three');const reads=sent.filter(v=>v.command_name==='getViewState');expect(reads).toHaveLength(1);const args=JSON.parse(reads[0]!.message)
 expect(!!args.diagnostics).toBe(diagnosticReturnEnabled);if(diagnosticReturnEnabled)expect(args.diagnostics).toEqual({profile:'bounded-display-v1',expectedSceneRevision:7,expectedProjectHash:hash})
 await act(async()=>c.receive({event_type:'dcdtuni:renderAck',payload:reply(args.requestId,hash,primitives)}))
 expect(selected).toHaveBeenCalledWith(['returned']);expect(host.querySelector('select')!.value).toBe('three');expect(ovSession().mode).toBe('three')
 expect(receipts).toHaveLength(diagnosticReturnEnabled?1:0);if(diagnosticReturnEnabled){expect(host.textContent).toContain('pixel/layout evidence remains unverified');expect(JSON.parse(receipts[0][1]).diagnostics.pixelVerified).toBe(false)}
})
it('actual store mutation after sending rejects the old project view and receipt',async()=>{
 const {selected,sent,change,hash,primitives}=await mounted();const log=vi.spyOn(console,'info').mockImplementation(()=>{})
 await change('three');const args=JSON.parse(sent.find(v=>v.command_name==='getViewState').message)
 await act(async()=>{store.loadProject({uid:'diag-project',name:'After'},null)})
 await act(async()=>c.receive({event_type:'dcdtuni:renderAck',payload:reply(args.requestId,hash,primitives)}))
 expect(selected).not.toHaveBeenCalled();expect(log).not.toHaveBeenCalled();expect(host.querySelector('select')!.value).toBe('three')
})
it('malformed diagnostic ACK refuses before camera/selection application',async()=>{
 if(!diagnosticReturnEnabled)return
 const {selected,sent,change,hash,camera,primitives}=await mounted();const before=camera.position.toArray();await change('three');const args=JSON.parse(sent.find(v=>v.command_name==='getViewState').message);const bad=reply(args.requestId,hash,primitives);bad.view.selectedUids={foreign:'path'}
 await act(async()=>c.receive({event_type:'dcdtuni:renderAck',payload:bad}));expect(selected).not.toHaveBeenCalled();expect(camera.position.toArray()).toEqual(before);expect(host.querySelector('select')!.value).toBe('three');expect(ovSession().mode).toBe('three')
})
it('hanging OV reply has one bounded wait and a late reply cannot apply state',async()=>{
 const {selected,sent,change,hash,primitives}=await mounted();vi.useFakeTimers();const log=vi.spyOn(console,'info').mockImplementation(()=>{})
 await change('three');const args=JSON.parse(sent.find(v=>v.command_name==='getViewState').message);expect(host.querySelector('select')!.disabled).toBe(true)
 await act(async()=>vi.advanceTimersByTimeAsync(diagnosticReturnEnabled?3100:1100));expect(host.querySelector('select')!.value).toBe('three');expect(host.querySelector('select')!.disabled).toBe(false)
 await act(async()=>c.receive({event_type:'dcdtuni:renderAck',payload:reply(args.requestId,hash,primitives)}));expect(selected).not.toHaveBeenCalled();expect(log).not.toHaveBeenCalled();expect(sent.filter(v=>v.command_name==='getViewState')).toHaveLength(1)
})
