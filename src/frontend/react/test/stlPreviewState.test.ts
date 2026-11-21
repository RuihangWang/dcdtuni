import { describe, expect, it } from 'vitest'
import {
  allRegionsVisible,
  anyRegionVisible,
  emptyFlags,
  hashPreviewKey,
  isPreviewModelVisible,
  isPreviewRegionReversed,
  isPreviewRegionVisible,
  parseAsciiStlRegions,
  previewRegionColor,
  previewSummary,
  setModelVisible,
  setRegionReversed,
  setRegionVisible,
  syncFlagsFromModels,
} from '../src/io/stlPreviewState'

/**
 * The preview's job is to answer "what will the exporter write". So the parser
 * is tested against the exporter's own output shape — `solid <name>` blocks
 * with three vertices per facet — and the visibility rules against the cases
 * that decide whether a solid appears in the file at all.
 */

const FACET = `facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
`

function solid(name: string, facets = 1): string {
  return `solid ${name}\n${FACET.repeat(facets)}endsolid ${name}\n`
}

describe('parseAsciiStlRegions', () => {
  it('splits one geometry per solid block', () => {
    const regions = parseAsciiStlRegions(solid('rack_frame', 2) + solid('rack_front'))
    expect(regions.map((r) => r.name)).toEqual(['rack_frame', 'rack_front'])
    expect(regions[0]!.geometry.attributes.position!.count).toBe(6)
    expect(regions[1]!.geometry.attributes.position!.count).toBe(3)
  })

  it('refuses a binary STL rather than producing garbage regions', () => {
    // A NUL byte is the tell. The legacy checks the same thing, because a
    // binary file parsed line by line yields plausible-looking empty solids.
    expect(parseAsciiStlRegions(`solid x\0${FACET}endsolid x`)).toEqual([])
  })

  it('returns nothing for text that is not an STL', () => {
    expect(parseAsciiStlRegions('')).toEqual([])
    expect(parseAsciiStlRegions('solid x\nendsolid x\n')).toEqual([])
    expect(parseAsciiStlRegions('{"not": "an stl"}')).toEqual([])
  })

  it('drops a solid with fewer than three vertices', () => {
    // Half a triangle is not a region; keeping it would report a triangle
    // count of 0 for a solid the exporter never really wrote.
    const text = `solid partial
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
 endloop
endfacet
endsolid partial
${solid('whole')}`
    expect(parseAsciiStlRegions(text).map((r) => r.name)).toEqual(['whole'])
  })

  it('names an unnamed solid by its position', () => {
    expect(parseAsciiStlRegions(`solid\n${FACET}endsolid\n`)[0]!.name).toBe('solid_1')
  })

  it('computes a bounding box, which the region panel reads', () => {
    const box = parseAsciiStlRegions(solid('a'))[0]!.geometry.boundingBox
    expect(box).not.toBeNull()
    expect(box!.max.x).toBeCloseTo(1)
    expect(box!.max.y).toBeCloseTo(1)
  })
})

