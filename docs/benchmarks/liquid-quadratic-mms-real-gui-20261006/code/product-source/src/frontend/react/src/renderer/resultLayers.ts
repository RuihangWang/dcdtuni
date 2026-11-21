/** Versioned solver/mesh display data, intentionally separate from editable geometry. */
import Ajv from 'ajv'
import schema from '../../../shared/schema/render-result-layers.schema.json'
import type { ColorMap } from '../postprocess/ppColormaps'

export interface ResultSource {
  projectUid: string
  projectContentSha256: string
  cfdrunSha256: string
  meshReceiptSha256: string
  /** Explicit quality gate result; diagnostic mesh edges are display-only. */
  meshQuality: 'accepted' | 'diagnostic'
  /** Null for a pre-solve mesh preview. Scalar layers require a verified run. */
  run: null | { uid: string; receiptSha256: string }
}
export interface ScalarField { name: string; unit: string }
export interface ScalarLut { palette: ColorMap; range: [number, number]; opacity: number }
interface CommonLayer { layerId: string; visible: boolean }
/** Mesh patch identity is (meshReceiptSha256, meshPatchName); source faces are lineage only. */
export interface NativeRegionInventory {sourceEntityUid:string;sourceRegionUid:string;sourceRegionSha256:string;sourceFacetCount:number;sourceFacetUidPrefix:string}
export interface NativeBoundaryProvenance {schema:'dcdtuni.native-boundary-provenance/v1';patchRanges:Array<{
  bindingKind:'regionFacetInventory'|'meshBoundaryOnly';rank:number;patchIndex:number;nativePatchName:string;
  startFace:number;nFaces:number;boundarySha256:string;sourceRegions:NativeRegionInventory[]
}>}
export interface RunConvergence {verdict:'CONVERGED'|'NOT_CONVERGED'|'INCONCLUSIVE'|'TRANSIENT_COMPLETED'|'TRANSIENT_INCOMPLETE';receiptSha256:string}
type BoundaryBinding = {sourceFaceUids:string[];boundaryProvenance?:never}|{sourceFaceUids?:never;boundaryProvenance:NativeBoundaryProvenance}
type BoundaryLayer = CommonLayer & {meshPatchName:string} & BoundaryBinding
export type MeshEdgesLayer = BoundaryLayer & {
  kind: 'meshEdges'; positions: number[]; indices: number[]; opacity: number;color?:[number,number,number]
}
export type SurfaceFieldLayer = BoundaryLayer & {
  /** Values are ordered by the mesh patch's vertices; they are not partitioned by sourceFaceUids. */
  kind: 'surfaceField'; positions: number[]; indices: number[]; values: number[]
  field: ScalarField; lut: ScalarLut
}
/** A cut through internal cells has a seed/plane identity, never a boundary patch UID. */
export interface SliceResultLayer extends CommonLayer {
  kind: 'slice'; sourceSliceUid: string
  originMeters: [number, number, number]; normal: [number, number, number]
  positions: number[]; indices: number[]; values: number[]
  field: ScalarField; lut: ScalarLut
}
export interface StreamlineResultLayer extends CommonLayer {
  kind: 'streamline'; sourceSeedUid: string
  lines: { positions: number[]; values: number[] }[]
  field: ScalarField; lut: ScalarLut; tubeRadiusMeters: number
}
export type MeshFacesLayer = Omit<MeshEdgesLayer,'kind'|'color'>&{kind:'meshFaces';color:[number,number,number]}
export type ResultLayer = MeshFacesLayer | MeshEdgesLayer | SurfaceFieldLayer | SliceResultLayer | StreamlineResultLayer
interface ResultPacketCommon {
  revision: number
  source: ResultSource
  coordinateSystem: { upAxis: 'Y'; metersPerUnit: 1; handedness: 'right' }
  timeSeconds: number | null
  layers: ResultLayer[]
}

export type ResultLayerSet = ResultPacketCommon & ({schema:'dcdtuni.render-result-layers/v1';runConvergence?:never}|{schema:'dcdtuni.render-result-layers/v2';runConvergence:RunConvergence;meshPreview?:never}|{schema:'dcdtuni.render-result-layers/v4';runConvergence?:never;meshPreview:{meshSnapshotKey:string;meshPayloadSha256:string;meshMetadataSha256:string}})

