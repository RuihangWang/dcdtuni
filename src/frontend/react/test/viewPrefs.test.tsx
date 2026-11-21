import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_VIEW_OPTIONS, type ViewOptions } from '../src/components/Toolbar'
import {
  loadViewPrefs,
  saveViewPrefs,
  useViewPrefs,
  useViewPrefsPersistence,
  VIEW_PREFS_DEBOUNCE_MS,
  viewPrefsKey,
} from '../src/ui/viewPrefs'

/**
 * The grid and the snap toggles come back the way the project was left.
 *
 * The legacy keeps them in `localStorage` under `cfdproj.ui.<uid>` and restores
 * them over the file's own `ui` on load (`UI_PREF_KEYS` / `loadUiPrefs`,
 * STL_generate.html:3247-3277, restore at :14968). React reset them to the
 * defaults on every page open, so a user working a room with the grid off and
 * face-snap on had to set both again every morning.
 *
 * ## Why the legacy's own shape, in the legacy's own key
 *
 * Both editors run from the same browser profile against the same projects. A
 * second key would make the two disagree about a setting the user thinks is one
 * setting; and writing this key in a DIFFERENT shape would corrupt the legacy's
 * copy. So the file keeps `view: { showGrid, gridSpacing }` in METRES and the
 * three snap booleans at the top level, exactly as the legacy writes them, and
 * the fields only this port has go under `react`.
 *
 * A save must also PRESERVE what it does not understand: `treeCollapsed`,
 * `groups` and the preview-visibility maps are the legacy's, in the same
 * object, and dropping them would wipe the other editor's state.
 */

const UID = 'proj_abc'

beforeEach(() => {
  localStorage.clear()
})

describe('loadViewPrefs', () => {
  it('returns nothing when the project has never been opened', () => {
    expect(loadViewPrefs(UID)).toEqual({})
  })

  it('reads the legacy shape: metres out of `view`, snaps from the top level', () => {
    localStorage.setItem(
      viewPrefsKey(UID),
      JSON.stringify({
        view: { showGrid: false, gridSpacing: 0.3 },
        snapFace: false,
        snapEdge: true,
        snapGrid: true,
      }),
    )
    expect(loadViewPrefs(UID)).toEqual({
      showGrid: false,
      gridSpacingMm: 300,
      snapFace: false,
      snapEdge: true,
      snapGrid: true,
    })
  })

  it('picks up the two fields only this port has', () => {
    localStorage.setItem(
      viewPrefsKey(UID),
      JSON.stringify({ react: { lights: false, rotationSnap: true } }),
    )
    expect(loadViewPrefs(UID)).toMatchObject({ lights: false, rotationSnap: true })
  })

  it('ignores a corrupt entry rather than throwing', () => {
    localStorage.setItem(viewPrefsKey(UID), '{not json')
    expect(loadViewPrefs(UID)).toEqual({})
  })

  it('has nothing to say without a project', () => {
    expect(loadViewPrefs(null)).toEqual({})
  })
})

describe('saveViewPrefs', () => {
  const view: ViewOptions = {
    ...DEFAULT_VIEW_OPTIONS,
    showGrid: false,
    snapGrid: true,
    gridSpacingMm: 450,
  }

  it('writes the legacy shape, in metres', () => {
    saveViewPrefs(UID, view)
    const stored = JSON.parse(localStorage.getItem(viewPrefsKey(UID)) as string)
    expect(stored.view).toEqual({ showGrid: false, gridSpacing: 0.45 })
    expect(stored.snapGrid).toBe(true)
    expect(stored.react).toEqual({ lights: true, rotationSnap: false })
  })

  it('leaves the legacy editor’s own keys alone', () => {
    localStorage.setItem(
      viewPrefsKey(UID),
      JSON.stringify({ treeCollapsed: { 'face:m_1': true }, groups: [{ id: 'g1' }] }),
    )
    saveViewPrefs(UID, view)
    const stored = JSON.parse(localStorage.getItem(viewPrefsKey(UID)) as string)
    expect(stored.treeCollapsed).toEqual({ 'face:m_1': true })
    expect(stored.groups).toEqual([{ id: 'g1' }])
  })

  it('round-trips', () => {
    saveViewPrefs(UID, view)
    expect(loadViewPrefs(UID)).toMatchObject({
      showGrid: false,
      snapGrid: true,
      gridSpacingMm: 450,
    })
  })

  it('clamps the spacing the way the legacy does', () => {
    // 0.01 .. 1 m. A grid at 5 m is one line on screen and at 1 mm it is a
    // solid wash; the legacy clamps for both reasons.
    saveViewPrefs(UID, { ...view, gridSpacingMm: 9000 })
    expect(loadViewPrefs(UID).gridSpacingMm).toBe(1000)
    saveViewPrefs(UID, { ...view, gridSpacingMm: 1 })
    expect(loadViewPrefs(UID).gridSpacingMm).toBe(10)
  })

  it('does nothing without a project, rather than writing a `default` bucket', () => {
    saveViewPrefs(null, view)
    expect(localStorage.length).toBe(0)
  })
})

