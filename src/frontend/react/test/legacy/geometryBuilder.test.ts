import * as THREE from 'three'
import { beforeAll, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'

/**
 * The geometry modules must work OUTSIDE the legacy page.
 *
 * They used to call ~50 sibling functions as bare globals, which resolved only
 * because a top-level `function` in a classic <script> becomes a window
 * property. Inside the page that worked; anywhere else every call was a
 * ReferenceError. Nothing inside the page could reveal it — the byte oracle
 * passed, the viewport rendered, `node --check` passed.
 *
 * So this loads them the way React does — as modules, with no page around them —
 * and drives a real build. That is the check the page cannot perform.
 */

type Scene = {
  buildGroupedStlMap(ids: string[], leakage: boolean): Record<string, string>
  renderBoxModel(m: unknown, g: THREE.Group, fl: unknown, isl: unknown): void
  isModelEnabled(m: unknown): boolean
}
type Pure = {
  getFaceLayouts(m: unknown): unknown
  buildFaceIslands(m: unknown): unknown
  applyModelTransformToObject(o: THREE.Object3D, m: unknown): void
  liquidEquipmentPortFrame(
    m: unknown,
    port: string,
    end?: 'top' | 'bottom',
  ): { position: THREE.Vector3; direction: THREE.Vector3 } | null
}

let createScene: (ctx: Record<string, unknown>) => Scene
let pure: Pure

beforeAll(async () => {
  const mod = await import('../../src/legacy/geometryBuilder')
  pure = mod.geometryPure() as unknown as Pure
  createScene = mod.createGeometryScene as unknown as typeof createScene
})

/**
 * A box built by the SHARED factory, not hand-rolled.
 *
 * The first version of this fixture had `faces: {}`, and renderBoxModel walks
 * `Object.keys(model.geometry.faces)` — so it built nothing and the test looked
 * like a broken module rather than a broken fixture. Going through
 * `createModel` also means the test exercises the path the app uses.
 */
function boxModel(uid: string) {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  const created = createModel(project, 'box')
  const model = created.model as unknown as Record<string, unknown>
  ;(model as { uid: string }).uid = uid
  return model
}

function makeContext(models: Record<string, unknown>[]) {
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  const modelMap = new Map<string, unknown>()
  for (const m of models) modelMap.set(m.uid as string, m)
  let renderMeshes: THREE.Object3D[] = []
  let renderRoots: THREE.Object3D[] = []
  let renderHelpers: THREE.Object3D[] = []
  return {
    ctx: {
      get state() {
        return { constructions: { boxes: Object.fromEntries(modelMap) } }
      },
      get uiState() {
        return { selection: { modelId: null, type: 'project' } }
      },
      get webglAvailable() {
        return false
      },
      get renderMeshes() {
        return renderMeshes
      },
      set renderMeshes(v: THREE.Object3D[]) {
        renderMeshes = v
      },
      get renderRoots() {
        return renderRoots
      },
      set renderRoots(v: THREE.Object3D[]) {
        renderRoots = v
      },
      get renderHelpers() {
        return renderHelpers
      },
      set renderHelpers(v: THREE.Object3D[]) {
        renderHelpers = v
      },
      globalGroup,
      wrapperGroup,
      geometryCache: new Map(),
      _renderRootMap: new Map<string, THREE.Object3D>(),
      _modelMap: modelMap,
      allModels: () => models,
    },
    meshes: () => renderMeshes,
    globalGroup,
  }
}

describe('loading outside the legacy page', () => {
  it('installs every shared module on window', () => {
    const w = window as unknown as Record<string, unknown>
    for (const name of ['stlWriter', 'stlNaming', 'geometryPure', 'geometryScene', 'polygonClipping']) {
      expect(w[name], `${name} missing`).toBeTruthy()
    }
  })

  it('shares ONE Three.js instance with the app', () => {
    // Two copies of Three do not interoperate: instanceof fails across them and
    // a mesh built by one cannot be added to the other's scene.
    //
    // The global is a mutable COPY of the namespace rather than the namespace
    // itself — the legacy modules add `TransformControls` to it, and a module
    // namespace is immutable. So the check is on the CLASSES, which is where
    // the guarantee actually lives: same constructor object, so `instanceof`
    // holds across both apps.
    const global = (window as unknown as { THREE: Record<string, unknown> }).THREE
    expect(global.Mesh).toBe(THREE.Mesh)
    expect(global.BufferGeometry).toBe(THREE.BufferGeometry)
    expect(global.Vector3).toBe(THREE.Vector3)
    expect(new (global.Mesh as typeof THREE.Mesh)()).toBeInstanceOf(THREE.Mesh)
  })

  it('lets the legacy modules ADD to the global THREE', () => {
    // `js/liquid_mode.js` checks `THREE.TransformControls`, an addon an app has
    // to install. Assigning onto a module namespace throws, and it throws
    // inside a viewport effect — which took the whole app to a blank page.
    const global = (window as unknown as { THREE: Record<string, unknown> }).THREE
    expect(() => {
      global.__probe = 1
    }).not.toThrow()
    delete global.__probe
  })

  it('rejects a context that is missing a required entry', () => {
    const mod = window as unknown as {
      geometryScene: { create(c: Record<string, unknown>): unknown }
    }
    expect(() => mod.geometryScene.create({ state: null })).toThrow(/missing/)
  })
})

describe('building a box, with no page around it', () => {
  it('produces meshes tagged with exportName', () => {
    const model = boxModel('box-1')
    const { ctx, meshes, globalGroup } = makeContext([model])
    const scene = createScene(ctx)

    const group = new THREE.Group()
    globalGroup.add(group)
    pure.applyModelTransformToObject(group, model)
    scene.renderBoxModel(model, group, pure.getFaceLayouts(model), pure.buildFaceIslands(model))

    const built = meshes()
    expect(built.length).toBeGreaterThan(0)
    // exportName is what becomes the OpenFOAM patch. A mesh without one is a
    // surface the solver will never see.
    const named = built.filter((m) => !!(m.userData as { exportName?: string })?.exportName)
    expect(named.length).toBe(built.length)
  })

  it('writes into the CURRENT registry after it is reassigned', () => {
    // The reason the context uses accessors: a project load replaces the array.
    const model = boxModel('box-2')
    const { ctx, meshes, globalGroup } = makeContext([model])
    const scene = createScene(ctx)
    ;(ctx as { renderMeshes: THREE.Object3D[] }).renderMeshes = [] // simulate a clear

    const group = new THREE.Group()
    globalGroup.add(group)
    scene.renderBoxModel(model, group, pure.getFaceLayouts(model), pure.buildFaceIslands(model))
    expect(meshes().length).toBeGreaterThan(0)
  })

  it('reports a disabled model as disabled', () => {
    const model = boxModel('box-3')
    ;(model.meta as { enabled: boolean }).enabled = false
    const { ctx } = makeContext([model])
    expect(createScene(ctx).isModelEnabled(model)).toBe(false)
  })
})

describe('liquid equipment port transforms', () => {
  it('rotates and translates CDU ports through the full model quaternion', () => {
    const cdu = {
      kind: 'cdu',
      geometry: {
        location: { x: 10, y: 2, z: -3 },
        size: { x: 0.8, y: 1.8, z: 0.6 },
        quaternion: { x: 0, y: Math.SQRT1_2, z: 0, w: Math.SQRT1_2 },
        scale: { x: 1, y: 1, z: 1 },
      },
    }

    const supply = pure.liquidEquipmentPortFrame(cdu, 'supply')
    const ret = pure.liquidEquipmentPortFrame(cdu, 'return')

    expect(supply?.position.x).toBeCloseTo(10.6, 12)
    expect(supply?.position.y).toBeCloseTo(3.53, 12)
    expect(supply?.position.z).toBeCloseTo(-3.4, 12)
    expect(supply?.direction.x).toBeCloseTo(1, 12)
    expect(supply?.direction.y).toBeCloseTo(0, 12)
    expect(supply?.direction.z).toBeCloseTo(0, 12)
    expect(ret?.position.x).toBeCloseTo(10.6, 12)
    expect(ret?.position.y).toBeCloseTo(3.48, 12)
    expect(ret?.position.z).toBeCloseTo(-3.4, 12)
  })

  it('uses the configured rack manifold and its rotated translated frame', () => {
    const rack = {
      kind: 'rack',
      safeMarginBottom: 0.05,
      geometry: {
        location: { x: 4, y: 1, z: 8 },
        size: { x: 0.6, y: 2, z: 1.2 },
        quaternion: { x: 0, y: Math.SQRT1_2, z: 0, w: Math.SQRT1_2 },
        scale: { x: 1, y: 1, z: 1 },
        slot: 10,
        slotHeight: 0.1,
      },
      cooling: {
        type: 'liquid',
        liquidManifold: {
          layout: 'split',
          mountFace: 'inlet',
          supplyPipe: { diameter: 0.025 },
          returnPipe: { diameter: 0.025 },
          pipeSpacing: 0.015,
        },
      },
    }

    const supply = pure.liquidEquipmentPortFrame(rack, 'inlet')
    const ret = pure.liquidEquipmentPortFrame(rack, 'outlet')

    expect(supply?.position.x).toBeCloseTo(3.95, 12)
    expect(supply?.position.y).toBeCloseTo(2.05, 12)
    expect(supply?.position.z).toBeCloseTo(7.9775, 12)
    expect(ret?.position.x).toBeCloseTo(3.95, 12)
    expect(ret?.position.y).toBeCloseTo(2.05, 12)
    expect(ret?.position.z).toBeCloseTo(7.4225, 12)
    expect(supply?.direction.x).toBeCloseTo(0, 12)
    expect(supply?.direction.y).toBeCloseTo(1, 12)
    expect(supply?.direction.z).toBeCloseTo(0, 12)
  })
})
