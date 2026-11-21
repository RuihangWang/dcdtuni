/**
 * A rack's outer size and its U-slot layout.
 *
 * These seven numbers — W, D, the height mode, the unit count, the unit height,
 * the slot width ratio and the two safe margins — are not independent. The unit
 * count times the unit height plus the margins IS the rack's total height, so
 * setting one of them changes another, and every server, door, partition and
 * blanking panel is positioned from the result.
 *
 * `syncRackModelState` (js/rack_state.js:391) is what resolves that system: it
 * clamps each field, picks which of height/unit-height is derived from the
 * height mode, re-lays the servers against the new slot count and rebuilds the
 * partition table. So every write here goes through it — which is exactly what
 * the generic `setGeometryComponent` size boxes do NOT do. Resizing a rack
 * through those leaves the slots, servers and partitions describing the old
 * geometry, and the model then silently disagrees with itself.
 *
 * Ported from `updateRackModel` / `updateRackSetting` (STL_generate.html:14028,
 * 14063) and their `_rackSettingHome` field map (:14054).
 */

import type { Construction } from '../schema/cfdproj'
import { rackState } from '../legacy/cfdprojModules'
import * as store from '../projectStore'

/** The three size boxes, in the legacy's own W/H/D vocabulary. */
export type RackSizeKey = 'W' | 'H' | 'D'

export type RackHeightMode = 'totalHeight' | 'unitHeight'

export type RackSettingKey =
  | 'unitCount'
  | 'unitHeight'
  | 'slotWidthRatio'
  | 'safeMarginTop'
  | 'safeMarginBottom'
  | 'defaultSpanUnits'

const SIZE_AXIS: Record<RackSizeKey, 'x' | 'y' | 'z'> = { W: 'x', H: 'y', D: 'z' }

/**
 * Where a rack setting lives in the V4 document.
 *
 * Three of them moved under `geometry` in V4 while the rest stayed top-level,
 * and writing one to the wrong home is silent: `syncRackModelState` reads the
 * V4 field, so a value parked on the model root is simply never seen.
 * `_rackSettingHome` (STL_generate.html:14054) is the same map.
 */
export function rackSettingPath(key: RackSettingKey): string[] {
  switch (key) {
    case 'unitCount':
      return ['geometry', 'slot']
    case 'unitHeight':
      return ['geometry', 'slotHeight']
    case 'slotWidthRatio':
      return ['geometry', 'slotWidthRatio']
    default:
      return [key]
  }
}

type Bag = Record<string, unknown>

function readPath(model: Construction, path: string[]): unknown {
  let node: unknown = model
  for (const key of path) {
    if (!node || typeof node !== 'object') return undefined
    node = (node as Bag)[key]
  }
  return node
}

function writePath(model: Construction, path: string[], value: unknown) {
  let node = model as unknown as Bag
  for (const key of path.slice(0, -1)) {
    const next = node[key]
    node = (next && typeof next === 'object' ? next : (node[key] = {})) as Bag
  }
  node[path[path.length - 1] as string] = value
}

/** The current value of a rack setting, or undefined when it is unset. */
export function readRackSetting(model: Construction, key: RackSettingKey): number | undefined {
  const raw = readPath(model, rackSettingPath(key))
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : undefined
}

export function rackHeightMode(model: Construction): RackHeightMode {
  return (model as unknown as { heightMode?: string }).heightMode === 'unitHeight'
    ? 'unitHeight'
    : 'totalHeight'
}

/** The rack's outer size along one of the W/H/D axes, in metres. */
export function readRackSize(model: Construction, key: RackSizeKey): number {
  const size = (model.geometry?.size ?? {}) as Record<string, number>
  return Number(size[SIZE_AXIS[key]] ?? 0)
}

function editRack(uid: string, action: string, detail: string, mutate: (rack: Construction) => void) {
  store.edit(action, detail, (draft) => {
    const rack = store.findConstruction(draft, uid)?.model
    if (!rack || rack.kind !== 'rack') return
    mutate(rack)
    rackState().syncRackModelState(rack)
  })
}

/**
 * Set the rack's width, depth or total height, in metres.
 *
 * H is only a free variable in `totalHeight` mode — in `unitHeight` mode
 * `syncRackModelState` recomputes it from the slots, so a written H is
 * overwritten in the same call. The panel hides the box there rather than
 * offering one that does nothing.
 */
export function updateRackSize(uid: string, key: RackSizeKey, metres: number): boolean {
  if (!Number.isFinite(metres)) return false
  // The same bounds refusal the generic size boxes get. A rack is the model
  // most likely to be typed past a `yMin` of 0 — its H box is right beside the
  // U count — and a rack below the floor meshes geometry outside the domain.
  //
  // The RACK SETTINGS (U count, width ratio, margins) are deliberately not
  // guarded: they change the size only through `syncRackModelState`, which owns
  // the limits, and predicting its output here would be a second answer to what
  // a legal rack is. The legacy guards them (`updateRackSetting`,
  // STL_generate.html:14077) by reverting afterwards, which this store cannot
  // do without leaving an undo entry that undoes nothing.
  if (!store.canSetGeometryComponent(uid, 'size', SIZE_AXIS[key], metres)) return false
  editRack(uid, 'updateRackModel', `${uid} ${key}=${metres}`, (rack) => {
    const geometry = (rack.geometry ?? (rack.geometry = {})) as { size?: Record<string, number> }
    const size = (geometry.size ?? (geometry.size = {})) as Record<string, number>
    size[SIZE_AXIS[key]] = metres
  })
  return true
}

/**
 * Set one U-slot layout field.
 *
 * No clamping happens here on purpose: `syncRackModelState` owns the limits (3
 * U minimum, the 0.2–0.95 width ratio, margins capped at half the slot area),
 * and a second set of bounds in the UI would be a second answer to what a legal
 * rack is.
 */
export function updateRackSetting(uid: string, key: RackSettingKey, value: number) {
  if (!Number.isFinite(value)) return
  editRack(uid, 'updateRackSetting', `${uid} ${key}=${value}`, (rack) => {
    writePath(rack, rackSettingPath(key), value)
  })
}

/** Switch between "total height + U count" and "unit height + U count". */
export function updateRackHeightMode(uid: string, mode: RackHeightMode) {
  editRack(uid, 'updateRackSetting', `${uid} heightMode=${mode}`, (rack) => {
    ;(rack as unknown as Bag).heightMode = mode
  })
}
