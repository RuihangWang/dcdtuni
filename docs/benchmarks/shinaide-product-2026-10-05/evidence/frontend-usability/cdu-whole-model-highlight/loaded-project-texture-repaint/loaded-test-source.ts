import * as THREE from 'three'
import {afterEach,describe,expect,it,vi} from 'vitest'
import fixture from './fixtures/closedCabinetShinaide.json'
import type {CfdProject} from '../src/schema/cfdproj'
import {stateV4} from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {createSceneBuilder,emptyRegistries,type SceneHost} from '../src/interaction/sceneBuilder'
import {setLiquidModeActive} from '../src/liquid/liquidModeState'
import {paintSelectionHighlights,type HighlightSelection} from '../src/interaction/selectionHighlight'
import {NO_VIEW_FADES,reapplyViewFades,type ViewFades} from '../src/geometry/viewFades'
import {pbrMaterials} from '../src/legacy/geometryBuilder'

type Std=THREE.MeshStandardMaterial
const COLOR=0x66ead0
const meshes=(root:THREE.Object3D)=>{const out:THREE.Mesh[]=[];root.traverse(o=>{if((o as THREE.Mesh).isMesh)out.push(o as THREE.Mesh)});return out}
const materials=(m:THREE.Mesh)=>(Array.isArray(m.material)?m.material:[m.material]) as Std[]
const builders:Array<ReturnType<typeof createSceneBuilder>>=[]
afterEach(()=>{for(const b of builders.splice(0))b.clear();setLiquidModeActive(false);pbrMaterials()._resetForTests()})
function scene(){
 // Saved V4 derives type from the cdus bucket; use the real loader rather
 // than injecting an enumerable working kind into this regression fixture.
 const raw=stateV4().getInitialStateV4() as unknown as CfdProject
 const cdu=structuredClone(fixture.models.find(m=>m.uid==='m_30')!)
 const other=structuredClone(cdu);other.uid='other-cdu';other.meta.name='other';other.geometry.location.x+=2
 raw.constructions!.cdus={m_30:cdu,'other-cdu':other}
 store.loadProject(raw,null)
 const project=store.getProject()!
 const globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group();wrapperGroup.add(globalGroup)
 const h:SceneHost={globalGroup,wrapperGroup,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:false}
 setLiquidModeActive(true)
 const b=createSceneBuilder(h);builders.push(b);b.rebuild()
 return {h,project,root:h.registries.renderRootMap.get('m_30')!,other:h.registries.renderRootMap.get('other-cdu')!}
}
function select(h:SceneHost,node:HighlightSelection['node']=null,uids=['m_30'],active:string|null='m_30',fades:ViewFades=NO_VIEW_FADES){
 const fn=paintSelectionHighlights as unknown as (roots:typeof h.registries.renderRootMap,s:HighlightSelection,span:(uid:string)=>()=>null,f:ViewFades,kind:(uid:string)=>string|undefined)=>void
 fn(h.registries.renderRootMap,{uid:active,node,uids:new Set(uids)},()=>()=>null,fades,uid=>String(h.registries.modelMap.get(uid)?.kind??''))
}
describe('whole CDU readable material highlight',()=>{
 it('revives the real saved no-kind CDU through projectStore and keeps the same scene registry object',()=>{
  const saved=fixture.models.find(m=>m.uid==='m_30')!
  expect(Object.hasOwn(saved,'kind')).toBe(false)
  const {h,project}=scene(),hit=store.findConstruction(project,'m_30')!
  expect(hit.bucket).toBe('cdus')
  expect(hit.model.kind).toBe('cdu')
  expect(Object.getOwnPropertyDescriptor(hit.model,'kind')?.enumerable).toBe(false)
  expect(h.registries.modelMap.get('m_30')).toBe(hit.model)
  expect(h.registries.modelMap.get('m_30')?.kind).toBe('cdu')
  expect(JSON.parse(JSON.stringify(project)).constructions.cdus.m_30.kind).toBeUndefined()
  expect(store.isDirty()).toBe(false)
 })
 it('paints actual saved m30 structural and detail surfaces and restores every original material',()=>{
  const {h,project,root}=scene(),before=JSON.stringify(project),all=meshes(root)
  expect(all.some(m=>m.userData.exportName)).toBe(true)
  expect(all.some(m=>m.userData.liquidVisualDetail)).toBe(true)
  const originals=all.map(m=>m.material),maps=new THREE.Texture()
  for(const m of all)for(const mat of materials(m)){mat.map=maps;if(mat.isMeshStandardMaterial){mat.normalMap=maps;mat.roughnessMap=maps;mat.metalnessMap=maps;mat.emissiveMap=maps;mat.metalness=.8}}
  select(h)
  all.forEach((m,i)=>{expect(m.material).not.toBe(originals[i]);for(const mat of materials(m)){
   expect(mat.color.getHex(),m.name).toBe(COLOR);if(mat.isMeshStandardMaterial){expect(mat.metalness).toBe(0);expect(mat.roughness).toBeCloseTo(.62)}
   for(const key of ['map','normalMap','roughnessMap','metalnessMap','emissiveMap'] as const)expect(mat[key]).toBeNull()
  }})
  select(h,null,[],null)
  all.forEach((m,i)=>{expect(m.material).toBe(originals[i]);for(const mat of materials(m)){expect(mat.map).toBe(maps);if(mat.isMeshStandardMaterial){expect(mat.normalMap).toBe(maps);expect(mat.metalness).toBe(.8)}}})
  expect(JSON.stringify(project)).toBe(before)
 })
 it('keeps the ordinary face highlight narrow and textured after whole-model selection',()=>{
  const {h,root}=scene(),all=meshes(root),front=all.find(m=>m.userData.faceId==='front')!
  expect(front).toBeDefined();const original=front.material as Std,texture=new THREE.Texture();original.map=texture;original.metalness=.73
  select(h);select(h,{type:'face',uid:'m_30',faceId:'front'})
  expect((front.material as Std).map).toBe(texture);expect((front.material as Std).metalness).toBe(.73)
  for(const m of all.filter(m=>m.userData.faceId!=='front'))expect(m.userData._origMaterial).toBeUndefined()
 })
 it('isolates shared original materials from unselected equipment and supports whole-model multiselect',()=>{
  const {h,root,other}=scene(),a=meshes(root)[0]!,b=meshes(other)[0]!,shared=a.material as Std;b.material=shared
  const originalColor=shared.color.getHex(),originalMetal=shared.metalness
  select(h);expect(b.material).toBe(shared);expect(shared.color.getHex()).toBe(originalColor);expect(shared.metalness).toBe(originalMetal)
  select(h,{type:'model',uid:'m_30'},['m_30','other-cdu'])
  for(const r of [root,other])for(const m of meshes(r))for(const mat of materials(m))expect(mat.color.getHex()).toBe(COLOR)
  expect(shared.color.getHex()).toBe(originalColor)
 })
 it.each([{model:1,preview:.3},{model:.15,preview:1}])('keeps view fade opacity and depth policy during paint and restore: %j',fades=>{
  const {h,root}=scene();reapplyViewFades([root],fades)
  select(h,null,['m_30'],'m_30',fades)
  for(const m of meshes(root))for(const mat of materials(m)){expect(mat.opacity).toBeCloseTo(fades.preview<1?fades.preview:fades.model);expect(mat.depthWrite).toBe(false)}
  select(h,null,[],null,fades)
  for(const m of meshes(root))for(const mat of materials(m)){expect(mat.opacity).toBeCloseTo(fades.preview<1?fades.preview:fades.model);expect(mat.depthWrite).toBe(false)}
 })
 it('keeps model-specific opacity and coverage masks rather than making a faded cabinet opaque',()=>{
  const {h,root}=scene(),m=meshes(root)[0]!,mat=m.material as Std,alpha=new THREE.Texture()
  mat.opacity=.27;mat.transparent=true;mat.depthWrite=false;mat.alphaMap=alpha;mat.alphaTest=.4
  select(h);const highlighted=m.material as Std
  expect(highlighted.opacity).toBe(.27);expect(highlighted.transparent).toBe(true);expect(highlighted.depthWrite).toBe(false)
  expect(highlighted.alphaMap).toBe(alpha);expect(highlighted.alphaTest).toBe(.4)
  select(h,null,[],null);expect(m.material).toBe(mat);expect(mat.opacity).toBe(.27)
 })
 it('does not recolour the CDU anchor helper as an equipment surface',()=>{
  const {h,root}=scene(),mat=new THREE.MeshBasicMaterial({color:0xff9900})
  const anchor=new THREE.Mesh(new THREE.SphereGeometry(.02),mat)
  anchor.userData={modelId:'m_30',isAnchorMarker:true};root.add(anchor)
  select(h);expect(anchor.material).toBe(mat);expect(mat.color.getHex()).toBe(0xff9900)
  select(h,null,[],null);expect(anchor.material).toBe(mat)
 })
 it.each(['tint','solid'] as const)('keeps non-CDU material-array %s behaviour unchanged',style=>{
  const {h,root}=scene(),m=meshes(root)[0]!,texture=new THREE.Texture()
  const original=[new THREE.MeshStandardMaterial({color:0x234567,metalness:.8,map:texture}),new THREE.MeshStandardMaterial({color:0xabcdef,metalness:.6})]
  m.material=original
  m.userData={...m.userData,isRackEquipment:true,equipmentId:'e-test',faceId:'front'}
  const node=style==='solid'?{type:'serverFace' as const,uid:'m_30',equipmentId:'e-test',faceId:'front'}:null
  paintSelectionHighlights(h.registries.renderRootMap,{uid:'m_30',node,uids:new Set(['m_30'])},()=>()=>null,NO_VIEW_FADES,()=> 'box')
  expect(m.material).toBe(original);expect(original[0]!.color.getHex()).toBe(0x234567)
  expect(original[0]!.metalness).toBe(.8);expect(original[0]!.map).toBe(texture)
  expect(original[0]!.emissive.getHex()).toBe(0);expect(m.userData._origMaterial).toBeUndefined()
 })
 it('restores every original array slot and alpha mask when demoted to ordinary face selection',()=>{
  const {h,root}=scene(),m=meshes(root)[0]!,map=new THREE.Texture(),alpha=new THREE.Texture()
  const original=[new THREE.MeshStandardMaterial({map,alphaMap:alpha,alphaTest:.3,opacity:.4,transparent:true}),new THREE.MeshStandardMaterial({metalness:.9,opacity:.8,transparent:true})]
  m.material=original;m.userData.faceId='front'
  select(h);const painted=materials(m),dispose=painted.map(mat=>vi.spyOn(mat,'dispose'))
  select(h,{type:'face',uid:'m_30',faceId:'front'})
  expect(m.material).toBe(original);expect(materials(m)[0]).toBe(original[0]);expect(materials(m)[1]).toBe(original[1])
  expect(original[0]!.map).toBe(map);expect(original[0]!.alphaMap).toBe(alpha);expect(original[0]!.alphaTest).toBe(.3)
  expect(original.map(mat=>mat.opacity)).toEqual([.4,.8]);expect(original[1]!.metalness).toBe(.9)
  for(const fn of dispose)expect(fn).toHaveBeenCalledOnce()
  expect(m.userData._origMaterial).toBeUndefined()
 })
 it('restores grouped material arrays and disposes only temporary highlight clones',()=>{
  const {h,root}=scene(),m=meshes(root)[0]!,original=[new THREE.MeshStandardMaterial({metalness:.8}),new THREE.MeshStandardMaterial({metalness:.6})];m.material=original
  const originalDispose=original.map(mat=>vi.spyOn(mat,'dispose'))
  select(h);const selected=materials(m);expect(m.material).not.toBe(original)
  for(const mat of selected)expect(mat.color.getHex()).toBe(COLOR)
  const cloneDispose=selected.map(mat=>vi.spyOn(mat,'dispose'))
  select(h,null,[],null);expect(m.material).toBe(original)
  for(const fn of cloneDispose)expect(fn).toHaveBeenCalledOnce()
  for(const fn of originalDispose)expect(fn).not.toHaveBeenCalled()
 })
})
