/**
 * One display distributor per Omniverse session.
 *
 * Kit's display is fed by six channels: the scene, the view (camera,
 * selection, background grid, lighting), the mesh-preview overlay, the
 * mesh-preview result layers, the post-processing frame and the liquid
 * colouring. Each was created, replayed and closed on its own, in three
 * places (ready, failure, return to Three), and the liquid one only ran while
 * the liquid 3D panel happened to be mounted, so Kit showed no liquid colours
 * from any other tab.
 *
 * The distributor is made when Kit acknowledges the first scene and closed
 * when the session ends. It owns the session's queues (`own`), counts what each
 * channel sent and what Kit acknowledged (`track`), runs every channel's
 * start-of-session replay (`onClose` holds their teardown), and reports all
 * six in one place (`channels`, also `window.__dcdtuni.ovDisplay()`).
 */

import type { OvCommandClient } from './ovProtocol'

export type DisplayChannel = 'scene' | 'view' | 'overlay' | 'result' | 'postprocess' | 'liquid'
export const DISPLAY_CHANNELS: readonly DisplayChannel[] = ['scene', 'view', 'overlay', 'result', 'postprocess', 'liquid']

export interface DisplayChannelStatus {
  channel: DisplayChannel
  /** idle: nothing sent yet; sending: awaiting Kit; delivered: Kit acknowledged the last one. */
  state: 'idle' | 'sending' | 'delivered' | 'failed' | 'closed'
  sent: number
  delivered: number
  /** Date.now() of the last acknowledgement. */
  lastDeliveredAt: number | null
  error: string | null
  /** A channel that keeps its own state (post-processing, liquid) adds it here. */
  detail?: string
}

type Report = () => Pick<DisplayChannelStatus, 'state'> & Partial<Pick<DisplayChannelStatus, 'error' | 'detail'>>

export class OvDisplayDistributor {
  private status = new Map<DisplayChannel, DisplayChannelStatus>()
  private reports = new Map<DisplayChannel, Report>()
  private closers: (() => void)[] = []
  private listeners = new Set<() => void>()
  private closedFlag = false

  constructor(readonly client: OvCommandClient) {
    for (const channel of DISPLAY_CHANNELS) {
      this.status.set(channel, { channel, state: 'idle', sent: 0, delivered: 0, lastDeliveredAt: null, error: null })
    }
  }

  get closed(): boolean { return this.closedFlag }

  /** `send` with this channel's sends and acknowledgements counted. */
  track<A extends unknown[], R>(channel: DisplayChannel, send: (...args: A) => Promise<R>): (...args: A) => Promise<R> {
    return async (...args: A) => {
      if (this.closedFlag) return send(...args)
      this.patch(channel, s => ({ ...s, state: 'sending', sent: s.sent + 1 }))
      try {
        const result = await send(...args)
        this.patch(channel, s => ({ ...s, state: 'delivered', delivered: s.delivered + 1, lastDeliveredAt: Date.now(), error: null }))
        return result
      } catch (error) {
        this.patch(channel, s => ({ ...s, state: 'failed', error: error instanceof Error ? error.message : String(error) }))
        throw error
      }
    }
  }

  /** Something Kit acknowledged outside a tracked send (the first scene comes with the connection). */
  delivered(channel: DisplayChannel): void {
    this.patch(channel, s => ({ ...s, state: 'delivered', sent: s.sent + 1, delivered: s.delivered + 1, lastDeliveredAt: Date.now(), error: null }))
  }

  /** The session's queue for a channel; closed with the session. */
  own<T extends { close(): void }>(channel: DisplayChannel, queue: T): T {
    void channel
    this.closers.push(() => queue.close())
    return queue
  }

  /** A channel that keeps its own state reports it live instead of being counted. */
  report(channel: DisplayChannel, report: Report): void {
    this.reports.set(channel, report)
  }

  onClose(teardown: () => void): void {
    if (this.closedFlag) teardown()
    else this.closers.push(teardown)
  }

  close(): void {
    if (this.closedFlag) return
    this.closedFlag = true
    for (const teardown of this.closers.splice(0).reverse()) {
      try { teardown() } catch { /* one channel's teardown must not keep the others open */ }
    }
    for (const channel of DISPLAY_CHANNELS) this.patch(channel, s => ({ ...s, state: 'closed' }), true)
    this.reports.clear()
    this.notify()
  }

  channels(): DisplayChannelStatus[] {
    return DISPLAY_CHANNELS.map(channel => {
      const base = this.status.get(channel)!
      const report = this.closedFlag ? undefined : this.reports.get(channel)
      if (!report) return { ...base }
      try { return { ...base, ...report() } } catch (error) {
        return { ...base, state: 'failed', error: error instanceof Error ? error.message : String(error) }
      }
    })
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  private patch(channel: DisplayChannel, f: (s: DisplayChannelStatus) => DisplayChannelStatus, quiet = false) {
    this.status.set(channel, f(this.status.get(channel)!))
    if (!quiet) this.notify()
  }

  private notify() { this.listeners.forEach(listener => listener()) }
}

let active: OvDisplayDistributor | null = null
const activeListeners = new Set<() => void>()

/** The live session's distributor; null in Three.js mode. */
export function ovDisplay(): OvDisplayDistributor | null { return active }

export function setOvDisplay(next: OvDisplayDistributor | null): void {
  if (active === next) return
  active = next
  activeListeners.forEach(listener => listener())
}

export function subscribeOvDisplay(listener: () => void): () => void {
  activeListeners.add(listener)
  return () => { activeListeners.delete(listener) }
}
