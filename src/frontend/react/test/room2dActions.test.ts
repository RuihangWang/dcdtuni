import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import { commitPlacement, dragOrigins, placeLive, snapBoxes } from '../src/room2d/room2dActions'
import { planModels } from '../src/room2d/room2dModels'

function project(): CfdProject {
  return {
    name: 'T',
    constructions: {
      racks: {
        r1: {
          uid: 'r1',
          meta: { name: 'Rack_1', enabled: true },
          geometry: { location: { x: 1, y: 0, z: 2 }, size: { x: 0.6, y: 2, z: 1.2 } },
        },
        r2: {
          uid: 'r2',
          meta: { name: 'Rack_2', enabled: true },
          geometry: { location: { x: 5, y: 0, z: 2 }, size: { x: 0.6, y: 2, z: 1.2 } },
        },
        r3: {
          uid: 'r3',
          meta: { name: 'Rack_3', enabled: false },
          geometry: { location: { x: 9, y: 0, z: 2 }, size: { x: 0.6, y: 2, z: 1.2 } },
        },
      },
    },
  }
}

beforeEach(() => store.loadProject(project(), '/tmp/T.cfdproj'))

const models = () => planModels(store.getProject())
const at = (uid: string) =>
  store.findConstruction(store.getProject()!, uid)!.model.geometry!.location!

describe('dragOrigins', () => {
  it('captures the plan position and orientation of the named models only', () => {
    const origins = dragOrigins(models(), ['r1'])
    expect(origins).toHaveLength(1)
    expect(origins[0]).toMatchObject({ uid: 'r1', x: 1, z: 2 })
  })

  it('copies the quaternion rather than aliasing it', () => {
    // The origin is what a cancelled or committed drag rewinds to; sharing the
    // live object would make it follow the drag it is supposed to undo.
    const origins = dragOrigins(models(), ['r1'])
    placeLive([{ uid: 'r1', x: 0, z: 0, quaternion: { x: 0, y: 1, z: 0, w: 0 } }])
    expect(origins[0]!.quaternion).toEqual({ x: 0, y: 0, z: 0, w: 1 })
  })
})

describe('snapBoxes', () => {
  it('leaves out the dragged models and the disabled ones', () => {
    const boxes = snapBoxes(models(), new Set(['r1']))
    expect(boxes.map((b) => b.uid)).toEqual(['r2'])
    expect(boxes[0]).toEqual({ uid: 'r2', x: 5, y: 2, w: 0.6, d: 1.2 })
  })
})

describe('placeLive / commitPlacement', () => {
  it('moves the model without adding an undo entry', () => {
    const before = store.undoDepth()
    placeLive([{ uid: 'r1', x: 3, z: 4 }])
    expect(at('r1')).toMatchObject({ x: 3, z: 4 })
    expect(store.undoDepth()).toBe(before)
  })

  it('leaves the anchor Y alone, because the plan has no idea what it is', () => {
    placeLive([{ uid: 'r1', x: 3, z: 4 }])
    expect(at('r1').y).toBe(0)
  })

  it('turns a whole drag into ONE undo entry that rewinds to its start', () => {
    const origins = dragOrigins(models(), ['r1'])
    placeLive([{ uid: 'r1', x: 2, z: 2 }])
    placeLive([{ uid: 'r1', x: 3, z: 2 }])
    placeLive([{ uid: 'r1', x: 4, z: 2 }])
    commitPlacement('2dMoveModel', '1 model', origins, [{ uid: 'r1', x: 4, z: 2 }])
    expect(at('r1')).toMatchObject({ x: 4, z: 2 })
    expect(store.undoDepth()).toBe(1)
    store.undo()
    // Not x: 3 — the intermediate frames must not be on the stack, and the
    // snapshot must predate the whole drag rather than its last frame.
    expect(at('r1')).toMatchObject({ x: 1, z: 2 })
  })

  it('commits a rotation as position AND orientation together', () => {
    const origins = dragOrigins(models(), ['r1'])
    commitPlacement('2dRotateModel', '1 model', origins, [
      { uid: 'r1', x: 1.6, z: 1.4, quaternion: { x: 0, y: 1, z: 0, w: 0 } },
    ])
    const hit = store.findConstruction(store.getProject()!, 'r1')!.model.geometry!
    expect(hit.location).toMatchObject({ x: 1.6, z: 1.4 })
    expect(hit.quaternion).toEqual({ x: 0, y: 1, z: 0, w: 0 })
    store.undo()
    const back = store.findConstruction(store.getProject()!, 'r1')!.model.geometry!
    expect(back.location).toMatchObject({ x: 1, z: 2 })
    expect(back.quaternion ?? { x: 0, y: 0, z: 0, w: 1 }).toEqual({ x: 0, y: 0, z: 0, w: 1 })
  })

  it('moves every model in a multi-selection by its own placement', () => {
    const origins = dragOrigins(models(), ['r1', 'r2'])
    commitPlacement('2dMoveModel', '2 models', origins, [
      { uid: 'r1', x: 1.5, z: 2.5 },
      { uid: 'r2', x: 5.5, z: 2.5 },
    ])
    expect(at('r1')).toMatchObject({ x: 1.5, z: 2.5 })
    expect(at('r2')).toMatchObject({ x: 5.5, z: 2.5 })
    expect(store.undoDepth()).toBe(1)
  })
})
