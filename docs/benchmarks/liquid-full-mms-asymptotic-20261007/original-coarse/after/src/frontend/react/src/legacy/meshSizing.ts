/**
 * The mesh-sizing arithmetic, shared with the legacy editor.
 *
 * `shared/js/mesh_sizing.js` owns the numbers: what cell edge every level
 * resolves to, the rack-region level dcCart will raise to (and the
 * `rackCellMinSize` cap on it), the first-layer floor per class, the cell
 * budget from the floor area, and the 6Sigma preset that writes the measured
 * rule (docs/reference/sixsigma-cell-size-rule.md) into a meshConfig patch.
 * Both panels read the same readout and apply the same patch, so they cannot
 * disagree about what a setting means.
 */

import '../../../shared/js/mesh_sizing.js'

import type { CfdProject } from '../schema/cfdproj'

export interface SizingRow {
  key: 'background' | 'room_wall' | 'rack_region' | 'acu' | 'white_model' | 'baffle_floor'
  level: number
  cellMm: number
}

export interface MeshSizingDescription {
  base: number
  rows: SizingRow[]
  rack: { level: number; requested: number; raisedFor: number | null; capped: boolean; slab: number | null }
  firstLayer: { fraction: number; maxMm: number; weldMm: number; floorMmByClass: Record<string, number> }
  budget: { areaM2: number; racks: number; recommendedCells: number; maxGlobalCells: number; cellsPerM2: number }
  finestMm: number
  sixSigma: Record<string, number>
}

export interface SixSigmaPreset {
  patch: Record<string, unknown>
  notes: Array<{ key: string; from: unknown; to: unknown }>
  areaM2: number
}

interface MeshSizingApi {
  describeMeshSizing(doc: CfdProject | null): MeshSizingDescription
  sixSigmaMeshConfig(doc: CfdProject | null): SixSigmaPreset
  dcCartProductMeshConfig(doc: CfdProject | null): SixSigmaPreset
  levelForCell(base: number, target: number): number
  cellAtLevel(base: number, level: number): number
  floorArea(doc: CfdProject | null): number
  SIXSIGMA: Record<string, number>
}

export function meshSizing(): MeshSizingApi {
  const api = (window as unknown as { meshSizing?: MeshSizingApi }).meshSizing
  if (!api) throw new Error('shared/js/mesh_sizing.js did not install window.meshSizing')
  return api
}
