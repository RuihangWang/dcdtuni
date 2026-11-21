import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as groups from '../src/interaction/groupStore'
import * as store from '../src/projectStore'

function project(uid = 'p_groups'): CfdProject {
  return {
    uid,
    name: 'T',
    constructions: {
      racks: {
        r1: { uid: 'r1', meta: { name: 'Rack_1' } },
        r2: { uid: 'r2', meta: { name: 'Rack_2' } },
        r3: { uid: 'r3', meta: { name: 'Rack_3' } },
      },
    },
  }
}

beforeEach(() => {
  localStorage.clear()
  groups.resetGroupsForTest()
  store.loadProject(project(), null)
  groups.syncGroups()
})

describe('persistence', () => {
  it('writes the groups into the legacy UI-prefs blob, under its own key', () => {
    groups.createModelGroup('Row A', ['r1', 'r2'])
    const raw = JSON.parse(localStorage.getItem('cfdproj.ui.p_groups')!) as Record<string, unknown>
    expect((raw.groups as { name: string }[])[0]!.name).toBe('Row A')
  })

  it('keeps the other view preferences in the blob', () => {
    // The blob is shared with treeCollapsed / previewVisibility, and the legacy
    // app writes the whole thing. Replacing it here would wipe those.
    localStorage.setItem(
      'cfdproj.ui.p_groups',
      JSON.stringify({ treeCollapsed: { 'cat:racks': true }, snapGrid: true }),
    )
    groups.createModelGroup('Row A', ['r1', 'r2'])
    const raw = JSON.parse(localStorage.getItem('cfdproj.ui.p_groups')!) as Record<string, unknown>
    expect(raw.treeCollapsed).toEqual({ 'cat:racks': true })
    expect(raw.snapGrid).toBe(true)
  })

  it('reads back what the legacy editor stored for the same project uid', () => {
    localStorage.setItem(
      'cfdproj.ui.p_other',
      JSON.stringify({ groups: [{ id: 'g_legacy', name: 'From HTML', memberIds: ['r1', 'r3'] }] }),
    )
    store.loadProject(project('p_other'), null)
    groups.syncGroups()
    expect(groups.getGroups()).toEqual([
      { id: 'g_legacy', name: 'From HTML', memberIds: ['r1', 'r3'] },
    ])
  })

  it('drops a stored member whose model is no longer in the project', () => {
    localStorage.setItem(
      'cfdproj.ui.p_other',
      JSON.stringify({
        groups: [{ id: 'g1', name: 'Stale', memberIds: ['r1', 'r2', 'deleted_last_week'] }],
      }),
    )
    store.loadProject(project('p_other'), null)
    groups.syncGroups()
    expect(groups.getGroups()[0]!.memberIds).toEqual(['r1', 'r2'])
  })

  it('is not stored in the .cfdproj — the V4 document has no home for it', () => {
    groups.createModelGroup('Row A', ['r1', 'r2'])
    expect(JSON.stringify(store.getProject())).not.toContain('Row A')
  })
})

describe('mutations', () => {
  it('creates, renames, adds to and dissolves a group', () => {
    const id = groups.createModelGroup('Row A', ['r1', 'r2'])
    expect(groups.getGroups()[0]!.memberIds).toEqual(['r1', 'r2'])
    groups.renameModelGroup(id, 'Row B')
    expect(groups.getGroups()[0]!.name).toBe('Row B')
    groups.addModelsToGroup(id, ['r3'])
    expect(groups.getGroups()[0]!.memberIds).toEqual(['r1', 'r2', 'r3'])
    groups.removeModelFromGroup('r3')
    expect(groups.getGroups()[0]!.memberIds).toEqual(['r1', 'r2'])
    groups.dissolveModelGroup(id)
    expect(groups.getGroups()).toEqual([])
  })

  it('logs each operation without dirtying the project', () => {
    const before = store.isDirty()
    groups.createModelGroup('Row A', ['r1', 'r2'])
    expect(store.getLog()[0]!.action).toBe('createGroup')
    expect(store.isDirty()).toBe(before)
    expect(store.canUndo()).toBe(false)
  })

  it('notifies subscribers on a change and not on a no-op rename', () => {
    let hits = 0
    const off = groups.subscribeGroups(() => {
      hits += 1
    })
    const id = groups.createModelGroup('Row A', ['r1', 'r2'])
    expect(hits).toBe(1)
    groups.renameModelGroup(id, 'Row A')
    expect(hits).toBe(1)
    off()
  })
})

describe('syncGroups', () => {
  it('drops a deleted model from its group and dissolves what is left', () => {
    groups.createModelGroup('Row A', ['r1', 'r2'])
    store.removeModel('r2')
    groups.syncGroups()
    expect(groups.getGroups()).toEqual([])
  })

  it('keeps a group of three when one member is deleted', () => {
    groups.createModelGroup('Row A', ['r1', 'r2', 'r3'])
    store.removeModel('r2')
    groups.syncGroups()
    expect(groups.getGroups()[0]!.memberIds).toEqual(['r1', 'r3'])
  })

  it('does not put a member back when its deletion is undone', () => {
    // The prune is destructive and undo does not cover it, exactly as in the
    // legacy: `removeModel` filters `memberIds` (STL_generate.html:13838) while
    // the undo snapshot holds only `state`, never `uiState`. Asserted so the
    // behaviour is a decision rather than a surprise.
    groups.createModelGroup('Row A', ['r1', 'r2', 'r3'])
    store.removeModel('r2')
    groups.syncGroups()
    store.undo()
    groups.syncGroups()
    expect(groups.getGroups()[0]!.memberIds).toEqual(['r1', 'r3'])
  })

  it('swaps the whole list when a different project is loaded', () => {
    groups.createModelGroup('Row A', ['r1', 'r2'])
    store.loadProject(project('p_second'), null)
    groups.syncGroups()
    expect(groups.getGroups()).toEqual([])
    store.loadProject(project('p_groups'), null)
    groups.syncGroups()
    expect(groups.getGroups()[0]!.name).toBe('Row A')
  })
})
