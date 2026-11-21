import { afterEach, describe, expect, it } from 'vitest'
import {
  boundAcuReturnPatches,
  defaultMonitoringConfig,
  getMonitoringConfig,
  resolveMonitoringRequest,
  setMonitoringConfig,
} from '../src/run/monitoring'

/**
 * The 求解监控 panel's answer for a script (the headless compiler's
 * `options.monitoring`).
 *
 * The panel offers the ACU returns `/api/server-patches` lists (server.py
 * handle_server_patches): the exported manifest's bindings whose role is
 * `acu_return`, in binding order, labelled by the ACU that owns the face, and
 * only those the case's mesh has. A script that presses 全选 must get the same
 * list in the same order, or its manifest is not the one the editor sends.
 * These cases mirror src/server/tests/test_server_patches.py.
 */

const RETURNS = ['ACU_01_Back_patch', 'ACU_02_Back_patch', 'ACU_03_Back_patch']

function manifest() {
  return {
    schemaVersion: 4,
    meshGeometry: {
      acus: Object.fromEntries(RETURNS.map((solid, i) => [`ACU_0${i + 1}`, { returnFaces: [{ solidName: solid }] }])),
    },
    boundaryBindings: {
      supply: { patchName: 'ACU_01_Front_patch', role: 'acu_supply', bcType: 'acu_supply' },
      ...Object.fromEntries(RETURNS.map((name) => [`bb_${name}`, { patchName: name, solidName: name, role: 'acu_return', bcType: 'acu_return' }])),
      duplicate: { patchName: RETURNS[0], solidName: RETURNS[0], role: 'acu_return' },
      fromPath: { patchName: 'Box_return', objectPath: 'constructions.boxes.m_17', bcType: 'acu_return' },
    },
  }
}

afterEach(() => setMonitoringConfig(defaultMonitoringConfig()))

describe('boundAcuReturnPatches', () => {
  it('lists the bound returns in binding order, once, labelled like the server', () => {
    expect(boundAcuReturnPatches(manifest())).toEqual([
      { patch: 'ACU_01_Back_patch', shortId: 'ACU_01', type: 'return' },
      { patch: 'ACU_02_Back_patch', shortId: 'ACU_02', type: 'return' },
      { patch: 'ACU_03_Back_patch', shortId: 'ACU_03', type: 'return' },
      { patch: 'Box_return', shortId: 'm_17', type: 'return' },
    ])
  })

  it('offers only what the mesh has when the mesh patches are known', () => {
    expect(boundAcuReturnPatches(manifest(), ['ACU_02_Back_patch', 'Box_return', 'wall']).map((p) => p.patch))
      .toEqual(['ACU_02_Back_patch', 'Box_return'])
  })

  it('names the ACU from meshGeometry, not the binding key', () => {
    const m = {
      meshGeometry: { acus: { 'source-device': { name: 'ACU West', returnFaces: [{ solidName: 'geometry_return' }] } } },
      boundaryBindings: { r: { patchName: 'actual_return', solidName: 'geometry_return', bcType: 'acu_return' } },
    }
    expect(boundAcuReturnPatches(m)).toEqual([{ patch: 'actual_return', shortId: 'ACU West', type: 'return' }])
  })
})

describe('resolveMonitoringRequest', () => {
  it('keeps the editor default for what the request leaves out: residuals and every ACU return', () => {
    expect(defaultMonitoringConfig()).toEqual({ residuals: true, serverTemperature: [], acuReturnTemperature: 'all' })
    expect(resolveMonitoringRequest({}, manifest())).toEqual(
      { residuals: true, serverTemperature: [], acuReturnTemperature: boundAcuReturnPatches(manifest()) })
    expect(boundAcuReturnPatches(manifest()).length).toBeGreaterThan(0)
  })

  it("'all' is the ACU list's select-all, in the panel's key order", () => {
    const config = resolveMonitoringRequest({ acuReturnTemperature: 'all' }, manifest())
    expect(Object.keys(config)).toEqual(['residuals', 'serverTemperature', 'acuReturnTemperature'])
    expect(config.acuReturnTemperature).toEqual(boundAcuReturnPatches(manifest()))
  })

  it('ticks by name come out in the panel order, with the panel labels', () => {
    const config = resolveMonitoringRequest(
      { residuals: false, acuReturnTemperature: ['ACU_03_Back_patch', { patch: 'ACU_01_Back_patch', shortId: 'x', type: 'y' }] },
      manifest())
    expect(config.residuals).toBe(false)
    expect(config.acuReturnTemperature).toEqual([
      { patch: 'ACU_01_Back_patch', shortId: 'ACU_01', type: 'return' },
      { patch: 'ACU_03_Back_patch', shortId: 'ACU_03', type: 'return' },
    ])
  })

  it('server patches by name take the server short id and type, in mesh order when known', () => {
    const config = resolveMonitoringRequest(
      { serverTemperature: ['Rack_A_srv1_outlet_p1', 'Rack_A_srv1_inlet'],
        meshPatches: ['Rack_A_srv1_inlet', 'Rack_A_srv1_outlet_p1', ...RETURNS] },
      manifest())
    expect(config.serverTemperature).toEqual([
      { patch: 'Rack_A_srv1_inlet', shortId: 'Rack_A_srv1', type: 'inlet' },
      { patch: 'Rack_A_srv1_outlet_p1', shortId: 'Rack_A_srv1_p1', type: 'outlet' },
    ])
  })

  it('refuses what the panel would not offer', () => {
    expect(() => resolveMonitoringRequest({ acuReturnTemperature: ['wall'] }, manifest())).toThrow(/ACU return/)
    expect(() => resolveMonitoringRequest({ acuReturnTemperature: ['ACU_01_Back_patch'], meshPatches: ['wall'] },
      manifest())).toThrow(/mesh/)
    expect(() => resolveMonitoringRequest({ serverTemperature: ['wall'] }, manifest())).toThrow(/inlet or outlet/)
    expect(() => resolveMonitoringRequest({ probes: true } as never, manifest())).toThrow(/probes/)
    expect(() => resolveMonitoringRequest({ residuals: 'yes' } as never, manifest())).toThrow(/residuals/)
  })

  it('does not touch the module config itself', () => {
    resolveMonitoringRequest({ acuReturnTemperature: 'all' }, manifest())
    expect(getMonitoringConfig()).toEqual(defaultMonitoringConfig())
  })
})
