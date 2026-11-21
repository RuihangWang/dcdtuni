import * as THREE from 'three'
import {createHash} from 'node:crypto'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import fixture from '../fixtures/closedCabinetShinaide.json'
import {installCabinetPipeDisplay,cabinetIsClosed} from '../../src/legacy/liquidScene'
import {setRenderRootLookup,applyOpacityToMeshes} from '../../src/geometry/modelOpacity'
import {applyMaterialOpacity} from '../../src/geometry/viewFades'
import {setShowCabinetPipes,showCabinetPipes,registerCabinetDisplayRefresh,
  closedCabinetDiagnostics,leafDisplayRevision} from '../../src/liquid/closedCabinetDisplay'
import type {Construction} from '../../src/schema/cfdproj'
import {buildSceneSnapshot} from '../../src/renderer/sceneSnapshot'
type Bag = Record<string, any>
let group: THREE.Group
const adapter = vi.fn((geometry: THREE.BufferGeometry) => ({geometry, diagnostic: null}))
let models:Bag[]
let cduRoot:THREE.Group
function pipe(){let found:THREE.Mesh|undefined;group.traverse(o=>{if((o as THREE.Mesh).isMesh&&o.userData.pipeModelId==='m_31')found=o as THREE.Mesh});return found!}
function cap(){let found:THREE.Mesh|undefined;group.traverse(o=>{if(o.userData.isBlindEndCap&&o.userData.pipeId==='m_31')found=o as THREE.Mesh});return found!}
function actualFixture(){
 models=structuredClone(fixture.models).map(m=>({...m,kind:m.uid==='m_30'?'cdu':'pipe_segment'}))
 cduRoot=new THREE.Group()
 cduRoot.userData.modelId='m_30'
 const shell=new THREE.Mesh(new THREE.BoxGeometry(),new THREE.MeshStandardMaterial())
 shell.userData={modelId:'m_30',exportName:'CDU_1_shell'};cduRoot.add(shell);group.add(cduRoot)
 setRenderRootLookup(uid=>uid==='m_30'?cduRoot:null)
 installCabinetPipeDisplay()
 ;(window as unknown as Bag)._liquidPipeRenderer.updatePipeGeometry()
}
function snapshot(){
 const camera=new THREE.PerspectiveCamera(40,1,.01,100)
 return buildSceneSnapshot({root:group,project:{uid:'unchanged',meta:{name:'fixture'},constructions:{}} as any,
  camera,target:new THREE.Vector3(),selectedUids:[],timeSeconds:600})
}
beforeEach(async () => {
  registerCabinetDisplayRefresh(null);setShowCabinetPipes(false)
  group = new THREE.Group()
  const w = window as unknown as Bag
  w.THREE = THREE; w.globalGroup = group; w.state = {}; w.requestRender = () => {}
  models = [{
    uid: 'pipe', kind: 'pipe_segment', meta: {enabled: true},
    geometry: {diameterM: .1, waypoints: [{x: 0,y: 0,z: 0},{x: 1,y: 1,z: 1}]},
    cooling: {connection: {blindEnds: {start: true}}},
  }]
  w.allModels=()=>models
  w._displayLiquidPipeGeometry = adapter
  await import('../../../shared/js/geometry_pure.js')
  await import('../../../shared/js/pbr_materials.js')
  await import('../../../shared/js/liquid_pipe_renderer.js')
  adapter.mockClear()
})
afterEach(() => {
 registerCabinetDisplayRefresh(null);setRenderRootLookup(null)
 const w = window as unknown as Bag
 for (const key of ['THREE','globalGroup','state','allModels','_displayLiquidPipeGeometry','_beginLiquidPipeDisplay','_endLiquidPipeDisplay']) delete w[key]
})
it('hands the original longitudinal UV buffers and path to the display-only adapter before PBR tiling', () => {
 ;(window as unknown as Bag)._liquidPipeRenderer.updatePipeGeometry()
 expect(adapter).toHaveBeenCalledOnce()
 const [geometry, context] = adapter.mock.calls[0]! as unknown as [THREE.BufferGeometry, Bag]
 expect(context.pipeModelId).toBe('pipe')
 expect(context.path).toBeInstanceOf(THREE.CurvePath)
 expect(context.points).toHaveLength(2)
 expect(geometry.getAttribute('position').count).toBeGreaterThan(0)
})

