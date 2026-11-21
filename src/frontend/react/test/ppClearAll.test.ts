import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  _resetPpClearAllListeners,
  requestPpClearAll,
  subscribePpClearAll,
} from '../src/postprocess/ppClearAll'

/**
 * The teardown signal behind 清除全部渲染.
 *
 * The button is in the right panel and could already empty the Three.js group.
 * Everything else the legacy's `ppClearAll` (STL_generate.html:24129) tears
 * down belongs to another subtree — the slice and streamline sets and the id
 * counter are the LEFT panel's, the 15% geometry dimming is the right panel's
 * own slider — so a press wiped the picture and left the lists behind.
 *
 * Fires synchronously, and to a COPY of the set, so a subscriber that
 * unsubscribes while being notified cannot make the loop skip its neighbour.
 */

afterEach(() => _resetPpClearAllListeners())

describe('requestPpClearAll', () => {
  it('reaches every subscriber', () => {
    const a = vi.fn()
    const b = vi.fn()
    subscribePpClearAll(a)
    subscribePpClearAll(b)
    requestPpClearAll()
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(1)
  })

  it('is a no-op with nobody listening — the panels mount independently', () => {
    expect(() => requestPpClearAll()).not.toThrow()
  })

  it('stops calling one that unsubscribed', () => {
    const gone = vi.fn()
    const stop = subscribePpClearAll(gone)
    stop()
    requestPpClearAll()
    expect(gone).not.toHaveBeenCalled()
  })

  it('still notifies the others when one unsubscribes DURING the notify', () => {
    const late = vi.fn()
    let stopSelf: () => void = () => {}
    stopSelf = subscribePpClearAll(() => stopSelf())
    subscribePpClearAll(late)
    requestPpClearAll()
    // Iterating the live Set would have skipped `late` here.
    expect(late).toHaveBeenCalledTimes(1)
  })

  it('fires again on a second press', () => {
    const fn = vi.fn()
    subscribePpClearAll(fn)
    requestPpClearAll()
    requestPpClearAll()
    expect(fn).toHaveBeenCalledTimes(2)
  })
})
