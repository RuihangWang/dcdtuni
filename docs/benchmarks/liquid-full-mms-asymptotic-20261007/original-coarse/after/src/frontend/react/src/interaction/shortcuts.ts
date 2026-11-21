/**
 * The editor's keyboard shortcuts: one registry, the defaults, and the user's
 * own bindings.
 *
 * `editorKeys` asks THIS module what a key means, and the shortcut dialog
 * edits what it answers — so the handler and the settings screen cannot
 * disagree about a binding, and adding an action is one row here.
 *
 * A binding is a string:
 *
 * - a plain key, normalised: a lower-case character (`'v'`, `'7'`, `'/'`) or a
 *   named editing key (`'Delete'`, `'Backspace'`, `'Home'`…);
 * - or a chord: `'Mod+z'`, `'Mod+Shift+z'`, where `Mod` is Ctrl or Cmd.
 *
 * The rules that keep a plain key from hijacking the browser live in
 * `matchShortcut`: a plain binding never fires with Ctrl or Cmd held (Ctrl+R
 * reloads the page, Ctrl+V pastes), a character binding never fires with Alt
 * held (Option+V types a character on a Mac), and a toggle does not re-fire on
 * a held key's auto-repeat. Shift is ignored for a plain key, so Caps Lock or
 * a held Shift does not make R a different key.
 *
 * Only the single keys can be rebound. Undo and redo are listed, and matched
 * from here, but fixed: they are the chords every editor shares, and a plain
 * key typed where Ctrl+Z was meant would be an edit nobody asked for.
 *
 * The user's bindings persist in `localStorage['dcdtuni-shortcuts']` as
 * `{ version: 1, bindings: { <action>: [<key>, …] } }`, holding only what
 * differs from the defaults. Storage can be absent or throw (private mode,
 * blocked site data); every access is guarded and the defaults apply.
 */

import type { TranslationKey } from '../i18n'

export type ShortcutActionId = 'moveGizmo' | 'rotateGizmo' | 'delete' | 'undo' | 'redo'

export interface ShortcutAction {
  id: ShortcutActionId
  labelKey: TranslationKey
  defaults: readonly string[]
  /** False for the chords the dialog lists but does not offer to change. */
  rebindable: boolean
  /** Whether a held key's auto-repeat fires the action again. */
  repeat: boolean
}

export const SHORTCUT_ACTIONS: readonly ShortcutAction[] = [
  {
    id: 'moveGizmo',
    labelKey: 'shortcut.action_move_gizmo',
    defaults: ['v'],
    rebindable: true,
    repeat: false,
  },
  {
    id: 'rotateGizmo',
    labelKey: 'shortcut.action_rotate_gizmo',
    defaults: ['r'],
    rebindable: true,
    repeat: false,
  },
  {
    id: 'delete',
    labelKey: 'shortcut.action_delete',
    defaults: ['Delete', 'Backspace'],
    rebindable: true,
    // As before: a held Delete kept firing, and the first one clears the
    // selection, so the rest are no-ops.
    repeat: true,
  },
  {
    id: 'undo',
    labelKey: 'shortcut.action_undo',
    defaults: ['Mod+z'],
    rebindable: false,
    repeat: true,
  },
  {
    id: 'redo',
    labelKey: 'shortcut.action_redo',
    defaults: ['Mod+y', 'Mod+Shift+z'],
    rebindable: false,
    repeat: true,
  },
]

export type ShortcutBindings = Readonly<Record<ShortcutActionId, readonly string[]>>
type Overrides = Partial<Record<ShortcutActionId, string[]>>

export const DEFAULT_BINDINGS: ShortcutBindings = Object.freeze(
  Object.fromEntries(SHORTCUT_ACTIONS.map((a) => [a.id, a.defaults])) as Record<
    ShortcutActionId,
    readonly string[]
  >,
)

export const SHORTCUTS_STORAGE_KEY = 'dcdtuni-shortcuts'

