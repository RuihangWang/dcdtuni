import { describe, expect, it } from 'vitest'
import { parseLiquidResults } from '../src/liquid/liquidResults'

/**
 * The network arrays the results PANEL never reads, and the post-processing
 * chart cannot do without: per-edge flow and per-node pressure.
 */
describe('parseLiquidResults — the network arrays', () => {
  const base = {
    rack_summaries: [{ rack_id: 'R', total_flow_kg_s: [1], supply_temp_K: [300], servers: [] }],
  }

  it('keeps edge and node ids alongside their matrices', () => {
    const r = parseLiquidResults({
      ...base,
      edge_ids: ['a', 'b'],
      edge_flow_kg_s: [[1, 2], [3, 4]],
      node_ids: ['n1'],
      node_pressure_Pa: [[10, 20]],
      chip_temp_K: [[340]],
      water_outlet_temp_K: [[311]],
    })
    expect(r?.edge_ids).toEqual(['a', 'b'])
    expect(r?.edge_flow_kg_s).toEqual([[1, 2], [3, 4]])
    expect(r?.node_ids).toEqual(['n1'])
    expect(r?.node_pressure_Pa).toEqual([[10, 20]])
    expect(r?.chip_temp_K).toEqual([[340]])
    expect(r?.water_outlet_temp_K).toEqual([[311]])
  })

  it('holds an id list at its original LENGTH when an entry is unusable', () => {
    // These ids index into the matrix beside them. Dropping a bad entry would
    // shift every id after it by one, and `edge_flow_kg_s[indexOf('c')]` would
    // then return a real, plausible curve belonging to a different pipe.
    const r = parseLiquidResults({
      ...base,
      edge_ids: ['a', null, 'c'],
      edge_flow_kg_s: [[1], [2], [3]],
    })
    expect(r?.edge_ids).toEqual(['a', '', 'c'])
    expect(r?.edge_ids.indexOf('c')).toBe(2)
  })

  it('turns a missing or malformed matrix into empty series, not undefined rows', () => {
    const r = parseLiquidResults({ ...base, edge_flow_kg_s: [null, [1, 2]] })
    expect(r?.edge_flow_kg_s).toEqual([[], [1, 2]])
    expect(parseLiquidResults(base)?.node_pressure_Pa).toEqual([])
  })
})
