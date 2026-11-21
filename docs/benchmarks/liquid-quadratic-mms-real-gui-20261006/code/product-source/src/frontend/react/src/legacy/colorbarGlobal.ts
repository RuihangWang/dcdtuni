/**
 * `window._ppCreateColorbarWidget`, for the shared modules that ask for it.
 *
 * React ported the colorbar as a component (`ColorbarOverlay`) and never
 * published the legacy's imperative factory. The shared liquid renderer looks
 * for that factory by name — `colorPipesByTemperature`
 * (js/liquid_pipe_renderer.js:898) and the server chip overlay next to it — and
 * both are written as `if (typeof window._ppCreateColorbarWidget === 'function')`.
 * So in React both temperature overlays coloured the scene and showed NO scale:
 * a legend-less picture where a rack's colour means a number nobody can read.
 * Silently, because the guard makes a missing factory a no-op.
 *
 * This is the adapter. It owns a React root per widget on `document.body` —
 * the same shape as `liquidPostprocessHost`, and the same reason: the bar
 * outlives whatever opened it, and `position: fixed` inside a panel with a
 * `backdrop-filter` is clipped to that panel.
 *
 * ## Kelvin in, Kelvin out
 *
 * `ColorbarOverlay` takes RAW limits and converts for display itself, and the
 * legacy widget does the same (`_dispVal`). The liquid callers, however, hand
 * it CELSIUS — `colorPipesByTemperature` passes `tMin`/`tMax` already converted
 * — so the label they choose says what the numbers are and this passes them
 * through untouched. `field` is what decides conversion, and the callers pass
 * `fieldName: 'T'`; a caller that has already converted must therefore NOT say
 * 'T', which is why `field` is taken from the config rather than assumed.
 */

import { createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ColorbarOverlay } from '../components/ColorbarOverlay'
import type { ColorMap } from '../postprocess/ppColormaps'

export interface ColorbarWidgetConfig {
  label?: string
  fieldName?: string
  climMin?: number
  climMax?: number
  cmapPreset?: string
  rightOffset?: number
  onAutoScale?: () => { min: number; max: number } | null
  onApply?: (min: number, max: number, colorMap: string) => void
}

/** The legacy widget's surface, as its callers use it. */
export interface ColorbarWidget {
  update(config: ColorbarWidgetConfig): void
  show(): void
  hide(): void
  destroy(): void
}

const KNOWN_MAPS = new Set<ColorMap>(['coolwarm', 'jet', 'viridis'])

function asColorMap(value: string | undefined): ColorMap {
  return value && KNOWN_MAPS.has(value as ColorMap) ? (value as ColorMap) : 'coolwarm'
}

let hostSeq = 0

export function createColorbarWidget(initial: ColorbarWidgetConfig): ColorbarWidget {
  const config: ColorbarWidgetConfig = { ...initial }
  let visible = true
  let host: HTMLDivElement | null = null
  let root: Root | null = null

  const draw = () => {
    if (!root) return
    if (!visible) {
      root.render(null)
      return
    }
    root.render(
      createElement(ColorbarOverlay, {
        // A stable id per widget, so two bars side by side can be told apart by
        // a test and by the harness.
        id: `lc-colorbar-${hostSeq}`,
        label: config.label ?? 'T',
        field: config.fieldName ?? 'T',
        colorMap: asColorMap(config.cmapPreset),
        climMin: Number(config.climMin ?? 0),
        climMax: Number(config.climMax ?? 1),
        rightOffset: config.rightOffset,
        onAutoScale: config.onAutoScale,
        onApply: (min: number, max: number, colorMap: ColorMap) => {
          config.climMin = min
          config.climMax = max
          config.cmapPreset = colorMap
          config.onApply?.(min, max, colorMap)
          draw()
        },
      }),
    )
  }

  if (typeof document !== 'undefined' && document.body) {
    hostSeq += 1
    host = document.createElement('div')
    host.id = `lc-colorbar-host-${hostSeq}`
    document.body.appendChild(host)
    root = createRoot(host)
    draw()
  }

  return {
    update(next: ColorbarWidgetConfig) {
      Object.assign(config, next)
      visible = true
      draw()
    },
    show() {
      visible = true
      draw()
    },
    hide() {
      visible = false
      draw()
    },
    destroy() {
      // Unmounted on a microtask: `destroy()` is called from inside a React
      // render in at least one caller path, and unmounting a root
      // synchronously from there throws "Attempted to synchronously unmount a
      // root while React was already rendering".
      const dying = root
      const element = host
      root = null
      host = null
      queueMicrotask(() => {
        dying?.unmount()
        element?.remove()
      })
    },
  }
}

/**
 * Publish the factory.
 *
 * Called from `installLiquidScene`, BEFORE the shared modules are imported, so
 * nothing they run at load time sees a half-installed window.
 */
export function installColorbarGlobal(): void {
  if (typeof window === 'undefined') return
  const w = window as unknown as { _ppCreateColorbarWidget?: unknown }
  // Never clobber a real one: on the legacy page this name is the page's own.
  if (typeof w._ppCreateColorbarWidget === 'function') return
  w._ppCreateColorbarWidget = createColorbarWidget
}
