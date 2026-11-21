/**
 * What the solver should probe, for the .cfdrun.
 *
 * `simulationConfig.monitoring` becomes OpenFOAM function objects, so an empty
 * config is a case that writes no probe data and a convergence plot with
 * nothing in it. The legacy editor reads the checkboxes out of the DOM at
 * export time (`smGetMonitoringConfig`); this holds the same answer in a module
 * so the export does not depend on which tab is mounted.
 *
 * The default is the legacy default: residuals on, no temperature probes.
 */

export interface MonitorPatch {
  patch: string
  shortId: string
  type: string
}

export interface MonitoringConfig {
  residuals: boolean
  serverTemperature: MonitorPatch[]
  acuReturnTemperature: MonitorPatch[]
}

let config: MonitoringConfig = {
  residuals: true,
  serverTemperature: [],
  acuReturnTemperature: [],
}

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
 * probes at all while the legacy one asked for residuals.
 */
export function installMonitoringBridge() {
  ;(window as unknown as { smGetMonitoringConfig?: () => MonitoringConfig }).smGetMonitoringConfig =
    getMonitoringConfig
}
