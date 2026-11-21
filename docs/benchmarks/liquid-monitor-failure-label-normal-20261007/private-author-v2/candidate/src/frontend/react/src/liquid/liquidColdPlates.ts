/**
 * Cold plate / compute unit (CP-CU) pairs on a liquid server.
 *
 * A pair is one chip and the plate that cools it. `js/liquid_graphgen.js:479`
 * turns each pair into a `cold_plate` EDGE of the .liquidrun network — the only
 * place heat enters the loop — so a liquid project whose servers carry no pairs
 * compiles into a network with nothing to cool, solves happily, and reports
 * coolant that never warms up. That is why this is authoring, not decoration.
 *
 * The field names here are the contract: the graph compiler reads `Q_W`,
 * `R_th`, `dp_rated_kPa`, `Q_rated_LPM`, `C_solid` and `V_fluid_mL` off the
 * stored pair by those exact names, and the legacy editor writes the same ones.
 * Renaming any of them would leave the compiler on its own defaults with no
 * error anywhere.
 *
 * Ported from `renderEquipmentLiquidInspector` and its four window actions
 * (STL_generate.html:10170-10293). Pairs live at
 * `constructions.racks[rack].constructions.servers[srv].liquidCooling.cpCuPairs`
 * — the V4 home, which `_rackServerView` (js/rack_state.js:357) passes straight
 * through, so both apps read and write the same bytes.
 */

import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { transientModel } from '../transient/model'
import { backfillServerPairUids, newPairUid } from './cpCuPairIdentity'

export type CpPresetKey = 'cpu' | 'gpu' | 'custom'

/** Dropdown order, matching the legacy's `['cpu', 'gpu', 'custom']`. */
export const CP_PRESET_KEYS: CpPresetKey[] = ['cpu', 'gpu', 'custom']

export type CpNumericField =
  | 'Q_W'
  | 'R_th'
  | 'dp_rated_kPa'
  | 'Q_rated_LPM'
  | 'C_solid'
  | 'V_fluid_mL'

export type CpNumbers = Record<CpNumericField, number>

export interface CpCuPair extends CpNumbers {
  name: string
  preset: CpPresetKey
}

/** A pair as STORED: the six numbers plus whatever else the file already had. */
export type RawPair = Record<string, unknown>

export const COLD_PLATE_PRESETS: Record<'cpu' | 'gpu', CpNumbers> = {
  cpu: { Q_W: 350, R_th: 0.03, dp_rated_kPa: 10, Q_rated_LPM: 1.0, C_solid: 100, V_fluid_mL: 5 },
  gpu: { Q_W: 700, R_th: 0.01, dp_rated_kPa: 20, Q_rated_LPM: 2.0, C_solid: 150, V_fluid_mL: 10 },
}

/**
 * What a missing field reads as. Identical to the CPU preset, because that is
 * what the legacy fills in (STL_generate.html:10186-10191) — kept as its own
 * constant so changing the CPU preset does not silently re-interpret every
 * older project that omitted a field.
 */
export const CP_DEFAULTS: CpNumbers = {
  Q_W: 350,
  R_th: 0.03,
  dp_rated_kPa: 10,
  Q_rated_LPM: 1.0,
  C_solid: 100,
  V_fluid_mL: 5,
}

export const CP_NUMERIC_FIELDS: CpNumericField[] = [
  'Q_W',
  'R_th',
  'dp_rated_kPa',
  'Q_rated_LPM',
  'C_solid',
  'V_fluid_mL',
]

