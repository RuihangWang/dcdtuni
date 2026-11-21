/**
 * Arithmetic for the app frame: panel widths, the log dock and the toolbar.
 *
 * Kept out of the components because every one of these numbers was a bug at
 * some point and a bug in a number is much easier to pin down in a test than in
 * a browser. The DOM writes stay in `PanelResizers` / `LogPanel`.
 */

export type ResizeEdge = 'left' | 'right' | 'toolbar' | 'log'

/** Below these the handle is no longer grabbable and the panel is unusable. */
export const MIN_SIZE: Record<ResizeEdge, number> = {
  left: 240,
  right: 240,
  toolbar: 28,
  log: 28,
}

/**
 * The viewport a drag must leave behind.
 *
 * Clamping each side panel to half the window is not enough: dragging BOTH to
 * half covers the window edge to edge and the 3D view disappears with no handle
 * left over the canvas to drag back.
 */
export const MIN_VIEWPORT_WIDTH = 120

/**
 * The share of the window height the toolbar and the log dock may each take.
 *
 * 0.5 is the legacy editor's own ceiling for both drags
 * (STL_generate.html:17449 for the toolbar, :29683 for the log).
 */
export const MAX_HEIGHT_FRACTION = 0.5

/**
 * How much of the window a WRAPPING toolbar may take before its tool body scrolls.
 *
 * Separate from `MAX_HEIGHT_FRACTION`, which caps a deliberate DRAG: if a user
 * drags the log dock to half the window that is their decision, but a toolbar
 * growing to half the window is just wrapping, and nobody asked for it. Measured
 * before this existed: 249px of a 700px-tall window at 1100 wide, 36% of the
 * screen spent on buttons. It now scrolls past 28% instead of stacking further.
 */
export const TOOLBAR_AUTO_HEIGHT_FRACTION = 0.28

/** How wide a side panel may actually be drawn, whatever width was asked for. */
export function panelFitWidth(requested: number, windowWidth: number): number {
  return Math.min(requested, Math.max(0, (windowWidth - MIN_VIEWPORT_WIDTH) / 2))
}

/**
 * The size a resize drag lands on, from the pointer position alone.
 *
 * The pointer, not a delta: the handle sits under the cursor for the whole
 * drag, so a delta accumulates rounding and the handle drifts off the pointer.
 */
export function clampResize(
  edge: ResizeEdge,
  clientX: number,
  clientY: number,
  windowWidth: number,
  windowHeight: number,
): number {
  let value: number
  let limit: number
  if (edge === 'left') {
    value = clientX
    limit = Math.max(MIN_SIZE.left, (windowWidth - MIN_VIEWPORT_WIDTH) / 2)
  } else if (edge === 'right') {
    value = windowWidth - clientX
    limit = Math.max(MIN_SIZE.right, (windowWidth - MIN_VIEWPORT_WIDTH) / 2)
  } else if (edge === 'toolbar') {
    value = clientY
    limit = Math.max(MIN_SIZE.toolbar, windowHeight * MAX_HEIGHT_FRACTION)
  } else {
    value = windowHeight - clientY
    limit = Math.max(MIN_SIZE.log, windowHeight * MAX_HEIGHT_FRACTION)
  }
  return Math.min(Math.max(value, MIN_SIZE[edge]), limit)
}

/** The log dock's height when its header is all that shows. */
export const COLLAPSED_LOG_HEIGHT = 28

/**
 * What `--log-panel-height` becomes on a collapse toggle, and what to remember.
 *
 * Which way the toggle goes is read off the CURRENT height, not off a boolean
 * held beside it — the same decision the legacy makes (STL_generate.html:4377).
 * The dock can also be dragged open from its collapsed state, and a boolean
 * would then disagree with the dock in front of the user: their next click
 * would fold something already folded.
 *
 * The height a user dragged the dock to has to survive a collapse/expand round
 * trip, and a collapsed dock must never be remembered as the height to come
 * back to — that is how a toggle ends up doing nothing on the second click.
 */
export function toggleLogDock(
  currentPx: number,
  rememberedPx: number,
): { heightPx: number; rememberPx: number } {
  if (currentPx <= COLLAPSED_LOG_HEIGHT) {
    return { heightPx: rememberedPx, rememberPx: rememberedPx }
  }
  return { heightPx: COLLAPSED_LOG_HEIGHT, rememberPx: currentPx }
}

/**
 * The height to draw the toolbar at.
 *
 * The toolbar wraps, so as the window narrows its natural height grows without
 * bound — measured at 1245px inside a 480px-tall window, which leaves no
 * viewport at all. Automatic wrapping is capped at28%, and deliberate drags
 * at half the window. Reserve the footer and an operable tool body within
 * that cap; unusually tall footers scroll internally at their natural text size.
 */
export function toolbarTargetHeight(
  naturalHeight: number,
  manualHeight: number | null,
  windowHeight: number,
  reservedHeight = MIN_SIZE.toolbar,
): number {
  // A dragged height is the user's call and gets the generous cap; an
  // un-dragged toolbar that merely wrapped gets the tighter one.
  const fraction = manualHeight === null ? TOOLBAR_AUTO_HEIGHT_FRACTION : MAX_HEIGHT_FRACTION
  const cap = Math.max(MIN_SIZE.toolbar, windowHeight * fraction)
  const wanted = manualHeight === null ? naturalHeight : Math.min(manualHeight, naturalHeight)
  return Math.max(1, Math.min(Math.max(wanted, reservedHeight), naturalHeight, cap))
}

/**
 * The transform that squeezes a `naturalHeight` toolbar into `targetHeight`.
 *
 * An empty transform (not `scale(1)`) when it already fits: a transform of any
 * kind makes the toolbar a containing block for its fixed-position children and
 * creates a stacking context, so a no-op scale is not a no-op.
 */
export function toolbarScaleStyle(
  targetHeight: number,
  naturalHeight: number,
): { transform: string; height: string } {
  if (naturalHeight <= 0 || targetHeight >= naturalHeight) return { transform: '', height: '' }
  return { transform: `scale(${targetHeight / naturalHeight})`, height: `${naturalHeight}px` }
}
