import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { liquidFlowFrame, liquidFlowGlobalDomains, type LiquidFlowFrame } from '../src/liquid/liquidFlow'

const TESTDATA = resolve(__dirname, '../../shared/testdata')
type Bag = Record<string, any>
const fixture = (): Bag => JSON.parse(readFileSync(resolve(TESTDATA, 'liquid_flow_fixture.json'), 'utf8'))
const expected = (): Bag => JSON.parse(readFileSync(resolve(TESTDATA, 'liquid_flow_expected.json'), 'utf8'))
const VALVE = 'm_32_control_valve_1778059294098'

/** Deep comparison: floats to 12 significant digits, everything else exact. */
function expectClose(got: unknown, exp: unknown, where: string): void {
  if (Array.isArray(exp)) {
    expect(Array.isArray(got), where).toBe(true)
    expect((got as unknown[]).length, where).toBe(exp.length)
    exp.forEach((e, i) => expectClose((got as unknown[])[i], e, `${where}[${i}]`))
  } else if (exp !== null && typeof exp === 'object') {
    expect(got !== null && typeof got === 'object', where).toBe(true)
    expect(Object.keys(got as object).sort(), where).toEqual(Object.keys(exp).sort())
    for (const [k, v] of Object.entries(exp)) expectClose((got as Bag)[k], v, `${where}.${k}`)
  } else if (typeof exp === 'number' && !Number.isInteger(exp)) {
    expect(typeof got, where).toBe('number')
    expect(Math.abs((got as number) - exp) <= 1e-12 * Math.abs(exp) + 1e-15, `${where}: ${got} vs ${exp}`).toBe(true)
  } else {
    expect(got, where).toEqual(exp)
  }
}

describe('liquid flow contract (Python liquid_flow.py <-> React liquidFlow.ts)', () => {
  it('reproduces every frame and the global domains of the shared contract', () => {
    const raw = fixture()
    const exp = expected()
    expect(exp.samples).toEqual([0, 1, 2])
    for (const [sample, frame] of Object.entries(exp.frames as Record<string, unknown>)) {
      expectClose(liquidFlowFrame(raw, Number(sample)), frame, `frame ${sample}`)
    }
    expectClose(liquidFlowGlobalDomains(raw), exp.globalDomains, 'globalDomains')
  })
})

