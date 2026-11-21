import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_BINDINGS,
  SHORTCUT_ACTIONS,
  SHORTCUTS_STORAGE_KEY,
  captureBinding,
  findConflict,
  formatBinding,
  formatBindings,
  getShortcutBindings,
  matchShortcut,
  normalizeKey,
  parseStoredBindings,
  reloadShortcuts,
  resetShortcutBindings,
  resolveBindings,
  setShortcutBinding,
  subscribeShortcuts,
  swapShortcutBinding,
} from '../src/interaction/shortcuts'

/**
 * The shortcut registry: what each key does, the defaults, and the user's own
 * bindings.
 *
 * The keyboard pipeline (`editorKeys`) asks THIS module what a key means, so
 * the settings dialog and the handler cannot disagree about a binding.
 */

beforeEach(() => {
  localStorage.clear()
  reloadShortcuts()
})

afterEach(() => {
  localStorage.clear()
  reloadShortcuts()
  vi.restoreAllMocks()
})

describe('the registry', () => {
  it('lists move, rotate, delete, undo and redo, with the asked-for defaults', () => {
    expect(SHORTCUT_ACTIONS.map((a) => a.id)).toEqual([
      'moveGizmo',
      'rotateGizmo',
      'delete',
      'undo',
      'redo',
    ])
    expect(DEFAULT_BINDINGS.moveGizmo).toEqual(['v'])
    expect(DEFAULT_BINDINGS.rotateGizmo).toEqual(['r'])
    expect(DEFAULT_BINDINGS.delete).toEqual(['Delete', 'Backspace'])
    expect(DEFAULT_BINDINGS.undo).toEqual(['Mod+z'])
    expect(DEFAULT_BINDINGS.redo).toEqual(['Mod+y', 'Mod+Shift+z'])
  })

  it('lets the single keys be rebound and keeps the Ctrl chords fixed', () => {
    const rebindable = Object.fromEntries(SHORTCUT_ACTIONS.map((a) => [a.id, a.rebindable]))
    expect(rebindable).toEqual({
      moveGizmo: true,
      rotateGizmo: true,
      delete: true,
      undo: false,
      redo: false,
    })
  })

  it('starts on the defaults', () => {
    expect(getShortcutBindings()).toEqual(DEFAULT_BINDINGS)
  })
})

describe('normalizeKey', () => {
  it('lower-cases a letter, so Caps Lock and Shift do not change which key it is', () => {
    expect(normalizeKey('R')).toBe('r')
    expect(normalizeKey('v')).toBe('v')
  })

  it('treats a missing or empty key as no shortcut instead of throwing', () => {
    // Stage 5 browser E2E: document-level synthetic events with `key` unset
    // crashed the global shortcut pass (production TypeError). The empty
    // string matches no binding, so such events are inert.
    expect(normalizeKey(undefined as unknown as string)).toBe('')
    expect(normalizeKey('')).toBe('')
    // the real document-handler path: an event whose key was never set is inert
    expect(matchShortcut({ key: undefined } as never, DEFAULT_BINDINGS)).toBeNull()
    expect(matchShortcut({ key: '' } as never, DEFAULT_BINDINGS)).toBeNull()
  })

  it('reads the physical key when an IME or a non-Latin layout hides the letter', () => {
    expect(normalizeKey('Process', 'KeyV')).toBe('v')
    expect(normalizeKey('к', 'KeyR')).toBe('r')
    expect(normalizeKey('Unidentified', 'Digit3')).toBe('3')
  })

  it('leaves named keys alone', () => {
    expect(normalizeKey('Delete')).toBe('Delete')
    expect(normalizeKey('Backspace')).toBe('Backspace')
  })
})

