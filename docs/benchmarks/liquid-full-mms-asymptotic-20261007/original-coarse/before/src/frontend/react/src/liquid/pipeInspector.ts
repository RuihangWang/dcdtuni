/**
 * What the pipe-segment inspector shows, and the one edit it makes.
 *
 * A pipe is the one kind whose geometry is NOT a box. Its shape is a waypoint
 * list and its only scalar is a bore, so the generic model panel's W/D/H boxes
 * describe nothing about it — they write `geometry.size`, which for a pipe is
 * `{0,0,0}` and stays that way however the pipe is drawn. Reading the panel
 * therefore told the user nothing and using it changed nothing.
 *
 * The readout is deliberately the FIRST and LAST waypoints rather than a bounding
 * box: a pipe's two ends are what get connected, and the legacy panel
 * (`renderPipeSegmentInspector`, STL_generate.html:8546) shows exactly those six
 * numbers.
 */

import type { Construction, Vec3 } from '../schema/cfdproj'
import type { TranslationKey } from '../i18n'
import * as store from '../projectStore'

type Bag = Record<string, unknown>

export type LoopRole = 'supply' | 'return' | 'unconnected'

export interface PipeSummary {
  /** Bore in METRES, the unit `geometry.diameterM` is stored in. */
  diameterM: number
  loopRole: LoopRole
  loopRoleKey: TranslationKey
  /** The legacy pipe colours: supply blue, return red, unconnected brown. */
  loopRoleColor: string
  waypointCount: number
  start: Vec3
  end: Vec3
  startPortId: string
  endPortId: string
  blindStart: boolean
  blindEnd: boolean
}

const ORIGIN: Vec3 = { x: 0, y: 0, z: 0 }

const ROLE_KEY: Record<LoopRole, TranslationKey> = {
  supply: 'pipe.color_supply',
  return: 'pipe.color_return',
  unconnected: 'pipe.color_unconnected',
}

/**
 * The pipe renderer's own three colours (js/liquid_pipe_renderer.js:17-19), so
 * the swatch beside the role name is the colour the pipe is drawn in.
 */
const ROLE_COLOR: Record<LoopRole, string> = {
  supply: '#2196F3',
  return: '#F44336',
  unconnected: '#8B6914',
}

function vec(raw: unknown, fallback: Vec3): Vec3 {
  if (!raw || typeof raw !== 'object') return fallback
  const v = raw as Partial<Vec3>
  return {
    x: Number(v.x ?? 0),
    y: Number(v.y ?? 0),
    z: Number(v.z ?? 0),
  }
}

export function pipeSummary(model: Construction): PipeSummary {
  const geometry = (model.geometry ?? {}) as Bag
  const cooling = ((model as unknown as Bag).cooling ?? {}) as Bag
  const connection = (cooling.connection ?? {}) as Bag
  const blindEnds = (connection.blindEnds ?? {}) as Bag
  const waypoints = Array.isArray(geometry.waypoints) ? geometry.waypoints : []
  // The legacy falls back to the START when there is no last point, so a
  // one-point pipe reads as a zero-length run rather than as one end at the
  // origin.
  const start = waypoints.length ? vec(waypoints[0], ORIGIN) : ORIGIN
  const end = waypoints.length ? vec(waypoints[waypoints.length - 1], start) : start
  const raw = cooling.loopRole
  const loopRole: LoopRole = raw === 'supply' || raw === 'return' ? raw : 'unconnected'

  return {
    // 25 mm, matching the legacy default — NOT the 100 mm a free pipe is
    // created at, which is a value that model already carries.
    diameterM: Number(geometry.diameterM) || 0.025,
    loopRole,
    loopRoleKey: ROLE_KEY[loopRole],
    loopRoleColor: ROLE_COLOR[loopRole],
    waypointCount: waypoints.length,
    start,
    end,
    startPortId: typeof connection.startPortId === 'string' ? connection.startPortId : '',
    endPortId: typeof connection.endPortId === 'string' ? connection.endPortId : '',
    blindStart: blindEnds.start === true,
    blindEnd: blindEnds.end === true,
  }
}

/**
 * Set a pipe's bore, in METRES.
 *
 * The legacy `window.updatePipeDiameter` (STL_generate.html:8590) takes the
 * number out of the box in the DISPLAY unit and converts; the conversion is the
 * caller's here so this stays a pure store write. Zero and negative are refused
 * rather than clamped: `geometry.diameterM` divides into the hydraulic
 * resistance the liquid solver builds, and a zero bore is an infinite one.
 */
export function setPipeDiameter(uid: string, metres: number) {
  if (!Number.isFinite(metres) || metres <= 0) return
  store.edit('updatePipeDiameter', `${uid} -> ${metres} m`, (draft) => {
    const hit = store.findConstruction(draft, uid)
    if (!hit || hit.model.kind !== 'pipe_segment') return
    const geometry = (hit.model.geometry ?? (hit.model.geometry = {})) as Bag
    geometry.diameterM = metres
  })
}
