import { describe, expect, it } from 'vitest'
import { applyClick, applyMany, minPoint } from '../src/interaction/selection'

const ORDER = ['a', 'b', 'c', 'd']

describe('applyClick', () => {
  it('replaces the selection on a plain click', () => {
    expect(applyClick(['a', 'b'], 'a', 'c', false, ORDER)).toEqual({ uids: ['c'], active: 'c' })
  })

  it('clears on a click that hit nothing', () => {
    expect(applyClick(['a'], 'a', null, false, ORDER)).toEqual({ uids: [], active: null })
  })

  it('promotes a single selection on the first modifier-click', () => {
    // The mistake this guards: dropping "a" and selecting only "b", which makes
    // ctrl-click feel like a plain click until the third model.
    expect(applyClick([], 'a', 'b', true, ORDER)).toEqual({ uids: ['a', 'b'], active: 'b' })
  })

  it('toggles a model out of an existing selection', () => {
    expect(applyClick(['a', 'b', 'c'], 'c', 'b', true, ORDER)).toEqual({
      uids: ['a', 'c'],
      active: 'c',
    })
  })

  it('orders by the PROJECT, not by click order', () => {
    // Batch duplicate/delete read this list; click order would make the same
    // three models produce different results run to run.
    expect(applyClick(['d'], 'd', 'b', true, ORDER).uids).toEqual(['b', 'd'])
  })

  it('keeps the clicked model as active when it stays selected', () => {
    expect(applyClick(['a'], 'a', 'd', true, ORDER).active).toBe('d')
  })

  it('falls back to the last remaining model when the clicked one was removed', () => {
    expect(applyClick(['a', 'b'], 'b', 'b', true, ORDER)).toEqual({ uids: ['a'], active: 'a' })
  })

  it('clears when the last model is toggled off', () => {
    expect(applyClick(['a'], 'a', 'a', true, ORDER)).toEqual({ uids: [], active: null })
  })

  it('drops uids the project no longer has', () => {
    expect(applyClick(['gone', 'a'], 'a', 'c', true, ORDER).uids).toEqual(['a', 'c'])
  })
})

describe('minPoint', () => {
  it('takes the smallest corner per axis', () => {
    expect(
      minPoint([
        { min: { x: 1, y: 5, z: 3 } },
        { min: { x: 4, y: 2, z: 9 } },
      ]),
    ).toEqual({ x: 1, y: 2, z: 3 })
  })

  it('returns the origin when nothing has bounds', () => {
    // Infinity would land in the project as a location and quietly destroy it.
    expect(minPoint([null, undefined])).toEqual({ x: 0, y: 0, z: 0 })
  })
})

/**
 * A set gesture in one step.
 *
 * The tree's root row, category rows, group folders and shift-ranges all select
 * a SET, and the host only offered one click at a time — so a set became a run
 * of single clicks: 287 store updates on a 287-model project, each re-deriving
 * the model order. `applyMany` replaces the run, and the contract is that it
 * produces exactly what the run produced.
 */
function foldClicks(
  current: string[],
  active: string | null,
  clicked: readonly string[],
  additive: boolean,
  order: string[],
  makeActive?: string | null,
) {
  const ordered =
    makeActive && clicked.includes(makeActive)
      ? [...clicked.filter((uid) => uid !== makeActive), makeActive]
      : [...clicked]
  let state = { uids: current, active }
  if (!additive) {
    state = applyClick(state.uids, state.active, ordered[0] ?? null, false, order)
    for (let i = 1; i < ordered.length; i++) {
      state = applyClick(state.uids, state.active, ordered[i]!, true, order)
    }
    return state
  }
  const had = new Set(current)
  for (const uid of ordered) if (!had.has(uid)) state = applyClick(state.uids, state.active, uid, true, order)
  return state
}

describe('applyMany', () => {
  const sets: [string[], string | null, string[], boolean, string | null][] = [
    [[], null, ORDER, false, null],
    [[], null, ORDER, true, null],
    [['a'], 'a', ['b', 'c', 'd'], false, null],
    [['a'], 'a', ['b', 'c', 'd'], true, null],
    [['a', 'b'], 'b', ['b', 'c'], true, null],
    [['a', 'b'], 'b', ['a', 'b'], true, null],
    [[], null, ['d', 'a'], false, 'a'],
    [['c'], 'c', ['a', 'b', 'c', 'd'], false, 'b'],
  ]

  it.each(sets)(
    'matches the run of single clicks it replaces (%j, %j, %j, %j, %j)',
    (current, active, clicked, additive, makeActive) => {
      expect(applyMany(current, active, clicked, additive, ORDER, makeActive)).toEqual(
        foldClicks(current, active, clicked, additive, ORDER, makeActive),
      )
    },
  )

  it('selects 287 models in ONE update, in project order', () => {
    const order = Array.from({ length: 287 }, (_, i) => `m_${i}`)
    const reversed = [...order].reverse()
    const out = applyMany([], null, reversed, false, order)
    expect(out.uids).toEqual(order)
    expect(out.active).toBe('m_0')
  })

  it('drops a uid the project no longer has', () => {
    // The one place the two differ, and deliberately: `applyClick` takes a
    // plain click on trust, because a single click can only come from a row the
    // tree drew. A SET can name a model that was deleted between the gesture
    // and the update, and selecting that would put a ghost in the inspector.
    expect(applyMany([], null, ['a', 'gone', 'c'], false, ORDER).uids).toEqual(['a', 'c'])
  })

  it('clears when nothing is left', () => {
    expect(applyMany([], null, [], false, ORDER)).toEqual({ uids: [], active: null })
    // An additive gesture over models already selected adds nothing, so it
    // changes nothing — it does NOT toggle them off.
    expect(applyMany(['a'], 'a', ['a'], true, ORDER)).toEqual({ uids: ['a'], active: 'a' })
  })
})
