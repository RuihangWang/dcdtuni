import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { decodeSlice, decodeStreamlines, type DecodedMesh } from '../src/postprocess'
import {
  assertResultLayerCompatibility,
  parseResultLayerSet,
  serializeResultLayerSet,
  validateResultLayerSet,
  type ResultSource,
} from '../src/renderer/resultLayers'
import {
  boundaryMeshLayers,
  boundarySurfaceFieldLayers,
  sliceResultLayer,
  streamlineResultLayer,
} from '../src/renderer/resultLayerAdapter'

const hash = (digit: string) => digit.repeat(64)
const source: ResultSource = {
  projectUid: 'project-a', projectContentSha256: hash('a'),
  cfdrunSha256: hash('b'), meshReceiptSha256: hash('c'), meshQuality: 'accepted',
  run: { uid: 'run-7', receiptSha256: hash('d') },
}
const patches = [
  { name: 'inlet', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true },
  { name: 'outlet', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true },
]
const mesh: DecodedMesh = {
  positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 2, 0, 0, 3, 0, 0, 2, 1, 0]),
  indices: new Uint32Array([0, 1, 2, 3, 4, 5]),
  edges: new Uint32Array([0, 1, 1, 2, 2, 0, 3, 4, 4, 5, 5, 3]),
  patches,
  totals: { points: 6, triangles: 2, edges: 6 },
}
const sourceFacesByPatch = {
  inlet: ['project-a/m_inlet/source-face'], outlet: ['project-a/m_outlet/source-face'],
}
const field = { name: 'T', unit: 'K' }
const lut = { palette: 'coolwarm' as const, range: [290, 310] as [number, number], opacity: 0.8 }

function packet() {
  return {
    schema: 'dcdtuni.render-result-layers/v1' as const,
    revision: 2,
    source,
    coordinateSystem: { upAxis: 'Y' as const, metersPerUnit: 1 as const, handedness: 'right' as const },
    timeSeconds: 20,
    layers: [
      ...boundaryMeshLayers(mesh, sourceFacesByPatch, new Set(['outlet'])),
      ...boundarySurfaceFieldLayers(mesh, new Float32Array([290, 291, 292, 300, 301, 302]), sourceFacesByPatch, field, lut, new Set(['outlet'])),
    ],
  }
}

