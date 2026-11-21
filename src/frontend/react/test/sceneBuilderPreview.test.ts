import * as THREE from 'three'
import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { createModel } from '../src/legacy/factories'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { previewCache, previewFlags, resetPreviewForTest } from '../src/io/stlPreview'

/**
 * The scene builder in STL Preview mode, and the render context underneath it.
 *
 * Two separate claims are checked here, and the second is the one that was
 * actually broken: the context used to hand the shared geometry module a
 * FRESH `uiState` object on every read, so `previewRegionReverse` was written
 * into an object nobody ever read again and a reversed solid exported with its
 * original winding.
 */

function projectWith(kinds: string[]): CfdProject {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  for (const kind of kinds) {
    const created = createModel(project, kind)
    ;(stateV4() as unknown as { addConstruction(p: unknown, m: unknown): void }).addConstruction(
      project,
      created.model,
    )
  }
  return project
}

function host(project: CfdProject, preview: boolean): SceneHost {
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  const scene = new THREE.Group()
  scene.add(wrapperGroup)
  wrapperGroup.add(globalGroup)
  return {
    globalGroup,
    wrapperGroup,
    registries: emptyRegistries(),
    selectedUid: () => null,
    project: () => project,
    // jsdom has no 2D canvas, so the procedural textures cannot be built. Every
    // geometry path still runs.
    webglAvailable: false,
    previewMode: () => preview,
  }
}

function uids(project: CfdProject): string[] {
  return (stateV4().allModels(project) as { uid: string }[]).map((m) => m.uid)
}

function exportNames(h: SceneHost): string[] {
  return h.registries.renderMeshes
    .map((m) => (m.userData as { exportName?: string }).exportName)
    .filter((n): n is string => !!n)
}

beforeEach(() => {
  resetPreviewForTest()
})

describe('preview mode', () => {
  it('builds one mesh per exported SOLID, not per face', () => {
    const project = projectWith(['box'])
    const h = host(project, true)
    createSceneBuilder(h).rebuild()
    const names = exportNames(h)
    expect(names.length).toBeGreaterThan(0)
    // Distinct: one mesh per solid means no name repeats.
    expect(new Set(names).size).toBe(names.length)
  })

  it('draws exactly the solids the exporter would write', () => {
    const project = projectWith(['box', 'acu'])
    const normal = host(project, false)
    const builder = createSceneBuilder(normal)
    builder.rebuild()
    const exported = new Set<string>()
    for (const text of Object.values(builder.api.buildGroupedStlMap(uids(project), false))) {
      for (const m of text.matchAll(/^solid (.+)$/gm)) exported.add(m[1]!.trim())
    }

    const preview = host(project, true)
    createSceneBuilder(preview).rebuild()
    expect(new Set(exportNames(preview))).toEqual(exported)
  })

  it('fills the cache the tree and the panels read', () => {
    const project = projectWith(['box'])
    createSceneBuilder(host(project, true)).rebuild()
    const entry = previewCache().get(uids(project)[0]!)
    expect(entry).toBeDefined()
    expect(entry!.regions.length).toBeGreaterThan(0)
    expect(entry!.triangleCount).toBeGreaterThan(0)
  })

  it('hangs the preview off globalGroup, where the viewport keeps looking', () => {
    // Not off `wrapperGroup.parent`: the viewport re-parents globalGroup into a
    // fresh scene when its setup effect re-runs but keeps ONE builder, so a
    // root placed relative to the wrapper lands in a scene that is no longer
    // rendered — 52 meshes built, an empty viewport, and no error anywhere.
    const project = projectWith(['box'])
    const h = host(project, true)
    createSceneBuilder(h).rebuild()
    expect(h.registries.renderRoots[0]!.parent).toBe(h.globalGroup)
  })

  it('cancels the transform above it, because the STL is already world-space', () => {
    // The exporter writes WORLD coordinates — under a mirrored or turned scene
    // it writes the transformed ones. Inheriting that transform again would
    // apply it twice and put the preview where no exported file is.
    const project = projectWith(['box'])
    const turned = host(project, true)
    turned.wrapperGroup.rotation.y = Math.PI / 3
    turned.wrapperGroup.scale.x = -1
    turned.wrapperGroup.updateMatrixWorld(true)
    createSceneBuilder(turned).rebuild()

    const root = turned.registries.renderRoots[0]!
    root.updateMatrixWorld(true)
    const identity = new THREE.Matrix4()
    root.matrixWorld.elements.forEach((v, i) => {
      expect(v).toBeCloseTo(identity.elements[i]!, 6)
    })
  })

  it('hides a region whose flag is off', () => {
    const project = projectWith(['box'])
    const uid = uids(project)[0]!
    const probe = host(project, true)
    createSceneBuilder(probe).rebuild()
    const first = previewCache().get(uid)!.regions[0]!.name
    const before = exportNames(probe).length

    previewFlags().regionVisibility[uid] = { [first]: false }
    const h = host(project, true)
    createSceneBuilder(h).rebuild()
    const after = exportNames(h)
    expect(after.length).toBe(before - 1)
    expect(after).not.toContain(first)
  })

  it('leaves the normal build alone', () => {
    const project = projectWith(['box'])
    const h = host(project, false)
    createSceneBuilder(h).rebuild()
    expect(h.registries.renderRoots[0]!.parent).toBe(h.globalGroup)
  })
})

describe('the render context', () => {
  it('lets the shared module WRITE its preview defaults back', () => {
    // `ensurePreviewVisibilityState` seeds one entry per model. A throwaway
    // uiState object swallowed every one of those writes.
    const project = projectWith(['box', 'box'])
    const builder = createSceneBuilder(host(project, false))
    builder.rebuild()
    builder.api.buildGroupedStlMap(uids(project), false)
    expect(Object.keys(previewFlags().visibility).sort()).toEqual(uids(project).sort())
  })

  it('a reversed region changes the EXPORTED bytes, not just the preview', () => {
    const project = projectWith(['box'])
    const uid = uids(project)[0]!
    const builder = createSceneBuilder(host(project, false))
    builder.rebuild()

    const before = builder.api.buildGroupedStlMap([uid], false)
    const file = Object.keys(before)[0]!
    const solidName = /^solid (.+)$/m.exec(before[file]!)![1]!.trim()

    previewFlags().regionReverse[uid] = { [solidName]: true }
    const after = builder.api.buildGroupedStlMap([uid], false)

    expect(after[file]).not.toBe(before[file])
    // Same triangles, opposite winding: a reversed normal must not add or drop
    // a facet, or it is a different surface rather than the same one flipped.
    const facets = (text: string) => (text.match(/facet normal/g) ?? []).length
    expect(facets(after[file]!)).toBe(facets(before[file]!))
  })
})
