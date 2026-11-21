import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { createModel } from '../src/legacy/factories'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { NO_VIEW_FADES, reapplyViewFades, type ViewFades } from '../src/geometry/viewFades'

/**
 * The view-level fades survive every rebuild.
 *
 * Two knobs fade the MODEL geometry without touching the project: the mesh
 * preview's geometry slider (default 30%) and post-processing's 3D-model
 * opacity. Both write onto the built materials, so every rebuild throws them
 * away — and the rebuild a rack SELECTION triggers (its slot guides,
 * `SceneBuilder.rebuildModels`) re-applied only the post-processing one. On the
 * mesh tab that brought the selected rack back solid, with depth writes on,
 * coincident with the mesh patches: the z-fighting the user saw on Tri_angle
 * (measured: Default_Rack_1 went from 0 to 32 solid materials on one click).
 *
 * The fix is one routine (`reapplyViewFades`) and one place that calls it: the
 * builder's `afterBuild`, which every build path — full, targeted, rack
 * detail, STL preview, a texture landing, an automation verb — goes through.
 */

type Std = THREE.MeshStandardMaterial

function meshes(root: THREE.Object3D): THREE.Mesh[] {
  const out: THREE.Mesh[] = []
  root.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) out.push(o as THREE.Mesh)
  })
  return out
}

function mats(root: THREE.Object3D): Std[] {
  return meshes(root).flatMap((m) => (Array.isArray(m.material) ? m.material : [m.material]) as Std[])
}

function mesh(userData: Record<string, unknown> = {}): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial())
  m.userData = { ...userData }
  return m
}

describe('reapplyViewFades', () => {
  it('fades every model mesh, servers included, under the mesh preview', () => {
    const root = new THREE.Group()
    root.add(mesh({ modelId: 'r' }), mesh({ modelId: 'r', equipmentId: 'e_1' }))
    reapplyViewFades([root], { model: 1, preview: 0.3 })
    for (const m of mats(root)) {
      expect(m.opacity).toBeCloseTo(0.3, 12)
      expect(m.transparent).toBe(true)
      // The z-fight: a coincident surface that still writes depth.
      expect(m.depthWrite).toBe(false)
    }
  })

  it('leaves the mesh preview itself alone — it carries its own opacity', () => {
    const preview = new THREE.Group()
    preview.name = 'meshPreview'
    preview.add(mesh())
    reapplyViewFades([preview], { model: 0.15, preview: 0.3 })
    for (const m of mats(preview)) {
      expect(m.opacity).toBe(1)
      expect(m.depthWrite).toBe(true)
    }
  })

  it('applies the post-processing fade on its own', () => {
    const root = new THREE.Group()
    root.add(mesh({ modelId: 'r' }))
    reapplyViewFades([root], { model: 0.15, preview: 1 })
    const [m] = mats(root)
    expect(m!.opacity).toBeCloseTo(0.15, 12)
    expect(m!.depthWrite).toBe(false)
  })

  it('lets the preview decide while a mesh is shown', () => {
    const root = new THREE.Group()
    root.add(mesh({ modelId: 'r' }))
    reapplyViewFades([root], { model: 0.15, preview: 0.3 })
    expect(mats(root)[0]!.opacity).toBeCloseTo(0.3, 12)
  })

  it('does nothing when no fade is up', () => {
    const root = new THREE.Group()
    const m = mesh({ modelId: 'r' })
    const before = m.material
    root.add(m)
    reapplyViewFades([root], NO_VIEW_FADES)
    expect(m.material).toBe(before)
    expect((before as Std).opacity).toBe(1)
    expect((before as Std).depthWrite).toBe(true)
  })

  it('fades what each slider fades: the scene for post-processing, the roots for the preview', () => {
    // The mesh tab's own helpers (locationInMesh sphere, refinement boxes)
    // hang under the same scene group as the models and must stay solid under
    // the preview; the post-processing slider has always faded the whole group.
    const scene = new THREE.Group()
    const root = new THREE.Group()
    root.add(mesh({ modelId: 'r' }))
    const helper = mesh({ isMeshHelper: true })
    scene.add(root, helper)

    reapplyViewFades([root], { model: 1, preview: 0.3 }, scene)
    expect(mats(root)[0]!.opacity).toBeCloseTo(0.3, 12)
    expect((helper.material as Std).opacity).toBe(1)
    expect((helper.material as Std).depthWrite).toBe(true)

    reapplyViewFades([root], { model: 0.15, preview: 1 }, scene)
    expect((helper.material as Std).opacity).toBeCloseTo(0.15, 12)
  })

  it('skips null roots', () => {
    expect(() => reapplyViewFades([null, undefined], { model: 0.5, preview: 0.3 })).not.toThrow()
  })
})

