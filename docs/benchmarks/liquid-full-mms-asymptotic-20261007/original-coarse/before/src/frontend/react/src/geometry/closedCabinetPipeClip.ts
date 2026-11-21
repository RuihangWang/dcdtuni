import * as THREE from 'three'

/** Display buffers only: positions are in the pipe's project-coordinate frame. */
export interface ClosedCabinet {
  uid: string
  toLocal: THREE.Matrix4
  size: THREE.Vector3
}
export interface TerminalDisplayPlane {
  point: THREE.Vector3
  normal: THREE.Vector3
  /** Side containing the original terminal centreline, never a reroute. */
  keepSign: 1 | -1
  /** Only the verified original straight terminal strip in TubeGeometry UV. */
  uMin: number
  uMax: number
}
export interface ClipResult {
  geometry: THREE.BufferGeometry
  diagnostic: string | null
}
type Vertex = {p: THREE.Vector3; n: THREE.Vector3; uv: THREE.Vector2}
type Polygon = Vertex[]
type Value = (v: Vertex) => number
const finite = (a: readonly number[]) => a.every(Number.isFinite)

/** Reject invalid frames rather than guessing a cabinet from an invalid input. */
export function cabinetFrame(uid: string, geometry: {
  location?: {x:number;y:number;z:number}
  size?: {x:number;y:number;z:number}
  quaternion?: {x:number;y:number;z:number;w:number}
  scale?: {x:number;y:number;z:number}
}): ClosedCabinet | null {
  const loc = geometry.location, size = geometry.size
  if (!loc || !size || !finite([loc.x,loc.y,loc.z,size.x,size.y,size.z]) ||
    Math.min(size.x,size.y,size.z) <= 0) return null
  const rawQ = geometry.quaternion ?? {x:0,y:0,z:0,w:1}
  const rawS = geometry.scale ?? {x:1,y:1,z:1}
  if (!finite([rawQ.x,rawQ.y,rawQ.z,rawQ.w,rawS.x,rawS.y,rawS.z]) ||
    Math.min(Math.abs(rawS.x),Math.abs(rawS.y),Math.abs(rawS.z)) < 1e-12) return null
  const q = new THREE.Quaternion(rawQ.x,rawQ.y,rawQ.z,rawQ.w)
  if (!Number.isFinite(q.lengthSq()) || q.lengthSq() < 1e-24) return null
  const m = new THREE.Matrix4().compose(
    new THREE.Vector3(loc.x,loc.y,loc.z),q.normalize(),new THREE.Vector3(rawS.x,rawS.y,rawS.z))
  return {uid,toLocal:m.invert(),size:new THREE.Vector3(size.x,size.y,size.z)}
}
function mix(a: Vertex,b: Vertex,t: number): Vertex {
  return {p:a.p.clone().lerp(b.p,t),n:a.n.clone().lerp(b.n,t),uv:a.uv.clone().lerp(b.uv,t)}
}
/** Return both sides of one plane with identical intersection coordinates. */
function split(poly: Polygon, value: Value): [Polygon,Polygon] {
  const inside: Polygon = [], outside: Polygon = []
  for (let i=0;i<poly.length;i++) {
    const a=poly[i]!, b=poly[(i+1)%poly.length]!, da=value(a), db=value(b)
    if (!finite([da,db])) throw new Error('nonfinite-clip')
    if (da<=0) inside.push(a)
    if (da>=0) outside.push(a)
    if ((da<0&&db>0)||(da>0&&db<0)) {
      const v=mix(a,b,da/(da-db)); inside.push(v); outside.push(v)
    }
  }
  return [inside, poly.some(v=>value(v)>0)?outside:[]]
}
function subtractBox(poly: Polygon, box: ClosedCabinet): Polygon[] {
  let remainder=poly
  const exterior: Polygon[]=[]
  for (let axis=0;axis<3;axis++) for (const upper of [false,true]) {
    const bound=box.size.getComponent(axis)
    const [inside,outside]=split(remainder,v=>{
      const x=v.p.clone().applyMatrix4(box.toLocal).getComponent(axis)
      return upper ? x-bound : -x
    })
    if(outside.length>=3) exterior.push(outside)
    remainder=inside
    if(remainder.length<3) return exterior
  }
  return exterior // the remaining intersection is concealed by the cabinet
}
function terminalClip(poly: Polygon,t: TerminalDisplayPlane): Polygon[] {
  const [from,below]=split(poly,v=>t.uMin-v.uv.x)
  const [strip,above]=split(from,v=>v.uv.x-t.uMax)
  const [retained]=split(strip,v=>-t.keepSign*v.p.clone().sub(t.point).dot(t.normal))
  return [below,retained,above].filter(p=>p.length>=3)
}

