/** CPU-only adapters from decoded OpenFOAM data to renderer-neutral raw geometry. */
import type { DecodedMesh, SlicePayload, StreamlineSet } from '../postprocess'
import { validateSourceFaceUids, validateNativeBoundaryProvenance, type NativeBoundaryProvenance, type MeshEdgesLayer,type MeshFacesLayer, type ScalarField, type ScalarLut, type SliceResultLayer, type StreamlineResultLayer, type SurfaceFieldLayer } from './resultLayers'

function meshLayout<T>(mesh: DecodedMesh, sourceBindings: Record<string,T>, check:(value:unknown,name:string)=>void) {
  let point = 0, triangle = 0, edge = 0
  const names = new Set<string>()
  const spans = mesh.patches.map(patch => {
    if (!patch.name || names.has(patch.name)) throw new Error(`Duplicate or empty mesh patch name ${patch.name}`)
    names.add(patch.name)
    const binding = Object.prototype.hasOwnProperty.call(sourceBindings, patch.name)
      ? sourceBindings[patch.name] : undefined
    check(binding, patch.name)
    if (![patch.nPoints, patch.nTriangles, patch.nEdges].every(n => Number.isSafeInteger(n) && n >= 0)) throw new Error(`Invalid patch counts for ${patch.name}`)
    const span = { patch, binding:binding as T, point, triangle, edge }
    point += patch.nPoints; triangle += patch.nTriangles; edge += patch.nEdges
    return span
  })
  for (const name of Object.keys(sourceBindings)) {
    if (!names.has(name)) throw new Error(`Source mapping ${name} is not in decoded mesh`)
  }
  if (point !== mesh.totals.points || triangle !== mesh.totals.triangles || edge !== mesh.totals.edges ||
      mesh.positions.length !== point * 3 || mesh.indices.length !== triangle * 3 || mesh.edges.length !== edge * 2) {
    throw new Error('Decoded mesh arrays disagree with patch table')
  }
  if (!mesh.positions.every(Number.isFinite)) throw new Error('Non-finite decoded mesh positions')
  // Check every source patch, including hidden ones. Visibility must never
  // turn a corrupt mesh into a packet that appears valid by omission.
  for (const { patch, point: p0, triangle: t0, edge: e0 } of spans) {
    for (const indices of [
      mesh.indices.subarray(t0 * 3, (t0 + patch.nTriangles) * 3),
      mesh.edges.subarray(e0 * 2, (e0 + patch.nEdges) * 2),
    ]) {
      for (const index of indices) {
        if (index < p0 || index >= p0 + patch.nPoints) throw new Error(`Mesh patch ${patch.name} index escapes its point span`)
      }
    }
  }
  return spans
}
function localIndices(global: Uint32Array, first: number, count: number, name: string): number[] {
  const out: number[] = []
  for (const index of global) {
    if (index < first || index >= first + count) throw new Error(`Mesh patch ${name} index escapes its point span`)
    out.push(index - first)
  }
  return out
}

type Binding = Pick<MeshEdgesLayer,'sourceFaceUids'|'boundaryProvenance'>
function meshBoundaryLayers<T>(mesh:DecodedMesh,source:Record<string,T>,check:(value:unknown,name:string)=>void,
  binding:(value:T)=>Binding,hidden:Set<string>):MeshEdgesLayer[] {
 return meshLayout(mesh,source,check).filter(({patch})=>!hidden.has(patch.name)&&patch.nEdges>0).map(({patch,binding:proof,point,edge})=>({
  kind:'meshEdges',layerId:`meshEdges/${patch.name}`,visible:true,meshPatchName:patch.name,...binding(proof),
  positions:Array.from(mesh.positions.subarray(point*3,(point+patch.nPoints)*3)),
  indices:localIndices(mesh.edges.subarray(edge*2,(edge+patch.nEdges)*2),point,patch.nPoints,patch.name),opacity:.85,
 }as MeshEdgesLayer))
}
function meshSurfaceLayers<T>(mesh:DecodedMesh,values:Float32Array,source:Record<string,T>,check:(value:unknown,name:string)=>void,
 binding:(value:T)=>Binding,field:ScalarField,lut:ScalarLut,hidden:Set<string>):SurfaceFieldLayer[] {
 const spans=meshLayout(mesh,source,check)
 if(values.length!==mesh.totals.points)throw new Error('Decoded field length does not match mesh points')
 if(!values.every(Number.isFinite))throw new Error('Non-finite decoded field values')
 return spans.filter(({patch})=>!hidden.has(patch.name)&&patch.nTriangles>0).map(({patch,binding:proof,point,triangle})=>({
  kind:'surfaceField',layerId:`surfaceField/${field.name}/${patch.name}`,visible:true,meshPatchName:patch.name,...binding(proof),
  positions:Array.from(mesh.positions.subarray(point*3,(point+patch.nPoints)*3)),
  indices:localIndices(mesh.indices.subarray(triangle*3,(triangle+patch.nTriangles)*3),point,patch.nPoints,patch.name),
  values:Array.from(values.subarray(point,point+patch.nPoints)),field:{...field},lut:{...lut,range:[...lut.range]as[number,number]},
 }as SurfaceFieldLayer))
}
/** Original polyMesh edges and raw scalar arrays share one numeric lowering. */
export function boundaryMeshLayers(mesh:DecodedMesh,source:Record<string,readonly string[]>,hidden=new Set<string>()) {
 return meshBoundaryLayers(mesh,source,validateSourceFaceUids,value=>({sourceFaceUids:[...value]}),hidden)
}
export function boundarySurfaceFieldLayers(mesh:DecodedMesh,values:Float32Array,source:Record<string,readonly string[]>,field:ScalarField,lut:ScalarLut,hidden=new Set<string>()) {
 return meshSurfaceLayers(mesh,values,source,validateSourceFaceUids,value=>({sourceFaceUids:[...value]}),field,lut,hidden)
}
export function nativeBoundaryMeshLayers(mesh:DecodedMesh,source:Record<string,NativeBoundaryProvenance>,hidden=new Set<string>()) {
 return meshBoundaryLayers(mesh,source,validateNativeBoundaryProvenance,value=>({boundaryProvenance:structuredClone(value)}),hidden)
}
export function nativeBoundarySurfaceFieldLayers(mesh:DecodedMesh,values:Float32Array,source:Record<string,NativeBoundaryProvenance>,field:ScalarField,lut:ScalarLut,hidden=new Set<string>()) {
 return meshSurfaceLayers(mesh,values,source,validateNativeBoundaryProvenance,value=>({boundaryProvenance:structuredClone(value)}),field,lut,hidden)
}

