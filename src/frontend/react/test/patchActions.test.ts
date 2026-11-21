import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  addPatch,
  autoFillPatch,
  deletePatch,
  deletePatchWithCavity,
  locatePatch,
  patchCfd,
  resetPatchVector,
  togglePatchVectorVisibility,
  updatePatchCfd,
  updatePatchGeometry,
} from '../src/geometry/patchActions'
import * as store from '../src/projectStore'

/**
 * These go through the real store, because the thing being checked is not the
 * arithmetic — that is `patchGeometry.test.ts` — but that a boundary opening
 * lands in the project, survives an undo, and does not cost an undo step when
 * it was refused.
 */

type Face = { id: string; wType: string; hType: string; patches: unknown[] }

function face(id: string, wType: string, hType: string, patches: unknown[] = []): Face {
  return { id, wType, hType, patches }
}

function boxFaces(patches: Record<string, unknown[]> = {}) {
  return {
    front: face('front', 'W', 'H', patches.front ?? []),
    back: face('back', 'W', 'H', patches.back ?? []),
    left: face('left', 'D', 'H', patches.left ?? []),
    right: face('right', 'D', 'H', patches.right ?? []),
    top: face('top', 'W', 'D', patches.top ?? []),
    bottom: face('bottom', 'W', 'D', patches.bottom ?? []),
  }
}

function project(): CfdProject {
  return {
    schemaVersion: 4,
    name: 'T',
    constructions: {
      // The bucket is what gives each model its `kind` — `reviveKinds` derives
      // it on load, exactly as a real .cfdproj does.
      acus: {
        m_1: {
          uid: 'm_1',
          meta: { name: 'ACU_1', enabled: true },
          geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 1.2, y: 2.0, z: 0.6 }, faces: boxFaces() },
        },
      },
      floors: {
        m_2: {
          uid: 'm_2',
          meta: { name: 'Floor_1', enabled: true },
          geometry: {
            location: { x: 0, y: 0, z: 0 },
            size: { x: 5, y: 0, z: 4 },
            faces: boxFaces({
              top: [{ id: 'p_4', type: 'rect', name: 'Tile', color: '#e06c75', x: 1, y: 1, w: 0.6, h: 0.6 }],
            }),
          },
        },
      },
      partitionWalls: {
        m_3: {
          uid: 'm_3',
          meta: { name: 'Partition_1', enabled: true },
          geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 2, y: 3, z: 0.05 }, faces: boxFaces() },
        },
      },
      walls: {
        m_5: {
          uid: 'm_5',
          meta: { name: 'Wall_1', enabled: true },
          geometry: {
            location: { x: 0, y: 0, z: 0 },
            size: { x: 4, y: 3, z: 0 },
            faces: boxFaces({
              front: [{ id: 'p_9', type: 'circle', name: 'Leak', color: '#e06c75', r: 0.2, cx: 2, cy: 1.5 }],
            }),
          },
        },
      },
      racks: {
        m_6: {
          uid: 'm_6',
          meta: { name: 'Rack_1', enabled: true },
          geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 0.6, y: 2, z: 1 }, faces: boxFaces() },
        },
      },
    },
  } as unknown as CfdProject
}

const patchesOn = (uid: string, faceId: string): Record<string, unknown>[] => {
  const model = store.findConstruction(store.getProject() as CfdProject, uid)?.model
  const faces = (model?.geometry as { faces?: Record<string, { patches?: unknown[] }> } | undefined)?.faces
  return (faces?.[faceId]?.patches ?? []) as Record<string, unknown>[]
}

beforeEach(() => store.loadProject(project(), '/tmp/T.cfdproj'))

