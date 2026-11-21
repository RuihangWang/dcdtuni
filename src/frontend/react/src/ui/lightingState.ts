/**
 * The viewport's lighting: one state for every place that sets or shows it.
 *
 * The top bar's lighting group, the post-processing panel's light slider, the
 * Three.js viewport and the Omniverse display all read and write THIS state,
 * so a light changed in one is the light everywhere: geometry, mesh preview and
 * result layers in Three.js, and the dome/key/fill rig in Kit (`view.lighting`
 * of the render scene). The on/off switch stays the per-project `view.lights`
 * the toolbar always had; scale, preset and shadows are a user preference and
 * persist across projects.
 *
 * Presets come from `lighting.presets` in shared/textures/materials.json, which
 * Kit reads too.
 */

import { materialLibrary } from '../legacy/materialManifest'

export interface LightingState {
  /** Multiplies every light and the environment; 1 is the manifest's rig. */
  scale: number
  /** A key of `lighting.presets`. */
  preset: string
  /** A world-fixed shadow-casting light (Three) and RTX shadows (Kit). */
  shadows: boolean
}

export const LIGHT_SCALE_MIN = 0
export const LIGHT_SCALE_MAX = 2.5
const STORAGE_KEY = 'dcdtuni.lighting.v1'

type Listener = () => void
const listeners = new Set<Listener>()

/** The manifest's preset ids, 'standard' first. */
export function lightingPresets(): string[] {
  const presets = materialLibrary.lighting().presets ?? { standard: {} }
  return Object.keys(presets).sort((a, b) => (a === 'standard' ? -1 : b === 'standard' ? 1 : 0))
}

export const DEFAULT_LIGHTING: LightingState = { scale: 1, preset: 'standard', shadows: false }

function clampScale(value: number): number {
  return Number.isFinite(value) ? Math.min(LIGHT_SCALE_MAX, Math.max(LIGHT_SCALE_MIN, value)) : 1
}

function normalize(raw: Partial<LightingState> | null | undefined): LightingState {
  const presets = lightingPresets()
  return {
    scale: clampScale(typeof raw?.scale === 'number' ? raw.scale : DEFAULT_LIGHTING.scale),
    preset: typeof raw?.preset === 'string' && presets.includes(raw.preset) ? raw.preset : DEFAULT_LIGHTING.preset,
    shadows: typeof raw?.shadows === 'boolean' ? raw.shadows : DEFAULT_LIGHTING.shadows,
  }
}

function load(): LightingState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return normalize(raw ? (JSON.parse(raw) as Partial<LightingState>) : null)
  } catch {
    return { ...DEFAULT_LIGHTING }
  }
}

let state: LightingState = load()

export function getLighting(): LightingState {
  return state
}

export function setLighting(patch: Partial<LightingState>): void {
  const next = normalize({ ...state, ...patch })
  if (next.scale === state.scale && next.preset === state.preset && next.shadows === state.shadows) return
  state = next
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    /* private mode or a full quota: the setting just does not persist */
  }
  listeners.forEach((fn) => fn())
}

export function subscribeLighting(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** Tests: back to the defaults without touching storage. */
export function resetLightingForTests(next: Partial<LightingState> = {}): void {
  state = normalize({ ...DEFAULT_LIGHTING, ...next })
  listeners.forEach((fn) => fn())
}
