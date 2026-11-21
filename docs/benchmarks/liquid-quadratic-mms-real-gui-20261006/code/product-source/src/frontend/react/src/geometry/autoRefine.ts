/**
 * Auto-detect refinement: pick a level that can actually resolve the geometry.
 *
 * A partition wall 40 mm thick under a 0.34 m base cell is invisible to
 * snappyHexMesh — the cell straddles the whole wall and the surface never gets
 * a face. The fix is a refinement level fine enough that at least TWO cells
 * span the thinnest feature, and this computes it rather than leaving the user
 * to guess in powers of two.
 *
 * Ported from `computeMinSafeLevel` / `_autoDetectPartitionWall`
 * (STL_generate.html:25895, 25919). The rule that matters: the level is FORCED
 * to the computed value, not raised to it. A hand-set level higher than
 * necessary is pulled back down, because over-refining a 40 mm wall is how a
 * case goes from 3 million cells to 30 million.
 */

import type { CfdProject, Construction } from '../schema/cfdproj'
import { meshConfigOf } from '../run/meshConfig'
import * as store from '../projectStore'

/** The [min, max] surface level that puts two cells across `thickness`. */
export function minSafeLevel(baseSize: number, thickness: number): [number, number] {
  if (thickness <= 0 || baseSize <= 0) return [0, 0]
  const maxCellSize = thickness / 2
  const maxLevel = Math.max(Math.ceil(Math.log2(baseSize / maxCellSize)), 0)
  return [Math.max(maxLevel - 1, 0), maxLevel]
}

/**
 * The base size the detection measures against.
 *
 * `meshDetectBaseSize` when set, otherwise the real base size — the detect
 * value exists so a user can ask "what would this need at a coarser base?"
 * without changing the mesh they are about to run.
 */
export function detectBaseSize(project: CfdProject | null): number {
  const mesh = meshConfigOf(project)
  const detect = Number(mesh.meshDetectBaseSize)
  if (detect > 0) return detect
  const base = Number(mesh.meshBaseSize)
  return base > 0 ? base : 0.34
}

export interface AutoDetectResult {
  thickness: number
  oldLevel: [number, number]
  newLevel: [number, number]
  oldFeature: number
  newFeature: number
  changed: boolean
}

/**
 * Set the partition-wall levels from the THINNEST partition wall in the room.
 *
 * The thinnest one governs: a level that resolves it resolves the rest, and
 * anything coarser loses that wall's surface entirely.
 */
export function autoDetectRefinement(
  project: CfdProject | null,
  models: Construction[],
): AutoDetectResult | null {
  if (!project) return null
  const walls = models.filter((m) => m.kind === 'partitionWall')
  if (!walls.length) return null

  let minThickness = Infinity
  for (const wall of walls) {
    const d = Number((wall.geometry as { size?: { z?: number } })?.size?.z)
    if (Number.isFinite(d) && d > 0) minThickness = Math.min(minThickness, d)
  }
  if (!Number.isFinite(minThickness)) return null

  const safe = minSafeLevel(detectBaseSize(project), minThickness)
  const sim = ((project as unknown as { runDefaults?: { simulationConfig?: Record<string, unknown> } })
    .runDefaults?.simulationConfig ?? {}) as Record<string, unknown>
  const current = sim.partitionWallRefinementLevel
  const oldLevel: [number, number] = Array.isArray(current) && current.length === 2
    ? [Number(current[0]), Number(current[1])]
    : [1, 1]
  const oldFeature = Number(sim.partitionWallFeatureLevel) || 2
  const newFeature = safe[1]
  const changed = safe[0] !== oldLevel[0] || safe[1] !== oldLevel[1] || newFeature !== oldFeature

  store.edit('autoDetectRefinement', `partitionWall ${safe[0]},${safe[1]}`, (draft) => {
    const bag = draft as unknown as { runDefaults?: { simulationConfig?: Record<string, unknown> } }
    const target = bag.runDefaults?.simulationConfig
    if (!target) return
    target.partitionWallRefinementLevel = safe
    target.partitionWallFeatureLevel = newFeature
  })

  return { thickness: minThickness, oldLevel, newLevel: safe, oldFeature, newFeature, changed }
}
