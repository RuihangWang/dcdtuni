/** CPU producer for verified native OpenFOAM results. No Three.js scene is inspected. */
import type { DecodedMesh, SlicePayload, StreamlineSet } from '../postprocess'
import type { ColorMap } from '../postprocess/ppColormaps'
import { assertSessionIdentity, type PostprocessSession } from '../postprocess/ppRevision'
import { boundaryMeshLayers, boundarySurfaceFieldLayers, sliceResultLayer, streamlineResultLayer } from './resultLayerAdapter'
import { publishResultLayerSet, resultLayerRevision } from './resultLayerRevision'
import type { ResultLayerSet, ResultSource, ScalarField, ScalarLut } from './resultLayers'

/** Supplied only by a backend that checked the run, mesh and complete final patch lineage. */
export interface VerifiedAirResultDescriptor {
  schema: 'dcdtuni.verified-air-result-source/v1'
  postprocess: PostprocessSession
  source: ResultSource
  sourceFacesByPatch: Record<string, readonly string[]>
  /** Units of the raw OpenFOAM arrays, never the UI's display units. */
  fieldUnits: Record<string, string>
}

export interface VerifiedMeshPreviewDescriptor {
  schema: 'dcdtuni.verified-mesh-preview-source/v1'
  /** Two-request stat identity, not a mesh content receipt. */
  meshSnapshotKey: string
  source: ResultSource & { run: null }
  sourceFacesByPatch: Record<string, readonly string[]>
}

interface DisplayLut {
  colorMap: ColorMap
  range: [number, number]
  opacity: number
}

export interface WindResultFrame {
  descriptor: VerifiedAirResultDescriptor
  session: PostprocessSession
  currentProject: { uid: string; contentSha256: string }
  mesh: DecodedMesh
  /** Live UI field selection; a delayed response for another field is stale. */
  activeSurfaceField: string
  surface: DisplayLut & { field: string; values: Float32Array; hidden: Set<string>; visible?: boolean }
  slices: Array<DisplayLut & {
    id: number; payload: SlicePayload; origin: [number, number, number]
    normal: [number, number, number]; visible: boolean
  }>
  streamlines: Array<DisplayLut & {
    id: number; set: StreamlineSet; tubeRadius: number; visible: boolean
  }>
}

function fieldWithRawUnit(name: string, descriptor: VerifiedAirResultDescriptor): ScalarField {
  const isVelocity = name === 'U' || name === 'speed' || name === 'Umag' || /^U[xyz]$/.test(name)
  if (name !== 'T' && !isVelocity) throw new Error(`Unsupported result field unit for ${name}`)
  const unit = descriptor.fieldUnits[name]
  if (typeof unit !== 'string' || !unit.trim() || unit === 'unknown') {
    throw new Error(`No verified raw unit for result field ${name}`)
  }
  if (name === 'T' && unit !== 'K') throw new Error('Raw OpenFOAM T must use K, not UI display units')
  if (isVelocity && unit !== 'm/s') {
    throw new Error(`Raw velocity ${name} must use m/s`)
  }
  return { name, unit }
}

function lut(value: DisplayLut): ScalarLut {
  return { palette: value.colorMap, range: [...value.range], opacity: value.opacity }
}

/** A truthful packet is impossible when any certificate or current-frame identity is missing. */
export function publishWindResultFrame(input: WindResultFrame): ResultLayerSet {
  const { descriptor, session, currentProject, mesh, surface } = input
  if (descriptor.schema !== 'dcdtuni.verified-air-result-source/v1') throw new Error('Unverified air result descriptor')
  assertSessionIdentity(descriptor.postprocess, session)
  if (session.timeValue === null || descriptor.source.run === null || descriptor.source.meshQuality !== 'accepted') {
    throw new Error('Wind scalar result requires an accepted mesh, run receipt and physical time')
  }
  if (descriptor.source.projectUid !== currentProject.uid ||
      descriptor.source.projectContentSha256 !== currentProject.contentSha256) {
    throw new Error('Wind result project revision does not match the current .cfdproj')
  }
  if (surface.field !== input.activeSurfaceField) throw new Error('Stale result field does not match current selection')

  const field = fieldWithRawUnit(surface.field, descriptor)
  const layers: ResultLayerSet['layers'] = [
    ...boundaryMeshLayers(mesh, descriptor.sourceFacesByPatch, surface.hidden).map(layer => ({ ...layer, visible: false })),
    ...boundarySurfaceFieldLayers(mesh, surface.values, descriptor.sourceFacesByPatch, field, lut(surface), surface.hidden)
      .map(layer => ({ ...layer, visible: surface.visible !== false })),
  ]
  for (const slice of input.slices) {
    if (!slice.visible) continue
    layers.push(sliceResultLayer(slice.payload, `${session.runId}/time-${session.timeValue}/slice-${slice.id}`,
      slice.origin, slice.normal, fieldWithRawUnit(slice.payload.field, descriptor), lut(slice)))
  }
  for (const streamline of input.streamlines) {
    if (!streamline.visible) continue
    const fieldUnit = fieldWithRawUnit(streamline.set.field, descriptor)
    layers.push(streamlineResultLayer(streamline.set,
      `${session.runId}/time-${session.timeValue}/seed-${streamline.id}`,
      fieldUnit.unit, lut(streamline), streamline.tubeRadius))
  }

  const next: ResultLayerSet = {
    schema: 'dcdtuni.render-result-layers/v1',
    // Clearing a packet also advances the independent revision. Never reuse a
    // number for the same source after the user closes and reopens a layer.
    revision: resultLayerRevision() + 1,
    source: descriptor.source,
    coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
    timeSeconds: session.timeValue,
    layers,
  }
  publishResultLayerSet(next, descriptor.source, session.timeValue, undefined, 'air-postprocess', {
    owner: 'air-postprocess', session: { ...session },
    surfaceField: input.activeSurfaceField,
    meshReceiptSha256: descriptor.source.meshReceiptSha256,
  })
  return next
}

/** A diagnostic preview can show only edges from proven final patch lineage. */
export function publishMeshPreviewEdges(input: {
  descriptor: VerifiedMeshPreviewDescriptor
  meshSnapshotKey: string
  currentProject: { uid: string; contentSha256: string }
  mesh: DecodedMesh
  hidden: Set<string>
  opacity: number
}): ResultLayerSet {
  const { descriptor, currentProject } = input
  if (descriptor.schema !== 'dcdtuni.verified-mesh-preview-source/v1' ||
      descriptor.meshSnapshotKey !== input.meshSnapshotKey || descriptor.source.run !== null) {
    throw new Error('Verified preview descriptor does not match the loaded mesh')
  }
  if (descriptor.source.projectUid !== currentProject.uid ||
      descriptor.source.projectContentSha256 !== currentProject.contentSha256) {
    throw new Error('Preview source does not match the current .cfdproj revision')
  }
  const next: ResultLayerSet = {
    schema: 'dcdtuni.render-result-layers/v1', revision: resultLayerRevision() + 1,
    source: descriptor.source, coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
    timeSeconds: null,
    layers: boundaryMeshLayers(input.mesh, descriptor.sourceFacesByPatch, input.hidden)
      .map(layer => ({ ...layer, opacity: input.opacity })),
  }
  publishResultLayerSet(next, descriptor.source, null, undefined, 'mesh-preview', {
    owner: 'mesh-preview', meshSnapshotKey: input.meshSnapshotKey,
  })
  return next
}
