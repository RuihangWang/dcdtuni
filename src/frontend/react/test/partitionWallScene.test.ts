import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { createModel } from '../src/legacy/factories'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'

// A partition wall is built by CSG (its holes are subtracted from the body),
// and geometry_scene.js reads the CSG library off window.ThreeCSG. The React
// app never loaded it, so adding one partition wall threw inside rebuild()
// and took every model after it out of the scene (browser: 77 meshes -> 6).
function projectWith(kinds: string[]): CfdProject {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  for (const kind of kinds) {
    const created = createModel(project, kind)
    ;(stateV4() as unknown as { addConstruction(p: unknown, m: unknown): void }).addConstruction(project, created.model)
  }
  return project
}

function host(project: CfdProject): SceneHost {
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  return {
    globalGroup,
    wrapperGroup,
    registries: emptyRegistries(),
    selectedUid: () => null,
    project: () => project,
    webglAvailable: false,
  }
}

describe('a partition wall in the scene', () => {
  it('the CSG library is installed, on the same THREE the scene uses', () => {
    const csg = (window as unknown as { ThreeCSG?: { CSG?: { fromMesh?: unknown } } }).ThreeCSG
    expect(typeof csg?.CSG?.fromMesh).toBe('function')
  })

  it('builds, and the models around it still build', () => {
    const project = projectWith(['box', 'partitionWall', 'box', 'acu'])
    const h = host(project)
    expect(() => createSceneBuilder(h).rebuild()).not.toThrow()
    expect(h.registries.renderRoots.length).toBe(4)
    const names = h.registries.renderMeshes.map((m) => (m.userData as { exportName?: string }).exportName ?? '')
    expect(names.some((n) => /partition/i.test(n))).toBe(true)
  })
})
