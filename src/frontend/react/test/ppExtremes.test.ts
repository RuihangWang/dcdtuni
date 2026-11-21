import { describe, expect, it } from 'vitest'
import { extremePoints } from '../src/postprocess/ppExtremes'

describe('ppExtremes extremePoints', () => {
  const pos = new Float32Array([0, 0, 0, 1, 0, 0, 2, 0, 0, 3, 0, 0])
  const val = new Float32Array([5, NaN, -2, 9])
  it('finds min and max over all finite points', () => {
    const p = extremePoints(pos, val)!
    expect(p.min).toEqual({ value: -2, position: [2, 0, 0] })
    expect(p.max).toEqual({ value: 9, position: [3, 0, 0] })
  })
  it('only over the points the visible triangles use', () => {
    const p = extremePoints(pos, val, new Uint32Array([0, 1, 2]))!
    expect(p.max.value).toBe(5)
    expect(p.min.value).toBe(-2)
  })
  it('null when nothing is finite', () => {
    expect(extremePoints(pos, new Float32Array([NaN, NaN, NaN, NaN]))).toBeNull()
  })
})