const ACTION_BY_ID = new Map(SHORTCUT_ACTIONS.map((a) => [a.id, a]))

export function shortcutAction(id: ShortcutActionId): ShortcutAction {
  return ACTION_BY_ID.get(id)!
}

// --- keys --------------------------------------------------------------------

/** The named keys a plain binding may use besides a single character. */
const NAMED_KEYS = new Set(['Delete', 'Backspace', 'Insert', 'Home', 'End', 'PageUp', 'PageDown'])

/** Keys that only modify another: pressing one during a capture means "wait". */
const MODIFIER_KEYS = new Set([
  'Shift',
  'Control',
  'Alt',
  'AltGraph',
  'Meta',
  'OS',
  'CapsLock',
  'NumLock',
  'ScrollLock',
  'Fn',
  'FnLock',
  'Hyper',
  'Super',
])

/**
 * The key an event names, normalised the way bindings are written.
 *
 * A letter is lower-cased, so Caps Lock and Shift do not change which key it
 * is. When an IME is composing (`Process`), the layout is not Latin (`к` on a
 * Russian keyboard), or the key is unknown, the PHYSICAL key is read from
 * `code` instead — the same key on the keyboard is the same shortcut.
 */
export function normalizeKey(key: string, code?: string): string {
  // Document-level boundary: synthetic or automation-dispatched events can
  // arrive with `key` never set (undefined). Treat those as "no key" — the
  // empty string matches no binding — instead of throwing inside the global
  // shortcut pass and surfacing a production TypeError.
  if (typeof key !== 'string' || key.length === 0) return ''
  const nonLatin = key.length === 1 && key.charCodeAt(0) > 0x7e
  if (key === 'Process' || key === 'Unidentified' || key === 'Dead' || nonLatin) {
    const m = /^Key([A-Z])$/.exec(code ?? '') ?? /^Digit([0-9])$/.exec(code ?? '')
    if (m) return m[1]!.toLowerCase()
  }
  return key.length === 1 ? key.toLowerCase() : key
}

/**
 * The key a key EVENT names — `normalizeKey`, minus the physical-key fallback
 * while AltGr is held.
 *
 * Windows reports AltGr as Ctrl+Alt. With it down a key TYPES a character —
 * Polish AltGr+Z is 'ż' — and reading `code` there would turn that character
 * into Ctrl+Z, an undo nobody pressed. A plain Ctrl+Alt+Z on a Latin layout
 * still reports 'z' and is still undo, as it always was.
 */
export function eventKey(e: { key: string; code?: string; ctrlKey?: boolean; altKey?: boolean }): string {
  const altGr = e.ctrlKey === true && e.altKey === true
  return normalizeKey(e.key, altGr ? undefined : e.code)
}

interface ParsedBinding {
  mod: boolean
  shift: boolean
  key: string
}

function parseBinding(binding: string): ParsedBinding {
  const m = /^(Mod\+)?(Shift\+)?(.+)$/.exec(binding)
  if (!m) return { mod: false, shift: false, key: binding }
  return { mod: !!m[1], shift: !!m[2], key: m[3]! }
}

/** Whether a stored plain key is one a user could have bound. */
function isBindablePlainKey(key: string): boolean {
  if (NAMED_KEYS.has(key)) return true
  return key.length === 1 && key !== ' ' && key === key.toLowerCase() && key.charCodeAt(0) <= 0x7e
}

export interface ShortcutKeyEvent {
  key: string
  code?: string
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
  shiftKey?: boolean
  repeat?: boolean
}

/**
 * The action this key press is bound to, or null.
 *
 * Pure: the bindings are passed in, so the rules can be tested without the
 * module's stored state.
 */
