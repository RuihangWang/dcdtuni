import { describe, expect, it } from 'vitest'
import transient from './fixtures/liquidPostprocess/shinaide_transient.json'
import steadyLegacy from './fixtures/liquidPostprocess/shinaide_steady_legacy.json'
import {
  LIQUID_GRAVITY_M_S2,
  chartCatalog,
  filterNetworkRows,
  focusEdgeIdsFor,
  liquidHeatSeries,
  liquidHeatSummary,
  liquidNetworkMetrics,
  liquidNetworkTable,
  objectSeries,
  pressureReferencePa,
  rowDisplayName,
  rowInactiveReason,
  sortNetworkRows,
} from '../src/liquid/liquidNetwork'

/**
 * The fixtures are cuts of real Shinaide DAE results (cut_fixture.py): every
 * value below is either copied from run b9bca1f1's result.json at t = 20 s
 * (fixture sample 2 = result sample 10) or recomputed here from the fixture's
 * own arrays by the rule the module states, independently of the module.
 */
type Raw = typeof transient
const raw = () => JSON.parse(JSON.stringify(transient)) as Raw
const S = 2
const rho = transient.fluid.rho_kg_m3
const nodeIndex = (n: string) => transient.node_ids.indexOf(n)
const edgeIndex = (e: string) => transient.edge_ids.indexOf(e)
const P = (n: string, s = S) => transient.node_pressure_Pa[nodeIndex(n)]![s]!
const T = (n: string, s = S) => transient.node_temperature_K[nodeIndex(n)]![s]!
const y = (n: string) => transient.node_positions_m[nodeIndex(n)]![1]!

