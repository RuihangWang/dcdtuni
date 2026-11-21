import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  claimRoom2dPanel,
  getRoom2dPanelHost,
  isRoom2dPanelOpen,
  releaseRoom2dPanel,
  setRoom2dPanelHost,
  subscribeRoom2dPanel,
} from '../src/room2d/room2dPanelSlot'

const first = {}
const second = {}

beforeEach(() => {
  releaseRoom2dPanel(first)
  releaseRoom2dPanel(second)
  setRoom2dPanelHost(null)
})

describe('room2d panel slot', () => {
  it('is closed until an editor claims it', () => {
    expect(isRoom2dPanelOpen()).toBe(false)
    claimRoom2dPanel(first)
    expect(isRoom2dPanelOpen()).toBe(true)
    releaseRoom2dPanel(first)
    expect(isRoom2dPanelOpen()).toBe(false)
  })

  it('ignores a release from an editor that no longer owns the slot', () => {
    // The order `dropStandaloneIfAny` produces: the second editor claims, then
    // the first one's deferred teardown runs. A release that did not check the
    // token would close the panel of the editor now on screen.
    claimRoom2dPanel(first)
    claimRoom2dPanel(second)
    releaseRoom2dPanel(first)
    expect(isRoom2dPanelOpen()).toBe(true)
    releaseRoom2dPanel(second)
    expect(isRoom2dPanelOpen()).toBe(false)
  })

  it('notifies subscribers on claim, release and host changes', () => {
    const seen = vi.fn()
    const stop = subscribeRoom2dPanel(seen)
    claimRoom2dPanel(first)
    expect(seen).toHaveBeenCalledTimes(1)
    // A repeat claim by the same owner is not a change.
    claimRoom2dPanel(first)
    expect(seen).toHaveBeenCalledTimes(1)
    const el = { nodeType: 1 } as unknown as HTMLElement
    setRoom2dPanelHost(el)
    expect(seen).toHaveBeenCalledTimes(2)
    setRoom2dPanelHost(el)
    expect(seen).toHaveBeenCalledTimes(2)
    releaseRoom2dPanel(first)
    expect(seen).toHaveBeenCalledTimes(3)
    stop()
    claimRoom2dPanel(first)
    expect(seen).toHaveBeenCalledTimes(3)
  })

  it('hands back the host element the shell published', () => {
    const el = { nodeType: 1 } as unknown as HTMLElement
    expect(getRoom2dPanelHost()).toBe(null)
    setRoom2dPanelHost(el)
    expect(getRoom2dPanelHost()).toBe(el)
    // React passes null when the host unmounts — a tab switch takes the right
    // column away, and the editor then has to fall back to its own column.
    setRoom2dPanelHost(null)
    expect(getRoom2dPanelHost()).toBe(null)
  })
})
