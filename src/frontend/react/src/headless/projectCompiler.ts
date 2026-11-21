/**
 * Headless project compiler: the browser run path without a browser.
 *
 * `headless_compiler.mjs` (next to this file) bundles it and evaluates
 * it inside a JSDOM window, so the shared UMD format modules attach to `window`
 * exactly as they do under Vite. Every step below calls the module the React
 * app itself calls when the user presses Mesh/Solve (`run/runSubmit.ts`,
 * `run/runPayload.ts`) or starts a DAE liquid run (`liquid/liquidExperiment.ts`).
 * Nothing here re-implements geometry, STL naming, boundary bindings or the
 * liquid graph; if the app's export changes, this compiler changes with it.
 */
import * as THREE from 'three'
import '../legacy/geometryBuilder'
import * as store from '../projectStore'
import { stateV4 } from '../legacy/cfdprojModules'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../interaction/sceneBuilder'
import {
  assertGeometryTopologyAirExportReady,
  bindStlFileHashes,
  buildManifestV2,
  createCfdrunBuild,
  type CfdrunContext,
} from '../legacy/cfdrunBuild'
import { LENGTH_UNITS } from '../lengthUnits'
import { MESH_MODES } from '../run/meshConfig'
import { DomainProject } from '../domain/DomainProject'
import { buildStlFiles, duplicateSolidNames } from '../io/stlExport'
import { cfdrunFilename, duplicateSolidMessage } from '../run/runPayload'
import { compileSavedExperiment } from '../liquid/liquidExperiment'
import { hasHybridFanServer } from '../liquid/hybridFanHeatBudget'
import { runPreflightChecks } from '../run/meshPreflight'
import { prepareLiquidTransport } from '../run/liquidTransportWorker'
import type { LiquidTransportInput, LiquidTransportProduct } from '../run/liquidTransportProtocol'
import { modelPoseKernel } from '../domain/modelPoseKernel'
import { setSimulationMode } from '../transient/simulationMode'
import {
  defaultMonitoringConfig,
  getMonitoringConfig,
  installMonitoringBridge,
  resolveMonitoringRequest,
  setMonitoringConfig,
  type MonitoringRequest,
} from '../run/monitoring'
import { domainModesOf, transientOf } from '../transient/model'
import { applyTimelineOp, lastTimelineRefusal, setDuration } from '../transient/trackEdits'
import { cfdrunExport } from '../legacy/cfdrunBuild'
import type { CfdProject, Construction } from '../schema/cfdproj'

/**
 * Edits applied to the loaded project before a compile, each through the
 * editor's own writer, so every operation of one run (air case, liquid run,
 * hybrid project) sees the same document and binds the same content hash.
 */
export interface ProjectOptions {
  /**
   * The air steady/transient choice, applied the way the editor's mode switch
   * applies it (`setSimulationMode(mode, 'air')`): `transient.airMode`, the
   * derived `transient.mode`, `simulationConfig.solverType` and, for a
   * transient run, `simulationConfig.endTimeSeconds` change together.
   */
  solverType?: 'steady' | 'transient'
  /**
   * The liquid steady/transient choice (`setSimulationMode(mode, 'liquid')`):
   * `transient.liquidMode`, the derived `transient.mode` and the DAE's
   * `analysis.mode` change together.
   */
  liquidMode?: 'steady' | 'transient'
  /**
   * The timeline length (s) a transient run follows, set as the timeline strip
   * sets it (`applyTimelineOp('setDuration', ...)`, no trim: a keyframe past
   * the new end is a refusal, never silently dropped).
   */
  durationS?: number
  /**
   * The 求解监控 panel's ticks (run/monitoring.ts `MonitoringRequest`), air
   * case only: residuals, server and ACU return patches by name, `'all'` for
   * the ACU list's select-all, and `meshPatches`, the case mesh's patch names
   * the panel's lists come from. Not a document edit: the export reads the
   * panel through `window.smGetMonitoringConfig`, never from the project.
   * Without it the export carries a freshly opened editor's panel.
   */
  monitoring?: MonitoringRequest
}

/** Kept for callers of the air export; the air case reads `solverType` and `monitoring`. */
export type AirExportOptions = ProjectOptions

