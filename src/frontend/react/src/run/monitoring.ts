/**
 * What the solver should probe, for the .cfdrun.
 *
 * `simulationConfig.monitoring` becomes OpenFOAM function objects, so an empty
 * config is a case that writes no probe data and a convergence plot with
 * nothing in it. The legacy editor reads the checkboxes out of the DOM at
 * export time (`smGetMonitoringConfig`); this holds the same answer in a module
 * so the export does not depend on which tab is mounted.
 *
 * The default is residuals on, no server probes and EVERY ACU return
 * (`acuReturnTemperature: 'all'`, owner decision 2026-10-22, the scripted API's
 * default too). The ACU return list matters beyond the plot: the runner judges
 * a steady run CONVERGED on the return-air temperatures of exactly these
 * patches (convergence.py), so a manifest without them can only be
 * INCONCLUSIVE. `'all'` stays symbolic in the editor and is resolved by the
 * export against the manifest it builds (shared/js/cfdrun_build.js
 * `resolveMonitoring`), so an ACU added later is monitored too.
 */
import '../../../shared/js/cfdrun_build.js'

export interface MonitorPatch {
  patch: string
  shortId: string
  type: string
}

export interface MonitoringConfig {
  residuals: boolean
  serverTemperature: MonitorPatch[]
  /** `'all'`: every ACU return the exported manifest binds. */
  acuReturnTemperature: MonitorPatch[] | 'all'
}

/** A freshly opened editor's panel: residuals on, every ACU return, no server probes. */
export function defaultMonitoringConfig(): MonitoringConfig {
  return { residuals: true, serverTemperature: [], acuReturnTemperature: 'all' }
}

interface SharedMonitoring {
  boundAcuReturnPatches(manifest: unknown, meshPatches?: readonly string[] | null): MonitorPatch[]
  resolveMonitoring<T>(monitoring: T, manifest: unknown, meshPatches?: readonly string[] | null): T
}

function shared(): SharedMonitoring {
  const api = (window as unknown as { cfdrunBuild?: SharedMonitoring }).cfdrunBuild
  if (!api?.resolveMonitoring) throw new Error('shared/js/cfdrun_build.js is not loaded')
  return api
}

/** `monitoring` with an `'all'` ACU list replaced by the returns `manifest` binds (the export's step). */
export function resolveMonitoring<T>(monitoring: T, manifest: unknown, meshPatches?: readonly string[] | null): T {
  return shared().resolveMonitoring(monitoring, manifest, meshPatches)
}

let config: MonitoringConfig = defaultMonitoringConfig()

export function getMonitoringConfig(): MonitoringConfig {
  return config
}

export function setMonitoringConfig(next: MonitoringConfig) {
  config = next
}

/**
 * Publish it where `js/cfdrun_build.js` looks.
 *
 * That module reads `window.smGetMonitoringConfig` and falls back to `{}` — a
 * silent fallback, which is exactly how React shipped a manifest asking for no
 * probes at all while the legacy one asked for residuals. The headless
 * compiler installs the same bridge, so a script's export asks for what the
 * editor's does.
 */
export function installMonitoringBridge() {
  ;(window as unknown as { smGetMonitoringConfig?: () => MonitoringConfig }).smGetMonitoringConfig =
    getMonitoringConfig
}

type Row = Record<string, unknown>
const isRow = (value: unknown): value is Row => !!value && typeof value === 'object' && !Array.isArray(value)

/**
 * The ACU returns the panel offers for an exported manifest: what
 * `/api/server-patches` answers (server.py `handle_server_patches`).
 *
 * The bindings whose role or bcType is `acu_return`, in binding order, each
 * patch once, labelled by the ACU whose `returnFaces` hold the solid (else the
 * binding's object uid, else the patch name). The server also keeps only the
 * patches the case's mesh has; `meshPatches` applies that filter when the
 * caller knows the mesh, and `null` offers every bound return (no mesh yet).
 * The rule lives once, in shared/js/cfdrun_build.js, which both editors' export uses.
 */
export function boundAcuReturnPatches(manifest: unknown, meshPatches?: readonly string[] | null): MonitorPatch[] {
  return shared().boundAcuReturnPatches(manifest, meshPatches ?? null)
}

/**
 * A script's request for the panel (the headless compiler's
 * `options.monitoring`): what to tick, as names or the panel's own rows.
 *
 * `acuReturnTemperature: 'all'` is the ACU list's 全选 button. `meshPatches`
 * are the patch names of the case's mesh, which the panel's lists come from;
 * leave it out when there is no mesh yet.
 */
