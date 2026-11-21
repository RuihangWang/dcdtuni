import * as THREE from 'three'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createAirPipe } from '../src/legacy/factories'
import { createAirPipeHandles, type AirPipeHandles } from '../src/geometry/airPipeHandles'
import type { SceneRegistries } from '../src/interaction/sceneBuilder'

/**
 * The air-pipe waypoint balls must not take a selection gesture away from the
 * viewport.
 *
 * Measured on DAML-Testbed (both editors): with Duct_riser_1 and Duct_riser_2
 * selected, a ctrl- or shift-click on the active riser's END ball attached the
 * gizmo to the ball and the selection never changed. The balls are larger than
 * the tube, so they cover the pipe's ends exactly where a user clicks it.
 */

const SIZE = 200
let scene: THREE.Scene
let camera: THREE.PerspectiveCamera
let dom: HTMLDivElement
let pipe: string
let count: number
let handles: AirPipeHandles

function balls(): THREE.Object3D[] {
  return scene.getObjectByName('airPipeHandles')?.children ?? []
}

/** A press on the screen pixel of waypoint ball `index`. */
function pressOn(index: number, mods: Partial<Pick<PointerEvent, 'shiftKey' | 'ctrlKey' | 'metaKey'>> = {}) {
  scene.updateMatrixWorld(true)
  const ball = balls().find((b) => (b.userData as { index?: number }).index === index)!
  const v = ball.getWorldPosition(new THREE.Vector3()).project(camera)
  return {
    button: 0,
    pointerId: 1,
    clientX: ((v.x + 1) / 2) * SIZE,
    clientY: ((1 - v.y) / 2) * SIZE,
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    ...mods,
  } as unknown as PointerEvent
}

beforeEach(() => {
  store.loadProject(stateV4().getInitialStateV4() as unknown as CfdProject, null)
  const created = createAirPipe(store.getProject()!, {
    location: { x: 0, y: 0, z: 0 },
    waypoints: [
      { x: 0, y: 0.5, z: 0 },
      { x: 0, y: 1.5, z: 0 },
      { x: 0, y: 2.5, z: 0 },
    ],
  })
  store.addModel(created.bucket, created.model)
  pipe = created.model.uid
  count = 1

  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
  camera.position.set(4, 1.5, 0)
  camera.lookAt(0, 1.5, 0)
  camera.updateMatrixWorld(true)
  dom = document.createElement('div')
  dom.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: SIZE, height: SIZE, right: SIZE, bottom: SIZE, x: 0, y: 0 }) as DOMRect
  document.body.appendChild(dom)
  handles = createAirPipeHandles({
    scene,
    camera,
    domElement: dom,
    orbit: null,
    registries: () => ({}) as SceneRegistries,
    project: () => store.getProject(),
    selection: () => pipe,
    selectionCount: () => count,
    rebuildSoon: () => {},
    rebuildNow: () => {},
    enabled: () => true,
  })
  handles.refresh()
})

afterEach(() => {
  handles.dispose()
  dom.remove()
})

describe('air pipe handles', () => {
  it('draws one ball per waypoint for a single selected pipe, and a plain press grabs one', () => {
    expect(balls()).toHaveLength(3)
    expect(handles.begin(pressOn(2))).toBe(true)
  })

  it('leaves a shift, ctrl or cmd press on a ball to the selection', () => {
    expect(handles.begin(pressOn(2, { shiftKey: true }))).toBe(false)
    expect(handles.begin(pressOn(2, { ctrlKey: true }))).toBe(false)
    expect(handles.begin(pressOn(2, { metaKey: true }))).toBe(false)
    // ...including right after a drag, when a plain press is swallowed.
    expect(handles.begin(pressOn(0, { ctrlKey: true }))).toBe(false)
  })

  it('draws no balls while several models are selected, and draws them again for one', () => {
    count = 2
    handles.refresh()
    expect(balls()).toHaveLength(0)
    count = 1
    handles.refresh()
    expect(balls()).toHaveLength(3)
  })
})
