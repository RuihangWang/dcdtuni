/** Camera-settled notifications: the hook the OV camera linkage subscribes to.
 * The view cube, toolbar presets and finished orbit gestures must each notify
 * exactly the discrete moments worth forwarding to a remote renderer — never
 * the continuous orbit frames. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import {
  applyViewPreset,
  animateViewPreset,
  notifyCameraSettled,
  setViewportHandle,
  subscribeCameraSettled,
  type ViewportHandle,
} from '../src/viewportBus'

function fakeControls() {
  const handlers: Record<string, (() => void)[]> = {}
  return {
    target: new THREE.Vector3(0, 0, 0),
    update: vi.fn(),
    addEventListener: (name: string, fn: () => void) => {
      ;(handlers[name] ??= []).push(fn)
    },
    removeEventListener: (name: string, fn: () => void) => {
      handlers[name] = (handlers[name] ?? []).filter(f => f !== fn)
    },
    fire: (name: string) => (handlers[name] ?? []).forEach(fn => fn()),
  }
}

function rig() {
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100)
  camera.position.set(4, 3, 4)
  const controls = fakeControls()
  const handle = { camera, controls } as unknown as ViewportHandle & { controls: ReturnType<typeof fakeControls> }
  setViewportHandle(handle)
  return handle
}

afterEach(() => {
  setViewportHandle(null)
  vi.useRealTimers()
})

describe('subscribeCameraSettled', () => {
  it('notifies on discrete preset application', () => {
    rig()
    const fn = vi.fn()
    const off = subscribeCameraSettled(fn)
    applyViewPreset('top')
    expect(fn).toHaveBeenCalledTimes(1)
    applyViewPreset('iso')
    expect(fn).toHaveBeenCalledTimes(2)
    off()
    applyViewPreset('front')
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('notifies when an orbit gesture ends (controls "end")', () => {
    const handle = rig()
    const fn = vi.fn()
    subscribeCameraSettled(fn)
    handle.controls.fire('end')
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('stops notifying for a replaced handle', () => {
    const first = rig()
    const fn = vi.fn()
    subscribeCameraSettled(fn)
    setViewportHandle(null)
    first.controls.fire('end')
    expect(fn).not.toHaveBeenCalled()
  })

  it('notifies once when a preset flight completes', () => {
    rig()
    const fn = vi.fn()
    subscribeCameraSettled(fn)
    // The flight measures progress with performance.now against the rAF
    // timestamp; both clocks must be faked or they disagree forever.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance'] })
    // jsdom provides requestAnimationFrame; fake timers drive it.
    animateViewPreset('top', 100)
    for (let i = 0; i < 30 && fn.mock.calls.length === 0; i++) {
      vi.advanceTimersByTime(20)
    }
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('does not notify during the flight itself', () => {
    rig()
    const fn = vi.fn()
    subscribeCameraSettled(fn)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance'] })
    animateViewPreset('left', 400)
    vi.advanceTimersByTime(100)
    expect(fn).not.toHaveBeenCalled()
  })

  it('throttles continuous change events (live drag) and flushes on gesture end', () => {
    const handle = rig()
    const fn = vi.fn()
    subscribeCameraSettled(fn)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] })
    // A 200 ms drag at pointer rate: ten change events.
    for (let i = 0; i < 10; i++) {
      handle.controls.fire('change')
      vi.advanceTimersByTime(20)
    }
    vi.advanceTimersByTime(200)
    // Leading edge + at most a couple of throttle windows + trailing edge —
    // never one emit per frame.
    expect(fn.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(fn.mock.calls.length).toBeLessThan(10)
    const before = fn.mock.calls.length
    handle.controls.fire('end')
    expect(fn).toHaveBeenCalledTimes(before + 1) // end flushes immediately
  })

  it('exposes notifyCameraSettled for programmatic camera moves outside the bus', () => {
    const fn = vi.fn()
    subscribeCameraSettled(fn)
    notifyCameraSettled()
    expect(fn).toHaveBeenCalledTimes(1)
  })
})
