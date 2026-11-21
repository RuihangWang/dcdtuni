/**
 * The .cfdrun V2 manifest, typed.
 *
 * A .cfdrun is a FROZEN snapshot of one CFD run: the STL artifacts, the runtime
 * inputs, the mesh and solver configuration, every patch's boundary binding, the
 * source terms and the post-processing config. The browser builds it from live
 * editor state at export / mesh / solve time, and the runner accepts nothing but
 * `schemaVersion: 2`.
 *
 * ## Why the vocabularies are imported, not redeclared
 *
 * `BC_TYPES` and `ROLES` exist in three places already — `js/cfdrun_schema.js`
 * (what the browser writes), `backend/aircooling/runner/cfdrun_schema.py` (what
 * the runner validates) and `fields.py` / `bridge_bc.py` (what actually gets
 * written into the OpenFOAM `0/` directory). A fourth hand-kept copy in
 * TypeScript would drift, and the way it fails is silent: a manifest that names
 * a bcType nobody implements meshes cleanly and then solves the wrong problem.
 *
 * So the values come from the shared module at runtime and the TYPES are derived
 * from them. `test/schema/cfdrun.test.ts` asserts the TS union and the JS set
 * are the same set, so adding a bcType in one place and not the other fails a
 * test instead of a run.
 *
 * ## What this file is NOT
 *
 * It is not a validator. `validateManifest` below is the shared module's own,
 * reached through a typed accessor, for the same reason: one implementation of
 * "is this manifest structurally sound", used by both apps.
 */

import '../../../shared/js/cfdrun_schema.js'

export const CFDRUN_SCHEMA_VERSION = 2

/**
 * The boundary-condition vocabulary the backend field generator implements.
 *
 * Mirrors `BC_TYPES` in js/cfdrun_schema.js. Do not add an entry here without
 * the matching change in fields.py and bridge_bc.py — the test will catch a
 * mismatch with the JS side, but nothing here can see the Python side.
 */
export type BcType =
  | 'wall'
  | 'acu_supply'
  | 'acu_return'
  | 'fan_inlet'
  | 'fan_outlet'
  | 'nonfan_fixed_temp'
  | 'nonfan_heat_flux'
  | 'fixedTemperature'
  | 'heat_source_flux'
  | 'perforated_baffle'
  | 'wall_baffle'
  | 'through_zone'

/** Patch-role vocabulary — domain semantics, NOT an OpenFOAM patchField. */
export type PatchRole =
  | 'acu_supply'
  | 'acu_return'
  | 'fan_inlet'
  | 'fan_outlet'
  | 'chip_heat_source'
  | 'cold_plate_sink'
  | 'rdhx_surface'
  | 'perforated_baffle'
  | 'wall'
  | 'fixed_temperature'
  | 'heat_source_flux'

/** Where a binding came from in the .cfdproj — `constructions.acus.m_201`. */
export type ObjectPath = string

export interface BoundaryBinding {
  patchName: string
  solidName: string
  objectPath: ObjectPath
  surfaceUid: string | null
  role: PatchRole | string
  bcType: BcType
  parameters: Record<string, unknown>
}

export interface PorousBinding {
  patchName: string
  objectPath: ObjectPath
  surfaceUid: string | null
  type: string
  parameters: Record<string, unknown>
}

export interface SolidEntry {
  solidName: string
  patchName: string
  /** Null until the exporter has written the file this solid lives in. */
  stlFileId: string | null
  objectPath: ObjectPath
  surfaceUid: string | null
  role: PatchRole | string
  areaM2: number | null
}

export interface GeometryExport {
  /** Always 'Y-up' and 'm' — the editor has no other convention. */
  coordinateSystem: string
  unit: string
  stlFiles: Record<string, unknown>
  solids: Record<string, SolidEntry>
  /** Optional provenance for the exact source STL region before mesh merging. */
  /** Actual emitted region/facet ancestry. It makes no OO physical-face claim. */
  sourceRegions?: Record<string,{
    stlFileId:string;solidName:string;sourceEntityUid:string;sourceRegionUid:string
    sourceFacetCount:number;sourceRegionSha256:string;operation:string
  }>
  sourceSurfaces?: Record<string,{
    stlFileId:string;solidName:string;sourceObjectUid:string;sourceFaceUid:string
    representation:'zeroThicknessSheet'|'surfaceFace'
  }>
}

