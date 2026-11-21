/**
 * Typing a vertex instead of clicking one.
 *
 * The 2D designer's draw tool places a point where the pointer is, snapped to
 * the grid. That is fine for sketching and useless for tracing a floor plan:
 * a wall at x = 12.340 m cannot be hit by eye at 40 px/m, and correcting each
 * vertex afterwards in the sidebar list means drawing the room twice.
 *
 * So the draw cursor carries a small X / Y / Z box — `_createCoordTooltip`
 * (web/room_2d_editor.js:1064-1198). Its rules, all of which live here:
 *
 *   - the boxes FOLLOW the snapped pointer until one of them takes focus, and
 *     freeze while it holds it, or a typed value would be overwritten by the
 *     next mouse move;
 *   - Enter PREVIEWS — it moves the cursor to the typed point without placing
 *     anything, so the number can be checked against the plan first;
 *   - Space places, and closes the ring instead if the point lands on the first
 *     vertex, which is the same rule a click obeys;
 *   - Z is read-only. The footprint is a plan at floor level and the room's
 *     height is a separate field; an editable Z would suggest otherwise.
 */

import { fromDisplay, toDisplay, type LengthUnit } from '../lengthUnits'
import type { Point, Vertex2 } from './room2dView'

/** Pixels down and to the right of the cursor, as the legacy positions it. */
export const COORD_TOOLTIP_OFFSET = 20

/** Decimals the boxes FOLLOW the pointer at. Typing is not limited to them. */
export const COORD_FOLLOW_DECIMALS = 1

/** What the three boxes read for a world point. */
export function coordDisplay(
  p: Vertex2,
  unit: LengthUnit,
): { x: string; y: string; z: string } {
  return {
    x: toDisplay(p[0], unit).toFixed(COORD_FOLLOW_DECIMALS),
    y: toDisplay(p[1], unit).toFixed(COORD_FOLLOW_DECIMALS),
    z: (0).toFixed(COORD_FOLLOW_DECIMALS),
  }
}

/**
 * The two boxes as a world point, or null when either is not a number.
 *
 * Null rather than a coerced 0: an empty X box would otherwise place a vertex
 * on the axis, which looks like a snap and is a typo.
 */
export function parseCoordEntry(x: string, y: string, unit: LengthUnit): Vertex2 | null {
  const wx = Number.parseFloat(x)
  const wy = Number.parseFloat(y)
  if (!Number.isFinite(wx) || !Number.isFinite(wy)) return null
  return [fromDisplay(wx, unit), fromDisplay(wy, unit)]
}

export type CoordPlacement = { kind: 'close' } | { kind: 'append'; at: Vertex2 }

/**
 * What placing a point at `at` does to the ring being drawn.
 *
 * Closes when the point lands within `closePx` of the FIRST vertex and there
 * are already three — the same test `onPointerDown` applies to a click, so a
 * typed vertex and a clicked one cannot disagree about whether the ring shut.
 */
export function coordPlacement(
  vertices: Vertex2[],
  at: Vertex2,
  toCanvas: (x: number, y: number) => Point,
  closePx: number,
): CoordPlacement {
  if (vertices.length >= 3) {
    const first = toCanvas(vertices[0]![0], vertices[0]![1])
    const here = toCanvas(at[0], at[1])
    if (Math.hypot(first.x - here.x, first.y - here.y) < closePx) return { kind: 'close' }
  }
  return { kind: 'append', at }
}

/**
 * The next box Tab moves to, cycling X and Y only.
 *
 * Z is read-only, so including it would give a third stop where nothing can be
 * typed — the legacy filters it out of the cycle for that reason. With two
 * stops left, shift-Tab and Tab land on the same box, which is why there is no
 * direction argument.
 */
export function nextCoordField(current: 'x' | 'y'): 'x' | 'y' {
  return current === 'x' ? 'y' : 'x'
}
