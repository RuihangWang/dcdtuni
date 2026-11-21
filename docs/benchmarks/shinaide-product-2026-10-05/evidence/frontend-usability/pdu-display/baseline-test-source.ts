import * as THREE from 'three'
import {afterEach,describe,expect,it} from 'vitest'
import fixture from './fixtures/pduAlibaba.json'
import {type CfdProject} from '../src/schema/cfdproj'
import {stateV4} from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {createSceneBuilder,emptyRegistries,type SceneHost} from '../src/interaction/sceneBuilder'
import {setLiquidModeActive} from '../src/liquid/liquidModeState'
import {pbrMaterials} from '../src/legacy/geometryBuilder'
import {paintSelectionHighlights} from '../src/interaction/selectionHighlight'
import {NO_VIEW_FADES} from '../src/geometry/viewFades'
const builders:Array<ReturnType<typeof createSceneBuilder>>=[]
function meshes(root:THREE.Object3D){const out:THREE.Mesh[]=[];root.traverse(o=>{if((o as THREE.Mesh).isMesh)out.push(o as THREE.Mesh)});return out}
afterEach(()=>{for(const b of builders.splice(0))b.clear();setLiquidModeActive(false);pbrMaterials()._resetForTests()})
function scene(liquid=false){
 const raw=stateV4().getInitialStateV4() as unknown as CfdProject
 raw.constructions!.boxes={m_252:structuredClone(fixture.model)}
 store.loadProject(raw,null)
 const project=store.getProject()!,globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group();wrapperGroup.add(globalGroup)
 const h:SceneHost={globalGroup,wrapperGroup,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:false}
 setLiquidModeActive(liquid)
 const b=createSceneBuilder(h);builders.push(b);b.rebuild()
 return {h,project,b,root:h.registries.renderRootMap.get('m_252')!}
}
describe('actual saved Alibaba PDU display',()=>{
 it('loads the original no-kind PDU as BOX without changing saved physics',()=>{
  expect(Object.hasOwn(fixture.model,'kind')).toBe(false)
  const {h,project}=scene(),found=store.findConstruction(project,'m_252')!
  expect(found.bucket).toBe('boxes');expect(found.model.kind).toBe('box')
  expect(Object.getOwnPropertyDescriptor(found.model,'kind')?.enumerable).toBe(false)
  expect(h.registries.modelMap.get('m_252')).toBe(found.model)
  expect(JSON.parse(JSON.stringify(project)).constructions.boxes.m_252).toEqual(fixture.model)
  expect(store.isDirty()).toBe(false)
 })
 it.each([false,true])('adds display-only PDU surfaces in liquid mode %s',liquid=>{
  const {h,root}=scene(liquid),detail=meshes(root).filter(m=>m.userData.equipmentVisualDetail)
  expect(root.userData.equipmentVisualRole).toBe('pdu')
  expect(detail.length).toBeGreaterThan(20)
  for(const m of detail){
   expect(m.userData).toMatchObject({modelId:'m_252',selectable:true,exportExcluded:true})
   expect(h.registries.renderMeshes).not.toContain(m)
  }
 })
 it('whole BOX PDU selection paints its structure and details and restores original PBR references',()=>{
  const {h,root,project}=scene(),all=meshes(root),before=JSON.stringify(project),originals=all.map(m=>m.material)
  expect(all.some(m=>m.userData.equipmentVisualDetail)).toBe(true)
  paintSelectionHighlights(h.registries.renderRootMap,{uid:'m_252',node:null,uids:new Set(['m_252'])},()=>()=>null,NO_VIEW_FADES,()=> 'box')
  for(const m of all)for(const mat of (Array.isArray(m.material)?m.material:[m.material]) as THREE.MeshStandardMaterial[]){
   expect(mat.color.getHex(),m.name).toBe(0x66ead0);if(mat.isMeshStandardMaterial)expect(mat.metalness).toBe(0)
  }
  paintSelectionHighlights(h.registries.renderRootMap,{uid:null,node:null,uids:new Set()},()=>()=>null,NO_VIEW_FADES,()=> 'box')
  all.forEach((m,i)=>expect(m.material).toBe(originals[i]))
  expect(JSON.stringify(project)).toBe(before);expect(store.isDirty()).toBe(false)
 })
})
