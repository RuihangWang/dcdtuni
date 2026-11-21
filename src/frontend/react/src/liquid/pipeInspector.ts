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
    // The liquid compiler's 25 mm for a pipe that states no bore, so the box
    // shows what the solve will use; `pipeDiameterStated` lets the panel say
    // the value is a default. A new free pipe states its own 100 mm.
    diameterM: Number(geometry.diameterM) || SOLVER_DEFAULT_PIPE_DIAMETER_M,
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
export function setPipeDiameter(uid: string, metres: number): boolean {
  if (!Number.isFinite(metres) || metres <= 0) return false
  const project = store.getProject()
  const hit = project && store.findConstruction(project, uid)
  if (!hit || hit.model.kind !== 'pipe_segment') return false
  return store.edit('updatePipeDiameter', `${uid} -> ${metres} m`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model) return
    const geometry = (model.geometry ?? (model.geometry = {})) as Bag
    geometry.diameterM = metres
  })
}

/**
 * Re-route a pipe segment: its WORLD waypoints, and the world positions of the
 * valves named in `valvePositions` (by valve id).
 *
 * The liquid compiler reads the route's length and heights from the waypoints
 * (`waypoints_m`, `n_cells`) and finds each tap's place along the main from its
 * valve's position. What the pipe joins — its ports, the rack each valve taps,
 * the bore and the openings — is left as it is, so a re-route changes the
 * graph's geometry and never its topology. One undo step. Refused, with nothing
 * written, for a uid that is not a pipe segment, fewer than two points, a
 * non-finite coordinate, or a valve id the pipe does not have.
 */
export function setPipeSegmentPoints(
  uid: string,
  waypoints: readonly Vec3[],
  valvePositions: Readonly<Record<string, Vec3>> = {},
): boolean {
  const point = (v: unknown): v is Vec3 =>
    !!v && typeof v === 'object' && ['x', 'y', 'z'].every((k) => Number.isFinite((v as Bag)[k]))
  if (!Array.isArray(waypoints) || waypoints.length < 2 || !waypoints.every(point)) return false
  if (!Object.values(valvePositions).every(point)) return false
  const project = store.getProject()
  const hit = project && store.findConstruction(project, uid)
  if (!hit || hit.model.kind !== 'pipe_segment') return false
  const valves = (((hit.model as unknown as Bag).cooling as Bag | undefined)?.valves ?? []) as Bag[]
  const ids = new Set(valves.map((v) => v?.id))
  if (!Object.keys(valvePositions).every((id) => ids.has(id))) return false
  return store.edit('setPipeSegmentPoints', `${uid} ${waypoints.length} points`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model as unknown as Bag | undefined
    if (!model) return
    const geometry = (model.geometry ?? (model.geometry = {})) as Bag
    geometry.waypoints = waypoints.map(({ x, y, z }) => ({ x, y, z }))
    for (const valve of ((model.cooling as Bag | undefined)?.valves ?? []) as Bag[]) {
      const at = valvePositions[valve.id as string]
      if (at) valve.position = { x: at.x, y: at.y, z: at.z }
    }
  })
}

/** The liquid compiler's bore for a pipe that states none (liquid_graphgen.js `D_m`). */
export const SOLVER_DEFAULT_PIPE_DIAMETER_M = 0.025

/** Whether the pipe states its own bore; the inspector says so when it does not. */
export function pipeDiameterStated(model: Construction): boolean {
  const d = Number(((model.geometry ?? {}) as Bag).diameterM)
  return Number.isFinite(d) && d > 0
}

export interface ValveSummary {
  id: string
  type: string
  name: string
  /** Opening 0..1 the solver uses: absent means fully open. */
  opening: number
  /** flow_control and isolation valves have an opening; a tee does not. */
  adjustable: boolean
  connectedRackId: string
}

export function valveSummary(model: Construction, valveId: string): ValveSummary | null {
  const valves = ((model as unknown as Bag).cooling as Bag | undefined)?.valves
  if (!Array.isArray(valves)) return null
  const valve = valves.find((v: Bag) => v && v.id === valveId) as Bag | undefined
  if (!valve) return null
  const type = String(valve.type ?? '')
  const opening = Number(valve.opening ?? 1)
  return {
    id: valveId,
    type,
    name: String(valve.name ?? valveId),
    opening: Number.isFinite(opening) ? opening : 1,
    adjustable: type === 'flow_control' || type === 'isolation',
    connectedRackId: typeof valve.connectedRackId === 'string' ? valve.connectedRackId : '',
  }
}

/**
 * Set a valve's opening (registry `valve.opening`: 0.001..1).
 *
 * Refused, not clamped, outside that range: a closed valve (0) would isolate
 * its branch, which the stagnation check then has to explain, and the registry
 * floor is what the timeline also enforces. Only flow-control and isolation
 * valves have an opening.
 */
export function setValveOpening(pipeUid: string, valveId: string, opening: number): boolean {
  if (!Number.isFinite(opening) || opening < 0.001 || opening > 1) return false
  const project = store.getProject()
  const hit = project && store.findConstruction(project, pipeUid)
  if (!hit || hit.model.kind !== 'pipe_segment' || !valveSummary(hit.model, valveId)?.adjustable) return false
  return store.edit('setValveOpening', `${pipeUid}/${valveId} opening=${opening}`, (draft) => {
    const valves = ((store.findConstruction(draft, pipeUid)?.model as unknown as Bag)?.cooling as Bag | undefined)?.valves
    const valve = Array.isArray(valves) ? valves.find((v: Bag) => v && v.id === valveId) : null
    if (valve) valve.opening = opening
  })
}
