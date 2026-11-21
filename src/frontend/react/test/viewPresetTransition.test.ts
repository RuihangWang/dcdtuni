import { afterEach, describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import {
  animateViewPreset,
  applyViewPreset,
  cancelViewTransition,
  setViewportHandle,
  type ViewportHandle,
} from '../src/viewportBus'

function rig() {
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
  camera.position.set(0, 0, 10)
  const target = new THREE.Vector3()
  const listeners = new Map<string, Set<() => void>>()
  const controls = {
    target,
    update: vi.fn(() => camera.lookAt(target)),
    addEventListener: vi.fn((event: string, listener: () => void) => {
      const group = listeners.get(event) ?? new Set<() => void>()
      group.add(listener)
      listeners.set(event, group)
    }),
    removeEventListener: vi.fn((event: string, listener: () => void) => {
      listeners.get(event)?.delete(listener)
    }),
  }
  setViewportHandle({ camera, controls } as unknown as ViewportHandle)
  return { camera, target, controls, startManualOrbit: () => listeners.get('start')?.forEach(fn => fn()) }
}

function frameClock() {
  let nextId = 0
  const frames = new Map<number, FrameRequestCallback>()
  const cancel = vi.fn((id: number) => { frames.delete(id) })
  vi.spyOn(performance, 'now').mockReturnValue(0)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    const id = ++nextId
    frames.set(id, callback)
    return id
  })
  vi.stubGlobal('cancelAnimationFrame', cancel)
  return {
    step(atMs: number) {
      const next = [...frames.entries()][0]
      expect(next).toBeDefined()
      frames.delete(next![0])
      next![1](atMs)
    },
    pending: () => frames.size,
    cancel,
  }
}

afterEach(() => {
  cancelViewTransition()
  setViewportHandle(null)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('view-cube camera transition', () => {
  it('orbits from the current angle instead of jumping to the clicked face', () => {
    const { camera, target } = rig()
    const clock = frameClock()
    animateViewPreset('right')
    expect(camera.position.toArray()).toEqual([0, 0, 10])
    clock.step(200)
    expect(camera.position.x).toBeGreaterThan(0)
    expect(camera.position.x).toBeLessThan(10)
    expect(camera.position.z).toBeGreaterThan(0)
    expect(camera.position.distanceTo(target)).toBeCloseTo(10, 6)
    clock.step(500)
    expect(camera.position.x).toBeCloseTo(10, 6)
    expect(camera.position.z).toBeCloseTo(0, 6)
    expect(clock.pending()).toBe(0)
  })

  it('lets a new user orbit cancel the transition without an old frame overriding it', () => {
    const { camera, startManualOrbit } = rig()
    const clock = frameClock()
    animateViewPreset('right')
    clock.step(120)
    const position = camera.position.clone()
    startManualOrbit()
    expect(clock.pending()).toBe(0)
    expect(camera.position.distanceTo(position)).toBeCloseTo(0, 8)
    applyViewPreset('front')
    expect(camera.position.x).toBeCloseTo(0, 8)
    expect(camera.position.z).toBeCloseTo(10, 8)
  })

  it('arrives at the top with a stable up vector and keeps the zoom distance', () => {
    const { camera, target } = rig()
    const clock = frameClock()
    animateViewPreset('top')
    clock.step(500)
    expect(camera.position.y).toBeCloseTo(10, 3)
    expect(camera.position.distanceTo(target)).toBeCloseTo(10, 6)
    expect(camera.up.z).toBeLessThan(-0.9)
  })
})
