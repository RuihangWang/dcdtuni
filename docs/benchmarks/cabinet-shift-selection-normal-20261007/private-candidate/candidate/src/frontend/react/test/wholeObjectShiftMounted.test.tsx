/** Actual mounted Viewport DOM pointer chain + actual Three ray/scene/highlight.
 * Controlled: WebGL pixels, Canvas2D texture carrier, RAF/extent and liquid API
 * claim boundary. No production picking/drag/selection/store function is mocked. */
import {act,useEffect,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import * as THREE from 'three'
import {afterEach,expect,it,vi} from 'vitest'
import type {CfdProject,Construction} from '../src/schema/cfdproj'
import type {TreeSelection} from '../src/interaction/treeNodes'
import {applyClick} from '../src/interaction/selection'
import {publishSelection,getSelection} from '../src/selectionBus'
import * as store from '../src/projectStore'
import {stateV4} from '../src/legacy/cfdprojModules'
import {geometryPure,pbrMaterials} from '../src/legacy/geometryBuilder'
import {getViewportHandle,setViewportHandle} from '../src/viewportBus'
import {liquidMode,setLiquidModeActive} from '../src/legacy/liquidScene'
import type {SceneBuilder,SceneRegistries} from '../src/interaction/sceneBuilder'
import type {FaceDragTools} from '../src/geometry/faceDrag'
import fixture from './fixtures/closedCabinetShinaide.json'
const carrier=vi.hoisted(()=>({renderer:null as any}))
vi.mock('three',async(importOriginal)=>{const a=await importOriginal<typeof import('three')>();return {...a,WebGLRenderer:class{
 domElement=document.createElement('canvas');ratio=1;size=new a.Vector2();toneMapping=0;toneMappingExposure=1;outputColorSpace=''
 constructor(){carrier.renderer=this;Object.assign(this.domElement,{setPointerCapture(){},releasePointerCapture(){},hasPointerCapture(){return false}})}
 setPixelRatio(v:number){this.ratio=v}getPixelRatio(){return this.ratio}setClearAlpha(){}
 setSize(w:number,h:number){this.size.set(w,h);this.domElement.width=w*this.ratio;this.domElement.height=h*this.ratio}
 getSize(v:THREE.Vector2){return v.copy(this.size)}getDrawingBufferSize(v:THREE.Vector2){return v.set(this.domElement.width,this.domElement.height)}
 getContext(){return {ALIASED_LINE_WIDTH_RANGE:1,getParameter:()=>new Float32Array([1,1]),isContextLost:()=>false}}
 render(){}dispose(){}
}}})
vi.mock('../src/renderer/environmentLighting',()=>({ENVIRONMENT_INTENSITY:.5,installRoomEnvironment:()=>()=>{}}))
vi.mock('../src/components/AxisGizmo',()=>({AxisGizmo:()=>null}))
import {Viewport} from '../src/components/Viewport'
import {DEFAULT_VIEW_OPTIONS} from '../src/components/Toolbar'
let root:ReturnType<typeof createRoot>|null=null,host:HTMLDivElement|null=null
let picked:Array<[string|null,boolean]>=[],nodes:TreeSelection[]=[]
let setNode:((n:TreeSelection|null)=>void)|null=null
function liquidApi(){const api=liquidMode();expect(api).toBeTruthy();return api as Required<NonNullable<ReturnType<typeof liquidMode>>>}
const live=()=>window as unknown as {__sceneBuilder:SceneBuilder;__viewportRegistries:SceneRegistries;__faceDrag:FaceDragTools}
function Harness({project,uid,node,tab}:{project:CfdProject;uid:string;node:TreeSelection|null;tab:string}){
 const [selection,update]=useState({uids:[uid],active:uid as string|null}),[n,updateNode]=useState(node)
 setNode=updateNode
 useEffect(()=>publishSelection({uid:selection.active,node:n}),[selection.active,n])
 return <Viewport project={project}selectedUid={selection.active}selectedUids={selection.uids}selectedNode={n}
  view={{...DEFAULT_VIEW_OPTIONS,showGrid:false}} activeTab={tab}
  onSelect={(hit,toggle=false)=>{picked.push([hit,toggle]);update(p=>applyClick(p.uids,p.active,hit,toggle,[uid]));updateNode(null)}}
  onSelectNode={next=>{nodes.push(next);updateNode(next)}}/>
}
async function mount(kind:'box'|'cdu',patch=false,tab='geometry'){
 vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(1000);vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(1000)
 vi.spyOn(HTMLCanvasElement.prototype,'getBoundingClientRect').mockReturnValue({left:0,top:0,right:1000,bottom:1000,width:1000,height:1000,x:0,y:0,toJSON:()=>({})})
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(((type:string)=>{
  if(type!=='2d')throw new Error('Only Canvas2D carrier is allowed')
  return {fillStyle:'',fillRect(){},getImageData(_x:number,_y:number,w:number,h:number){return {data:new Uint8ClampedArray(w*h*4)}},createImageData(w:number,h:number){return {data:new Uint8ClampedArray(w*h*4)}},putImageData(){}}
 }) as unknown as typeof HTMLCanvasElement.prototype.getContext)
 vi.stubGlobal('fetch',vi.fn(async()=>new Response('{}',{status:200})))
 vi.stubGlobal('requestAnimationFrame',vi.fn(()=>1));vi.stubGlobal('cancelAnimationFrame',vi.fn())
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}})
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true})
 let raw:CfdProject
 if(kind==='box')raw=JSON.parse(readFileSync(resolve('test/fixtures/boxShiftActualGui.cfdproj'),'utf8'))
 else{raw=stateV4().getInitialStateV4() as unknown as CfdProject;raw.constructions!.cdus={m_30:structuredClone(fixture.models.find(m=>m.uid==='m_30')!)}}
 const uid=kind==='box'?'m_1':'m_30'
 const model=(kind==='box'?raw.constructions!.boxes:raw.constructions!.cdus)![uid]!
 if(patch)(model.geometry!.faces as Record<string,{patches:unknown[]}>).front!.patches=[{id:'p_rect',type:'rect',x:.1,y:.2,w:.4,h:.4}]
 store.loadProject(raw,null);setLiquidModeActive(kind==='cdu');const project=store.getProject()!
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
 const node:TreeSelection=patch?{type:'patch',uid,faceId:'front',patchId:'p_rect'}:{type:'face',uid,faceId:'front'}
 await act(async()=>{root!.render(<Harness project={project}uid={uid}node={node}tab={tab}/>);await vi.dynamicImportSettled()})
 const builder=live().__sceneBuilder;expect(builder).toBeDefined();const structural=live().__viewportRegistries.renderRootMap.get(uid)!;expect(structural).toBeDefined();structural.updateWorldMatrix(true,true)
 const json=JSON.stringify(store.getProject()),dirty=store.isDirty()
 return {uid,model:store.findConstruction(project,uid)!.model,structural,json,dirty}
}
function aim(target:THREE.Vector3,outward:THREE.Vector3){
 const h=getViewportHandle()!;h.camera.position.copy(target).addScaledVector(outward,5);h.camera.up.set(0,1,0)
 if(Math.abs(outward.y)>.9)h.camera.up.set(0,0,1)
 h.camera.lookAt(target);h.camera.updateProjectionMatrix();h.camera.updateMatrixWorld(true)
}
function event(type:string,mods:Record<string,boolean>={}){
 // jsdom PointerEvent support varies; properties are the real DOM event contract.
 const e=new MouseEvent(type,{bubbles:true,cancelable:true,button:0,buttons:type==='pointerdown'?1:0,clientX:500,clientY:500,...mods})
 Object.defineProperties(e,{pointerId:{value:7},pointerType:{value:'mouse'}});return e
}
async function click(mods:Record<string,boolean>={}){
 const c=carrier.renderer.domElement as HTMLCanvasElement
 await act(async()=>{c.dispatchEvent(event('pointerdown',mods));c.dispatchEvent(event('pointerup',mods));c.dispatchEvent(event('click',mods))})
}
function frontPoint(m:Construction,r:THREE.Object3D,u:number,v:number){
 const layouts=geometryPure().getFaceLayouts(m) as Record<string,{origin:THREE.Vector3;right:THREE.Vector3;up:THREE.Vector3}>
 const f=layouts.front!;return r.localToWorld(f.origin.clone().addScaledVector(f.right,u).addScaledVector(f.up,v))
}
afterEach(async()=>{if(root)await act(async()=>root!.unmount());root=null;host?.remove();host=null;setNode=null;picked=[];nodes=[];publishSelection({uid:null,node:null});setViewportHandle(null);setLiquidModeActive(false);pbrMaterials()._resetForTests();vi.restoreAllMocks();vi.unstubAllGlobals();Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:false})})
it.each(['shiftKey','ctrlKey','metaKey'])('mounted selected BOX patch %s click reaches UID selection rather than no-move patch drag',async modifier=>{
 const h=await mount('box',true),target=frontPoint(h.model,h.structural,.3,.4)
 aim(target,new THREE.Vector3(0,0,-1));expect(live().__faceDrag).toBeDefined()
 await click({[modifier]:true})
 expect(picked).toEqual([[h.uid,modifier!=='shiftKey']]);expect(getSelection().node).toBeNull()
 expect(getSelection().uid).toBe(modifier==='shiftKey'?h.uid:null)
 expect(live().__faceDrag.dragging()).toBe(false);expect(JSON.stringify(store.getProject())).toBe(h.json);expect(store.isDirty()).toBe(h.dirty)
})
it('plain selected BOX patch still starts the actual face body drag and consumes its no-move release',async()=>{
 const h=await mount('box',true);aim(frontPoint(h.model,h.structural,.3,.4),new THREE.Vector3(0,0,-1))
 const c=carrier.renderer.domElement as HTMLCanvasElement
 await act(async()=>c.dispatchEvent(event('pointerdown')));expect(live().__faceDrag.dragging()).toBe(true)
 await act(async()=>{c.dispatchEvent(event('pointerup'));c.dispatchEvent(event('click'))})
 expect(live().__faceDrag.dragging()).toBe(false);expect(picked).toEqual([]);expect(getSelection().node?.type).toBe('patch')
 expect(JSON.stringify(store.getProject())).toBe(h.json)
})
it.each(['box','cdu'] as const)('mounted Shift on all six %s body directions retains whole UID/highlight and saved bytes',async kind=>{
 const h=await mount(kind),bounds=new THREE.Box3().setFromObject(h.structural),center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3())
 for(const axis of [new THREE.Vector3(1,0,0),new THREE.Vector3(-1,0,0),new THREE.Vector3(0,1,0),new THREE.Vector3(0,-1,0),new THREE.Vector3(0,0,1),new THREE.Vector3(0,0,-1)]){
  await act(async()=>setNode!({type:'face',uid:h.uid,faceId:'front'}))
  const target=center.clone();aim(target,axis.clone().multiplyScalar(Math.max(size.x,size.y,size.z)/5+1))
  await click({shiftKey:true});expect(picked.at(-1),axis.toArray().join(',')).toEqual([h.uid,false]);expect(getSelection()).toEqual({uid:h.uid,node:null})
  let painted=0;h.structural.traverse(o=>{if((o as THREE.Mesh).isMesh){painted++;expect(o.userData._origMaterial,o.name).toBeDefined()}});expect(painted).toBeGreaterThan(0)
 }
 expect(JSON.stringify(store.getProject())).toBe(h.json);expect(store.isDirty()).toBe(h.dirty)
})
it.each(['shiftKey','ctrlKey','metaKey'])('mounted CDU %s selection is not consumed by the legacy liquid click claim boundary',async modifier=>{
 const h=await mount('cdu'),bounds=new THREE.Box3().setFromObject(h.structural),target=bounds.getCenter(new THREE.Vector3())
 aim(target,new THREE.Vector3(0,0,-1));const liquid=liquidApi();expect(liquid).toBeTruthy()
 const controls=vi.spyOn(liquid,'handleControlPointClick').mockReturnValue(true),valves=vi.spyOn(liquid,'handleValveClick').mockReturnValue(true)
 await click({[modifier]:true});expect(picked).toEqual([[h.uid,modifier!=='shiftKey']]);expect(controls).not.toHaveBeenCalled();expect(valves).not.toHaveBeenCalled()
 expect(getSelection().node).toBeNull();expect(JSON.stringify(store.getProject())).toBe(h.json)
})
it('plain CDU liquid control claim keeps its existing priority',async()=>{
 const h=await mount('cdu');aim(new THREE.Box3().setFromObject(h.structural).getCenter(new THREE.Vector3()),new THREE.Vector3(0,0,-1))
 const controls=vi.spyOn(liquidApi(),'handleControlPointClick').mockReturnValue(true)
 await click();expect(controls).toHaveBeenCalledOnce();expect(picked).toEqual([])
})

