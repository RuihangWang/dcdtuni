import { describe, expect, it } from 'vitest'
import {
  base64ToBuffer,
  bufferToBase64,
  parseAsciiStlRegions,
  parseStlAsset,
  restoreGeometryCache,
  stlOf,
} from '../src/io/stlAssets'
import type { CfdProject, Construction } from '../src/schema/cfdproj'

const TRI = (name: string, z = 0) => `solid ${name}
facet normal 0 0 1
  outer loop
    vertex 0 0 ${z}
    vertex 1 0 ${z}
    vertex 0 1 ${z}
  endloop
endfacet
endsolid ${name}
`

describe('parseAsciiStlRegions', () => {
  it('returns one region per solid block, in file order', () => {
    const regions = parseAsciiStlRegions(TRI('inlet') + TRI('outlet', 1))
    expect(regions.map((r) => r.name)).toEqual(['inlet', 'outlet'])
  })

  it('gives an unnamed solid a positional name', () => {
    expect(parseAsciiStlRegions(TRI(''))[0]!.name).toBe('solid_1')
  })

  it('drops a solid with fewer than three vertices', () => {
    // A stray header is not a region; keeping it would add an empty OpenFOAM
    // patch to the export.
    const stray = 'solid empty\nendsolid empty\n' + TRI('real')
    expect(parseAsciiStlRegions(stray).map((r) => r.name)).toEqual(['real'])
  })

  it('returns nothing for a binary STL', () => {
    // Binary STLs cannot carry solid names at all, and the NUL byte is what
    // separates them from text.
    expect(parseAsciiStlRegions('solid x\0\0facet normal\nendsolid x')).toEqual([])
  })

  it('returns nothing for text that is not an STL', () => {
    expect(parseAsciiStlRegions('hello')).toEqual([])
  })
})

describe('base64 round trip', () => {
  it('survives bytes above 0x7f', () => {
    const bytes = new Uint8Array([0, 1, 127, 128, 255, 42])
    const back = new Uint8Array(base64ToBuffer(bufferToBase64(bytes.buffer)))
    expect(Array.from(back)).toEqual(Array.from(bytes))
  })

  it('handles a buffer larger than the argument limit', () => {
    // `String.fromCharCode(...bytes)` throws RangeError well below this.
    const big = new Uint8Array(200_000).fill(7)
    expect(base64ToBuffer(bufferToBase64(big.buffer)).byteLength).toBe(200_000)
  })
})

describe('parseStlAsset', () => {
  it('parses geometry and regions from one ASCII file', () => {
    const text = TRI('a') + TRI('b', 2)
    const buffer = new TextEncoder().encode(text).buffer
    const asset = parseStlAsset(buffer, text)
    expect(asset.regions.map((r) => r.name)).toEqual(['a', 'b'])
    expect(asset.baseGeometry.boundingBox).not.toBeNull()
  })
})

function customModel(uid: string, stl: string | null): Construction {
  return {
    uid,
    kind: 'custom',
    meta: { name: uid },
    geometry: {},
    customMesh: { stlBase64: stl },
  } as unknown as Construction
}

const PROJECT = {} as CfdProject

describe('restoreGeometryCache', () => {
  const stl = bufferToBase64(new TextEncoder().encode(TRI('r1')).buffer)

  it('parses a custom model into the cache', () => {
    const cache = new Map<string, unknown>()
    restoreGeometryCache(PROJECT, cache, [customModel('m1', stl)])
    expect(cache.has('m1')).toBe(true)
  })

  it('reuses the existing parse when the stored STL is unchanged', () => {
    const cache = new Map<string, unknown>()
    const models = [customModel('m1', stl)]
    restoreGeometryCache(PROJECT, cache, models)
    const first = cache.get('m1')
    restoreGeometryCache(PROJECT, cache, models)
    // Same object, not an equal one: re-parsing megabytes of triangles on every
    // undo is the cost this check exists to avoid.
    expect(cache.get('m1')).toBe(first)
  })

  it('re-parses when the STL was replaced', () => {
    const cache = new Map<string, unknown>()
    restoreGeometryCache(PROJECT, cache, [customModel('m1', stl)])
    const first = cache.get('m1')
    const other = bufferToBase64(new TextEncoder().encode(TRI('r2', 5)).buffer)
    restoreGeometryCache(PROJECT, cache, [customModel('m1', other)])
    expect(cache.get('m1')).not.toBe(first)
  })

  it('drops entries for models that are gone', () => {
    const cache = new Map<string, unknown>()
    restoreGeometryCache(PROJECT, cache, [customModel('m1', stl)])
    restoreGeometryCache(PROJECT, cache, [])
    expect(cache.size).toBe(0)
  })

  it('ignores non-custom models and custom models with no mesh', () => {
    const cache = new Map<string, unknown>()
    const box = { uid: 'b1', kind: 'box', meta: {}, geometry: {} } as unknown as Construction
    restoreGeometryCache(PROJECT, cache, [box, customModel('m2', null)])
    expect(cache.size).toBe(0)
  })

  it('survives an unparseable stored mesh', () => {
    const cache = new Map<string, unknown>()
    restoreGeometryCache(PROJECT, cache, [customModel('bad', 'not-base64-@@@')])
    expect(cache.has('bad')).toBe(false)
  })
})

describe('stlOf', () => {
  it('reads the V4 home first, then the flat legacy field', () => {
    expect(stlOf(customModel('m', 'AAA'))).toBe('AAA')
    const flat = { uid: 'm', kind: 'custom', customSTLBase64: 'BBB' } as unknown as Construction
    expect(stlOf(flat)).toBe('BBB')
  })
})
