/**
 * The editor's keyboard shortcuts: undo, redo, delete, and the move / rotate
 * gizmo toggles.
 *
 * A port of the one `document` keydown handler the legacy editor installs
 * (STL_generate.html:29696-29723). Until this existed the undo and redo buttons
 * carried `title="Ctrl+Z"` / `title="Ctrl+Y"` and nothing was listening — the
 * UI advertised a binding it did not have.
 *
 * WHICH key does WHAT is not decided here: `shortcuts.ts` holds the registry,
 * the defaults (V = move gizmo, R = rotate gizmo) and the user's rebindings,
 * and this handler asks it on every key press. What is decided here is WHEN a
 * key may act at all, in `resolveEditorKey`, which is pure so the rules can be
 * tested without a document:
 *
 * - **A keystroke aimed at a field is never a command.** Backspace in the
 *   project-name box must delete a letter, not the selected rack. The legacy
 *   checks the target's tag name; this also honours `contenteditable`, which
 *   the tag check misses. One exception: a plain LETTER pressed on a focused
 *   checkbox, radio, slider or button — inputs that take no typed character. A
 *   clicked checkbox keeps focus, and without the exception R and V would be
 *   dead until the user clicked somewhere else.
 * - **Ctrl+Delete is not delete.** It is a word-delete in a text field and the
 *   legacy explicitly lets it through rather than treating it as a command.
 * - **Ctrl+Shift+Z is redo, Ctrl+Z alone is undo.** With Shift held the browser
 *   reports `Z`, so both cases are matched.
 * - **A plain key never fires with Ctrl or Cmd held**: Ctrl+R reloads the page
 *   and Ctrl+V pastes, and neither may become a gizmo toggle.
 *
 * One rule is NOT in the legacy: a keystroke while a modal is open does
 * nothing. The legacy has the same hole and a Delete pressed behind its file
 * browser silently removes a model from the project underneath — which is data
 * loss with no visible cause.
 *
 * The gizmo keys have more places they must not act — first-person mode, the
 * 2D room editor, STL preview, rack detail, any tab but geometry, and the
 * middle of a viewport drag — and those are the HOST's to report
 * (`gizmoShortcutsBlocked`, built by `gizmoShortcutHost.ts`), since only the
 * viewport knows them.
 */

import * as store from '../projectStore'
import {
  DEFAULT_BINDINGS,
  eventKey,
  getShortcutBindings,
  matchShortcut,
  type ShortcutBindings,
} from './shortcuts'

export type EditorKeyAction = 'undo' | 'redo' | 'delete' | 'moveGizmo' | 'rotateGizmo' | null

export interface EditorKeyEvent {
  key: string
  /** The physical key (`KeyV`), read when an IME hides the letter. */
  code?: string
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
  shiftKey?: boolean
  /** A held key's auto-repeat. */
  repeat?: boolean
  /** Lower-cased tag name of the element the keystroke went to. */
  targetTag?: string
  /** The `type` of that element, when it is an `<input>`. */
  targetType?: string
  /** Whether that element (or an ancestor) is contenteditable. */
  targetEditable?: boolean
  /** Whether a modal dialog is open over the editor. */
  inDialog?: boolean
}

const FIELD_TAGS = new Set(['input', 'textarea', 'select'])

/** Inputs that take no typed character, so a plain letter aimed at one is free. */
const NON_TEXT_INPUT_TYPES = new Set([
  'checkbox',
  'radio',
  'range',
  'button',
  'submit',
  'reset',
  'color',
])

export function resolveEditorKey(
  e: EditorKeyEvent,
  bindings: ShortcutBindings = DEFAULT_BINDINGS,
): EditorKeyAction {
  if (e.targetEditable) return null
  if (e.inDialog) return null
  const action = matchShortcut(e, bindings)
  if (!action) return null
  if (e.targetTag && FIELD_TAGS.has(e.targetTag)) {
    const plainCharacter =
      e.ctrlKey !== true && e.metaKey !== true && eventKey(e).length === 1
    const freeInput =
      e.targetTag === 'input' && NON_TEXT_INPUT_TYPES.has(String(e.targetType ?? '').toLowerCase())
    if (!(plainCharacter && freeInput)) return null
  }
  return action
}