describe('matchShortcut', () => {
  const b = DEFAULT_BINDINGS

  it('matches a plain key with no Ctrl, Cmd or Alt', () => {
    expect(matchShortcut({ key: 'v' }, b)).toBe('moveGizmo')
    expect(matchShortcut({ key: 'r' }, b)).toBe('rotateGizmo')
    expect(matchShortcut({ key: 'R', shiftKey: true }, b)).toBe('rotateGizmo')
    expect(matchShortcut({ key: 'v', ctrlKey: true }, b)).toBe(null)
    expect(matchShortcut({ key: 'r', metaKey: true }, b)).toBe(null)
    expect(matchShortcut({ key: 'v', altKey: true }, b)).toBe(null)
  })

  it('matches the chords with the modifier held and Shift exactly as written', () => {
    expect(matchShortcut({ key: 'z', ctrlKey: true }, b)).toBe('undo')
    expect(matchShortcut({ key: 'z', metaKey: true }, b)).toBe('undo')
    expect(matchShortcut({ key: 'Z', ctrlKey: true, shiftKey: true }, b)).toBe('redo')
    expect(matchShortcut({ key: 'y', ctrlKey: true }, b)).toBe('redo')
    expect(matchShortcut({ key: 'z' }, b)).toBe(null)
  })

  it('never turns an AltGr character into undo or redo', () => {
    // Windows reports AltGr as Ctrl+Alt. Polish AltGr+Z types 'ż' and
    // AltGr+Shift+Z 'Ż': characters, not chords. The physical-key fallback
    // must not read them as KeyZ.
    expect(matchShortcut({ key: 'ż', code: 'KeyZ', ctrlKey: true, altKey: true }, b)).toBe(null)
    expect(
      matchShortcut({ key: 'Ż', code: 'KeyZ', ctrlKey: true, altKey: true, shiftKey: true }, b),
    ).toBe(null)
    expect(matchShortcut({ key: 'ý', code: 'KeyY', ctrlKey: true, altKey: true }, b)).toBe(null)
    // Ctrl+Alt+Z on a Latin layout is still undo, as it always was.
    expect(matchShortcut({ key: 'z', code: 'KeyZ', ctrlKey: true, altKey: true }, b)).toBe('undo')
    // A non-Latin layout without AltGr still reads the physical key.
    expect(matchShortcut({ key: 'я', code: 'KeyZ', ctrlKey: true }, b)).toBe('undo')
  })

  it('does not repeat a toggle while the key is held, but does repeat undo', () => {
    expect(matchShortcut({ key: 'v', repeat: true }, b)).toBe(null)
    expect(matchShortcut({ key: 'z', ctrlKey: true, repeat: true }, b)).toBe('undo')
  })

  it('follows a rebinding', () => {
    const next = { ...b, moveGizmo: ['m'] }
    expect(matchShortcut({ key: 'm' }, next)).toBe('moveGizmo')
    expect(matchShortcut({ key: 'v' }, next)).toBe(null)
  })
})

describe('captureBinding', () => {
  it('takes a letter, a digit, punctuation or an editing key', () => {
    expect(captureBinding({ key: 'M' })).toEqual({ kind: 'key', key: 'm' })
    expect(captureBinding({ key: '7' })).toEqual({ kind: 'key', key: '7' })
    expect(captureBinding({ key: '/' })).toEqual({ kind: 'key', key: '/' })
    expect(captureBinding({ key: 'Delete' })).toEqual({ kind: 'key', key: 'Delete' })
    expect(captureBinding({ key: 'Process', code: 'KeyG' })).toEqual({ kind: 'key', key: 'g' })
  })

  it('keeps waiting while only a modifier is down', () => {
    for (const key of ['Shift', 'Control', 'Alt', 'Meta', 'CapsLock']) {
      expect(captureBinding({ key }), key).toEqual({ kind: 'wait' })
    }
  })

  it('refuses a combination — Ctrl+R reloads the page, Ctrl+V pastes', () => {
    expect(captureBinding({ key: 'r', ctrlKey: true })).toEqual({
      kind: 'error',
      reason: 'modifier',
      key: 'r',
    })
    expect(captureBinding({ key: 'v', metaKey: true }).kind).toBe('error')
    expect(captureBinding({ key: 'v', altKey: true }).kind).toBe('error')
  })

  it('refuses keys that already mean something to the page', () => {
    for (const key of ['Tab', 'Enter', ' ', 'F5', 'ArrowUp', 'Escape']) {
      expect(captureBinding({ key }), key).toMatchObject({ kind: 'error', reason: 'reserved' })
    }
  })
})

describe('formatBinding', () => {
  it('prints what is on the keycap', () => {
    expect(formatBinding('v')).toBe('V')
    expect(formatBinding('Delete')).toBe('Delete')
    expect(formatBinding('Mod+z')).toBe('Ctrl+Z')
    expect(formatBinding('Mod+Shift+z')).toBe('Ctrl+Shift+Z')
    expect(formatBinding(' ')).toBe('Space')
    expect(formatBindings(['Delete', 'Backspace'])).toBe('Delete / Backspace')
  })
})

