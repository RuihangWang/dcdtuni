import { afterEach, describe, expect, it } from 'vitest'
import { withWritableStateGlobal } from '../src/liquid/liquidRunGraph'

/**
 * The getter-only `window.state` is not hypothetical: `installLiquidScene`
 * defines it on every liquid-mode page, and the assignment inside the shared
 * compiler threw there — silently, because the click handler discarded the
 * promise. These tests pin the shape of the global the guard has to survive.
 */

const w = window as unknown as Record<string, unknown>

afterEach(() => {
  delete w.state
})

describe('withWritableStateGlobal', () => {
  it('lets an assignment through when the global is a getter with no setter', () => {
    const live = { name: 'live' }
    Object.defineProperty(w, 'state', { get: () => live, configurable: true })
    const seen = withWritableStateGlobal(() => {
      // This is the assignment liquid_graphgen's caller makes.
      w.state = { name: 'swapped' }
      return (w.state as { name: string }).name
    })
    expect(seen).toBe('swapped')
  })

  it('puts the getter back, even when the body throws', () => {
    const live = { name: 'live' }
    Object.defineProperty(w, 'state', { get: () => live, configurable: true })
    expect(() =>
      withWritableStateGlobal(() => {
        throw new Error('validation failed')
      }),
    ).toThrow('validation failed')
    const after = Object.getOwnPropertyDescriptor(w, 'state')
    expect(typeof after?.get).toBe('function')
    expect(w.state).toBe(live)
  })

  it('hands the body the value the getter was returning', () => {
    const live = { name: 'live' }
    Object.defineProperty(w, 'state', { get: () => live, configurable: true })
    expect(withWritableStateGlobal(() => w.state)).toBe(live)
  })

  it('leaves a plain data property alone', () => {
    w.state = { name: 'plain' }
    withWritableStateGlobal(() => undefined)
    expect(Object.getOwnPropertyDescriptor(w, 'state')?.value).toEqual({ name: 'plain' })
  })

  it('does not invent the global when there is none', () => {
    withWritableStateGlobal(() => undefined)
    expect('state' in w).toBe(false)
  })
})
