import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeshPanel } from '../../src/components/MeshPanel'
import { reviveKinds } from '../../src/legacy/cfdprojModules'
import * as store from '../../src/projectStore'
import { simulationConfigOf } from '../../src/run/meshConfig'
import type { CfdProject } from '../../src/schema/cfdproj'
import { setViewportHandle, type ViewportHandle } from '../../src/viewportBus'

/**
 * Clicking an extra seed row flies the camera to it.
 *
 * An extra `locationInMesh` marks a SECOND connected fluid region — a sealed
 * containment aisle — and the only way to check one is in the right place is to
 * go and look. The legacy makes the row's label the control:
 * `lbl.onclick = _flyToExtraLoc(entry.id)`, `title = 'Click to locate'`
 * (STL_generate.html:27428). React listed the seeds with editable X/Y/Z boxes
 * and no way to visit any of them: `locateSeed` (MeshPanel.tsx:99) reads
 * `meshConfig.locationInMesh` — the PRIMARY seed — takes no argument, and is
 * bound to one button.
 *
 * ## Two gestures, deliberately different
 *
 * The primary button RE-AIMS: it keeps the viewing direction and the distance,
 * so pressing it does not spin the room (`locateLocInMesh`, :27304). An extra
 * seed row FLIES: target on the seed, camera 3 m off it diagonally
 * (`_flyToExtraLoc`, :27442). Both are ported as they are. On a 40 m room the
 * re-aim leaves the seed a dot in the middle of the screen, which is fine for
 * the one seed that has its own sphere and its own drag handles, and useless
 * for picking one of six out of a list.
 */

const PROJECT = resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj')

let host: HTMLDivElement
let root: Root
let camera: THREE.PerspectiveCamera
let controls: { target: THREE.Vector3; update: ReturnType<typeof vi.fn> }

function render() {
  flushSync(() => root.render(<MeshPanel project={store.getProject()} />))
}

function click(id: string) {
  const el = document.getElementById(id)
  expect(el, id).not.toBeNull()
  flushSync(() => el!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

function seeds() {
  return (simulationConfigOf(store.getProject()).extraLocations ?? []) as store.ExtraLocation[]
}

beforeEach(() => {
  store.loadProject(
    reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject),
    '/cases/Base_leakage.cfdproj',
  )
  // Both seeds are placed at the same point — `addExtraLocation` offsets a
  // fixed 0.75 m from the primary — so they are moved apart here, or "each row
  // goes somewhere different" would be unfalsifiable.
  for (const [id, at] of [
    [store.addExtraLocation(), [1, 2, 3]],
    [store.addExtraLocation(), [7, 8, 9]],
  ] as [number, [number, number, number]][]) {
    ;(['x', 'y', 'z'] as const).forEach((axis, i) => store.setExtraLocation(id, axis, at[i]!))
  }
  camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000)
  camera.position.set(0, 0, 20)
  camera.lookAt(0, 0, 0)
  camera.updateMatrixWorld()
  controls = { target: new THREE.Vector3(0, 0, 0), update: vi.fn() }
  setViewportHandle({ camera, controls } as unknown as ViewportHandle)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  setViewportHandle(null)
})

describe('an extra seed row', () => {
  it('takes the camera to ITS OWN point, not the primary seed', () => {
    const [, second] = seeds()
    expect(second).toBeDefined()
    click(`extra-loc-${second!.id}-locate`)
    expect(controls.target.toArray()).toEqual([second!.x, second!.y, second!.z])
    expect(controls.update).toHaveBeenCalled()
  })

  it('stands 3 m off it, the offset the legacy flies to', () => {
    const [first] = seeds()
    click(`extra-loc-${first!.id}-locate`)
    expect(camera.position.toArray()).toEqual([first!.x + 3, first!.y + 3, first!.z + 3])
  })

  it('is per row — each one goes somewhere different', () => {
    const [first, second] = seeds()
    click(`extra-loc-${first!.id}-locate`)
    const a = controls.target.clone()
    click(`extra-loc-${second!.id}-locate`)
    expect(controls.target.equals(a)).toBe(false)
  })

  it('says what it does, the way the legacy row does', () => {
    const [first] = seeds()
    expect(document.getElementById(`extra-loc-${first!.id}-locate`)?.title).toBeTruthy()
  })

  it('does nothing at all with no viewport, rather than throwing', () => {
    setViewportHandle(null)
    const [first] = seeds()
    expect(() => click(`extra-loc-${first!.id}-locate`)).not.toThrow()
  })
})

describe('the primary seed button, which is a different gesture', () => {
  it('re-aims: the distance to the target is unchanged', () => {
    // Not the extra rows' 3 m hop — pressing 📷 on the primary seed must not
    // spin the room. If these two ever collapse into one implementation, this
    // is the assertion that says which one was lost.
    const before = camera.position.distanceTo(controls.target)
    click('btn-locate-loc')
    expect(camera.position.distanceTo(controls.target)).toBeCloseTo(before, 6)
  })
})
