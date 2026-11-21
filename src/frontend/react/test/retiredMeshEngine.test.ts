import { afterEach, expect, it } from 'vitest'
import * as THREE from 'three'

import { stateV4 } from '../src/legacy/cfdprojModules'
import { createCfdrunBuild } from '../src/legacy/cfdrunBuild'
import { meshConfigOf, MESH_ENGINES, normalizeRetiredMeshEngine, resolveMeshEngine } from '../src/run/meshConfig'
import * as store from '../src/projectStore'
import { t } from '../src/i18n'
import type { CfdProject } from '../src/schema/cfdproj'

// meshgen (MeshGenerator) was retired on 2026-10-26. The editor's load turns a
// saved `meshEngine: "meshgen"` into dcCart and says so in the operation log;
// the runner and the scripted API refuse it instead.

const savedWith = (meshEngine: string) => {
  const project = stateV4().getInitialStateV4() as CfdProject
  ;(project.runDefaults!.meshConfig as Record<string, unknown>).meshEngine = meshEngine
  return JSON.parse(JSON.stringify(project)) as CfdProject
}

afterEach(() => {
  store.loadProject(stateV4().getInitialStateV4() as CfdProject, null)
})

it('the panel offers only dcCart and snappy', () => {
  expect([...MESH_ENGINES].sort()).toEqual(['dcCart', 'snappy'])
  expect(resolveMeshEngine({ meshEngine: 'meshgen' as never })).toBe('dcCart')
})

it('loading a project saved with meshgen opens on dcCart with a warning in the operation log', () => {
  store.loadProject(savedWith('meshgen'), 'Old.cfdproj')
  expect(meshConfigOf(store.getProject()).meshEngine).toBe('dcCart')
  const warning = store.getLog().find((entry) => entry.detail === t('log.meshgen_retired'))
  expect(warning?.action).toBe('loadProject')
  expect(t('log.meshgen_retired')).toMatch(/2026-10-26/)
  // A migration, not an edit: the file is not dirty and there is nothing to undo.
  expect(store.isDirty()).toBe(false)
  expect(store.canUndo()).toBe(false)
})

it('a project on dcCart or snappy loads unchanged and logs no warning', () => {
  for (const engine of ['dcCart', 'snappy']) {
    store.loadProject(savedWith(engine), 'Current.cfdproj')
    expect(meshConfigOf(store.getProject()).meshEngine).toBe(engine)
    expect(store.getLog().some((entry) => entry.detail === t('log.meshgen_retired'))).toBe(false)
  }
  expect(normalizeRetiredMeshEngine(null)).toBe(false)
})

it('a state that bypassed the load still exports meshgen as is, for the runner to refuse', () => {
  const project = savedWith('meshgen')
  const host = window as unknown as { allModels?: () => unknown[] }
  const previous = host.allModels
  host.allModels = () => stateV4().allModels(project)
  try {
    const builder = createCfdrunBuild({ state: project, globalGroup: new THREE.Group(),
      LENGTH_UNITS: { m: 1, mm: 0.001 }, _MESH_MODES: {} })
    expect((builder.buildCfdrunJson().meshConfig as Record<string, unknown>).meshEngine).toBe('meshgen')
  } finally {
    if (previous) host.allModels = previous
    else delete host.allModels
  }
})
