import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Toolbar, DEFAULT_VIEW_OPTIONS } from '../../src/components/Toolbar'
import { setLocale, t } from '../../src/i18n'
import {
  SHORTCUTS_STORAGE_KEY,
  getShortcutBindings,
  reloadShortcuts,
  setShortcutBinding,
} from '../../src/interaction/shortcuts'
import { resolveEditorKey, describeKeyEvent } from '../../src/interaction/editorKeys'

// The view cube is a WebGL canvas of its own and jsdom has no context for it.
vi.mock('../../src/components/ViewCube', () => ({ ViewCube: () => null }))

/**
 * The keyboard-shortcut dialog, from the top-bar button to the stored binding.
 *
 * Opened from the toolbar, drawn with the same `.modal-overlay > .modal`
 * chrome as every other dialog here, and portalled to `body`: `#toolbar` has
 * a backdrop-filter, which would make it the containing block of a fixed
 * overlay and clip the dialog to the bar.
 */

let host: HTMLDivElement
let root: Root

function render() {
  flushSync(() =>
    root.render(
      <Toolbar
        view={{ ...DEFAULT_VIEW_OPTIONS }}
        onChange={() => {}}
        hasSelection={false}
        selectedUids={[]}
        project={null}
      />,
    ),
  )
}

const byId = (id: string) => document.getElementById(id)

function click(id: string) {
  const el = byId(id)
  expect(el, id).not.toBeNull()
  flushSync(() => el!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

function press(key: string, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })
  flushSync(() => {
    document.body.dispatchEvent(event)
  })
  return event
}

const keyCell = (id: string) => byId(`shortcut-key-${id}`)?.textContent ?? ''

function open() {
  click('shortcut-settings-btn')
  expect(byId('shortcut-settings-overlay')).not.toBeNull()
}

beforeEach(() => {
  localStorage.clear()
  reloadShortcuts()
  setLocale('zh-CN')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  localStorage.clear()
  reloadShortcuts()
  setLocale('zh-CN')
})

describe('the top-bar button', () => {
  it('is a toolbar button like its neighbours, next to the language and theme selectors', () => {
    const btn = byId('shortcut-settings-btn')!
    expect(btn.className).toContain('tool-btn')
    expect(btn.textContent).toBe(t('toolbar.shortcuts'))
    expect(btn.getAttribute('title')).toBe(t('tooltip.shortcut_settings'))
    expect(byId('theme-selector')!.nextElementSibling).toBe(btn)
  })

  it('opens a portalled modal, outside the toolbar', () => {
    open()
    const overlay = byId('shortcut-settings-overlay')!
    expect(overlay.className).toContain('modal-overlay')
    expect(overlay.parentElement).toBe(document.body)
    expect(host.contains(overlay)).toBe(false)
    expect(overlay.querySelector('.modal')).not.toBeNull()
  })
})

describe('the table', () => {
  it('lists every action with its current key', () => {
    open()
    expect(keyCell('moveGizmo')).toBe('V')
    expect(keyCell('rotateGizmo')).toBe('R')
    expect(keyCell('delete')).toBe('Delete / Backspace')
    expect(keyCell('undo')).toBe('Ctrl+Z')
    expect(keyCell('redo')).toBe('Ctrl+Y / Ctrl+Shift+Z')
  })

  it('offers Change on the single keys and marks the chords fixed', () => {
    open()
    expect(byId('shortcut-change-moveGizmo')).not.toBeNull()
    expect(byId('shortcut-change-rotateGizmo')).not.toBeNull()
    expect(byId('shortcut-change-delete')).not.toBeNull()
    expect(byId('shortcut-change-undo')).toBeNull()
    expect(byId('shortcut-row-undo')!.textContent).toContain(t('shortcut.fixed'))
  })

  it('lists the first-person keys as read-only rows', () => {
    open()
    const fps = byId('shortcut-fps-table')!
    expect(fps.textContent).toContain('W / A / S / D')
    expect(fps.querySelector('button')).toBeNull()
  })

  it('follows the language switch', () => {
    open()
    const zh = byId('shortcut-settings-overlay')!.querySelector('.modal-title')!.textContent
    flushSync(() => setLocale('en-US'))
    const en = byId('shortcut-settings-overlay')!.querySelector('.modal-title')!.textContent
    expect(en).toBe(t('shortcut.title'))
    expect(en).not.toBe(zh)
  })
})

