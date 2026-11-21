import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { setLocale, t } from '../src/i18n'
import type { CfdProject } from '../src/schema/cfdproj'
import type { TreeSelection } from '../src/interaction/treeNodes'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel, createPipeJunction, createPipeSegment } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import {
  gizmoShortcutBlocked,
  setGroupMoveEnabled,
  setGroupRotateEnabled,
  toggleSelectionGizmo,
} from '../src/interaction/transformToolToggle'

/**
 * What R and V do to the selection.
 *
 * "Activate" is the same store call the inspector's buttons make — one undo
 * entry, the same move/rotate exclusion — so a key and a click can never leave
 * the model in different states. Pressed again, the key turns the gizmo off.
 */

type Tools = {
  moveTool?: { enabled?: boolean }
  rotateTool?: { enabled?: boolean }
}

function project(): CfdProject {
  return store.getProject() as CfdProject
}

function add(kind: string): string {
  const created =
    kind === 'pipe_segment'
      ? createPipeSegment(project())
      : kind === 'pipe_junction'
        ? createPipeJunction(project())
        : createModel(project(), kind)
  return store.addModel(created.bucket, created.model)
}

function tools(uid: string): Tools {
  return store.findConstruction(project(), uid)!.model as unknown as Tools
}

const moveOn = (uid: string) => tools(uid).moveTool?.enabled === true
const rotateOn = (uid: string) => tools(uid).rotateTool?.enabled === true

function press(kind: 'move' | 'rotate', uids: string[], node: TreeSelection | null = null) {
  return toggleSelectionGizmo(kind, { project: store.getProject(), uids, node })
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
})

describe('one selected model', () => {
  it('V turns the move gizmo on, and V again turns it off', () => {
    const uid = add('box')
    expect(press('move', [uid])).toMatchObject({ ok: true, enabled: true })
    expect(moveOn(uid)).toBe(true)
    expect(press('move', [uid])).toMatchObject({ ok: true, enabled: false })
    expect(moveOn(uid)).toBe(false)
  })

  it('is one undo entry per press, exactly as the button is', () => {
    const uid = add('rack')
    const depth = store.undoDepth()
    press('move', [uid])
    expect(store.undoDepth()).toBe(depth + 1)
    store.undo()
    expect(moveOn(uid)).toBe(false)
  })

  it('R turns the rings on and puts the arrows away', () => {
    const uid = add('acu')
    press('move', [uid])
    press('rotate', [uid])
    expect(rotateOn(uid)).toBe(true)
    expect(moveOn(uid)).toBe(false)
    press('rotate', [uid])
    expect(rotateOn(uid)).toBe(false)
  })

  it('acts on the model when one of its faces is what is selected', () => {
    const uid = add('box')
    const face: TreeSelection = { type: 'face', uid, faceId: 'top' }
    expect(press('move', [uid], face).ok).toBe(true)
    expect(moveOn(uid)).toBe(true)
  })

  it('does nothing on a patch or on a server inside a rack — the panel offers no gizmo there', () => {
    const box = add('box')
    const patch: TreeSelection = { type: 'patch', uid: box, faceId: 'front', patchId: 'p1' }
    const depth = store.undoDepth()
    expect(press('move', [box], patch)).toEqual({ ok: false, reason: 'unsupported' })
    const rack = add('rack')
    const server: TreeSelection = { type: 'equipment', uid: rack, equipmentId: 'e1' }
    expect(press('rotate', [rack], server)).toEqual({ ok: false, reason: 'unsupported' })
    expect(store.undoDepth()).toBe(depth + 1) // the rack add only
    expect(moveOn(box)).toBe(false)
    expect(rotateOn(rack)).toBe(false)
  })

  it('does nothing for a kind without that gizmo', () => {
    const pipe = add('pipe_segment')
    const junction = add('pipe_junction')
    const depth = store.undoDepth()
    expect(press('move', [pipe])).toEqual({ ok: false, reason: 'unsupported' })
    expect(press('rotate', [pipe])).toEqual({ ok: false, reason: 'unsupported' })
    expect(press('rotate', [junction])).toEqual({ ok: false, reason: 'unsupported' })
    expect(store.undoDepth()).toBe(depth)
    // …but a junction does move.
    expect(press('move', [junction]).ok).toBe(true)
    expect(moveOn(junction)).toBe(true)
  })

  describe('says so when it cannot act, and leaves a line in the log', () => {
    afterEach(() => setLocale('zh-CN'))

    it.each(['zh-CN', 'en-US'] as const)('in %s, translated', (locale) => {
      setLocale(locale)
      const pipe = add('pipe_segment')
      const name = store.findConstruction(project(), pipe)!.model.meta?.name ?? pipe
      const before = store.getLog().length
      press('rotate', [pipe])
      expect(store.getLog().length).toBe(before + 1)
      // Newest first.
      expect(store.getLog()[0]!.detail).toBe(t('log.rotate_gizmo_unsupported', { name }))
      press('move', [pipe])
      expect(store.getLog()[0]!.detail).toBe(t('log.move_gizmo_unsupported', { name }))
      expect(t('log.move_gizmo_unsupported', { name })).toContain(name)
    })

    it('the two languages say it differently', () => {
      setLocale('zh-CN')
      const zh = t('log.rotate_gizmo_unsupported', { name: 'P' })
      setLocale('en-US')
      expect(t('log.rotate_gizmo_unsupported', { name: 'P' })).not.toBe(zh)
    })
  })

  it('does nothing with no selection', () => {
    expect(press('move', [])).toEqual({ ok: false, reason: 'no-selection' })
    expect(press('move', ['m_missing'])).toEqual({ ok: false, reason: 'no-selection' })
    expect(toggleSelectionGizmo('move', { project: null, uids: ['x'], node: null })).toEqual({
      ok: false,
      reason: 'no-selection',
    })
  })
})

