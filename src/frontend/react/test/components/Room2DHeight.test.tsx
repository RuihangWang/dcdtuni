import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import { fromDisplay, unitOf } from '../../src/lengthUnits'
import * as store from '../../src/projectStore'
import { Room2DEditor } from '../../src/components/Room2DEditor'

/**
 * The 2D designer opens on the ceiling that EXISTS, not the one it last wrote.
 *
 * `geometry.planeHeight` is a record of the last 2D apply. Nothing updates it
 * when the ceiling model is dragged in 3D, so the two drift apart — and because
 * the designer's 应用 writes the height back out, opening it on the stale
 * number quietly drags the ceiling to it. A geometry change nobody asked for,
 * from a panel the user only opened to look at the floor plan.
 *
 * The legacy re-derives first thing in `enterEditor`
 * (`_syncRoomPolygonHeightFromExplicitCeiling`, room_2d_editor.js:137).
 */

let host: HTMLDivElement
let root: Root

/**
 * jsdom has no `ResizeObserver`, and the canvas repaint effect observes its
 * host. Stubbed rather than mocked away: the editor is being rendered for real,
 * and the alternative is not rendering it — which is how the stale height got
 * in.
 */
class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function project(): CfdProject {
  return store.getProject() as CfdProject
}

/** A room ceiling: the `roomBoundaryLocation: 'top'` floor `inferRoomHeight` looks for. */
function addCeiling(y: number): string {
  const { bucket, model } = createModel(project(), 'floor')
  const uid = store.addModel(bucket, model)
  store.setModelLocation(uid, { x: 0, y, z: 0 })
  store.edit('ceiling', uid, (draft) => {
    const floor = store.findConstruction(draft, uid)!.model as unknown as {
      cooling?: Record<string, unknown>
    }
    ;(floor.cooling ?? (floor.cooling = {})).roomBoundaryLocation = 'top'
  })
  return uid
}

function setPlaneHeight(value: number) {
  store.edit('planeHeight', String(value), (draft) => {
    const geometry = draft.geometry as unknown as Record<string, unknown>
    geometry.plane = [
      [0, 0],
      [6, 0],
      [6, 4],
      [0, 4],
    ]
    geometry.planeHeight = value
  })
}

function render() {
  flushSync(() =>
    root.render(<Room2DEditor project={project()} onClose={() => {}} />),
  )
}

/**
 * The height box in the designer's side column.
 *
 * Searched on `document.body`, not the host: the side column is PORTALLED out
 * of this subtree when the editor mounts beside the app's panels.
 */
function heightValue(): number {
  const box = document.body.querySelector<HTMLInputElement>('#room2d-height')
  // Read back in METRES: the box is in the project's input unit, which
  // defaults to millimetres, and the assertions below are about geometry.
  return fromDisplay(Number(box?.value ?? NaN), unitOf(project()))
}

beforeEach(() => {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = StubResizeObserver
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the 2D designer’s room height', () => {
  it('follows the ceiling model, not the stored planeHeight', () => {
    setPlaneHeight(3.0)
    // The ceiling was raised in 3D after the last 2D apply.
    addCeiling(4.2)
    render()
    expect(heightValue()).toBeCloseTo(4.2, 4)
  })

  it('falls back to planeHeight when the room has no ceiling', () => {
    setPlaneHeight(2.7)
    render()
    expect(heightValue()).toBeCloseTo(2.7, 4)
  })

  it('takes the HIGHEST ceiling when there are several', () => {
    setPlaneHeight(3.0)
    addCeiling(3.6)
    addCeiling(4.8)
    render()
    expect(heightValue()).toBeCloseTo(4.8, 4)
  })

  it('ignores a floor that is not the room boundary', () => {
    setPlaneHeight(3.0)
    const { bucket, model } = createModel(project(), 'floor')
    const uid = store.addModel(bucket, model)
    // A raised floor at 0.6 m is not a ceiling, and taking it would shrink the
    // room to the plenum.
    store.setModelLocation(uid, { x: 0, y: 0.6, z: 0 })
    render()
    expect(heightValue()).toBeCloseTo(3.0, 4)
  })
})

/** Guards the accessor above: a renamed id would make every assertion vacuous. */
describe('the height box', () => {
  it('is on screen', () => {
    setPlaneHeight(3.0)
    render()
    expect(Number.isFinite(heightValue())).toBe(true)
  })
})