describe('network table', () => {
  it('lists every solved edge with the values of result.json at the chosen sample', () => {
    const table = liquidNetworkTable(raw(), S)!
    expect(table.hasTopology).toBe(true)
    expect(table.timeSeconds).toBe(20)
    expect(table.rows.map((r) => r.edgeId)).toEqual(transient.edge_ids)
    transient.edge_ids.forEach((_edgeId, i) => {
      const row = table.rows[i]!
      const m = transient.edge_flow_kg_s[i]![S]!
      const [up, down] = m >= 0 ? [transient.edge_from[i]!, transient.edge_to[i]!] : [transient.edge_to[i]!, transient.edge_from[i]!]
      expect(row.flowKgS).toBe(m)
      expect(row.massFlowKgS).toBe(Math.abs(m))
      expect(row.volumeFlowLpm).toBe(Math.abs(m) / rho * 60000)
      expect(row.upstream).toBe(up)
      expect(row.downstream).toBe(down)
      expect(row.reversed).toBe(m < 0)
      expect(row.staticDropPa).toBe(P(up) - P(down))
      expect(row.lossPa).toBe(P(up) - P(down) + rho * LIQUID_GRAVITY_M_S2 * (y(up) - y(down)))
      expect(row.inletK).toBe(T(up))
      expect(row.outletK).toBe(transient.edge_outlet_temp_K[i]![S])
      const d = transient.edge_D_m[i]
      expect(row.velocityMS).toBe(typeof d === 'number' ? Math.abs(m) / (rho * (Math.PI * d * d / 4)) : null)
      expect(row.type).toBe(transient.edge_types[i])
    })
  })

  it('reads the reversed return valve by its actual flow, rack to ring', () => {
    const row = liquidNetworkTable(raw(), S)!.rows.find((r) => r.edgeId === 'm_32_control_valve_1778059337713')!
    // result.json, sample 10: modelled ring -> rack, solved -0.025994250692657043 kg/s.
    expect(row.flowKgS).toBe(-0.025994250692657043)
    expect(row.reversed).toBe(true)
    expect(row.upstream).toBe('m_32_control_valve_1778059337713_out')
    expect(row.downstream).toBe('m_32_control_valve_1778059337713_in')
    expect(row.staticDropPa).toBe(153931.61798716016 - 153554.48250674744)
    expect(row.inletK).toBe(311.9564305542455)
    expect(row.type).toBe('valve')
    expect(rowDisplayName(row)).toBe('valve_1778059337713')
    expect(row.select).toEqual({ uid: 'm_32', node: { type: 'fitting', uid: 'm_32', fittingId: 'valve_1778059337713' } })
  })

  it('names rows by their bound object and selects the server, CDU or pipe they belong to', () => {
    const rows = liquidNetworkTable(raw(), S)!.rows
    const by = (id: string) => rows.find((r) => r.edgeId === id)!
    expect(rowDisplayName(by('m_1_e_1_cp_CP-CU_1'))).toBe('Rack_A_01-server_1 · cp_CP-CU_1')
    expect(by('m_1_e_1_cp_CP-CU_1').select).toEqual({ uid: 'm_1', node: { type: 'equipment', uid: 'm_1', equipmentId: 'e_1' } })
    expect(rowDisplayName(by('m_1_e_2_branch_in'))).toBe('Rack_A_01-server_2 · branch_in')
    expect(rowDisplayName(by('m_1_mf_supply_bottom_header'))).toBe('Rack_A_01 · mf_supply_bottom_header')
    expect(by('m_1_mf_supply_bottom_header').select).toEqual({ uid: 'm_1', node: null })
    expect(rowDisplayName(by('m_30_pump'))).toBe('CDU_1 · pump')
    expect(by('m_30_hx').select).toEqual({ uid: 'm_30', node: null })
    // A feed pipe's binding lists no edges; the edge carries the model's own uid.
    expect(rowDisplayName(by('cdu_supply_feed'))).toBe('CDU_supply_Feed')
    expect(by('cdu_supply_feed').select).toEqual({ uid: 'cdu_supply_feed', node: null })
    expect(rowDisplayName(by('m_31_spoke_tee_1778059193283'))).toBe('Supply_Closed_Ring · spoke_tee_1778059193283')
  })

  it('loses head along the actual flow on every passive element and gains it across the pump', () => {
    for (let s = 0; s < 3; s++) {
      for (const row of liquidNetworkTable(raw(), s)!.rows) {
        if (row.type === 'pump') expect(row.lossPa!).toBeLessThan(0)
        else expect(row.lossPa!).toBeGreaterThanOrEqual(0)
      }
    }
  })

  it('marks stagnant edges and dry nodes instead of dropping them', () => {
    const r = raw()
    const k = edgeIndex('m_1_e_4_branch_in')
    r.edge_stagnant[k] = true
    const dry = nodeIndex('m_1_e_4_in')
    r.node_observable[dry]![S] = false
    const table = liquidNetworkTable(r, S)!
    const branch = table.rows[k]!
    expect(branch.stagnant).toBe(true)
    expect(branch.massFlowKgS).toBe(0)
    expect(branch.upstream).toBeNull()
    expect(branch.inletK).toBeNull()
    expect(branch.outletK).toBeNull()
    expect(rowInactiveReason(branch)).toBe('stagnant')
    const plate = table.rows[edgeIndex('m_1_e_4_cp_CP-CU_1')]!
    expect(plate.unobservable).toEqual(['m_1_e_4_in'])
    expect(plate.inletK).toBeNull()
    expect(rowInactiveReason(plate)).toBe('unobservable')
    expect(table.stagnantEdges).toEqual(['m_1_e_4_branch_in'])
    expect(table.unobservableNodes).toEqual(['m_1_e_4_in'])
  })

  it('filters by type and search text, and sorts with missing values last', () => {
    const rows = liquidNetworkTable(raw(), S)!.rows
    expect(filterNetworkRows(rows, new Set(['valve', 'pump']), '').map((r) => r.edgeId))
      .toEqual(['m_30_pump', 'm_32_control_valve_1778059337713'])
    expect(filterNetworkRows(rows, null, 'SERVER_3').map((r) => r.edgeId))
      .toEqual(['m_1_e_3_branch_in', 'm_1_e_3_branch_out', 'm_1_e_3_cp_CP-CU_1'])
    expect(filterNetworkRows(rows, null, 'm_30_supply').map((r) => r.edgeId)).toEqual(['cdu_supply_feed', 'm_30_hx'])
    const byFlow = sortNetworkRows(rows, 'flow', 'desc')
    expect(byFlow[0]!.massFlowKgS).toBe(Math.max(...rows.map((r) => r.massFlowKgS!)))
    const byVelocity = sortNetworkRows(rows, 'velocity', 'asc')
    expect(byVelocity.at(-1)!.velocityMS).toBeNull()
    expect(sortNetworkRows(rows, 'velocity', 'desc').at(-1)!.velocityMS).toBeNull()
  })
})