const validate = new Ajv({ strict: true }).compile(schema)
const validateNative = new Ajv({strict:true}).compile({$defs:schema.$defs,$ref:'#/$defs/nativeBoundaryProvenance'})

function finiteArray(values: number[], description: string) {
  if (!values.every(Number.isFinite)) throw new Error(`Non-finite ${description}`)
}
function validatePositions(positions: number[], description: string): number {
  finiteArray(positions, description)
  if (positions.length < 3 || positions.length % 3) throw new Error(`Invalid ${description}: expected xyz triplets`)
  return positions.length / 3
}
function validateIndices(indices: number[], nPoints: number, arity: number, description: string) {
  if (!indices.length || indices.length % arity || indices.some(i => i >= nPoints)) {
    throw new Error(`Invalid ${description}: indices must form complete in-range primitives`)
  }
}
function validateLut(lut: ScalarLut) {
  if (!Number.isFinite(lut.range[0]) || !Number.isFinite(lut.range[1]) || lut.range[0] >= lut.range[1]) {
    throw new Error('Invalid LUT range: expected finite ascending values')
  }
}

/** Do not infer a single source face from an OpenFOAM patch that merges several. */
export function validateSourceFaceUids(value: unknown, meshPatchName: string): asserts value is string[] {
  if (!Array.isArray(value) || !value.length || value.some(uid => typeof uid !== 'string' || !uid.trim())) {
    throw new Error(`Mesh patch ${meshPatchName} has no verified source face UID list`)
  }
  for (let i = 1; i < value.length; i++) {
    if (!(value[i - 1] < value[i])) throw new Error(`Mesh patch ${meshPatchName} source face UIDs must be sorted and unique`)
  }
}

export function validateNativeBoundaryProvenance(value:unknown,patch:string):asserts value is NativeBoundaryProvenance {
  if(!validateNative(value))throw new Error(`Mesh patch ${patch} has no verified native boundary provenance`)
  const proof=value as NativeBoundaryProvenance;let previous:[number,number]|null=null
  for(const range of proof.patchRanges){
    if(previous&&(range.rank<previous[0]||(range.rank===previous[0]&&range.patchIndex<=previous[1])))throw new Error('Native boundary ranges must be ordered and unique')
    previous=[range.rank,range.patchIndex]
    if(!!range.sourceRegions.length!==(range.bindingKind==='regionFacetInventory'))throw new Error('Native boundary inventory kind differs from evidence')
    const ids=range.sourceRegions.map(row=>row.sourceRegionUid)
    if(ids.some((uid,index)=>index>0&&ids[index-1]!>=uid))throw new Error('Original native region inventory must be ordered and unique')
    if(range.sourceRegions.some(row=>row.sourceFacetUidPrefix!==row.sourceRegionUid+'/facet/'))throw new Error('Facet namespace differs from actual region inventory')
  }
}

