import { describe, expect, it } from 'vitest'
import { localIndices, patchOffsets } from '../src/run/meshPreview'
import type { PatchMeta } from '../src/postprocess'

const patches: PatchMeta[] = [
  { name: 'a', nPoints: 4, nTriangles: 2, nEdges: 4, defaultVisible: true },
  { name: 'b', nPoints: 8, nTriangles: 6, nEdges: 12, defaultVisible: false },
  { name: 'c', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true },
] as PatchMeta[]

describe('patchOffsets', () => {
  it('accumulates points, triangles and edges independently', () => {
    expect(patchOffsets(patches)).toEqual([
      { point: 0, triangle: 0, edge: 0 },
      { point: 4, triangle: 2, edge: 4 },
      { point: 12, triangle: 8, edge: 16 },
    ])
  })

  it('treats a missing nEdges as zero rather than NaN', () => {
    // One NaN offset poisons every later patch's slice, and the geometry comes
    // out empty with no error.
    const noEdges = [{ name: 'x', nPoints: 2, nTriangles: 1 }] as unknown as PatchMeta[]
    expect(patchOffsets([...noEdges, ...patches])[1]!.edge).toBe(0)
  })
})

describe('localIndices', () => {
  it('re-bases global indices onto the patch slice', () => {
    expect(Array.from(localIndices(new Uint32Array([4, 5, 6]), 4))).toEqual([0, 1, 2])
  })

  it('leaves the first patch untouched', () => {
    expect(Array.from(localIndices(new Uint32Array([0, 1, 2]), 0))).toEqual([0, 1, 2])
  })
})
