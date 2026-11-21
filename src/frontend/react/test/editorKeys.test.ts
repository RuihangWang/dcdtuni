import { createElement } from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { AuthOverlay } from '../src/components/AuthOverlay'
import { PpInitTask } from '../src/components/PpInitTask'
import { installEditorKeys, resolveEditorKey } from '../src/interaction/editorKeys'
import { requireLogin, resetAuthForTest } from '../src/run/auth'
import { DEFAULT_BINDINGS, reloadShortcuts, setShortcutBinding } from '../src/interaction/shortcuts'

describe('resolveEditorKey', () => {
  it('maps Ctrl+Z to undo and Cmd+Z to the same', () => {
    expect(resolveEditorKey({ key: 'z', ctrlKey: true })).toBe('undo')
    expect(resolveEditorKey({ key: 'z', metaKey: true })).toBe('undo')
  })

  it('maps both redo spellings', () => {
    expect(resolveEditorKey({ key: 'y', ctrlKey: true })).toBe('redo')
    // With Shift held the browser reports the capital, so both are matched.
    expect(resolveEditorKey({ key: 'Z', ctrlKey: true, shiftKey: true })).toBe('redo')
    expect(resolveEditorKey({ key: 'z', ctrlKey: true, shiftKey: true })).toBe('redo')
  })

  it('maps Delete and Backspace to delete', () => {
    expect(resolveEditorKey({ key: 'Delete' })).toBe('delete')
    expect(resolveEditorKey({ key: 'Backspace' })).toBe('delete')
  })

  it('leaves Ctrl+Delete alone — it is a word delete, not a command', () => {
    expect(resolveEditorKey({ key: 'Delete', ctrlKey: true })).toBe(null)
    expect(resolveEditorKey({ key: 'Backspace', metaKey: true })).toBe(null)
  })

  it('never fires for a keystroke typed into a field', () => {
    // The one that matters: Backspace in the rename box must delete a letter,
    // not the rack being renamed.
    for (const tag of ['input', 'textarea', 'select']) {
      expect(resolveEditorKey({ key: 'Backspace', targetTag: tag })).toBe(null)
      expect(resolveEditorKey({ key: 'z', ctrlKey: true, targetTag: tag })).toBe(null)
    }
  })

  it('honours contenteditable, which a tag-name check misses', () => {
    expect(resolveEditorKey({ key: 'Delete', targetTag: 'div', targetEditable: true })).toBe(null)
  })

  it('does nothing behind an open modal', () => {
    expect(resolveEditorKey({ key: 'Delete', inDialog: true })).toBe(null)
    expect(resolveEditorKey({ key: 'z', ctrlKey: true, inDialog: true })).toBe(null)
  })

  it('ignores plain letters and unmodified keys', () => {
    expect(resolveEditorKey({ key: 'z' })).toBe(null)
    expect(resolveEditorKey({ key: 'a', ctrlKey: true })).toBe(null)
    expect(resolveEditorKey({ key: 'Escape' })).toBe(null)
  })
})

describe('resolveEditorKey — the gizmo shortcuts', () => {
  it('maps the default V and R to the move and rotate gizmos', () => {
    expect(resolveEditorKey({ key: 'v' })).toBe('moveGizmo')
    expect(resolveEditorKey({ key: 'r' })).toBe('rotateGizmo')
    // Caps Lock, or Shift held: still the same key.
    expect(resolveEditorKey({ key: 'V' })).toBe('moveGizmo')
    expect(resolveEditorKey({ key: 'R', shiftKey: true })).toBe('rotateGizmo')
  })

  it('leaves Ctrl+R, Cmd+R, Ctrl+V and Alt+V to the browser', () => {
    expect(resolveEditorKey({ key: 'r', ctrlKey: true })).toBe(null)
    expect(resolveEditorKey({ key: 'r', metaKey: true })).toBe(null)
    expect(resolveEditorKey({ key: 'v', ctrlKey: true })).toBe(null)
    expect(resolveEditorKey({ key: 'v', altKey: true })).toBe(null)
  })

  it('ignores the auto-repeat of a held key, which would flicker the gizmo', () => {
    expect(resolveEditorKey({ key: 'v', repeat: true })).toBe(null)
  })

  it('reads the physical key under an IME', () => {
    expect(resolveEditorKey({ key: 'Process', code: 'KeyV' })).toBe('moveGizmo')
  })

  it('leaves an AltGr character alone (Windows reports AltGr as Ctrl+Alt)', () => {
    expect(resolveEditorKey({ key: 'ż', code: 'KeyZ', ctrlKey: true, altKey: true })).toBe(null)
    expect(
      resolveEditorKey({ key: 'Ż', code: 'KeyZ', ctrlKey: true, altKey: true, shiftKey: true }),
    ).toBe(null)
  })

  it('never fires while typing, behind a modal, or in contenteditable', () => {
    for (const tag of ['textarea', 'select']) {
      expect(resolveEditorKey({ key: 'v', targetTag: tag }), tag).toBe(null)
    }
    expect(resolveEditorKey({ key: 'v', targetTag: 'input', targetType: 'text' })).toBe(null)
    expect(resolveEditorKey({ key: 'r', targetTag: 'input', targetType: 'number' })).toBe(null)
    expect(resolveEditorKey({ key: 'v', targetTag: 'input' })).toBe(null)
    expect(resolveEditorKey({ key: 'v', inDialog: true })).toBe(null)
    expect(resolveEditorKey({ key: 'v', targetTag: 'div', targetEditable: true })).toBe(null)
  })

  it('still fires from a focused checkbox or slider, which take no typed letter', () => {
    // A clicked checkbox keeps focus, so without this the keys would be dead
    // until the user clicked somewhere else first.
    for (const type of ['checkbox', 'radio', 'range', 'button']) {
      expect(resolveEditorKey({ key: 'v', targetTag: 'input', targetType: type }), type).toBe('moveGizmo')
    }
    // Delete and the Ctrl chords keep the old rule on every field.
    expect(resolveEditorKey({ key: 'Backspace', targetTag: 'input', targetType: 'checkbox' })).toBe(null)
    expect(
      resolveEditorKey({ key: 'z', ctrlKey: true, targetTag: 'input', targetType: 'checkbox' }),
    ).toBe(null)
  })

  it('follows the bindings it is given', () => {
    const bindings = { ...DEFAULT_BINDINGS, moveGizmo: ['m'], delete: ['x'] }
    expect(resolveEditorKey({ key: 'm' }, bindings)).toBe('moveGizmo')
    expect(resolveEditorKey({ key: 'v' }, bindings)).toBe(null)
    expect(resolveEditorKey({ key: 'x' }, bindings)).toBe('delete')
    expect(resolveEditorKey({ key: 'Delete' }, bindings)).toBe(null)
    // The chords are not rebindable and are unaffected.
    expect(resolveEditorKey({ key: 'z', ctrlKey: true }, bindings)).toBe('undo')
  })
})

