import { describe, expect, it } from 'vitest'
import {
  COLOR_MAPS,
  colormapCss,
  colormapData,
  colormapGradient,
  type ColorMap,
} from '../src/postprocess/ppColormaps'

/**
 * The lookup tables, checked against the values the legacy editor generates.
 *
 * These are the numbers a colour actually comes from, so a drift here is a
 * picture that disagrees with the other app while looking perfectly reasonable
 * on its own — which no screenshot comparison would catch.
 */
describe('colormapData', () => {
  it('offers exactly the three presets the legacy panel does', () => {
    expect(COLOR_MAPS.map((c) => c.id)).toEqual(['coolwarm', 'jet', 'viridis'])
  })

  it('fills 256 RGBA entries with a fully opaque alpha', () => {
    for (const { id } of COLOR_MAPS) {
      const d = colormapData(id)
      expect(d.length).toBe(1024)
      for (let i = 0; i < 256; i++) expect(d[i * 4 + 3]).toBe(255)
    }
  })

  it('starts coolwarm at the ParaView blue and ends at its red', () => {
    // 59,76,192 → 180,4,38, the two endpoints of the Cool-to-Warm ramp.
    const d = colormapData('coolwarm')
    expect([d[0], d[1], d[2]]).toEqual([59, 76, 192])
    expect([d[255 * 4], d[255 * 4 + 1], d[255 * 4 + 2]]).toEqual([180, 4, 38])
  })

  it('passes through white at the middle of coolwarm', () => {
    // The midpoint is what makes it diverging. A ramp that goes blue→red
    // through purple reads as a continuous scale with no neutral.
    const d = colormapData('coolwarm')
    const mid = 128 * 4
    expect(d[mid]).toBeGreaterThan(240)
    expect(d[mid + 1]).toBeGreaterThan(240)
    expect(d[mid + 2]).toBeGreaterThan(240)
  })

  it('starts viridis dark purple and ends yellow', () => {
    const d = colormapData('viridis')
    expect([d[0], d[1], d[2]]).toEqual([68, 1, 84])
    expect([d[255 * 4], d[255 * 4 + 1], d[255 * 4 + 2]]).toEqual([253, 231, 37])
  })

  it('starts jet in blue and ends in red', () => {
    const d = colormapData('jet')
    expect(d[2]).toBeGreaterThan(d[0] as number)
    expect(d[255 * 4]).toBeGreaterThan(d[255 * 4 + 2] as number)
  })

  it('never jumps between neighbouring entries', () => {
    // A discontinuity means the stop search picked the wrong segment, which
    // shows as a hard band across an otherwise smooth field.
    for (const { id } of COLOR_MAPS) {
      const d = colormapData(id)
      for (let i = 1; i < 256; i++) {
        for (let c = 0; c < 3; c++) {
          const step = Math.abs((d[i * 4 + c] as number) - (d[(i - 1) * 4 + c] as number))
          expect(step).toBeLessThan(12)
        }
      }
    }
  })

  it('falls back to coolwarm for an unknown preset rather than throwing', () => {
    expect(Array.from(colormapData('nope' as ColorMap))).toEqual(
      Array.from(colormapData('coolwarm')),
    )
  })
})

describe('colormapCss', () => {
  it('reads the same table the shader samples', () => {
    const d = colormapData('viridis')
    expect(colormapCss('viridis', 0)).toBe(`rgb(${d[0]}, ${d[1]}, ${d[2]})`)
  })

  it('CLAMPS out-of-range values instead of wrapping', () => {
    // Wrapping would paint the hottest cell in the coldest colour — a legend
    // that looks fine and is exactly backwards.
    expect(colormapCss('jet', 1.7)).toBe(colormapCss('jet', 1))
    expect(colormapCss('jet', -3)).toBe(colormapCss('jet', 0))
  })

  it('survives NaN', () => {
    expect(colormapCss('jet', NaN)).toBe(colormapCss('jet', 0))
  })

  it('builds a gradient that runs low at the bottom', () => {
    // `to top`: a vertical colorbar puts the maximum at the top, and a flipped
    // gradient mislabels every value on it.
    const g = colormapGradient('coolwarm', 3)
    expect(g.startsWith('linear-gradient(to top,')).toBe(true)
    expect(g).toContain(colormapCss('coolwarm', 0))
    expect(g).toContain(colormapCss('coolwarm', 1))
  })
})
