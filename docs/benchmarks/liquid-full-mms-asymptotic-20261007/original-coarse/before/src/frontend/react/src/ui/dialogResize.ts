/**
 * Drag-resize for a modal dialog.
 *
 * The folder browser is the one that needs it: picking a case directory means
 * reading `/mnt/lv/home/.../case/<name>` out of a list, and the path does not
 * fit the dialog's CSS width. The legacy puts handles on all four edges and all
 * four corners, with a 380x320 floor (`fb-resize-*`, STL_generate.html:18934,
 * drag logic at :18959-19020), and this is that in one place so the next dialog
 * that needs it does not grow its own copy.
 */

export interface Rect {
  left: number
  top: number
  width: number
  height: number
}

/** The legacy's floor. Below this the list has no room for a name. */
export const MIN_DIALOG = { width: 380, height: 320 }

/** The legacy's eight, in its own order. */
export const RESIZE_DIRECTIONS = ['r', 'b', 'l', 't', 'br', 'bl', 'tr', 'tl'] as const

export type ResizeDir = (typeof RESIZE_DIRECTIONS)[number]

/**
 * Where the box lands when `dir` is dragged by (dx, dy) from `start`.
 *
 * The far edge stays put: dragging the LEFT edge moves `left` and widens by the
 * same amount, so the edge tracks the pointer instead of sliding out from under
 * it — and when the minimum bites, the box stops shrinking rather than walking
 * across the screen.
 */
export function resizeRect(
  start: Rect,
  dir: ResizeDir,
  dx: number,
  dy: number,
  min: { width: number; height: number } = MIN_DIALOG,
): Rect {
  let { left, top, width, height } = start
  if (dir.includes('r')) width = Math.max(min.width, start.width + dx)
  if (dir.includes('b')) height = Math.max(min.height, start.height + dy)
  if (dir.includes('l')) {
    width = Math.max(min.width, start.width - dx)
    left = start.left + start.width - width
  }
  if (dir.includes('t')) {
    height = Math.max(min.height, start.height - dy)
    top = start.top + start.height - height
  }
  return { left, top, width, height }
}

/** The cursor each handle shows, and where it sits on the box. */
export const HANDLE_STYLE: Record<ResizeDir, { cursor: string; style: Record<string, string> }> = {
  r: { cursor: 'ew-resize', style: { top: '0', right: '-3px', width: '6px', height: '100%' } },
  b: { cursor: 'ns-resize', style: { left: '0', bottom: '-3px', width: '100%', height: '6px' } },
  l: { cursor: 'ew-resize', style: { top: '0', left: '-3px', width: '6px', height: '100%' } },
  t: { cursor: 'ns-resize', style: { left: '0', top: '-3px', width: '100%', height: '6px' } },
  br: { cursor: 'nwse-resize', style: { right: '-3px', bottom: '-3px', width: '12px', height: '12px' } },
  bl: { cursor: 'nesw-resize', style: { left: '-3px', bottom: '-3px', width: '12px', height: '12px' } },
  tr: { cursor: 'nesw-resize', style: { right: '-3px', top: '-3px', width: '12px', height: '12px' } },
  tl: { cursor: 'nwse-resize', style: { left: '-3px', top: '-3px', width: '12px', height: '12px' } },
}
