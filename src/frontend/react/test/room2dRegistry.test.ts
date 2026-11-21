import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearPending,
  getHandle,
  ifEditor,
  isActive,
  registerEditor,
  withEditor,
  type Room2DHandle,
} from '../src/room2d/room2dRegistry'

function stub(): Room2DHandle {
  const noop = () => {}
  return {
    zoomToFit: vi.fn(),
    setShowAxes: vi.fn(),
    setAnnotations: vi.fn(),
    setBgColor: noop,
    setViewRotation: noop,
    rotateView90: noop,
    setTool: noop,
    toggleGridSnap: noop,
    toggleVertexSnap: noop,
    toggleEdgeAlign: noop,
    toggleRotationSnap: noop,
    setRotationSnap: noop,
    setGridSpacing: noop,
    editGridBadge: noop,
    setHeight: noop,
    setVertexCoord: noop,
    clearAll: noop,
    exportCanvasPNG: () => 'data:image/png;base64,AAA',
    getDebugState: () => ({ active: true }),
    exit: noop,
  }
}

beforeEach(() => {
  clearPending()
  const live = getHandle()
  if (live) registerEditor(live)()
})

describe('registerEditor', () => {
  it('reports the editor as active and unregisters on cleanup', () => {
    expect(isActive()).toBe(false)
    const off = registerEditor(stub())
    expect(isActive()).toBe(true)
    off()
    expect(isActive()).toBe(false)
  })

  it('ignores a stale cleanup, so a remount does not unregister its successor', () => {
    // Two editors overlap for one commit during a remount: the first one's
    // effect cleanup runs AFTER the second has registered.
    const offFirst = registerEditor(stub())
    const second = stub()
    registerEditor(second)
    offFirst()
    expect(getHandle()).toBe(second)
  })
})

describe('withEditor', () => {
  it('runs immediately when the editor is up', () => {
    const handle = stub()
    registerEditor(handle)
    withEditor((h) => h.zoomToFit())
    expect(handle.zoomToFit).toHaveBeenCalledTimes(1)
  })

  it('queues calls made before the mount and replays them in order', () => {
    // The 3-agent SOP fires enterEditor(), setAnnotations(true) and zoomToFit()
    // in ONE page eval, so the last two land before React has mounted anything.
    const order: string[] = []
    withEditor(() => order.push('annotations'))
    withEditor(() => order.push('fit'))
    expect(order).toEqual([])
    registerEditor(stub())
    expect(order).toEqual(['annotations', 'fit'])
  })

  it('drains the queue once, not on every later registration', () => {
    const fn = vi.fn()
    withEditor(fn)
    const off = registerEditor(stub())
    off()
    registerEditor(stub())
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('forgets a queue whose editor never appeared', () => {
    const fn = vi.fn()
    withEditor(fn)
    clearPending()
    registerEditor(stub())
    expect(fn).not.toHaveBeenCalled()
  })
})

describe('ifEditor', () => {
  it('falls back rather than queueing, because a query has to answer now', () => {
    expect(ifEditor((h) => h.exportCanvasPNG(), null)).toBeNull()
    registerEditor(stub())
    expect(ifEditor((h) => h.exportCanvasPNG(), null)).toContain('data:image/png')
  })
})
