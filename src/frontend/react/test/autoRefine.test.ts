import { describe, expect, it } from 'vitest'
import { minSafeLevel } from '../src/geometry/autoRefine'

describe('minSafeLevel', () => {
  it('puts at least two cells across the feature', () => {
    // 0.34 m base, 40 mm wall: needs cells <= 20 mm, i.e. 0.34/2^n <= 0.02 -> n = 5.
    expect(minSafeLevel(0.34, 0.04)).toEqual([4, 5])
  })

  it('needs no refinement when the base cell is already fine enough', () => {
    expect(minSafeLevel(0.01, 1)).toEqual([0, 0])
  })

  it('keeps min one level below max, floored at zero', () => {
    const [min, max] = minSafeLevel(0.34, 0.2)
    expect(max - min).toBeLessThanOrEqual(1)
    expect(min).toBeGreaterThanOrEqual(0)
  })

  it('returns zeros for nonsense input rather than -Infinity', () => {
    // log2(x/0) is Infinity, and Math.ceil of that lands in the project as a
    // refinement level the runner would try to honour.
    expect(minSafeLevel(0.34, 0)).toEqual([0, 0])
    expect(minSafeLevel(0, 0.04)).toEqual([0, 0])
  })

  it('rounds UP: a level that is half a step short resolves nothing', () => {
    // 0.3/0.05 = 6 -> log2 = 2.58 -> 3, not 2.
    expect(minSafeLevel(0.3, 0.1)).toEqual([2, 3])
  })
})
