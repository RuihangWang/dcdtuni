import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import type { TreeSelection } from '../src/interaction/treeNodes'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import { createGizmoShortcutHost } from '../src/interaction/gizmoShortcutHost'
import { claimRoom2dPanel, releaseRoom2dPanel } from '../src/room2d/room2dPanelSlot'
import { resetPreviewForTest, setPreviewActive } from '../src/io/stlPreview'
import { enterRackDetail, exitRackDetail } from '../src/geometry/rackDetail'

/**
 * The R / V host the viewport installs: WHERE the keys are off, read from the
 * modules that own each mode, and WHICH selection they act on, read at the
 * moment of the key press.
 *
 * The pure rule (`gizmoShortcutBlocked`) is tested on its own; this is the
 * wiring — a host that passed a constant tab, dropped a flag, or captured the
 * selection at install time would pass that test and still be wrong.
 */

type Win = { _fpsActive?: boolean; fpsMode?: { isActive(): boolean } }

let tab: string
let uids: string[]
let node: TreeSelection | null
let dragging: boolean

function host() {
  return createGizmoShortcutHost({
    activeTab: () => tab,
    selectedUids: () => uids,
    node: () => node,
    dragging: () => dragging,
  })
}

function add(kind: string): string {
  const created = createModel(store.getProject() as CfdProject, kind)
  return store.addModel(created.bucket, created.model)
}

const moveOn = (uid: string) =>
  (store.findConstruction(store.getProject()!, uid)!.model as unknown as {
    moveTool?: { enabled?: boolean }
  }).moveTool?.enabled === true

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  tab = 'geometry'
  uids = []
  node = null
  dragging = false
})

afterEach(() => {
  const w = window as unknown as Win
  delete w._fpsActive
  delete w.fpsMode
  resetPreviewForTest()
  exitRackDetail()
})

describe('gizmoShortcutsBlocked', () => {
  it('is open on the geometry tab with nothing else in the way', () => {
    expect(host().gizmoShortcutsBlocked()).toBe(false)
  })

  it('reads the tab at the key press, not when it was installed', () => {
    const h = host()
    tab = 'mesh'
    expect(h.gizmoShortcutsBlocked()).toBe(true)
    tab = 'geometry'
    expect(h.gizmoShortcutsBlocked()).toBe(false)
  })

  it('is blocked in first-person mode, by either of its flags', () => {
    const h = host()
    const w = window as unknown as Win
    w._fpsActive = true
    expect(h.gizmoShortcutsBlocked()).toBe(true)
    delete w._fpsActive
    w.fpsMode = { isActive: () => true }
    expect(h.gizmoShortcutsBlocked()).toBe(true)
    w.fpsMode = { isActive: () => false }
    expect(h.gizmoShortcutsBlocked()).toBe(false)
  })

  it('is blocked while the 2D room editor holds the right column', () => {
    const h = host()
    const token = {}
    claimRoom2dPanel(token)
    try {
      expect(h.gizmoShortcutsBlocked()).toBe(true)
    } finally {
      releaseRoom2dPanel(token)
    }
    expect(h.gizmoShortcutsBlocked()).toBe(false)
  })

  it('is blocked in STL preview and in rack detail', () => {
    const h = host()
    setPreviewActive(true)
    expect(h.gizmoShortcutsBlocked()).toBe(true)
    setPreviewActive(false)
    const rack = add('rack')
    enterRackDetail(rack)
    expect(h.gizmoShortcutsBlocked()).toBe(true)
    exitRackDetail()
    expect(h.gizmoShortcutsBlocked()).toBe(false)
  })

  it('is blocked while a viewport drag is live', () => {
    const h = host()
    dragging = true
    expect(h.gizmoShortcutsBlocked()).toBe(true)
  })
})

describe('toggleGizmo', () => {
  it('acts on the selection as it is at the key press', () => {
    const a = add('box')
    const b = add('box')
    const h = host()
    uids = [a]
    h.toggleGizmo('move')
    expect(moveOn(a)).toBe(true)
    uids = [b]
    h.toggleGizmo('move')
    expect(moveOn(b)).toBe(true)
    expect(moveOn(a)).toBe(true)
  })

  it('reads the narrower node too: a patch has no gizmo, so the key does nothing', () => {
    const box = add('box')
    const h = host()
    uids = [box]
    node = { type: 'patch', uid: box, faceId: 'front', patchId: 'p1' }
    const depth = store.undoDepth()
    h.toggleGizmo('move')
    expect(moveOn(box)).toBe(false)
    expect(store.undoDepth()).toBe(depth)
    node = { type: 'face', uid: box, faceId: 'top' }
    h.toggleGizmo('move')
    expect(moveOn(box)).toBe(true)
  })

  it('does nothing with nothing selected', () => {
    const h = host()
    const depth = store.undoDepth()
    h.toggleGizmo('rotate')
    expect(store.undoDepth()).toBe(depth)
  })
})
