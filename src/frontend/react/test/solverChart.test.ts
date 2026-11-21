import { describe, expect, it } from 'vitest'
import {
  legendLabel,
  residualTickLabel,
  residualYRange,
  seriesColor,
  stepRange,
  temperatureYRange,
  xTickStep,
  yTickStep,
} from '../src/run/solverChart'
import type { SeriesMap } from '../src/run/solverMonitorParse'

const RESIDUALS: SeriesMap = {
  Ux: [
    { step: 1, value: 1 },
    { step: 2, value: 1e-4 },
  ],
  T: [
    { step: 1, value: 0.5 },
    { step: 2, value: 1e-6 },
  ],
}

describe('stepRange', () => {
  it('spans every series, hidden ones included', () => {
    // Hiding a curve must not move the time axis under the ones still drawn —
    // the plot would appear to jump on a legend click.
    expect(stepRange(RESIDUALS, ['Ux', 'T'])).toEqual([1, 2])
  })

  it('widens a single-point range so the x scale is not a divide by zero', () => {
    expect(stepRange({ Ux: [{ step: 7, value: 1 }] }, ['Ux'])).toEqual([7, 8])
  })

  it('is null when nothing has arrived yet', () => {
    expect(stepRange({}, [])).toBeNull()
  })
})

describe('residualYRange', () => {
  it('keeps the default eight decades when the data fits inside them', () => {
    expect(residualYRange(RESIDUALS, ['Ux', 'T'], {})).toEqual([-9, 0])
  })

  it('grows downward for a residual below 1e-8', () => {
    const deep: SeriesMap = { Ux: [{ step: 1, value: 1e-11 }] }
    expect(residualYRange(deep, ['Ux'], {})).toEqual([-12, 0])
  })

  it('ignores a hidden series when picking the range', () => {
    const deep: SeriesMap = { Ux: [{ step: 1, value: 1e-11 }], T: [{ step: 1, value: 1e-2 }] }
    expect(residualYRange(deep, ['Ux', 'T'], { Ux: true })).toEqual([-9, 0])
  })

  it('never opens the axis above 1', () => {
    const big: SeriesMap = { Ux: [{ step: 1, value: 500 }] }
    expect(residualYRange(big, ['Ux'], {})[1]).toBe(0)
  })
})

describe('temperatureYRange', () => {
  it('converts kelvin to °C and pads by a tenth of the span', () => {
    const temps: SeriesMap = {
      a: [
        { step: 1, value: 293.15 },
        { step: 2, value: 303.15 },
      ],
    }
    expect(temperatureYRange(temps, ['a'], {})).toEqual([19, 31])
  })

  it('pads a flat series by half a degree, not by zero', () => {
    // A converged patch average moves by millikelvin; a purely proportional pad
    // would draw that wander at full height.
    const flat: SeriesMap = { a: [{ step: 1, value: 300 }, { step: 2, value: 300 }] }
    const [lo, hi] = temperatureYRange(flat, ['a'], {})
    expect(hi - lo).toBeCloseTo(1, 10)
  })

  it('falls back to 0..100 with no visible data', () => {
    expect(temperatureYRange({ a: [{ step: 1, value: 300 }] }, ['a'], { a: true })).toEqual([0, 100])
  })
})

describe('axis labels', () => {
  it('prints decades down to 1e-3 as plain decimals', () => {
    expect(residualTickLabel(0)).toBe('1')
    expect(residualTickLabel(-3)).toBe('0.001')
  })

  it('switches to exponents below 1e-3, where a decimal is unreadable', () => {
    expect(residualTickLabel(-7)).toBe('1e-7')
  })
})

describe('xTickStep', () => {
  it('rounds to 1/2/5 times a power of ten', () => {
    expect(xTickStep(500, 10)).toBe(50)
    expect(xTickStep(300, 10)).toBe(50)
    expect(xTickStep(100, 10)).toBe(10)
  })

  it('goes below 1 for a transient run measured in seconds', () => {
    // A fixed integer step would draw no ticks at all on a 0.01 s axis.
    expect(xTickStep(0.01, 5)).toBeCloseTo(0.002, 12)
  })

  it('never returns zero or a negative step', () => {
    expect(xTickStep(0, 10)).toBe(1)
    expect(xTickStep(-5, 10)).toBe(1)
  })
})

describe('yTickStep', () => {
  it('picks a decade and doubles it once when that decade gives over ten ticks', () => {
    // The legacy rule, ported as-is: ONE doubling pass, from the decade below
    // range/5. It is not a tick-count optimiser — a 4 K span still gets a 0.2
    // step — and changing it here would make the two apps' axes disagree.
    expect(yTickStep(50)).toBe(10)
    expect(yTickStep(12)).toBe(2)
    expect(yTickStep(4)).toBeCloseTo(0.2, 12)
  })
})

describe('series colours', () => {
  it('gives each residual field its fixed colour', () => {
    expect(seriesColor('residual', 'Ux', 0)).toBe('#e06c75')
    expect(seriesColor('residual', 'p_rgh', 3)).toBe('#56b6c2')
  })

  it('falls back to grey for a field the palette does not name', () => {
    expect(seriesColor('residual', 'omega', 0)).toBe('#ccc')
  })

  it('rotates the palette for temperature series', () => {
    expect(seriesColor('temperature', 'a', 0)).toBe(seriesColor('temperature', 'b', 10))
  })
})

describe('legendLabel', () => {
  it('strips the function-object prefix, leaving the patch a user recognises', () => {
    expect(legendLabel('avgT_ACU1_return')).toBe('ACU1_return')
    expect(legendLabel('Ux')).toBe('Ux')
  })
})

it('a zero residual does not stretch the log axis to 1e-16', () => {
  const data = { Ux: [{ x: 1, value: 0.5 }, { x: 2, value: 1e-3 }], Uz: [{ x: 1, value: 0 }, { x: 2, value: 2e-3 }] }
  expect(residualYRange(data as never, ['Ux', 'Uz'], {})).toEqual([-9, 0])
})
