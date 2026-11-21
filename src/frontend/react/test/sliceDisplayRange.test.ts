import { describe, expect, it } from 'vitest'
import { sliceDisplayRange } from '../src/postprocess'
import type { SlicePayload } from '../src/postprocess'

/**
 * A freshly cut slice opens on a ROBUST colour range.
 *
 * The header's `scalarRange` is the raw min and max of the cut. One cell
 * against a hot server, or one near-zero cell in a velocity field, stretches
 * the ramp so far that every other cell lands in the same bucket and the plane
 * renders as a single flat colour — which reads as "the slice is empty" or "the
 * field is uniform", neither of which is true. The legacy takes a 2–98%
 * percentile of the slice's own scalars for the default clim, the 值域范围
 * boxes and the colorbar's 自动缩放 (`_ppPercentileRange`,
 * STL_generate.html:22943).
 */

function payload(values: number[] | null, scalarRange: [number, number]): SlicePayload {
  return {
    positions: new Float32Array(0),
    indices: new Uint32Array(0),
    values: values === null ? null : new Float32Array(values),
    scalarRange,
    field: 'T',
    nPoints: values?.length ?? 0,
    nTriangles: 1,
  }
}

describe('sliceDisplayRange', () => {
  it('ignores a single hot outlier', () => {
    // 99 cells around room temperature and one cell against a chip.
    const values = Array.from({ length: 99 }, (_, i) => 295 + i * 0.05).concat([400])
    const [lo, hi] = sliceDisplayRange(payload(values, [295, 400]))

    expect(hi).toBeLessThan(320)
    expect(lo).toBeGreaterThanOrEqual(295)
    // The raw range would have been 105 K wide; the robust one is the spread
    // the room actually has.
    expect(hi - lo).toBeLessThan(10)
  })

  it('is the raw range when the data has no outliers', () => {
    const values = Array.from({ length: 101 }, (_, i) => 300 + i * 0.1)
    const [lo, hi] = sliceDisplayRange(payload(values, [300, 310]))
    expect(lo).toBeCloseTo(300.2, 3)
    expect(hi).toBeCloseTo(309.8, 3)
  })

  it('falls back to the header range for a slice with no scalars', () => {
    // An empty cut writes a zero-length array rather than omitting the section.
    expect(sliceDisplayRange(payload([], [12, 34]))).toEqual([12, 34])
    expect(sliceDisplayRange(payload(null, [12, 34]))).toEqual([12, 34])
  })

  it('keeps an equal data range finite and nondecreasing for flat LUT rendering', () => {
    const [lo, hi] = sliceDisplayRange(payload([7, 7, 7, 7], [7, 7]))
    expect([lo, hi]).toEqual([7, 7])
    expect([lo, hi].every(Number.isFinite)).toBe(true)
    expect(hi).toBeGreaterThanOrEqual(lo)
  })

  it('preserves a tiny but nonflat float32 range without epsilon classification', () => {
    // One float32 ULP at 7: numerical data is distinct despite its small width.
    const high = 7 + 2 ** -21
    const [lo, hi] = sliceDisplayRange(payload([7, high, 7, high], [7, high]))
    expect([lo, hi]).toEqual([7, high])
    expect([lo, hi].every(Number.isFinite)).toBe(true)
    expect(hi).toBeGreaterThan(lo)
  })

  it('drops NaNs rather than poisoning the range', () => {
    const values = [300, 301, Number.NaN, 302, 303]
    const [lo, hi] = sliceDisplayRange(payload(values, [300, 303]))
    expect(Number.isFinite(lo)).toBe(true)
    expect(Number.isFinite(hi)).toBe(true)
  })
})
