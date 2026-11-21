import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import { installFirstPerson, type FpsModeApi, type FpsThreeHandles } from '../src/legacy/firstPerson'

let api: FpsModeApi
let canvas: HTMLCanvasElement
let camera: THREE.PerspectiveCamera
let controls: FpsThreeHandles['controls']

beforeEach(async () => {
  vi.stubGlobal('THREE', THREE)
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  // Preserve the module's HUD between entries, as a mounted React page does.
  document.getElementById('fps-toggle-btn')?.remove()
  const button = document.createElement('button')
  button.id = 'fps-toggle-btn'
  document.body.append(button)
  canvas = document.createElement('canvas')
  document.body.append(canvas)
  camera = new THREE.PerspectiveCamera()
  camera.position.set(0, 2, 3)
  controls = { enabled: true, target: new THREE.Vector3(0, 2, 0), update: vi.fn() }
  const scene = new THREE.Scene()
  const group = new THREE.Group()
  group.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()))
  scene.add(group)
  api = await installFirstPerson({
    camera, controls, renderer: { domElement: canvas } as unknown as THREE.WebGLRenderer,
    scene, globalGroup: group,
  })
})

afterEach(() => {
  api.exit()
  canvas.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const settle = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() }

describe('first person when an embedded browser denies pointer lock', () => {
  it('handles document denial once and offers usable drag look without a plain retry', async () => {
    canvas.requestPointerLock = vi.fn(() => Promise.reject(new DOMException('embedded document', 'WrongDocumentError')))
    api.enter()
    await settle()
    expect(canvas.requestPointerLock).toHaveBeenCalledTimes(1)
    expect(api.isActive()).toBe(true)
    expect(document.getElementById('fps-hint')?.textContent).toContain('WASD')
    expect(document.getElementById('fps-toggle-btn')?.textContent).toContain('返回上帝视角')

    const before = api._state.yaw
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 50, clientY: 30, buttons: 1 }))
    expect(api._state.yaw).toBe(before) // dragging a panel never turns the camera
    canvas.dispatchEvent(new MouseEvent('pointerdown', { clientX: 50, clientY: 30, button: 0, buttons: 1 }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 80, clientY: 35, buttons: 1 }))
    expect(api._state.yaw).toBeCloseTo(before - 30 * 0.0022)
    document.dispatchEvent(new MouseEvent('pointerup', { button: 0 }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 120, buttons: 1 }))
    expect(api._state.yaw).toBeCloseTo(before - 30 * 0.0022)

    const position = api._state.pos.clone()
    document.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW' }))
    api._tick(0.05)
    document.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyW' }))
    expect(api._state.pos.distanceTo(position)).toBeGreaterThan(0.1)
    api.exit()
    expect(controls.enabled).toBe(true)
    expect(document.getElementById('fps-toggle-btn')?.textContent).toContain('进入第一人称')
  })

  it('handles rejection of the plain fallback too', async () => {
    canvas.requestPointerLock = vi.fn()
      .mockRejectedValueOnce(new DOMException('raw input unavailable', 'NotSupportedError'))
      .mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'))
    api.enter()
    await settle()
    expect(canvas.requestPointerLock).toHaveBeenCalledTimes(2)
    expect(document.getElementById('fps-hint')?.textContent).toContain('WASD')
  })

  it('does not reopen fallback HUD after a late rejected request', async () => {
    let reject: (reason: unknown) => void = () => {}
    canvas.requestPointerLock = vi.fn(() => new Promise<void>((_, fail) => { reject = fail }))
    api.enter()
    api.exit()
    reject(new DOMException('late denial', 'WrongDocumentError'))
    await settle()
    expect(api.isActive()).toBe(false)
    expect(document.getElementById('fps-hint')?.style.display).toBe('none')
    expect(controls.enabled).toBe(true)
  })

  it('supports missing pointer lock and releases the real collider on exit', () => {
    canvas.requestPointerLock = undefined as unknown as HTMLCanvasElement['requestPointerLock']
    const dispose = vi.spyOn(THREE.BufferGeometry.prototype, 'dispose')
    api.enter()
    expect(document.getElementById('fps-hint')?.textContent).toContain('WASD')
    const before = dispose.mock.calls.length
    api.exit()
    expect(dispose.mock.calls.length).toBe(before + 1)
    dispose.mockRestore()
  })
})
