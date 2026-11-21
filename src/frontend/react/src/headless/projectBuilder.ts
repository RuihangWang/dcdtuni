/**
 * Headless modelling: the automation contract without a browser.
 *
 * `headless_compiler.mjs` bundles this file (operation `build-project`) and
 * evaluates it inside a JSDOM window. It drives `window.__dcdtuni` -- the SAME
 * contract `/api/browser/exec-js` and `/dc-builder` drive in the editor
 * (automation/contract.ts) -- so every edit goes through `projectStore.edit`
 * and the panels' own writers, and the result is the bytes the save button
 * writes (`serializeProjectState`).
 *
 * Only the verbs that edit or read the DOCUMENT are open (BUILD_VERBS). The
 * rest need what a headless run does not have: the 3D scene
 * (`buildGeometryData`, `buildGroupedStlMap`, `geometryStatus`,
 * `buildCfdrunJsonV2`), the view or the selection (`applyViewPreset`,
 * `select`), or a server (`runMeshGeneration`, `runSolveOnly`,
 * `runLiquidSolve`, `setCaseDir`, ...). History, subscriptions and the playhead
 * stay closed too: a build edits the start of the timeline, never t > 0.
 *
 * A step is `{verb, args}`. A string argument that is exactly `"$N"` is the
 * value step N returned (an earlier step only) -- a create's uid, `addPatch`'s
 * patch id, `addRackServerAtRange`'s server uid; `"$$..."` is the literal
 * string with one `$` removed. A step fails when the verb throws, returns
 * `false`/`null` or `{error}` (createRackEquipmentAtSlot: anything but `'ok'`),
 * or when the store refused its edit; the build stops there and nothing is
 * saved. After the last step the saved bytes are run through the editor's air
 * export (`exportAirCase`) as a self-check, whose findings are reported.
 */
import { automation, type Automation } from '../automation/contract'
import * as store from '../projectStore'
import { serializeProjectState } from '../io/projectFiles'
import { exportAirCase, load } from './projectCompiler'

/** The document verbs a build may call (typed against the contract, so a renamed verb fails the build). */
export const BUILD_VERBS = [
  // creation
  'newProject', 'createDefaultRackModel', 'createDefaultAcuModel', 'createDefaultWallModel',
  'createDefaultBaffleModel', 'createDefaultFloorModel', 'createDefaultBaseModel', 'createDefaultCduModel',
  'createDefaultPipeModel', 'addModel', 'duplicateModel', 'duplicateModels', 'removeModel', 'removeModels',
  // air pipes
  'setAirPipeWorldPoints', 'setAirPipeDiameter', 'setAirPipeShape', 'setAirPipeSize', 'joinAirPipes',
  'airPipeWorldPoints',
  // placement, room and project
  'placeModel', 'renameModel', 'setEnabled', 'setEnabledMany', 'setGeometryComponent', 'setModelLocation',
  'setRotationDegrees', 'setRoomPolygon', 'applyRoom', 'replaceRoom', 'setProjectMeta', 'setProjectDescription', 'setInputUnit',
  'setProjectBound', 'setFluid', 'setSimulationMode',
  // patches
  'addPatch', 'deletePatch', 'fillFacePatch', 'setPatchGeometry', 'updateAcuPatchPhysics',
  // rack interior and cooling
  'createRackEquipmentAtSlot', 'updateRackServerCfd', 'rackServers', 'setRackServersOpacity', 'setServerOpacity',
  'addRackServerAtRange', 'addServerPatch', 'rackServerDepths', 'setRackLayout', 'setRackHeightMode', 'setRackDoorOffset', 'setModelCooling', 'setCduLiquidParam', 'setCduServedRack',
  // liquid routes and pump
  'setPipeSegmentPoints', 'setCduPumpSpeed', 'setPipeDiameter', 'setValveOpening',
  // run configuration
  'setSolverType', 'setSolverConfig', 'setMeshConfig', 'setRunConfig', 'setNestedRunConfig', 'setLocationInMesh',
  'addExtraLocation', 'removeExtraLocation', 'setExtraLocation', 'addRefinementZone', 'setRefinementZoneField',
  'removeRefinementZone', 'setRefinementBound', 'addSensorProbe', 'setSensorProbe', 'removeSensorProbe',
  'addSensorRecord', 'updateSensorRecord', 'deleteSensorRecord',
  // read-backs
  'allModels', 'getProject', 'saveProjectJson', 'getCfdrunFilename', 'equipmentPortFrames', 'modelBounds',
] as const satisfies readonly (keyof Automation)[]

/**
 * Every key of `window.__dcdtuni` with its runtime type, and `BUILD_VERBS`.
 *
 * Operation `contract-verbs`: `agent.scripted_api.contract_verbs` generates the verb
 * table the skills carry from the SOURCE of contract.ts; its test compares that
 * parse with this live list, so a verb the parser misses cannot drop out of the
 * table unnoticed.
 */
export function contractVerbs(): { verbs: { name: string; type: string }[]; buildVerbs: string[] } {
  const surface = automation as unknown as Record<string, unknown>
  return {
    verbs: Object.keys(surface).sort().map((name) => ({ name, type: typeof surface[name] })),
    buildVerbs: [...BUILD_VERBS],
  }
}

export interface BuildStep { verb: string; args?: unknown[] }

export interface BuildStepRecord { index: number; verb: string; args: unknown[]; result: unknown }

