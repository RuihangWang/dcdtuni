/**
 * Moving models: the same arithmetic the legacy editor uses.
 *
 * `js/transform_tools.js` holds the oriented bounding box, the separating-axis
 * collision test, the project-bounds check and both snap modes. Reimplementing
 * any of it here would be a second answer to "where does this rack end up",
 * and the difference — a few millimetres from a snap — reaches the mesh.
 *
 * The module needs the BUILT scene: a rack's real extent includes its doors and
 * equipment, so world bounds come from the rendered meshes and not from
 * `geometry.size`. That is why this takes a context rather than a project.
 */

import type * as THREE from 'three'
import type { CfdProject, Construction } from '../schema/cfdproj'

export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface SnapLock {
  target: number
  side: 'min' | 'max'
}

export interface ModelGap {
  a: string
  b: string
  axis: 'x' | 'y' | 'z'
  gap: number
  direction: 1 | -1
}

export interface GapMove {
  uid: string
  axis: 'x' | 'y' | 'z'
  from: number
  to: number
  gap: number
  against: string
}

export interface TransformToolsApi {
  getRenderRootWorldBounds(root: THREE.Object3D | null): THREE.Box3 | null
  /** The nominal box (location + size + rotation): what the export writes and what the snap aligns. */
  getModelAABB(model: Construction | null): THREE.Box3 | null
  snapLocks(): { x: SnapLock | null; y: SnapLock | null; z: SnapLock | null }
  invalidateBounds(modelId?: string): void
  snapThreshold(): number | null
  querySnapCandidates(box: THREE.Box3, margin: number): { id: string; bounds: THREE.Box3 }[]
  initSnapCache(): void
  clearSnapCache(): void
  computeProximitySnap(movingIds: string[], includeFace: boolean, includeEdge: boolean): Vec3
  computeGridSnap(movingIds: string[]): Vec3
  analyticalCollisionCheck(movingModels: Construction[], movingIds: string[]): boolean
  isModelWithinProjectBounds(modelId: string): boolean
  areModelsWithinProjectBounds(modelIds: string[]): boolean
}

export interface Obb {
  center: THREE.Vector3
  half: [number, number, number]
  axes: [THREE.Vector3, THREE.Vector3, THREE.Vector3]
}

interface TransformToolsModule {
  getModelAABB(model: Construction | null): THREE.Box3 | null
  /** Round to the micrometre — every coordinate a drag writes goes through this. */
  quantize(v: number): number
  findModelGaps(models: Construction[], maxGap?: number): ModelGap[]
  closeModelGaps(models: Construction[], maxGap?: number): GapMove[]
  getModelOBB(model: Construction): Obb
  getOBBCorners(obb: Obb): THREE.Vector3[]
  obbOverlapSAT(a: Obb, b: Obb, eps: number): boolean
  hasModelRotation(model: Construction): boolean
  isCollisionExempt(kind: string): boolean
  createTransformTools(ctx: TransformToolsContext): TransformToolsApi
}

export interface TransformToolsContext {
  state(): CfdProject
  /** View settings — only `view.gridSpacing` is read, by the grid snap. */
  uiState(): { view?: { gridSpacing?: number } }
  allModels(): Construction[]
  renderRoots(): THREE.Object3D[]
  getRenderRootByModelId(id: string): THREE.Object3D | null
  isModelEnabled(modelOrId: Construction | string): boolean
  getModelById(id: string): Construction | null
  /** Metres per screen pixel at the selection; makes the snap radius 10 px on screen. */
  snapPixelWorld?(): number | null
}

export function transformToolsModule(): TransformToolsModule {
  const mod = (window as unknown as { transformTools?: TransformToolsModule }).transformTools
  if (!mod) {
    throw new Error('js/transform_tools.js is not loaded — import legacy/geometryBuilder first')
  }
  return mod
}

export function createTransformTools(ctx: TransformToolsContext): TransformToolsApi {
  return transformToolsModule().createTransformTools(ctx)
}

export function getModelOBB(model: Construction): Obb {
  return transformToolsModule().getModelOBB(model)
}

export function quantize(v: number): number {
  // The same arithmetic as the module's, so a caller that runs before the
  // legacy modules are loaded (drag-create in a unit test) rounds identically.
  const mod = (window as unknown as { transformTools?: TransformToolsModule }).transformTools
  return mod ? mod.quantize(v) : Math.round(v * 1e6) / 1e6
}

export function findModelGaps(models: Construction[], maxGap = 0.005): ModelGap[] {
  return transformToolsModule().findModelGaps(models, maxGap)
}

export function closeModelGaps(models: Construction[], maxGap = 0.005): GapMove[] {
  return transformToolsModule().closeModelGaps(models, maxGap)
}