describe('region colours', () => {
  it('is stable for the same solid across rebuilds', () => {
    expect(previewRegionColor('m_1', 'wall_front', 0)).toBe(
      previewRegionColor('m_1', 'wall_front', 0),
    )
  })

  it('differs between solids of the same model', () => {
    expect(previewRegionColor('m_1', 'wall_front', 0)).not.toBe(
      previewRegionColor('m_1', 'wall_back', 1),
    )
  })

  it('is a hex string', () => {
    expect(previewRegionColor('m_1', 'a', 0)).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('hashes with FNV-1a, as the legacy does', () => {
    // Pinned so a "simplification" of the hash cannot silently recolour every
    // region in a project a user has learned to read.
    expect(hashPreviewKey('')).toBe(2166136261)
    expect(hashPreviewKey('a')).toBe(0xe40c292c)
  })
})

describe('visibility rules', () => {
  it('follows the model until an override is stored', () => {
    const flags = emptyFlags()
    expect(isPreviewModelVisible(flags, 'm_1', true)).toBe(true)
    expect(isPreviewModelVisible(flags, 'm_1', false)).toBe(false)
    flags.visibility.m_1 = true
    expect(isPreviewModelVisible(flags, 'm_1', false)).toBe(true)
  })

  it('hides a region of a hidden model that has no override of its own', () => {
    const flags = emptyFlags()
    flags.visibility.m_1 = false
    expect(isPreviewRegionVisible(flags, 'm_1', 'a')).toBe(false)
    flags.regionVisibility.m_1 = { a: true }
    expect(isPreviewRegionVisible(flags, 'm_1', 'a')).toBe(true)
  })

  it('shows every region by default', () => {
    expect(isPreviewRegionVisible(emptyFlags(), 'm_1', 'a')).toBe(true)
  })

  it('any/all fall back to the model when it has no regions', () => {
    const flags = emptyFlags()
    expect(anyRegionVisible(flags, 'm_1', [], false)).toBe(false)
    expect(allRegionsVisible(flags, 'm_1', [], true)).toBe(true)
  })

  it('any is true while one region is shown, all is not', () => {
    const flags = emptyFlags()
    setRegionVisible(flags, 'm_1', 'a', false, ['a', 'b'], true)
    expect(anyRegionVisible(flags, 'm_1', ['a', 'b'], true)).toBe(true)
    expect(allRegionsVisible(flags, 'm_1', ['a', 'b'], true)).toBe(false)
  })

  it('hiding one region leaves the others visible', () => {
    // The legacy drops the MODEL flag here, and every region without an
    // override of its own follows it down — so the whole object vanishes.
    const flags = emptyFlags()
    setRegionVisible(flags, 'm_1', 'a', false, ['a', 'b', 'c'], true)
    expect(isPreviewRegionVisible(flags, 'm_1', 'a')).toBe(false)
    expect(isPreviewRegionVisible(flags, 'm_1', 'b')).toBe(true)
    expect(isPreviewRegionVisible(flags, 'm_1', 'c')).toBe(true)
  })

  it('re-derives the model flag from its regions', () => {
    // The model checkbox has to mean "all of them", or it drifts out of step
    // with the boxes below it.
    const flags = emptyFlags()
    setRegionVisible(flags, 'm_1', 'a', false, ['a', 'b'], true)
    expect(flags.visibility.m_1).toBe(false)
    setRegionVisible(flags, 'm_1', 'a', true, ['a', 'b'], true)
    expect(flags.visibility.m_1).toBe(true)
  })

  it('the model toggle sets every region with it', () => {
    const flags = emptyFlags()
    setModelVisible(flags, 'm_1', ['a', 'b'], false)
    expect(flags.regionVisibility.m_1).toEqual({ a: false, b: false })
    expect(anyRegionVisible(flags, 'm_1', ['a', 'b'], true)).toBe(false)
  })
})

describe('reverse normals', () => {
  it('defaults to off and is per region', () => {
    const flags = emptyFlags()
    expect(isPreviewRegionReversed(flags, 'm_1', 'a')).toBe(false)
    setRegionReversed(flags, 'm_1', 'a', true)
    expect(isPreviewRegionReversed(flags, 'm_1', 'a')).toBe(true)
    expect(isPreviewRegionReversed(flags, 'm_1', 'b')).toBe(false)
    expect(isPreviewRegionReversed(flags, 'm_2', 'a')).toBe(false)
  })
})

describe('entering the mode', () => {
  it('seeds from the modelling view and drops stale region overrides', () => {
    // A region override made against the PREVIOUS preview may name a solid
    // that no longer exists, and would then hide a region nobody hid.
    const flags = emptyFlags()
    flags.regionVisibility.m_1 = { old_solid_name: false }
    syncFlagsFromModels(flags, [
      { uid: 'm_1', enabled: false },
      { uid: 'm_2', enabled: true },
    ])
    expect(flags.visibility).toEqual({ m_1: false, m_2: true })
    expect(flags.regionVisibility.m_1).toEqual({})
  })

  it('leaves the reverse flags alone', () => {
    // Those are not a view preference: they change the exported bytes, so
    // entering the mode must not silently un-flip a normal.
    const flags = emptyFlags()
    setRegionReversed(flags, 'm_1', 'a', true)
    syncFlagsFromModels(flags, [{ uid: 'm_1', enabled: true }])
    expect(isPreviewRegionReversed(flags, 'm_1', 'a')).toBe(true)
  })
})

describe('previewSummary', () => {
  it('totals regions and triangles across models', () => {
    const entry = (regions: number, triangles: number) => ({
      modelId: 'm',
      modelName: 'm',
      kind: 'box',
      stlText: '',
      triangleCount: triangles,
      regions: Array.from({ length: regions }, (_, i) => ({
        name: `r${i}`,
        geometry: null as never,
        triangleCount: 0,
        color: '#000000',
      })),
    })
    expect(previewSummary([entry(2, 24), entry(3, 36)])).toEqual({ regions: 5, triangles: 60 })
  })
})
