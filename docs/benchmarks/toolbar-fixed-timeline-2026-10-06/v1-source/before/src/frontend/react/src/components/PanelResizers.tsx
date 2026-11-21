import { useEffect, useRef } from 'react'
import { useTranslation } from '../useTranslation'
import {
  type ResizeEdge,
  clampResize,
  toolbarTargetHeight,
  MIN_SIZE,
} from '../ui/layoutMetrics'

/**
 * Draggable edges for the two side panels, the toolbar and the log.
 *
 * Three of the four are one CSS custom property on `:root` —
 * `--left-panel-width`, `--right-panel-width`, `--log-panel-height` — which the
 * layout already reads, so a drag is one property write and no React render.
 * Routing it through state would re-render the whole app on every pointermove.
 *
 * The toolbar is the odd one out. It wraps, so its height is decided by its
 * content and the width left between the panels, and no variable can set it:
 * the current fit keeps text at its natural size and scrolls overflow,
 * parking the handle on its measured bottom edge. This port used to write a
 * `--toolbar-height` variable that nothing read, which made the drag a no-op and
 * pinned the handle at a constant 120px — an invisible row-resize strip lying
 * across the middle of the canvas, eating clicks meant for the scene.
 *
 * Bounds are clamped against the WINDOW, not just against a minimum: dragging a
 * panel past the far edge otherwise leaves a layout with no viewport at all and
 * no handle left to drag back. The vertical arithmetic is in `layoutMetrics`.
 */

const PROPERTY: Partial<Record<ResizeEdge, string>> = {
  left: '--left-panel-width',
  right: '--right-panel-width',
  log: '--log-panel-height',
}

// The mounted fit's re-measure, for a row whose height changes from the
// inside (the transient timeline strip re-wrapping) — neither a new child nor
// a toolbar resize, so none of the observers below sees it.
let refit: (() => void) | null = null

/** Ask the toolbar fit to measure its content again. */
export function refitToolbar(): void {
  refit?.()
}

