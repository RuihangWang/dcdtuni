/**
 * Editing a refinement zone by NUMBER.
 *
 * A zone is a `searchableBox` in snappyHexMeshDict, so its bounds are metres a
 * user has a reason to type: a 1.20 m plenum, the exact footprint of an aisle.
 * Dragging its faces in the viewport is the only other way to set them, and no
 * amount of dragging lands on 1.20 exactly.
 *
 * Ported from `setRefinementZoneBound` / `renameRefinementZone` /
 * `toggleRefinementZoneVisibility` (STL_generate.html:27259-27298).
 */

import { getMeshVisuals } from './meshViewState'
import { edit, getProject, mutateLive, notifyLiveEdit } from '../projectStore'

/**
 * A zone as it sits in `meshConfig.refinementZones`.
 *
 * Everything optional, because that is what a .cfdproj can actually contain: a
 * zone written by an older editor has no `visible`, and a hand-edited one can be
 * missing a bound. Declaring them required here would only move the crash from
 * this module into the panel.
 */
export interface ZoneBox {
  id?: string
  name?: string
  level?: number
  visible?: boolean
  min?: { x: number; y: number; z: number }
  max?: { x: number; y: number; z: number }
}

export type Axis = 'x' | 'y' | 'z'
export type Bound = 'min' | 'max'

/** A box thinner than this has no cells in it and snappyHexMesh ignores it. */
export const MIN_SPAN = 0.01

/**
 * Apply one typed bound, keeping the box from inverting.
 *
 * The edited side wins and the OTHER side is pushed out of its way — typing a
 * min above the max would otherwise produce a negative-extent searchableBox,
 * which snappy reads as an empty region and silently refines nothing.
 *
 * Pure, and returns a NEW pair, so the clamp can be tested without a project.
 */
export function clampZoneBound(
  min: number,
  max: number,
  bound: Bound,
  value: number,
): { min: number; max: number } {
  let lo = bound === 'min' ? value : min
  let hi = bound === 'max' ? value : max
  if (lo > hi - MIN_SPAN) {
    if (bound === 'min') lo = hi - MIN_SPAN
    else hi = lo + MIN_SPAN
  }
  return { min: lo, max: hi }
}

function zonesOf(project: unknown): ZoneBox[] {
  const bag = project as { runDefaults?: { meshConfig?: { refinementZones?: ZoneBox[] } } } | null
  return bag?.runDefaults?.meshConfig?.refinementZones ?? []
}

export function refinementZones(project: unknown = getProject()): ZoneBox[] {
  return zonesOf(project)
}

/**
 * Write one bound.
 *
 * One `edit` per commit, not per keystroke: `NumberField` commits on blur or
 * Enter, so a typed "1.20" is one undo step rather than four — which is the
 * same "one checkpoint per focus session" the legacy gets with its
 * `_rzBoundCheckpointPushed` flag (:27275).
 */
export function setRefinementZoneBound(id: string, bound: Bound, axis: Axis, value: number): void {
  if (!Number.isFinite(value)) return
  edit('setRefinementZoneBound', `${id} ${bound}.${axis} = ${value}`, (draft) => {
    const zone = zonesOf(draft).find((z) => z.id === id)
    if (!zone?.min || !zone?.max) return
    const clamped = clampZoneBound(zone.min[axis], zone.max[axis], bound, value)
    zone.min[axis] = clamped.min
    zone.max[axis] = clamped.max
  })
}

export function renameRefinementZone(id: string, name: string): void {
  const trimmed = name.trim()
  if (!trimmed) return
  edit('renameRefinementZone', trimmed, (draft) => {
    const zone = zonesOf(draft).find((z) => z.id === id)
    if (zone) zone.name = trimmed
  })
}

/**
 * Show or hide one zone's wireframe.
 *
 * A view flag, and the legacy takes no undo checkpoint for it (:27296) —
 * hiding a box to see the racks behind it is not an edit to the case, and
 * putting it on the undo stack would make Ctrl+Z undo the wrong thing.
 */
export function setRefinementZoneVisible(id: string, visible: boolean): void {
  mutateLive((project) => {
    const zone = zonesOf(project).find((z) => z.id === id)
    if (zone) zone.visible = visible
  }, { scene: false })
  notifyLiveEdit('toggleRefinementZoneVisibility', `${id} -> ${visible}`)
  // `notifyLiveEdit` keeps the SAME project object, so the viewport's
  // `[project]` effect does not fire and the wireframe would stay on screen.
  getMeshVisuals()?.refresh()
}

/** Display rounding for a bound box: millimetres, as the legacy list shows (:27019). */
export function displayBound(value: number): number {
  return Math.round(value * 1000) / 1000
}
