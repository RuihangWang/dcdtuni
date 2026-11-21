import { beforeEach, describe, expect, it } from 'vitest'
import {
  hideBusyOverlay,
  isBusyOverlayOpen,
  runBlocking,
  showBusyOverlay,
} from '../src/run/busyOverlay'

/**
 * The overlay exists so a multi-second synchronous rebuild reads as work rather
 * than as a hang. Two things make it work at all and both are pinned here: it
 * must paint BEFORE the work starts, and it must come down even when the work
 * throws — an overlay left up over a live app is worse than the freeze it was
 * explaining.
 */

beforeEach(() => {
  while (isBusyOverlayOpen()) hideBusyOverlay()
  document.getElementById('loading')?.remove()
  document.getElementById('busy-overlay-style')?.remove()
})

describe('showBusyOverlay', () => {
  it('mounts the legacy #loading host with both lines of text', () => {
    showBusyOverlay()
    const host = document.getElementById('loading')!
    expect(host.classList.contains('busy-open')).toBe(true)
    expect(host.querySelector('#loading-text')?.textContent).toBeTruthy()
    expect(host.querySelector('.busy-hint')?.textContent).toBeTruthy()
    hideBusyOverlay()
    expect(host.classList.contains('busy-open')).toBe(false)
  })

  it('nests, so an inner pass cannot uncover the outer one', () => {
    // The package export runs the STL builder inside its own overlay.
    showBusyOverlay()
    showBusyOverlay()
    hideBusyOverlay()
    expect(document.getElementById('loading')?.classList.contains('busy-open')).toBe(true)
    hideBusyOverlay()
    expect(document.getElementById('loading')?.classList.contains('busy-open')).toBe(false)
  })
})

describe('runBlocking', () => {
  it('has the overlay up before the work runs and down after', async () => {
    let sawOverlay = false
    const result = await runBlocking(() => {
      sawOverlay = document.getElementById('loading')!.classList.contains('busy-open')
      return 42
    })
    expect(sawOverlay).toBe(true)
    expect(result).toBe(42)
    expect(isBusyOverlayOpen()).toBe(false)
  })

  it('takes the overlay down when the work throws', async () => {
    await expect(
      runBlocking(() => {
        throw new Error('CSG failed')
      }),
    ).rejects.toThrow('CSG failed')
    expect(isBusyOverlayOpen()).toBe(false)
    expect(document.getElementById('loading')?.classList.contains('busy-open')).toBe(false)
  })
})
