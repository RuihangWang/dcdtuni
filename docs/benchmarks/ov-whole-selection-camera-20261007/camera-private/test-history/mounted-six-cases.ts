// Real mounted RendererControl with a controlled media transport boundary.
// No media/Kit/browser API or scientific artifacts are used.
import {act,createElement} from 'react'
import {createRoot} from 'react-dom/client'
import {afterEach,expect,it,vi} from 'vitest'
import * as THREE from 'three'
import {RendererControl} from '../src/renderer/RendererControl'
import {DEFAULT_VIEW_OPTIONS} from '../src/components/Toolbar'
import type {OvViewportProps} from '../src/renderer/OvViewport'
import type {RenderAck,OvCommandClient} from '../src/renderer/ovProtocol'
import type {RenderViewState,SceneSnapshot} from '../src/renderer/sceneSnapshot'
import * as store from '../src/projectStore'
import {cfdrunExport} from '../src/legacy/cfdrunBuild'
import {setViewportHandle,type ViewportHandle} from '../src/viewportBus'
import {selectedResultTime} from '../src/liquid/liquidTempColor'
import {setOvSession} from '../src/renderer/ovSession'
const media=vi.hoisted(()=>({props:null as OvViewportProps|null}))
vi.mock('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/src/renderer/OvViewport',()=>({OvViewport:(props:OvViewportProps)=>{media.props=props;return null}}))
;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
let root:ReturnType<typeof createRoot>|null=null,host:HTMLDivElement|null=null
const iso=[12.5514,7.2,13.2277] as const
const low=[4.8534,1.1215,17.1898] as const
async function ticks(){await Promise.resolve();await Promise.resolve();await Promise.resolve()}
afterEach(async()=>{
 if(root){await act(async()=>{root!.unmount();await ticks()});root=null}
 host?.remove();host=null;media.props=null
 setViewportHandle(null);setOvSession({client:null,ready:false,mode:'three',degraded:false,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
 delete (window as unknown as {globalGroup?:THREE.Group}).globalGroup
 vi.restoreAllMocks()
})
async function mounted(){
 store.loadProject({uid:'owned-camera-project',name:'Controlled display only'},null)
 const project=store.getProject()!,hash=cfdrunExport().projectContentHashSync(project),revision=31
 const camera=new THREE.PerspectiveCamera(45,1.5,.1,400);camera.position.set(...iso)
 const target=new THREE.Vector3(4.4514,1.8,3.3277);camera.lookAt(target)
 const controls=Object.assign(new THREE.EventDispatcher(),{target,enabled:true,update:vi.fn()})
 setViewportHandle({camera,controls:controls as unknown as ViewportHandle['controls']})
 ;(window as unknown as {globalGroup:THREE.Group}).globalGroup=new THREE.Group()
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
 const selection=vi.fn()
 await act(async()=>{root!.render(createElement(RendererControl,{project,selectedUids:[],onSelection:selection,view:DEFAULT_VIEW_OPTIONS}));await ticks()})
 const select=host.querySelector<HTMLSelectElement>('.renderer-selector-label select')!
 await act(async()=>{select.value='ov';select.dispatchEvent(new Event('change',{bubbles:true}));await ticks()})
 expect(media.props).not.toBeNull()
 const packet=media.props!.snapshot()
 let finish!:(ack:RenderAck)=>void
 const delayed=new Promise<RenderAck>(resolve=>{finish=resolve})
 const ack=(overrides:Partial<RenderAck>={}):RenderAck=>({requestId:'controlled-read',ok:true,projectHash:hash,sceneRevision:revision,
   view:{...packet.view,camera:{...packet.view.camera!,position:[...low]},selectedUids:[],timeSeconds:selectedResultTime()},...overrides})
 const client={onEditEvent:vi.fn(),updateView:vi.fn(async(view:RenderViewState)=>ack({view})),readView:vi.fn(()=>delayed),
  loadScene:vi.fn(async(p:SceneSnapshot)=>ack({view:p.view})),loadOverlay:vi.fn(async()=>ack()),clearResultLayers:vi.fn(async()=>ack())}
 await act(async()=>{media.props!.onReady!(client as unknown as OvCommandClient,ack({view:packet.view}),packet);await ticks()})
 expect(select.value).toBe('ov')
 await act(async()=>{select.value='three';select.dispatchEvent(new Event('change',{bubbles:true}));await ticks()})
 expect(client.readView).toHaveBeenCalledTimes(1)
 return {camera,target,select,selection,client,finish,ack,project}
}
it('adopts a delayed native orbit only from the precisely acknowledged scene',async()=>{
 const f=await mounted()
 await act(async()=>{f.finish(f.ack());await ticks()})
 expect(f.select.value).toBe('three');expect(f.camera.position.toArray()).toEqual([...low]);expect(f.selection).toHaveBeenCalledWith([])
})
for(const [label,overrides] of [['foreign same-UID hash',{projectHash:'b'.repeat(64)}],['foreign scene revision',{sceneRevision:32}],['missing identity',{projectHash:undefined,sceneRevision:undefined}]] as const){
 it('keeps the local camera and selection on delayed '+label,async()=>{
  const f=await mounted()
  await act(async()=>{f.finish(f.ack(overrides));await ticks()})
  expect(f.select.value).toBe('three');expect(f.camera.position.toArray()).toEqual([...iso]);expect(f.selection).not.toHaveBeenCalled()
 })
}
it('cannot restore a reply after a project change while return is pending',async()=>{
 const f=await mounted()
 await act(async()=>{store.loadProject({...f.project,name:'New local source'},null);await ticks();f.finish(f.ack());await ticks()})
 expect(f.select.value).toBe('three');expect(f.camera.position.toArray()).toEqual([...iso]);expect(f.selection).not.toHaveBeenCalled()
})
it('cannot restore a reply after the actual component session is disposed',async()=>{
 const f=await mounted()
 await act(async()=>{root!.unmount();root=null;await ticks();f.finish(f.ack());await ticks()})
 expect(f.camera.position.toArray()).toEqual([...iso]);expect(f.selection).not.toHaveBeenCalled()
})
