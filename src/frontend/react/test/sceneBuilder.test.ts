import * as THREE from 'three'
import { describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { createModel } from '../src/legacy/factories'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'

/**
 * The scene builder, driven over a real project.
 *
 * The point of these is that the meshes carry `exportName`: that string becomes
 * the OpenFOAM patch name, so a scene that builds beautifully but tags nothing
 * exports a file the solver reads as having no boundaries.
 */

function projectWith(kinds: string[]): CfdProject {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  for (const kind of kinds) {
    const created = createModel(project, kind)
    ;(stateV4() as unknown as { addConstruction(p: unknown, m: unknown): void }).addConstruction(project, created.model)
  }
  return project
}

function host(project: CfdProject, selected: string | null = null): SceneHost {
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  return {
    globalGroup,
    wrapperGroup,
    registries: emptyRegistries(),
    selectedUid: () => selected,
    project: () => project,
    // jsdom has no 2D canvas — `getContext('2d')` returns null and the first
    // `fillStyle` assignment inside the procedural texture generators throws.
    // Every geometry path below still runs; only the decoration is off.
    webglAvailable: false,
  }
}

function exportNames(h: SceneHost): string[] {
  return h.registries.renderMeshes
    .map((m) => (m.userData as { exportName?: string }).exportName)
    .filter((n): n is string => !!n)
}

describe('building a scene', () => {
  it('creates one root group per enabled model', () => {
    const project = projectWith(['box', 'box', 'acu'])
    const h = host(project)
    createSceneBuilder(h).rebuild()
    expect(h.registries.renderRoots.length).toBe(3)
    expect(h.registries.renderRootMap.size).toBe(3)
    expect(h.globalGroup.children.length).toBe(3)
  })

  it('skips disabled models entirely', () => {
    const project = projectWith(['box', 'box'])
    const models = stateV4().allModels(project) as unknown as { meta: { enabled: boolean } }[]
    models[0]!.meta.enabled = false
    const h = host(project)
    createSceneBuilder(h).rebuild()
    // Hidden, not dimmed — a disabled model must not reach the export either.
    expect(h.registries.renderRoots.length).toBe(1)
  })

  it('tags every mesh with an export name', () => {
    const project = projectWith(['box', 'acu', 'wall'])
    const h = host(project)
    createSceneBuilder(h).rebuild()
    const meshes = h.registries.renderMeshes
    expect(meshes.length).toBeGreaterThan(0)
    expect(exportNames(h).length).toBe(meshes.length)
  })

  it('gives every solid a DISTINCT name', () => {
    // Two solids sharing a name collapse into one OpenFOAM patch, and the
    // second one's boundary condition is silently lost.
    const project = projectWith(['box', 'box', 'acu'])
    const h = host(project)
    createSceneBuilder(h).rebuild()
    const names = exportNames(h)
    expect(new Set(names).size).toBe(names.length)
  })

  it('builds a rack, which is the hardest kind', () => {
    // Racks pull in U-slots, server faces, blanking panels and the frame — the
    // path that needs polygon-clipping and rack_state together.
    const project = projectWith(['rack'])
    const h = host(project)
    createSceneBuilder(h).rebuild()
    expect(h.registries.renderMeshes.length).toBeGreaterThan(4)
    expect(exportNames(h).some((n) => /frame/i.test(n))).toBe(true)
  })

  it('rebuilds to the same result rather than accumulating', () => {
    const project = projectWith(['box', 'rack'])
    const h = host(project)
    const b = createSceneBuilder(h)
    b.rebuild()
    const first = h.registries.renderMeshes.length
    b.rebuild()
    b.rebuild()
    expect(h.registries.renderMeshes.length).toBe(first)
    expect(h.globalGroup.children.length).toBe(2)
  })

  it('clears back to empty', () => {
    const project = projectWith(['box', 'rack'])
    const h = host(project)
    const b = createSceneBuilder(h)
    b.rebuild()
    b.clear()
    expect(h.registries.renderMeshes).toEqual([])
    expect(h.registries.renderRoots).toEqual([])
    expect(h.globalGroup.children.length).toBe(0)
  })

  it('places a model at its MIN-CORNER anchor', () => {
    // geometry.location is the min corner, not the centre. Centring on it sinks
    // every wall half its height below the floor.
    const project = projectWith(['box'])
    const model = (stateV4().allModels(project) as unknown as { geometry: { location: Record<string, number> } }[])[0]!
    model.geometry.location = { x: 3, y: 0, z: -2 }
    const h = host(project)
    createSceneBuilder(h).rebuild()
    const root = h.registries.renderRoots[0] as THREE.Object3D
    expect(root.position.toArray()).toEqual([3, 0, -2])
  })

  it('survives an empty project', () => {
    const h = host(stateV4().getInitialStateV4() as unknown as CfdProject)
    const b = createSceneBuilder(h)
    expect(() => b.rebuild()).not.toThrow()
    expect(h.registries.renderMeshes).toEqual([])
  })
})

describe('liquid equipment visual details', () => {
  const detailRoots = (root: THREE.Object3D) => {
    const found: THREE.Object3D[] = []
    root.traverse((child) => {
      if (child.userData.isLiquidEquipmentDetail === true) found.push(child)
    })
    return found
  }

  it('keeps CDU detail in both modes and adds rack liquid detail without changing exports', () => {
    const project = projectWith(['cdu', 'rack'])
    const rack = (stateV4().allModels(project) as unknown as Array<{
      kind: string
      cooling: { type: string }
    }>).find((model) => model.kind === 'rack')!
    rack.cooling.type = 'liquid'
    setLiquidModeActive(false)
    const air = host(project)
    createSceneBuilder(air).rebuild()
    const airExports = exportNames(air)
    expect(detailRoots(air.globalGroup)).toHaveLength(1)

    setLiquidModeActive(true)
    const liquid = host(project)
    createSceneBuilder(liquid).rebuild()
    const details = detailRoots(liquid.globalGroup)
    const detailMeshes = details.flatMap((root) => {
      const found: THREE.Object3D[] = []
      root.traverse((child) => { if ((child as THREE.Mesh).isMesh) found.push(child) })
      return found
    })

    expect(details).toHaveLength(2)
    expect(detailMeshes.length).toBeGreaterThan(12)
    expect(detailMeshes.every((mesh) => mesh.userData.exportExcluded === true)).toBe(true)
    expect(detailMeshes.some((mesh) => liquid.registries.renderMeshes.includes(mesh))).toBe(false)
    expect(exportNames(liquid)).toEqual(airExports)
    setLiquidModeActive(false)
  })

  it('disposes detail resources with the owning model root', () => {
    const project = projectWith(['cdu'])
    setLiquidModeActive(true)
    const h = host(project)
    const builder = createSceneBuilder(h)
    builder.rebuild()
    const detail = detailRoots(h.globalGroup)[0]
    const mesh = detail?.getObjectByProperty('type', 'Mesh') as THREE.Mesh | undefined
    expect(mesh).toBeTruthy()
    const geometryDispose = vi.spyOn(mesh!.geometry, 'dispose')
    const materialDispose = vi.spyOn(mesh!.material as THREE.Material, 'dispose')

    builder.clear()

    expect(geometryDispose).toHaveBeenCalledOnce()
    expect(materialDispose).toHaveBeenCalledOnce()
    setLiquidModeActive(false)
  })

  it('rebuilds loaded structural roots when liquid mode is toggled later', () => {
    const project = projectWith(['cdu', 'rack'])
    const rack = (stateV4().allModels(project) as unknown as Array<{ kind: string; cooling: { type: string } }>).find(m => m.kind === 'rack')!
    rack.cooling.type = 'liquid'
    setLiquidModeActive(false)
    const h = host(project)
    const builder = createSceneBuilder(h)
    builder.rebuild()
    expect(detailRoots(h.globalGroup)).toHaveLength(1)

    const watched = builder as typeof builder & { watchLiquidMode?: () => () => void }
    expect(typeof watched.watchLiquidMode).toBe('function')
    const stop = watched.watchLiquidMode!()
    setLiquidModeActive(true)

    expect(detailRoots(h.globalGroup)).toHaveLength(2)
    stop()
    setLiquidModeActive(false)
  })

  it('draws branch-hose pairs only for enabled liquid server trays', () => {
    const project = projectWith(['rack'])
    const rack = (stateV4().allModels(project) as unknown as Array<Record<string, unknown>>)[0]!
    ;(rack.cooling as { type: string }).type = 'liquid'
    rack.constructions = {
      servers: {
        liquid: {
          uid: 'srv-liquid',
          meta: { enabled: true },
          geometry: { slotPosition: 10, slotOccupation: 2 },
          liquidCooling: { cpCuPairs: [{}] },
        },
        disabled: {
          uid: 'srv-disabled',
          meta: { enabled: false },
          geometry: { slotPosition: 20, slotOccupation: 1 },
          liquidCooling: { cpCuPairs: [{}] },
        },
        air: {
          uid: 'srv-air',
          meta: { enabled: true },
          geometry: { slotPosition: 30, slotOccupation: 1 },
        },
      },
    }
    setLiquidModeActive(true)
    const h = host(project)
    createSceneBuilder(h).rebuild()
    const detail = detailRoots(h.globalGroup)[0]!
    const branches: THREE.Object3D[] = []
    detail.traverse((child) => {
      if (child.name.includes('-branch-')) branches.push(child)
    })

    expect(branches.map((branch) => branch.name).sort()).toEqual([
      'generic-rack-return-branch-srv-liquid',
      'generic-rack-supply-branch-srv-liquid',
    ])
    setLiquidModeActive(false)
  })
})

/**
 * The targeted rebuild a selection change uses.
 *
 * A rack's U-slot guides exist only for the SELECTED rack, so picking a
 * different rack has to re-render something — and "something" used to be the
 * whole room. On `case/Alibaba-hongchou` that was 287 models and 15,153 meshes
 * for one click: 1.4 s in jsdom with textures off, against 18 ms for the two
 * racks that were actually out of date.
 *
 * The contract that makes the swap safe is that the two builds are
 * indistinguishable afterwards — same meshes, same registries, and the same
 * FLAT ORDER, because `io/stlExport` walks `renderMeshes` to group solids and a
 * re-ordered list exports a different file.
 */
describe('rebuildModels', () => {
  function liveHost(project: CfdProject) {
    const globalGroup = new THREE.Group()
    const wrapperGroup = new THREE.Group()
    wrapperGroup.add(globalGroup)
    let selected: string | null = null
    const h: SceneHost = {
      globalGroup,
      wrapperGroup,
      registries: emptyRegistries(),
      selectedUid: () => selected,
      project: () => project,
      webglAvailable: false,
    }
    return { h, select: (uid: string | null) => { selected = uid } }
  }

  const names = (h: SceneHost) =>
    h.registries.renderMeshes.map((m) => (m.userData as { exportName?: string }).exportName ?? '')

  it('leaves the flat mesh order identical to a full rebuild', () => {
    const project = projectWith(['box', 'rack', 'box', 'rack', 'acu'])
    const racks = (stateV4().allModels(project) as unknown as { uid: string; kind: string }[]).filter(
      (m) => m.kind === 'rack',
    )
    const picked = racks[1]!.uid

    const whole = liveHost(project)
    whole.select(picked)
    createSceneBuilder(whole.h).rebuild()

    const part = liveHost(project)
    const builder = createSceneBuilder(part.h)
    builder.rebuild()
    part.select(picked)
    builder.rebuildModels([picked])

    expect(names(part.h)).toEqual(names(whole.h))
    expect(part.h.registries.renderRoots.length).toBe(whole.h.registries.renderRoots.length)
    expect(part.h.registries.renderRootMap.size).toBe(whole.h.registries.renderRootMap.size)
    expect(part.h.globalGroup.children.length).toBe(whole.h.globalGroup.children.length)
  })

  it('moves the guides off the rack that was selected and onto the one that is', () => {
    const project = projectWith(['rack', 'rack'])
    const racks = (stateV4().allModels(project) as unknown as { uid: string; kind: string }[]).filter(
      (m) => m.kind === 'rack',
    )
    const [a, b] = [racks[0]!.uid, racks[1]!.uid]
    const { h, select } = liveHost(project)
    const builder = createSceneBuilder(h)

    const guidesFor = (uid: string) =>
      h.registries.renderMeshes.filter(
        (m) =>
          (m.userData as { modelId?: string; isRackSlotGuide?: boolean }).modelId === uid &&
          (m.userData as { isRackSlotGuide?: boolean }).isRackSlotGuide === true,
      ).length

    select(a)
    builder.rebuild()
    expect(guidesFor(a)).toBeGreaterThan(0)
    expect(guidesFor(b)).toBe(0)

    select(b)
    builder.rebuildModels([a, b])
    expect(guidesFor(a)).toBe(0)
    expect(guidesFor(b)).toBeGreaterThan(0)
  })

  it('drops a model that has been disabled since it was built', () => {
    const project = projectWith(['box', 'box'])
    const boxes = stateV4().allModels(project) as unknown as { uid: string; meta: { enabled: boolean } }[]
    const { h } = liveHost(project)
    const builder = createSceneBuilder(h)
    builder.rebuild()
    expect(h.registries.renderRootMap.has(boxes[0]!.uid)).toBe(true)

    boxes[0]!.meta.enabled = false
    builder.rebuildModels([boxes[0]!.uid])
    expect(h.registries.renderRootMap.has(boxes[0]!.uid)).toBe(false)
    expect(h.registries.renderRoots.length).toBe(1)
    expect(names(h).some((n) => n.includes(boxes[0]!.uid))).toBe(false)
  })

  it('does nothing for an empty list and survives an unknown uid', () => {
    const project = projectWith(['box'])
    const { h } = liveHost(project)
    const builder = createSceneBuilder(h)
    builder.rebuild()
    const before = names(h)
    builder.rebuildModels([])
    builder.rebuildModels(['nope'])
    expect(names(h)).toEqual(before)
  })
})