/**
 * Selectors for the overlays this app puts over the editor.
 *
 * The two classes are the dialogs' chrome, mounted only while open. An overlay
 * that stays mounted and is merely hidden — the sign-in form, `#auth-overlay`,
 * shown on any 401 — cannot be found by class, so it carries
 * `data-modal-open` while it is up and loses it when it hides.
 */
export const DIALOG_SELECTOR = '.modal-backdrop, .modal-overlay, [data-modal-open]'

export function describeKeyEvent(event: KeyboardEvent): EditorKeyEvent {
  const target = event.target as (Element & { isContentEditable?: boolean }) | null
  const tag = target?.tagName ? target.tagName.toLowerCase() : undefined
  return {
    key: event.key,
    code: event.code,
    ctrlKey: event.ctrlKey,
    metaKey: event.metaKey,
    altKey: event.altKey,
    shiftKey: event.shiftKey,
    repeat: event.repeat,
    targetTag: tag,
    targetType: tag === 'input' ? (target as HTMLInputElement).type : undefined,
    targetEditable: target?.isContentEditable === true,
    inDialog:
      typeof document !== 'undefined' && !!document.querySelector(DIALOG_SELECTOR),
  }
}

export interface EditorKeyHost {
  /** Every selected model, active one first. */
  selectedUids(): readonly string[]
  /** Called after a delete, so the inspector stops showing a model that is gone. */
  clearSelection(): void
  /** True while a mesh or solve job owns the geometry. */
  frozen(): boolean
  /**
   * Liquid mode's waypoint delete, which gets first refusal.
   *
   * A selected pipe waypoint is a finer selection than the pipe itself, so
   * Delete has to remove the waypoint rather than the whole pipe — the legacy
   * calls `_liquidMode._deleteSelectedWaypoint()` before touching the model.
   */
  deleteWaypoint?(): boolean
  /**
   * Whether the gizmo keys are off right now: first-person mode, the 2D room
   * editor, STL preview, rack detail, a tab other than geometry, or a live
   * viewport drag. Only the viewport knows these, so it answers.
   */
  gizmoShortcutsBlocked?(): boolean
  /** Toggle the move or rotate gizmo of the current selection. */
  toggleGizmo?(kind: 'move' | 'rotate'): void
}

/** Bind the shortcuts. Returns the unbind, for an effect cleanup. */
export function installEditorKeys(host: EditorKeyHost): () => void {
  const onKeyDown = (event: KeyboardEvent) => {
    // The CURRENT bindings, read per key press: a rebinding in the shortcut
    // dialog takes effect without re-installing anything.
    const action = resolveEditorKey(describeKeyEvent(event), getShortcutBindings())
    if (!action) return
    // An edit during a run would not be in the job that is running, so the
    // results would stop matching the screen with nothing failing.
    if (host.frozen()) return
    if (action === 'moveGizmo' || action === 'rotateGizmo') {
      if (!host.toggleGizmo || host.gizmoShortcutsBlocked?.()) return
      event.preventDefault()
      host.toggleGizmo(action === 'moveGizmo' ? 'move' : 'rotate')
      return
    }
    if (action === 'undo') {
      event.preventDefault()
      store.undo()
      return
    }
    if (action === 'redo') {
      event.preventDefault()
      store.redo()
      return
    }
    if (host.deleteWaypoint?.()) return
    const uids = [...host.selectedUids()]
    if (!uids.length) return
    event.preventDefault()
    // `removeModels` is ONE undo entry for the whole group; a loop over
    // `removeModel` would need eight presses of Ctrl+Z to put eight racks back.
    if (uids.length > 1) store.removeModels(uids)
    else store.removeModel(uids[0]!)
    host.clearSelection()
  }
  document.addEventListener('keydown', onKeyDown)
  return () => document.removeEventListener('keydown', onKeyDown)
}
