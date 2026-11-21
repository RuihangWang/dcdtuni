import type { CfdProject } from '../schema/cfdproj'
import { stateV4 } from '../legacy/cfdprojModules'

/**
 * The mesh half of `runDefaults`, typed.
 *
 * Defaults come from `cfdproj_state_v4.getInitialStateV4()`, not from a copy
 * kept here. The HTML panel's `value=` attributes disagree with the real
 * defaults in several places — `ms-base-size` reads 0.2 in the markup while
 * DEFAULT_MESH_CONFIG says 0.25, and the panel overwrites the attribute on tab
 * entry — so the markup is not a source of truth for anything.
 *
 * Three fields deliberately live in `simulationConfig` rather than here, which
 * is easy to get wrong because they sit in the same panel section:
 * `partitionWallRefinementLevel`, `partitionWallFeatureLevel` and
 * `extraLocations`.
 */

export interface Vec3Like {
  x: number
  y: number
  z: number
}

export interface RefinementZone {
  name?: string
  min?: Vec3Like
  max?: Vec3Like
  level?: number
  [key: string]: unknown
}

export interface MeshConfig {
  meshBaseSize: number
  meshDetectBaseSize: number | null
  /** Whole mesh pipeline budget, with a product ceiling of 300 seconds. */
  meshTimeLimitSeconds?: number | null
  maxGlobalCells: number
  parallelMesh: boolean
  meshProcess: number
  meshMode?: MeshMode
  meshEngine?: MeshEngine
  firstCellSize?: number | null
  /** dcCart sizing keys (2026-09-22). Absent = the runner's own defaults. */
  weldTolerance?: number | null
  firstLayerMinFraction?: number | null
  firstLayerMax?: number | null
  firstLayerSealGap?: number | null
  rackCellMinSize?: number | null
  refinementLevel: [number, number]
  defaultFeatureLevel: number
  rackRegionLevel: number
  acuRefinementLevel: [number, number]
  acuFeatureLevel: number
  boxRefinementLevel: [number, number]
  boxFeatureLevel: number
  wallBafflesRefinementLevel: [number, number]
  baffleFeatureLevel: number
  floorRefinementLevel: [number, number]
  floorFeatureLevel: number
  locationInMesh: Vec3Like
  locInMeshVisible: boolean
  blockMeshBounds: unknown
  refinementZones: RefinementZone[]
  /** Legacy booleans still honoured by the runner's mode resolver. */
  cartesianMode?: boolean
  structuredGrid?: boolean
  [key: string]: unknown
}

export type MeshMode = 'bodyFitted' | 'cartesianLocalized' | 'structured'

export const MESH_MODES: MeshMode[] = ['bodyFitted', 'cartesianLocalized', 'structured']

/**
 * Which executable builds the mesh — an axis orthogonal to `meshMode`.
 *
 * `snappy` is blockMesh + snappyHexMesh, the pipeline as it always was and the
 * default when the field is absent. `dcCart` is the repo's own cut-cell
 * Cartesian mesher (third_parties/dcCartMesh/DCCARTMESH.md): one pass, serial,
 * same dictionaries, same patch names. `meshgen` is the vendored
 * MeshGenerator cut-cell engine (runner `_run_meshgen_pipeline`): one
 * `meshgen-cli cfdrun --dc-mode` pass that emits the final boundary /
 * faceZones, so blockMesh / snappy / createBaffles never run.
 */
export type MeshEngine = 'snappy' | 'dcCart' | 'meshgen'

export const MESH_ENGINES: MeshEngine[] = ['snappy', 'dcCart', 'meshgen']

export function resolveMeshEngine(config: Partial<MeshConfig> | undefined): MeshEngine {
  const explicit = config?.meshEngine
  return explicit && MESH_ENGINES.includes(explicit) ? explicit : 'snappy'
}

/**
 * Which mesh mode a project actually runs in.
 *
 * `meshMode` is absent from most real projects, so reading the raw field would
 * show "bodyFitted" for cases that in fact mesh as `structured` — the repo
 * default since commit 5af4e80. This mirrors the runner's `_resolve_mesh_mode`
 * and the panel's `_resolveMeshMode`: an explicit valid mode wins, then the
 * legacy boolean pair, then `structured`.
 */
export function resolveMeshMode(config: Partial<MeshConfig> | undefined): MeshMode {
  // The semantics follow the engine (2026-09-09): snappy is body-fitted,
  // dcCart is pure cut-cell on a uniform background. `meshMode` is ignored.
  return resolveMeshEngine(config) === 'dcCart' ? 'cartesianLocalized' : 'bodyFitted'
}

let cachedDefaults: MeshConfig | null = null

