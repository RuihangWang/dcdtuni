import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import { projectName, setProjectName } from '../src/io/projectMeta'

function project(): CfdProject {
  return {
    schemaVersion: 4,
    uid: 'proj_T',
    name: 'Base_leakage',
    meta: { projectName: 'Base_leakage', exportFileName: 'default' },
    constructions: { racks: {} },
  } as CfdProject
}

beforeEach(() => store.loadProject(project(), '/cases/T.cfdproj'))

describe('setProjectName', () => {
  it('writes meta.projectName', () => {
    setProjectName('Base_leakage_v2')
    expect(projectName(store.getProject())).toBe('Base_leakage_v2')
  })

  it('leaves the document root `name` alone', () => {
    // The legacy setProjectName (STL_generate.html:13242) touches only
    // meta.projectName, and the root field is the migrator's. Writing both here
    // would make the two editors save different bytes for the same edit.
    setProjectName('Renamed')
    expect(store.getProject()?.name).toBe('Base_leakage')
  })

  it('trims, so a stray space does not become part of the save filename', () => {
    setProjectName('  Spaced  ')
    expect(projectName(store.getProject())).toBe('Spaced')
  })

  it('accepts an empty name', () => {
    setProjectName('')
    expect(projectName(store.getProject())).toBe('')
  })

  it('takes one undo checkpoint, so a rename is undone in one press', () => {
    const depth = store.undoDepth()
    setProjectName('Renamed')
    expect(store.undoDepth()).toBe(depth + 1)
    store.undo()
    expect(projectName(store.getProject())).toBe('Base_leakage')
  })

  it('creates meta when the document has none', () => {
    store.loadProject({ uid: 'p', constructions: {} } as CfdProject, null)
    setProjectName('Fresh')
    expect(projectName(store.getProject())).toBe('Fresh')
  })
})

describe('projectName', () => {
  it('is empty rather than undefined for a document without one', () => {
    expect(projectName(null)).toBe('')
    expect(projectName({ meta: {} } as CfdProject)).toBe('')
    expect(projectName({ meta: { projectName: 42 } } as unknown as CfdProject)).toBe('')
  })
})