describe('restoring when a project opens', () => {
  let host: HTMLDivElement
  let root: Root
  let view: ViewOptions

  function Probe({ uid }: { uid: string | null }) {
    useViewPrefs(uid, view, (next) => {
      view = typeof next === 'function' ? next(view) : next
    })
    return null
  }

  function render(uid: string | null) {
    flushSync(() => root.render(<Probe uid={uid} />))
  }

  beforeEach(() => {
    view = { ...DEFAULT_VIEW_OPTIONS }
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
  })

  afterEach(() => {
    flushSync(() => root.unmount())
    host.remove()
  })

  it('brings the stored prefs back, whatever route opened the project', () => {
    // Restoring inside the file dialog's own handler would leave every OTHER
    // route without it — and `window.__dcdtuni.loadProject`, which is the route
    // every script and the parity harness take, is one of them.
    saveViewPrefs(UID, { ...DEFAULT_VIEW_OPTIONS, showGrid: false, snapGrid: true })
    render(UID)
    expect(view).toMatchObject({ showGrid: false, snapGrid: true })
  })

  it('leaves the session alone for a project it has never seen', () => {
    view = { ...DEFAULT_VIEW_OPTIONS, showGrid: false }
    render('proj_never_opened')
    expect(view.showGrid).toBe(false)
  })

  it('does not undo a toggle made after the restore', () => {
    saveViewPrefs(UID, { ...DEFAULT_VIEW_OPTIONS, showGrid: false })
    render(UID)
    view = { ...view, showGrid: true }
    render(UID)
    expect(view.showGrid).toBe(true)
  })

  it('switches to the other project’s prefs when the project changes', () => {
    saveViewPrefs(UID, { ...DEFAULT_VIEW_OPTIONS, snapGrid: true })
    saveViewPrefs('proj_two', { ...DEFAULT_VIEW_OPTIONS, snapGrid: false, showGrid: false })
    render(UID)
    expect(view.snapGrid).toBe(true)
    render('proj_two')
    expect(view).toMatchObject({ snapGrid: false, showGrid: false })
  })
})

describe('useViewPrefsPersistence', () => {
  let host: HTMLDivElement
  let root: Root

  function Probe({ uid, view }: { uid: string | null; view: ViewOptions }) {
    useViewPrefsPersistence(uid, view)
    return null
  }

  function render(uid: string | null, view: ViewOptions) {
    flushSync(() => root.render(<Probe uid={uid} view={view} />))
  }

  beforeEach(() => {
    vi.useFakeTimers()
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
  })

  afterEach(() => {
    flushSync(() => root.unmount())
    host.remove()
    vi.useRealTimers()
  })

  it('waits before writing — a slider drag is not 40 writes', () => {
    render(UID, { ...DEFAULT_VIEW_OPTIONS, gridSpacingMm: 100 })
    render(UID, { ...DEFAULT_VIEW_OPTIONS, gridSpacingMm: 200 })
    render(UID, { ...DEFAULT_VIEW_OPTIONS, gridSpacingMm: 300 })
    expect(localStorage.getItem(viewPrefsKey(UID))).toBeNull()
    vi.advanceTimersByTime(VIEW_PREFS_DEBOUNCE_MS)
    expect(loadViewPrefs(UID).gridSpacingMm).toBe(300)
  })

  it('flushes on unload, so the last change is not lost with the tab', () => {
    render(UID, { ...DEFAULT_VIEW_OPTIONS, showGrid: false })
    window.dispatchEvent(new Event('beforeunload'))
    expect(loadViewPrefs(UID).showGrid).toBe(false)
  })

  it('writes under the project that is open, not the one that was', () => {
    render(UID, { ...DEFAULT_VIEW_OPTIONS, snapGrid: true })
    vi.advanceTimersByTime(VIEW_PREFS_DEBOUNCE_MS)
    render('proj_other', { ...DEFAULT_VIEW_OPTIONS, snapGrid: false })
    vi.advanceTimersByTime(VIEW_PREFS_DEBOUNCE_MS)
    expect(loadViewPrefs(UID).snapGrid).toBe(true)
    expect(loadViewPrefs('proj_other').snapGrid).toBe(false)
  })
})
