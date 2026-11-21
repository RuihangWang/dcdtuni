import { afterEach, describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import {
  focusSelectedRenderRoots, installSelectionFocus, readUnobscuredFrameArea,
  requestSelectionFocus, selectedVisibleBounds, selectionFrame, unobscuredFrameArea,
  type SafeFrameArea,
} from '../src/interaction/frameSelection'

const surface = (w = 1, h = 1, d = 1) =>
  new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial())
function rig(aspect = 1) {
  const camera = new THREE.PerspectiveCamera(45, aspect, .1, 400)
  camera.position.set(8, 7, 9)
  const controls = { target: new THREE.Vector3(0, 1, 0), update: vi.fn() }
  camera.lookAt(controls.target)
  return { camera, controls }
}
function expectFit(box: THREE.Box3, camera: THREE.PerspectiveCamera, area?: SafeFrameArea) {
  camera.updateMatrixWorld()
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) {
    for (const z of [box.min.z, box.max.z]) {
      const projected = new THREE.Vector3(x, y, z).project(camera)
      expect(projected.x).toBeGreaterThanOrEqual((area?.minX ?? -1) - 1e-10)
      expect(projected.x).toBeLessThanOrEqual((area?.maxX ?? 1) + 1e-10)
      expect(projected.y).toBeGreaterThanOrEqual((area?.minY ?? -1) - 1e-10)
      expect(projected.y).toBeLessThanOrEqual((area?.maxY ?? 1) + 1e-10)
      expect(projected.z).toBeGreaterThan(-1)
      expect(projected.z).toBeLessThan(1)
    }
  }
}
afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = '' })

describe('selected visible rendered bounds', () => {
  it('fits a rotated and scaled selected CDU in world coordinates, excluding the room', () => {
    const world = new THREE.Group()
    world.position.set(9, 3, -2)
    world.rotation.y = .43
    const root = new THREE.Group()
    root.rotation.z = .31
    root.scale.set(.5, 1.2, .7)
    root.add(surface(.6, 2.047, 1.2))
    world.add(root)
    const room = surface(40, 20, 50)
    const roots = new Map<string, THREE.Object3D>([['cdu', root], ['room', room]])
    world.updateMatrixWorld(true)
    const expected = new THREE.Box3().setFromObject(root)
    const actual = selectedVisibleBounds(roots, ['cdu', 'cdu'])!
    expect(actual.min.distanceTo(expected.min)).toBeLessThan(1e-12)
    expect(actual.max.distanceTo(expected.max)).toBeLessThan(1e-12)
    expect(actual.getSize(new THREE.Vector3()).length()).toBeLessThan(5)
  })
  it('excludes hidden ancestors and helper subtrees, including registered helpers', () => {
    const root = new THREE.Group()
    root.add(surface())
    const marker = surface(100, 100, 100)
    marker.userData.isSeedHandle = true
    root.add(marker)
    const registered = surface(200, 200, 200)
    root.add(registered)
    const roots = new Map<string, THREE.Object3D>([['model', root]])
    expect(selectedVisibleBounds(roots, ['model'], new Set([registered]))!.min.toArray())
      .toEqual([-.5, -.5, -.5])
    const parent = new THREE.Group()
    parent.visible = false
    parent.add(root)
    expect(selectedVisibleBounds(roots, ['model'])).toBeNull()
  })
  it('does not include invisible materials, empty roots or nonfinite buffers', () => {
    const hidden = surface()
    hidden.material.visible = false
    const bad = surface()
    bad.geometry.getAttribute('position').setX(0, NaN)
    const helper = surface()
    helper.userData.isHelper = true
    const roots = new Map<string, THREE.Object3D>([['hidden', hidden], ['bad', bad], ['helper', helper], ['empty', new THREE.Group()]])
    expect(selectedVisibleBounds(roots, [...roots.keys(), 'missing'])).toBeNull()
  })
  it('uses the selected real liquid pipe overlay and blind cap when its structural root is empty', () => {
    const global = new THREE.Group()
    const pipeGroup = new THREE.Group()
    const pipe = new THREE.Mesh(new THREE.TubeGeometry(new THREE.LineCurve3(
      new THREE.Vector3(6, 1, 4), new THREE.Vector3(9, 1, 4)), 8, .05, 8),
      new THREE.MeshStandardMaterial())
    pipe.userData.pipeModelId = 'm_31'
    const cap = surface(.1, .1, .1)
    cap.position.set(9, 1, 4); cap.userData.pipeId = 'm_31'
    const otherPipe = surface(100, 100, 100)
    otherPipe.userData.pipeModelId = 'm_32'
    pipeGroup.add(pipe, cap, otherPipe); global.add(pipeGroup)
    const roots = new Map<string, THREE.Object3D>([['m_31', new THREE.Group()]])
    const actual = selectedVisibleBounds(roots, ['m_31'], new Set(), global)!
    expect(actual.min.x).toBeCloseTo(6, 7)
    expect(actual.max.x).toBeCloseTo(9.05, 7)
    const { camera, controls } = rig()
    expect(focusSelectedRenderRoots({ camera, controls, roots, uids: ['m_31'],
      displayRoot: global, frozen: false })).toBe(true)
    expectFit(actual, camera)
    pipeGroup.visible = false
    expect(selectedVisibleBounds(roots, ['m_31'], new Set(), global)).toBeNull()
    expect(pipe.parent).toBe(pipeGroup)
    expect(global.children).toEqual([pipeGroup])
  })
  it('includes all visible instances in world coordinates', () => {
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial(), 2)
    mesh.setMatrixAt(0, new THREE.Matrix4().makeTranslation(0, 0, 0))
    mesh.setMatrixAt(1, new THREE.Matrix4().makeTranslation(5, 0, 0))
    mesh.position.y = 2
    expect(selectedVisibleBounds(new Map<string, THREE.Object3D>([['model', mesh]]), ['model'])!.max.toArray())
      .toEqual([5.5, 2.5, .5])
  })
})

