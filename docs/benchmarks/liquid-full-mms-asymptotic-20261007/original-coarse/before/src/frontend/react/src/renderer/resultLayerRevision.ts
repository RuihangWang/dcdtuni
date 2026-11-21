/** Independent result-layer revision; a scene primitive never owns solver fields. */
import {
  assertResultLayerCompatibility, parseResultLayerSet, serializeResultLayerSet,
  type ResultLayerSet, type ResultSource, type ScalarField, type ScalarLut,
} from './resultLayers'
import { boundaryMeshLayers, boundarySurfaceFieldLayers } from './resultLayerAdapter'
import type { DecodedMesh } from '../postprocess'
import type { PostprocessSession } from '../postprocess/ppRevision'

let packet: ResultLayerSet | null = null
export type ResultLayerOwner = 'air-postprocess' | 'mesh-preview'
export type ResultLayerSelection =
  | { owner: 'air-postprocess'; session: PostprocessSession; surfaceField: string; meshReceiptSha256: string }
  | { owner: 'mesh-preview'; meshSnapshotKey: string }
export type AcceptedResultContext = {
  source: ResultSource; timeSeconds: number | null; selection: ResultLayerSelection | null
}
let acceptedContext: AcceptedResultContext | null = null
let liveAirSelection: Extract<ResultLayerSelection, { owner: 'air-postprocess' }> | null = null
let owner: ResultLayerOwner | null = null
let revision = 0
const listeners = new Set<() => void>()

function emit() {
  revision++
  for (const listener of [...listeners]) listener()
}

function sameSource(a: ResultSource, b: ResultSource): boolean {
  return a.projectUid === b.projectUid && a.projectContentSha256 === b.projectContentSha256 &&
    a.cfdrunSha256 === b.cfdrunSha256 && a.meshReceiptSha256 === b.meshReceiptSha256 &&
    a.meshQuality === b.meshQuality &&
    a.run?.uid === b.run?.uid && a.run?.receiptSha256 === b.run?.receiptSha256
}

export function resultLayerRevision(): number { return revision }
export function currentResultLayerSet(): ResultLayerSet | null { return packet }
/** The publisher's current verified selection, captured separately from the packet. */
export function currentResultLayerContext(): AcceptedResultContext | null {
  return acceptedContext
}
export function currentResultLayerOwner(): ResultLayerOwner | null { return owner }
export function currentLiveAirResultSelection() { return liveAirSelection }
export function setLiveAirResultSelection(
  selection: Extract<ResultLayerSelection, { owner: 'air-postprocess' }> | null,
): void {
  const previous = liveAirSelection
  liveAirSelection = selection ? structuredClone(selection) : null
  if (owner === 'air-postprocess' && packet && (
    !previous || !liveAirSelection ||
    previous.session.runId !== liveAirSelection.session.runId ||
    previous.session.timeValue !== liveAirSelection.session.timeValue ||
    previous.session.revision !== liveAirSelection.session.revision ||
    previous.surfaceField !== liveAirSelection.surfaceField ||
    previous.meshReceiptSha256 !== liveAirSelection.meshReceiptSha256
  )) clearResultLayerSet('air-postprocess')
}
export function clearStaleResultLayerSet(projectUid: string, projectContentSha256: string): void {
  if (packet && (packet.source.projectUid !== projectUid ||
      packet.source.projectContentSha256 !== projectContentSha256)) clearResultLayerSet()
}
export function subscribeResultLayerRevision(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** Caller must supply CURRENT accepted source and time, not the packet's own claims. */
export function publishResultLayerSet(
  next: ResultLayerSet, currentSource: ResultSource, currentTimeSeconds: number | null,
  currentField?: ScalarField,
  nextOwner: ResultLayerOwner | null = null,
  selection: ResultLayerSelection | null = null,
): void {
  assertResultLayerCompatibility(next, currentSource, currentTimeSeconds, currentField)
  if (nextOwner && (!selection || selection.owner !== nextOwner)) {
    throw new Error('A verified result publisher must identify the current selection')
  }
  if (packet && sameSource(packet.source, next.source) && next.revision <= packet.revision) {
    throw new Error('Result layer revision must increase for the same source')
  }
  // Own a JSON-safe snapshot: later changes to decoded typed arrays cannot
  // silently change what subscribers believe was accepted at this revision.
  packet = parseResultLayerSet(serializeResultLayerSet(next))
  acceptedContext = { source: structuredClone(currentSource), timeSeconds: currentTimeSeconds,
    selection: selection ? structuredClone(selection) : null }
  owner = nextOwner
  emit()
}

/** Entry point for a verified, already-decoded OpenFOAM boundary sample. */
export function publishDecodedBoundaryResultLayers(options: {
  mesh: DecodedMesh
  values: Float32Array
  sourceFacesByPatch: Record<string, readonly string[]>
  source: ResultSource
  timeSeconds: number
  revision: number
  field: ScalarField
  lut: ScalarLut
  hidden?: Set<string>
}): void {
  const { mesh, values, sourceFacesByPatch, source, timeSeconds, field, lut, hidden } = options
  const next: ResultLayerSet = {
    schema: 'dcdtuni.render-result-layers/v1', revision: options.revision,
    source, coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
    timeSeconds,
    layers: [
      ...boundaryMeshLayers(mesh, sourceFacesByPatch, hidden),
      ...boundarySurfaceFieldLayers(mesh, values, sourceFacesByPatch, field, lut, hidden),
    ],
  }
  publishResultLayerSet(next, source, timeSeconds, field)
}

export function clearResultLayerSet(onlyOwner?: ResultLayerOwner): void {
  if (onlyOwner && owner !== onlyOwner) return
  if (!packet) return
  packet = null
  acceptedContext = null
  owner = null
  emit()
}