export function matchShortcut(
  e: ShortcutKeyEvent,
  bindings: ShortcutBindings,
): ShortcutActionId | null {
  const key = eventKey(e)
  const mod = e.ctrlKey === true || e.metaKey === true
  for (const action of SHORTCUT_ACTIONS) {
    for (const binding of bindings[action.id] ?? []) {
      const b = parseBinding(binding)
      if (b.key !== key) continue
      if (b.mod) {
        // A chord: Ctrl or Cmd held, and Shift exactly as written — Ctrl+Z is
        // undo and Ctrl+Shift+Z is redo.
        if (!mod || (e.shiftKey === true) !== b.shift) continue
      } else {
        if (mod) continue
        // Alt changes the character a key types; a character binding is the
        // bare key.
        if (b.key.length === 1 && e.altKey === true) continue
        if (b.shift && e.shiftKey !== true) continue
      }
      if (e.repeat === true && !action.repeat) return null
      return action.id
    }
  }
  return null
}

export type CaptureResult =
  | { kind: 'key'; key: string }
  | { kind: 'wait' }
  | { kind: 'error'; reason: 'modifier' | 'reserved'; key: string }

/**
 * What a key pressed in the dialog's capture mode binds to.
 *
 * A single key, never a combination — Ctrl/Cmd/Alt combinations belong to the
 * browser and the OS. A modifier on its own means someone is reaching for a
 * combination, so the capture keeps waiting rather than failing. Tab, Enter,
 * Space, Escape, the arrows and the function keys already mean something to
 * the page or the browser, and are refused.
 */
export function captureBinding(e: ShortcutKeyEvent): CaptureResult {
  if (MODIFIER_KEYS.has(e.key)) return { kind: 'wait' }
  const key = normalizeKey(e.key, e.code)
  if (e.ctrlKey === true || e.metaKey === true || e.altKey === true) {
    return { kind: 'error', reason: 'modifier', key }
  }
  if (isBindablePlainKey(key)) return { kind: 'key', key }
  return { kind: 'error', reason: 'reserved', key }
}

/** The key as printed on the keycap: `V`, `Delete`, `Space`. */
export function formatKey(key: string): string {
  if (key === ' ') return 'Space'
  return key.length === 1 ? key.toUpperCase() : key
}

/** A binding as the dialog prints it: `V`, `Ctrl+Shift+Z`. */
export function formatBinding(binding: string): string {
  if (binding === ' ') return 'Space'
  const b = parseBinding(binding)
  return [b.mod ? 'Ctrl' : null, b.shift ? 'Shift' : null, formatKey(b.key)]
    .filter(Boolean)
    .join('+')
}

export function formatBindings(bindings: readonly string[]): string {
  return bindings.map(formatBinding).join(' / ')
}

/** The OTHER action that already owns `key`, if one does. */
export function findConflict(
  bindings: ShortcutBindings,
  id: ShortcutActionId,
  key: string,
): ShortcutActionId | null {
  for (const action of SHORTCUT_ACTIONS) {
    if (action.id === id) continue
    if ((bindings[action.id] ?? []).includes(key)) return action.id
  }
  return null
}

// --- stored state ------------------------------------------------------------

/** The user's overrides, validated. Garbage and unknown actions are dropped. */
export function parseStoredBindings(raw: string | null): Overrides {
  if (!raw) return {}
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return {}
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return {}
  const bindings = (data as { bindings?: unknown }).bindings
  if (!bindings || typeof bindings !== 'object' || Array.isArray(bindings)) return {}
  const out: Overrides = {}
  for (const action of SHORTCUT_ACTIONS) {
    if (!action.rebindable) continue
    const keys = (bindings as Record<string, unknown>)[action.id]
    if (!Array.isArray(keys) || keys.length === 0) continue
    if (!keys.every((k) => typeof k === 'string' && isBindablePlainKey(k))) continue
    out[action.id] = [...new Set(keys as string[])]
  }
  return out
}

/**
 * Defaults plus overrides, with no key bound twice.
 *
 * A user binding wins over a default that collides with it — a future default
 * could land on a key the user already took — and among stored bindings the
 * first action in registry order wins.
 */