describe('camera fit and unobscured canvas', () => {
  it.each([.35, 1, 3])('fits all eight corners at aspect %s, preserving direction and up', aspect => {
    const { camera, controls } = rig(aspect)
    const root = surface(.6, 2.047, 1.2)
    root.rotation.set(.2, .9, .3)
    root.position.set(6.5, 1.8, 4.6)
    const box = selectedVisibleBounds(new Map<string, THREE.Object3D>([['cdu', root]]), ['cdu'])!
    const direction = camera.position.clone().sub(controls.target).normalize()
    const up = camera.up.clone()
    expect(focusSelectedRenderRoots({ camera, controls, roots: new Map<string, THREE.Object3D>([['cdu', root]]),
      uids: ['cdu'], frozen: false })).toBe(true)
    expectFit(box, camera)
    expect(camera.position.clone().sub(controls.target).normalize().distanceTo(direction)).toBeLessThan(1e-12)
    expect(camera.up.toArray()).toEqual(up.toArray())
    expect(controls.update).toHaveBeenCalledTimes(1)
  })
  it('fits asymmetric side panels and expanded toolbar, placing the centre inside the safe gap', () => {
    const area = unobscuredFrameArea({ left: 0, right: 1520, top: 0, bottom: 796 }, {
      left: { left: 0, right: 330, top: 0, bottom: 796 },
      right: { left: 1180, right: 1520, top: 0, bottom: 796 },
      toolbar: { left: 338, right: 1172, top: 8, bottom: 229 },
      log: { left: 0, right: 1520, top: 796, bottom: 956 },
    })!
    const { camera, controls } = rig(1520 / 796)
    const root = surface(3, 8, 4)
    root.position.set(10, 4, -7)
    const roots = new Map<string, THREE.Object3D>([['model', root]])
    const box = selectedVisibleBounds(roots, ['model'])!
    const beforeDirection = camera.getWorldDirection(new THREE.Vector3())
    focusSelectedRenderRoots({ camera, controls, roots, uids: ['model'], area, frozen: false })
    expectFit(box, camera, area)
    const centreNdc = box.getCenter(new THREE.Vector3()).project(camera)
    expect(centreNdc.x).toBeCloseTo((area.minX + area.maxX) / 2, 12)
    expect(centreNdc.y).toBeCloseTo((area.minY + area.maxY) / 2, 12)
    expect(camera.getWorldDirection(new THREE.Vector3()).distanceTo(beforeDirection)).toBeLessThan(1e-12)
    expect(camera.view).toBeNull()
  })
  it('reads current DOM rects and ignores panels that are hidden', () => {
    const canvas = document.createElement('canvas')
    document.body.appendChild(canvas)
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      left: 10, right: 1010, top: 20, bottom: 820, width: 1000, height: 800,
    } as DOMRect)
    const add = (id: string, r: object) => {
      const el = document.createElement('div'); el.id = id; document.body.appendChild(el)
      vi.spyOn(el, 'getBoundingClientRect').mockReturnValue(r as DOMRect)
      return el
    }
    const left = add('left-panel', { left: 10, right: 310, top: 20, bottom: 820 })
    add('right-panel', { left: 850, right: 1010, top: 20, bottom: 820 })
    add('toolbar', { left: 318, right: 842, top: 28, bottom: 220 })
    const first = readUnobscuredFrameArea(canvas)!
    expect(first.minX).toBeCloseTo(-.376, 12)
    expect(first.maxY).toBeCloseTo(.47, 12)
    left.hidden = true
    const next = readUnobscuredFrameArea(canvas)!
    expect(next.minX).toBeCloseTo(-.976, 12)
  })
  it('rejects invalid or fully occluded available rectangles', () => {
    expect(unobscuredFrameArea({ left: 0, right: 0, top: 0, bottom: 100 }, {})).toBeNull()
    expect(unobscuredFrameArea({ left: 0, right: 100, top: 0, bottom: 100 }, {
      toolbar: { left: 0, right: 100, top: 0, bottom: 100 },
    })).toBeNull()
  })
  it('fits a micron-scale model with finite positive clip planes and retains a top-view up vector', () => {
    const { camera, controls } = rig(2)
    camera.position.set(0, 10, 0)
    controls.target.set(0, 0, 0)
    camera.up.set(0, 0, -1)
    camera.lookAt(controls.target)
    const root = surface(1e-6, 2e-6, 1e-6)
    const roots = new Map<string, THREE.Object3D>([['tiny', root]])
    focusSelectedRenderRoots({ camera, controls, roots, uids: ['tiny'], frozen: false })
    expectFit(selectedVisibleBounds(roots, ['tiny'])!, camera)
    expect(camera.near).toBeGreaterThan(0)
    expect(camera.near).toBeLessThan(1e-6)
    expect(Number.isFinite(camera.far)).toBe(true)
    expect(camera.far).toBeGreaterThan(camera.near)
    expect(camera.up.toArray()).toEqual([0, 0, -1])
  })
  it('handles a zero-size selected surface without division by zero', () => {
    const { camera, controls } = rig()
    const box = new THREE.Box3(new THREE.Vector3(1, 2, 3), new THREE.Vector3(1, 2, 3))
    const plan = selectionFrame(box, camera, controls.target)!
    expect(plan.position.toArray().every(Number.isFinite)).toBe(true)
    expect(plan.near).toBeGreaterThan(0)
    expect(plan.far).toBeGreaterThan(plan.near)
  })
  it('rejects nonfinite frustum or bounds without changing the camera', () => {
    const { camera, controls } = rig()
    const box = new THREE.Box3(new THREE.Vector3(), new THREE.Vector3(1, 1, 1))
    camera.aspect = NaN
    expect(selectionFrame(box, camera, controls.target)).toBeNull()
    camera.aspect = 1
    box.max.x = Infinity
    expect(selectionFrame(box, camera, controls.target)).toBeNull()
  })
})

