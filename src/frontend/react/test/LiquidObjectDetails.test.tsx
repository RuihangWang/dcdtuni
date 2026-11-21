import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import transient from './fixtures/liquidPostprocess/shinaide_transient.json'
import { LiquidObjectDetails } from '../src/components/LiquidObjectDetails'
import { getLocale, setLocale } from '../src/i18n'
import { focusedLiquidEdge, setFocusedLiquidEdge } from '../src/liquid/liquidPostprocessFocus'
import { liquidPickSelection, pickFromHits, resultKnows } from '../src/liquid/liquidResultPick'
import { meshBelongsTo } from '../src/liquid/liquidResultHighlight'
import type { TreeSelection } from '../src/interaction/treeNodes'

vi.mock('../src/components/TimeSeriesChart', () => ({
  TimeSeriesChart: ({ series }: { series: { label: string }[] }) => <div data-testid="chart">{series.map((s) => s.label).join('|')}</div>,
}))

/** The selected-object details over the real Shinaide result fixture (t = 20 s). */
const S = 2
const raw = () => JSON.parse(JSON.stringify(transient))
const nodeIdx = (n: string) => transient.node_ids.indexOf(n)
const edgeIdx = (e: string) => transient.edge_ids.indexOf(e)
let host: HTMLDivElement, root: Root
const originalLocale = getLocale()
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  act(() => { setLocale('en-US'); setFocusedLiquidEdge(null) })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => { root.unmount(); setLocale(originalLocale); setFocusedLiquidEdge(null) })
  host.remove(); vi.unstubAllGlobals()
})
const detail = (id: string) => host.querySelector<HTMLElement>(`[data-detail="${id}"]`)?.textContent
const render = (selection: { uid: string | null; node: TreeSelection | null }, focusedEdge: string | null = null) =>
  act(() => root.render(<LiquidObjectDetails raw={raw()} sample={S} transient selection={selection} focusedEdge={focusedEdge} />))

describe('LiquidObjectDetails', () => {
  it('asks for a pick when nothing is selected', () => {
    render({ uid: null, node: null })
    expect(host.textContent).toContain('Pick an object in the network table or the 3D view')
  })

  it('shows every value of a selected valve, by its actual flow', () => {
    render({ uid: 'm_32', node: { type: 'fitting', uid: 'm_32', fittingId: 'valve_1778059337713' } })
    const valve = 'm_32_control_valve_1778059337713'
    const pIn = transient.node_pressure_Pa[nodeIdx(`${valve}_in`)]![S]!, pOut = transient.node_pressure_Pa[nodeIdx(`${valve}_out`)]![S]!
    expect(host.querySelector('[data-details-title]')!.textContent).toBe('valve_1778059337713')
    expect(detail('actual')).toBe('valve_1778059337713 · outlet → valve_1778059337713 · inlet (Against the modelled direction)')
    expect(detail('signed-flow')).toBe(`${transient.edge_flow_kg_s[edgeIdx(valve)]![S]!.toPrecision(6)} kg/s`)
    expect(detail('p-up')).toBe(`${(pOut / 1000).toFixed(3)} kPa`)
    expect(detail('p-down')).toBe(`${(pIn / 1000).toFixed(3)} kPa`)
    expect(detail('drop')).toBe(`${((pOut - pIn) / 1000).toFixed(3)} kPa`)
    expect(detail('diameter')).toBe('25.0 mm')
    expect(detail('opening')).toBe('1.000')
    expect(detail('edge-id')).toBe(valve)
  })

  it('summarises a selected server and narrows to the edge picked in the table', () => {
    const selection = { uid: 'm_1', node: { type: 'equipment', uid: 'm_1', equipmentId: 'e_2' } as TreeSelection }
    render(selection)
    const server = transient.rack_summaries[0]!.servers[1]!
    expect(host.querySelector('[data-details-title]')!.textContent).toBe('Rack_A_01-server_2')
    expect(detail('chip')).toBe(`${(server.max_chip_temp_K[S]! - 273.15).toFixed(2)} °C`)
    expect(detail('heat')).toBe(`${server.heat_removed_W[S]!.toFixed(1)} W`)
    expect(detail('inlet')).toBe(`${(transient.node_temperature_K[nodeIdx('m_1_e_2_in')]![S]! - 273.15).toFixed(2)} °C`)
    const edges = [...host.querySelectorAll<HTMLButtonElement>('[data-detail="edges"] button')]
    expect(edges.map((b) => b.dataset.edgeId)).toEqual(['m_1_e_2_branch_in', 'm_1_e_2_branch_out', 'm_1_e_2_cp_CP-CU_1'])
    act(() => edges[2]!.click())
    expect(focusedLiquidEdge()).toBe('m_1_e_2_cp_CP-CU_1')
    render(selection, 'm_1_e_2_cp_CP-CU_1')
    expect(host.querySelector('[data-details-title]')!.textContent).toBe('Rack_A_01-server_2 · cold plate CP-CU_1')
    expect(detail('type')).toBe('Cold plate')
    // A cold plate's own figure is its chip temperature (cp_details), not only the coolant's.
    const plate = transient.cp_details.find((c) => c.edge_id === 'm_1_e_2_cp_CP-CU_1')!
    expect(detail('chip')).toBe(`${(plate.chip_temp_K[S]! - 273.15).toFixed(2)} °C`)
  })

  it('opens the time curves of the selection', () => {
    render({ uid: 'm_30', node: null })
    expect(detail('supply')).toBe(`${(307.8251237262553 - 273.15).toFixed(2)} °C`)
    act(() => host.querySelector<HTMLButtonElement>('[data-details-chart]')!.click())
    expect(document.querySelector('#lq-pp-details-chart [data-testid="chart"]')!.textContent)
      .toBe('Supply temperature|Return temperature|Primary inlet|Primary return')
  })

  it('says when the selected object is not part of the result', () => {
    render({ uid: 'm_9', node: null })
    expect(host.querySelector('[data-details-missing]')).not.toBeNull()
  })
})

