import * as THREE from 'three'
import {afterEach,describe,expect,it} from 'vitest'
import fixture from './fixtures/pduAlibaba.json'
import type {Construction,CfdProject} from '../src/schema/cfdproj'
import {stateV4} from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {pbrMaterials} from '../src/legacy/geometryBuilder'
import {buildPduEquipmentVisual,isPduDisplayModel} from '../src/geometry/pduEquipmentVisuals'
function model():Construction{
 const raw=stateV4().getInitialStateV4() as unknown as CfdProject
 raw.constructions!.boxes={m_252:structuredClone(fixture.model)}
 store.loadProject(raw,null)
 return store.findConstruction(store.getProject()!,'m_252')!.model
}
function meshes(root:THREE.Object3D){const out:THREE.Mesh[]=[];root.traverse(o=>{if((o as THREE.Mesh).isMesh)out.push(o as THREE.Mesh)});return out}
const groups:THREE.Group[]=[]
afterEach(()=>{for(const g of groups.splice(0))for(const m of meshes(g)){m.geometry.dispose();for(const mat of Array.isArray(m.material)?m.material:[m.material])mat.dispose()}pbrMaterials()._resetForTests()})
function visual(m:Construction){const g=buildPduEquipmentVisual(m)!;groups.push(g);return g}
describe('generic explicit-name PDU display geometry',()=>{
 it.each(['PDU',' pdu ','PDU - 125 kVA ','PDU 125kVA','PDU–125kVA','PDU—125kVA_copy_copy','PDU_copy_copy','PDU-125kVA (2)'])('recognizes an explicit PDU name %s',name=>{
  const m=model();m.meta!.name=name
  expect(isPduDisplayModel(m)).toBe(true);expect(visual(m).userData.modelId).toBe(m.uid)
 })
 it.each(['BOX','UPS_Rack','PowerStrip1','rack-strip-left','PDUx','APDU','UPS PDU','Cooling cabinet'])('does not remodel %s',name=>{
  const m=model();m.meta!.name=name
  expect(isPduDisplayModel(m)).toBe(false);expect(buildPduEquipmentVisual(m)).toBeNull()
 })
 it.each(['rack','cdu','acu','custom'])('does not replace physical kind %s even when named PDU',kind=>{
  const m=model();Object.defineProperty(m,'kind',{value:kind,configurable:true});m.meta!.name='PDU'
  expect(buildPduEquipmentVisual(m)).toBeNull()
 })
 it.each([NaN,Infinity,0,-1])('rejects invalid authored size %s without inventing bounds',x=>{
  const m=model();m.geometry!.size!.x=x
  expect(buildPduEquipmentVisual(m)).toBeNull()
 })
 it('keeps authored openings and imported shapes visible instead of covering them with a generic skin',()=>{
  const m=model();m.customSTLBase64='authored-stl'
  expect(buildPduEquipmentVisual(m)).toBeNull()
  m.customSTLBase64=null;m.geometry!.cutouts=[{}] as never
  expect(buildPduEquipmentVisual(m)).toBeNull()
  m.geometry!.cutouts=[];
  (m.geometry!.faces!.front as {patches:unknown[]}).patches=[{id:'original-patch'}]
  expect(buildPduEquipmentVisual(m)).toBeNull()
 })
 it('uses graphite powder coat, stainless hardware and finite metre-scale UVs with independent materials',()=>{
  const m=model(),json=JSON.stringify(m),g=visual(m),all=meshes(g)
  expect(all.length).toBeGreaterThan(40);expect(all.length).toBeLessThan(90)
  const cabinet=all.find(x=>x.name==='generic-pdu-cabinet')!,mat=cabinet.material as THREE.MeshStandardMaterial
  expect(mat.isMeshStandardMaterial).toBe(true);expect(mat.color.getHex()).toBe(0x252c33)
  expect(mat.userData.pbrRole).toBe('painted-steel');expect(mat.roughness).toBe(.68);expect(mat.metalness).toBe(.2)
  expect(mat.normalScale.toArray()).toEqual([.16,.16])
  expect(all.some(x=>(x.material as THREE.MeshStandardMaterial).userData.pbrRole==='stainless')).toBe(true)
  expect(new Set(all.map(x=>x.material)).size).toBe(all.length)
  const textured=all.filter(x=>(x.material as THREE.MeshStandardMaterial).userData.pbrRole)
  for(const mesh of textured){
   const pos=mesh.geometry.getAttribute('position'),uv=mesh.geometry.getAttribute('uv'),n=mesh.geometry.getAttribute('normal')
   const tile=pbrMaterials().ROLES[(mesh.material as THREE.MeshStandardMaterial).userData.pbrRole]!.tileM
   expect(uv.count).toBe(pos.count)
   for(let i=0;i<pos.count;i++){
    const nx=Math.abs(n.getX(i)),ny=Math.abs(n.getY(i)),nz=Math.abs(n.getZ(i))
    const u=nx>=ny&&nx>=nz?pos.getZ(i)/tile:pos.getX(i)/tile
    const v=ny>=nx&&ny>=nz?pos.getZ(i)/tile:pos.getY(i)/tile
    expect(uv.getX(i)).toBeCloseTo(u,4);expect(uv.getY(i)).toBeCloseTo(v,4)
   }
  }
  expect(JSON.stringify(m)).toBe(json)
 })
 it.each([{x:1.2,y:2.35,z:.6},{x:.4,y:.8,z:.2},{x:2,y:3,z:1}])('keeps a bounded sheet/hardware envelope for %j',size=>{
  const m=model();m.geometry!.size=size;const g=visual(m);g.updateMatrixWorld(true)
  const bounds=new THREE.Box3().setFromObject(g),skin=Math.min(.0018,Math.min(size.x,size.y,size.z)*.003)
  expect(bounds.min.x).toBeGreaterThanOrEqual(-skin-1e-6);expect(bounds.max.x).toBeLessThanOrEqual(size.x+skin+1e-6)
  expect(bounds.min.y).toBeGreaterThanOrEqual(-1e-6);expect(bounds.max.y).toBeLessThanOrEqual(size.y+skin/4+1e-6)
  expect(bounds.max.y).toBeGreaterThan(size.y)
  expect(bounds.min.z).toBeGreaterThanOrEqual(-Math.max(.016,size.z*.025)-1e-6)
  expect(bounds.max.z).toBeLessThanOrEqual(size.z+skin+1e-6)
  const side=meshes(g).find(x=>x.name==='generic-pdu-cabinet')!;side.geometry.computeBoundingBox()
  expect(side.geometry.boundingBox!.getSize(new THREE.Vector3()).x).toBeCloseTo(size.x+2*skin,6)
 })
 it('keeps the illustrative HMI unlit and exposure-independent without displaying fabricated telemetry',()=>{
  const g=visual(model()),screen=meshes(g).find(m=>m.name==='generic-pdu-hmi-screen')!,mat=screen.material as THREE.MeshBasicMaterial
  expect(mat.isMeshBasicMaterial).toBe(true);expect(mat.toneMapped).toBe(false)
  expect(mat.color.getHex()).toBe(0x1b3847)
  expect(screen.userData.telemetry).toBeUndefined()
  for(const line of meshes(g).filter(m=>m.name.startsWith('generic-pdu-hmi-line')))expect((line.material as THREE.MeshBasicMaterial).toneMapped).toBe(false)
 })
 it('uses the existing owner generically and initializes display opacity from the original model',()=>{
  const m=model();m.uid='copied-pdu-owner';m.meta!.display!.opacity=.24
  const json=JSON.stringify(m),g=visual(m)
  for(const mesh of meshes(g)){
   expect(mesh.userData).toMatchObject({modelId:'copied-pdu-owner',selectable:true,exportExcluded:true,equipmentVisualDetail:true})
   expect(mesh.userData.exportName).toBeUndefined()
   const mat=mesh.material as THREE.MeshStandardMaterial
   expect(mat.opacity).toBe(.24);expect(mat.transparent).toBe(true);expect(mat.depthWrite).toBe(false)
  }
  expect(JSON.stringify(m)).toBe(json)
 })
})