describe('key figures', () => {
  it('reports pump flow and head, chip, loop temperatures, fastest pipe and weakest branch', () => {
    const m = liquidNetworkMetrics(raw(), S)!
    expect(m.totalFlowKgS).toBe(0.35277983675517166)
    expect(m.totalFlowLpm).toBe(0.35277983675517166 / rho * 60000)
    const pump = m.pumps[0]!
    expect(pump.edgeId).toBe('m_30_pump')
    expect(pump.speedRatio).toBe(0.7)
    const headPa = (156103.66693763444 - 150000.0) - rho * LIQUID_GRAVITY_M_S2 * (y('m_30_return') - y('m_30_pump_discharge'))
    expect(pump.headPa).toBeCloseTo(headPa, 9)
    expect(pump.headM).toBeCloseTo(headPa / (rho * LIQUID_GRAVITY_M_S2), 12)
    expect(m.maxChip).toEqual({ valueK: 316.64426642176846, rackId: 'm_1', serverId: 'e_4' })
    expect(m.loops).toEqual([{ cduUid: 'm_30', label: 'CDU_1', supplyK: 307.8251237262553, returnK: 319.37569684222296 }])
    const rows = liquidNetworkTable(raw(), S)!.rows
    expect(m.maxVelocity!.valueMS).toBe(Math.max(...rows.flatMap((r) => (r.velocityMS === null ? [] : [r.velocityMS]))))
    expect(m.minFlowBranch).toMatchObject({ rackId: 'm_1', serverId: 'e_1', massFlowKgS: 0.006495038238157252 })
  })

  it('follows the pump speed drop between samples', () => {
    expect(liquidNetworkMetrics(raw(), 0)!.totalFlowKgS).toBe(0.509677232170846)
    expect(liquidNetworkMetrics(raw(), 0)!.pumps[0]!.speedRatio).toBe(1)
  })
})

describe('heat and HX', () => {
  it('copies the diagnostics, HX temperatures and controls of the chosen sample', () => {
    const h = liquidHeatSummary(raw(), S)!
    expect(h.diagnostics).toEqual({
      heatInputW: 25200.0, primaryRejectionW: 20509.550510529254, storageRateW: 4690.44948947617,
      energyBalanceW: -5.424226401373744e-09, boundaryExchangeW: 0.0, massResidualKgS: 5.551115123125783e-17,
      momentumResidualPa: 5.820766091346741e-11, scaledResidual: 2.0445440895855427e-13,
    })
    expect(h.hx).toEqual([{
      edgeId: 'm_30_hx', ownerLabel: 'CDU_1', model: 'mixed-volumes', modelInferred: false,
      primaryInletK: 293.15, primaryFlowKgS: 1.0, primaryFlowLpm: 1.0 / 992.2 * 60000,
      primaryReturnK: 298.0565910312271, secondaryInletK: 319.37569684222296,
      secondarySupplyK: 307.8251237262553, secondaryFlowKgS: 0.3527798367551716,
    }])
    expect(h.heatMultiplier).toBe(1.2)
    expect(h.pumpSpeeds).toEqual([{ edgeId: 'm_30_pump', ratio: 0.7 }])
    expect(h.valveOpenings.find((v) => v.edgeId === 'm_32_control_valve_1778059294098')!.opening).toBe(0.3)
    expect(h.solver).toEqual({ name: 'differentiable_newton_dae', newtonDae: true, maxScaledResidual: 9.815458668072097e-11, iterations: 14 })
    const series = liquidHeatSeries(raw())!
    expect(series.input).toEqual(transient.diagnostics.map((d) => d.heat_input_W))
    expect(series.storage).toEqual(transient.diagnostics.map((d) => d.storage_rate_W))
  })

  it('says the HX model was not recorded on a run that predates the record', () => {
    expect(liquidHeatSummary(JSON.parse(JSON.stringify(steadyLegacy)), -1)!.hx[0]!.model).toBeNull()
  })
})

