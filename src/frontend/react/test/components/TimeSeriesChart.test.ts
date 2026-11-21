import { describe, expect, it } from 'vitest'

/**
 * The x-alignment contract, tested on the data shape rather than the pixels.
 *
 * The chart plots `points[i]` against `time[i]`. A caller that DROPS a missing
 * sample instead of passing NaN shifts every later point one position left and
 * draws a curve that never happened — and the result looks plausible, which is
 * why it needs a test rather than an eye.
 */
function alignedAgainst(time: number[], points: number[]): [number, number][] {
  const n = Math.min(time.length, points.length)
  const out: [number, number][] = []
  for (let i = 0; i < n; i++) {
    const v = points[i] as number
    if (Number.isFinite(v)) out.push([time[i] as number, v])
  }
  return out
}

describe('time-series x alignment', () => {
  it('keeps every sample on its own x when gaps are NaN', () => {
    const time = [0, 1, 2, 3, 4]
    const withGap = [10, NaN, 12, NaN, 14]
    expect(alignedAgainst(time, withGap)).toEqual([
      [0, 10],
      [2, 12],
      [4, 14],
    ])
  })

  it('shows what dropping instead of NaN would have done', () => {
    // The bug this guards: filtering first, then plotting against time[i].
    const time = [0, 1, 2, 3, 4]
    const dropped = [10, 12, 14] // what .filter(isFinite) produces
    const wrong = dropped.map((v, i) => [time[i], v])
    expect(wrong).toEqual([
      [0, 10],
      [1, 12], // should be x=2
      [2, 14], // should be x=4
    ])
    expect(wrong).not.toEqual(alignedAgainst(time, [10, NaN, 12, NaN, 14]))
  })

  it('tolerates a series shorter than time (a stopped run)', () => {
    expect(alignedAgainst([0, 1, 2, 3], [5, 6])).toEqual([
      [0, 5],
      [1, 6],
    ])
  })

  it('yields nothing when every sample is missing', () => {
    expect(alignedAgainst([0, 1, 2], [NaN, NaN, NaN])).toEqual([])
  })
})
