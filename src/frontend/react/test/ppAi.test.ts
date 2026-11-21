import { describe, expect, it } from 'vitest'
import { normaliseBox } from '../src/postprocess/ppAi'

describe('normaliseBox', () => {
  it('orders inverted bounds instead of passing them on', () => {
    // Dragging a bound past its partner is easy in six number boxes, and the
    // server reads an inverted box as an EMPTY region — an inference that
    // returns nothing and reads as a model failure.
    expect(normaliseBox({ min: [3, 0, 5], max: [1, 2, 2] })).toEqual({
      min: [1, 0, 2],
      max: [3, 2, 5],
    })
  })

  it('leaves an already-ordered box alone', () => {
    const box = { min: [0, 0, 0] as [number, number, number], max: [1, 2, 3] as [number, number, number] }
    expect(normaliseBox(box)).toEqual(box)
  })
})
