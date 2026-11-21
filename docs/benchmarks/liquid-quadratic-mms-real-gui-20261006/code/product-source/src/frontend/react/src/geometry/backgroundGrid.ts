/**
 * The XOZ background grid.
 *
 * Ported from `rebuildBackgroundGrid` (STL_generate.html:1963), which this port
 * had reduced to a four-line `new THREE.GridHelper(40, …)`. Three things were
 * lost in that reduction and all three are visible on screen:
 *
 * - **Contrast.** The port drew `0x2e2e42` at `opacity 0.25`. Measured against
 *   the legacy on the same camera and the same viewport background
 *   (`rgb(20,25,32)` in both apps): the port's brightest grid pixel was
 *   `rgb(29,35,45)`, the legacy's `rgb(62,76,89)` — a fifth of the contrast.
 *   The legacy colours and 0.6 opacity are restored here.
 *
 * - **Extent.** A fixed 40 m grid stops inside `case_14`, a 41.3 m room. The
 *   legacy sizes it from the spacing instead, so the grid always reaches past
 *   the room, and caps the line count for the single draw call.
 *
 * - **Depth.** Without `depthWrite:false` and the -5 mm drop the grid
 *   z-fights with a floor sheet sitting at y=0, and being decorative it wins
 *   often enough to speckle the floor.
 */

import * as THREE from 'three'

/** Max background-grid extent in metres (x10 for ultra-large data centers). */
export const GRID_RENDER_RANGE = 2000
/** Perf ceiling on the line count — the grid is a single LineSegments call. */
export const GRID_MAX_DIVISIONS = 4000
export const GRID_CENTER_COLOR = 0x8ba3b8
export const GRID_LINE_COLOR = 0x5a6b7d
export const GRID_OPACITY = 0.6
/**
 * Just below y=0, so the grid sits under floor geometry rather than fighting
 * it, while still occluding correctly when the camera goes below the floor.
 */
export const GRID_Y_OFFSET = -0.005

/** The legacy's clamp: a 1 mm grid is 2 million lines, a 10 m grid is not one. */
export function clampGridSpacing(spacingM: number): number {
  const value = Number(spacingM)
  if (!Number.isFinite(value) || value <= 0) return 0.1
  return Math.min(1, Math.max(0.01, value))
}

/**
 * Cell count across the grid, forced EVEN.
 *
 * An odd division count puts the origin at a cell CENTRE, so the two centre
 * axes stop coinciding with x=0 / z=0 and the grid no longer says where the
 * origin is — which is the one thing it is for.
 */
export function gridDivisions(spacingM: number): number {
  const spacing = clampGridSpacing(spacingM)
  let divisions = Math.round(Math.min(GRID_RENDER_RANGE, GRID_MAX_DIVISIONS * spacing) / spacing)
  divisions = Math.max(4, divisions)
  if (divisions % 2 !== 0) divisions += 1
  return divisions
}

/** Side length in metres: `divisions * spacing`, so the pitch is exact. */
export function gridExtent(spacingM: number): number {
  return gridDivisions(spacingM) * clampGridSpacing(spacingM)
}

/** Build the helper for one spacing, in metres. */
export function buildBackgroundGrid(spacingM: number): THREE.GridHelper {
  const divisions = gridDivisions(spacingM)
  const grid = new THREE.GridHelper(
    gridExtent(spacingM),
    divisions,
    GRID_CENTER_COLOR,
    GRID_LINE_COLOR,
  )
  grid.position.y = GRID_Y_OFFSET
  // Decorative: drawn first, and never blocking a solid object.
  grid.renderOrder = -1
  const materials = Array.isArray(grid.material) ? grid.material : [grid.material]
  for (const material of materials) {
    material.transparent = true
    material.opacity = GRID_OPACITY
    material.depthWrite = false
    material.depthTest = true
  }
  return grid
}

/** Free the helper's geometry and every material it owns. */
export function disposeBackgroundGrid(grid: THREE.GridHelper) {
  grid.parent?.remove(grid)
  grid.geometry.dispose()
  const materials = Array.isArray(grid.material) ? grid.material : [grid.material]
  for (const material of materials) material.dispose()
}
