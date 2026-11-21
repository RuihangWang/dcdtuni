import { describe, expect, it } from 'vitest'
import {
  clearResultLayerSet, currentResultLayerSet, publishDecodedBoundaryResultLayers, publishResultLayerSet,
  currentResultLayerOwner, resultLayerRevision, subscribeResultLayerRevision,
} from '../src/renderer/resultLayerRevision'
import type { ResultLayerSet } from '../src/renderer/resultLayers'
import type { DecodedMesh } from '../src/postprocess'

const hash = (digit: string) => digit.repeat(64)
const packet: ResultLayerSet = {
  schema: 'dcdtuni.render-result-layers/v1', revision: 1,
  source: {
    projectUid: 'p', projectContentSha256: hash('a'), cfdrunSha256: hash('b'),
    meshReceiptSha256: hash('c'), meshQuality: 'diagnostic', run: null,
  },
  coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
  timeSeconds: null,
  layers: [{ kind: 'meshEdges', layerId: 'edges/wall', visible: true,
    meshPatchName: 'wall', sourceFaceUids: ['p/wall/source-face'], opacity: 0.85,
    positions: [0, 0, 0, 1, 0, 0], indices: [0, 1] }],
}

describe('independent result-layer revision', () => {
  it('publishes a validated layer update and notifies subscribers without touching a scene primitive', () => {
    clearResultLayerSet()
    const before = resultLayerRevision()
    const observed: number[] = []
    const unsubscribe = subscribeResultLayerRevision(() => observed.push(resultLayerRevision()))
    try {
      publishResultLayerSet(packet, packet.source, null)
      expect(resultLayerRevision()).toBe(before + 1)
      expect(observed).toEqual([before + 1])
      expect(currentResultLayerSet()?.layers[0]).toMatchObject({ kind: 'meshEdges', sourceFaceUids: ['p/wall/source-face'] })
      expect('modelUid' in currentResultLayerSet()!.layers[0]!).toBe(false)
    } finally { unsubscribe(); clearResultLayerSet() }
  })

  it('rejects stale source and non-monotone revision without notifying', () => {
    clearResultLayerSet()
    const observed: number[] = []
    const unsubscribe = subscribeResultLayerRevision(() => observed.push(resultLayerRevision()))
    try {
      expect(() => publishResultLayerSet(packet, { ...packet.source, meshReceiptSha256: hash('d') }, null)).toThrow(/meshReceiptSha256/)
      expect(observed).toEqual([])
      publishResultLayerSet(packet, packet.source, null)
      expect(() => publishResultLayerSet(packet, packet.source, null)).toThrow(/revision/)
      expect(observed).toHaveLength(1)
    } finally { unsubscribe(); clearResultLayerSet() }
  })

  it('accepts decoded boundary data with verified identities and supports explicit invalidation', () => {
    clearResultLayerSet()
    const mesh: DecodedMesh = {
      positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
      indices: new Uint32Array([0, 1, 2]), edges: new Uint32Array([0, 1, 1, 2, 2, 0]),
      patches: [{ name: 'wall', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true }],
      totals: { points: 3, triangles: 1, edges: 3 },
    }
    const runSource = { ...packet.source, meshQuality: 'accepted' as const,
      run: { uid: 'run-1', receiptSha256: hash('d') } }
    publishDecodedBoundaryResultLayers({
      mesh, values: new Float32Array([300, 301, 302]), sourceFacesByPatch: { wall: ['p/wall/source-face'] },
      source: runSource, timeSeconds: 10, revision: 1, field: { name: 'T', unit: 'K' },
      lut: { palette: 'coolwarm', range: [290, 310], opacity: 1 },
    })
    expect(currentResultLayerSet()?.layers.map(x => x.kind)).toEqual(['meshEdges', 'surfaceField'])
    clearResultLayerSet()
    expect(currentResultLayerSet()).toBeNull()
  })

  it('does not let mesh-preview cleanup erase an active air result or vice versa', () => {
    clearResultLayerSet()
    const runSource = { ...packet.source, meshQuality: 'accepted' as const,
      run: { uid: 'run-1', receiptSha256: hash('d') } }
    const air = { ...packet, source: runSource, timeSeconds: 10, revision: 100 }
    publishResultLayerSet(air, runSource, 10, undefined, 'air-postprocess', {
      owner: 'air-postprocess', session: { runId: 'selected-run', timeValue: 10, revision: 'pp-1' },
      surfaceField: 'T', meshReceiptSha256: runSource.meshReceiptSha256,
    })
    clearResultLayerSet('mesh-preview')
    expect(currentResultLayerSet()).toEqual(air)
    expect(currentResultLayerOwner()).toBe('air-postprocess')

    const preview = { ...packet, revision: 101 }
    publishResultLayerSet(preview, preview.source, null, undefined, 'mesh-preview', {
      owner: 'mesh-preview', meshSnapshotKey: hash('e'),
    })
    clearResultLayerSet('air-postprocess')
    expect(currentResultLayerSet()).toEqual(preview)
    expect(currentResultLayerOwner()).toBe('mesh-preview')
    clearResultLayerSet()
  })
})
