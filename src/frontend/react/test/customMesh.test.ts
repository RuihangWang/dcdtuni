import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  addPatchToRegion,
  applyCustomPatchBoolDiff,
  buildCustomModel,
  buildRegionPatch,
  canBoolDiff,
  customImportSeed,
  unapplyCustomPatch,
} from '../src/geometry/customMesh'
import type { ImportedStl } from '../src/io/stlAssets'
import * as store from '../src/projectStore'

function project(): CfdProject {
  return { name: 'T', constructions: { customMeshes: {} } } as unknown as CfdProject
}

/** What `readStlFile` hands back for a two-solid file, without reading one. */
const imported = (): ImportedStl => ({
  name: 'merged_box',
  base: { W: 0.6, D: 0.4, H: 1.8 },
  customOffset: { x: -1, y: -2, z: 0 },
  customSTLBase64: 'c29saWQK',
  solidRegions: ['UPS_Rack_Front', 'UPS_Rack_Back'],
  asset: { baseGeometry: null, regions: [] } as unknown as ImportedStl['asset'],
})

beforeEach(() => store.loadProject(project(), null))

describe('customImportSeed', () => {
  it('is the legacy createModelFromRaw seed, key order included', () => {
    // The parity check against the HTML editor is a byte diff of the saved
    // .cfdproj, and JSON.stringify writes keys in insertion order.
    expect(Object.keys(customImportSeed(store.getProject()!, imported()))).toEqual([
      'id',
      'fromModel',
      'kind',
      'name',
      'enabled',
      'showAnchorMarker',
      'opacity',
      'anchor',
      'base',
      'transform',
      'moveTool',
      'faces',
      'solidRegions',
      'customSTLBase64',
      'customOffset',
      'rack',
      'cutouts',
      'polygon',
      'isRoomBoundary',
      'rotateTool',
      'cfd',
    ])
  })

  it('names the model after the file and holds rotateTool open for the migrator', () => {
    const seed = customImportSeed(store.getProject()!, imported())
    expect(seed.name).toBe('merged_box')
    // Null in the SEED only, so the key lands where the legacy puts it.
    expect(seed.rotateTool).toBeNull()
    expect(seed.cfd).toEqual({ role: 'wall' })
  })

  it('gives every face the six-face default state', () => {
    const faces = customImportSeed(store.getProject()!, imported()).faces as Record<string, unknown>
    expect(Object.keys(faces)).toEqual(['front', 'back', 'left', 'right', 'top', 'bottom'])
  })
})

describe('buildCustomModel', () => {
  it('nests the mesh under customMesh, as V4 stores it', () => {
    const { bucket, model } = buildCustomModel(store.getProject()!, imported())
    expect(bucket).toBe('customMeshes')
    expect(model.kind).toBe('custom')
    expect((model as unknown as Record<string, unknown>).customMesh).toEqual({
      stlBase64: 'c29saWQK',
      offset: { x: -1, y: -2, z: 0 },
      solidRegions: ['UPS_Rack_Front', 'UPS_Rack_Back'],
    })
  })

  it('fills rotateTool, the one field a legacy import differed in', () => {
    // Measured by importing the same .stl in both apps and diffing the model:
    // the HTML editor's inspector materialises the block when the new model is
    // selected, so this is what a saved legacy file carries.
    const { model } = buildCustomModel(store.getProject()!, imported())
    expect((model as unknown as Record<string, unknown>).rotateTool).toEqual({
      enabled: false,
      axes: { yaw: true, pitch: false, roll: false },
    })
    expect(Object.keys(model as unknown as Record<string, unknown>)).toEqual([
      'uid',
      'geometry',
      'cooling',
      'meta',
      'customMesh',
      'fromModel',
      'moveTool',
      'rack',
      'polygon',
      'isRoomBoundary',
      'rotateTool',
    ])
  })

  it('writes the file bounds into geometry.size in the V4 axis order', () => {
    // The v3 seed is W/D/H; V4 is x/y/z with y up, so D and H swap.
    const { model } = buildCustomModel(store.getProject()!, imported())
    expect(model.geometry?.size).toEqual({ x: 0.6, y: 1.8, z: 0.4 })
  })

  it('takes the next free uid rather than a per-load counter', () => {
    const first = buildCustomModel(store.getProject()!, imported())
    store.addModel(first.bucket, first.model)
    const second = buildCustomModel(store.getProject()!, imported())
    expect(second.model.uid).not.toBe(first.model.uid)
  })
})