describe('changing a key', () => {
  it('captures the next key press and stores it', () => {
    open()
    click('shortcut-change-moveGizmo')
    expect(keyCell('moveGizmo')).toBe(t('shortcut.press_key'))
    const e = press('m')
    // The captured key is the dialog's; nothing else may act on it.
    expect(e.defaultPrevented).toBe(true)
    expect(keyCell('moveGizmo')).toBe('M')
    expect(getShortcutBindings().moveGizmo).toEqual(['m'])
    expect(JSON.parse(localStorage.getItem(SHORTCUTS_STORAGE_KEY)!).bindings.moveGizmo).toEqual(['m'])
  })

  it('a captured key never reaches any other key listener', () => {
    // The 2D room editor and first-person mode listen on window/document
    // themselves: the dialog's capture-phase window listener has to stop the
    // key there, or binding R would also act in the plan behind the dialog.
    const seen: string[] = []
    const onWindow = (e: KeyboardEvent) => seen.push(`window:${e.key}`)
    const onDocument = (e: KeyboardEvent) => seen.push(`document:${e.key}`)
    const onDocumentCapture = (e: KeyboardEvent) => seen.push(`document-capture:${e.key}`)
    window.addEventListener('keydown', onWindow)
    document.addEventListener('keydown', onDocument)
    document.addEventListener('keydown', onDocumentCapture, true)
    try {
      open()
      click('shortcut-change-moveGizmo')
      press('m')
      expect(getShortcutBindings().moveGizmo).toEqual(['m'])
      click('shortcut-change-rotateGizmo')
      press('Escape')
      expect(byId('shortcut-settings-overlay')).not.toBeNull()
      // Idle: Esc closes the dialog, and is the dialog's too.
      press('Escape')
      expect(byId('shortcut-settings-overlay')).toBeNull()
      expect(seen).toEqual([])
      // Closed: keys flow again.
      press('v')
      expect(seen).toEqual(['document-capture:v', 'document:v', 'window:v'])
    } finally {
      window.removeEventListener('keydown', onWindow)
      document.removeEventListener('keydown', onDocument)
      document.removeEventListener('keydown', onDocumentCapture, true)
    }
  })

  it('Esc cancels the capture and leaves the dialog open', () => {
    open()
    click('shortcut-change-rotateGizmo')
    press('Escape')
    expect(byId('shortcut-settings-overlay')).not.toBeNull()
    expect(keyCell('rotateGizmo')).toBe('R')
    expect(getShortcutBindings().rotateGizmo).toEqual(['r'])
  })

  it('refuses a combination and a reserved key, and keeps listening', () => {
    open()
    click('shortcut-change-moveGizmo')
    press('m', { ctrlKey: true })
    expect(byId('shortcut-error')!.textContent).toBe(t('shortcut.err_modifier'))
    press('Tab')
    expect(byId('shortcut-error')!.textContent).toBe(t('shortcut.err_reserved', { key: 'Tab' }))
    // A modifier on its own is someone reaching for a combination: wait.
    press('Shift')
    expect(keyCell('moveGizmo')).toBe(t('shortcut.press_key'))
    press('g')
    expect(getShortcutBindings().moveGizmo).toEqual(['g'])
    expect(byId('shortcut-error')).toBeNull()
  })

  it('shows a conflict and changes nothing until the user decides', () => {
    open()
    click('shortcut-change-moveGizmo')
    press('r')
    const conflict = byId('shortcut-conflict')!
    expect(conflict.textContent).toContain(
      t('shortcut.conflict', { key: 'R', action: t('shortcut.action_rotate_gizmo') }),
    )
    expect(getShortcutBindings().moveGizmo).toEqual(['v'])
    expect(getShortcutBindings().rotateGizmo).toEqual(['r'])
  })

  it('swaps the two keys on confirm', () => {
    open()
    click('shortcut-change-moveGizmo')
    press('r')
    click('shortcut-swap')
    expect(getShortcutBindings().moveGizmo).toEqual(['r'])
    expect(getShortcutBindings().rotateGizmo).toEqual(['v'])
    expect(keyCell('moveGizmo')).toBe('R')
    expect(keyCell('rotateGizmo')).toBe('V')
    expect(byId('shortcut-conflict')).toBeNull()
  })

  it('leaves both alone on cancel', () => {
    open()
    click('shortcut-change-moveGizmo')
    press('r')
    click('shortcut-conflict-cancel')
    expect(getShortcutBindings().moveGizmo).toEqual(['v'])
    expect(byId('shortcut-conflict')).toBeNull()
  })

  it('pressing the key an action already has is not a conflict', () => {
    open()
    click('shortcut-change-moveGizmo')
    press('v')
    expect(byId('shortcut-conflict')).toBeNull()
    expect(keyCell('moveGizmo')).toBe('V')
  })
})

describe('reset and close', () => {
  it('reset puts every key back and clears the stored entry', () => {
    setShortcutBinding('moveGizmo', ['m'])
    open()
    expect(keyCell('moveGizmo')).toBe('M')
    click('shortcut-reset')
    expect(keyCell('moveGizmo')).toBe('V')
    expect(localStorage.getItem(SHORTCUTS_STORAGE_KEY)).toBe(null)
  })

  it('closes from the button, from Esc, and from the backdrop', () => {
    open()
    click('shortcut-close')
    expect(byId('shortcut-settings-overlay')).toBeNull()
    open()
    press('Escape')
    expect(byId('shortcut-settings-overlay')).toBeNull()
    open()
    click('shortcut-settings-overlay')
    expect(byId('shortcut-settings-overlay')).toBeNull()
  })

  it('suppresses the editor keys while it is open', () => {
    open()
    const e = new KeyboardEvent('keydown', { key: 'v', bubbles: true })
    Object.defineProperty(e, 'target', { value: document.body })
    expect(resolveEditorKey(describeKeyEvent(e))).toBe(null)
    click('shortcut-close')
    expect(resolveEditorKey(describeKeyEvent(e))).toBe('moveGizmo')
  })
})