describe('several selected models', () => {
  it('uses the batch toggle: all on, then all off, one undo entry each', () => {
    const a = add('box')
    const b = add('acu')
    const depth = store.undoDepth()
    expect(press('move', [a, b])).toMatchObject({ ok: true, enabled: true })
    expect(moveOn(a) && moveOn(b)).toBe(true)
    expect(store.undoDepth()).toBe(depth + 1)
    press('move', [a, b])
    expect(moveOn(a) || moveOn(b)).toBe(false)
  })

  it('turns everything off when ANY model has it on — what the batch button shows', () => {
    const a = add('box')
    const b = add('box')
    store.setMoveTool(a, { enabled: true })
    expect(press('move', [a, b])).toMatchObject({ ok: true, enabled: false })
    expect(moveOn(a) || moveOn(b)).toBe(false)
  })

  it('rotate puts the arrows away on the selection', () => {
    const a = add('rack')
    const b = add('rack')
    press('move', [a, b])
    press('rotate', [a, b])
    expect(rotateOn(a) && rotateOn(b)).toBe(true)
    expect(moveOn(a) || moveOn(b)).toBe(false)
  })

  it('does nothing when no model in the selection has that gizmo', () => {
    const p = add('pipe_segment')
    const q = add('pipe_segment')
    expect(press('rotate', [p, q])).toEqual({ ok: false, reason: 'unsupported' })
    expect(rotateOn(p) || rotateOn(q)).toBe(false)
  })
})

describe('the batch setters', () => {
  it('flip the flag on every selected model and clear the other tool there only', () => {
    const a = add('box')
    const b = add('box')
    const outside = add('box')
    store.setMoveTool(outside, { enabled: true })
    setGroupRotateEnabled([a, b], true)
    expect(rotateOn(a) && rotateOn(b)).toBe(true)
    // The multi-select button never reached outside the selection.
    expect(moveOn(outside)).toBe(true)
    setGroupMoveEnabled([a, b], true)
    expect(moveOn(a) && moveOn(b)).toBe(true)
    expect(rotateOn(a) || rotateOn(b)).toBe(false)
  })
})

describe('gizmoShortcutBlocked', () => {
  const open = {
    firstPerson: false,
    room2d: false,
    preview: false,
    rackDetail: false,
    dragging: false,
    tab: 'geometry',
  }

  it('is open on the geometry tab with nothing else in the way', () => {
    expect(gizmoShortcutBlocked(open)).toBe(false)
  })

  it('is blocked in first person, the 2D editor, STL preview, rack detail, or off the geometry tab', () => {
    expect(gizmoShortcutBlocked({ ...open, firstPerson: true })).toBe(true)
    expect(gizmoShortcutBlocked({ ...open, room2d: true })).toBe(true)
    expect(gizmoShortcutBlocked({ ...open, preview: true })).toBe(true)
    expect(gizmoShortcutBlocked({ ...open, rackDetail: true })).toBe(true)
    expect(gizmoShortcutBlocked({ ...open, tab: 'mesh' })).toBe(true)
  })

  it('is blocked while a viewport drag is live', () => {
    // A toggle mid-drag is a store.edit: the undo stack would take the
    // uncommitted mid-drag position, and the drag would keep writing into
    // the snapshot the edit replaced.
    expect(gizmoShortcutBlocked({ ...open, dragging: true })).toBe(true)
  })
})