/** The runner's own defaults, read from the shared V4 state factory. */
export function defaultMeshConfig(): MeshConfig {
  if (!cachedDefaults) {
    const initial = stateV4().getInitialStateV4() as {
      runDefaults?: { meshConfig?: Partial<MeshConfig> }
    }
    cachedDefaults = { ...(initial.runDefaults?.meshConfig ?? {}) } as MeshConfig
  }
  return cachedDefaults
}

/** A project's mesh config, with every default filled in. */
export function meshConfigOf(project: CfdProject | null): MeshConfig {
  const runDefaults = (project as { runDefaults?: { meshConfig?: Partial<MeshConfig> } } | null)
    ?.runDefaults
  return { ...defaultMeshConfig(), ...(runDefaults?.meshConfig ?? {}) }
}

export interface SimulationConfig {
  partitionWallRefinementLevel?: [number, number]
  partitionWallFeatureLevel?: number
  extraLocations?: unknown[]
  [key: string]: unknown
}

export function simulationConfigOf(project: CfdProject | null): SimulationConfig {
  const runDefaults = (project as { runDefaults?: { simulationConfig?: SimulationConfig } } | null)
    ?.runDefaults
  return runDefaults?.simulationConfig ?? {}
}

/**
 * Refinement rows, in the panel's order.
 *
 * `surface` is a two-element [min, max] array; `feature` is a SCALAR despite
 * sitting in the same row — the legacy `setMeshRefineLevel` special-cases any
 * key ending in `FeatureLevel` (plus `rackRegionLevel`) and ignores the index.
 * Writing a 2-element array into those would produce a snappyHexMeshDict the
 * runner cannot read.
 */
export interface RefinementRow {
  id: string
  labelKey: string
  surfaceKey: string | null
  featureKey: string | null
  /** Which runDefaults bucket the keys live in. */
  bucket: 'meshConfig' | 'simulationConfig'
  /**
   * The legacy editor's DOM ids for this row's inputs, so one selector
   * addresses both apps. `surfaceDomId` is a PREFIX: the min/max pair becomes
   * `<prefix>-0` / `<prefix>-1`, which is how the legacy panel names them.
   *
   * The floor row uses an `rx-` prefix because it has no legacy counterpart —
   * it is unreachable from the HTML UI (see the note on that row). Borrowing a
   * legacy id for it would make the sweep compare a React control against an
   * unrelated HTML one.
   */
  surfaceDomId?: string
  featureDomId?: string
}

export const REFINEMENT_ROWS: RefinementRow[] = [
  {
    id: 'room',
    labelKey: 'label.room_wall',
    surfaceKey: 'refinementLevel',
    featureKey: 'defaultFeatureLevel',
    bucket: 'meshConfig',
    surfaceDomId: 'ms-rl',
    featureDomId: 'ms-feat-default',
  },
  { id: 'rack', labelKey: 'label.rack_region', surfaceKey: null, featureKey: 'rackRegionLevel', bucket: 'meshConfig',
    featureDomId: 'ms-rack-region' },
  {
    id: 'acu',
    labelKey: 'label.acu',
    surfaceKey: 'acuRefinementLevel',
    featureKey: 'acuFeatureLevel',
    bucket: 'meshConfig',
    surfaceDomId: 'ms-acu',
    featureDomId: 'ms-feat-acu',
  },
  {
    id: 'box',
    labelKey: 'label.white_model',
    surfaceKey: 'boxRefinementLevel',
    featureKey: 'boxFeatureLevel',
    bucket: 'meshConfig',
    surfaceDomId: 'ms-box',
    featureDomId: 'ms-feat-box',
  },
  {
    id: 'partitionWall',
    labelKey: 'label.partition_wall_refine',
    surfaceKey: 'partitionWallRefinementLevel',
    featureKey: 'partitionWallFeatureLevel',
    bucket: 'simulationConfig',
    surfaceDomId: 'ms-pw',
    featureDomId: 'ms-feat-pw',
  },
  {
    id: 'baffle',
    labelKey: 'label.baffle_floor',
    surfaceKey: 'wallBafflesRefinementLevel',
    featureKey: 'baffleFeatureLevel',
    bucket: 'meshConfig',
    surfaceDomId: 'ms-bfl',
    featureDomId: 'ms-feat-baffle',
  },
  // Not in the legacy panel at all. `floorRefinementLevel` and
  // `floorFeatureLevel` are real MESH_KEYS with defaults [1,1] and 2, they are
  // written into the .cfdrun, and 21 of this repo's 22 case projects carry
  // them — but no control reaches them. The row labelled 挡板/地板 above writes
  // wallBaffles*/baffle*, not floor*, so the setting has been unreachable.
  {
    id: 'floor',
    labelKey: 'category.floor',
    surfaceKey: 'floorRefinementLevel',
    featureKey: 'floorFeatureLevel',
    bucket: 'meshConfig',
    surfaceDomId: 'rx-floor',
    featureDomId: 'rx-floor-feat',
  },
]