export interface AirExportResult {
  /** The steady/transient state of the project as exported (after `options.solverType`). */
  simulationMode: { mode: string; airMode: string; liquidMode: string; durationS: number; solverType: string | null }
  /** The editor's mesh pre-flight findings (run/meshPreflight.ts), as data. */
  preflight: Record<string, unknown[]>
  /** Non-empty when the editor would stop before uploading (every pre-flight dialog except seed collisions). */
  preflightStops: string[]
  /** Hybrid fan servers need a completed liquid session before an air solve; the API cannot provide one. */
  hybrid: { hasHybridFanServer: boolean; budgetVersion: number | null }
  /** `<case>/model/<name>.cfdrun` file name the app would use. */
  cfdrunFilename: string
  /** Exactly `JSON.stringify(manifest)`, the string `/api/run-mesh` receives. */
  cfdrunJson: string
  /** filename -> ASCII STL text (not base64). */
  stlFiles: Record<string, string>
  manifestErrors: unknown[]
  modelCount: number
}

export interface LiquidExportResult {
  liquidrun: Record<string, unknown>
  /** Liquid PID controllers; a DAE (v3/v4) input always has none. */
  controllers: unknown[]
  /** The steady/transient state of the project as compiled (after the options). */
  simulationMode: { mode: string; airMode: string; liquidMode: string; durationS: number }
}

export interface HybridProjectResult {
  /**
   * `cfdrunExport().canonicalProjectForHash(project)` of the loaded project: the
   * `hybridHeat.project` the editor's solve request carries (run/runSubmit.ts).
   */
  project: Record<string, unknown>
  /** Its content hash, the one the air manifest and the liquid run both bind. */
  contentHash: string
  /** The editor's hybrid gates (run/runSubmit.ts): a v2 fan budget and inherited liquid results. */
  hasHybridFanServer: boolean
  budgetVersion: number | null
  inheritResults: boolean
}

type Globals = {
  allModels?: () => Construction[]
  _meshExportGroupedMap?: Record<string, string> | null
  cfdrunSchema?: { validateManifest(manifest: unknown): unknown[] }
  ThreeCSG?: { CSG: unknown }
  geometryScene?: { buildLiquidTransportDetached(input: LiquidTransportInput, three: typeof THREE, csg: unknown,
    portFrame: (model: LiquidTransportInput['models'][number], port: string, end: string) => unknown): LiquidTransportProduct | null }
}

/**
 * In-process stand-in for `run/liquidTransport.worker.ts`: the same
 * `geometryScene.buildLiquidTransportDetached` call with the same vendored CSG
 * engine and port frames, answered through the Worker message protocol that
 * `prepareLiquidTransport` already speaks. JSDOM has no module Worker.
 */
function inProcessLiquidTransportWorker(): Worker {
  const worker = {
    onmessage: null as ((event: { data: unknown }) => void) | null,
    onerror: null as unknown,
    onmessageerror: null as unknown,
    terminate() {},
    postMessage(input: LiquidTransportInput) {
      queueMicrotask(() => {
        let reply: unknown
        try {
          const globals = window as unknown as Globals
          if (!globals.geometryScene || !globals.ThreeCSG) throw new Error('geometry scene or CSG engine is not loaded')
          const product = globals.geometryScene.buildLiquidTransportDetached(input, THREE, globals.ThreeCSG.CSG,
            (model, port, end) => modelPoseKernel.equipmentPortFrame(
              model as unknown as Record<string, unknown>, model.kind as string, port, end))
          reply = { ok: true, product }
        } catch (error) {
          reply = { ok: false, error: error instanceof Error ? error.message : String(error) }
        }
        worker.onmessage?.({ data: reply })
      })
    },
  }
  return worker as unknown as Worker
}

/** Load as the editor does: revive kinds, bring to v5 at t = 0, run load-time migrations. */
export function load(raw: unknown, projectPath: string | null): CfdProject {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Expected a saved .cfdproj object')
  store.loadProject(JSON.parse(JSON.stringify(raw)) as CfdProject, projectPath)
  const project = store.getProject()
  if (!project) throw new Error('The project store rejected the .cfdproj')
  return project
}

const MODES = ['steady', 'transient']

/**
 * Load, then apply `options` through the editor's writers: the air mode, the
 * liquid mode (both `setSimulationMode`), then the timeline duration. Each is
 * one store edit, as a click would make it, and only when it changes
 * something: a user does not click a switch that is already where they want
 * it, and a redundant `setSimulationMode` still rewrites `solverType` and the
 * stop time, which would change the document's content hash. A refusal is an
 * error.
 */