/** Finite, bounded clipping of triangle surfaces. Failure retains original buffers. */
export function clipClosedCabinetPipe(
  original: THREE.BufferGeometry,
  cabinets: readonly ClosedCabinet[],
  terminals: readonly TerminalDisplayPlane[]=[],
  budget: {maxInputTriangles?:number;maxOutputTriangles?:number}={},
): ClipResult {
  if (!cabinets.length) return {geometry:original,diagnostic:null}
  try {
    if (cabinets.length>4 || terminals.length>2) throw new Error('clip-budget')
    for(const c of cabinets) if(!finite([...c.toLocal.elements,c.size.x,c.size.y,c.size.z])||
      Math.min(c.size.x,c.size.y,c.size.z)<=0) throw new Error('invalid-cabinet')
    for(const t of terminals) if(!finite([...t.point.toArray(),...t.normal.toArray(),t.uMin,t.uMax])||
      Math.abs(t.normal.length()-1)>1e-8 || t.uMin<0 || t.uMax>1 || t.uMin>t.uMax ||
      (t.keepSign!==1&&t.keepSign!==-1)) throw new Error('invalid-terminal')
    const position=original.getAttribute('position'), normal=original.getAttribute('normal'),
      uv=original.getAttribute('uv'), index=original.getIndex()
    if (!position||!normal||!uv||position.itemSize!==3||normal.itemSize!==3||uv.itemSize!==2||
      normal.count!==position.count||uv.count!==position.count ||
      Object.keys(original.attributes).some(k=>!['position','normal','uv'].includes(k))) throw new Error('unsupported-buffers')
    const count=index?.count??position.count
    if(count%3!==0||count/3>(budget.maxInputTriangles??40000)) throw new Error('clip-budget')
    const outputP:number[]=[],outputN:number[]=[],outputUV:number[]=[]
    const max=budget.maxOutputTriangles??120000
    const vertex=(i:number):Vertex=>{
      if(!Number.isInteger(i)||i<0||i>=position.count)throw new Error('invalid-index')
      const p=new THREE.Vector3().fromBufferAttribute(position,i),
        n=new THREE.Vector3().fromBufferAttribute(normal,i),v=new THREE.Vector2(uv.getX(i),uv.getY(i))
      if(!finite([...p.toArray(),...n.toArray(),...v.toArray()]))throw new Error('nonfinite-buffers')
      return {p,n,uv:v}
    }
    for(let i=0;i<count;i+=3) {
      let pieces:Polygon[]=[[0,1,2].map(j=>vertex(index?index.getX(i+j):i+j))]
      for(const t of terminals)pieces=pieces.flatMap(p=>terminalClip(p,t))
      for(const box of cabinets)pieces=pieces.flatMap(p=>subtractBox(p,box))
      if(pieces.length>256)throw new Error('clip-budget')
      for(const poly of pieces) for(let j=1;j<poly.length-1;j++) {
        const tri=[poly[0]!,poly[j]!,poly[j+1]!]
        const oriented=tri[1]!.p.clone().sub(tri[0]!.p).cross(tri[2]!.p.clone().sub(tri[0]!.p))
        if(!Number.isFinite(oriented.lengthSq()))throw new Error('nonfinite-output')
        if(oriented.lengthSq()===0)continue
        // Output is a Float32 GPU buffer. Ignore only unrepresentable slivers;
        // never emit a collapsed or flipped triangle after that conversion.
        const packed=tri.map(v=>new THREE.Vector3(Math.fround(v.p.x),Math.fround(v.p.y),Math.fround(v.p.z)))
        if(!packed.every(v=>finite(v.toArray())))throw new Error('nonfinite-output')
        const quantized=packed[1]!.clone().sub(packed[0]!).cross(packed[2]!.clone().sub(packed[0]!))
        if(quantized.dot(oriented)<=0)continue
        if(outputP.length/9>=max)throw new Error('clip-budget')
        for(const v of tri) {
          if(v.n.lengthSq()<1e-24)throw new Error('invalid-normal')
          outputP.push(...v.p.toArray());outputN.push(...v.n.clone().normalize().toArray());outputUV.push(...v.uv.toArray())
        }
      }
    }
    const packedP=new Float32Array(outputP),packedN=new Float32Array(outputN),packedUV=new Float32Array(outputUV)
    if(packedP.some(v=>!Number.isFinite(v))||packedN.some(v=>!Number.isFinite(v))||packedUV.some(v=>!Number.isFinite(v)))throw new Error('nonfinite-output')
    const geometry=new THREE.BufferGeometry()
    geometry.setAttribute('position',new THREE.BufferAttribute(packedP,3))
    geometry.setAttribute('normal',new THREE.BufferAttribute(packedN,3))
    geometry.setAttribute('uv',new THREE.BufferAttribute(packedUV,2))
    if(outputP.length){geometry.computeBoundingBox();geometry.computeBoundingSphere()}
    geometry.userData={...original.userData,closedCabinetDisplay:true}
    return {geometry,diagnostic:null}
  } catch(error) {
    return {geometry:original,diagnostic:error instanceof Error?error.message:'clip-failed'}
  }
}

/** Restrict a terminal cut to TubeGeometry rings lying on its original straight leg. */
export function terminalStraightDomain(path: THREE.CurvePath<THREE.Vector3>,
  segments:number,end:'start'|'end'): {uMin:number;uMax:number}|null {
  if(!Number.isInteger(segments)||segments<1||segments>100000)return null
  const curve=end==='start'?path.curves[0]:path.curves[path.curves.length-1]
  if(!(curve instanceof THREE.LineCurve3))return null
  const a=curve.v1,b=curve.v2,leg=b.clone().sub(a),len=leg.length()
  if(!Number.isFinite(len)||len<=1e-12)return null
  leg.divideScalar(len)
  let limit=end==='start'?0:1
  for(let j=1;j<=segments;j++){
    const u=end==='start'?j/segments:1-j/segments,p=path.getPointAt(u),d=p.clone().sub(a)
    const s=d.dot(leg),off=d.clone().addScaledVector(leg,-s).length()
    if(!finite([...p.toArray(),s,off])||s < -1e-10*len||s>len+1e-10*len||off>1e-10*Math.max(1,len))break
    limit=u
  }
  if(limit===(end==='start'?0:1))return null
  return end==='start'?{uMin:0,uMax:limit}:{uMin:limit,uMax:1}
}