it.each(['ctrlKey','metaKey'])('mounted Shift+%s keeps the cabinet additive toggle',async modifier=>{
 const h=await mount('box',true);aim(frontPoint(h.model,h.structural,.3,.4),new THREE.Vector3(0,0,-1))
 await click({shiftKey:true,[modifier]:true});expect(picked).toEqual([[h.uid,true]]);expect(getSelection()).toEqual({uid:null,node:null})
 expect(JSON.stringify(store.getProject())).toBe(h.json)
})
it.each(['mesh','solver','postprocess','system'])('Shift on %s retains its pointer chain and cannot select hidden geometry',async tab=>{
 const h=await mount('cdu',false,tab);aim(new THREE.Box3().setFromObject(h.structural).getCenter(new THREE.Vector3()),new THREE.Vector3(0,0,-1))
 const begin=vi.spyOn(liquidApi(),'startValveDrag').mockReturnValue(true)
 await click({shiftKey:true});expect(begin).toHaveBeenCalledOnce();expect(picked).toEqual([]);expect(JSON.stringify(store.getProject())).toBe(h.json)
})

it('a new modifier press clears the grab left by a completed drag without a browser click',async()=>{
 const h=await mount('box',true);aim(frontPoint(h.model,h.structural,.3,.4),new THREE.Vector3(0,0,-1));const c=carrier.renderer.domElement as HTMLCanvasElement
 await act(async()=>{c.dispatchEvent(event('pointerdown'));c.dispatchEvent(event('pointerup'))})
 expect(live().__faceDrag.dragging()).toBe(false);expect(picked).toEqual([])
 aim(frontPoint(h.model,h.structural,.3,1.4),new THREE.Vector3(0,0,-1));await click({shiftKey:true})
 expect(picked).toEqual([[h.uid,false]]);expect(getSelection()).toEqual({uid:h.uid,node:null});expect(JSON.stringify(store.getProject())).toBe(h.json)
})