function prepare(raw: unknown, projectPath: string | null, options: ProjectOptions = {}): CfdProject {
  load(raw, projectPath)
  for (const [key, domain] of [['solverType', 'air'], ['liquidMode', 'liquid']] as const) {
    const mode = options[key]
    if (mode === undefined) continue
    if (!MODES.includes(mode)) throw new Error(`${key} must be steady or transient, got ${String(mode)}`)
    // Already there -- unless the air mode's mirror disagrees (a hand-edited
    // file with transient.airMode set but simulationConfig.solverType not):
    // the manifest follows solverType, so skipping would export the other mode.
    const mirrored = domain !== 'air' || (store.getProject() as { runDefaults?: { simulationConfig?: { solverType?: string } } } | null)
      ?.runDefaults?.simulationConfig?.solverType === mode
    if (domainModesOf(store.getProject())[domain] === mode && mirrored) continue
    if (!setSimulationMode(mode, domain)) throw new Error(`The project refused the ${domain} ${mode} mode`)
  }
  if (options.durationS !== undefined) {
    const duration = Number(options.durationS)
    if (!Number.isFinite(duration) || duration <= 0) throw new Error(`durationS must be a positive number, got ${String(options.durationS)}`)
    if (transientOf(store.getProject()).durationS !== duration &&
        !applyTimelineOp('setDuration', setDuration(duration, { trim: false }), `${duration} s`)) {
      const reason = lastTimelineRefusal()
      throw new Error(`The timeline refused a ${duration} s duration: ${reason === 'trim'
        ? 'keyframes lie past the new end (the editor would ask to trim them)' : reason ?? 'unknown reason'}`)
    }
  }
  const project = store.getProject()
  if (!project) throw new Error('The project store lost the project')
  return project
}

/** The scene the viewport would build, without a renderer (webglAvailable: false). */
async function withScene<T>(project: CfdProject, use: (builder: ReturnType<typeof createSceneBuilder>) => Promise<T>): Promise<T> {
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  const root = new THREE.Group()
  root.add(wrapperGroup)
  wrapperGroup.add(globalGroup)
  const host: SceneHost = {
    globalGroup, wrapperGroup, registries: emptyRegistries(),
    selectedUid: () => null, project: () => project, webglAvailable: false,
  } as SceneHost
  const globals = window as unknown as Globals
  const previous = globals.allModels
  globals.allModels = () => stateV4().allModels(project) as Construction[]
  const builder = createSceneBuilder(host)
  try {
    builder.rebuild()
    root.updateMatrixWorld(true)
    // Awaited inside the try: the app's allModels global must outlive the async export.
    return await use(builder)
  } finally {
    builder.clear()
    if (previous) globals.allModels = previous
    else delete globals.allModels
  }
}

/**
 * The mesh-run body of `buildRunRequest('mesh')` (run/runSubmit.ts +
 * run/runPayload.ts): pre-flight findings, duplicate-solid refusal, liquid
 * transport preparation, manifest first, then the cached grouped STL map, then
 * the hash binding. The pre-flight dialogs are reported, not shown; the caller
 * decides (the API refuses whatever the editor would stop on).
 */