/** JSON Schema rejects unknown fields (including modelUid and ShaderMaterial). */
export function validateResultLayerSet(value: unknown): asserts value is ResultLayerSet {
  if (!validate(value)) throw new Error(`Render result layer schema: ${new Ajv().errorsText(validate.errors)}`)
  const packet = value as unknown as ResultLayerSet
  if (packet.timeSeconds !== null && !Number.isFinite(packet.timeSeconds)) throw new Error('Non-finite physical time')
  const ids = new Set<string>()
  const patchBindings = new Map<string, string>()
  for (const layer of packet.layers) {
    if (ids.has(layer.layerId)) throw new Error(`Duplicate result layer ${layer.layerId}`)
    ids.add(layer.layerId)
    if (layer.kind === 'meshEdges' || layer.kind === 'meshFaces' || layer.kind === 'surfaceField') {
      if(packet.schema==='dcdtuni.render-result-layers/v1')validateSourceFaceUids(layer.sourceFaceUids,layer.meshPatchName)
      else validateNativeBoundaryProvenance(layer.boundaryProvenance,layer.meshPatchName)
      const binding=JSON.stringify(packet.schema==='dcdtuni.render-result-layers/v1'?layer.sourceFaceUids:layer.boundaryProvenance)
      const bound = patchBindings.get(layer.meshPatchName)
      if (bound && bound !== binding) throw new Error(`Result patch ${packet.schema==='dcdtuni.render-result-layers/v1'?'UID list':'binding'} mismatch for ${layer.meshPatchName}`)
      patchBindings.set(layer.meshPatchName, binding)
    }
    if (layer.kind === 'meshEdges'||layer.kind==='meshFaces') {
      const nPoints = validatePositions(layer.positions, `${layer.layerId} positions`)
      validateIndices(layer.indices, nPoints, layer.kind==='meshEdges'?2:3, `${layer.layerId} primitives`)
      continue
    }
    if (!packet.source.run || packet.timeSeconds === null) throw new Error('Scalar result layers require run receipt and physical time')
    if (packet.source.meshQuality !== 'accepted') throw new Error('Scalar result layers require accepted mesh quality')
    validateLut(layer.lut)
    if (layer.kind === 'surfaceField' || layer.kind === 'slice') {
      if (layer.kind === 'slice') {
        finiteArray(layer.originMeters, `${layer.layerId} origin`)
        finiteArray(layer.normal, `${layer.layerId} normal`)
        if (Math.hypot(...layer.normal) <= 0) throw new Error(`Invalid ${layer.layerId} slice normal`)
      }
      const nPoints = validatePositions(layer.positions, `${layer.layerId} positions`)
      validateIndices(layer.indices, nPoints, 3, `${layer.layerId} triangles`)
      finiteArray(layer.values, `${layer.layerId} values`)
      if (layer.values.length !== nPoints) throw new Error(`Field values do not match ${layer.layerId} points`)
    } else {
      for (const [i, line] of layer.lines.entries()) {
        const nPoints = validatePositions(line.positions, `${layer.layerId} line ${i} positions`)
        finiteArray(line.values, `${layer.layerId} line ${i} values`)
        if (nPoints < 2 || line.values.length !== nPoints) throw new Error(`Invalid ${layer.layerId} line ${i} point/value count`)
      }
    }
  }
  if (packet.source.run === null && packet.timeSeconds !== null) throw new Error('Mesh-only packet cannot invent a physical time')
}

export function serializeResultLayerSet(value: ResultLayerSet): string {
  validateResultLayerSet(value)
  return JSON.stringify(value)
}
export function parseResultLayerSet(json: string): ResultLayerSet {
  const value: unknown = JSON.parse(json)
  validateResultLayerSet(value)
  return value
}

export function sameResultSource(a:ResultSource|undefined,b:ResultSource):boolean {
 return !!a&&(['projectUid','projectContentSha256','cfdrunSha256','meshReceiptSha256','meshQuality'] as const).every(key=>a[key]===b[key])&&a.run?.uid===b.run?.uid&&a.run?.receiptSha256===b.run?.receiptSha256
}

/** Fail closed when a delayed layer update belongs to another project, run or mesh. */
export function assertResultLayerCompatibility(packet: ResultLayerSet, source: ResultSource, timeSeconds: number | null, field?: ScalarField): void {
  validateResultLayerSet(packet)
  for (const key of ['projectUid', 'projectContentSha256', 'cfdrunSha256', 'meshReceiptSha256', 'meshQuality'] as const) {
    if (packet.source[key] !== source[key]) throw new Error(`Result layer ${key} does not match current source`)
  }
  if (packet.source.run?.uid !== source.run?.uid || packet.source.run?.receiptSha256 !== source.run?.receiptSha256) {
    throw new Error('Result layer run receipt does not match current run')
  }
  if (packet.timeSeconds !== timeSeconds) throw new Error('Result layer physical time does not match current sample')
  if (field) {
    for (const layer of packet.layers) {
      if (layer.kind !== 'meshEdges' &&layer.kind!=='meshFaces'&& (layer.field.name !== field.name || layer.field.unit !== field.unit)) {
        throw new Error(`Result layer field does not match current selection: ${layer.layerId}`)
      }
    }
  }
}
