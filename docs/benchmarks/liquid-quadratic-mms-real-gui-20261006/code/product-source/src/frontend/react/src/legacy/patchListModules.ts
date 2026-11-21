/**
 * The patch-list filter bar and selection model, shared with the legacy editor.
 *
 * `js/patch_filter.js` and `js/patch_selection.js` are loaded by the legacy page
 * (STL_generate.html:1198-1199) and drive BOTH of its patch lists — the
 * post-processing tree and the mesh-preview list. They are pure UI logic with no
 * dependency on the rest of that page: the filter bar builds its own DOM into a
 * container you hand it, and the selection model is a state machine over patch
 * NAMES that never touches the document at all.
 *
 * Reimplementing them in TypeScript would leave two implementations of the same
 * three-flag predicate (case / whole-word / regex) and the same shift+ctrl range
 * arithmetic, and the way that pair breaks is silent: the two apps disagree
 * about which rows a filter matches and nothing fails. So they are imported for
 * their side effects, exactly as `cfdprojModules.ts` imports the .cfdproj format
 * modules, and typed here once.
 *
 * ## Nothing to shim
 *
 * Unlike `liquidScene.ts`, these two files read no bare globals — no `state`, no
 * `requestRender`, no `logOperation`. They only assign `window.createFilterBar`
 * and `window.createSelectionModel`. That is why this wrapper has no shim block:
 * the accessors below are the whole integration.
 */

import '../../../shared/js/patch_filter.js'
import '../../../shared/js/patch_selection.js'

/** The three toggles, in the order the bar renders them. */
export interface FilterState {
  text: string
  /** `Aa` — match case. */
  case: boolean
  /** `ab|` — the whole name must equal the text. Ignored while `regex` is on. */
  whole: boolean
  /** `.*` — the text is a regular expression. */
  regex: boolean
}

export type PatchPredicate = (name: string) => boolean

export interface FilterBar {
  /** Rebuilt on every keystroke and every toggle; read it, never cache it. */
  predicate: PatchPredicate
  getState(): FilterState
  setState(next: Partial<FilterState>): void
  clear(): void
  /** Fills the `n / total` counter at the right of the bar. */
  updateCount(matched: number, total: number): void
  element: HTMLElement
}

export interface SelectionModel {
  handleRowClick(
    name: string,
    mods: { shift: boolean; ctrl: boolean; orderedNames: string[] },
  ): void
  /**
   * A checkbox click, which is a VISIBILITY change, not a selection change.
   * With a modifier held and a live selection it fans the new checked state out
   * over the whole selection; otherwise it is just the one patch.
   */
  handleCheckboxClick(
    name: string,
    mods: { shift: boolean; ctrl: boolean; nextVisible: boolean },
  ): { targets: string[]; visible: boolean }
  setSelection(names: string[]): void
  clear(): void
  has(name: string): boolean
  getAll(): Set<string>
  getLastAnchor(): string | null
}

interface PatchListGlobals {
  createFilterBar(opts: { container: HTMLElement; onChange?: (p: PatchPredicate) => void }): FilterBar
  createSelectionModel(opts?: {
    onChange?: (prev: Set<string>, curr: Set<string>) => void
  }): SelectionModel
}

function globals(): PatchListGlobals {
  const w = window as unknown as Partial<PatchListGlobals>
  if (!w.createFilterBar || !w.createSelectionModel) {
    throw new Error(
      'shared/js/patch_filter.js and patch_selection.js did not attach to window — ' +
        'the import in legacy/patchListModules.ts was dropped or the files moved',
    )
  }
  return w as PatchListGlobals
}

/**
 * Build the filter bar and insert it as the FIRST child of `container`.
 *
 * The module inserts its own DOM, so the container must be one React does not
 * render children into — see `patchListControls.ts`, which passes an empty host
 * div for exactly that reason.
 */
export function createFilterBar(opts: {
  container: HTMLElement
  onChange?: (p: PatchPredicate) => void
}): FilterBar {
  return globals().createFilterBar(opts)
}

export function createSelectionModel(opts?: {
  onChange?: (prev: Set<string>, curr: Set<string>) => void
}): SelectionModel {
  return globals().createSelectionModel(opts)
}