it('removes the real saved rear-ring overrun and cabinet interior while preserving authored external U headers',()=>{
 actualFixture()
 const before=JSON.stringify(models)
 const p=pipe(),pos=p.geometry.getAttribute('position')
 let maxZ=-Infinity,maxX=-Infinity,minY=Infinity
 for(let i=0;i<pos.count;i++){maxZ=Math.max(maxZ,pos.getZ(i));maxX=Math.max(maxX,pos.getX(i));minY=Math.min(minY,pos.getY(i))}
 expect(maxZ).toBeLessThanOrEqual(5.219872474670411+1e-6)
 expect(maxX).toBeGreaterThan(7.62)
 expect(minY).toBeLessThan(.51)
 expect(closedCabinetDiagnostics()).toEqual([])
 expect(JSON.stringify(models)).toBe(before)
})
it('restores the complete original buffers on inspect without replacing materials, mesh, blind caps or inputs',()=>{
 actualFixture()
 const p=pipe(),material=p.material,blind=cap(),capGeometry=blind.geometry,before=JSON.stringify(models)
 const clipped=Array.from(p.geometry.getAttribute('position').array),original=(p.userData._pipeDisplaySource.geometry as THREE.BufferGeometry)
 const rev=leafDisplayRevision()
 setShowCabinetPipes(true)
 expect(showCabinetPipes()).toBe(true)
 expect(pipe()).toBe(p);expect(p.material).toBe(material)
 expect(cap()).toBe(blind);expect(blind.geometry).toBe(capGeometry)
 expect(Array.from(p.geometry.getAttribute('position').array)).toEqual(Array.from(original.getAttribute('position').array))
 expect(p.geometry.getAttribute('position').count).toBe(2025)
 expect(leafDisplayRevision()).toBeGreaterThan(rev)
 setShowCabinetPipes(false)
 expect(Array.from(p.geometry.getAttribute('position').array)).toEqual(clipped)
 expect(JSON.stringify(models)).toBe(before)
 expect(original.userData.pbrUvScaled).toBeUndefined()
 const uv=original.getAttribute('uv')
 expect(Math.max(...Array.from({length:uv.count},(_,i)=>uv.getX(i)))).toBe(1)
 setShowCabinetPipes(true);setShowCabinetPipes(false)
 expect(Array.from(p.geometry.getAttribute('position').array)).toEqual(clipped)
})
it('OV serializes selected actual buffers while input identity and result time stay fixed',()=>{
 actualFixture()
 const before=JSON.stringify(models),closed=snapshot()
 setShowCabinetPipes(true)
 const open=snapshot()
 const a=closed.primitives.find(p=>p.modelUid==='m_31'),b=open.primitives.find(p=>p.modelUid==='m_31')
 expect(a).toBeDefined();expect(b).toBeDefined()
 expect(JSON.stringify(a)).not.toBe(JSON.stringify(b))
 expect(closed.project).toEqual(open.project)
 expect(closed.view.timeSeconds).toBe(600);expect(open.view.timeSeconds).toBe(600)
 expect(JSON.stringify(models)).toBe(before)
})
it('returns the entire route when the actual shell is hidden or transparent',()=>{
 actualFixture()
 const p=pipe(),original=Array.from((p.userData._pipeDisplaySource.geometry as THREE.BufferGeometry).getAttribute('position').array)
 cduRoot.visible=false
 ;(window as unknown as Bag)._liquidPipeRenderer.refreshPipeDisplayGeometry()
 expect(Array.from(p.geometry.getAttribute('position').array)).toEqual(original)
 cduRoot.visible=true
 const shell=cduRoot.children[0] as THREE.Mesh<THREE.BufferGeometry,THREE.MeshStandardMaterial>
 shell.material.opacity=.4
 ;(window as unknown as Bag)._liquidPipeRenderer.refreshPipeDisplayGeometry()
 expect(Array.from(p.geometry.getAttribute('position').array)).toEqual(original)
 expect(cabinetIsClosed(models.find(m=>m.uid==='m_30') as Construction,cduRoot)).toBe(false)
})
it('fails open visibly for an invalid cabinet frame without changing route data',()=>{
 actualFixture()
 const c=models.find(m=>m.uid==='m_30')!
 c.geometry.scale.x=0
 const before=JSON.stringify(models)
 ;(window as unknown as Bag)._liquidPipeRenderer.refreshPipeDisplayGeometry()
 expect(closedCabinetDiagnostics().some(s=>s.includes('invalid-cabinet'))).toBe(true)
 expect(pipe().userData.pipeDisplayDiagnostic).toBe('invalid-cabinet')
 expect(JSON.stringify(models)).toBe(before)
})

