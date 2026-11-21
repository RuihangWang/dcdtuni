/**
 * The .cfdproj format modules, shared with the legacy editor rather than reimplemented.
 *
 * `cfdproj_schema.js`, `cfdproj_migrate.js` and `cfdproj_state_v4.js` are the
 * single source of truth for what a .cfdproj document IS: the bucket list, the
 * kind→bucket mapping, the v3→v4 migration, and the shape a newly created model
 * has to have. The Python runner's schema doc is written against them.
 *
 * Rewriting them in TypeScript would create a SECOND definition of the file
 * format, and the two would drift — which is exactly the failure this repo
 * already carries with the `meshGeometry` blob, where a derived shape and a raw
 * shape diverged in 25-43 fields without anyone noticing. The React rewrite is
 * of the UI. The document format is not the UI.
 *
 * `rack_state.js` joins them for the same reason: `syncRackModelState` is 190
 * lines normalising a rack's slots, doors, servers and partitions against each
 * other, and a rack that is subtly wrong exports subtly wrong STL patches.
 *
 * The four files are UMD-ish: they attach to `window` when there is one and to
 * `module.exports` when there is not. Under Vite there is a window and no
 * `module`, so importing them for their side effects populates the globals.
 * Import order matters — state_v4 reads `window.cfdprojSchema` at module scope.
 */

import '../../../shared/js/cfdproj_schema.js'
import '../../../shared/js/cfdproj_migrate.js'
// mesh_sizing before state_v4: a new document takes its sizing from it.
import '../../../shared/js/mesh_sizing.js'
import '../../../shared/js/cfdproj_state_v4.js'
import '../../../shared/js/model_pose_ports.js'
import '../../../shared/js/rack_state.js'
// v5 transient keyframes (tracks as state). It reads the parameter registry
// lazily, so injecting the JSON below — before any caller runs — is enough.
import '../../../shared/js/cfdproj_transient.js'
import transientParams from '../../../shared/transient/transient_params.json'
;(window as unknown as { __TRANSIENT_PARAMS__?: unknown }).__TRANSIENT_PARAMS__ = transientParams

import type { Bucket, CfdProject, Construction } from '../schema/cfdproj'

/** A v3-shaped factory seed, the input `finalizeNewModelV4` expects. */
export interface V3Seed {
  id: string
  kind: string
  name: string
  enabled: boolean
  showAnchorMarker: boolean
  opacity: number
  anchor: { x: number; y: number; z: number }
  base: { W: number; D: number; H: number }
  transform: unknown
  moveTool: unknown
  faces: unknown
  solidRegions: unknown[]
  customSTLBase64: string | null
  customOffset: { x: number; y: number; z: number }
  cfd: unknown
  [key: string]: unknown
}

interface CfdprojSchema {
  SCHEMA_VERSION: number
  BUCKETS: readonly string[]
  BUCKET_BY_KIND: Record<string, string>
  validateDocument(value:unknown): {path:string;msg:string}[]
  validateLiquidExperiment(value:unknown): {path:string;msg:string}[]
  validateLiquidIsolationConnections(value:unknown): {path:string;msg:string}[]
  validateHybridHeatBudget(value:unknown, project:unknown): {path:string;msg:string}[]
  validateGeometryTopology(value:unknown): {path:string;msg:string}[]
}

export function projectExperimentErrors(project: unknown): {path:string;msg:string}[] {
  const value=(project as {runDefaults?:{liquid?:{experiment?:unknown}}})?.runDefaults?.liquid?.experiment
  return value===undefined?[]:schema().validateLiquidExperiment(value)
}

/** Validate the optional project-owned total IT power ledger without inventing one for old files. */
export function projectHybridHeatBudgetErrors(project: unknown): {path:string;msg:string}[] {
  const value=(project as {runDefaults?:{hybridHeatBudget?:unknown}})?.runDefaults?.hybridHeatBudget
  return value===undefined?[]:schema().validateHybridHeatBudget(value,project)
}

/** Validate only the optional declaration structure; STL/triangle checks are a later compiler stage. */
export function projectGeometryTopologyErrors(project: unknown): {path:string;msg:string}[] {
  const value=(project as {geometryTopology?:unknown})?.geometryTopology
  return value===undefined?[]:schema().validateGeometryTopology(value)
}

/** One depth conflict as the shared predicate reports it (spec §4.3). */
export interface ServerDepthConflict {
  aUid: string
  bUid: string
  slots: number[]
  overlapM: number
}

/**
 * The shared writer's answer. `range` is a REFUSAL, not a silent clamp: the
 * panel shows the number the user typed, and a quiet clamp would leave the box
 * and the model disagreeing.
 */
export type ServerDepthWriteResult =
  | { ok: true }
  | { ok: false; error: 'range' | 'overlap' | 'missing' }

