import * as THREE from 'three'
import {describe,it,expect} from 'vitest'
import {cabinetFrame,clipClosedCabinetPipe,terminalStraightDomain} from '../src/geometry/closedCabinetPipeClip'
const box=()=>cabinetFrame('cdu',{location:{x:0,y:0,z:0},size:{x:1,y:1,z:1}})!
function triangle(){
 const g=new THREE.BufferGeometry()
 g.setAttribute('position',new THREE.Float32BufferAttribute([-1,.5,.5,2,.5,.5,.5,2,.5],3))
 g.setAttribute('normal',new THREE.Float32BufferAttribute([0,0,1,0,0,1,0,0,1],3))
 g.setAttribute('uv',new THREE.Float32BufferAttribute([0,0,1,0,.5,1],2))
 return g
}
function area(g:THREE.BufferGeometry){
 const p=g.getAttribute('position');let a=0
 for(let i=0;i<p.count;i+=3){
  const v=[0,1,2].map(j=>new THREE.Vector3().fromBufferAttribute(p,i+j))
  const n=v[1]!.clone().sub(v[0]!).cross(v[2]!.clone().sub(v[0]!))
  expect(n.z).toBeGreaterThan(0);a+=n.length()/2
 }
 return a
}
describe('closed cabinet triangle display clipping',()=>{
 it('retains exactly the triangle complement, its winding, smooth normals and UVs',()=>{
  const g=triangle(),raw=Array.from(g.getAttribute('position').array)
  const o=clipClosedCabinetPipe(g,[box()])
  expect(o.diagnostic).toBeNull();expect(o.geometry).not.toBe(g)
  expect(area(o.geometry)).toBeCloseTo(1.75,7)
  const p=o.geometry.getAttribute('position'),uv=o.geometry.getAttribute('uv'),n=o.geometry.getAttribute('normal')
  for(let i=0;i<p.count;i++){
   expect(uv.getX(i)).toBeCloseTo((p.getX(i)+1)/3,6)
   expect(uv.getY(i)).toBeCloseTo((p.getY(i)-.5)/1.5,6)
   expect(new THREE.Vector3().fromBufferAttribute(n,i).length()).toBeCloseTo(1)
  }
  expect(Array.from(g.getAttribute('position').array)).toEqual(raw)
 })
 it('does not duplicate polygons lying on a clipping plane',()=>{
  const g=triangle();const p=g.getAttribute('position')
  for(let i=0;i<p.count;i++)p.setZ(i,0)
  const o=clipClosedCabinetPipe(g,[box()])
  expect(area(o.geometry)).toBeCloseTo(1.75,7)
 })
 it('uses local cabinet planes for rotated, translated, nonuniform and mirrored frames',()=>{
  const q=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),.7)
  const c=cabinetFrame('rot',{location:{x:8,y:-3,z:2},size:{x:1,y:1,z:1},
    quaternion:{x:q.x,y:q.y,z:q.z,w:q.w},scale:{x:-2,y:.5,z:3}})!
  const g=triangle().applyMatrix4(c.toLocal.clone().invert())
  const o=clipClosedCabinetPipe(g,[c])
  const local=o.geometry.clone().applyMatrix4(c.toLocal)
  expect(area(local)).toBeCloseTo(1.75,5)
 })
 it('subtracts the union of multiple connected cabinets without overlapping fragments',()=>{
  const b=box();const o=clipClosedCabinetPipe(triangle(),[b,b])
  expect(area(o.geometry)).toBeCloseTo(1.75,7)
 })
 it('rejects invalid frames instead of inferring a unit cabinet',()=>{
  expect(cabinetFrame('x',{location:{x:0,y:0,z:0},size:{x:1,y:NaN,z:1}})).toBeNull()
  expect(cabinetFrame('x',{location:{x:0,y:0,z:0},size:{x:1,y:1,z:1},scale:{x:0,y:1,z:1}})).toBeNull()
  expect(cabinetFrame('x',{location:{x:0,y:0,z:0},size:{x:1,y:1,z:1},quaternion:{x:0,y:0,z:0,w:0}})).toBeNull()
 })
 it('fails open with diagnostics for invalid buffers and bounded output budget',()=>{
  const g=triangle()
  const o=clipClosedCabinetPipe(g,[box()],[],{maxOutputTriangles:1})
  expect(o.geometry).toBe(g);expect(o.diagnostic).toBe('clip-budget')
  g.getAttribute('position').setX(1,NaN)
  expect(clipClosedCabinetPipe(g,[box()]).geometry).toBe(g)
  expect(clipClosedCabinetPipe(g,[box()]).diagnostic).toBe('nonfinite-buffers')
 })
 it('leaves unsupported interleaved attributes intact and reports them',()=>{
  const g=triangle();g.setAttribute('color',new THREE.Float32BufferAttribute(new Array(9).fill(1),3))
  expect(clipClosedCabinetPipe(g,[box()]).diagnostic).toBe('unsupported-buffers')
 })
 it('uses a verified straight UV domain and preserves the curved external route',()=>{
  const path=new THREE.CurvePath<THREE.Vector3>()
  path.add(new THREE.LineCurve3(new THREE.Vector3(-2,.5,.5),new THREE.Vector3(-1,.5,.5)))
  path.add(new THREE.QuadraticBezierCurve3(new THREE.Vector3(-1,.5,.5),new THREE.Vector3(0,-1,.5),new THREE.Vector3(.5,-.2,.2)))
  path.add(new THREE.LineCurve3(new THREE.Vector3(.5,-.2,.2),new THREE.Vector3(.5,.85,1)))
  const d=terminalStraightDomain(path,120,'end')!
  expect(d.uMin).toBeGreaterThan(0);expect(d.uMax).toBe(1)
  const g=new THREE.TubeGeometry(path,120,.05,16,false)
  const o=clipClosedCabinetPipe(g,[box()],[{point:new THREE.Vector3(.5,.85,1),
    normal:new THREE.Vector3(0,0,1),keepSign:-1,...d}])
  const p=o.geometry.getAttribute('position');let maxZ=-Infinity,minX=Infinity
  for(let i=0;i<p.count;i++){maxZ=Math.max(maxZ,p.getZ(i));minX=Math.min(minX,p.getX(i))}
  expect(maxZ).toBeLessThanOrEqual(1.000001)
  expect(minX).toBeLessThan(-1.95)
  expect(g.getAttribute('position').count).toBe(121*17)
 })
 it('retains an external port departure while removing only its terminal overrun',()=>{
  const path=new THREE.CurvePath<THREE.Vector3>()
  path.add(new THREE.LineCurve3(new THREE.Vector3(.5,.85,1),new THREE.Vector3(.5,.85,2)))
  const g=new THREE.TubeGeometry(path,16,.05,16,false)
  const o=clipClosedCabinetPipe(g,[box()],[{point:new THREE.Vector3(.5,.85,1),
    normal:new THREE.Vector3(0,0,1),keepSign:1,...terminalStraightDomain(path,16,'start')!}])
  o.geometry.computeBoundingBox()
  expect(o.geometry.boundingBox!.min.z).toBeGreaterThanOrEqual(1)
  expect(o.geometry.boundingBox!.max.z).toBe(2)
 })
 it('does not manufacture a terminal strip from a bezier or zero length path',()=>{
  const path=new THREE.CurvePath<THREE.Vector3>()
  path.add(new THREE.QuadraticBezierCurve3(new THREE.Vector3(),new THREE.Vector3(1,1,1),new THREE.Vector3(2,0,0)))
  expect(terminalStraightDomain(path,32,'end')).toBeNull()
 })
})

it('fails open on output buffer overflow and quaternion length overflow',()=>{
 expect(cabinetFrame('x',{location:{x:0,y:0,z:0},size:{x:1,y:1,z:1},
  quaternion:{x:1e200,y:0,z:0,w:1}})).toBeNull()
 const g=triangle()
 g.setAttribute('uv',new THREE.BufferAttribute(new Float64Array([1e100,0,1e100,0,1e100,1]),2))
 expect(clipClosedCabinetPipe(g,[box()]).geometry).toBe(g)
 expect(clipClosedCabinetPipe(g,[box()]).diagnostic).toBe('nonfinite-output')
})
