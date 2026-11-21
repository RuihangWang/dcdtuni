import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  LIQUID_QUANTITIES, liquidQuantityFrame, liquidQuantityGlobalDomain, liquidValueColor, displayDomain,
} from '../src/liquid/liquidDisplay'
import { liquidArrowGlobalDomains, liquidFlowArrows, liquidFlowFrame } from '../src/liquid/liquidFlow'
import { liquidFrame } from '../src/liquid/liquidTempColor'

const TESTDATA = resolve(__dirname, '../../shared/testdata')
type Bag = Record<string, any>
/** The flow fixture plus the two bindings the display rules add parts for: a
 * ring-feed pipe_junction and an untee'd pipe whose binding lists no edges —
 * the same patch as cfd_demo/tests/test_liquid_display.display_fixture. */
const fixture = (): Bag => {
  const raw = JSON.parse(readFileSync(resolve(TESTDATA, 'liquid_flow_fixture.json'), 'utf8'))
  raw.object_bindings.ring_supply_feed_tee = { label: 'Supply_Feed_Tee', kind: 'pipe_junction', node_ids: ['ring_supply_feed_tee_junction'], edge_ids: [] }
  raw.object_bindings.cdu_supply_feed = { label: 'CDU_supply_Feed', kind: 'pipe_segment', node_ids: [], edge_ids: [] }
  return raw
}
const expected = (): Bag => JSON.parse(readFileSync(resolve(TESTDATA, 'liquid_display_expected.json'), 'utf8'))

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
  } else if (typeof exp === 'number') {
    // Integers, and the signed zeros of a flipped direction, compare by value.
    expect(got === exp, `${where}: ${got} vs ${exp}`).toBe(true)
  } else {
    expect(got, where).toEqual(exp)
  }
}

describe('liquid display contract (Python liquid_colour/liquid_flow <-> liquidDisplay.ts/liquidFlow.ts)', () => {
  it('reproduces every quantity frame and global domain', () => {
    const raw = fixture()
    const exp = expected()
    expect(exp.samples).toEqual([0, 1, 2])
    expect([...LIQUID_QUANTITIES]).toEqual(Object.keys(exp.quantities))
    for (const q of LIQUID_QUANTITIES) {
      for (const s of exp.samples as number[]) {
        expectClose(liquidQuantityFrame(raw, s, q), exp.quantities[q][String(s)], `${q} ${s}`)
      }
      expectClose(liquidQuantityGlobalDomain(raw, q), exp.globalDomains[q], `${q} global`)
    }
  })

  it('reproduces the flow arrows and their global domains', () => {
    const raw = fixture()
    const exp = expected()
    for (const s of exp.samples as number[]) expectClose(liquidFlowArrows(raw, s), exp.arrows[String(s)], `arrows ${s}`)
    expectClose(liquidArrowGlobalDomains(raw), exp.arrowGlobalDomains, 'arrow global')
  })

  it('reproduces the colour tables', () => {
    for (const [key, rgb] of Object.entries(expected().colours as Record<string, number[]>)) {
      const [cmap, value, lo, hi] = key.split('|')
      const c = liquidValueColor(Number(value), [Number(lo), Number(hi)], cmap as 'coolwarm')
      expect([(c >> 16) & 255, (c >> 8) & 255, c & 255], key).toEqual(rgb)
    }
  })
})

