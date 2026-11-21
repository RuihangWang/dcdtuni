import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import {
  patchAtTriangle,
  patchTriangleRanges,
  selectionIndices,
  setEdgeHighlight,
  setPatchHighlight,
  visibleTriangleTotal,
} from '../src/geometry/patchHighlight'
import { PATCH_HIGHLIGHT_COLOR } from '../src/geometry/patchListDom'

const PATCHES = [
  { name: 'a', nTriangles: 2 },
  { name: 'b', nTriangles: 3 },
  { name: 'c', nTriangles: 1 },
]

describe('patchTriangleRanges', () => {
  it('runs the patches end to end when nothing is hidden', () => {
    expect([...patchTriangleRanges(PATCHES, new Set()).entries()]).toEqual([
      ['a', { firstTriangle: 0, nTriangles: 2 }],
      ['b', { firstTriangle: 2, nTriangles: 3 }],
      ['c', { firstTriangle: 5, nTriangles: 1 }],
    ])
  })

  it('closes the gap a hidden patch leaves, and omits it entirely', () => {
    // This is the whole reason the function exists: `filterIndices` drops the
    // hidden patch's triangles from the index array, so everything after it
    // moves DOWN. Keeping the original offsets here would paint patch `c`
    // purple when the user selected `b`.
    const ranges = patchTriangleRanges(PATCHES, new Set(['a']))
    expect(ranges.has('a')).toBe(false)
    expect(ranges.get('b')).toEqual({ firstTriangle: 0, nTriangles: 3 })
    expect(ranges.get('c')).toEqual({ firstTriangle: 3, nTriangles: 1 })
  })
})

describe('visibleTriangleTotal', () => {
  it('counts only the drawn patches', () => {
    expect(visibleTriangleTotal(PATCHES, new Set())).toBe(6)
    expect(visibleTriangleTotal(PATCHES, new Set(['b']))).toBe(3)
    expect(visibleTriangleTotal(PATCHES, new Set(['a', 'b', 'c']))).toBe(0)
  })
})

describe('patchAtTriangle', () => {
  it('maps a faceIndex back to its patch', () => {
    expect(patchAtTriangle(PATCHES, new Set(), 0)).toBe('a')
    expect(patchAtTriangle(PATCHES, new Set(), 1)).toBe('a')
    expect(patchAtTriangle(PATCHES, new Set(), 2)).toBe('b')
    expect(patchAtTriangle(PATCHES, new Set(), 4)).toBe('b')
    expect(patchAtTriangle(PATCHES, new Set(), 5)).toBe('c')
  })

  it('follows the shift when a patch is hidden', () => {
    expect(patchAtTriangle(PATCHES, new Set(['a']), 0)).toBe('b')
    expect(patchAtTriangle(PATCHES, new Set(['a']), 3)).toBe('c')
  })

  it('returns null past the end and for a non-index', () => {
    expect(patchAtTriangle(PATCHES, new Set(), 6)).toBeNull()
    expect(patchAtTriangle(PATCHES, new Set(), -1)).toBeNull()
    expect(patchAtTriangle(PATCHES, new Set(), 1.5)).toBeNull()
  })
})

describe('selectionIndices', () => {
  const index = new Uint32Array(18)
  for (let i = 0; i < index.length; i++) index[i] = i

  it('slices out only the selected patches, in geometry order', () => {
    const ranges = patchTriangleRanges(PATCHES, new Set())
    // Asked for c then a; the output must still be ascending, or the overlay's
    // triangles interleave two patches and the normals come out wrong.
    const picked = selectionIndices(index, ranges, ['c', 'a'])
    expect([...picked]).toEqual([0, 1, 2, 3, 4, 5, 15, 16, 17])
  })

  it('ignores a name with no range', () => {
    const ranges = patchTriangleRanges(PATCHES, new Set(['b']))
    expect([...selectionIndices(index, ranges, ['b'])]).toEqual([])
  })
})

describe('setPatchHighlight', () => {
  it('paints and restores the original colour', () => {
    const mesh = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshPhongMaterial({ color: 0x123456, emissive: 0x000000 }),
    )
    setPatchHighlight(mesh, true)
    expect((mesh.material as THREE.MeshPhongMaterial).color.getHex()).toBe(PATCH_HIGHLIGHT_COLOR)
    setPatchHighlight(mesh, false)
    expect((mesh.material as THREE.MeshPhongMaterial).color.getHex()).toBe(0x123456)
  })

  it('does not lose the original colour when highlighted twice', () => {
    const mesh = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshPhongMaterial({ color: 0x123456 }),
    )
    setPatchHighlight(mesh, true)
    setPatchHighlight(mesh, true)
    setPatchHighlight(mesh, false)
    expect((mesh.material as THREE.MeshPhongMaterial).color.getHex()).toBe(0x123456)
  })

  it('leaves a never-highlighted mesh alone on toggle-off', () => {
    const mesh = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshPhongMaterial({ color: 0x123456 }),
    )
    setPatchHighlight(mesh, false)
    expect((mesh.material as THREE.MeshPhongMaterial).color.getHex()).toBe(0x123456)
  })

  it('is a no-op for a missing object', () => {
    expect(() => setPatchHighlight(null, true)).not.toThrow()
  })
})

describe('setEdgeHighlight', () => {
  it('paints and restores the wireframe colour', () => {
    const lines = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: 0x00ff00 }),
    )
    setEdgeHighlight(lines, true)
    expect((lines.material as THREE.LineBasicMaterial).color.getHex()).toBe(PATCH_HIGHLIGHT_COLOR)
    setEdgeHighlight(lines, false)
    expect((lines.material as THREE.LineBasicMaterial).color.getHex()).toBe(0x00ff00)
  })
})
