import { describe, expect, it } from 'vitest'
import { boxSurfaceArea } from '../../src/components/BoxCfdSection'

describe('boxSurfaceArea', () => {
  it('is the full outer surface, all six faces', () => {
    // 1 x 2 x 3 -> 2(2 + 6 + 3) = 22
    expect(boxSurfaceArea({ x: 1, y: 2, z: 3 })).toBe(22)
  })

  it('is zero for a box with no size rather than NaN', () => {
    // The flux read-out divides by this; NaN W/m² tells the user nothing.
    expect(boxSurfaceArea(undefined)).toBe(0)
    expect(boxSurfaceArea({})).toBe(0)
  })

  it('grows as the square of a uniform scale', () => {
    expect(boxSurfaceArea({ x: 2, y: 2, z: 2 })).toBe(4 * boxSurfaceArea({ x: 1, y: 1, z: 1 }))
  })
})