describe('liquid display rules', () => {
  const part = (fr: ReturnType<typeof liquidQuantityFrame>, kind: string, model: string, equip = '', side = '') =>
    fr!.parts.find(p => p.kind === kind && p.modelUid === model && p.equipmentUid === equip && p.liquidPart === side)

  it('temperature parts are the colour rule, cells the solved cell temperatures', () => {
    const raw = fixture()
    const fr = liquidQuantityFrame(raw, 1, 'temperature')!
    const colours = liquidFrame(raw, 1)!
    for (const t of colours.targets.filter(t => t.group === 'network')) {
      expect(part(fr, t.kind, t.modelUid, t.equipmentUid, t.liquidPart)!.value).toBe(t.T)
    }
    expect(fr.cells).toEqual(raw.cell_temp_K.map((row: number[]) => row[1]))
    expect(fr.unit).toBe('K')
  })

  it('flow parts are L/min of the flow rule; pumps and HX are not coloured parts', () => {
    const raw = fixture()
    const fr = liquidQuantityFrame(raw, 0, 'flow')!
    const flow = liquidFlowFrame(raw, 0)!
    const pipe = flow.targets.find(t => t.kind === 'pipe' && t.modelUid === 'm_31')!
    expect(part(fr, 'pipe', 'm_31')!.value).toBe(pipe.volumeFlowLpm)
    expect(fr.parts.some(p => (p.kind as string) === 'pump' || (p.kind as string) === 'hx')).toBe(false)
  })

  it('pressure cells interpolate their edge ends in kPa', () => {
    const raw = fixture()
    const fr = liquidQuantityFrame(raw, 2, 'pressure')!
    const edge = 'm_31_seg0'
    const i = raw.edge_ids.indexOf(edge)
    const p = (node: string) => raw.node_pressure_Pa[raw.node_ids.indexOf(node)][2]
    const cells = raw.cell_edge_ids.map((e: string, c: number) => [e, c]).filter(([e]: [string]) => e === edge).map(([, c]: [string, number]) => c)
    cells.forEach((c: number, k: number) => {
      const a = p(raw.edge_from[i]), b = p(raw.edge_to[i])
      expect(fr.cells[c]).toBeCloseTo((a + (b - a) * (k + 0.5) / cells.length) / 1000, 12)
    })
  })

  it('a pipe junction shows its node: temperature, pressure, and what flows through it', () => {
    const raw = fixture()
    const i = raw.node_ids.indexOf('ring_supply_feed_tee_junction')
    const feed = raw.edge_flow_kg_s[raw.edge_ids.indexOf('cdu_supply_feed')][1]
    expect(part(liquidQuantityFrame(raw, 1, 'temperature')!, 'junction', 'ring_supply_feed_tee')!.value).toBe(raw.node_temperature_K[i][1])
    expect(part(liquidQuantityFrame(raw, 1, 'flow')!, 'junction', 'ring_supply_feed_tee')!.value).toBeCloseTo(feed / raw.fluid.rho_kg_m3 * 60000, 12)
    expect(part(liquidQuantityFrame(raw, 1, 'pressure')!, 'junction', 'ring_supply_feed_tee')!.value).toBeCloseTo(raw.node_pressure_Pa[i][1] / 1000, 12)
    expect(part(liquidQuantityFrame(raw, 1, 'velocity')!, 'junction', 'ring_supply_feed_tee')).toBeUndefined()
    expect(liquidFrame(raw, 1)!.targets.some(t => t.kind === 'junction' && t.modelUid === 'ring_supply_feed_tee')).toBe(true)
  })

  it('an untee\'d pipe binding without edges owns the edge named after it', () => {
    const raw = fixture()
    expect(part(liquidQuantityFrame(raw, 0, 'temperature')!, 'pipe', 'cdu_supply_feed')).toBeDefined()
    expect(liquidFrame(raw, 0)!.targets.some(t => t.kind === 'pipe' && t.modelUid === 'cdu_supply_feed')).toBe(true)
    expect(liquidFlowFrame(raw, 0)!.targets.some(t => t.kind === 'pipe' && t.modelUid === 'cdu_supply_feed')).toBe(true)
  })

  it('a display domain is never empty (Kit refuses lo == hi)', () => {
    expect(displayDomain([2, 2])).toEqual([2, 2 + 1e-6])
    expect(displayDomain([1, 3])).toEqual([1, 3])
    expect(displayDomain(null)).toBeNull()
  })

  it('arrows skip server branches and carry the distributed manifold flow', () => {
    const raw = fixture()
    const fr = liquidFlowArrows(raw, 2)!
    expect(fr.arrows.some(a => a.edgeId.endsWith('_branch_in') || a.edgeId.endsWith('_branch_out'))).toBe(false)
    const headers = fr.arrows.filter(a => a.kind === 'header')
    expect(headers.length).toBeGreaterThan(0)
    const branches = fr.branches.filter(b => b.rackUid === 'm_1')
    expect(branches.map(b => `${b.serverUid}/${b.side}`).sort()).toEqual(['e_1/return', 'e_1/supply', 'e_2/return', 'e_2/supply'])
    const lowest = headers.filter(a => a.edgeId.includes('_mf_supply')).sort((a, b) => a.position[1]! - b.position[1]!)[0]!
    // Below every tap the supply header carries both servers' flow.
    const both = branches.filter(b => b.side === 'supply').reduce((sum, b) => sum + b.flowKgS, 0)
    expect(lowest.massFlowKgS).toBeCloseTo(both, 15)
    expect(lowest.direction).toEqual([0, 1, 0])
  })
})