export function resolveBindings(overrides: Overrides): ShortcutBindings {
  const out = {} as Record<ShortcutActionId, readonly string[]>
  const taken = new Set<string>()
  for (const action of SHORTCUT_ACTIONS) {
    const own = action.rebindable ? overrides[action.id] : undefined
    if (!own) continue
    const keys = own.filter((k) => !taken.has(k))
    keys.forEach((k) => taken.add(k))
    out[action.id] = keys
  }
  for (const action of SHORTCUT_ACTIONS) {
    if (out[action.id]) continue
    out[action.id] = action.defaults.filter((k) => !taken.has(k))
  }
  return Object.freeze(out)
}

let overrides: Overrides = {}
let current: ShortcutBindings = DEFAULT_BINDINGS
const listeners = new Set<() => void>()

function readStorage(): Overrides {
  try {
    return parseStoredBindings(localStorage.getItem(SHORTCUTS_STORAGE_KEY))
  } catch {
    return {}
  }
}

function writeStorage() {
  try {
    if (Object.keys(overrides).length === 0) localStorage.removeItem(SHORTCUTS_STORAGE_KEY)
    else
      localStorage.setItem(
        SHORTCUTS_STORAGE_KEY,
        JSON.stringify({ version: 1, bindings: overrides }),
      )
  } catch {
    /* private mode: the choice holds for this page and does not persist */
  }
}

function commit() {
  current = resolveBindings(overrides)
  writeStorage()
  listeners.forEach((fn) => fn())
}

/** Re-read the stored bindings (page load, tests). */
export function reloadShortcuts() {
  overrides = readStorage()
  current = resolveBindings(overrides)
  listeners.forEach((fn) => fn())
}

reloadShortcuts()

/** The live bindings. A new object after every change, for snapshot readers. */
export function getShortcutBindings(): ShortcutBindings {
  return current
}

export function subscribeShortcuts(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

function sameKeys(a: readonly string[], b: readonly string[]) {
  return a.length === b.length && a.every((k, i) => k === b[i])
}

function setOverride(id: ShortcutActionId, keys: string[]) {
  const unique = [...new Set(keys)]
  if (sameKeys(unique, shortcutAction(id).defaults)) delete overrides[id]
  else overrides[id] = unique
}

/**
 * Bind `id` to `keys`, replacing what it had.
 *
 * Refused (false) for a fixed chord, an unbindable key, or a key another
 * action already owns — the dialog resolves a conflict through
 * `swapShortcutBinding` first, so one key never means two things.
 */
export function setShortcutBinding(id: ShortcutActionId, keys: string[]): boolean {
  const action = ACTION_BY_ID.get(id)
  if (!action?.rebindable || keys.length === 0) return false
  if (!keys.every(isBindablePlainKey)) return false
  if (keys.some((k) => findConflict(current, id, k))) return false
  setOverride(id, keys)
  commit()
  return true
}

/**
 * Give `key` to `id`, and hand `id`'s old keys to `otherId`, which had `key`.
 *
 * `otherId` keeps whatever else it had: swapping Backspace out of Delete's
 * pair leaves Delete on Delete.
 */
export function swapShortcutBinding(
  id: ShortcutActionId,
  key: string,
  otherId: ShortcutActionId,
): boolean {
  const action = ACTION_BY_ID.get(id)
  const other = ACTION_BY_ID.get(otherId)
  if (!action?.rebindable || !other?.rebindable || id === otherId) return false
  if (!isBindablePlainKey(key)) return false
  const previous = current[id].filter((k) => k !== key)
  const kept = current[otherId].filter((k) => k !== key)
  setOverride(id, [key])
  setOverride(otherId, [...kept, ...previous.filter((k) => !kept.includes(k))])
  commit()
  return true
}

/** Every action back on its defaults; the stored entry is removed. */
export function resetShortcutBindings() {
  overrides = {}
  commit()
}
