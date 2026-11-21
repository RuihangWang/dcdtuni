import { describe, expect, it } from 'vitest'
import { liquidRunOptions } from '../src/liquid/liquidPostprocess'

describe('liquidRunOptions', () => {
  it('lists each run by name and sends its path to the results endpoint', () => {
    expect(
      liquidRunOptions(
        [
          { name: '2026-08-27_11-06-17', path: '/case/L/Liquid/log/2026-08-27_11-06-17' },
          { name: '2026-08-26_09-00-00', path: '/case/L/Liquid/log/2026-08-26_09-00-00' },
        ],
        true,
        '(none)',
      ),
    ).toEqual([
      { value: '', label: 'Latest' },
      { value: '/case/L/Liquid/log/2026-08-27_11-06-17', label: '2026-08-27_11-06-17' },
      { value: '/case/L/Liquid/log/2026-08-26_09-00-00', label: '2026-08-26_09-00-00' },
    ])
  })

  it('offers the unqualified endpoint when a result is loaded but no run was scanned', () => {
    expect(liquidRunOptions([], true, '(none)')).toEqual([{ value: '', label: 'Latest' }])
  })

  it('keeps the current unqualified selection explicit beside completed records', () => {
    const options = liquidRunOptions(
      [null, {}, { name: 'completed', path: '/case/L/log/completed' }],
      true, '(none)', '最新结果',
    )
    expect(options).toEqual([
      { value: '', label: '最新结果' },
      { value: '/case/L/log/completed', label: 'completed' },
    ])
  })

  it('says so when nothing has been solved', () => {
    expect(liquidRunOptions(null, false, '(none)')).toEqual([{ value: '', label: '(none)' }])
  })
})
