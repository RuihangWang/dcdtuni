import { describe, expect, it, vi } from 'vitest'
import { OvDisplayDistributor, ovDisplay, setOvDisplay } from '../src/renderer/displayDistributor'
import type { OvCommandClient } from '../src/renderer/ovProtocol'

const client = {} as OvCommandClient

describe('OV display distributor', () => {
  it('counts what each channel sent and Kit acknowledged, failures included', async () => {
    const display = new OvDisplayDistributor(client)
    expect(display.channels().map(c => c.channel)).toEqual(['scene', 'view', 'overlay', 'result', 'postprocess', 'liquid'])
    display.delivered('scene')
    const view = display.track('view', async (n: number) => n * 2)
    await expect(view(21)).resolves.toBe(42)
    const overlay = display.track('overlay', async () => { throw new Error('Kit refused the overlay') })
    await expect(overlay()).rejects.toThrow('refused')
    const byName = Object.fromEntries(display.channels().map(c => [c.channel, c]))
    expect(byName.scene).toMatchObject({ state: 'delivered', sent: 1, delivered: 1 })
    expect(byName.view).toMatchObject({ state: 'delivered', sent: 1, delivered: 1, error: null })
    expect(byName.overlay).toMatchObject({ state: 'failed', sent: 1, delivered: 0, error: 'Kit refused the overlay' })
    expect(byName.result!.state).toBe('idle')
  })

  it('reports self-managed channels live and closes every queue and teardown once', () => {
    const display = new OvDisplayDistributor(client)
    let liquid: 'idle' | 'sending' | 'delivered' = 'sending'
    display.report('liquid', () => ({ state: liquid, detail: 'loading' }))
    expect(display.channels().find(c => c.channel === 'liquid')!.state).toBe('sending')
    liquid = 'delivered'
    expect(display.channels().find(c => c.channel === 'liquid')!.state).toBe('delivered')
    const queue = { close: vi.fn() }, teardown = vi.fn(), listener = vi.fn()
    display.own('scene', queue)
    display.onClose(teardown)
    display.subscribe(listener)
    setOvDisplay(display)
    expect(ovDisplay()).toBe(display)
    display.close()
    display.close()
    expect(queue.close).toHaveBeenCalledTimes(1)
    expect(teardown).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalled()
    expect(display.channels().every(c => c.state === 'closed')).toBe(true)
    const late = vi.fn()
    display.onClose(late)  // registered after the session ended: torn down at once
    expect(late).toHaveBeenCalledTimes(1)
    setOvDisplay(null)
  })
})