describe('addPatch', () => {
  it('creates an ACU return opening on the face that was asked for', () => {
    const result = addPatch('m_1', 'top', 'rect')
    expect(result).toMatchObject({ ok: true, faceId: 'top', patchId: 'p_10' })
    expect(patchesOn('m_1', 'top')).toHaveLength(1)
    expect(patchesOn('m_1', 'top')[0]).toEqual({
      id: 'p_10',
      type: 'rect',
      name: 'Rect_Patch_10',
      color: '#e06c75',
      x: 0,
      y: 0,
      w: 0.6,
      h: 0.3,
      showVector: true,
      vx: 0,
      vy: -1,
      vz: 0,
    })
  })

  it('continues the patch numbering past what the file already holds', () => {
    // p_9 is the highest in the fixture; a counter starting at zero would hand
    // out p_1 and two patches would share a boundary condition.
    expect(addPatch('m_1', 'front', 'circle').patchId).toBe('p_10')
    expect(addPatch('m_1', 'front', 'circle').patchId).toBe('p_11')
  })

  it('colours by position within the face, so two faces both start red', () => {
    addPatch('m_1', 'front', 'rect')
    addPatch('m_1', 'front', 'rect')
    addPatch('m_1', 'back', 'rect')
    expect(patchesOn('m_1', 'front').map((p) => p.color)).toEqual(['#e06c75', '#61afef'])
    expect(patchesOn('m_1', 'back').map((p) => p.color)).toEqual(['#e06c75'])
  })

  it('is one undo step', () => {
    const depth = store.undoDepth()
    addPatch('m_1', 'top', 'circle')
    expect(store.undoDepth()).toBe(depth + 1)
    store.undo()
    expect(patchesOn('m_1', 'top')).toHaveLength(0)
  })

  it('refuses a rack, whose openings are U-slots and not free patches', () => {
    expect(addPatch('m_6', 'front', 'rect').ok).toBe(false)
    expect(patchesOn('m_6', 'front')).toHaveLength(0)
    expect(store.undoDepth()).toBe(0)
  })

  it('sends a partition-wall hole to the single front-face store', () => {
    // The hole goes through the plate, so both large faces describe the same
    // one; keeping two stores would export the hole twice.
    const result = addPatch('m_3', 'back', 'circle')
    expect(result).toMatchObject({ ok: true, faceId: 'front' })
    expect(patchesOn('m_3', 'front')).toHaveLength(1)
    expect(patchesOn('m_3', 'back')).toHaveLength(0)
  })

  it('refuses a partition-wall edge face without costing an undo step', () => {
    expect(addPatch('m_3', 'left', 'circle')).toMatchObject({ ok: false })
    expect(store.undoDepth()).toBe(0)
    expect(store.isDirty()).toBe(false)
  })

  it('clamps the new patch onto the face and says when it had to', () => {
    // A floor is 5 x 4, so half of it fits; a wall of zero depth has a
    // degenerate top face and the default rectangle cannot fit there.
    expect(addPatch('m_2', 'top', 'rect').clamped).toBe(false)
    expect(addPatch('m_5', 'top', 'rect').clamped).toBe(true)
  })
})

describe('deletePatch', () => {
  it('removes the opening and gives the surface back', () => {
    expect(deletePatch('m_2', 'p_4')).toMatchObject({ ok: true, faceId: 'top' })
    expect(patchesOn('m_2', 'top')).toHaveLength(0)
    store.undo()
    expect(patchesOn('m_2', 'top')).toHaveLength(1)
  })

  it('reports a patch that is not there rather than editing nothing', () => {
    expect(deletePatch('m_2', 'p_999').ok).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })
})

describe('deletePatchWithCavity', () => {
  it('leaves a hole where a plain delete leaves wall', () => {
    // On a raised floor this is the difference between a sealed tile and an
    // open cable cut-out.
    deletePatchWithCavity('m_2', 'p_4')
    const model = store.findConstruction(store.getProject() as CfdProject, 'm_2')?.model
    const cutouts = (model?.geometry as { cutouts?: { faceId: string; polygon: number[][][][] }[] })?.cutouts
    expect(cutouts).toHaveLength(1)
    expect(cutouts?.[0]?.faceId).toBe('top')
    expect(cutouts?.[0]?.polygon).toEqual([
      [
        [
          [1, 1],
          [1.6, 1],
          [1.6, 1.6],
          [1, 1.6],
          [1, 1],
        ],
      ],
    ])
    expect(patchesOn('m_2', 'top')).toHaveLength(0)
  })

  it('cuts a 32-segment ring for a circular patch', () => {
    deletePatchWithCavity('m_5', 'p_9')
    const model = store.findConstruction(store.getProject() as CfdProject, 'm_5')?.model
    const cutouts = (model?.geometry as { cutouts?: { polygon: number[][][][] }[] })?.cutouts
    expect(cutouts?.[0]?.polygon[0]?.[0]).toHaveLength(33)
  })
})

