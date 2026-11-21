import { describe, expect, it, vi } from 'vitest'
import { dispatchPointerDown, type PointerDownChain } from '../src/interaction/viewportPointerChain'

/**
 * Who gets the pointer first, and who is not asked once someone has it.
 *
 * Everything in this chain is drawn with `depthTest` off, ON TOP of the thing
 * it belongs to, so the order is not cosmetic: whoever is asked first takes
 * every press aimed at the helpers behind it. The legacy dispatcher fixes the
 * order (STL_generate.html:18204-18211) — valve drag, then the seed hub, then
 * the rotate rings, then the resize handles — and the valve step is the one
 * React never had, which is why a liquid valve could not be dragged to a rack
 * manifold at all.
 *
 * Each step is a thunk because asking is not free: two of them raycast the
 * scene, and a chain that evaluates all four to pick one would pay for four.
 */

function chain(over: Partial<PointerDownChain> = {}): PointerDownChain {
  return {
    valveDrag: vi.fn(() => false),
    seedDrag: vi.fn(() => false),
    rotateGizmo: vi.fn(() => false),
    faceDrag: vi.fn(() => false),
    ...over,
  }
}

describe('dispatchPointerDown', () => {
  it('asks the valve first, as the legacy does', () => {
    const c = chain({ valveDrag: vi.fn(() => true) })
    expect(dispatchPointerDown(c)).toBe('valve')
    expect(c.seedDrag).not.toHaveBeenCalled()
    expect(c.rotateGizmo).not.toHaveBeenCalled()
    expect(c.faceDrag).not.toHaveBeenCalled()
  })

  it('falls through to the seed hub', () => {
    const c = chain({ seedDrag: vi.fn(() => true) })
    expect(dispatchPointerDown(c)).toBe('seed')
    expect(c.valveDrag).toHaveBeenCalledOnce()
    expect(c.rotateGizmo).not.toHaveBeenCalled()
  })

  it('then the rotate rings', () => {
    const c = chain({ rotateGizmo: vi.fn(() => true) })
    expect(dispatchPointerDown(c)).toBe('rotate')
    expect(c.faceDrag).not.toHaveBeenCalled()
  })

  it('then the resize handles', () => {
    const c = chain({ faceDrag: vi.fn(() => true) })
    expect(dispatchPointerDown(c)).toBe('face')
  })

  it('says so when nobody wanted it — that press is a selection click', () => {
    const c = chain()
    expect(dispatchPointerDown(c)).toBeNull()
    for (const step of [c.valveDrag, c.seedDrag, c.rotateGizmo, c.faceDrag]) {
      expect(step).toHaveBeenCalledOnce()
    }
  })

  it('works with no valve step at all — liquid mode is off most of the time', () => {
    const c = chain({ valveDrag: undefined })
    expect(dispatchPointerDown(c)).toBeNull()
    expect(c.seedDrag).toHaveBeenCalledOnce()
  })
})
