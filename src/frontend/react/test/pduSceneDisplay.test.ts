import * as THREE from 'three'
import {afterEach,describe,expect,it,vi} from 'vitest'
import fixture from './fixtures/pduAlibaba.json'
import {type CfdProject} from '../src/schema/cfdproj'
import {stateV4} from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {createSceneBuilder,emptyRegistries,type SceneHost} from '../src/interaction/sceneBuilder'
import {setLiquidModeActive} from '../src/liquid/liquidModeState'
import {pbrMaterials} from '../src/legacy/geometryBuilder'
import {paintSelectionHighlights} from '../src/interaction/selectionHighlight'
import {NO_VIEW_FADES,reapplyViewFades} from '../src/geometry/viewFades'
import {applyOpacityToMeshes} from '../src/geometry/modelOpacity'
import {updateFace} from '../src/geometry/patchPhysics'
import {buildStlFiles} from '../src/io/stlExport'
import {buildSceneSnapshot} from '../src/renderer/sceneSnapshot'
import {isSelectableHit,pickNearestHit,viewportSelectionFor} from '../src/interaction/picking'
import {isAdditiveSelectGesture} from '../src/interaction/rackFaceGesture'
import * as visuals from '../src/geometry/pduEquipmentVisuals'
import {createHash} from 'node:crypto'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
const builders:Array<ReturnType<typeof createSceneBuilder>>=[]
function meshes(root:THREE.Object3D){const out:THREE.Mesh[]=[];root.traverse(o=>{if((o as THREE.Mesh).isMesh)out.push(o as THREE.Mesh)});return out}
afterEach(()=>{for(const b of builders.splice(0))b.clear();setLiquidModeActive(false);pbrMaterials()._resetForTests();vi.restoreAllMocks()})
function scene(liquid=false,extra=false){
 const raw=stateV4().getInitialStateV4() as unknown as CfdProject
 raw.constructions!.boxes={m_252:structuredClone(fixture.model)}
 if(extra){const other=structuredClone(fixture.model);other.uid='ordinary-ups';other.meta.name='UPS_Rack';other.geometry.location.x+=2;raw.constructions!.boxes[other.uid]=other}
 store.loadProject(raw,null)
 const project=store.getProject()!,globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group();wrapperGroup.add(globalGroup)
 const h:SceneHost={globalGroup,wrapperGroup,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:false}
 setLiquidModeActive(liquid)
 const b=createSceneBuilder(h);builders.push(b);b.rebuild()
 return {h,project,b,root:h.registries.renderRootMap.get('m_252')!}
}
describe('actual saved Alibaba PDU display',()=>{
 it('keeps the owned BOX geometry and STL usable without the optional PBR library',()=>{
  const {h,project,b,root}=scene(),json=JSON.stringify(project),geometry=buffers(h),stl=buildStlFiles(b,project)
  const library=pbrMaterials(),globals=window as unknown as {pbrMaterials?:typeof library}
  expect(meshes(root).some(m=>m.userData.equipmentVisualDetail)).toBe(true)
  try{
   globals.pbrMaterials=undefined
   expect(()=>b.rebuild()).not.toThrow()
   expect(buffers(h)).toEqual(geometry);expect(buildStlFiles(b,project)).toEqual(stl)
   expect(meshes(h.globalGroup).some(m=>m.userData.equipmentVisualDetail)).toBe(false)
   const packet=buildSceneSnapshot({root:h.globalGroup,project,helpers:h.registries.renderHelpers})
   expect(packet.primitives.length).toBeGreaterThan(0)
   expect(packet.primitives.every(p=>p.modelUid==='m_252')).toBe(true)
   expect(JSON.stringify(project)).toBe(json);expect(store.isDirty()).toBe(false)
  }finally{globals.pbrMaterials=library}
  b.rebuild();expect(buffers(h)).toEqual(geometry);expect(buildStlFiles(b,project)).toEqual(stl)
  expect(meshes(h.globalGroup).some(m=>m.userData.equipmentVisualDetail)).toBe(true)
 })
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

function buffers(h:SceneHost){
 return h.registries.renderMeshes.map(o=>{const m=o as THREE.Mesh;return {data:structuredClone(m.userData),matrix:m.matrixWorld.toArray(),position:Array.from(m.geometry.getAttribute('position').array),normal:Array.from(m.geometry.getAttribute('normal').array),index:m.geometry.index?Array.from(m.geometry.index.array):null}})
}
function fakeTexture(tag:string){return new THREE.Texture({width:2,height:2,toDataURL:()=> 'data:image/png;base64,'+btoa(tag)} as unknown as HTMLCanvasElement)}
describe('PDU display keeps selection ownership, physics and shared OV buffers',()=>{
 it('fixture is the exact original PDU payload in the tracked Alibaba source',()=>{
  const data=readFileSync(resolve('../../../'+fixture.source.path))
  // Full-file provenance is recorded by the fixture and receipt. Main's
  // protected copy has a legitimate meta.casePath difference; prove the
  // actual model payload rather than treating routing metadata as physics.
  const actual=JSON.parse(data.toString()).constructions.boxes.m_252
  expect(JSON.stringify(actual)).toBe(JSON.stringify(fixture.model))
  expect(createHash('sha256').update(JSON.stringify(actual)).digest('hex')).toBe(createHash('sha256').update(JSON.stringify(fixture.model)).digest('hex'))
 })
 it('has identical structural registry buffers, grouped STL bytes and saved JSON with the display builder disabled',()=>{
  const {h,project,b,root}=scene(),json=JSON.stringify(project),geometry=buffers(h),stl=buildStlFiles(b,project)
  expect(stl.totalFacets).toBe(12)
  expect(meshes(root).filter(m=>m.userData.equipmentVisualDetail).length).toBeGreaterThan(20)
  // The scene builder calls buildPowerCabinetVisual (PDU and UPS, dc54654ed).
  const bypass=vi.spyOn(visuals,'buildPowerCabinetVisual').mockReturnValue(null)
  b.rebuild();expect(buffers(h)).toEqual(geometry);expect(buildStlFiles(b,project)).toEqual(stl)
  expect(JSON.stringify(project)).toBe(json);expect(store.isDirty()).toBe(false)
  expect(h.registries.renderRootMap.get('m_252')!.userData.equipmentVisualRole).toBeUndefined()
  bypass.mockRestore();b.rebuild();expect(buffers(h)).toEqual(geometry);expect(buildStlFiles(b,project)).toEqual(stl)
 })
 it.each([false,true])('visible HMI detail is a real owner hit under world rotation/scale and hidden-parent rejection, liquid=%s',liquid=>{
  const {h,project,root}=scene(liquid),before=JSON.stringify(project),model=h.registries.modelMap.get('m_252')!
  root.quaternion.setFromAxisAngle(new THREE.Vector3(0,1,0),.7);root.scale.set(1.3,.9,.8);root.updateMatrixWorld(true)
  const local=new THREE.Vector3(.6,2.35*.79+.02,-.4),origin=root.localToWorld(local),dir=new THREE.Vector3(0,0,1).transformDirection(root.matrixWorld)
  const ray=new THREE.Raycaster(origin,dir),hits=ray.intersectObject(root,true).filter(isSelectableHit)
  expect(hits.length).toBeGreaterThan(0)
  const picked=pickNearestHit(hits)!
  expect(picked.object.userData.modelId).toBe('m_252')
  expect(picked.object.userData.equipmentVisualDetail).toBe(true)
  expect(isAdditiveSelectGesture({shiftKey:true,ctrlKey:false,metaKey:false},String(model.kind))).toBe(false)
  root.visible=false
  expect(ray.intersectObject(root,true).filter(isSelectableHit)).toHaveLength(0)
  root.visible=true;h.globalGroup.visible=false
  expect(ray.intersectObject(root,true).filter(isSelectableHit)).toHaveLength(0)
  expect(JSON.stringify(project)).toBe(before);expect(store.isDirty()).toBe(false)
 })
 it('fades actual structure and details, including 0%, without recolouring a neighbouring UPS cabinet',()=>{
  const {h,root}=scene(false,true),other=h.registries.renderRootMap.get('ordinary-ups')!
  // A BOX named UPS* carries its own display-only cabinet skin (owner draft
  // POWER-CABINET-UPS-VISUAL, promoted in dc54654ed); the PDU's paint never reaches it.
  expect(other.userData.equipmentVisualRole).toBe('ups')
  expect(meshes(other).some(m=>m.userData.equipmentVisualDetail)).toBe(true)
  const ordinary=meshes(other).map(m=>m.material)
  for(const percent of [27,0,100]){
   applyOpacityToMeshes(root,percent)
   paintSelectionHighlights(h.registries.renderRootMap,{uid:'m_252',node:null,uids:new Set(['m_252'])},()=>()=>null,NO_VIEW_FADES,()=> 'box')
   for(const mesh of meshes(root))for(const mat of Array.isArray(mesh.material)?mesh.material:[mesh.material]){
    expect(mat.opacity,mesh.name).toBeCloseTo(percent/100)
    expect(mat.depthWrite).toBe(percent===100)
   }
   paintSelectionHighlights(h.registries.renderRootMap,{uid:null,node:null,uids:new Set()},()=>()=>null,NO_VIEW_FADES,()=> 'box')
  }
  // View fades follow the existing absolute preview-opacity contract, rather
  // than multiplying a saved model opacity. Painting/restoring keeps that cap.
  const fades={model:1,preview:.3};reapplyViewFades([root],fades)
  for(const selected of [true,false]){
   paintSelectionHighlights(h.registries.renderRootMap,{uid:selected?'m_252':null,node:null,uids:new Set(selected?['m_252']:[])},()=>()=>null,fades,()=> 'box')
   for(const mesh of meshes(root))for(const mat of Array.isArray(mesh.material)?mesh.material:[mesh.material]){
    expect(mat.opacity,mesh.name).toBeCloseTo(.3);expect(mat.depthWrite).toBe(false)
   }
  }
  meshes(other).forEach((m,i)=>expect(m.material).toBe(ordinary[i]))
 })
 it('keeps ordinary face selection narrow after the full PDU highlight',()=>{
  const {h,root}=scene(),all=meshes(root),originals=all.map(m=>m.material)
  paintSelectionHighlights(h.registries.renderRootMap,{uid:'m_252',node:null,uids:new Set(['m_252'])},()=>()=>null,NO_VIEW_FADES,()=> 'box')
  paintSelectionHighlights(h.registries.renderRootMap,{uid:'m_252',node:{type:'face',uid:'m_252',faceId:'front'},uids:new Set(['m_252'])},()=>()=>null,NO_VIEW_FADES,()=> 'box')
  all.forEach((m,i)=>{
   if(m.userData.faceId!=='front')expect(m.material).toBe(originals[i])
   else for(const mat of Array.isArray(m.material)?m.material:[m.material])expect((mat as THREE.MeshStandardMaterial).color.getHex()).toBe(0x66ead0)
  })
  for(const m of all.filter(x=>x.userData.equipmentVisualDetail&&x.userData.faceId!=='front'))expect(m.userData._origMaterial).toBeUndefined()
 })
 it('shares the actual textured buffers/materials with OV snapshot while excluding them from STL registry',()=>{
  const lib=pbrMaterials()
  for(const set of Object.keys(lib.SETS))for(const map of ['diffuse','normal','roughness'] as const)lib.registerTexture(set,map,fakeTexture(set+'/'+map))
  const {h,root,project}=scene(),json=JSON.stringify(project),detail=meshes(root).filter(m=>m.userData.equipmentVisualDetail)
  const packet=buildSceneSnapshot({root:h.globalGroup,project,helpers:h.registries.renderHelpers})
  for(const mesh of detail){
   const primitives=packet.primitives.filter(p=>p.name===mesh.name)
   expect(primitives.length).toBeGreaterThan(0)
   for(const p of primitives){expect(p.modelUid).toBe('m_252');expect(p.positions.every(Number.isFinite)).toBe(true);expect(p.uvs?.every(Number.isFinite)).toBe(true)}
   expect(h.registries.renderMeshes).not.toContain(mesh)
  }
  const cabinet=packet.primitives.find(p=>p.name==='generic-pdu-cabinet')!
  expect(cabinet.material.map&&cabinet.material.normalMap&&cabinet.material.roughnessMap).toBeTruthy()
  expect(cabinet.material.metalness).toBe(.2)
  const live=detail.find(m=>m.name==='generic-pdu-cabinet')!.material as THREE.MeshStandardMaterial
  expect(cabinet.material.roughness).toBe(live.roughness)
  expect(cabinet.material.normalScale).toEqual([.16,.16])
  const screen=packet.primitives.find(p=>p.name==='generic-pdu-hmi-screen')!
  expect(screen.material.unlit).toBe(true)
  expect(JSON.stringify(project)).toBe(json);expect(store.isDirty()).toBe(false)
 })
})

describe('original BOX face editing through visible PDU panels',()=>{
 it.each([
  {face:'front',origin:[.6,1.4,-.4],direction:[0,0,1]},
  {face:'back',origin:[.6,1.4,1],direction:[0,0,-1]},
  {face:'left',origin:[-.4,1.4,.3],direction:[1,0,0]},
  {face:'right',origin:[1.6,1.4,.3],direction:[-1,0,0]},
  {face:'top',origin:[.6,2.75,.3],direction:[0,-1,0]},
  {face:'bottom',origin:[.6,-.4,.3],direction:[0,1,0]},
 ])('preserves real $face face picking and visible narrow highlight without changing Shift grammar',({face,origin,direction})=>{
  const {h,root,project}=scene(),before=JSON.stringify(project)
  root.updateMatrixWorld(true)
  const ray=new THREE.Raycaster(root.localToWorld(new THREE.Vector3(...origin)),new THREE.Vector3(...direction).transformDirection(root.matrixWorld))
  const hit=pickNearestHit(ray.intersectObject(root,true).filter(isSelectableHit))!
  const node=viewportSelectionFor('m_252',hit.object.userData,h.registries.modelMap.get('m_252'))
  expect(node).toEqual({type:'face',uid:'m_252',faceId:face})
  expect(isAdditiveSelectGesture({shiftKey:true,ctrlKey:false,metaKey:false},'box')).toBe(false)
  const all=meshes(root),originals=all.map(m=>m.material)
  paintSelectionHighlights(h.registries.renderRootMap,{uid:'m_252',node,uids:new Set(['m_252'])},()=>()=>null,NO_VIEW_FADES,()=> 'box')
  const visiblePaint=all.filter(m=>m.userData.equipmentVisualDetail&&m.userData.faceId===face)
  expect(visiblePaint.length).toBeGreaterThan(0)
  for(const m of visiblePaint)for(const mat of Array.isArray(m.material)?m.material:[m.material])expect((mat as THREE.MeshStandardMaterial).color.getHex()).toBe(0x66ead0)
  all.forEach((m,i)=>{if(m.userData.faceId!==face)expect(m.material).toBe(originals[i])})
  expect(JSON.stringify(project)).toBe(before)
 })

 it('plain visible HMI hit reaches the original editable front face through the real Viewport resolution table',()=>{
  const {h,root}=scene()
  root.updateMatrixWorld(true)
  const origin=root.localToWorld(new THREE.Vector3(.6,2.35*.79+.02,-.4)),dir=new THREE.Vector3(0,0,1).transformDirection(root.matrixWorld)
  const hit=pickNearestHit(new THREE.Raycaster(origin,dir).intersectObject(root,true).filter(isSelectableHit))!
  expect(hit.object.userData.equipmentVisualDetail).toBe(true)
  expect(viewportSelectionFor('m_252',hit.object.userData,h.registries.modelMap.get('m_252'))).toEqual({type:'face',uid:'m_252',faceId:'front'})
 })
})

describe('PDU visual eligibility does not survive a changed original BOX',()=>{
 it.each(['rename','patch'] as const)('clears role and detail on fresh and targeted roots after %s',change=>{
  const {h,project,b,root}=scene(),model=store.findConstruction(project,'m_252')!.model
  if(change==='rename')model.meta!.name='Storage_Box'
  else (model.geometry!.faces!.front as {patches:unknown[]}).patches=[{id:'authored-physical-patch',type:'rect',name:'Authored',color:'#c00000',x:.2,y:.4,w:.2,h:.2}]
  b.rebuildModels(['m_252'])
  const targeted=h.registries.renderRootMap.get('m_252')!
  expect(targeted).not.toBe(root);expect(targeted.userData.equipmentVisualRole).toBeUndefined()
  expect(meshes(targeted).some(m=>m.userData.equipmentVisualDetail)).toBe(false)
  b.rebuild()
  const full=h.registries.renderRootMap.get('m_252')!
  expect(full).not.toBe(targeted);expect(full.userData.equipmentVisualRole).toBeUndefined()
  expect(meshes(full).some(m=>m.userData.equipmentVisualDetail)).toBe(false)
  expect(store.findConstruction(project,'m_252')!.model.kind).toBe('box')
 })
 it('switches a PDU renamed to UPS* to the UPS cabinet skin',()=>{
  const {h,project,b}=scene(),model=store.findConstruction(project,'m_252')!.model
  model.meta!.name='UPS_Rack'
  b.rebuildModels(['m_252'])
  const root=h.registries.renderRootMap.get('m_252')!,detail=meshes(root).filter(m=>m.userData.equipmentVisualDetail)
  expect(root.userData.equipmentVisualRole).toBe('ups')
  expect(detail.length).toBeGreaterThan(20)
  for(const m of detail){expect(m.name).toMatch(/^generic-ups-/);expect(m.userData.equipmentVisualRole).toBe('ups')}
  expect(store.findConstruction(project,'m_252')!.model.kind).toBe('box')
 })
})

describe('PDU local material lifetime',()=>{
 it('keeps local powder-coat normal strength when actual pending PBR maps bind later',()=>{
  vi.spyOn(THREE.TextureLoader.prototype,'load').mockImplementation(()=>new THREE.Texture())
  pbrMaterials().loadTextureSets('pdu-pending-texture-test')
  const {root}=scene(),painted=meshes(root).filter(m=>(m.material as THREE.MeshStandardMaterial).userData.pbrRole==='painted-steel')
  expect(painted.length).toBeGreaterThan(0)
  for(const mat of painted.map(m=>m.material as THREE.MeshStandardMaterial))expect(mat.normalScale.toArray()).toEqual([.16,.16])
  const lib=pbrMaterials()
  for(const map of ['diffuse','normal','roughness'] as const)lib.registerTexture('painted-steel',map,fakeTexture('late/'+map))
  for(const mat of painted.map(m=>m.material as THREE.MeshStandardMaterial)){
   expect(mat.normalMap).toBeTruthy();expect(mat.normalScale.toArray()).toEqual([.16,.16])
  }
 })
 it.each([false,true])('clears selected PDU private original, retained fade and highlight materials once; faded=%s',faded=>{
  const {h,root,b}=scene(),detail=meshes(root).filter(m=>m.userData.equipmentVisualDetail)
  const initial=detail.map(m=>m.material as THREE.Material)
  const first=initial.map(m=>vi.spyOn(m,'dispose'))
  if(faded)applyOpacityToMeshes(root,27)
  const retained=detail.map(m=>m.material as THREE.Material)
  const middle=retained.map((m,i)=>m===initial[i]?first[i]!:vi.spyOn(m,'dispose'))
  paintSelectionHighlights(h.registries.renderRootMap,{uid:'m_252',node:null,uids:new Set(['m_252'])},()=>()=>null,NO_VIEW_FADES,()=> 'box')
  const current=detail.map(m=>m.material as THREE.Material),last=current.map(m=>vi.spyOn(m,'dispose'))
  b.clear()
  for(const calls of [first,middle,last])for(const spy of calls)expect(spy).toHaveBeenCalledOnce()
 })
})

describe('existing BOX face opacity stays effective on PDU display skins',()=>{
 it.each([0,.35])('respects actual FaceInspector updateFace(front, opacity, %s) after targeted rebuild',opacity=>{
  const {h,b}=scene()
  h.project=()=>store.getProject()!
  updateFace('m_252','front','opacity',opacity)
  const project=store.getProject()!
  expect((store.findConstruction(project,'m_252')!.model.geometry!.faces!.front as {opacity:number}).opacity).toBe(opacity)
  b.rebuildModels(['m_252'])
  const detail=meshes(h.registries.renderRootMap.get('m_252')!).filter(m=>m.userData.equipmentVisualDetail)
  expect(detail.filter(m=>m.userData.faceId==='front').length).toBeGreaterThan(0)
  for(const mesh of detail){
   const mat=mesh.material as THREE.Material
   expect(mat.opacity,mesh.name).toBe(mesh.userData.faceId==='front'?opacity:1)
   expect(mat.depthWrite,mesh.name).toBe(mesh.userData.faceId==='front'?opacity===1:true)
  }
 })
})
