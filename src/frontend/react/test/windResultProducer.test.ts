import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, expect, it } from 'vitest'
import { decodeSlice, decodeStreamlines, type DecodedMesh } from '../src/postprocess'
import { clearResultLayerSet, currentResultLayerSet } from '../src/renderer/resultLayerRevision'
import { publishMeshPreviewEdges, publishWindResultFrame } from '../src/renderer/windResultProducer'

const hash = (digit: string) => digit.repeat(64)
const mesh: DecodedMesh = {
  positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
  indices: new Uint32Array([0, 1, 2]),
  edges: new Uint32Array([0, 1, 1, 2, 2, 0]),
  patches: [{ name: 'wall', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true }],
  totals: { points: 3, triangles: 1, edges: 3 },
}
const session = { runId: 'run-1', timeValue: 20, revision: 'pp-1' }
const descriptor = {
  schema: 'dcdtuni.verified-air-result-source/v1' as const,
  postprocess: session,
  source: {
    projectUid: 'p', projectContentSha256: hash('a'), cfdrunSha256: hash('b'),
    meshReceiptSha256: hash('c'), meshQuality: 'accepted' as const,
    run: { uid: 'run-1', receiptSha256: hash('d') },
  },
  sourceFacesByPatch: { wall: ['p/wall/source-face'] },
  fieldUnits: { T: 'K', speed: 'm/s' },
}

afterEach(() => clearResultLayerSet())

it('publishes real decoded mesh, raw-K field, internal cut and streamlines under one accepted frame', () => {
  const sliceRaw = readFileSync(join(__dirname, '__fixtures__/pp_slice_small.bin'))
  const streamRaw = readFileSync(join(__dirname, '__fixtures__/pp_streamlines_small.bin'))
  const cut = decodeSlice(sliceRaw.buffer.slice(sliceRaw.byteOffset, sliceRaw.byteOffset + sliceRaw.byteLength))
  const stream = decodeStreamlines(streamRaw.buffer.slice(streamRaw.byteOffset, streamRaw.byteOffset + streamRaw.byteLength))
  publishWindResultFrame({
    descriptor, session, currentProject: { uid: 'p', contentSha256: hash('a') }, mesh,
    activeSurfaceField: 'T',
    surface: { field: 'T', values: new Float32Array([300, 301, 302]),
      colorMap: 'coolwarm', range: [299, 303], opacity: 0.8, hidden: new Set<string>() },
    slices: [{ id: 1, payload: cut, origin: [0, 0.5, 0], normal: [0, 1, 0],
      colorMap: 'coolwarm', range: [288, 295], opacity: 1, visible: true }],
    streamlines: [{ id: 2, set: stream, colorMap: 'viridis', range: [0, 1],
      opacity: 1, tubeRadius: 0.02, visible: true }],
  })
  const packet = currentResultLayerSet()!
  expect(packet.source).toEqual(descriptor.source)
  expect(packet.timeSeconds).toBe(20)
  expect(packet.layers.map(layer => layer.kind)).toEqual(['meshEdges', 'surfaceField', 'slice', 'streamline'])
  expect(packet.layers[1]).toMatchObject({ values: [300, 301, 302], field: { name: 'T', unit: 'K' },
    lut: { range: [299, 303] } })
  expect(packet.layers[2]).toMatchObject({ sourceSliceUid: 'run-1/time-20/slice-1', field: { name: 'T', unit: 'K' } })
  expect(packet.layers[3]).toMatchObject({ sourceSeedUid: 'run-1/time-20/seed-2', field: { name: stream.field, unit: 'm/s' } })
})

it('rejects a stale session or unknown field unit without publishing a packet', () => {
  const base = { descriptor, session, currentProject: { uid: 'p', contentSha256: hash('a') }, mesh,
    activeSurfaceField: 'T',
    surface: { field: 'T', values: new Float32Array([300, 301, 302]),
      colorMap: 'coolwarm' as const, range: [299, 303] as [number, number], opacity: 1, hidden: new Set<string>() },
    slices: [], streamlines: [] }
  expect(() => publishWindResultFrame({ ...base, session: { ...session, timeValue: 21 } })).toThrow(/session|time/i)
  expect(() => publishWindResultFrame({ ...base, descriptor: { ...descriptor, fieldUnits: {} } })).toThrow(/unit/i)
  expect(() => publishWindResultFrame({ ...base, descriptor: { ...descriptor, fieldUnits: { T: '°C' } } })).toThrow(/K|Kelvin|unit/i)
  expect(() => publishWindResultFrame({ ...base, activeSurfaceField: 'p', surface: { ...base.surface, field: 'p' },
    descriptor: { ...descriptor, fieldUnits: { p: 'Pa' } } })).toThrow(/unsupported.*unit/i)
  expect(currentResultLayerSet()).toBeNull()
})

it('does not reuse an OV result revision after clearing and reopening the same source', () => {
  const input = { descriptor, session, currentProject: { uid: 'p', contentSha256: hash('a') }, mesh,
    activeSurfaceField: 'T',
    surface: { field: 'T', values: new Float32Array([300, 301, 302]),
      colorMap: 'coolwarm' as const, range: [299, 303] as [number, number], opacity: 1, hidden: new Set<string>() },
    slices: [], streamlines: [] }
  const first = publishWindResultFrame(input)
  clearResultLayerSet()
  const reopened = publishWindResultFrame(input)
  expect(reopened.revision).toBeGreaterThan(first.revision)
})

it('accepts an execution receipt UID distinct from the postprocess path hash', () => {
  const execution = { ...descriptor, source: { ...descriptor.source,
    run: { uid: 'execution-42', receiptSha256: hash('d') } } }
  expect(() => publishWindResultFrame({
    descriptor: execution, session, currentProject: { uid: 'p', contentSha256: hash('a') }, mesh,
    activeSurfaceField: 'T',
    surface: { field: 'T', values: new Float32Array([300, 301, 302]),
      colorMap: 'coolwarm', range: [299, 303], opacity: 1, hidden: new Set<string>() },
    slices: [], streamlines: [],
  })).not.toThrow()
})

it('refuses an old surface response after the current field selection changes', () => {
  expect(() => publishWindResultFrame({
    descriptor, session, currentProject: { uid: 'p', contentSha256: hash('a') }, mesh,
    activeSurfaceField: 'Umag',
    surface: { field: 'T', values: new Float32Array([300, 301, 302]),
      colorMap: 'coolwarm', range: [299, 303], opacity: 1, hidden: new Set<string>() },
    slices: [], streamlines: [],
  })).toThrow(/field.*selection|stale.*field/i)
  expect(currentResultLayerSet()).toBeNull()
})

it('publishes only sourced diagnostic mesh edges before any accepted air solve', () => {
  const previewSource = { ...descriptor.source, meshQuality: 'diagnostic' as const, run: null }
  const preview = publishMeshPreviewEdges({
    descriptor: { schema: 'dcdtuni.verified-mesh-preview-source/v1',
      meshSnapshotKey: hash('e'), source: previewSource,
      sourceFacesByPatch: descriptor.sourceFacesByPatch },
    meshSnapshotKey: hash('e'), currentProject: { uid: 'p', contentSha256: hash('a') },
    mesh, hidden: new Set<string>(), opacity: 0.85,
  })
  expect(preview.source.run).toBeNull()
  expect(preview.timeSeconds).toBeNull()
  expect(preview.layers).toMatchObject([{ kind: 'meshEdges', meshPatchName: 'wall',
    sourceFaceUids: ['p/wall/source-face'] }])
  expect(preview.layers).toHaveLength(1)
})
