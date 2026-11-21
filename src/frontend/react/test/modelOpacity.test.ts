import { describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import {
  applyModelOpacityState,
  averageOpacityPercent,
  clampOpacityPercent,
  commitMultiOpacity,
  modelOpacityPercent,
  opacityToPercent,
  percentToOpacity,
  previewMultiOpacity,
  resetOpacityPreview,
} from '../src/geometry/modelOpacity'
import * as store from '../src/projectStore'

function model(kind: string, faces?: Record<string, unknown>): Construction {
  const m = {
    uid: 'm_1',
    meta: { name: 'M', display: { opacity: 1, showAnchorMarker: true } },
    geometry: { faces },
  } as unknown as Construction
  Object.defineProperty(m, 'kind', { value: kind, enumerable: false })
  return m
}

function faces() {
  return {
    front: { opacity: 1, wallNames: ['a', 'b'], wallOpacities: [1] },
    back: { opacity: 1, wallNames: [], wallOpacities: [1] },
  } as Record<string, unknown>
}

describe('percent <-> opacity', () => {
  it('round-trips the slider positions the legacy uses', () => {
    expect(percentToOpacity(0)).toBe(0)
    expect(percentToOpacity(15)).toBeCloseTo(0.15, 12)
    expect(percentToOpacity(100)).toBe(1)
    expect(opacityToPercent(0.15)).toBe(15)
    expect(opacityToPercent(1)).toBe(100)
  })

  it('treats a missing opacity as fully opaque', () => {
    // A model saved before the field existed must not render invisible.
    expect(opacityToPercent(undefined)).toBe(100)
    expect(modelOpacityPercent(model('box'))).toBe(100)
  })

  it('clamps out-of-range input instead of wrapping it', () => {
    expect(clampOpacityPercent(-40)).toBe(0)
    expect(clampOpacityPercent(140)).toBe(100)
    expect(percentToOpacity(140)).toBe(1)
  })

  it('keeps NaN as NaN so a half-typed value can be rejected', () => {
    expect(Number.isNaN(percentToOpacity(Number.NaN))).toBe(true)
  })

  it('rounds to whole percent, as the slider step does', () => {
    expect(opacityToPercent(0.157)).toBe(16)
  })
})

describe('applyModelOpacityState', () => {
  it('writes the model level value', () => {
    const m = model('rack', faces())
    expect(applyModelOpacityState(m, 15)).toBe(true)
    expect(m.meta.display?.opacity).toBeCloseTo(0.15, 12)
  })

  it('cascades into every face, so a rack door does not stay opaque', () => {
    const m = model('rack', faces())
    applyModelOpacityState(m, 40)
    const f = m.geometry?.faces as Record<string, { opacity: number }>
    expect(f.front!.opacity).toBeCloseTo(0.4, 12)
    expect(f.back!.opacity).toBeCloseTo(0.4, 12)
  })

  it('fills wall opacities up to the longest of names and opacities', () => {
    // `front` has two named regions but only one stored opacity; leaving the
    // second alone would keep half the wall opaque.
    const m = model('wall', faces())
    applyModelOpacityState(m, 20)
    const f = m.geometry?.faces as Record<string, { wallOpacities: number[] }>
    expect(f.front!.wallOpacities).toEqual([0.2, 0.2])
    expect(f.back!.wallOpacities).toEqual([0.2])
  })

  it('leaves per-region opacities alone on an imported STL', () => {
    // `custom` faces are the STL's own solids and carry their own opacity.
    const m = model('custom', faces())
    applyModelOpacityState(m, 20)
    expect(m.meta.display?.opacity).toBeCloseTo(0.2, 12)
    const f = m.geometry?.faces as Record<string, { opacity: number }>
    expect(f.front!.opacity).toBe(1)
  })

  it('does not give a rack per-wall opacities it never had', () => {
    const m = model('rack', faces())
    applyModelOpacityState(m, 30)
    const f = m.geometry?.faces as Record<string, { wallOpacities: number[] }>
    expect(f.front!.wallOpacities).toEqual([1])
  })

  it('refuses a value that is not a number', () => {
    const m = model('box', faces())
    expect(applyModelOpacityState(m, Number.NaN)).toBe(false)
    expect(m.meta.display?.opacity).toBe(1)
  })

  it('keeps `display` key order so the saved file does not churn', () => {
    const m = model('box')
    applyModelOpacityState(m, 50)
    expect(Object.keys(m.meta.display as object)).toEqual(['opacity', 'showAnchorMarker'])
  })
})

describe('averageOpacityPercent', () => {
  it('is the mean of the group, so one faded member is visible in the slider', () => {
    const a = model('box')
    const b = model('box')
    applyModelOpacityState(a, 100)
    applyModelOpacityState(b, 20)
    expect(averageOpacityPercent([a, b])).toBe(60)
  })

  it('counts a model with no stored opacity as fully opaque', () => {
    const bare = { uid: 'm', meta: { name: 'm' } } as unknown as Construction
    expect(averageOpacityPercent([bare])).toBe(100)
  })

  it('answers 100 for an empty selection rather than NaN', () => {
    expect(averageOpacityPercent([])).toBe(100)
  })
})

describe('the group slider writes ONE undo entry per drag', () => {
  function loaded() {
    store.loadProject(
      {
        name: 'T',
        constructions: {
          racks: {
            r1: { uid: 'r1', kind: 'rack', meta: { name: 'Rack_1' }, geometry: {} },
            r2: { uid: 'r2', kind: 'rack', meta: { name: 'Rack_2' }, geometry: {} },
          },
        },
      },
      null,
    )
    resetOpacityPreview()
  }

  const opacityOf = (uid: string) =>
    store.getProject()?.constructions?.racks?.[uid]?.meta?.display?.opacity

  it('takes one checkpoint however many frames the drag emits', () => {
    // The bug this guards: eighty pointer-move frames leaving eighty undo
    // entries, so putting a faded group back takes eighty presses of Ctrl+Z.
    loaded()
    const before = store.undoDepth()
    for (const percent of [90, 70, 50, 30, 15]) previewMultiOpacity(['r1', 'r2'], percent)
    commitMultiOpacity(['r1', 'r2'], 15)
    expect(store.undoDepth() - before).toBe(1)
    expect(opacityOf('r1')).toBeCloseTo(0.15)
    expect(opacityOf('r2')).toBeCloseTo(0.15)
  })

  it('undoes the whole drag in one step, back to where it started', () => {
    loaded()
    previewMultiOpacity(['r1', 'r2'], 40)
    commitMultiOpacity(['r1', 'r2'], 40)
    store.undo()
    expect(opacityOf('r1')).toBeUndefined()
    expect(opacityOf('r2')).toBeUndefined()
  })

  it('is still one undoable edit when only `change` fires, with no preview', () => {
    // A programmatic set, or a browser that skips `input` entirely.
    loaded()
    const before = store.undoDepth()
    commitMultiOpacity(['r1', 'r2'], 25)
    expect(store.undoDepth() - before).toBe(1)
    expect(opacityOf('r2')).toBeCloseTo(0.25)
  })

  it('leaves models outside the selection alone', () => {
    loaded()
    previewMultiOpacity(['r1'], 30)
    commitMultiOpacity(['r1'], 30)
    expect(opacityOf('r2')).toBeUndefined()
  })
})

describe('the mesh-preview fade (includeEquipment)', () => {
  it('fades server meshes too, which the model slider leaves alone', async () => {
    const THREE = await import('three')
    const { applyOpacityToMeshes } = await import('../src/geometry/modelOpacity')
    const root = new THREE.Group()
    const body = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshPhongMaterial())
    const server = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshPhongMaterial())
    server.userData.equipmentId = 'srv-1'
    root.add(body, server)
    applyOpacityToMeshes(root, 0)
    expect((body.material as { opacity: number }).opacity).toBe(0)
    expect((server.material as { opacity: number }).opacity).toBe(1)
    applyOpacityToMeshes(root, 0, { includeEquipment: true })
    expect((server.material as { opacity: number }).opacity).toBe(0)
    expect((server.material as { forceSinglePass: boolean }).forceSinglePass).toBe(true)
  })
})
