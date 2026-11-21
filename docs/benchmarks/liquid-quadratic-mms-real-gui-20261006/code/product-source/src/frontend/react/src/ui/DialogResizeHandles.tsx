import { useRef, type RefObject } from 'react'
import {
  HANDLE_STYLE,
  MIN_DIALOG,
  RESIZE_DIRECTIONS,
  resizeRect,
  type ResizeDir,
} from './dialogResize'

/**
 * Eight drag handles around a modal, as the legacy folder browser has
 * (STL_generate.html:18934).
 *
 * ## The pin
 *
 * The modal is centred by its backdrop's flexbox, so writing a wider `width`
 * alone moves BOTH edges — the one under the cursor at half speed. The drag
 * therefore fixes the box at the rect it started from (`position: fixed` with
 * the measured left/top/width/height) before it changes anything, and every
 * frame after that is plain arithmetic on that rect.
 *
 * Listeners go on the WINDOW rather than the handle: a pointer that leaves a
 * 6 px strip mid-drag must keep resizing, and pointer capture on an element
 * React may re-render is the fiddlier half of the same guarantee.
 */
export function DialogResizeHandles({
  targetRef,
  min = MIN_DIALOG,
}: {
  targetRef: RefObject<HTMLElement>
  min?: { width: number; height: number }
}) {
  const drag = useRef<{ dir: ResizeDir; x: number; y: number; rect: DOMRect } | null>(null)

  function start(e: React.PointerEvent, dir: ResizeDir) {
    const el = targetRef.current
    if (!el) return
    e.preventDefault()
    e.stopPropagation()
    const rect = el.getBoundingClientRect()
    drag.current = { dir, x: e.clientX, y: e.clientY, rect }
    // Pin first: see the note above.
    el.style.position = 'fixed'
    el.style.margin = '0'
    el.style.left = `${rect.left}px`
    el.style.top = `${rect.top}px`
    el.style.width = `${rect.width}px`
    el.style.height = `${rect.height}px`
    el.style.maxHeight = 'none'

    const move = (ev: PointerEvent) => {
      const d = drag.current
      const node = targetRef.current
      if (!d || !node) return
      const next = resizeRect(
        { left: d.rect.left, top: d.rect.top, width: d.rect.width, height: d.rect.height },
        d.dir,
        ev.clientX - d.x,
        ev.clientY - d.y,
        min,
      )
      node.style.left = `${next.left}px`
      node.style.top = `${next.top}px`
      node.style.width = `${next.width}px`
      node.style.height = `${next.height}px`
    }
    const end = () => {
      drag.current = null
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', end)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
  }

  return (
    <>
      {RESIZE_DIRECTIONS.map((dir) => (
        <div
          key={dir}
          className={`fb-resize fb-resize-${dir}`}
          onPointerDown={(e) => start(e, dir)}
          style={{
            position: 'absolute',
            zIndex: 5,
            touchAction: 'none',
            cursor: HANDLE_STYLE[dir].cursor,
            ...HANDLE_STYLE[dir].style,
          }}
        />
      ))}
    </>
  )
}