export interface RunInputs {
  racks: Record<string, unknown>
  acus: Record<string, unknown>
  servers: Record<string, unknown>
  cdus: Record<string, unknown>
  partitionWalls: Record<string, unknown>
  pipeSegments: Record<string, unknown>
  liquid: { enabled: boolean; inheritResults: boolean; [k: string]: unknown }
  labels: { sensorMeasurements: Record<string, unknown>; [k: string]: unknown }
}

/** Identifies the .cfdproj this run was frozen from; null for a synthetic run. */
export interface SourceProject {
  uid?: string
  schemaVersion: number
  projectName?: string
  filePath?: string
  contentHash: string
  [k: string]: unknown
}

export interface RunArtifacts {
  caseDir: string
  modelDir: string
  triSurfaceDir: string
  openfoamCaseDir: string
  liquidResultsPath: string | null
  logs: Record<string, string>
}

export interface PostprocessConfig {
  probes: Record<string, unknown>
  surfaceMonitors: Record<string, unknown>
  outputFields: string[]
  labels: Record<string, unknown>
}

/**
 * The manifest.
 *
 * `meshGeometry` is deliberately loose and deliberately present: it is the
 * transitional v1 blob the runner still reads geometry through
 * (`stlgen_runner.py` `_load_cfd_json_only`), and it is NOT parity with the root
 * `simulationConfig` / `meshConfig` — the derived blob carries nested
 * `gravity {x,y,z}` where the root carries flat `gravityX/Y/Z`. Anything reading
 * it must say which of the two it means. See docs/reference/cfdrun-v2-schema.md §14.
 */
export interface CfdrunV2 {
  /** 2, 3 (v2 + transient block and schedules), 4 (one simulationConfig: the root). */
  schemaVersion: 2 | 3 | 4
  runId: string
  name: string
  sourceProject: SourceProject | null
  inputs: RunInputs
  geometryExport: GeometryExport
  boundaryBindings: Record<string, BoundaryBinding>
  sourceTerms: Record<string, unknown>
  porousBindings: Record<string, PorousBinding>
  meshConfig: Record<string, unknown>
  simulationConfig: Record<string, unknown>
  postprocessConfig: PostprocessConfig
  artifacts: RunArtifacts
  /** The transitional v1 blob — see the note above before reading it. */
  meshGeometry?: Record<string, unknown>
}

/** The 13 keys `validateManifest` requires. `meshGeometry` is not among them. */
export const CFDRUN_REQUIRED_KEYS: readonly (keyof CfdrunV2)[] = [
  'schemaVersion',
  'runId',
  'name',
  'sourceProject',
  'inputs',
  'geometryExport',
  'boundaryBindings',
  'sourceTerms',
  'porousBindings',
  'meshConfig',
  'simulationConfig',
  'postprocessConfig',
  'artifacts',
]

interface CfdrunSchemaModule {
  CFDRUN_SCHEMA_VERSION: number
  BC_TYPES: Set<string>
  ROLES: Set<string>
  emptyArtifacts(): RunArtifacts
  emptyManifest(): CfdrunV2
  validateManifest(json: unknown): string[]
}

/**
 * The shared schema module, or a throw naming what did not load.
 *
 * Same contract as `legacy/cfdprojModules.ts`: the .js files attach to `window`
 * on import, so a missing script has to fail here with a sentence rather than as
 * `undefined is not a function` inside an export.
 */
export function cfdrunSchema(): CfdrunSchemaModule {
  const mod = (window as unknown as { cfdrunSchema?: CfdrunSchemaModule }).cfdrunSchema
  if (!mod) {
    throw new Error(
      'window.cfdrunSchema is missing — js/cfdrun_schema.js did not load. It attaches to ' +
        'window on import; check the import at the top of schema/cfdrun.ts.',
    )
  }
  return mod
}

/** An empty V2 manifest with all 13 keys — the shared module's own skeleton. */
export function emptyManifest(): CfdrunV2 {
  return cfdrunSchema().emptyManifest()
}

/** Structural errors in a manifest; an empty array means it is sound. */
export function validateManifest(json: unknown): string[] {
  return cfdrunSchema().validateManifest(json)
}
