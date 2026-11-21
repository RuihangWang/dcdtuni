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
interface BoundaryLayer extends CommonLayer { meshPatchName: string; sourceFaceUids: string[] }
export interface MeshEdgesLayer extends BoundaryLayer {
  kind: 'meshEdges'; positions: number[]; indices: number[]; opacity: number
}
export interface SurfaceFieldLayer extends BoundaryLayer {
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
export type ResultLayer = MeshEdgesLayer | SurfaceFieldLayer | SliceResultLayer | StreamlineResultLayer
export interface ResultLayerSet {
  schema: 'dcdtuni.render-result-layers/v1'
  revision: number
  source: ResultSource
  coordinateSystem: { upAxis: 'Y'; metersPerUnit: 1; handedness: 'right' }
  timeSeconds: number | null
  layers: ResultLayer[]
}

const validate = new Ajv({ strict: true }).compile(schema)

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

/** JSON Schema rejects unknown fields (including modelUid and ShaderMaterial). */
export function validateResultLayerSet(value: unknown): asserts value is ResultLayerSet {
  if (!validate(value)) throw new Error(`Render result layer schema: ${new Ajv().errorsText(validate.errors)}`)
  const packet = value as unknown as ResultLayerSet
  if (packet.timeSeconds !== null && !Number.isFinite(packet.timeSeconds)) throw new Error('Non-finite physical time')
  const ids = new Set<string>()
  const patchBindings = new Map<string, string[]>()
  for (const layer of packet.layers) {
    if (ids.has(layer.layerId)) throw new Error(`Duplicate result layer ${layer.layerId}`)
    ids.add(layer.layerId)
    if (layer.kind === 'meshEdges' || layer.kind === 'surfaceField') {
      validateSourceFaceUids(layer.sourceFaceUids, layer.meshPatchName)
      const bound = patchBindings.get(layer.meshPatchName)
      if (bound && (bound.length !== layer.sourceFaceUids.length || bound.some((uid, i) => uid !== layer.sourceFaceUids[i]))) {
        throw new Error(`Result patch UID list mismatch for ${layer.meshPatchName}`)
      }
      patchBindings.set(layer.meshPatchName, layer.sourceFaceUids)
    }
    if (layer.kind === 'meshEdges') {
      const nPoints = validatePositions(layer.positions, `${layer.layerId} positions`)
      validateIndices(layer.indices, nPoints, 2, `${layer.layerId} edges`)
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
      if (layer.kind !== 'meshEdges' && (layer.field.name !== field.name || layer.field.unit !== field.unit)) {
        throw new Error(`Result layer field does not match current selection: ${layer.layerId}`)
      }
    }
  }
}