export function streamlineResultLayer(
  set: StreamlineSet, sourceSeedUid: string, unit: string, lut: ScalarLut,
  tubeRadiusMeters = 0.02,
): StreamlineResultLayer {
  if (set.points.length !== set.scalars.length * 3) throw new Error('Streamline coordinates and scalars disagree')
  const lines = set.lines.map(({ start, count }) => {
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(count) || count < 2 || start < 0 || start + count > set.scalars.length) {
      throw new Error('Invalid streamline point span')
    }
    return { positions: Array.from(set.points.subarray(start * 3, (start + count) * 3)),
      values: Array.from(set.scalars.subarray(start, start + count)) }
  })
  return { kind: 'streamline', layerId: `streamline/${sourceSeedUid}/${set.field}`, visible: true,
    sourceSeedUid, lines, field: { name: set.field, unit },
    lut: { ...lut, range: [...lut.range] as [number, number] }, tubeRadiusMeters }
}

/** A physical cut comes from internal cells and has no boundary-patch lineage. */
export function sliceResultLayer(
  cut: SlicePayload, sourceSliceUid: string,
  originMeters: [number, number, number], normal: [number, number, number],
  field: ScalarField, lut: ScalarLut,
): SliceResultLayer {
  if (cut.field !== field.name) throw new Error(`Slice field ${cut.field} does not match ${field.name}`)
  if (!cut.values || cut.values.length !== cut.nPoints ||
      cut.positions.length !== cut.nPoints * 3 || cut.indices.length !== cut.nTriangles * 3) {
    throw new Error('Decoded slice arrays disagree with point/triangle counts')
  }
  return {
    kind: 'slice', layerId: `slice/${sourceSliceUid}`, visible: true, sourceSliceUid,
    originMeters: [...originMeters], normal: [...normal],
    positions: Array.from(cut.positions), indices: Array.from(cut.indices), values: Array.from(cut.values),
    field: { ...field }, lut: { ...lut, range: [...lut.range] as [number, number] },
  }
}

/** Native mesh preview faces share original decoded triangles, never scalar values. */
export function nativeBoundaryMeshFaces(mesh:DecodedMesh,source:Record<string,NativeBoundaryProvenance>,hidden=new Set<string>()):MeshFacesLayer[]{
 return meshLayout(mesh,source,validateNativeBoundaryProvenance).filter(({patch})=>!hidden.has(patch.name)&&patch.nTriangles>0).map(({patch,binding,point,triangle})=>({
  kind:'meshFaces',layerId:`meshFaces/${patch.name}`,visible:true,meshPatchName:patch.name,boundaryProvenance:structuredClone(binding),
  positions:Array.from(mesh.positions.subarray(point*3,(point+patch.nPoints)*3)),
  indices:localIndices(mesh.indices.subarray(triangle*3,(triangle+patch.nTriangles)*3),point,patch.nPoints,patch.name),opacity:.85,
  color:[0.1844749944900301,0.31854677812509186,0.4969329950515914],
 }))
}