describe('selection mapping', () => {
  it('maps a model, a server and a valve to their edges through object_bindings', () => {
    expect(focusEdgeIdsFor(raw(), ['m_1'], { type: 'equipment', uid: 'm_1', equipmentId: 'e_2' }))
      .toEqual(['m_1_e_2_branch_in', 'm_1_e_2_branch_out', 'm_1_e_2_cp_CP-CU_1'])
    expect(focusEdgeIdsFor(raw(), ['m_32'], { type: 'fitting', uid: 'm_32', fittingId: 'valve_1778059337713' }))
      .toEqual(['m_32_control_valve_1778059337713'])
    expect(focusEdgeIdsFor(raw(), ['m_30'], null)).toEqual(['m_30_pump', 'm_30_hx'])
    expect(focusEdgeIdsFor(raw(), ['cdu_supply_feed'], null)).toEqual(['cdu_supply_feed'])
    expect(focusEdgeIdsFor(raw(), ['m_9'], null)).toEqual([])
  })
})

describe('object series', () => {
  it('plots a server by its chip, coolant in/out, branch flow and in/out pressure', () => {
    const server = transient.rack_summaries[0]!.servers[0]!
    const temp = objectSeries(raw(), { kind: 'server', key: 'm_1/e_1' }, 'temperature')!
    expect(temp.unit).toBe('°C')
    expect(temp.series.map((x) => x.id)).toEqual(['chip', 'inlet', 'outlet'])
    expect(temp.series[0]!.points).toEqual(server.max_chip_temp_K.map((k) => k - 273.15))
    expect(temp.series[1]!.points).toEqual([0, 1, 2].map((s) => T('m_1_e_1_in', s) - 273.15))
    const flow = objectSeries(raw(), { kind: 'server', key: 'm_1/e_1' }, 'flow')!
    expect(flow.series[0]!.kgS).toEqual(server.flow_kg_s)
    expect(flow.series[0]!.points).toEqual(server.flow_kg_s.map((m) => m / rho * 60000))
    const abs = objectSeries(raw(), { kind: 'server', key: 'm_1/e_1' }, 'pressure')!
    expect(abs.gauge).toBe(false)
    expect(abs.series[0]!.points).toEqual([0, 1, 2].map((s) => P('m_1_e_1_in', s) / 1000))
    const gauge = objectSeries(raw(), { kind: 'server', key: 'm_1/e_1' }, 'pressure', true)!
    expect(gauge.gauge).toBe(true)
    expect(gauge.referencePa).toEqual([150000, 150000, 150000])
    expect(gauge.series[0]!.points).toEqual([0, 1, 2].map((s) => (P('m_1_e_1_in', s) - 150000) / 1000))
  })

  it('plots a cold plate from cp_details and a pipe edge by its actual ends', () => {
    const cp = transient.cp_details[0]!
    const temp = objectSeries(raw(), { kind: 'edge', edgeId: cp.edge_id }, 'temperature')!
    expect(temp.series.map((x) => [x.id, x.points])).toEqual([
      ['chip', cp.chip_temp_K.map((k) => k - 273.15)],
      ['inlet', cp.water_inlet_temp_K.map((k) => k - 273.15)],
      ['outlet', cp.water_outlet_temp_K.map((k) => k - 273.15)],
    ])
    const valve = 'm_32_control_valve_1778059337713'
    const press = objectSeries(raw(), { kind: 'edge', edgeId: valve }, 'pressure')!
    expect(press.series[0]!.points[S]).toBe(153931.61798716016 / 1000)
    expect(press.series[1]!.points[S]).toBe(153554.48250674744 / 1000)
  })

  it('plots a CDU by supply/return, primary side, pump flow and its three pressures', () => {
    const temp = objectSeries(raw(), { kind: 'cdu', key: 'm_30' }, 'temperature')!
    expect(temp.series.map((x) => x.id)).toEqual(['supply', 'return', 'primary_inlet', 'primary_return'])
    expect(temp.series[3]!.points).toEqual(transient.primary_return_K[0]!.map((k) => k - 273.15))
    const flow = objectSeries(raw(), { kind: 'cdu', key: 'm_30' }, 'flow')!
    expect(flow.series[0]!.kgS).toEqual(transient.edge_flow_kg_s[edgeIndex('m_30_pump')])
    const press = objectSeries(raw(), { kind: 'cdu', key: 'm_30' }, 'pressure', true)!
    expect(press.series.map((x) => x.id)).toEqual(['p_supply', 'p_return', 'p_discharge'])
    expect(press.series[1]!.points).toEqual([0, 0, 0])
  })

  it('finds every node a loop reference through the CDU return', () => {
    expect(pressureReferencePa(raw(), 'm_1_e_3_out')).toEqual([150000, 150000, 150000])
  })

  it('offers servers, plates, pipes and CDUs for the chart picker', () => {
    const catalog = chartCatalog(raw())!
    expect(catalog.servers.map((x) => x.label)).toEqual(['Rack_A_01-server_1', 'Rack_A_01-server_2', 'Rack_A_01-server_3', 'Rack_A_01-server_4'])
    expect(catalog.coldPlates).toHaveLength(4)
    expect(catalog.cdus).toEqual([{ key: 'm_30', label: 'CDU_1' }])
    expect(catalog.pipes.map((x) => x.edgeId)).toContain('m_30_pump')
  })
})