describe('addPatchToRegion', () => {
  function withModel() {
    const { bucket, model } = buildCustomModel(store.getProject()!, imported())
    store.addModel(bucket, model)
    return model.uid
  }

  it('tags the patch with the region it was added from', () => {
    const uid = withModel()
    const result = addPatchToRegion(uid, 'UPS_Rack_Back', 'circle')
    expect(result.ok).toBe(true)
    const model = store.findConstruction(store.getProject()!, uid)!.model
    const faces = model.geometry!.faces as Record<string, { patches: Record<string, unknown>[] }>
    const patch = faces[result.faceId!]!.patches.find((p) => p.id === result.patchId)!
    expect(patch.regionName).toBe('UPS_Rack_Back')
    expect(patch.applied).toBe(false)
    expect(patch.type).toBe('circle')
  })

  it('stores null for the whole model, never an empty string', () => {
    // `getAppliedCustomPatchFragments` tests `patch.regionName &&` — an empty
    // string would be falsy there but would still print as a region name.
    const uid = withModel()
    const result = addPatchToRegion(uid, null, 'rect')
    const model = store.findConstruction(store.getProject()!, uid)!.model
    const faces = model.geometry!.faces as Record<string, { patches: Record<string, unknown>[] }>
    expect(faces[result.faceId!]!.patches[0]!.regionName).toBeNull()
  })

  it('numbers patches across the whole project, not per model', () => {
    const a = withModel()
    const b = withModel()
    const first = addPatchToRegion(a, null, 'rect')
    const second = addPatchToRegion(b, null, 'rect')
    expect(second.patchId).not.toBe(first.patchId)
  })

  it('is one undo step', () => {
    const uid = withModel()
    addPatchToRegion(uid, null, 'circle')
    store.undo()
    const model = store.findConstruction(store.getProject()!, uid)!.model
    const faces = model.geometry!.faces as Record<string, { patches: unknown[] }>
    expect(Object.values(faces).every((f) => f.patches.length === 0)).toBe(true)
  })

  it('refuses a model that is not an imported mesh', () => {
    expect(addPatchToRegion('nope', null, 'rect')).toEqual({ ok: false, reason: 'no-model' })
  })
})

describe('buildRegionPatch', () => {
  it('sizes a circle from the region, never to zero', () => {
    const { model } = buildCustomModel(store.getProject()!, imported())
    const patch = buildRegionPatch(model, 'front', 'circle', 7, 'UPS_Rack_Front')
    expect(patch.id).toBe('p_7')
    expect(patch.r).toBeGreaterThan(0)
    expect(patch.depth).toBeGreaterThan(0)
  })

  it('gives a rect a width and a height instead of a radius', () => {
    const { model } = buildCustomModel(store.getProject()!, imported())
    const patch = buildRegionPatch(model, 'front', 'rect', 1, null)
    expect(patch.r).toBeUndefined()
    expect(patch.w).toBeGreaterThan(0)
    expect(patch.h).toBeGreaterThan(0)
  })
})

describe('Bool Diff', () => {
  it('refuses a mesh whose regions are not planar shells', () => {
    // No scene has been built here, so the cache holds nothing and the shared
    // planarity test cannot pass — the same refusal a curved STL gets.
    const { bucket, model } = buildCustomModel(store.getProject()!, imported())
    store.addModel(bucket, model)
    const added = addPatchToRegion(model.uid, null, 'circle')
    expect(canBoolDiff(model.uid)).toBe(false)
    expect(applyCustomPatchBoolDiff(model.uid, added.patchId!)).toEqual({
      ok: false,
      reason: 'not-planar',
    })
  })

  it('brings the beam back, and does not need the scene to do it', () => {
    // Un-apply must work even where apply would refuse: a project loaded with
    // `applied: true` from another session has to be editable again.
    const { bucket, model } = buildCustomModel(store.getProject()!, imported())
    store.addModel(bucket, model)
    const added = addPatchToRegion(model.uid, null, 'circle')
    store.edit('test', 'force applied', (draft) => {
      const m = store.findConstruction(draft, model.uid)!.model
      const faces = m.geometry!.faces as Record<string, { patches: Record<string, unknown>[] }>
      faces[added.faceId!]!.patches[0]!.applied = true
    })
    expect(unapplyCustomPatch(model.uid, added.patchId!).ok).toBe(true)
    const after = store.findConstruction(store.getProject()!, model.uid)!.model
    const faces = after.geometry!.faces as Record<string, { patches: Record<string, unknown>[] }>
    expect(faces[added.faceId!]!.patches[0]!.applied).toBe(false)
  })

  it('refuses a patch that is not there', () => {
    const { bucket, model } = buildCustomModel(store.getProject()!, imported())
    store.addModel(bucket, model)
    expect(applyCustomPatchBoolDiff(model.uid, 'p_999')).toEqual({ ok: false, reason: 'no-patch' })
  })
})