describe('installEditorKeys — the gizmo shortcuts', () => {
  let off: (() => void) | null = null
  let toggles: string[]
  let blocked: boolean
  let frozen: boolean
  let cleared: number

  function install() {
    off = installEditorKeys({
      selectedUids: () => ['m_1'],
      clearSelection: () => {
        cleared += 1
      },
      frozen: () => frozen,
      gizmoShortcutsBlocked: () => blocked,
      toggleGizmo: (kind) => {
        toggles.push(kind)
      },
    })
  }

  function press(key: string, init: KeyboardEventInit = {}, target: EventTarget = document.body) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })
    target.dispatchEvent(event)
    return event
  }

  beforeEach(() => {
    localStorage.clear()
    reloadShortcuts()
    toggles = []
    blocked = false
    frozen = false
    cleared = 0
    install()
  })

  afterEach(() => {
    off?.()
    off = null
    document.querySelectorAll('.modal-overlay, input').forEach((el) => el.remove())
    localStorage.clear()
    reloadShortcuts()
  })

  it('toggles the move gizmo on V and the rotate gizmo on R', () => {
    const e = press('v')
    press('r')
    expect(toggles).toEqual(['move', 'rotate'])
    expect(e.defaultPrevented).toBe(true)
  })

  it('does nothing while the host says the shortcuts are blocked (first person, 2D editor…)', () => {
    blocked = true
    press('v')
    expect(toggles).toEqual([])
  })

  it('does nothing during a run', () => {
    frozen = true
    press('r')
    expect(toggles).toEqual([])
  })

  it('does nothing behind an open modal', () => {
    const overlay = document.createElement('div')
    overlay.className = 'modal-overlay'
    document.body.appendChild(overlay)
    press('v')
    expect(toggles).toEqual([])
  })

  it('does nothing behind the sign-in overlay, which is a modal too', () => {
    // #auth-overlay is always mounted and only hidden with display:none, so
    // it has to say when it is up; a hidden one must not block anything.
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    try {
      flushSync(() => root.render(createElement(AuthOverlay)))
      press('v')
      expect(toggles).toEqual(['move'])
      flushSync(() => {
        void requireLogin()
      })
      expect((document.getElementById('auth-overlay') as HTMLElement).style.display).toBe('flex')
      press('r')
      press('Delete')
      expect(toggles).toEqual(['move'])
      expect(cleared).toBe(0)
    } finally {
      resetAuthForTest()
      flushSync(() => root.unmount())
      host.remove()
    }
  })

  it.each([
    [
      'a blocking post-processing task',
      () => createElement(PpInitTask, { title: 'x', lines: [], warnings: [], onCancel: () => {} }),
    ],
  ])('does nothing behind %s, a full-screen modal with no dialog class', (_name, element) => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    try {
      flushSync(() => root.render(element()))
      press('v')
      press('Delete')
      expect(toggles).toEqual([])
      expect(cleared).toBe(0)
    } finally {
      flushSync(() => root.unmount())
      host.remove()
    }
    press('v')
    expect(toggles).toEqual(['move'])
  })

  it('does nothing while typing in a text box, and still works from a checkbox', () => {
    const text = document.createElement('input')
    text.type = 'text'
    document.body.appendChild(text)
    press('v', {}, text)
    const box = document.createElement('input')
    box.type = 'checkbox'
    document.body.appendChild(box)
    press('v', {}, box)
    expect(toggles).toEqual(['move'])
  })

  it('reads the CURRENT bindings on every key, so a rebinding takes effect at once', () => {
    setShortcutBinding('moveGizmo', ['m'])
    press('v')
    press('m')
    expect(toggles).toEqual(['move'])
  })
})