function projectWith(kinds: string[]): CfdProject {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  for (const kind of kinds) {
    const created = createModel(project, kind)
    ;(stateV4() as unknown as { addConstruction(p: unknown, m: unknown): void }).addConstruction(project, created.model)
  }
  return project
}

describe('the scene builder hands every build to afterBuild', () => {
  function liveHost(project: CfdProject, fades: () => ViewFades) {
    const globalGroup = new THREE.Group()
    const wrapperGroup = new THREE.Group()
    wrapperGroup.add(globalGroup)
    let selected: string | null = null
    const built: THREE.Object3D[][] = []
    const h: SceneHost = {
      globalGroup,
      wrapperGroup,
      registries: emptyRegistries(),
      selectedUid: () => selected,
      project: () => project,
      webglAvailable: false,
      afterBuild: (roots) => {
        built.push([...roots])
        reapplyViewFades(roots, fades())
      },
    }
    return { h, built, select: (uid: string | null) => { selected = uid } }
  }

  const rackUids = (project: CfdProject) =>
    (stateV4().allModels(project) as unknown as { uid: string; kind: string }[])
      .filter((m) => m.kind === 'rack')
      .map((m) => m.uid)

  it('passes the full rebuild every root it made', () => {
    const project = projectWith(['rack', 'box', 'acu'])
    const { h, built } = liveHost(project, () => NO_VIEW_FADES)
    createSceneBuilder(h).rebuild()
    expect(built.length).toBe(1)
    expect(built[0]).toEqual(h.registries.renderRoots)
  })

  it('passes a targeted rebuild only the roots it re-made', () => {
    const project = projectWith(['rack', 'rack', 'box'])
    const [a, b] = rackUids(project)
    const { h, built, select } = liveHost(project, () => NO_VIEW_FADES)
    const builder = createSceneBuilder(h)
    builder.rebuild()
    select(b!)
    builder.rebuildModels([a!, b!])
    expect(built.length).toBe(2)
    expect(built[1]).toEqual([h.registries.renderRootMap.get(a!), h.registries.renderRootMap.get(b!)])
  })

  it('keeps a rack faded when selecting it rebuilds it under the mesh preview', () => {
    // The user's sequence: preview up at 30%, click the rack. The rack is
    // re-made with fresh opacity-1 materials for its slot guides.
    const project = projectWith(['rack', 'rack'])
    const [a] = rackUids(project)
    let fades: ViewFades = NO_VIEW_FADES
    const { h, select } = liveHost(project, () => fades)
    const builder = createSceneBuilder(h)
    builder.rebuild()
    fades = { model: 1, preview: 0.3 }
    for (const root of h.registries.renderRoots) reapplyViewFades([root], fades)

    select(a!)
    builder.rebuildModels([a!])
    const rack = h.registries.renderRootMap.get(a!)!
    const surfaces = meshes(rack).filter(
      (m) => !(m.userData as { isRackSlotGuide?: boolean }).isRackSlotGuide,
    )
    expect(surfaces.length).toBeGreaterThan(0)
    const solid = surfaces
      .flatMap((m) => (Array.isArray(m.material) ? m.material : [m.material]) as Std[])
      .filter((m) => m.opacity >= 0.999 || m.depthWrite)
    expect(solid).toEqual([])
  })
})

/**
 * The viewport's side of the contract: the hook it passes is the one routine,
 * fed from BOTH fades. A source assertion, for the reason in
 * `ppLightIntensity.test.ts` — the builder is created inside an effect that
 * needs a WebGL context.
 */
describe('the viewport wires the hook', () => {
  const viewport = readFileSync(join(__dirname, '..', 'src', 'components', 'Viewport.tsx'), 'utf8')

  it('re-applies both fades after every build', () => {
    const hook = viewport.slice(viewport.indexOf('afterBuild:'), viewport.indexOf('afterBuild:') + 400)
    expect(viewport.indexOf('afterBuild:')).toBeGreaterThan(0)
    expect(hook).toContain('reapplyViewFades(')
    // Both fades come from the one controller that the two sliders write
    // through (`viewFadeController.test.ts`).
    expect(hook).toContain('viewFadesNow()')
    expect(viewport).toMatch(/const viewFadesNow = \(\): ViewFades => fadeCtl\.now\(\)/)
  })

  it('no longer re-applies a fade by hand in the slot-guide rebuild', () => {
    const start = viewport.indexOf('const lastRackRef')
    const block = viewport.slice(start, viewport.indexOf('}, [selectedUid])', start))
    expect(block).toContain('rebuildModels(touched)')
    // One of the two fades used to be hand-applied here, and the other was
    // forgotten. The builder's hook now covers both.
    expect(block).not.toContain('modelOpacityRef')
  })
})
