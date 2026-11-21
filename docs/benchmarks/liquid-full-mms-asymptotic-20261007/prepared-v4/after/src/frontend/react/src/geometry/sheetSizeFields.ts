/**
 * Which size boxes a ZERO-THICKNESS surface should offer.
 *
 * A wall, a baffle and a rectangular floor are sheets: the shared renderer
 * builds them from two components only — `renderFloorModel` and its wall and
 * baffle siblings (js/geometry_scene.js:1005) take the two IN-PLANE size
 * components and nothing else. The generic three-box size group therefore gives
 * these three kinds an editable box for an axis that is never drawn and never
 * exported: the number reaches the .cfdproj and changes nothing at all.
 *
 * The hint matters as much as the missing box. A floor's HEIGHT comes from its
 * anchor Z, not from a size field, and that is the first thing someone looks for
 * when a floor sits at the wrong level.
 *
 * Ports the `sizeHtml` branches of `renderWallBaffleInspector`
 * (STL_generate.html:8372-8395).
 */

import type { TranslationKey } from '../i18n'

export interface SheetSizeField {
  /** The component of `geometry.size` this box writes. */
  axis: 'x' | 'y' | 'z'
  label: TranslationKey
  /** The legacy `updateModel` code for the same box, kept for parity checks. */
  legacyKey: 'W' | 'H' | 'D'
}

export interface SheetSizeSpec {
  /** The group heading the legacy gives this kind. */
  title: TranslationKey
  fields: SheetSizeField[]
  hint: TranslationKey
}

const SPECS: Record<string, SheetSizeSpec> = {
  floor: {
    title: 'inspector.floor_naming',
    fields: [
      { axis: 'x', label: 'label.length_w_x', legacyKey: 'W' },
      // The floor's second in-plane axis is Z: it lies flat, so its "width"
      // runs along the room's depth. `y` is the axis with no thickness.
      { axis: 'z', label: 'label.width_d_y', legacyKey: 'D' },
    ],
    hint: 'hint.floor_zero_thickness',
  },
  wall: {
    title: 'inspector.wall_naming',
    fields: [
      { axis: 'x', label: 'label.width_w', legacyKey: 'W' },
      { axis: 'y', label: 'label.height_h', legacyKey: 'H' },
    ],
    hint: 'hint.wall_zero_thickness',
  },
  baffle: {
    title: 'inspector.baffle_naming',
    fields: [
      { axis: 'x', label: 'label.width_w', legacyKey: 'W' },
      { axis: 'y', label: 'label.height_h', legacyKey: 'H' },
    ],
    hint: 'hint.baffle_zero_thickness',
  },
}

/**
 * The in-plane size spec for a sheet kind, or null for a solid body.
 *
 * A POLYGON floor is not covered here: its size is its outline's bounding box
 * and it has its own panel (`FloorPolygonSection`), so the caller tests for that
 * first exactly as the legacy does.
 */
export function sheetSizeSpec(kind: string | undefined): SheetSizeSpec | null {
  return kind ? SPECS[kind] ?? null : null
}

/** The axis a sheet has no thickness on — the box that must NOT be offered. */
export function sheetDeadAxis(kind: string | undefined): 'x' | 'y' | 'z' | null {
  const spec = sheetSizeSpec(kind)
  if (!spec) return null
  const live = new Set(spec.fields.map((f) => f.axis))
  return (['x', 'y', 'z'] as const).find((axis) => !live.has(axis)) ?? null
}