describe('renderer-neutral result layer V1', () => {
  it('serializes original polygon edges and raw scalar values per explicitly bound patch', () => {
    const result = parseResultLayerSet(serializeResultLayerSet(packet()))
    expect(result.layers).toHaveLength(2)
    expect(result.layers[0]).toMatchObject({
      kind: 'meshEdges', layerId: 'meshEdges/inlet', meshPatchName: 'inlet',
      sourceFaceUids: sourceFacesByPatch.inlet,
      positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 1, 2, 2, 0],
    })
    expect(result.layers[1]).toMatchObject({
      kind: 'surfaceField', layerId: 'surfaceField/T/inlet', sourceFaceUids: sourceFacesByPatch.inlet,
      field, lut, values: [290, 291, 292], indices: [0, 1, 2],
    })
    expect(result.layers.every(layer => !('modelUid' in layer) && !('material' in layer))).toBe(true)
  })

  it('keeps a real decoded streamline payload as ordered raw points and scalar samples', () => {
    const raw = readFileSync(join(__dirname, '__fixtures__/pp_streamlines_small.bin'))
    const set = decodeStreamlines(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength))
    const layer = streamlineResultLayer(set, 'seed-1', 'm/s', {
      palette: 'viridis', range: set.scalarRange, opacity: 1,
    })
    const result = { ...packet(), layers: [layer] }
    validateResultLayerSet(result)
    expect(result.layers[0]).toMatchObject({ kind: 'streamline', field: { name: set.field, unit: 'm/s' }, sourceSeedUid: 'seed-1' })
    expect((result.layers[0] as typeof layer).lines.length).toBe(set.lines.length)
    expect((result.layers[0] as typeof layer).lines[0]?.values).toEqual(Array.from(set.scalars.slice(set.lines[0]!.start, set.lines[0]!.start + set.lines[0]!.count)))
  })

  it('accepts a volume cut as its own sourced slice layer, never a boundary patch', () => {
    const cut = {
      kind: 'slice', layerId: 'slice/cut-1', visible: true,
      sourceSliceUid: 'cut-1', originMeters: [0, 0.5, 0], normal: [0, 1, 0],
      positions: [0, 0.5, 0, 1, 0.5, 0, 0, 0.5, 1], indices: [0, 1, 2],
      values: [290, 291, 292], field, lut,
    }
    const result = { ...packet(), layers: [cut] }
    expect(() => validateResultLayerSet(result)).not.toThrow()
    expect('meshPatchName' in cut).toBe(false)
    expect(() => validateResultLayerSet({ ...result, source: { ...source, run: null } })).toThrow(/run.*time/i)
  })

  it('adapts the actual decoded internal slice without inventing a mesh patch', () => {
    const raw = readFileSync(join(__dirname, '__fixtures__/pp_slice_small.bin'))
    const cut = decodeSlice(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength))
    const layer = sliceResultLayer(cut, 'cut-1', [0, 0.5, 0], [0, 1, 0], field, lut)
    expect(layer).toMatchObject({
      kind: 'slice', sourceSliceUid: 'cut-1', originMeters: [0, 0.5, 0], normal: [0, 1, 0],
      indices: [0, 1, 2], values: [288.5, 291, 294], field,
    })
    expect('meshPatchName' in layer).toBe(false)
    expect(() => validateResultLayerSet({ ...packet(), layers: [layer] })).not.toThrow()
  })

  it('rejects unbound or corrupt patch geometry rather than guessing source identity', () => {
    expect(() => boundaryMeshLayers(mesh, { inlet: sourceFacesByPatch.inlet })).toThrow(/outlet.*UID/i)
    expect(() => boundaryMeshLayers(mesh, { ...sourceFacesByPatch, stale_patch: ['project-a/stale/source-face'] })).toThrow(/stale_patch.*mesh/i)
    expect(() => boundarySurfaceFieldLayers(mesh, new Float32Array(5), sourceFacesByPatch, field, lut)).toThrow(/field.*points/i)
    const corrupt: DecodedMesh = { ...mesh, indices: new Uint32Array([0, 3, 2, 3, 4, 5]) }
    expect(() => boundarySurfaceFieldLayers(corrupt, new Float32Array(6), sourceFacesByPatch, field, lut)).toThrow(/patch.*inlet.*index/i)
    const hiddenCorrupt: DecodedMesh = { ...mesh, edges: new Uint32Array([0, 1, 1, 2, 2, 0, 3, 4, 4, 0, 5, 3]) }
    expect(() => boundaryMeshLayers(hiddenCorrupt, sourceFacesByPatch, new Set(['outlet']))).toThrow(/patch.*outlet.*index/i)
  })

  it('rejects stale project/run/mesh receipts and physical time', () => {
    const result = packet()
    expect(() => assertResultLayerCompatibility(result, source, 20)).not.toThrow()
    expect(() => assertResultLayerCompatibility(result, { ...source, meshReceiptSha256: hash('e') }, 20)).toThrow(/meshReceiptSha256/)
    expect(() => assertResultLayerCompatibility(result, { ...source, run: { uid: 'another', receiptSha256: hash('d') } }, 20)).toThrow(/run/)
    expect(() => assertResultLayerCompatibility(result, source, 40)).toThrow(/time/)
    expect(() => assertResultLayerCompatibility(result, source, 20, { name: 'p', unit: 'Pa' })).toThrow(/field/)
  })

  it('refuses duplicate IDs, unknown shader/model fields, invalid LUT and nonfinite scalars', () => {
    const result = packet()
    expect(() => validateResultLayerSet({ ...result, layers: [result.layers[0], result.layers[0]] })).toThrow(/Duplicate.*layer/i)
    expect(() => validateResultLayerSet({ ...result, layers: [{ ...result.layers[1], modelUid: 'fake-editable' }] })).toThrow(/schema/i)
    expect(() => validateResultLayerSet({ ...result, layers: [{ ...result.layers[1], material: { type: 'ShaderMaterial' } }] })).toThrow(/schema/i)
    expect(() => validateResultLayerSet({ ...result, layers: [{ ...result.layers[1], lut: { ...lut, range: [310, 290] } }] })).toThrow(/LUT/i)
    expect(() => validateResultLayerSet({ ...result, layers: [{ ...result.layers[1], values: [290, NaN, 292] }] })).toThrow(/schema|finite/i)
    expect(() => validateResultLayerSet({ ...result, layers: [result.layers[0], { ...result.layers[1], sourceFaceUids: ['wrong-face'] }] })).toThrow(/patch.*UID/i)
    expect(() => validateResultLayerSet({ ...result, layers: [{ ...result.layers[0], sourceFaceUids: ['z-face', 'a-face'] }] })).toThrow(/sorted.*unique/i)
    expect(() => validateResultLayerSet({ ...result, layers: [{ ...result.layers[0], sourcePatchUid: 'legacy-single-face' }] })).toThrow(/schema/i)
  })

  it('mesh-only packet has no invented run or field time; scalar layers require both', () => {
    const meshOnly = { ...packet(), source: { ...source, run: null }, timeSeconds: null,
      layers: boundaryMeshLayers(mesh, sourceFacesByPatch) }
    expect(() => validateResultLayerSet(meshOnly)).not.toThrow()
    expect(() => validateResultLayerSet({ ...meshOnly, layers: packet().layers })).toThrow(/run.*time/i)
    expect(() => validateResultLayerSet({ ...meshOnly, source: { ...meshOnly.source, meshQuality: 'diagnostic' } })).not.toThrow()
    expect(() => validateResultLayerSet({ ...packet(), source: { ...source, meshQuality: 'diagnostic' } })).toThrow(/mesh quality/i)
  })

  it('keeps one aggregated mesh patch mapped to all three Shinaide source faces without attributing field values to a face', () => {
    const merged: DecodedMesh = { ...mesh, patches: [
      { name: 'merged_wall_baffle', nPoints: 6, nTriangles: 2, nEdges: 6, defaultVisible: true },
    ] }
    const sourceFaceUids = [
      'proj_Shinaide/m_22/source-sheet', 'proj_Shinaide/m_23/source-sheet', 'proj_Shinaide/m_24/source-sheet',
    ]
    const bindings = { merged_wall_baffle: sourceFaceUids }
    const edges = boundaryMeshLayers(merged, bindings)
    const surfaces = boundarySurfaceFieldLayers(merged, new Float32Array([290, 291, 292, 300, 301, 302]), bindings, field, lut)
    expect(edges).toHaveLength(1)
    expect(surfaces).toHaveLength(1)
    expect(edges[0]!.sourceFaceUids).toEqual(sourceFaceUids)
    expect(surfaces[0]!.sourceFaceUids).toEqual(sourceFaceUids)
    expect(surfaces[0]!.values).toEqual([290, 291, 292, 300, 301, 302])
    expect(() => validateResultLayerSet({ ...packet(), layers: [...edges, ...surfaces] })).not.toThrow()
    expect(() => boundaryMeshLayers(merged, { merged_wall_baffle: [...sourceFaceUids].reverse() })).toThrow(/sorted.*unique/i)
    expect(() => boundaryMeshLayers(merged, { merged_wall_baffle: [sourceFaceUids[0]!, sourceFaceUids[0]!] })).toThrow(/sorted.*unique/i)
    expect(() => boundaryMeshLayers(merged, {})).toThrow(/merged_wall_baffle.*UID/i)
  })
})