describe('camera action lifecycle', () => {
  it('reads the current selection at click time and cleans its listener on unmount', () => {
    const { camera, controls } = rig()
    const a = surface(); a.position.x = 10
    const b = surface(); b.position.x = -20
    const roots = new Map<string, THREE.Object3D>([['a', a], ['b', b]])
    let selected = ['a'], frozen = false
    const beforeFocus = vi.fn(), onFocused = vi.fn()
    const stop = installSelectionFocus(() => ({ camera, controls, roots,
      uids: selected, frozen, beforeFocus, onFocused }))
    requestSelectionFocus()
    expect(controls.target.x).toBeCloseTo(10)
    selected = ['b']
    requestSelectionFocus()
    expect(controls.target.x).toBeCloseTo(-20)
    frozen = true
    const before = camera.position.clone()
    requestSelectionFocus()
    expect(camera.position.toArray()).toEqual(before.toArray())
    expect(onFocused).toHaveBeenCalledTimes(2)
    expect(beforeFocus).toHaveBeenCalledTimes(2)
    stop()
    frozen = false; selected = ['a']; requestSelectionFocus()
    expect(controls.target.x).toBeCloseTo(-20)
  })
  it('does not write models, geometry buffers or selection and rejects a missing safe area', () => {
    const { camera, controls } = rig()
    const root = surface()
    root.position.set(7, 1, -3)
    const uids = ['model']
    const before = JSON.stringify({ position: root.position, rotation: root.rotation,
      vertices: [...root.geometry.getAttribute('position').array], uids })
    const ctx = { camera, controls, roots: new Map<string, THREE.Object3D>([['model', root]]), uids, frozen: false }
    focusSelectedRenderRoots(ctx)
    expect(JSON.stringify({ position: root.position, rotation: root.rotation,
      vertices: [...root.geometry.getAttribute('position').array], uids })).toBe(before)
    const position = camera.position.clone()
    expect(focusSelectedRenderRoots({ ...ctx, area: null })).toBe(false)
    expect(camera.position.toArray()).toEqual(position.toArray())
  })
})