describe('a result written before the result topology', () => {
  it('still lists flows and outlet temperatures, with types read from the file and no direction', () => {
    const legacy = JSON.parse(JSON.stringify(steadyLegacy))
    const table = liquidNetworkTable(legacy, -1)!
    expect(table.hasTopology).toBe(false)
    const i = steadyLegacy.edge_ids.indexOf('m_1_e_1_cp_CP-CU_1')
    const plate = table.rows[i]!
    expect(plate.type).toBe('cold_plate')
    expect(plate.flowKgS).toBe(steadyLegacy.edge_flow_kg_s[i]![0])
    expect(plate.outletK).toBe(steadyLegacy.edge_outlet_temp_K[i]![0])
    expect(plate.upstream).toBeNull()
    expect(plate.staticDropPa).toBeNull()
    expect(table.rows.find((r) => r.edgeId === 'm_30_hx')!.type).toBe('heat_exchanger')
    expect(table.rows.find((r) => r.edgeId === 'm_30_pump')!.type).toBe('pump')
    const press = objectSeries(legacy, { kind: 'edge', edgeId: 'm_1_e_1_cp_CP-CU_1' }, 'pressure', true)!
    const cp = steadyLegacy.cp_details[0]!
    expect(press.series[0]!.points).toEqual([(cp.inlet_pressure_Pa[0]! - press.referencePa![0]!) / 1000])
  })
})

describe('chart target of a selection', () => {
  it('plots the picked edge, else the selected server or CDU, else the first selected edge', async () => {
    const { seriesTargetForFocus } = await import('../src/liquid/liquidNetwork')
    expect(seriesTargetForFocus(raw(), ['m_30_pump', 'm_30_hx'], 'm_30_hx')).toEqual({ kind: 'edge', edgeId: 'm_30_hx' })
    expect(seriesTargetForFocus(raw(), ['m_1_e_2_branch_in', 'm_1_e_2_branch_out', 'm_1_e_2_cp_CP-CU_1'], null)).toEqual({ kind: 'server', key: 'm_1/e_2' })
    expect(seriesTargetForFocus(raw(), ['m_30_pump', 'm_30_hx'], null)).toEqual({ kind: 'cdu', key: 'm_30' })
    expect(seriesTargetForFocus(raw(), ['m_32_control_valve_1778059337713'], null)).toEqual({ kind: 'edge', edgeId: 'm_32_control_valve_1778059337713' })
    expect(seriesTargetForFocus(raw(), [], null)).toBeNull()
  })
})