export function PanelResizers() {
  const { t, locale } = useTranslation()
  const dragging = useRef<ResizeEdge | null>(null)
  // The height the user last dragged the toolbar to, or null while it has never
  // been dragged. Without it the auto-fit pass below would undo the drag on the
  // next resize notification.
  const toolbarManual = useRef<number | null>(null)
  const applyToolbar = useRef<(target: number | null) => void>(() => {})
  const toolbarNatural = useRef(0)
  const refreshA11y = useRef<() => void>(() => {})

  const controlledId = (edge: ResizeEdge) => edge === 'toolbar' ? 'toolbar' : `${edge}-panel`
  const collapsed = (edge: ResizeEdge) => {
    if (edge === 'left' || edge === 'right') return document.body.classList.contains(`panel-${edge}-collapsed`)
    return edge === 'toolbar' && document.getElementById('toolbar')?.classList.contains('collapsed')
  }
  const bounds = (edge: ResizeEdge) => {
    if (edge === 'left' || edge === 'right') {
      // Custom properties containing max()/calc() are not resolved pixel values.
      // Match the CSS frame's actual floor and viewport gap instead of the
      // legacy arithmetic's smaller gap.
      const css = getComputedStyle(document.documentElement)
      const px = (name: string, fallback: number) => {
        const value = Number.parseFloat(css.getPropertyValue(name))
        return Number.isFinite(value) ? value : fallback
      }
      const maximum = Math.max(0, (window.innerWidth - px('--min-viewport-width', 260)) / 2)
      return { minimum: Math.min(px('--panel-floor', 220), maximum), maximum }
    }
    const maximum = edge === 'toolbar'
      ? Math.min(toolbarNatural.current || document.getElementById('toolbar')?.scrollHeight || MIN_SIZE.toolbar, Math.max(MIN_SIZE.toolbar, window.innerHeight * .5))
      : Math.max(MIN_SIZE.log, window.innerHeight * .5)
    return { minimum: Math.min(MIN_SIZE[edge], maximum), maximum }
  }
  const drawnSize = (edge: ResizeEdge) => {
    const rect = document.getElementById(controlledId(edge))?.getBoundingClientRect()
    return rect ? (edge === 'left' || edge === 'right' ? rect.width : rect.height) : 0
  }
  const applySize = (edge: ResizeEdge, value: number) => {
    if (collapsed(edge) || !document.getElementById(controlledId(edge)) || !Number.isFinite(value)) return
    const { minimum, maximum } = bounds(edge)
    const target = Math.min(Math.max(value, minimum), maximum)
    if (Math.abs(target - drawnSize(edge)) < .001) return
    if (edge === 'toolbar') {
      toolbarManual.current = target
      applyToolbar.current(target)
    } else {
      const root = document.documentElement
      if (edge === 'left' || edge === 'right') {
        // An explicit manual request may exceed the automatic 23vw share, but
        // still yields to the fit limit as the window gets smaller. Retain this
        // preference along with the existing width across collapse/remount.
        root.style.setProperty(`--${edge}-panel-share`, 'var(--panel-fit-max)')
        root.style.setProperty(`--${edge}-panel-manual-width`, `${Math.round(target * 1000) / 1000}px`)
      }
      root.style.setProperty(PROPERTY[edge] as string, `${Math.round(target * 1000) / 1000}px`)
    }
    refreshA11y.current()
  }
  const keyResize = (edge: ResizeEdge) => (e: React.KeyboardEvent) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return
    // A focused splitter owns arrows, including off-axis arrows. Do not let
    // them also orbit/move the viewport through document shortcuts.
    e.stopPropagation()
    if (e.altKey || e.ctrlKey || e.metaKey) return
    e.preventDefault()
    if (collapsed(edge)) return
    const horizontal = edge === 'left' || edge === 'right'
    if (horizontal && e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    if (!horizontal && e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
    const screenDirection = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1
    const sizeDirection = edge === 'right' || edge === 'log' ? -screenDirection : screenDirection
    applySize(edge, drawnSize(edge) + sizeDirection * (e.shiftKey ? 40 : 10))
  }

  useEffect(() => {
    const move = (e: PointerEvent) => {
      const edge = dragging.current
      if (!edge) return
      const value = edge === 'left' ? e.clientX
        : edge === 'right' ? window.innerWidth - e.clientX
          : clampResize(edge, e.clientX, e.clientY, window.innerWidth, window.innerHeight)
      applySize(edge, value)
      // The canvas is sized by its container, and a WebGL drawing buffer does
      // not follow CSS on its own — the viewport's ResizeObserver picks this up.
      e.preventDefault()
    }
    const up = () => {
      if (!dragging.current) return
      dragging.current = null
      document.body.classList.remove('resizing')
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
    return () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
  }, [])

  // Read the drawn frame without putting resize events through React state.
  useEffect(() => {
    const sync = () => {
      const root = document.documentElement
      for (const edge of ['left', 'right'] as const) {
        // App's existing collapse effect writes default widths for BOTH panels.
        // Restore an explicit manual preference only on expanded sides. Keep
        // the remembered value separate so collapse/remount cannot erase it.
        const remembered = root.style.getPropertyValue(`--${edge}-panel-manual-width`)
        if (!collapsed(edge) && root.style.getPropertyValue(`--${edge}-panel-share`)
          && Number.isFinite(Number.parseFloat(remembered))
          && root.style.getPropertyValue(`--${edge}-panel-width`) !== remembered) {
          root.style.setProperty(`--${edge}-panel-width`, remembered)
        }
      }
      for (const edge of ['left', 'right', 'toolbar', 'log'] as const) {
        const handle = document.getElementById(`${edge}-resizer`)
        if (!handle) continue
        const unavailable = !document.getElementById(controlledId(edge)) || !!collapsed(edge)
        const { minimum, maximum } = bounds(edge)
        const now = unavailable ? 0 : drawnSize(edge)
        handle.hidden = unavailable
        handle.tabIndex = unavailable ? -1 : 0
        handle.setAttribute('aria-valuemin', String(unavailable ? 0 : minimum))
        handle.setAttribute('aria-valuemax', String(unavailable ? 0 : maximum))
        handle.setAttribute('aria-valuenow', String(now))
        handle.setAttribute('aria-valuetext', t('layout.resize_pixels', { size: Math.round(now) }))
      }
    }
    refreshA11y.current = sync
    const observer = new ResizeObserver(sync)
    for (const edge of ['left', 'right', 'toolbar', 'log'] as const) {
      const panel = document.getElementById(controlledId(edge))
      if (panel) observer.observe(panel)
    }
    const classes = new MutationObserver(sync)
    classes.observe(document.body, { attributes: true, attributeFilter: ['class'] })
    const toolbar = document.getElementById('toolbar')
    if (toolbar) classes.observe(toolbar, { attributes: true, attributeFilter: ['class'] })
    window.addEventListener('resize', sync)
    sync()
    return () => {
      observer.disconnect(); classes.disconnect()
      window.removeEventListener('resize', sync)
      refreshA11y.current = () => {}
    }
  }, [])
  useEffect(() => refreshA11y.current(), [locale])

  // Toolbar fit: keep the handle on the toolbar's real bottom edge, and keep the
  // toolbar itself inside half the window. Its natural height grows without
  // bound as the window narrows — 129px at 1920 wide, 403px at 1000, 1245px in a
  // 640px window — and at that point it is the whole app.
  useEffect(() => {
    const toolbar = document.getElementById('toolbar')
    const handle = document.getElementById('toolbar-resizer')
    if (!toolbar || !handle) return

    let natural: number | null = null
    let lastWidth = toolbar.offsetWidth
    let applied: number | null = null

    // Measure without the pinned height so expanding a tool group is visible
    // to the fit even while overflow is scrolled.
    const measureNatural = () => {
      const savedTransform = toolbar.style.transform
      const savedHeight = toolbar.style.height
      toolbar.style.transform = 'none'
      toolbar.style.height = ''
      const css = getComputedStyle(toolbar)
      const border = (Number.parseFloat(css.borderTopWidth) || 0)
        + (Number.parseFloat(css.borderBottomWidth) || 0)
      // scrollHeight includes the full scrollable content and padding, but not
      // borders and it is integer-rounded. The unpinned rectangle preserves
      // fractional natural border-box height when the content already fits.
      const h = Math.max(toolbar.scrollHeight + border, toolbar.getBoundingClientRect().height)
      toolbar.style.transform = savedTransform
      toolbar.style.height = savedHeight
      return h
    }

    const placeHandle = () => {
      const rect = toolbar.getBoundingClientRect()
      handle.style.top = `${Math.round(rect.bottom)}px`
      // A collapsed toolbar has no edge worth dragging, and the legacy hides the
      // handle outright in that state (base.css:71).
      handle.hidden = toolbar.classList.contains('collapsed')
    }

    const fit = (manual: number | null) => {
      if (natural === null) natural = measureNatural()
      toolbarNatural.current = natural
      const target = toolbarTargetHeight(natural, manual, window.innerHeight)
      if (applied === null || Math.abs(applied - target) > 0.5) {
        applied = target
        // Keep labels readable. A constrained toolbar scrolls at its normal
        // text size instead of shrinking every control and pointer target.
        toolbar.style.transform = ''
        toolbar.style.height = natural > target ? `${target}px` : ''
        toolbar.dataset.scrollable = String(natural > target)
      }
      placeHandle()
      refreshA11y.current()
    }
    applyToolbar.current = (target) => fit(target)

    const remeasure = () => {
      natural = null
      applied = null
      fit(toolbarManual.current)
    }

    // A width change re-wraps the toolbar, so the cached natural height is stale
    // — and a drag on the LEFT panel changes the toolbar's width, not just the
    // window's, which is why this hangs off the element and not off `resize`.
    const observer = new ResizeObserver(() => {
      if (toolbar.offsetWidth !== lastWidth) {
        lastWidth = toolbar.offsetWidth
        natural = null
        applied = null
      }
      fit(toolbarManual.current)
    })
    observer.observe(toolbar)

    // Collapsing empties the toolbar, which changes its natural height without
    // changing its measured one — the squeezed toolbar is pinned to the height
    // it needed while full, so the ResizeObserver never fires and the collapsed
    // bar keeps the footprint of the expanded one.
    const classes = new MutationObserver(remeasure)
    classes.observe(toolbar, { attributes: true, attributeFilter: ['class'] })
    // Timeline rows and expanded tool groups can change their natural height
    // inside a pinned box. Observe group open attributes as well as children.
    refit = remeasure
    const rows = new MutationObserver(remeasure)
    const content = document.getElementById('toolbar-content')
    if (content) rows.observe(content, { childList: true, subtree: true, attributes: true, attributeFilter: ['open', 'hidden'] })

    window.addEventListener('resize', remeasure)
    fit(null)
    return () => {
      observer.disconnect()
      classes.disconnect()
      rows.disconnect()
      if (refit === remeasure) refit = null
      window.removeEventListener('resize', remeasure)
      applyToolbar.current = () => {}
    }
  }, [])

  const start = (edge: ResizeEdge) => (e: React.PointerEvent<HTMLDivElement>) => {
    if (collapsed(edge)) return
    e.preventDefault()
    e.currentTarget.focus()
    dragging.current = edge
    document.body.classList.add('resizing')
  }

  return (
    <>
      <span id="panel-resize-width-help" className="resize-help">{t('layout.resize_width_help')}</span>
      <span id="panel-resize-height-help" className="resize-help">{t('layout.resize_height_help')}</span>
      {(['left', 'right', 'toolbar', 'log'] as const).map((edge) => {
        const horizontal = edge === 'left' || edge === 'right'
        const label = t(`layout.resize_${edge}`)
        const help = t(horizontal ? 'layout.resize_width_help' : 'layout.resize_height_help')
        return (
          <div
            key={edge}
            id={`${edge}-resizer`}
            className={horizontal ? 'resize-handle' : 'resize-handle-h'}
            role="separator"
            tabIndex={0}
            aria-orientation={horizontal ? 'vertical' : 'horizontal'}
            aria-controls={controlledId(edge)}
            aria-label={label}
            aria-describedby={horizontal ? 'panel-resize-width-help' : 'panel-resize-height-help'}
            title={`${label} — ${help}`}
            onPointerDown={start(edge)}
            onKeyDown={keyResize(edge)}
          />
        )
      })}
    </>
  )
}