export interface MonitoringRequest {
  residuals?: boolean
  serverTemperature?: ReadonlyArray<string | MonitorPatch>
  acuReturnTemperature?: 'all' | ReadonlyArray<string | MonitorPatch>
  meshPatches?: readonly string[] | null
}

const REQUEST_KEYS = ['residuals', 'serverTemperature', 'acuReturnTemperature', 'meshPatches']

function patchName(entry: unknown, list: string): string {
  const name = typeof entry === 'string' ? entry : isRow(entry) ? entry.patch : undefined
  if (typeof name !== 'string' || !name) {
    throw new Error(`monitoring.${list} entries are patch names or {patch, shortId, type} rows, got ${JSON.stringify(entry)}`)
  }
  return name
}

/** A server inlet/outlet patch as the server lists it: the role token dropped from the name. */
function serverPatch(name: string): MonitorPatch | null {
  const low = name.toLowerCase()
  const inlet = low.includes('_inlet')
  if (!inlet && !low.includes('_outlet')) return null
  return { patch: name, shortId: name.replace(/_(?:inlet|outlet)(?=_|$)/i, ''), type: inlet ? 'inlet' : 'outlet' }
}

/**
 * The panel after a script's clicks, for one exported manifest.
 *
 * What the request leaves out stays at the default; ticked patches come out in
 * the panel's own order and with its labels (it publishes `list.filter(ticked)`),
 * whatever order they were asked in. A patch the panel would not offer -- an
 * ACU return the manifest does not bind, a server patch that is neither an
 * inlet nor an outlet, a patch the given mesh lacks -- is an error: the solver
 * would have no such patch to probe. Pure; the module config is not touched.
 */
export function resolveMonitoringRequest(request: MonitoringRequest, manifest: unknown): MonitoringConfig {
  if (!isRow(request)) throw new Error('monitoring must be an object')
  const unknown = Object.keys(request).filter((key) => !REQUEST_KEYS.includes(key))
  if (unknown.length) throw new Error(`monitoring accepts ${REQUEST_KEYS.join(', ')}, got ${unknown.join(', ')}`)
  const result = defaultMonitoringConfig()
  if (request.residuals !== undefined) {
    if (typeof request.residuals !== 'boolean') throw new Error('monitoring.residuals must be true or false')
    result.residuals = request.residuals
  }
  const mesh = request.meshPatches ?? null
  if (mesh !== null && !(Array.isArray(mesh) && mesh.every((name) => typeof name === 'string'))) {
    throw new Error('monitoring.meshPatches must be a list of patch names')
  }
  const inMesh = mesh ? new Set(mesh) : null
  const acu = request.acuReturnTemperature
  if (acu === undefined || acu === 'all') {
    // Left out: the default, which is every ACU return.
    result.acuReturnTemperature = boundAcuReturnPatches(manifest, mesh)
  } else {
    if (!Array.isArray(acu)) throw new Error("monitoring.acuReturnTemperature must be 'all' or a list")
    const offered = boundAcuReturnPatches(manifest)
    const wanted = new Set(acu.map((entry) => patchName(entry, 'acuReturnTemperature')))
    for (const name of wanted) {
      if (!offered.some((p) => p.patch === name)) {
        throw new Error(`monitoring.acuReturnTemperature: ${name} is not an ACU return this project binds ` +
          `(bound: ${offered.map((p) => p.patch).join(', ') || 'none'})`)
      }
      if (inMesh && !inMesh.has(name)) throw new Error(`monitoring.acuReturnTemperature: the mesh has no patch ${name}`)
    }
    result.acuReturnTemperature = offered.filter((p) => wanted.has(p.patch))
  }
  const servers = request.serverTemperature
  if (servers !== undefined) {
    if (!Array.isArray(servers)) throw new Error('monitoring.serverTemperature must be a list')
    const rows = new Map<string, MonitorPatch>()
    for (const entry of servers) {
      const name = patchName(entry, 'serverTemperature')
      const row = isRow(entry)
        ? { patch: name, shortId: String(entry.shortId ?? serverPatch(name)?.shortId ?? name),
            type: String(entry.type ?? serverPatch(name)?.type ?? 'outlet') }
        : serverPatch(name)
      if (!row) throw new Error(`monitoring.serverTemperature: ${name} is not a server inlet or outlet patch`)
      if (inMesh && !inMesh.has(name)) throw new Error(`monitoring.serverTemperature: the mesh has no patch ${name}`)
      if (!rows.has(name)) rows.set(name, row)
    }
    const order = mesh ? mesh.filter((name) => rows.has(name)) : [...rows.keys()]
    result.serverTemperature = [...new Set(order)].map((name) => rows.get(name) as MonitorPatch)
  }
  return result
}
