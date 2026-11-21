import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useRef } from 'react'
import { MIN_DIALOG, RESIZE_DIRECTIONS, resizeRect } from '../src/ui/dialogResize'
import { DialogResizeHandles } from '../src/ui/DialogResizeHandles'
import { FileBrowser } from '../src/components/FileBrowser'

/**
 * The folder browser can be dragged bigger.
 *
 * It is how a deep path is read: `/mnt/lv/home/litianyu/mycode/dcdtuni/case/...`
 * does not fit a 760 px dialog, and the legacy puts handles on all four edges
 * and all four corners for it (`fb-resize-{r,b,l,t,br,bl,tr,tl}`,
 * STL_generate.html:18934, min 380x320 at :18959-19020). React's dialog is
 * pinned by CSS at `min(760px, 88vw)` with no handles and not even a native
 * `resize: both`.
 *
 * ## Why the rect, not the element, is what gets tested
 *
 * jsdom lays nothing out, so a test that drags a real handle can only assert
 * the inline style the drag wrote. The decision it wrote is the arithmetic
 * below — which edge moves, which one stays, and where the minimum bites — and
 * that is worth pinning on its own.
 */

const START = { left: 100, top: 50, width: 760, height: 560 }

describe('resizeRect', () => {
  it('grows to the right without moving the left edge', () => {
    expect(resizeRect(START, 'r', 120, 0)).toMatchObject({ left: 100, width: 880 })
  })

  it('grows down without moving the top', () => {
    expect(resizeRect(START, 'b', 0, 90)).toMatchObject({ top: 50, height: 650 })
  })

  it('moves the left edge with the pointer', () => {
    // Dragging left by 100 must widen by 100 AND move the box left by 100, or
    // the edge slides out from under the cursor.
    expect(resizeRect(START, 'l', -100, 0)).toMatchObject({ left: 0, width: 860 })
  })

  it('moves the top edge with the pointer', () => {
    expect(resizeRect(START, 't', 0, -30)).toMatchObject({ top: 20, height: 590 })
  })

  it('does both on a corner', () => {
    expect(resizeRect(START, 'tl', -40, -20)).toMatchObject({
      left: 60,
      top: 30,
      width: 800,
      height: 580,
    })
  })

  it('stops at the minimum rather than inverting', () => {
    const squashed = resizeRect(START, 'r', -1000, 0)
    expect(squashed.width).toBe(MIN_DIALOG.width)
    expect(squashed.left).toBe(100)
  })

  it('keeps the far edge still when the minimum bites from the left', () => {
    // The right edge is at 860. Squashing from the left must leave it there:
    // the box stops shrinking, it does not start walking right.
    const squashed = resizeRect(START, 'l', 1000, 0)
    expect(squashed.width).toBe(MIN_DIALOG.width)
    expect(squashed.left + squashed.width).toBe(START.left + START.width)
  })

  it('has the legacy’s eight directions', () => {
    expect([...RESIZE_DIRECTIONS]).toEqual(['r', 'b', 'l', 't', 'br', 'bl', 'tr', 'tl'])
  })
})

describe('DialogResizeHandles', () => {
  let host: HTMLDivElement
  let root: Root

  function Probe() {
    const ref = useRef<HTMLDivElement>(null)
    return (
      <div className="modal" ref={ref} style={{ width: 760, height: 560 }}>
        <DialogResizeHandles targetRef={ref} />
      </div>
    )
  }

  const dialog = () => host.querySelector<HTMLElement>('.modal')!
  const handle = (dir: string) => host.querySelector<HTMLElement>(`.fb-resize-${dir}`)!

  function drag(dir: string, dx: number, dy: number) {
    const el = handle(dir)
    flushSync(() =>
      el.dispatchEvent(
        new PointerEvent('pointerdown', { bubbles: true, clientX: 0, clientY: 0 }),
      ),
    )
    flushSync(() =>
      window.dispatchEvent(
        new PointerEvent('pointermove', { bubbles: true, clientX: dx, clientY: dy }),
      ),
    )
    flushSync(() => window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })))
  }

  beforeEach(() => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    flushSync(() => root.render(<Probe />))
    // jsdom lays nothing out, so the pin step would read zeros.
    dialog().getBoundingClientRect = () =>
      ({ left: 100, top: 50, width: 760, height: 560, right: 860, bottom: 610 }) as DOMRect
  })

  afterEach(() => {
    flushSync(() => root.unmount())
    host.remove()
  })

  it('renders all eight handles, by the legacy’s class names', () => {
    for (const dir of RESIZE_DIRECTIONS) {
      expect(handle(dir), dir).not.toBeNull()
      expect(handle(dir).className).toContain('fb-resize')
    }
  })

  it('writes the new size onto the dialog', () => {
    drag('r', 120, 0)
    expect(dialog().style.width).toBe('880px')
  })

  it('pins the box before resizing it, so the far edge does not drift', () => {
    // Centred by flexbox, growing the width would move BOTH edges. The drag
    // fixes the rect it started from first.
    drag('l', -100, 0)
    expect(dialog().style.position).toBe('fixed')
    expect(dialog().style.left).toBe('0px')
    expect(dialog().style.width).toBe('860px')
  })

  it('stops following the pointer after release', () => {
    drag('r', 120, 0)
    window.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 400 }))
    expect(dialog().style.width).toBe('880px')
  })
})

describe('the folder browser itself', () => {
  let host: HTMLDivElement
  let root: Root

  beforeEach(() => {
    // The listing is one `fetch`; the dialog renders its chrome either way.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ json: async () => ({ current: '/case', dirs: [], files: [] }) })),
    )
    host = document.createElement('div')
    // The panel this dialog is opened FROM carries a backdrop-filter, which is
    // what makes it the containing block for `position: fixed` — measured in
    // the browser: the backdrop came out 339x920 at x=1581, i.e. the width of
    // the right panel, with the 760px dialog squeezed to 420.
    host.style.backdropFilter = 'blur(6px)'
    document.body.appendChild(host)
    root = createRoot(host)
    flushSync(() =>
      root.render(
        <FileBrowser
          title="pick"
          confirmLabel="ok"
          onPick={() => {}}
          onCancel={() => {}}
          pickDirectory
        />,
      ),
    )
  })

  afterEach(() => {
    flushSync(() => root.unmount())
    host.remove()
    vi.unstubAllGlobals()
  })

  it('renders through a portal, not inside the panel that opened it', () => {
    const backdrop = document.querySelector('.modal-backdrop')
    expect(backdrop).not.toBeNull()
    expect(backdrop!.parentElement).toBe(document.body)
    expect(host.contains(backdrop!)).toBe(false)
  })

  it('carries the eight handles', () => {
    expect(document.querySelectorAll('.modal.file-browser .fb-resize')).toHaveLength(8)
  })
})
