/**
 * View state the timeline strip and the track panel share: whether the panel
 * is open. Not part of the project (a view preference, like the toolbar's
 * collapse), so it lives here rather than in the store.
 */
const listeners = new Set<() => void>()
let open = false

export function trackPanelOpen(): boolean {
  return open
}

export function setTrackPanelOpen(next: boolean): void {
  if (next === open) return
  open = next
  listeners.forEach((fn) => fn())
}

export function subscribeTrackPanel(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}
