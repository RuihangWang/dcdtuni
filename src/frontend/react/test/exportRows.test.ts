import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import { anyLeakage, exportRowBadges, rememberExportFileName } from '../src/io/exportRows'

/**
 * The dialog's two badges answer questions a model NAME cannot: does this rack
 * export extra leakage solids (so "include leakage" changes the output), and is
 * this model hidden in the viewport but exported anyway.
 */

function rack(uid: string, opts: { hidden?: boolean; throughUnits?: number[] } = {}): Construction {
  // A through zone is a U with BOTH doors deleted, which is what
  // getRackThroughZoneGroups scans for (js/stl_naming.js:155).
  const faces: Record<string, unknown> = {}
  for (const u of opts.throughUnits ?? []) {
    faces[`front_${u}`] = { role: 'door', unit: u, deleted: true, side: 'front' }
    faces[`back_${u}`] = { role: 'door', unit: u, deleted: true, side: 'back' }
  }
  return {
    uid,
    kind: 'rack',
    meta: { name: uid, enabled: opts.hidden ? false : true },
    geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 0.6, y: 2, z: 1 }, faces },
  } as unknown as Construction
}

function project(): CfdProject {
  return {
    schemaVersion: 4,
    name: 'T',
    meta: { exportFileName: 'default' },
    constructions: { racks: {} },
  } as CfdProject
}

beforeEach(() => store.loadProject(project(), '/cases/T.cfdproj'))

describe('exportRowBadges', () => {
  it('reports no leak groups for a rack with no through zones', () => {
    expect(exportRowBadges(rack('R1')).leakGroups).toBe(0)
  })

  it('flags a hidden model, which is exported regardless', () => {
    // meta.enabled is 3D VISIBILITY, not a data flag: a hidden rack still
    // reaches the mesh, so the dialog says so rather than implying it does not.
    expect(exportRowBadges(rack('R1', { hidden: true })).hidden).toBe(true)
    expect(exportRowBadges(rack('R2')).hidden).toBe(false)
  })

  it('counts nothing for a non-rack, which has no through zones at all', () => {
    const acu = { uid: 'A1', kind: 'acu', meta: { name: 'ACU_1' } } as unknown as Construction
    expect(exportRowBadges(acu)).toEqual({ leakGroups: 0, hidden: false })
  })

  it('anyLeakage is false for a room with no leaky rack', () => {
    expect(anyLeakage([rack('R1'), rack('R2', { hidden: true })])).toBe(false)
  })
})

describe('rememberExportFileName', () => {
  it('persists the typed name so the next export starts from it', () => {
    rememberExportFileName('  MyRoom  ')
    const meta = store.getProject()?.meta as { exportFileName?: string }
    expect(meta.exportFileName).toBe('MyRoom')
  })

  it('is not undoable: Ctrl+Z after an export must not undo the filename', () => {
    const depth = store.undoDepth()
    rememberExportFileName('MyRoom')
    expect(store.undoDepth()).toBe(depth)
  })

  it('ignores an empty box rather than storing a blank name', () => {
    rememberExportFileName('   ')
    const meta = store.getProject()?.meta as { exportFileName?: string }
    expect(meta.exportFileName).toBe('default')
  })
})