export interface BuildSelfCheck {
  /** No schema error, no pre-flight stop and no export refusal. Seed collisions are reported, not a stop. */
  ok: boolean
  error?: string
  manifestErrors?: unknown[]
  preflightStops?: string[]
  preflight?: Record<string, unknown[]>
  seedCollisions?: unknown[]
  stlFileCount?: number
  modelCount?: number
  boundaryRoles?: string[]
  cfdrunFilename?: string
  hybrid?: { hasHybridFanServer: boolean; budgetVersion: number | null }
}

export interface BuildResult {
  ok: boolean
  failure?: { step: number; verb: string; reason: string }
  steps: BuildStepRecord[]
  /** The saved bytes (two-space JSON, the save button's whitelist). Absent when a step failed. */
  cfdproj?: string
  modelCount?: number
  selfCheck?: BuildSelfCheck
}

const OPEN = new Set<string>(BUILD_VERBS)
const REF = /^\$(\d+)$/

class StepError extends Error {}

function resolveRefs(value: unknown, records: BuildStepRecord[], index: number): unknown {
  if (typeof value === 'string') {
    if (value.startsWith('$$')) return value.slice(1)
    const match = REF.exec(value)
    if (!match) return value
    const target = Number(match[1])
    if (target >= index) throw new StepError(`${value} refers to step ${target}, which has not run before step ${index}`)
    const result = records[target]?.result
    if (result === null || result === undefined) throw new StepError(`${value}: step ${target} returned nothing to refer to`)
    return result
  }
  if (Array.isArray(value)) return value.map(item => resolveRefs(item, records, index))
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolveRefs(item, records, index)]))
  }
  return value
}

function refusal(verb: string, result: unknown): string | null {
  if (verb === 'createRackEquipmentAtSlot') return result === 'ok' ? null : `returned ${JSON.stringify(result)}`
  if (result === false || result === null) return `returned ${String(result)}`
  if (result && typeof result === 'object' && !Array.isArray(result) && typeof (result as { error?: unknown }).error === 'string') {
    return (result as { error: string }).error
  }
  return null
}

const jsonable = (value: unknown): unknown => (value === undefined ? null : JSON.parse(JSON.stringify(value)))

/**
 * Run `steps` against `base` (a saved .cfdproj object) or, when it is null, a
 * fresh `newProject()`, then serialise and self-check. Never throws for a step:
 * a failed step is reported with its index, verb and reason.
 */
export async function buildProject(steps: unknown, base: unknown, projectPath: string | null,
  timestamp: string): Promise<BuildResult> {
  if (!Array.isArray(steps)) throw new Error('build-project needs a list of steps')
  if (base !== null && base !== undefined) load(base, projectPath)
  else automation.newProject()
  const records: BuildStepRecord[] = []
  const verbs = automation as unknown as Record<string, (...args: unknown[]) => unknown>
  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index] as BuildStep
    const verb = step && typeof step === 'object' ? step.verb : undefined
    const fail = (reason: string): BuildResult => ({ ok: false, failure: { step: index, verb: String(verb), reason }, steps: records })
    if (typeof verb !== 'string' || !OPEN.has(verb)) {
      return fail(`${JSON.stringify(verb)} is not a build verb (scene, view, selection, server and history verbs ` +
        `are closed headless); open verbs: ${BUILD_VERBS.join(', ')}`)
    }
    if (step.args !== undefined && !Array.isArray(step.args)) return fail('args must be a list')
    let args: unknown[]
    try {
      args = resolveRefs(step.args ?? [], records, index) as unknown[]
    } catch (error) {
      return fail(error instanceof Error ? error.message : String(error))
    }
    const before = store.rejectionSeq()
    let result: unknown
    try {
      result = await verbs[verb]!(...args)
    } catch (error) {
      return fail(`threw: ${error instanceof Error ? error.message : String(error)}`)
    }
    if (store.rejectionSeq() !== before) {
      const why = store.lastEditRejection()
      return fail(`refused: the store rejected the edit${why ? ` (${JSON.stringify(why)})` : ''}`)
    }
    const why = refusal(verb, result)
    if (why) return fail(`refused: ${verb} ${why}`)
    records.push({ index, verb, args: jsonable(args) as unknown[], result: jsonable(result) })
  }
  const project = store.getProject()
  if (!project) throw new Error('the build left no project')
  const cfdproj = serializeProjectState(project)
  const modelCount = automation.allModels().length
  let selfCheck: BuildSelfCheck
  try {
    const exported = await exportAirCase(JSON.parse(cfdproj), projectPath, timestamp)
    const manifest = JSON.parse(exported.cfdrunJson) as { boundaryBindings?: Record<string, { bcType?: string }> }
    const roles = new Set(Object.values(manifest.boundaryBindings ?? {}).map(row => String(row.bcType)))
    const manifestErrors = exported.manifestErrors ?? []
    selfCheck = {
      ok: manifestErrors.length === 0 && exported.preflightStops.length === 0,
      manifestErrors,
      preflightStops: exported.preflightStops,
      preflight: exported.preflight,
      seedCollisions: exported.preflight.seedCollisions ?? [],
      stlFileCount: Object.keys(exported.stlFiles).length,
      modelCount: exported.modelCount,
      boundaryRoles: [...roles].sort(),
      cfdrunFilename: exported.cfdrunFilename,
      hybrid: exported.hybrid,
    }
  } catch (error) {
    selfCheck = { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
  return { ok: true, steps: records, cfdproj, modelCount, selfCheck }
}
