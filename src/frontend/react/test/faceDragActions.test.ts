import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  beginDragCreatedModel,
  cancelDragCreatedModel,
  commitModelBox,
  commitPatchGeometry,
  enforceSiteSafety,
  findPatchOnModel,
  readPatchGeometry,
  writePatchGeometry,
} from '../src/geometry/faceDragActions'
import { stateV4 } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'

/**
 * Through the real store, like `patchActions.test.ts`: the arithmetic is
 * covered in `faceDragMath.test.ts`, and what these have to show is that a drag
 * lands in the project as ONE undoable step and that undoing it puts back what
 * was there before.
 */

function face(id: string, wType: string, hType: string, patches: unknown[] = []) {
  return { id, wType, hType, patches }
}

function project(): CfdProject {
  return {
    schemaVersion: 4,
    name: 'T',
    geometry: {
      bounds: {
        xMin: { enabled: false, value: 0 },
        xMax: { enabled: false, value: 10 },
        yMin: { enabled: true, value: 0 },
        yMax: { enabled: false, value: 3 },
        zMin: { enabled: false, value: 0 },
        zMax: { enabled: false, value: 6 },
      },
    },
    constructions: {
      acus: {
        m_1: {
          uid: 'm_1',
          meta: { name: 'ACU_1', enabled: true },
          geometry: {
            location: { x: 1, y: 0, z: 2 },
            size: { x: 1.2, y: 2, z: 0.6 },
            faces: {
              front: face('front', 'W', 'H', [
                { id: 'p_1', type: 'rect', name: 'Supply', color: '#e06c75', x: 0.1, y: 0.2, w: 0.5, h: 0.5 },
              ]),
              top: face('top', 'W', 'D'),
            },
          },
        },
      },
    },
  } as unknown as CfdProject
}

function acu() {
  return store.findConstruction(store.getProject() as CfdProject, 'm_1')!.model
}

beforeEach(() => {
  store.loadProject(project(), null)
})

describe('findPatchOnModel', () => {
  it('finds a patch and the face holding it', () => {
    const site = findPatchOnModel(acu(), 'p_1')
    expect(site?.faceId).toBe('front')
    expect(site?.patch.name).toBe('Supply')
  })

  it('returns null for a patch that is not on the model', () => {
    expect(findPatchOnModel(acu(), 'p_nope')).toBeNull()
  })
})

describe('reading and writing a patch in place', () => {
  it('reads the fields the patch TYPE uses, and no others', () => {
    const site = findPatchOnModel(acu(), 'p_1')!
    expect(readPatchGeometry(site.patch)).toEqual({ x: 0.1, y: 0.2, w: 0.5, h: 0.5 })
    site.patch.type = 'circle'
    site.patch.cx = 0.3
    site.patch.cy = 0.4
    site.patch.r = 0.1
    expect(readPatchGeometry(site.patch)).toEqual({ cx: 0.3, cy: 0.4, r: 0.1 })
  })

  it('ignores a value that is not a finite number', () => {
    const site = findPatchOnModel(acu(), 'p_1')!
    writePatchGeometry(site.patch, { x: Number.NaN, y: 0.7 })
    expect(site.patch.x).toBe(0.1)
    expect(site.patch.y).toBe(0.7)
  })

  it('pulls a patch dragged off the face back inside it', () => {
    const site = findPatchOnModel(acu(), 'p_1')!
    writePatchGeometry(site.patch, { x: 5 })
    expect(enforceSiteSafety(site)).toBe(true)
    // The face is 1.2 wide and the patch 0.5, so the furthest it can start is 0.7.
    expect(site.patch.x).toBeCloseTo(0.7, 12)
  })
})

describe('commitPatchGeometry', () => {
  it('writes the drag result as ONE undo entry', () => {
    expect(commitPatchGeometry('m_1', 'p_1', { x: 0.4, y: 0.3, w: 0.5, h: 0.5 })).toBe(true)
    expect(store.undoDepth()).toBe(1)
    expect(findPatchOnModel(acu(), 'p_1')!.patch.x).toBeCloseTo(0.4, 12)
    store.undo()
    expect(findPatchOnModel(acu(), 'p_1')!.patch.x).toBeCloseTo(0.1, 12)
  })

  it('clamps on the way in, so a drag cannot commit a patch off its face', () => {
    commitPatchGeometry('m_1', 'p_1', { x: 3, y: 0.2, w: 0.5, h: 0.5 })
    expect(findPatchOnModel(acu(), 'p_1')!.patch.x).toBeCloseTo(0.7, 12)
  })

  it('refuses a patch that has gone, rather than resurrecting it', () => {
    expect(commitPatchGeometry('m_1', 'p_gone', { x: 1 })).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })
})

describe('commitModelBox', () => {
  it('writes the size AND the anchor together, as one entry', () => {
    expect(
      commitModelBox('m_1', { x: 1.8, y: 2, z: 0.6 }, { x: 0.4, y: 0, z: 2 }),
    ).toBe(true)
    expect(store.undoDepth()).toBe(1)
    const geometry = acu().geometry as { size: { x: number }; location: { x: number } }
    expect(geometry.size.x).toBe(1.8)
    // Both, because a left-edge resize holds the far edge still by moving the
    // anchor — committing only the size would slide the model.
    expect(geometry.location.x).toBe(0.4)
    store.undo()
    const back = acu().geometry as { size: { x: number }; location: { x: number } }
    expect(back.size.x).toBe(1.2)
    expect(back.location.x).toBe(1)
  })

  it('refuses a box with a non-finite number in it', () => {
    expect(commitModelBox('m_1', { x: Number.NaN, y: 2, z: 0.6 }, { x: 0, y: 0, z: 0 })).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })
})

describe('drag-create', () => {
  beforeEach(() => {
    store.loadProject(
      stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
      null,
    )
  })

  it('inserts the model with its move tool already on', () => {
    const created = beginDragCreatedModel(store.getProject() as CfdProject, 'wall')!
    expect(created.uid).toBeTruthy()
    const model = store.findConstruction(store.getProject() as CfdProject, created.uid)!.model
    // The gesture that placed it WAS a move, so the arrows to keep moving it
    // should already be there.
    expect((model as unknown as { moveTool: { enabled: boolean } }).moveTool.enabled).toBe(true)
    expect(store.undoDepth()).toBe(1)
  })

  it('takes the model back out and leaves no undo entry behind', () => {
    const created = beginDragCreatedModel(store.getProject() as CfdProject, 'wall')!
    cancelDragCreatedModel()
    expect(store.findConstruction(store.getProject() as CfdProject, created.uid)).toBeNull()
    expect(store.undoDepth()).toBe(0)
  })
})
