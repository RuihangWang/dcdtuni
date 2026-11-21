import {it,expect,vi} from 'vitest'
import * as THREE from 'three'
import {createHash} from 'node:crypto'
import {createSceneBuilder,emptyRegistries} from '../src/interaction/sceneBuilder'
import {geometryPure} from '../src/legacy/geometryBuilder'
import type {CfdProject} from '../src/schema/cfdproj'
import golden from './fixtures/baffleProductOriginalCarrier.json'
const bytes=(v:ArrayBufferView)=>Buffer.from(v.buffer,v.byteOffset,v.byteLength).toString('hex')
for(const sample of golden.cases)it('original carrier/STL byte parity: '+sample.label,()=>{
 const context=new Proxy({}, {get(_target,key){
  if(key==='createLinearGradient'||key==='createRadialGradient')return ()=>({addColorStop(){}})
  if(key==='getImageData'||key==='createImageData')return (...args:number[])=>({data:new Uint8ClampedArray((args.at(-2)??1)*(args.at(-1)??1)*4)})
  return ()=>undefined
 }})
 const canvas=vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(()=>context as CanvasRenderingContext2D)
 const texture=vi.spyOn(THREE.TextureLoader.prototype,'load').mockImplementation(()=>new THREE.Texture())
 const model=structuredClone(sample.model)
 const doc={schemaVersion:5,uid:'proof',constructions:{baffles:{[model.uid]:model}}} as unknown as CfdProject
 const globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group();wrapperGroup.add(globalGroup)
 const registries=emptyRegistries(),builder=createSceneBuilder({globalGroup,wrapperGroup,registries,project:()=>doc,selectedUid:()=>null,webglAvailable:true})
 const group=new THREE.Group();globalGroup.add(group)
 const pure=geometryPure() as any
 pure.applyModelTransformToObject(group,model);group.position.set(model.geometry.location.x,model.geometry.location.y,model.geometry.location.z)
 try{
  ;(builder.api as any).renderBaffleModel(model,group,pure.getFaceLayouts(model));wrapperGroup.updateMatrixWorld(true)
  expect(registries.renderMeshes).toHaveLength(sample.expected.parts.length)
  registries.renderMeshes.forEach((mesh,i)=>{
   const original=sample.expected.parts[i]!,g=(mesh as THREE.Mesh).geometry as THREE.BufferGeometry,m=(mesh as THREE.Mesh).material as THREE.MeshStandardMaterial
   expect(bytes(g.getAttribute('position').array)).toBe(original.position)
   expect(bytes(g.getAttribute('normal').array)).toBe(original.normal)
   expect(bytes(g.getAttribute('uv').array)).toBe(original.uv)
   expect(bytes(g.getIndex()!.array)).toBe(original.index)
   expect(mesh.userData).toMatchObject({faceId:original.faceId,exportName:original.exportName,refIds:original.refIds,isBaseWall:original.isBaseWall})
   expect(mesh.renderOrder).toBe(original.renderOrder)
   // Re-frozen 2026-10-21 for the textures/materials.json PBR redesign: the material, and the
   // UVs of a containment panel (now stretched over the face), changed; positions, normals,
   // indices, STL and the source model did not (_tmp/campaign_M/regen_baffle_fixture.py).
   expect({type:m.type,color:m.color.getHex(),opacity:m.opacity,transparent:m.transparent,depthWrite:m.depthWrite,side:m.side,roughness:m.roughness,metalness:m.metalness,polygonOffset:m.polygonOffset,polygonOffsetFactor:m.polygonOffsetFactor,kind:m.userData.materialKind??null,mapRepeat:m.map?.repeat.toArray()??null}).toEqual(original.material)
  })
  const stl=(builder.api as any).exportModelToStl(model,false,registries.renderMeshes) as string
  expect(stl).toBe(sample.expected.stl)
  expect(createHash('sha256').update(stl).digest('hex')).toBe(sample.expected.stlSHA256)
  expect(model).toEqual(sample.expected.sourceAfterCarrier)
 }finally{group.traverse(x=>{if((x as THREE.Mesh).isMesh)(x as THREE.Mesh).geometry.dispose()});builder.clear();canvas.mockRestore();texture.mockRestore()}
})