export interface RackStateModule {
  configureIds(gen: { nextModelId: () => string; nextPatchId: () => string }): void
  createRackModel(name: string): Construction
  syncRackModelState(model: Construction): void
  RACK_DEFAULT_UNIT_HEIGHT: number
  /**
   * The stacked-server API (spec 2026-09-17 §3-§4). All three read a model and
   * return data except `setServerDepthAbsolute`, which writes IN PLACE on the
   * object handed in — the caller owns the transaction (store draft or
   * mutateLive), the undo semantics and the sync that follows.
   */
  SERVER_DEPTH_MAX_FRACTION: number
  findServerDepthConflicts(model: unknown): ServerDepthConflict[]
  setServerDepthAbsolute(
    model: unknown,
    serverUid: string,
    inletOffset: number,
    outletOffset: number,
  ): ServerDepthWriteResult
  defaultServerDepthOffsets(model: unknown): { inletOffset: number; outletOffset: number }
  /**
   * A rack side's airflow ROLE and its display name — front = inlet,
   * back = outlet, unconditionally. Display only: the side keys stay the data.
   * `geometry/rackFaceLabels.ts` is the React entry point.
   */
  RACK_SIDE_FLOW_ROLE: Readonly<Record<'front' | 'back', 'inlet' | 'outlet'>>
  rackFaceRole(faceId: string): string
  rackFaceLabelKey(faceId: string): string | null
  rackFaceColorLabelKey(faceId: string): string | null
  rackFaceLabel(faceId: string, t?: (key: string) => string): string
}

interface CfdprojStateV4 {
  getInitialStateV4(): Record<string, unknown>
  /** A document created from nothing: getInitialStateV4 on the recommended cell-size rule. */
  newDocumentV4(): Record<string, unknown>
  /** Backfill only missing/empty document identity; preserve a saved UID exactly. */
  withProjectUid<T extends { uid?: string }>(doc: T): T
  bucketForKind(kind: string): string
  finalizeNewModelV4(seed: V3Seed): Construction
  normalizeStateV4(raw: unknown): Record<string, unknown>
  /** Restore the exact footprint carried by older floor documents; in place at load. */
  upgradeFloorPolygon(model: unknown): boolean
  /** E8c: gives a pre-E8c simulationConfig the transient per-step set and max once; in place. */
  upgradeSimConvergence(simulationConfig: unknown): boolean
  /**
   * Every construction as a FLAT list, in the shared module's bucket order.
   *
   * `cfdproj.ts` has its own `allModels`, which returns bucket/model pairs in
   * the order the TREE wants. The two orders are not required to agree, so
   * anything feeding a legacy consumer — the .liquidrun compiler, the .cfdrun
   * builder — has to use THIS one: those consumers index into the list and a
   * different order silently rewires the network.
   */
  allModels(state: unknown): Construction[]
}

declare global {
  interface Window {
    cfdprojSchema?: CfdprojSchema
    cfdprojStateV4?: CfdprojStateV4
    rackState?: RackStateModule
  }
}

function required<T>(value: T | undefined, name: string): T {
  if (!value) {
    throw new Error(
      `${name} is missing — the legacy .cfdproj modules did not load. ` +
        'They attach to window on import; check the import order in src/legacy/cfdprojModules.ts.',
    )
  }
  return value
}

export function schema(): CfdprojSchema {
  return required(window.cfdprojSchema, 'window.cfdprojSchema')
}

export function stateV4(): CfdprojStateV4 {
  return required(window.cfdprojStateV4, 'window.cfdprojStateV4')
}

export function rackState(): RackStateModule {
  return required(window.rackState, 'window.rackState')
}

/**
 * Re-attach the non-enumerable `kind` discriminator to every construction.
 *
 * `kind` is deliberately non-enumerable so it never serialises into a saved
 * .cfdproj — the V4 file derives kind from the bucket. The catch is that
 * `structuredClone` drops non-enumerable properties, so the undo store's deep
 * copy silently strips it, and the next `syncRackModelState` early-returns on
 * `kind !== 'rack'` and does nothing at all. Rebuilding it after every clone is
 * what keeps that from being a silent no-op.
 *
 * Derived from the bucket, exactly as the legacy `normalizeStateV4` does:
 * `meta.legacyKind` wins for the box subtypes, otherwise the bucket's canonical
 * kind.
 */
export function reviveKinds(project: CfdProject): CfdProject {
  const byKind = schema().BUCKET_BY_KIND
  const canonical: Record<string, string> = {}
  for (const [kind, bucket] of Object.entries(byKind)) {
    // Box subtypes (heat_source/crh/cold_plate) also live in `boxes`; only
    // plain `box` is the canonical kind for that bucket.
    if (bucket === 'boxes' && kind !== 'box') continue
    canonical[bucket] = kind
  }
  for (const [bucket, items] of Object.entries(project.constructions ?? {})) {
    if (!items) continue
    for (const model of Object.values(items)) {
      if (!model || typeof model !== 'object') continue
      const legacyKind = (model.meta as { legacyKind?: string } | undefined)?.legacyKind
      Object.defineProperty(model, 'kind', {
        value: legacyKind || canonical[bucket] || bucket,
        enumerable: false,
        configurable: true,
        writable: true,
      })
    }
  }
  return project
}

/** Which constructions bucket a v3 `kind` belongs in. */
export function bucketForKind(kind: string): Bucket {
  return stateV4().bucketForKind(kind) as Bucket
}

/**
 * Turn a v3 factory seed into a ready V4 model.
 *
 * Identical to the shape the load path produces, because it runs the same
 * migrator — which is the reason for going through a v3 seed at all rather than
 * writing V4 directly.
 */
export function finalizeNewModel(seed: V3Seed): Construction {
  return stateV4().finalizeNewModelV4(seed)
}
