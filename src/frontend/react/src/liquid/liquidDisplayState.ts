/**
 * How the liquid result is shown in the 3D view: what the network is coloured
 * by, on which scale, and how the flow arrows look.
 *
 * Session display state, never serialized (like the colouring checkboxes): it
 * outlives a tab switch and the results panel remounting, and every reader —
 * the network painter, the arrows, the legends, the Omniverse frame — takes
 * its scale from here, so the 3D view, the colour bars and Kit cannot disagree.
 *
 * Scales default to the whole run ("全时段", every sample): scrubbing the
 * timeline then shows the change over time instead of re-normalising each
 * frame. "按帧" uses the shown sample's range; a range typed in a colour bar's
 * dialog wins over both until the quantity or the scale mode changes. The
 * colour map chosen in that dialog outlives the range: each quantity (and the
 * arrows) keeps its map until the workspace resets.
 */

import type { ColorMap } from '../postprocess/ppColormaps'
import { liquidFrame, liquidGlobalDomains } from './liquidColourRules'
import {
  displayDomain, LIQUID_DEFAULT_COLORMAPS, LIQUID_QUANTITY_UNITS, liquidQuantityFrame, liquidQuantityGlobalDomain,
  type LiquidQuantity,
} from './liquidDisplay'
import { liquidArrowGlobalDomains, liquidFlowArrows } from './liquidFlow'

export type LiquidScaleMode = 'global' | 'frame'
export interface ManualScale { domain: [number, number]; colorMap: ColorMap }
export interface LiquidDisplayOptions {
  quantity: LiquidQuantity
  scale: LiquidScaleMode
  /** The network colour bar's dialog; cleared by a quantity or scale change. */
  manual: ManualScale | null
  /** The map last chosen for each quantity, kept when its range is cleared. */
  colorMaps: Partial<Record<LiquidQuantity, ColorMap>>
  arrows: boolean
  animate: boolean
  /** Arrows per pipe cell. */
  density: number
  /** Arrow speed = coolant velocity x this. */
  speedScale: number
  arrowColor: 'velocity' | 'plain'
  arrowManual: ManualScale | null
  arrowColorMap: ColorMap | null
  /** While an experiment runs, show its latest sample (liquidLiveFollow). */
  live: boolean
}

export const LIQUID_DISPLAY_DEFAULTS: LiquidDisplayOptions = {
  quantity: 'temperature', scale: 'global', manual: null, colorMaps: {},
  arrows: true, animate: true, density: 1, speedScale: 2, arrowColor: 'velocity', arrowManual: null, arrowColorMap: null,
  live: true,
}
export const ARROW_DENSITIES = [1, 2, 3, 4] as const
export const ARROW_SPEED_SCALES = [0.25, 0.5, 1, 2, 5, 10, 20] as const
/** The arrows' own map. */
export const ARROW_COLORMAP: ColorMap = 'viridis'
/** A plain arrow: light, readable over every network colour. */
export const PLAIN_ARROW_COLOR = 0xf2f2f2

let options: LiquidDisplayOptions = { ...LIQUID_DISPLAY_DEFAULTS }
const listeners = new Set<() => void>()
export function liquidDisplayOptions(): LiquidDisplayOptions { return options }
export function subscribeLiquidDisplay(fn: () => void): () => void { listeners.add(fn); return () => { listeners.delete(fn) } }
export function setLiquidDisplayOptions(patch: Partial<LiquidDisplayOptions>): void {
  const next = { ...options, ...patch }
  if ((patch.quantity !== undefined && patch.quantity !== options.quantity) || (patch.scale !== undefined && patch.scale !== options.scale)) {
    if (patch.manual === undefined) next.manual = null
    if (patch.arrowManual === undefined) next.arrowManual = null
  }
  if (patch.manual) next.colorMaps = { ...options.colorMaps, [next.quantity]: patch.manual.colorMap }
  if (patch.arrowManual) next.arrowColorMap = patch.arrowManual.colorMap
  next.density = Math.max(1, Math.min(4, Math.round(next.density)))
  next.speedScale = Number.isFinite(next.speedScale) && next.speedScale >= 0 ? next.speedScale : LIQUID_DISPLAY_DEFAULTS.speedScale
  options = next
  listeners.forEach(fn => fn())
}
/** Workspace reset. */
export function resetLiquidDisplayOptions(): void { options = { ...LIQUID_DISPLAY_DEFAULTS }; listeners.forEach(fn => fn()) }

type Bag = Record<string, unknown>
const memo = new WeakMap<object, Map<string, unknown>>()
/** Whole-run scales are computed once per result document. */
function remember<T>(raw: object, key: string, make: () => T): T {
  let own = memo.get(raw)
  if (!own) { own = new Map(); memo.set(raw, own) }
  if (!own.has(key)) own.set(key, make())
  return own.get(key) as T
}

export interface NetworkScale {
  quantity: LiquidQuantity
  unit: string
  domain: [number, number] | null
  colorMap: ColorMap
  source: LiquidScaleMode | 'manual'
}
export function networkScale(raw: Bag, sample: number, opts: LiquidDisplayOptions = options): NetworkScale {
  const q = opts.quantity
  const base = { quantity: q, unit: LIQUID_QUANTITY_UNITS[q] }
  if (opts.manual) return { ...base, domain: displayDomain(opts.manual.domain), colorMap: opts.manual.colorMap, source: 'manual' }
  const domain = opts.scale === 'global'
    ? remember(raw, `network|${q}`, () => liquidQuantityGlobalDomain(raw, q))
    : liquidQuantityFrame(raw, sample, q)?.domain ?? null
  return { ...base, domain: displayDomain(domain), colorMap: opts.colorMaps[q] ?? LIQUID_DEFAULT_COLORMAPS[q], source: opts.scale }
}

/** The chips always show chip temperature, on the same scale mode. */
export function chipScale(raw: Bag, sample: number, opts: LiquidDisplayOptions = options): [number, number] | null {
  const d = opts.scale === 'global'
    ? remember(raw, 'chips', () => liquidGlobalDomains(raw).chips ?? null)
    : liquidFrame(raw, sample)?.domains.chips ?? null
  return displayDomain(d)
}

export interface ArrowScales { mass: [number, number] | null; velocity: [number, number] | null; colorMap: ColorMap; source: LiquidScaleMode | 'manual' }
export function arrowScales(raw: Bag, sample: number, opts: LiquidDisplayOptions = options): ArrowScales {
  const d = opts.scale === 'global'
    ? remember(raw, 'arrows', () => liquidArrowGlobalDomains(raw))
    : liquidFlowArrows(raw, sample)?.domains ?? {}
  const velocity = opts.arrowManual ? opts.arrowManual.domain : d.velocity ?? null
  return {
    mass: displayDomain(d.mass ?? null), velocity: displayDomain(velocity),
    colorMap: opts.arrowManual?.colorMap ?? opts.arrowColorMap ?? ARROW_COLORMAP, source: opts.arrowManual ? 'manual' : opts.scale,
  }
}
