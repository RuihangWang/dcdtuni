/**
 * Where the 2D editor's property column goes: the app's own right panel.
 *
 * The legacy editor writes its controls into the SHARED `#prop-title` /
 * `#prop-content` (`room_2d_editor.js:1955-2015`), so the floor plan's
 * properties take the 3D inspector's place in the one right column. The port
 * drew a second column of its own inside the editor overlay instead, which put
 * 640px of the window under two side panels and left the right one showing a
 * 3D inspector for a scene the user is no longer looking at.
 *
 * The two halves live in different React trees — the editor can be mounted by
 * App or by `window.room2d.enterEditor()` into a root of its own — so this
 * module is the link between them: the shell publishes the host element it
 * renders inside `#right-panel`, and the editor portals its column into it.
 *
 * An editor CLAIMS the slot with a token of its own and only the claimant can
 * release it. When the button-opened editor takes over from an API-mounted one
 * (`dropStandaloneIfAny`), the loser's teardown runs a tick AFTER the winner's
 * claim, and an untokened release would close a slot that is in use.
 */

type Listener = () => void

let owner: object | null = null
let host: HTMLElement | null = null
const listeners = new Set<Listener>()

function emit(): void {
  // Copied first: a listener that unsubscribes itself while being notified
  // would otherwise mutate the set mid-iteration.
  for (const fn of [...listeners]) fn()
}

/** Subscribe to slot changes — both the claim and the host element. */
export function subscribeRoom2dPanel(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** Take the slot. Re-claiming with the same token is a no-op, not a re-render. */
export function claimRoom2dPanel(token: object): void {
  if (owner === token) return
  owner = token
  emit()
}

/** Give the slot back. Ignored unless `token` is the current owner. */
export function releaseRoom2dPanel(token: object): void {
  if (owner !== token) return
  owner = null
  emit()
}

/** True while an editor holds the slot — the right column then shows the plan. */
export function isRoom2dPanelOpen(): boolean {
  return owner !== null
}

export function getRoom2dPanelHost(): HTMLElement | null {
  return host
}

/**
 * Publish the element the column is portaled into. A React callback ref: React
 * passes the node on mount and `null` on unmount.
 */
export function setRoom2dPanelHost(el: HTMLElement | null): void {
  if (host === el) return
  host = el
  emit()
}
