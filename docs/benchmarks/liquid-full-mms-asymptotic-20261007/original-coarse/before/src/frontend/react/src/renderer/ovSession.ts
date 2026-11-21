/** Which renderer owns the viewport right now, and whether the OV side is
 * ready/confirmed. The post-process tab reads this to show the OV control
 * block only while Omniverse actually owns the display; RendererControl is
 * the single writer. Kept outside React state because the panel lives in a
 * different subtree from the renderer switch. */
import type { OvCommandClient } from './ovProtocol'

export interface OvSessionState {
  mode: 'three' | 'ov'
  ready: boolean
  /** Acknowledgement channel dead but video live: display-only, unverified. */
  degraded: boolean
  client: OvCommandClient | null
  /** Kit colours liquid results itself (verified grant): Three.js result
   * recolouring must not trigger scene resyncs, and snapshots export the
   * un-painted materials. Reset whenever the OV session ends. */
  liquidActive: boolean
  /** Resolves when no scene transfer is queued or in flight; commands that
   * need a prompt ack (liquid result load) wait for it. */
  sceneIdle: (() => Promise<void>) | null
  /** Captured from the validated Kit scene ACK, never from a display packet. */
  projectUid: string | null
  projectContentSha256: string | null
  sceneRevision: number | null
}

let state: OvSessionState = { mode: 'three', ready: false, degraded: false, client: null, liquidActive: false, sceneIdle: null, projectUid: null, projectContentSha256: null, sceneRevision: null }
const listeners = new Set<() => void>()

export function ovSession(): OvSessionState {
  return state
}

export function setOvSession(patch: Partial<OvSessionState>): void {
  state = { ...state, ...patch }
  listeners.forEach(fn => fn())
}

export function subscribeOvSession(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}