it('updates geometry during actual model-opacity preview without waiting for an edit commit',async()=>{
 actualFixture()
 const p=pipe(),closed=Array.from(p.geometry.getAttribute('position').array),
   original=Array.from((p.userData._pipeDisplaySource.geometry as THREE.BufferGeometry).getAttribute('position').array)
 const before=JSON.stringify(models),rev=leafDisplayRevision()
 applyOpacityToMeshes(cduRoot,40)
 await Promise.resolve()
 expect(Array.from(p.geometry.getAttribute('position').array)).toEqual(original)
 expect(leafDisplayRevision()).toBeGreaterThan(rev)
 applyOpacityToMeshes(cduRoot,100)
 await Promise.resolve()
 expect(Array.from(p.geometry.getAttribute('position').array)).toEqual(closed)
 expect(JSON.stringify(models)).toBe(before)
})
it('re-clips only when the cabinet opens or closes, not on every step of a fade',async()=>{
 actualFixture()
 const p=pipe()
 applyOpacityToMeshes(cduRoot,40)
 await Promise.resolve()
 const opened=p.geometry,rev=leafDisplayRevision()
 for(const percent of [35,30,20])applyOpacityToMeshes(cduRoot,percent)
 await Promise.resolve()
 expect(p.geometry).toBe(opened)
 expect(leafDisplayRevision()).toBe(rev)
 applyOpacityToMeshes(cduRoot,100)
 await Promise.resolve()
 expect(p.geometry).not.toBe(opened)
 expect(leafDisplayRevision()).toBeGreaterThan(rev)
})
it('updates geometry during the actual whole-scene postprocess fade without changing result/material identity',async()=>{
 actualFixture()
 const p=pipe(),material=p.material,original=Array.from((p.userData._pipeDisplaySource.geometry as THREE.BufferGeometry).getAttribute('position').array)
 const before=JSON.stringify(models)
 applyMaterialOpacity(group,.3)
 await Promise.resolve()
 expect(Array.from(p.geometry.getAttribute('position').array)).toEqual(original)
 expect(p.material).toBe(material)
 expect((p.material as THREE.Material).opacity).toBe(.3)
 expect(JSON.stringify(models)).toBe(before)
})

it('does not unhide a pipe explicitly hidden outside the cabinet display policy',()=>{
 actualFixture()
 const p=pipe();p.visible=false
 setShowCabinetPipes(true)
 expect(p.visible).toBe(false)
})

it('records actual original and displayed buffers from both saved Shinaide pipes',()=>{
 actualFixture()
 const rows:Array<Record<string,unknown>>=[]
 group.traverse(o=>{
  const mesh=o as THREE.Mesh
  if(!mesh.isMesh||!['m_31','m_32'].includes(o.userData.pipeModelId))return
  const source=o.userData._pipeDisplaySource.geometry as THREE.BufferGeometry
  const buffer=(g:THREE.BufferGeometry)=>{
   g.computeBoundingBox()
   const attr=g.getAttribute('position')
   const bytes=Buffer.from(attr.array.buffer,attr.array.byteOffset,attr.array.byteLength)
   let minArea=Infinity
   if(!g.index)for(let i=0;i<attr.count;i+=3){
    const a=new THREE.Vector3().fromBufferAttribute(attr,i),
     b=new THREE.Vector3().fromBufferAttribute(attr,i+1),c=new THREE.Vector3().fromBufferAttribute(attr,i+2)
    minArea=Math.min(minArea,b.sub(a).cross(c.sub(a)).length()/2)
   }
   return {vertices:attr.count,triangles:g.index?g.index.count/3:attr.count/3,
    min:g.boundingBox!.min.toArray(),max:g.boundingBox!.max.toArray(),
    positionsSha256:createHash('sha256').update(bytes).digest('hex'),
    minArea:Number.isFinite(minArea)?minArea:null}
  }
  rows.push({uid:o.userData.pipeModelId,original:buffer(source),displayed:buffer(mesh.geometry)})
  expect(buffer(mesh.geometry).minArea).toBeGreaterThan(0)
  expect((buffer(mesh.geometry).max as number[])[2]).toBeLessThanOrEqual(5.219872474670411+1e-6)
 })
 expect(rows).toHaveLength(2)
 console.log('DCDTUNI_CLOSED_PIPE_METRICS '+JSON.stringify({schema:'dcdtuni.closed-cabinet-display-audit/v1',
  fixtureSourceSha256:fixture.sourceSha256,rows}))
})
