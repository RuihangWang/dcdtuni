import { describe, expect, it } from 'vitest'
import { projectForSave, readProjectFileText, serializeProjectState } from '../src/io/projectFiles'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'

function project(): CfdProject {
  return {
    schemaVersion: 5, uid: 'domain-test', name: 'Domain test',
    geometry: { height: 3, plane: [], tileSize: .6 },
    constructions: { boxes: { b: { uid: 'b', meta: { name: 'Box' }, futureModel: { vendor: 'x' } } } },
    futureDocument: { revision: 7, nested: ['retained'] },
  } as CfdProject
}

describe('domain document codec on real load/save/store paths', () => {
  it('retains a document extension across canonical save and actual file parse', () => {
    const saved = projectForSave(project())
    expect(saved.futureDocument).toEqual({ revision: 7, nested: ['retained'] })
    const loaded = readProjectFileText(serializeProjectState(project()))
    expect(loaded.ok).toBe(true)
    if (loaded.ok) expect((loaded.project as unknown as Record<string, unknown>).futureDocument).toEqual(saved.futureDocument)
  })

  it('retains nested extension fields across ordinary edit/undo/redo', () => {
    store.loadProject(project(), '/owned/domain.cfdproj')
    store.renameModel('b', 'Changed')
    expect(store.getProject()?.constructions?.boxes?.b?.meta.name).toBe('Changed')
    store.undo()
    expect(store.getProject()?.constructions?.boxes?.b?.meta.name).toBe('Box')
    store.redo()
    expect(store.getProject()?.constructions?.boxes?.b?.futureModel).toEqual({ vendor: 'x' })
    expect((store.getProject() as unknown as Record<string, unknown>).futureDocument).toEqual({ revision: 7, nested: ['retained'] })
    expect(Object.getPrototypeOf(store.getProject())).toBe(Object.prototype)
  })
})
