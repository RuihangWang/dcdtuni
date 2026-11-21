/**
 * The grid and the snap toggles, remembered per project.
 *
 * These are how a room is worked on — the grid off while looking at airflow,
 * face-snap on while placing racks — and the legacy keeps them in
 * `localStorage` under `cfdproj.ui.<uid>`, restoring them over the file's own
 * `ui` when the project loads (`UI_PREF_KEYS`, `loadUiPrefs`, `saveUiPrefs`,
 * STL_generate.html:3247-3277; restore at :14968). React reset them to defaults
 * on every page open.
 *
 * ## The legacy's key, in the legacy's shape
 *
 * Both editors run from one browser profile against the same projects, so a
 * second key would make them disagree about a setting the user thinks is one
 * setting — and writing this key in a different shape would corrupt the other
 * editor's copy. The file therefore keeps `view: { showGrid, gridSpacing }` in
 * METRES with the three snap booleans at the top level, exactly as the legacy
 * writes them; the two fields only this port has (`lights`, `rotationSnap`)
 * live under `react`, which the legacy ignores.
 *
 * A save MERGES: `treeCollapsed`, `groups` and the preview-visibility maps are
 * the legacy's, in the same object, and overwriting the file would wipe them.
 */

import { useEffect, useRef } from 'react'
import type { ViewOptions } from '../components/Toolbar'

/** The legacy's debounce (STL_generate.html:3277). */
export const VIEW_PREFS_DEBOUNCE_MS = 800

/** Metres, as the legacy clamps them (`normalizeViewSettings`). */
const MIN_SPACING_M = 0.01
const MAX_SPACING_M = 1

export function viewPrefsKey(uid: string): string {
  return `cfdproj.ui.${uid}`
}

type Stored = Record<string, unknown> & {
  view?: { showGrid?: unknown; gridSpacing?: unknown }
  snapFace?: unknown
  snapEdge?: unknown
  snapGrid?: unknown
  react?: { lights?: unknown; rotationSnap?: unknown }
}

/**
 * Read the whole entry, foreign keys included.
 *
 * Everything here is guarded: `localStorage` throws outright in a browser set
 * to block site data, and a headless run may not have it at all.
 */
function readRaw(uid: string): Stored {
  try {
    const raw = localStorage.getItem(viewPrefsKey(uid))
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as Stored) : {}
  } catch {
    return {}
  }
}

function bool(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined
}

/** What this project was last left looking like. Absent fields stay absent. */
export function loadViewPrefs(uid: string | null | undefined): Partial<ViewOptions> {
  if (!uid) return {}
  const stored = readRaw(uid)
  const out: Partial<ViewOptions> = {}
  const showGrid = bool(stored.view?.showGrid)
  if (showGrid !== undefined) out.showGrid = showGrid
  const spacing = stored.view?.gridSpacing
  if (typeof spacing === 'number' && Number.isFinite(spacing)) {
    out.gridSpacingMm = clampSpacing(spacing) * 1000
  }
  for (const key of ['snapFace', 'snapEdge', 'snapGrid'] as const) {
    const value = bool(stored[key])
    if (value !== undefined) out[key] = value
  }
  const lights = bool(stored.react?.lights)
  if (lights !== undefined) out.lights = lights
  const rotationSnap = bool(stored.react?.rotationSnap)
  if (rotationSnap !== undefined) out.rotationSnap = rotationSnap
  return out
}

function clampSpacing(metres: number): number {
  return Math.min(MAX_SPACING_M, Math.max(MIN_SPACING_M, metres))
}

export function saveViewPrefs(uid: string | null | undefined, view: ViewOptions): void {
  // No project, no bucket. The legacy falls back to a `default` key; writing
  // one here would hand the next project someone else's grid.
  if (!uid) return
  const next: Stored = {
    ...readRaw(uid),
    view: {
      showGrid: view.showGrid,
      gridSpacing: clampSpacing((view.gridSpacingMm || 0) / 1000),
    },
    snapFace: view.snapFace,
    snapEdge: view.snapEdge,
    snapGrid: view.snapGrid,
    react: { lights: view.lights, rotationSnap: view.rotationSnap },
  }
  try {
    localStorage.setItem(viewPrefsKey(uid), JSON.stringify(next))
  } catch {
    /* private mode, or the quota: the prefs just do not persist */
  }
}

/**
 * Keep the open project's entry in step with the toolbar.
 *
 * Debounced, because the grid-spacing slider changes this on every pointer
 * move, and flushed on `beforeunload` so the last change does not go with the
 * tab — both as the legacy does.
 */
export function useViewPrefsPersistence(uid: string | null | undefined, view: ViewOptions): void {
  // Read through refs: the flush runs from an event listener installed once,
  // and a listener re-bound on every view change would fire with a stale one.
  const latest = useRef({ uid, view })
  latest.current = { uid, view }

  useEffect(() => {
    const timer = setTimeout(() => saveViewPrefs(uid, view), VIEW_PREFS_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [uid, view])

  useEffect(() => {
    const flush = () => saveViewPrefs(latest.current.uid, latest.current.view)
    window.addEventListener('beforeunload', flush)
    return () => window.removeEventListener('beforeunload', flush)
  }, [])
}

/**
 * Restore this project's prefs, and keep them written.
 *
 * The restore is keyed on the UID, not hung off the file dialog's handler: a
 * project is opened from the dialog, from a remote path, and from
 * `window.__dcdtuni.loadProject` — the route every script and the parity
 * harness take — and a restore that lives in one of them is missing from the
 * other two.
 *
 * Nothing stored means nothing changes: the session keeps whatever the user is
 * currently looking at, which is this port's existing behaviour for a project
 * it has never seen.
 */
export function useViewPrefs(
  uid: string | null | undefined,
  view: ViewOptions,
  setView: (next: ViewOptions | ((prev: ViewOptions) => ViewOptions)) => void,
): void {
  const apply = useRef(setView)
  apply.current = setView

  useEffect(() => {
    if (!uid) return
    const stored = loadViewPrefs(uid)
    if (Object.keys(stored).length === 0) return
    // Keyed on the uid alone: re-running it when `view` changes would undo the
    // toggle the user just made.
    apply.current((prev) => ({ ...prev, ...stored }))
  }, [uid])

  useViewPrefsPersistence(uid, view)
}