describe('autoFillPatch', () => {
  it('stretches a floor tile across the whole slab', () => {
    expect(autoFillPatch('m_2', 'p_4', 'H')).toMatchObject({ ok: true, clamped: false })
    expect(patchesOn('m_2', 'top')[0]).toMatchObject({ x: 0, w: 5, y: 1, h: 0.6 })
    autoFillPatch('m_2', 'p_4', 'V')
    expect(patchesOn('m_2', 'top')[0]).toMatchObject({ x: 0, w: 5, y: 0, h: 4 })
  })

  it('refuses a circle, which has no edges to reach', () => {
    expect(autoFillPatch('m_5', 'p_9', 'H').ok).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })
})

describe('the projection vector', () => {
  it('flips the arrow and seeds the vector it describes', () => {
    const before = patchesOn('m_2', 'top')[0]
    expect(before?.showVector).toBeUndefined()
    togglePatchVectorVisibility('m_2', 'p_4')
    // Defaults are applied first, so the flip is off a known state rather than
    // off `undefined` — which would toggle to true and show nothing new.
    expect(patchesOn('m_2', 'top')[0]).toMatchObject({ showVector: false, vx: 0, vy: -1, vz: 0 })
    togglePatchVectorVisibility('m_2', 'p_4')
    expect(patchesOn('m_2', 'top')[0]?.showVector).toBe(true)
  })

  it('resets to the face inward normal, whatever was typed', () => {
    updatePatchGeometry('m_2', 'p_4', 'vx', 0.7)
    updatePatchGeometry('m_2', 'p_4', 'vy', 0.7)
    resetPatchVector('m_2', 'p_4')
    expect(patchesOn('m_2', 'top')[0]).toMatchObject({ vx: 0, vy: -1, vz: 0 })
  })
})

describe('updatePatchGeometry', () => {
  it('clamps as the value is entered, not at export', () => {
    const result = updatePatchGeometry('m_2', 'p_4', 'w', 50)
    expect(result.clamped).toBe(true)
    expect(patchesOn('m_2', 'top')[0]?.w).toBe(5)
  })

  it('leaves a value that fits alone', () => {
    expect(updatePatchGeometry('m_2', 'p_4', 'w', 2).clamped).toBe(false)
    expect(patchesOn('m_2', 'top')[0]?.w).toBe(2)
  })

  it('ignores a non-number instead of writing NaN into the geometry', () => {
    expect(updatePatchGeometry('m_2', 'p_4', 'w', Number.NaN).ok).toBe(false)
    expect(patchesOn('m_2', 'top')[0]?.w).toBe(0.6)
    expect(store.undoDepth()).toBe(0)
  })
})

describe('updatePatchCfd', () => {
  it('stores a fixed surface temperature on the patch', () => {
    updatePatchCfd('m_5', 'front', 'p_9', 'fixedTemperature', 42.5)
    expect(patchCfd(store.getProject(), 'm_5', 'p_9')).toEqual({ fixedTemperature: 42.5 })
  })

  it('REMOVES the key when switched off, because absent is what the exporter reads', () => {
    // js/cfdrun_build.js:637 emits a heat patch only when the field is not
    // null; a stored null would still travel in the saved project.
    updatePatchCfd('m_5', 'front', 'p_9', 'fixedTemperature', 42.5)
    updatePatchCfd('m_5', 'front', 'p_9', 'fixedTemperature', null)
    const found = locatePatch(store.getProject(), 'm_5', 'p_9')
    expect(found?.patch.cfd).toEqual({})
    expect('fixedTemperature' in (found?.patch.cfd as object)).toBe(false)
  })
})
