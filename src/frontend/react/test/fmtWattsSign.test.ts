import { expect, it } from 'vitest'
import { fmtWatts } from '../src/liquid/liquidDisplayFormat'

it('prints a value that rounds to zero without a minus sign', () => {
  expect(fmtWatts(-0.004)).toBe('0.0 W')
  expect(fmtWatts(-0)).toBe('0.0 W')
  expect(fmtWatts(-0.06)).toBe('-0.1 W')
  expect(fmtWatts(-12000)).toBe('-12.00 kW')
})