export async function exportAirCase(raw: unknown, projectPath: string | null, timestamp: string,
  options: AirExportOptions = {}): Promise<AirExportResult> {
  const project = prepare(raw, projectPath, options)
  const block = transientOf(project)
  const modes = domainModesOf(project)
  const simulationMode = {
    mode: block.mode, airMode: modes.air, liquidMode: modes.liquid, durationS: block.durationS,
    solverType: ((project.runDefaults as { simulationConfig?: { solverType?: string } } | undefined)
      ?.simulationConfig?.solverType) ?? null,
  }
  const preflight = runPreflightChecks(project) as unknown as Record<string, unknown[]>
  const preflightStops = ['digitNames', 'floorHeights', 'roomWallGaps', 'doorPorosity']
    .filter(name => (preflight[name] ?? []).length > 0)
  assertGeometryTopologyAirExportReady(project)
  const uids = new DomainProject(project).structuralForAirExport().map(entity => entity.uid)
  if (uids.length === 0) throw new Error('The project has no geometry to export for an air run')
  const budget = (project.runDefaults as { hybridHeatBudget?: { version?: number } } | undefined)?.hybridHeatBudget
  const hybrid = { hasHybridFanServer: hasHybridFanServer(project), budgetVersion: budget?.version ?? null }
  return await withScene(project, async builder => {
    const duplicates = duplicateSolidNames(builder, project, uids)
    if (duplicates.length) throw new Error(duplicateSolidMessage(duplicates))
    // sceneRunDeps.buildManifest: the render roots' parent, its world matrices flushed.
    const globalGroup = builder.registries.renderRoots[0]?.parent ?? null
    if (globalGroup) {
      let root: THREE.Object3D = globalGroup
      while (root.parent) root = root.parent
      root.updateMatrixWorld(true)
    }
    const allIds = (stateV4().allModels(project) as { uid: string }[]).map(model => model.uid)
    const prepared = await prepareLiquidTransport(builder.api.captureLiquidTransportInput(allIds), undefined,
      inProcessLiquidTransportWorker)
    let groupedError: unknown
    const api = createCfdrunBuild({
      state: project,
      globalGroup,
      LENGTH_UNITS,
      _MESH_MODES: MESH_MODES,
      buildGroupedStlMap: (modelIds: string[], leakage: boolean) => {
        try { return builder.api.buildGroupedStlMap(modelIds, leakage, prepared) }
        catch (error) { groupedError = error; throw error }
      },
    } as unknown as CfdrunContext)
    // App.tsx installs this bridge once; the export reads the panel through it
    // (cfdrun_build.js), so without it a script's manifest asked for no probes.
    installMonitoringBridge()
    setMonitoringConfig(defaultMonitoringConfig())
    let manifest
    try {
      manifest = await buildManifestV2(api, project, timestamp)
      if (groupedError) throw groupedError
    } catch (error) {
      throw groupedError || error
    }
    if (options.monitoring) {
      // The panel's lists are built from this manifest's bindings (the server
      // reads them from the exported .cfdrun: /api/server-patches), so the
      // ticks resolve once it exists. buildManifestV2 put the bridge's answer
      // in exactly one place, root simulationConfig.monitoring; the panel's
      // answer after the ticks replaces it there, key order unchanged.
      setMonitoringConfig(resolveMonitoringRequest(options.monitoring, manifest))
      ;(manifest.simulationConfig as Record<string, unknown>).monitoring = getMonitoringConfig()
    }
    // takeGroupedStl: reuse and clear the map the manifest build cached.
    const globals = window as unknown as Globals
    const cached = globals._meshExportGroupedMap
    globals._meshExportGroupedMap = null
    const files = cached && Object.keys(cached).length ? cached : buildStlFiles(builder, project, true).files
    bindStlFileHashes(manifest, files, project)
    const manifestErrors = globals.cfdrunSchema ? globals.cfdrunSchema.validateManifest(manifest) : ['cfdrunSchema module is not loaded']
    return {
      simulationMode,
      preflight: JSON.parse(JSON.stringify(preflight)),
      preflightStops,
      hybrid,
      cfdrunFilename: cfdrunFilename(project, projectPath),
      cfdrunJson: JSON.stringify(manifest),
      stlFiles: files,
      manifestErrors,
      modelCount: uids.length,
    }
  })
}

/** The `.liquidrun` (v2 graph, or v3/v4 DAE) the liquid panel would submit for this project. */
export function exportLiquidrun(raw: unknown, projectPath: string | null,
  options: ProjectOptions = {}): LiquidExportResult {
  const project = prepare(raw, projectPath, options)
  const block = transientOf(project)
  const modes = domainModesOf(project)
  const compiled = compileSavedExperiment(project) as { liquidrun: Record<string, unknown>; controllers: unknown[] }
  return {
    liquidrun: JSON.parse(JSON.stringify(compiled.liquidrun)),
    controllers: JSON.parse(JSON.stringify(compiled.controllers)),
    simulationMode: { mode: block.mode, airMode: modes.air, liquidMode: modes.liquid, durationS: block.durationS },
  }
}

/**
 * The `hybridHeat.project` of the editor's hybrid solve request for this saved
 * project: `buildRunRequest` sends `canonicalProjectForHash` of the live
 * project, and the server checks its content hash against the air manifest's
 * `sourceProject` and the liquid run's `source_project` (all three must come
 * from the same document).
 */
export function hybridProject(raw: unknown, projectPath: string | null,
  options: ProjectOptions = {}): HybridProjectResult {
  const project = prepare(raw, projectPath, options)
  const exporter = cfdrunExport()
  const runDefaults = (project.runDefaults ?? {}) as {
    hybridHeatBudget?: { version?: number }; liquid?: { inheritResults?: boolean }
  }
  return {
    project: JSON.parse(JSON.stringify(exporter.canonicalProjectForHash(project))),
    contentHash: exporter.projectContentHashSync(project),
    hasHybridFanServer: hasHybridFanServer(project),
    budgetVersion: runDefaults.hybridHeatBudget?.version ?? null,
    inheritResults: runDefaults.liquid?.inheritResults === true,
  }
}
