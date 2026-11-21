import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import { exportAirCase } from '../src/headless/projectCompiler'
import { boundAcuReturnPatches, defaultMonitoringConfig, resolveMonitoring } from '../src/run/monitoring'

/**
 * Every ACU return is monitored unless the user unticks one (owner decision
 * 2026-10-22): the runner judges a steady run CONVERGED on exactly those
 * return-air temperatures, so an export without them can only read
 * INCONCLUSIVE. The editor keeps the default as 'all' and the export resolves
 * it against the manifest it builds.
 */
const repo = resolve(process.cwd(), '../../..')

it('an export with the default panel monitors every ACU return the manifest binds', async () => {
  const path = resolve(repo, 'case/Base_leakage/Base_leakage.cfdproj')
  const result = await exportAirCase(JSON.parse(readFileSync(path, 'utf8')), path, '2026-10-22T00:00:00.000Z')
  const manifest = JSON.parse(result.cfdrunJson)
  const acus = manifest.simulationConfig.monitoring.acuReturnTemperature
  expect(Array.isArray(acus)).toBe(true)
  expect(acus.length).toBeGreaterThan(0)
  expect(acus).toEqual(boundAcuReturnPatches(manifest))
  expect(manifest.simulationConfig.monitoring.residuals).toBe(true)
}, 120_000)

it('resolves only the symbolic default; an explicit choice passes through', () => {
  const manifest = { boundaryBindings: { r: { role: 'acu_return', patchName: 'acu_ret', objectPath: 'acus.m_2' } } }
  expect(resolveMonitoring(defaultMonitoringConfig(), manifest).acuReturnTemperature)
    .toEqual([{ patch: 'acu_ret', shortId: 'm_2', type: 'return' }])
  const explicit = { residuals: true, serverTemperature: [], acuReturnTemperature: [] }
  expect(resolveMonitoring(explicit, manifest)).toBe(explicit)
})