describe('liquidPickSelection', () => {
  it('turns a 3D hit into the selection the tree would make', () => {
    expect(liquidPickSelection([{ isValve: true, valveId: 'valve_1', pipeId: 'm_32' }]))
      .toEqual({ uid: 'm_32', node: { type: 'fitting', uid: 'm_32', fittingId: 'valve_1' } })
    expect(liquidPickSelection([{}, { isPipe: true, pipeModelId: 'm_31' }])).toEqual({ uid: 'm_31', node: null })
    expect(liquidPickSelection([{ isLiquidManifold: true, pipeType: 'supply', modelId: 'm_1' }])).toEqual({ uid: 'm_1', node: null })
    expect(liquidPickSelection([{ isRackEquipment: true, equipmentId: 'e_3', modelId: 'm_1' }]))
      .toEqual({ uid: 'm_1', node: { type: 'equipment', uid: 'm_1', equipmentId: 'e_3' } })
    expect(liquidPickSelection([{ isJunctionBodyMesh: true, junctionId: 'ring_supply_feed_tee' }])).toEqual({ uid: 'ring_supply_feed_tee', node: null })
    expect(liquidPickSelection([{ isPortCoupler: true }, {}])).toBeNull()
  })
})

describe('pickFromHits', () => {
  // Hits nearest first. The raised floor is a translucent plane in front of
  // the pipes under it; on the results tab a click there means the pipe
  // (seen in the browser check: clicks on the supply ring selected the floor).
  const floor = [{ modelId: 'm_9' }]
  const valve = [{ isValve: true, valveId: 'valve_1778059337713', pipeId: 'm_32' }]
  it('prefers the nearest hit the result knows over geometry in front of it', () => {
    const knows = resultKnows(raw())
    expect(knows({ uid: 'm_9', node: null })).toBe(false)
    expect(knows({ uid: 'm_32', node: { type: 'fitting', uid: 'm_32', fittingId: 'valve_1778059337713' } })).toBe(true)
    expect(pickFromHits([floor, valve], knows))
      .toEqual({ uid: 'm_32', node: { type: 'fitting', uid: 'm_32', fittingId: 'valve_1778059337713' } })
  })
  it('falls back to the nearest hit when the result knows none of them', () => {
    const knows = resultKnows(raw())
    expect(pickFromHits([[{ isPortCoupler: true }], floor], knows)).toEqual({ uid: 'm_9', node: null })
    expect(pickFromHits([floor, valve])).toEqual({ uid: 'm_9', node: null })
    expect(pickFromHits([[{}]], knows)).toBeNull()
  })
  it('knows nothing without a result', () => {
    expect(resultKnows(null)({ uid: 'm_32', node: null })).toBe(false)
  })
})

describe('meshBelongsTo', () => {
  it('lights a valve, a server in a rack, or a whole pipe with its fittings', () => {
    const valve = { uid: 'm_32', node: { type: 'fitting' as const, uid: 'm_32', fittingId: 'valve_1' } }
    expect(meshBelongsTo([{}, { isValve: true, valveId: 'valve_1', pipeId: 'm_32' }], valve)).toBe(true)
    expect(meshBelongsTo([{ isPipe: true, pipeModelId: 'm_32' }], valve)).toBe(false)
    const server = { uid: 'm_1', node: { type: 'equipment' as const, uid: 'm_1', equipmentId: 'e_2' } }
    expect(meshBelongsTo([{ isRackEquipment: true, equipmentId: 'e_2' }, { modelId: 'm_1' }], server)).toBe(true)
    expect(meshBelongsTo([{ isRackEquipment: true, equipmentId: 'e_3' }, { modelId: 'm_1' }], server)).toBe(false)
    const pipe = { uid: 'm_32', node: null }
    expect(meshBelongsTo([{ isPipe: true, pipeModelId: 'm_32' }], pipe)).toBe(true)
    expect(meshBelongsTo([{}, { isValve: true, valveId: 'valve_1', pipeId: 'm_32' }], pipe)).toBe(true)
    expect(meshBelongsTo([{ isPipe: true, pipeModelId: 'm_31' }], pipe)).toBe(false)
  })
})
