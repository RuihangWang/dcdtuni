import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  beginDragCreate,
  dragCreateInFlight,
  setDragCreateBridge,
  takeDragCreateClick,
  type DragCreateBridge,
} from '../src/interaction/dragCreate'
import { stateV4 } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import type { Vec3 } from '../src/geometry/faceDragMath'

/**
 * The gesture, driven end to end against a stub viewport.
 *
 * The bridge is what the real viewport lends it — where the pointer is over the
 * floor, and what the shared snap makes of the result — so stubbing it is what
 * lets the SEQUENCE be checked without a WebGL canvas: nothing happens until
 * the pointer has travelled, one model appears however many moves follow, and
 * the release is where the snap and the bounds clamp get their say.
 */

/** The ground point each move reports, in world coordinates. */
let ground: Vec3 | null = { x: 3.5, y: 0, z: 1.5 }
let snapDelta: Vec3 = { x: 0, y: 0, z: 0 }
let snapOn = { face: false, edge: false, grid: false }
const synced: string[] = []

const bridge: DragCreateBridge = {
  groundPointAt: () => (ground ? { ...ground } : null),
  syncRenderTransform: (uid) => synced.push(uid),
  rebuildNow: () => {},
  snap: () => snapOn,
  snapDelta: () => snapDelta,
}

function emptyProject(): CfdProject {
  return stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
}

function context() {
  return {
    project: () => store.getProject(),
    onCreated: vi.fn(),
    cannotFitMessage: 'cannot fit',
  }
}

function pointer(type: string, x: number, y: number) {
  // jsdom has no PointerEvent; the module reads only the coordinates and the
  // button, so a MouseEvent of the same type is the same thing to it.
  return new MouseEvent(type, { clientX: x, clientY: y, bubbles: true }) as unknown as PointerEvent
}

function press(x = 0, y = 0) {
  return { button: 0, clientX: x, clientY: y } as PointerEvent
}

function walls(): { uid: string; location: Vec3; name: string }[] {
  const project = store.getProject() as CfdProject
  const bucket = (project.constructions?.walls ?? {}) as unknown as Record<
    string,
    { meta: { name: string }; geometry: { location: Vec3 } }
  >
  return Object.entries(bucket).map(([uid, model]) => ({
    uid,
    name: model.meta.name,
    location: model.geometry.location,
  }))
}

beforeEach(() => {
  store.loadProject(emptyProject(), null)
  setDragCreateBridge(bridge)
  ground = { x: 3.5, y: 0, z: 1.5 }
  snapDelta = { x: 0, y: 0, z: 0 }
  snapOn = { face: false, edge: false, grid: false }
  synced.length = 0
  takeDragCreateClick()
})

describe('drag-to-place creation', () => {
  it('creates nothing until the pointer has travelled 5 px', () => {
    beginDragCreate(press(100, 100), 'wall', context())
    window.dispatchEvent(pointer('pointermove', 103, 103))
    expect(walls()).toHaveLength(0)
    expect(dragCreateInFlight()).toBe(true)
    window.dispatchEvent(pointer('pointerup', 103, 103))
    expect(walls()).toHaveLength(0)
    // Nothing was created, so the button's own click still has to work.
    expect(takeDragCreateClick()).toBe(false)
  })

  it('creates ONE model however many moves follow, at the released point', () => {
    const ctx = context()
    beginDragCreate(press(100, 100), 'wall', ctx)
    window.dispatchEvent(pointer('pointermove', 110, 110))
    window.dispatchEvent(pointer('pointermove', 140, 130))
    ground = { x: 4.25, y: 0, z: 2.75 }
    window.dispatchEvent(pointer('pointermove', 180, 160))
    window.dispatchEvent(pointer('pointerup', 180, 160))

    const created = walls()
    expect(created).toHaveLength(1)
    expect(created[0]!.location).toEqual({ x: 4.25, y: 0, z: 2.75 })
    expect(ctx.onCreated).toHaveBeenCalledTimes(1)
    // The rendered group follows the anchor directly during the drag; without
    // this the model would only appear where it was dropped on the next rebuild.
    expect(synced).toContain(created[0]!.uid)
    // One `addModel`, so one press of Ctrl+Z takes the whole gesture back.
    expect(store.undoDepth()).toBe(1)
    store.undo()
    expect(walls()).toHaveLength(0)
  })

  it('answers the click that a released press fires on the button', () => {
    beginDragCreate(press(100, 100), 'wall', context())
    window.dispatchEvent(pointer('pointermove', 140, 130))
    window.dispatchEvent(pointer('pointerup', 140, 130))
    expect(takeDragCreateClick()).toBe(true)
    // Once only: the next click is a real one.
    expect(takeDragCreateClick()).toBe(false)
  })

  it('adds the snap offset on release, not on every move', () => {
    snapOn = { face: true, edge: true, grid: false }
    snapDelta = { x: -0.25, y: 0, z: 0.5 }
    beginDragCreate(press(100, 100), 'wall', context())
    window.dispatchEvent(pointer('pointermove', 140, 130))
    expect(walls()[0]!.location).toEqual({ x: 3.5, y: 0, z: 1.5 })
    window.dispatchEvent(pointer('pointerup', 140, 130))
    expect(walls()[0]!.location).toEqual({ x: 3.25, y: 0, z: 2 })
  })

  it('keeps the model inside an enabled project bound', () => {
    store.setProjectBound('xMax', 'enabled', true)
    store.setProjectBound('xMax', 'value', 4)
    beginDragCreate(press(100, 100), 'wall', context())
    window.dispatchEvent(pointer('pointermove', 140, 130))
    window.dispatchEvent(pointer('pointerup', 140, 130))
    // A default wall is 1 m wide, so the furthest its anchor can sit is 3.
    expect(walls()[0]!.location.x).toBe(3)
  })

  it('takes the model back out when it cannot fit between the bounds', () => {
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    store.setProjectBound('xMin', 'enabled', true)
    store.setProjectBound('xMin', 'value', 0)
    store.setProjectBound('xMax', 'enabled', true)
    store.setProjectBound('xMax', 'value', 0.5)
    const depth = store.undoDepth()
    beginDragCreate(press(100, 100), 'wall', context())
    window.dispatchEvent(pointer('pointermove', 140, 130))
    expect(walls()).toHaveLength(1)
    window.dispatchEvent(pointer('pointerup', 140, 130))
    expect(walls()).toHaveLength(0)
    expect(alert).toHaveBeenCalledWith('cannot fit')
    // And no undo entry left behind for a model that never landed.
    expect(store.undoDepth()).toBe(depth)
    alert.mockRestore()
  })

  it('ignores a right-click and a kind the gesture is not wired to', () => {
    beginDragCreate({ button: 2, clientX: 0, clientY: 0 } as PointerEvent, 'wall', context())
    expect(dragCreateInFlight()).toBe(false)
    beginDragCreate(press(0, 0), 'rack', context())
    expect(dragCreateInFlight()).toBe(false)
  })
})