describe('conflicts', () => {
  it('names the action that already owns a key', () => {
    const b = getShortcutBindings()
    expect(findConflict(b, 'moveGizmo', 'r')).toBe('rotateGizmo')
    expect(findConflict(b, 'moveGizmo', 'Backspace')).toBe('delete')
    expect(findConflict(b, 'moveGizmo', 'v')).toBe(null)
    expect(findConflict(b, 'moveGizmo', 'm')).toBe(null)
  })

  it('refuses a plain set that would give one key two meanings', () => {
    expect(setShortcutBinding('moveGizmo', ['r'])).toBe(false)
    expect(getShortcutBindings().moveGizmo).toEqual(['v'])
  })

  it('swaps: the key moves over, and the other action gets the old one', () => {
    expect(swapShortcutBinding('moveGizmo', 'r', 'rotateGizmo')).toBe(true)
    expect(getShortcutBindings().moveGizmo).toEqual(['r'])
    expect(getShortcutBindings().rotateGizmo).toEqual(['v'])
  })

  it('swaps one key out of a pair and keeps the other', () => {
    expect(swapShortcutBinding('moveGizmo', 'Backspace', 'delete')).toBe(true)
    expect(getShortcutBindings().moveGizmo).toEqual(['Backspace'])
    expect(getShortcutBindings().delete).toEqual(['Delete', 'v'])
  })
})

describe('persistence', () => {
  it('stores only what differs from the defaults, under dcdtuni-shortcuts', () => {
    expect(setShortcutBinding('moveGizmo', ['m'])).toBe(true)
    const stored = JSON.parse(localStorage.getItem(SHORTCUTS_STORAGE_KEY) ?? 'null')
    expect(stored).toEqual({ version: 1, bindings: { moveGizmo: ['m'] } })
    reloadShortcuts()
    expect(getShortcutBindings().moveGizmo).toEqual(['m'])
  })

  it('removes the entry when set back to the defaults, and on reset', () => {
    setShortcutBinding('moveGizmo', ['m'])
    setShortcutBinding('moveGizmo', ['v'])
    expect(localStorage.getItem(SHORTCUTS_STORAGE_KEY)).toBe(null)
    setShortcutBinding('rotateGizmo', ['t'])
    resetShortcutBindings()
    expect(localStorage.getItem(SHORTCUTS_STORAGE_KEY)).toBe(null)
    expect(getShortcutBindings()).toEqual(DEFAULT_BINDINGS)
  })

  it('refuses to rebind a fixed chord', () => {
    expect(setShortcutBinding('undo', ['u'])).toBe(false)
    expect(getShortcutBindings().undo).toEqual(['Mod+z'])
  })

  it('tells subscribers, and hands out a new object so a snapshot comparison sees it', () => {
    const before = getShortcutBindings()
    const fn = vi.fn()
    const off = subscribeShortcuts(fn)
    setShortcutBinding('rotateGizmo', ['t'])
    expect(fn).toHaveBeenCalledTimes(1)
    expect(getShortcutBindings()).not.toBe(before)
    off()
    resetShortcutBindings()
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('survives storage that throws — private mode, blocked site data', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    expect(() => reloadShortcuts()).not.toThrow()
    expect(getShortcutBindings()).toEqual(DEFAULT_BINDINGS)
    // The choice still takes effect for this page; it just does not persist.
    expect(setShortcutBinding('moveGizmo', ['m'])).toBe(true)
    expect(getShortcutBindings().moveGizmo).toEqual(['m'])
  })
})

describe('parseStoredBindings / resolveBindings', () => {
  it('ignores garbage, unknown actions, fixed chords and invalid keys', () => {
    expect(parseStoredBindings(null)).toEqual({})
    expect(parseStoredBindings('not json')).toEqual({})
    expect(parseStoredBindings('[1,2]')).toEqual({})
    expect(
      parseStoredBindings(
        JSON.stringify({
          version: 1,
          bindings: {
            moveGizmo: ['m'],
            rotateGizmo: ['Tab'],
            undo: ['u'],
            bogus: ['b'],
            delete: 'x',
          },
        }),
      ),
    ).toEqual({ moveGizmo: ['m'] })
  })

  it('lets a user binding win over a default that collides with it', () => {
    // A future default could land on a key the user already took.
    const resolved = resolveBindings({ moveGizmo: ['r'] })
    expect(resolved.moveGizmo).toEqual(['r'])
    expect(resolved.rotateGizmo).toEqual([])
  })

  it('never gives one key to two stored actions', () => {
    const resolved = resolveBindings({ moveGizmo: ['m'], rotateGizmo: ['m'] })
    expect(resolved.moveGizmo).toEqual(['m'])
    expect(resolved.rotateGizmo).toEqual([])
  })
})
