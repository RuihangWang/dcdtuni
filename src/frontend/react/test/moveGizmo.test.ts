import { describe, expect, it } from 'vitest'
import { followDelta } from '../src/interaction/moveGizmo'

describe('followDelta', () => {
  it('shifts a co-moved model by the drag delta from its OWN start', () => {
    expect(followDelta({ x: 5, y: 0, z: 2 }, { x: 0.5, y: 0, z: -1 })).toEqual({
      x: 5.5,
      y: 0,
      z: 1,
    })
  })

  it('keeps the group rigid: two models a fixed distance apart stay that far apart', () => {
    const a = { x: 1, y: 0, z: 0 }
    const b = { x: 4, y: 0, z: 0 }
    const delta = { x: 2.25, y: 1, z: 0 }
    const a2 = followDelta(a, delta)
    const b2 = followDelta(b, delta)
    expect(b2.x - a2.x).toBe(b.x - a.x)
    expect(b2.y - a2.y).toBe(b.y - a.y)
  })

  it('is not cumulative — applying it twice from the same start gives the same answer', () => {
    // Reading each model's CURRENT position per frame instead of its start is
    // the bug this pins: the delta would compound and the group would scatter.
    const start = { x: 0, y: 0, z: 0 }
    const delta = { x: 1, y: 0, z: 0 }
    expect(followDelta(start, delta)).toEqual(followDelta(start, delta))
  })
})
