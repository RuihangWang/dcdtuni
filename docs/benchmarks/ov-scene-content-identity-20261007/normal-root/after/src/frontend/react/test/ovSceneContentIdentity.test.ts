// Source identity with the real store/SceneSnapshot/controller/bridge.
// Only the media transport is controlled; no API, Kit or browser calls.
import {act,createElement,useSyncExternalStore} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {afterEach,expect,it,vi} from 'vitest'
import * as THREE from 'three'
import {RendererControl} from '../src/renderer/RendererControl'
import {createOvEditBridge} from '../src/renderer/ovEditBridge'
import {buildSceneSnapshot,type SceneSnapshot,type RenderViewState} from '../src/renderer/sceneSnapshot'
import {OvCommandClient,type RenderAck} from '../src/renderer/ovProtocol'
import type {OvViewportProps} from '../src/renderer/OvViewport'
import * as store from '../src/projectStore'
import type {CfdProject} from '../src/schema/cfdproj'
import {cfdrunExport} from '../src/legacy/cfdrunBuild'
import {setViewportHandle,type ViewportHandle} from '../src/viewportBus'
import {setOvSession} from '../src/renderer/ovSession'
import {DEFAULT_VIEW_OPTIONS} from '../src/components/Toolbar'
import {displaySceneKey,publishLeafDisplayChange} from '../src/liquid/closedCabinetDisplay'
const media=vi.hoisted(()=>({props:null as OvViewportProps|null}))
vi.mock('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/src/renderer/OvViewport',()=>({OvViewport:(props:OvViewportProps)=>{media.props=props;return null}}))
;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
let root:Root|null=null,host:HTMLDivElement|null=null,client:OvCommandClient|null=null
const eventBase={schema:'dcdtuni.ov-edit/v1',coordinateSystem:{upAxis:'Y',handedness:'right',metersPerUnit:1},action:{type:'selection'}}
function source():CfdProject{return {uid:'source-identity-case',name:'Controlled content only',constructions:{racks:{rack:{uid:'rack',meta:{name:'Rack',enabled:true},geometry:{location:{x:1,y:1,z:1},size:{x:1,y:2,z:1}}}},cdus:{cdu:{uid:'cdu',meta:{name:'CDU',enabled:true},geometry:{location:{x:3,y:1,z:1},size:{x:1,y:2,z:1}}}}}}}
function carrier(){const root=new THREE.Group();for(const uid of ['rack','cdu']){const model=new THREE.Group();model.userData.modelId=uid;model.position.set(uid==='rack'?1:3,1,1);model.add(new THREE.Mesh(new THREE.BoxGeometry(1,2,1),new THREE.MeshStandardMaterial()));root.add(model)}return root}
async function ticks(){await Promise.resolve();await Promise.resolve();await Promise.resolve()}
async function settle(){await act(async()=>{await new Promise(resolve=>setTimeout(resolve,390));await ticks()})}
afterEach(async()=>{if(root){await act(async()=>{root!.unmount();await ticks()});root=null}client?.dispose();client=null;host?.remove();host=null;media.props=null;setViewportHandle(null);setOvSession({client:null,ready:false,mode:'three',degraded:false,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null});delete(window as {globalGroup?:unknown}).globalGroup;vi.restoreAllMocks()})
async function mounted(){
 store.loadProject(source(),null)
 const group=carrier(),camera=new THREE.PerspectiveCamera(45,1.5,.1,400);camera.position.set(8,5,12)
 const target=new THREE.Vector3(2,1,1);camera.lookAt(target)
 setViewportHandle({camera,controls:Object.assign(new THREE.EventDispatcher(),{target,enabled:true,update:vi.fn()}) as unknown as ViewportHandle['controls']})
 ;(window as unknown as {globalGroup:THREE.Group}).globalGroup=group
 const selection=vi.fn()
 function Common(){useSyncExternalStore(store.subscribe,store.revision);return createElement(RendererControl,{project:store.getProject(),activeTab:'geometry',airMode:false,selectedUids:[],onSelection:selection,view:DEFAULT_VIEW_OPTIONS})}
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
 await act(async()=>{root!.render(createElement(Common));await ticks()})
 const selector=host.querySelector<HTMLSelectElement>('.renderer-selector-label select')!
 await act(async()=>{selector.value='ov';selector.dispatchEvent(new Event('change',{bubbles:true}));await ticks()})
 let accepted=media.props!.snapshot(),revision=7,hash=cfdrunExport().projectContentHashSync(store.getProject())
 const ack=(view:RenderViewState=accepted.view):RenderAck=>({ok:true,requestId:'controlled-identity',projectHash:hash,sceneRevision:revision,view})
 client=new OvCommandClient(async()=>{})
 const transport=client
 const load=vi.spyOn(transport,'loadScene').mockImplementation(async(p:SceneSnapshot,h?:string)=>{accepted=p;hash=h??cfdrunExport().projectContentHashSync(store.getProject());revision++;return ack()})
 vi.spyOn(transport,'readView').mockImplementation(async()=>ack())
 vi.spyOn(transport,'updateView').mockImplementation(async(view:RenderViewState)=>ack(view))
 vi.spyOn(transport,'loadOverlay').mockImplementation(async()=>ack())
 vi.spyOn(transport,'clearResultLayers').mockImplementation(async()=>ack())
 await act(async()=>{media.props!.onReady!(transport,ack(),accepted);await ticks()})
 await settle();expect(load).not.toHaveBeenCalled()
 const event=(uids=['rack','cdu'],overrides:Record<string,unknown>={})=>({...eventBase,sceneRevision:revision,projectHash:hash,selection:uids.map(uid=>({modelUid:uid,primId:accepted.primitives.find(p=>p.modelUid===uid)!.id})),...overrides})
 const receive=async(raw:unknown)=>{await act(async()=>{transport.receive({event_type:'dcdtuni:ovEditEvent',payload:raw});await ticks()})}
 return {group,load,selection,event,receive,identity:()=>({hash,revision})}
}
const refreshes=[['log action',()=>store.logAction('postprocess-complete','no content change')],['save completion',()=>store.markSaved('/controlled/source-identity.cfdproj')],['non-content live notification',()=>store.notifyLiveEdit()]] as const
for(const [label,refresh] of refreshes){
 it('does not retransmit acknowledged geometry for '+label,async()=>{const f=await mounted(),before=f.identity();await act(async()=>{refresh();await ticks()});await settle();expect(f.load).not.toHaveBeenCalled();expect(f.identity()).toEqual(before);await f.receive(f.event());expect(f.selection).toHaveBeenCalledWith(['rack','cdu'])})
 it('keeps an atomic accepted selection valid across '+label,()=>{store.loadProject(source(),null);const snapshot=buildSceneSnapshot({root:carrier(),project:store.getProject()!});const selected=vi.fn(),bridge=createOvEditBridge({snapshot,sceneRevision:7,currentSceneRevision:()=>7,pickablePrimitiveIds:new Set(snapshot.primitives.map(p=>p.id)),editablePrimitiveIds:new Set(),canEdit:()=>true,onSelection:selected});const event={...eventBase,sceneRevision:7,projectHash:bridge.projectHash,selection:snapshot.primitives.map(p=>({modelUid:p.modelUid,primId:p.id}))};const revision=store.revision();refresh();expect(store.revision()).toBeGreaterThan(revision);expect(cfdrunExport().projectContentHashSync(store.getProject())).toBe(bridge.projectHash);expect(bridge.apply(event)).toEqual({ok:true,action:'selection',modelUids:['rack','cdu']});expect(selected).toHaveBeenCalledOnce()})
}
it('rejects an old event during a real geometry edit then binds the new ACK',async()=>{const f=await mounted(),old=f.event(['rack']),identity=f.identity();await act(async()=>{expect(store.setModelLocation('rack',{x:1.2,y:1,z:1})).toBe(true);f.group.children[0]!.position.x=1.2;await ticks()});await f.receive(old);expect(f.selection).not.toHaveBeenCalled();await settle();expect(f.load).toHaveBeenCalledOnce();expect(f.identity().hash).not.toBe(identity.hash);expect(f.identity().revision).toBe(identity.revision+1);await f.receive(old);expect(f.selection).not.toHaveBeenCalled();await f.receive(f.event(['rack']));expect(f.selection).toHaveBeenCalledWith(['rack'])})
it('invalidates real hidden-source changes and never picks its new invisible primitive',async()=>{const f=await mounted(),old=f.event(['rack']),identity=f.identity();await act(async()=>{store.setEnabled('rack',false);f.group.children[0]!.visible=false;await ticks()});await f.receive(old);expect(f.selection).not.toHaveBeenCalled();await settle();expect(f.load).toHaveBeenCalledOnce();expect(f.identity().hash).not.toBe(identity.hash);await f.receive(old);await f.receive(f.event(['rack']));expect(f.selection).not.toHaveBeenCalled();await f.receive(f.event(['cdu']));expect(f.selection).toHaveBeenCalledWith(['cdu'])})
it('still fences hash/revision/unknown/mixed UID events after a harmless repaint',async()=>{const f=await mounted();await act(async()=>{store.logAction('harmless');await ticks()});for(const raw of [f.event(['cdu'],{projectHash:'b'.repeat(64)}),f.event(['cdu'],{sceneRevision:6}),f.event(['rack'],{selection:[{primId:'unknown',modelUid:'rack'}]}),f.event(['rack'],{selection:[{primId:f.event(['rack']).selection[0]!.primId,modelUid:'cdu'}]})]){await f.receive(raw)}expect(f.selection).not.toHaveBeenCalled()})
it('still retransmits a real display visibility revision without changing scientific source',async()=>{const f=await mounted(),identity=f.identity();await act(async()=>{publishLeafDisplayChange();await ticks()});await settle();expect(f.load).toHaveBeenCalledOnce();expect(f.identity().hash).toBe(identity.hash);expect(f.identity().revision).toBe(identity.revision+1)})
it('preserves result sample, time, material and display invalidation in the content key',()=>{const key=displaySceneKey('case','a'.repeat(64),[4,20,9],3);for(const changed of [displaySceneKey('case','b'.repeat(64),[4,20,9],3),displaySceneKey('other','a'.repeat(64),[4,20,9],3),displaySceneKey('case','a'.repeat(64),[5,20,9],3),displaySceneKey('case','a'.repeat(64),[4,21,9],3),displaySceneKey('case','a'.repeat(64),[4,20,10],3),displaySceneKey('case','a'.repeat(64),[4,20,9],4)])expect(changed).not.toBe(key)})
