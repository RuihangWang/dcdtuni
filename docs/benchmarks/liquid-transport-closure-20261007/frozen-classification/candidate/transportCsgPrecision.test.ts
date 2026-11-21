/** Local transport precision; legacy CSG producers retain their default. */
import { expect,it } from 'vitest'
import * as THREE from 'three'
import '../src/legacy/geometryBuilder'
const CSG=(window as unknown as {ThreeCSG:{CSG:any}}).ThreeCSG.CSG
const mesh=(x=0)=>{const m=new THREE.Mesh(new THREE.BoxGeometry(2,2,2));m.position.x=x;m.updateMatrix();return m}
const volume=(csg:any)=>csg.polygons.reduce((s:number,p:any)=>{
  const a=new THREE.Vector3(p.vertices[0].pos.x,p.vertices[0].pos.y,p.vertices[0].pos.z)
  return s+p.vertices.slice(2).reduce((q:number,v:any,i:number)=>q+a.dot(new THREE.Vector3(p.vertices[i+1].pos.x,p.vertices[i+1].pos.y,p.vertices[i+1].pos.z).cross(new THREE.Vector3(v.pos.x,v.pos.y,v.pos.z)))/6,0)
},0)
const seed=()=>{const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(new Float64Array([0,0,0,1,0,0,0,1,0]),3));g.computeVertexNormals();return CSG.fromGeometry(g,37,1e-9).polygons[0]}
it('does not change default EPS or lend transport precision to subsequent default objects',()=>{
  const a=CSG.fromMesh(mesh(),37),p=CSG.fromMesh(mesh(),41,1e-9),b=CSG.fromMesh(mesh(),43)
  expect(a.polygons.every((q:any)=>q.plane.classificationEpsilon===undefined)).toBe(true)
  expect(b.polygons.every((q:any)=>q.plane.classificationEpsilon===undefined)).toBe(true)
  expect(p.polygons.every((q:any)=>q.plane.classificationEpsilon===1e-9)).toBe(true)
  expect(a.polygons[0].plane.constructor.EPSILON).toBe(1e-5)
  expect(volume(a.union(b))).toBeCloseTo(8,12)
})
it('rejects invalid or looser precision before creating geometry',()=>{
  for(const v of [0,-1,NaN,Infinity,1e-4])expect(()=>CSG.fromMesh(mesh(),37,v)).toThrow('positive and no looser')
})
it('classifies a real five-micrometre span without moving its endpoints and leaves default classification unchanged',()=>{
  const p=seed(),Vertex=p.vertices[0].constructor,Vector=p.vertices[0].pos.constructor,Polygon=p.constructor,Plane=p.plane.constructor
  const vertices=[[-5e-6,-1,0],[5e-6,-1,0],[5e-6,1,0],[-5e-6,1,0]].map(([x,y,z])=>new Vertex(new Vector(x,y,z),new Vector(0,0,1),new Vector()))
  const poly=new Polygon(vertices,37,p.plane),normal=new Vector(1,0,0),cut=new Plane(normal,0),front:any[]=[],back:any[]=[],coplanar:any[]=[]
  cut.classificationEpsilon=p.plane.classificationEpsilon;cut.splitPolygon(poly,coplanar,coplanar,front,back)
  expect(front).toHaveLength(1);expect(back).toHaveLength(1);expect(coplanar).toHaveLength(0)
  expect(front[0].vertices.some((v:any)=>v.pos.x===5e-6)).toBe(true);expect(back[0].vertices.some((v:any)=>v.pos.x===-5e-6)).toBe(true)
  expect(front[0].vertices.some((v:any)=>v.pos.x===0)).toBe(true)
  const oldCut=new Plane(new Vector(1,0,0),0),oldCo:any[]=[],oldFront:any[]=[],oldBack:any[]=[]
  oldCut.splitPolygon(poly,oldCo,oldCo,oldFront,oldBack);expect(oldCo).toHaveLength(1);expect(oldFront).toHaveLength(0);expect(oldBack).toHaveLength(0)
})
it('preserves precise clipped plane lineage and independent clone/flip ownership',()=>{
  const p=seed(),Vertex=p.vertices[0].constructor,Vector=p.vertices[0].pos.constructor,Polygon=p.constructor,Plane=p.plane.constructor
  const vertices=[[-1,1,0],[-1,-1,0],[0,-1,0],[1,-1,0],[1,1,0]].map(([x,y,z])=>new Vertex(new Vector(x,y,z),new Vector(0,0,1),new Vector()))
  const poly=new Polygon(vertices,37,p.plane),cut=new Plane(new Vector(1,0,0),-.5),front:any[]=[],back:any[]=[];cut.classificationEpsilon=1e-9
  cut.splitPolygon(poly,[],[],front,back);expect(front).toHaveLength(1);expect(back).toHaveLength(1)
  expect(front[0].vertices.slice(0,3).map((v:any)=>[v.pos.x,v.pos.y,v.pos.z])).toEqual([[-.5,-1,0],[0,-1,0],[1,-1,0]])
  const before={...poly.plane.normal},cloned=front[0].clone();expect({...cloned.plane.normal}).toEqual(before);expect(cloned.plane.classificationEpsilon).toBe(1e-9);expect(cloned.shared).toBe(37)
  cloned.flip();expect({...front[0].plane.normal}).toEqual(before);expect({...back[0].plane.normal}).toEqual(before);expect({...poly.plane.normal}).toEqual(before)
})
it('binds precise support planes after affine transform before split/clone',()=>{
  const m=mesh();m.position.set(3,2,-4);m.rotation.set(.23,.45,-.12);m.scale.set(1.2,.8,1.5);m.updateMatrix()
  const a=CSG.fromMesh(m,37,1e-9),clone=a.clone(),inverse=a.inverse();expect(volume(a)).toBeCloseTo(11.52,10);expect(volume(clone)).toBe(volume(a));expect(volume(inverse)).toBeCloseTo(-volume(a),12)
  a.polygons.forEach((p:any,i:number)=>{for(const v of p.vertices)expect(Math.abs(p.plane.normal.dot(v.pos)-p.plane.w)).toBeLessThan(1e-12)
    expect(p.plane).not.toBe(clone.polygons[i].plane);expect(p.plane.classificationEpsilon).toBe(1e-9);expect(p.plane.normal.dot(inverse.polygons[i].plane.normal)).toBeCloseTo(-1,14)})
})
it('keeps a physical one-micrometre Boolean intersection and original owners',()=>{
  const a=CSG.fromMesh(mesh(),37,1e-9),b=CSG.fromMesh(mesh(2-1e-6),41,1e-9)
  const intersection=a.intersect(b),union=a.union(b)
  expect(volume(intersection)).toBeCloseTo(4e-6,12);expect(volume(union)).toBeCloseTo(16-4e-6,12)
  for(const out of [intersection,union])expect(out.polygons.every((p:any)=>(p.shared===37||p.shared===41)&&p.plane.classificationEpsilon===1e-9)).toBe(true)
  const xs=intersection.polygons.flatMap((p:any)=>p.vertices.map((v:any)=>v.pos.x));expect(Math.min(...xs)).toBe(Math.min(...b.polygons.flatMap((p:any)=>p.vertices.map((v:any)=>v.pos.x))));expect(Math.max(...xs)).toBe(1)
})