function num(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

export function defaultPairName(index: number): string {
  return `CP-CU_${index + 1}`
}

/**
 * The display view of a stored pair: defaults filled, `heatLoad` migrated.
 *
 * The legacy renderer does this by MUTATING the stored pair as it draws. React
 * cannot — writing to the project from a render would dirty a project the user
 * only looked at, and one undo checkpoint per repaint. Nothing is lost by
 * reading defaults instead: `liquid_graphgen.js:503-508` applies exactly the
 * same fallbacks when it compiles, so a pair that never had `R_th` written
 * solves as 0.03 either way.
 */
export function normalizePair(raw: RawPair | undefined, index: number): CpCuPair {
  const p = raw ?? {}
  const preset = p.preset
  return {
    name: typeof p.name === 'string' && p.name ? p.name : defaultPairName(index),
    preset: preset === 'cpu' || preset === 'gpu' ? preset : 'custom',
    // `heatLoad` is the pre-rename spelling of Q_W and still sits in saved
    // projects next to it. Q_W wins, as it does in the compiler.
    Q_W: num(p.Q_W, num(p.heatLoad, CP_DEFAULTS.Q_W)),
    R_th: num(p.R_th, CP_DEFAULTS.R_th),
    dp_rated_kPa: num(p.dp_rated_kPa, CP_DEFAULTS.dp_rated_kPa),
    Q_rated_LPM: num(p.Q_rated_LPM, CP_DEFAULTS.Q_rated_LPM),
    C_solid: num(p.C_solid, CP_DEFAULTS.C_solid),
    V_fluid_mL: num(p.V_fluid_mL, CP_DEFAULTS.V_fluid_mL),
  }
}

// ---------------------------------------------------------------------------
// Reducers — pure, over the RAW stored array
// ---------------------------------------------------------------------------

/**
 * The stored pairs of one server, or an empty array.
 *
 * `servers` is a dict keyed by uid in V4; the older array shape is not read
 * here because the migrator has already converted it by the time a project
 * reaches the store.
 */
export function readRawPairs(server: Record<string, unknown> | undefined): RawPair[] {
  const liquid = server?.liquidCooling as { cpCuPairs?: unknown } | undefined
  const pairs = liquid?.cpCuPairs
  return Array.isArray(pairs) ? (pairs as RawPair[]) : []
}

export function readPairs(server: Record<string, unknown> | undefined): CpCuPair[] {
  return readRawPairs(server).map((p, i) => normalizePair(p, i))
}

/**
 * A new pair, CPU-preset, named after its 1-based position (legacy :10236).
 *
 * The name is a label the user can change; the `uid` is what the compiled edge
 * is named after, so it must not collide with another pair's (see
 * `cpCuPairIdentity.ts`).
 */
export function addPair(pairs: RawPair[]): RawPair[] {
  return [
    ...pairs,
    { uid: newPairUid(pairs), name: defaultPairName(pairs.length), preset: 'cpu', ...COLD_PLATE_PRESETS.cpu },
  ]
}

export function removePair(pairs: RawPair[], index: number): RawPair[] {
  if (index < 0 || index >= pairs.length) return pairs
  return pairs.filter((_, i) => i !== index)
}

/**
 * Write one field.
 *
 * Editing a NUMBER re-types the pair as 'custom' (legacy :10265): a plate whose
 * heat load no longer matches the CPU preset must not keep claiming to be that
 * preset, or the next preset re-selection would look like a no-op and leave the
 * old value in place. `keepPreset` skips that for a keyframe: at a keyframe time
 * the heat load is a value over time, not a new plate, and the preset is static
 * (frozen off t = 0).
 */
export function updatePair(
  pairs: RawPair[],
  index: number,
  key: string,
  value: unknown,
  opts: { keepPreset?: boolean } = {},
): RawPair[] {
  const target = pairs[index]
  if (!target) return pairs
  const next = { ...target, [key]: value }
  if (key !== 'name' && key !== 'preset' && !opts.keepPreset) next.preset = 'custom'
  return pairs.map((p, i) => (i === index ? next : p))
}

/** The cold-plate fields the parameter registry tracks over time (Q_W, dT_K). */
function trackedPairFields(): Set<string> {
  return new Set(transientModel().registry().params
    .filter((p) => p.path.includes('cpCuPairs'))
    .map((p) => p.path.split('.').pop() as string))
}

/** Apply a preset. 'custom' re-types the pair and keeps its numbers (legacy :10280). */
export function applyPreset(pairs: RawPair[], index: number, preset: CpPresetKey): RawPair[] {
  const target = pairs[index]
  if (!target) return pairs
  const values = preset === 'custom' ? null : COLD_PLATE_PRESETS[preset]
  const next: RawPair = { ...target, preset, ...(values ?? {}) }
  return pairs.map((p, i) => (i === index ? next : p))
}

// ---------------------------------------------------------------------------
// Store actions
// ---------------------------------------------------------------------------

function serversOf(model: Construction): Record<string, Record<string, unknown>> | null {
  const cons = model.constructions as
    | { servers?: Record<string, Record<string, unknown>> }
    | undefined
  return cons?.servers ?? null
}

/**
 * Apply a reducer to one server's pairs, as one undo step.
 *
 * `store.edit` hands out a deep copy and checkpoints before the write, which is
 * the legacy `pushUndoCheckpoint(...)`-then-mutate order.
 */
function editPairs(
  rackUid: string,
  serverId: string,
  action: string,
  detail: string,
  reduce: (pairs: RawPair[]) => RawPair[],
) {
  store.edit(action, detail, (draft) => {
    const rack = store.findConstruction(draft, rackUid)?.model
    if (!rack) return
    const server = serversOf(rack)?.[serverId]
    if (!server) return
    // The first pair edit on a server writes each pair's uid (see
    // cpCuPairIdentity.ts), so a later rename cannot move its compiled edge.
    // Not off t = 0: a uid is a static field there, and would get the keyframe
    // it rides with refused; add, remove and rename are refused there anyway.
    if (!store.sceneFrozen()) backfillServerPairUids(server)
    const bag = (server.liquidCooling ?? (server.liquidCooling = {})) as { cpCuPairs?: RawPair[] }
    bag.cpCuPairs = reduce(Array.isArray(bag.cpCuPairs) ? bag.cpCuPairs : [])
  })
}

export function addCpCuPair(rackUid: string, serverId: string) {
  editPairs(rackUid, serverId, 'addCpCuPair', `${serverId}`, addPair)
}

export function removeCpCuPair(rackUid: string, serverId: string, index: number) {
  editPairs(rackUid, serverId, 'removeCpCuPair', `${serverId} #${index + 1}`, (pairs) =>
    removePair(pairs, index),
  )
}

export function updateCpCuPair(
  rackUid: string,
  serverId: string,
  index: number,
  key: string,
  value: unknown,
) {
  const keepPreset = store.sceneFrozen() && trackedPairFields().has(key)
  editPairs(rackUid, serverId, 'updateCpCuPair', `${serverId} #${index + 1} ${key}`, (pairs) =>
    updatePair(pairs, index, key, value, { keepPreset }),
  )
}

export type CpHeatMode = 'power' | 'temperature_rise'

/** A pair's heat form (.liquidrun v4): a prescribed power, or a prescribed
 * coolant temperature rise whose heat follows the flow. */
export function pairHeatMode(pair: RawPair | undefined): { mode: CpHeatMode; dT_K: number | null } {
  const rise = pair?.heatMode === 'temperature_rise'
  const dT = Number(pair?.dT_K)
  return { mode: rise ? 'temperature_rise' : 'power', dT_K: Number.isFinite(dT) ? dT : null }
}

/** Switch the heat form in one edit; a first switch to temperature rise gets 5 K. */
export function setCpCuHeatMode(rackUid: string, serverId: string, index: number, mode: CpHeatMode) {
  editPairs(rackUid, serverId, 'setCpCuHeatMode', `${serverId} #${index + 1} ${mode}`, (pairs) =>
    pairs.map((p, i) => {
      if (i !== index) return p
      const next: RawPair = { ...p, heatMode: mode }
      if (mode === 'temperature_rise' && !Number.isFinite(Number(p.dT_K))) next.dT_K = 5
      return next
    }),
  )
}

export function applyCpCuPreset(
  rackUid: string,
  serverId: string,
  index: number,
  preset: CpPresetKey,
) {
  editPairs(rackUid, serverId, 'applyCpCuPreset', `${serverId} #${index + 1} ${preset}`, (pairs) =>
    applyPreset(pairs, index, preset),
  )
}