describe('liquid flow rules', () => {
  const edge = (fr: LiquidFlowFrame, id: string) => fr.edges.find(e => e.edgeId === id)!
  const target = (fr: LiquidFlowFrame, kind: string, model: string, equip = '', part = '') =>
    fr.targets.find(t => t.kind === kind && t.modelUid === model && t.equipmentUid === equip && t.liquidPart === part)!

  it('shows the actual direction of a reversed valve, with LPM and velocity', () => {
    const raw = fixture()
    const fr = liquidFlowFrame(raw, 0)!
    const i = raw.edge_ids.indexOf(VALVE)
    const m = raw.edge_flow_kg_s[i][0]
    const valve = edge(fr, VALVE)
    expect(m).toBeLessThan(0)
    expect(valve.reversed).toBe(true)
    expect(valve.upstream).toBe(raw.edge_to[i])
    expect(valve.downstream).toBe(raw.edge_from[i])
    expect(valve.massFlowKgS).toBe(-m)
    const rho = raw.fluid.rho_kg_m3
    expect(valve.volumeFlowLpm).toBeCloseTo(-m / rho * 60000, 12)
    expect(valve.velocityMS!).toBeCloseTo(-m / (rho * Math.PI * 0.025 * 0.025 / 4), 12)
    expect(edge(fr, 'm_30_pump').velocityMS).toBeNull()
  })

  it('points every cell arrow along its pipe in the direction the water moves', () => {
    const raw = fixture()
    const fr = liquidFlowFrame(raw, 2)!
    expect(fr.cells).toHaveLength(raw.cell_edge_ids.length)
    raw.cell_edge_ids.forEach((e: string, c: number) => {
      const own = raw.cell_edge_ids.map((x: string, j: number) => (x === e ? j : -1)).filter((j: number) => j >= 0)
      const k = own.indexOf(c)
      const a = raw.cell_positions[own[Math.max(k - 1, 0)]]
      const b = raw.cell_positions[own[Math.min(k + 1, own.length - 1)]]
      const m = raw.edge_flow_kg_s[raw.edge_ids.indexOf(e)][2]
      const d = fr.cells[c]!.direction
      expect(Math.hypot(...d)).toBeCloseTo(1, 12)
      expect((d[0] * (b[0] - a[0]) + d[1] * (b[1] - a[1]) + d[2] * (b[2] - a[2])) * m).toBeGreaterThan(0)
    })
  })

  it('a manifold carries the sum of its branches and a tee passes what flows in', () => {
    const raw = fixture()
    const fr = liquidFlowFrame(raw, 1)!
    const flow = (id: string) => raw.edge_flow_kg_s[raw.edge_ids.indexOf(id)][1] as number
    const branchIn = (raw.object_bindings.m_1.edge_ids as string[]).filter(e => e.endsWith('_branch_in'))
    expect(target(fr, 'manifold', 'm_1', '', 'supply-manifold').massFlowKgS)
      .toBeCloseTo(branchIn.reduce((s, e) => s + Math.abs(flow(e)), 0), 14)
    expect(target(fr, 'cdu', 'm_30').massFlowKgS).toBeCloseTo(Math.abs(flow('m_30_pump')), 14)
    expect(target(fr, 'server', 'm_1', 'e_1').group).toBe('servers')
    const tee = 'm_31_tee_tee_1778059219789'
    let inflow = 0, outflow = 0
    raw.edge_ids.forEach((e: string, i: number) => {
      const m = flow(e)
      if (raw.edge_to[i] === tee) { inflow += Math.max(m, 0); outflow += Math.max(-m, 0) }
      if (raw.edge_from[i] === tee) { inflow += Math.max(-m, 0); outflow += Math.max(m, 0) }
    })
    expect(target(fr, 'tee', 'm_31', 'tee_1778059219789').massFlowKgS).toBeCloseTo(inflow, 14)
    expect(inflow).toBeCloseTo(outflow, 12)
  })

  it('marks stagnant edges and dry nodes, and gives them no arrow', () => {
    const raw = fixture()
    const i = raw.edge_ids.indexOf(VALVE)
    raw.edge_stagnant[i] = true
    const dry = raw.edge_to[i]
    raw.node_observable[raw.node_ids.indexOf(dry)] = [false, false, false]
    const pipe = raw.cell_edge_ids[0]
    raw.edge_stagnant[raw.edge_ids.indexOf(pipe)] = true
    const fr = liquidFlowFrame(raw, 0)!
    const valve = edge(fr, VALVE)
    expect([valve.stagnant, valve.massFlowKgS, valve.velocityMS, valve.upstream, valve.reversed]).toEqual([true, 0, 0, null, false])
    expect(fr.stagnantEdges).toEqual([pipe, VALVE].sort((a, b) => raw.edge_ids.indexOf(a) - raw.edge_ids.indexOf(b)))
    expect(fr.unobservableNodes).toEqual([dry])
    const cells = fr.cells.filter(c => c.edgeId === pipe)
    expect(cells.length).toBeGreaterThan(0)
    for (const c of cells) expect([c.direction, c.stagnant, c.massFlowKgS]).toEqual([[0, 0, 0], true, 0])
  })

  it('a result without topology still gives arrows and edge flows, but no node throughputs', () => {
    const raw = fixture()
    for (const k of ['edge_from', 'edge_to', 'edge_types', 'edge_D_m', 'node_positions_m', 'node_observable', 'edge_stagnant']) delete raw[k]
    const fr = liquidFlowFrame(raw, 0)!
    for (const c of fr.cells) expect(Math.hypot(...c.direction)).toBeCloseTo(1, 12)
    expect(edge(fr, VALVE).upstream).toBeNull()
    expect(edge(fr, VALVE).reversed).toBe(true)
    const kinds = new Set(fr.targets.map(t => t.kind))
    for (const k of ['tee', 'cdu', 'pump']) expect(kinds.has(k as never)).toBe(false)
    for (const k of ['pipe', 'manifold', 'valve', 'server']) expect(kinds.has(k as never)).toBe(true)
  })

  it('returns null for an out-of-range sample or a result without flows or density', () => {
    expect(liquidFlowFrame(fixture(), 3)).toBeNull()
    expect(liquidFlowFrame({ time_s: [0], edge_ids: [], fluid: { rho_kg_m3: 1000 } }, 0)).toBeNull()
    expect(liquidFlowFrame({ time_s: [0], edge_ids: [], edge_flow_kg_s: [], fluid: {} }, 0)).toBeNull()
    expect(liquidFlowFrame(fixture(), -1)!.sample).toBe(2)
  })
})
