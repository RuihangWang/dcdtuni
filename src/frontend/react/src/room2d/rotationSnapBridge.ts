/**
 * One 旋转吸附 flag for both editors.
 *
 * The legacy keeps `_rotationSnap3D` on the page and syncs it into the 2D
 * editor by hand: the toolbar button calls `room2d._setRotationSnap(...)`
 * (STL_generate.html:16210) and the 2D toggle calls `setRotationSnap3D(...)`
 * back (`room_2d_editor.js:2124`). Two flags that disagree mean the ring snaps
 * to whole degrees in one view and not in the other, which reads as the toggle
 * being broken.
 *
 * In this port the 3D flag is App state, and the 2D editor cannot reach it.
 * What it CAN reach is the button App renders for it — `#rot-snap-toggle`,
 * which carries `active` exactly when the flag is set. So the button IS the
 * shared flag: reading it reads App's state, and clicking it goes through the
 * app's own handler rather than around it. `selectionBus.requestSelect` already
 * reaches the app's selection the same way, through the tree row it renders.
 *
 * The local copy is the fallback for the case where the toolbar is not mounted
 * — a standalone editor opened through `window.room2d`, and the unit tests.
 */

/** The toolbar button App renders for the 3D rotate gizmo's degree snap. */
export const ROTATION_SNAP_BUTTON_ID = 'rot-snap-toggle'

let local = false

function button(): HTMLElement | null {
  if (typeof document === 'undefined') return null
  return document.getElementById(ROTATION_SNAP_BUTTON_ID)
}

export function isRotationSnapOn(): boolean {
  const btn = button()
  if (btn) return btn.classList.contains('active')
  return local
}

/**
 * Set the flag in both editors.
 *
 * Clicking rather than toggling the class: the class is a RENDER of App's
 * state, so setting it directly would be overwritten on the next render and the
 * 3D gizmo would keep the old behaviour.
 */
export function setRotationSnap(on: boolean): void {
  local = on
  const btn = button()
  if (btn && btn.classList.contains('active') !== on) btn.click()
}

export function toggleRotationSnap(): boolean {
  const next = !isRotationSnapOn()
  setRotationSnap(next)
  return next
}
