/**
 * The .cfdproj V4 shape, typed.
 *
 * The legacy editor keeps this in one global `state` object and reaches into it
 * from ~32,000 lines of script. Typing it is the first thing the rewrite buys:
 * every field below was read off a real project (case/Base_leakage), not guessed.
 *
 * `constructions` is a bucket-per-kind dictionary whose insertion order is the
 * order the editor shows — which is exactly why a freshly added Rack_C10 lands
 * at the bottom of its category. Ordering is a VIEW concern here too:
 * `sortModelsForTree` never mutates the buckets, so export order and file bytes
 * are untouched.
 */

import type { TranslationKey } from '../i18n'

export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface Quat {
  x: number
  y: number
  z: number
  w: number
}

export interface ModelDisplay {
  opacity?: number
  showAnchorMarker?: boolean
}

export interface ModelMeta {
  name: string
  enabled?: boolean
  description?: string
  tags?: string[]
  display?: ModelDisplay
}

export interface ModelGeometry {
  location?: Vec3
  size?: Vec3
  quaternion?: Quat
  scale?: Vec3
  faces?: Record<string, unknown>
  cutouts?: unknown[]
}

export interface Construction {
  uid: string
  meta: ModelMeta
  geometry?: ModelGeometry
  polygon?: unknown
  isRoomBoundary?: boolean
  [key: string]: unknown
}

/**
 * Bucket names as they appear in `constructions`.
 *
 * Must stay equal to `BUCKETS` in js/cfdproj_schema.js — the format module is
 * the authority and this is a second hand-kept copy;
 * `test/schema/cfdproj.test.ts` asserts they are the same set. It caught this
 * list missing `liquidFlowNetworks`, which three saved projects already use.
 *
 * `raisedFloor` and `falseCeiling` are deliberately NOT here. They are
 * SINGLETON SLOTS, not buckets: `emptyConstructions()` seeds them as `null`
 * rather than `{}` (js/cfdproj_schema.js:63), because a room has at most one of
 * each. Anything iterating buckets must not expect a uid-keyed dict there.
 */
export const BUCKETS = [
  'boxes',
  'racks',
  'acus',
  'cdus',
  'pipeSegments',
  'pipeJunctions',
  'pipes',
  'walls',
  'partitionWalls',
  'baffles',
  'floors',
  'customMeshes',
  'surfaces',
  'rows',
  'servers',
  'sensors',
  'liquidFlowNetworks',
  'heatGains',
] as const

/**
 * The two singleton slots beside the buckets: one raised floor, one false
 * ceiling, each a model or `null`. Kept separate so a `for (const b of BUCKETS)`
 * loop never reaches into them expecting a dict.
 */
export const SINGLETON_SLOTS = ['raisedFloor', 'falseCeiling'] as const

export type SingletonSlot = (typeof SINGLETON_SLOTS)[number]

export type Bucket = (typeof BUCKETS)[number]

/**
 * Display order and labels, mirroring the legacy tree's categoryOrder.
 *
 * Labels are dictionary KEYS, not text: the tree has to switch language with the
 * rest of the app, and a hard-coded Chinese label here would survive the switch.
 */
export const CATEGORIES: { bucket: Bucket; icon: string; labelKey: TranslationKey }[] = [
  { bucket: 'boxes', icon: '📦', labelKey: 'category.box' },
  { bucket: 'racks', icon: '🗄️', labelKey: 'category.rack' },
  { bucket: 'acus', icon: '❄️', labelKey: 'category.acu' },
  { bucket: 'cdus', icon: '🌊', labelKey: 'category.cdu' },
  { bucket: 'pipeSegments', icon: '🔗', labelKey: 'category.pipe_segment' },
  { bucket: 'pipeJunctions', icon: '🔀', labelKey: 'category.pipe_junction' },
  { bucket: 'pipes', icon: '🧵', labelKey: 'category.pipe' },
  { bucket: 'walls', icon: '🧱', labelKey: 'category.wall' },
  { bucket: 'partitionWalls', icon: '🧱', labelKey: 'category.partition_wall' },
  { bucket: 'baffles', icon: '📄', labelKey: 'category.baffle' },
  { bucket: 'floors', icon: '🟫', labelKey: 'category.floor' },
  { bucket: 'customMeshes', icon: '🧊', labelKey: 'category.custom' },
  { bucket: 'surfaces', icon: '🔲', labelKey: 'category.surface' },
  { bucket: 'rows', icon: '🧱', labelKey: 'category.row' },
  { bucket: 'servers', icon: '🖥️', labelKey: 'category.server' },
  { bucket: 'sensors', icon: '📍', labelKey: 'category.sensor' },
  { bucket: 'heatGains', icon: '🔥', labelKey: 'category.heat_gain' },
]

export interface RoomGeometry {
  height: number
  /** Footprint polygon in plan coordinates: [[x, y], ...] */
  plane: [number, number][]
  tileSize?: number
  temperature?: number
}

/** Optional V4 mixed air/liquid IT power declaration; runtime validation is in cfdproj_schema.js. */
export interface HybridHeatBudgetV1 {
  version: 1
  source: 'mock' | 'vendor' | 'calibrated'
  source_reference: string
  load_mapping: 'shared_heat_multiplier'
  air_surface_allocation: {
    front_fraction: number
    rear_fraction: number
  }
  servers: Record<string, { total_it_power_W: number }>
}

