import { describe, expect, it } from 'vitest'
import { display } from '../../src/components/NumberField'

/**
 * The field commits whatever it displays, so a display that rounds is a display
 * that CORRUPTS. These are the values the liquid-cooling panel puts through it.
 */
describe('NumberField display', () => {
  it('keeps millimetre resolution on metre-scale values', () => {
    expect(display(3.6000000000000005)).toBe('3.6')
    expect(display(0.0254)).toBe('0.0254')
    expect(display(12345)).toBe('12345')
  })

  it('does not annihilate small values', () => {
    // Coolant dynamic viscosity, water at 40 C — toFixed(4) gave 0.0007.
    expect(display(0.000653)).toBe('0.000653')
    // Newton convergence tolerance — toFixed(4) gave 0.
    expect(display(1e-6)).toBe('0.000001')
    // Momentum residual weight.
    expect(display(1e-6 * 1.5)).toBe('0.0000015')
  })

  it('round-trips every value it shows', () => {
    for (const v of [0.000653, 1e-6, 0.001002, 0.0018, 0.632, 4182, 992.2, 0]) {
      expect(Number(display(v))).toBe(v)
    }
  })

  it('handles zero and non-finite input', () => {
    expect(display(0)).toBe('0')
    expect(display(NaN)).toBe('')
    expect(display(Infinity)).toBe('')
  })
})

describe('NumberField optional value', () => {
  it('renders null as empty, not as 0', () => {
    // `firstCellSize: null` means "derive it". Showing 0 claims the user asked
    // for a zero-metre first cell.
    expect(display(null)).toBe('')
    expect(display(0)).toBe('0')
  })
})

describe('fixed-decimal display', () => {
  it('rounds only the LABEL, never the value', () => {
    // The colour-limit boxes read 37.73 while the shader keeps 37.7258. If the
    // rounded text were committed back, every focus-and-leave would nudge the
    // colour scale.
    expect(display(37.7258, 2)).toBe('37.73')
    expect(display(15, 2)).toBe('15.00')
  })

  it('keeps small magnitudes visible when no decimals are asked for', () => {
    // Without `decimals` the field must not collapse a 6.53e-4 viscosity.
    expect(Number(display(0.000653))).toBeCloseTo(0.000653, 9)
  })

  it('still renders an unset value as empty, decimals or not', () => {
    expect(display(null, 2)).toBe('')
  })
})