/** Strict V1 relation declarations; geometric admission is deliberately not implemented yet. */
export interface GeometryTopologyV1 {
  version: 1
  mode: 'strict'
  coverage: 'airInternalSheets'
  wallPairs: Record<string, {
    sides: [string, string]
    thickness_m: number
    meshReferenceFaceUid: string
    meshTreatment: 'collapsedAirBarrier'
  }>
  independentPairs: Record<string, {
    faces: [string, string]
    relation: 'fluidGap'
    minGap_m: number
  }>
  edgeContacts: Record<string,
    | { kind: 'edgeEdge'; a: { sourceFaceUid: string; sourceEdgeUid: string }; b: { sourceFaceUid: string; sourceEdgeUid: string } }
    | { kind: 'edgeFace'; edge: { sourceFaceUid: string; sourceEdgeUid: string }; face: { sourceFaceUid: string } }
  >
}

export interface CfdProject {
  schemaVersion?: number | string
  uid?: string
  name?: string
  meta?: Record<string, unknown>
  geometry?: RoomGeometry
  geometryTopology?: GeometryTopologyV1
  constructions?: Partial<Record<Bucket | string, Record<string, Construction> | null>>
  runDefaults?: { hybridHeatBudget?: HybridHeatBudgetV1; [key: string]: unknown }
}

/** A construction plus the bucket it came from. */
export interface TreeModel {
  bucket: Bucket
  model: Construction
}

/** Every construction, in bucket order then insertion order. */
export function allModels(project: CfdProject): TreeModel[] {
  const out: TreeModel[] = []
  for (const { bucket } of CATEGORIES) {
    const items = project.constructions?.[bucket]
    if (!items) continue
    for (const model of Object.values(items)) {
      if (model && typeof model === 'object') out.push({ bucket, model })
    }
  }
  return out
}

export function modelsIn(project: CfdProject, bucket: Bucket): Construction[] {
  const items = project.constructions?.[bucket]
  if (!items) return []
  return Object.values(items).filter((m): m is Construction => !!m && typeof m === 'object')
}

// ---------------------------------------------------------------------------
// Ordering
// ---------------------------------------------------------------------------

export type SortMode = 'name-asc' | 'name-desc' | 'manual'

export const SORT_MODES: SortMode[] = ['name-asc', 'name-desc', 'manual']

export const SORT_LABEL_KEYS: Record<SortMode, TranslationKey> = {
  'name-asc': 'tree.sort_name_asc',
  'name-desc': 'tree.sort_name_desc',
  manual: 'tree.sort_manual',
}

/**
 * Natural ordering, so Rack_C2 precedes Rack_C10.
 *
 * `numeric: true` is the whole point: plain lexicographic order puts C10 before
 * C2, which is what makes a newly added rack look misplaced.
 */
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Sort a copy. `manual` returns the same array, so it allocates nothing. */
export function sortModelsForTree(models: Construction[], mode: SortMode): Construction[] {
  if (mode === 'manual') return models
  const dir = mode === 'name-desc' ? -1 : 1
  return [...models].sort(
    (a, b) => dir * collator.compare(a.meta?.name ?? '', b.meta?.name ?? ''),
  )
}

export function nextSortMode(mode: SortMode): SortMode {
  const i = SORT_MODES.indexOf(mode)
  return SORT_MODES[(i + 1) % SORT_MODES.length]!
}

/** Total construction count, for the header badge. */
export function countModels(project: CfdProject): number {
  return allModels(project).length
}

// ---------------------------------------------------------------------------
// Patch physics — the per-face boundary conditions the toolbar flow monitor reads
// ---------------------------------------------------------------------------

export interface PatchPhysics {
  role?: 'supply' | 'return' | string
  velocity?: number
  temperature?: number
  volumeFlowRate?: number
  [key: string]: unknown
}

interface FacePatch {
  id?: string
  physics?: PatchPhysics
}

interface Face {
  patches?: FacePatch[]
}

/**
 * Every patch physics record on a model, keyed by patch id.
 *
 * Two sources, as in the legacy `cfdproj_state_v4.patchPhysicsMap`: physics
 * bound to a real geometry patch, and `cooling.unboundPatchPhysics` for ids
 * whose patch no longer exists. Bound wins on a clash — an orphaned unbound
 * entry is stale by definition.
 */
export function patchPhysicsMap(model: Construction): Record<string, PatchPhysics> {
  const out: Record<string, PatchPhysics> = {}
  const cooling = model.cooling as { unboundPatchPhysics?: Record<string, PatchPhysics> } | undefined
  const unbound = cooling?.unboundPatchPhysics
  if (unbound && typeof unbound === 'object') Object.assign(out, unbound)

  const faces = model.geometry?.faces
  if (faces) {
    const list: Face[] = Array.isArray(faces) ? (faces as Face[]) : (Object.values(faces) as Face[])
    for (const face of list) {
      for (const patch of face?.patches ?? []) {
        if (patch?.id != null && patch.physics) out[patch.id] = patch.physics
      }
    }
  }
  return out
}

export interface FlowTotals {
  supply: number
  return: number
}

/** Total ACU supply and return volume flow, in m³/s. Informational only. */
export function flowTotals(project: CfdProject | null): FlowTotals {
  const totals: FlowTotals = { supply: 0, return: 0 }
  if (!project) return totals
  for (const model of modelsIn(project, 'acus')) {
    for (const physics of Object.values(patchPhysicsMap(model))) {
      const q = Number(physics.volumeFlowRate) || 0
      if (physics.role === 'supply') totals.supply += q
      else if (physics.role === 'return') totals.return += q
    }
  }
  return totals
}
