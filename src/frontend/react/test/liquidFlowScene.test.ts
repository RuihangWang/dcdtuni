import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { buildLiquidArrowSpec } from '../src/liquid/liquidFlowScene'
import { cellsOnTrack, paintLiquidNetwork, pipeTracks, vertexArcLengths } from '../src/liquid/liquidNetworkPaint'
import { liquidQuantityFrame, liquidValueColor } from '../src/liquid/liquidDisplay'
import { MeshColorOverlay } from '../src/liquid/meshColorOverlay'
import { liquidFlowArrows } from '../src/liquid/liquidFlow'
import {
  arrowScales, LIQUID_DISPLAY_DEFAULTS, liquidDisplayOptions, networkScale, resetLiquidDisplayOptions, setLiquidDisplayOptions,
} from '../src/liquid/liquidDisplayState'

const TESTDATA = resolve(__dirname, '../../shared/testdata')
type Bag = Record<string, any>
const fixture = (): Bag => JSON.parse(readFileSync(resolve(TESTDATA, 'liquid_flow_fixture.json'), 'utf8'))
const node = (raw: Bag, id: string) => raw.node_ids.indexOf(id) >= 0 ? raw.node_positions_m[raw.node_ids.indexOf(id)] as number[] : null

/** The trunk m_31 as the renderer draws it: a tube swept along its route, with its display source. */
function trunk(raw: Bag, segments = 24): THREE.Mesh {
  const route = ['ring_supply_feed_tee_junction', 'm_31_tee_tee_1778059219789', 'm_31_tee_tee_1778059216039']
    .map(id => new THREE.Vector3(...node(raw, id)!))
  const path = new THREE.CurvePath<THREE.Vector3>()
  for (let i = 1; i < route.length; i++) path.add(new THREE.LineCurve3(route[i - 1]!, route[i]!))
  const mesh = new THREE.Mesh(new THREE.TubeGeometry(path, segments, 0.0125, 8, false),
    new THREE.MeshStandardMaterial({ color: 0x2196f3 }))
  mesh.userData = { isPipe: true, pipeModelId: 'm_31', _pipeDisplaySource: { context: { path, segments }, radius: 0.0125 } }
  return mesh
}

/** Server e_1's supply hose on rack m_1, from its tap on the manifold axis toward the server. */
function stub(raw: Bag, server = 'e_1', side: 'supply' | 'return' = 'supply'): THREE.Mesh {
  const tap = node(raw, `m_1_${server}_${side === 'supply' ? 'in' : 'out'}`)!
  const g = new THREE.CylinderGeometry(0.008, 0.008, 0.2, 8)
  g.rotateZ(Math.PI / 2)
  const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial())
  mesh.position.set(tap[0]! + 0.1, tap[1]!, tap[2]!)
  mesh.userData = { isLiquidManifold: true, stubType: side, modelId: 'm_1', stubServerId: server }
  return mesh
}

const opts = { density: 1, animate: false, speedScale: 1, arrowColor: 'velocity' as const }

describe('arrows on the rendered pipework', () => {
  it('ride the trunk tube in the direction the water flows', () => {
    const raw = fixture()
    const root = new THREE.Group()
    root.add(trunk(raw))
    const spec = buildLiquidArrowSpec(raw, 0, root, opts, arrowScales(raw, 0))
    const onTrunk = spec.arrows.filter(a => a.edgeId === 'm_31_seg0' || a.edgeId === 'm_31_seg1')
    expect(onTrunk).toHaveLength(11)
    const tube = pipeTracks(root)[0]!
    for (const a of onTrunk) {
      expect(spec.tracks[a.track]!.points).toBe(tube.points)
      expect(a.sMin).toBeLessThanOrEqual(a.s)
      expect(a.s).toBeLessThanOrEqual(a.sMax)
      // The route runs from the feed junction toward the tees, as the water does.
      expect(a.dir).toBe(1)
      expect(a.direction[0]).toBeLessThan(0)
    }
    // Neighbouring cells' stretches meet halfway and do not overlap.
    const seg0 = onTrunk.filter(a => a.edgeId === 'm_31_seg0').sort((a, b) => a.s - b.s)
    for (let i = 1; i < seg0.length; i++) expect(seg0[i]!.sMin).toBeCloseTo(seg0[i - 1]!.sMax, 12)
  })

  it('ride their own polyline where no tube carries them (headers, spokes)', () => {
    const raw = fixture()
    const spec = buildLiquidArrowSpec(raw, 0, new THREE.Group(), opts, arrowScales(raw, 0))
    const headers = spec.arrows.filter(a => a.kind === 'header')
    expect(headers.length).toBeGreaterThan(0)
    for (const a of headers) {
      const points = spec.tracks[a.track]!.points
      expect(points[0]).toEqual(node(raw, a.edgeId.includes('supply') ? 'm_1_mf_supply_bottom' : 'm_1_mf_return_bottom'))
    }
    // The supply header carries water up, the return header down.
    expect(headers.filter(a => a.edgeId.includes('_mf_supply')).every(a => a.dir === 1)).toBe(true)
    expect(headers.filter(a => a.edgeId.includes('_mf_return')).every(a => a.dir === -1)).toBe(true)
  })

  it('draw each server branch on its hose, outward on supply and inward on return', () => {
    const raw = fixture()
    const root = new THREE.Group()
    root.add(stub(raw, 'e_1', 'supply'), stub(raw, 'e_1', 'return'))
    const spec = buildLiquidArrowSpec(raw, 0, root, opts, arrowScales(raw, 0))
    const branches = spec.arrows.filter(a => a.kind === 'branch')
    expect(branches.map(a => a.edgeId).sort()).toEqual(['m_1_e_1_branch_in', 'm_1_e_1_branch_out'])
    for (const a of branches) {
      const [start, end] = spec.tracks[a.track]!.points as [number[], number[]]
      // The track starts at the manifold end, the one at the tap.
      expect(Math.abs(start[0]! - node(raw, a.edgeId.endsWith('_in') ? 'm_1_e_1_in' : 'm_1_e_1_out')![0]!)).toBeLessThan(1e-9)
      expect(end[0]).toBeGreaterThan(start[0]!)
    }
    expect(branches.find(a => a.edgeId.endsWith('_in'))!.dir).toBe(1)
    expect(branches.find(a => a.edgeId.endsWith('_out'))!.dir).toBe(-1)
    // Servers without a hose in the scene are counted, not drawn.
    expect(spec.skipped.noStub).toBe(2)
  })

  it('leave out arrows inside a closed cabinet', () => {
    const raw = fixture()
    const all = buildLiquidArrowSpec(raw, 0, new THREE.Group(), opts, arrowScales(raw, 0))
    const cdu = node(raw, 'm_30_supply')!
    const near = (p: readonly number[]) => Math.hypot(p[0]! - cdu[0]!, p[2]! - cdu[2]!) < 0.6
    const some = buildLiquidArrowSpec(raw, 0, new THREE.Group(), opts, arrowScales(raw, 0), near)
    expect(some.skipped.hidden).toBeGreaterThan(0)
    expect(some.arrows.length + some.skipped.hidden).toBe(all.arrows.length)
    expect(some.arrows.some(a => near(a.position))).toBe(false)
  })

  it('grow with mass flow and take the velocity colour', () => {
    const raw = fixture()
    const scales = arrowScales(raw, 2)
    const spec = buildLiquidArrowSpec(raw, 2, new THREE.Group(), opts, scales)
    const big = spec.arrows.reduce((a, b) => (b.massFlowKgS > a.massFlowKgS ? b : a))
    const small = spec.arrows.reduce((a, b) => (b.massFlowKgS < a.massFlowKgS ? b : a))
    // Size is the mass flow's alone, on any pipe, on the whole-run scale.
    expect(big.radius).toBeGreaterThan(small.radius)
    const [lo, hi] = scales.mass!
    for (const a of spec.arrows) {
      const n = Math.max(0, Math.min(1, (a.massFlowKgS - lo) / (hi - lo)))
      expect(a.radius).toBeCloseTo(0.007 + 0.021 * Math.sqrt(n), 12)
    }
    expect(spec.arrows.every(a => a.length > a.radius && a.lift > a.radius * 0.6)).toBe(true)
    for (const a of spec.arrows) {
      expect(a.color).toBe(a.velocityMS === null ? 0xf2f2f2 : liquidValueColor(a.velocityMS, scales.velocity!, scales.colorMap))
      expect(a.speed).toBe(a.velocityMS ?? 0.05)
    }
    const plain = buildLiquidArrowSpec(raw, 2, new THREE.Group(), { ...opts, arrowColor: 'plain' }, scales)
    expect(new Set(plain.arrows.map(a => a.color))).toEqual(new Set([0xf2f2f2]))
    expect(liquidFlowArrows(raw, 2)!.arrows.length).toBe(spec.arrows.length)
  })
})

describe('pipes coloured along their length', () => {
  it('give each tube vertex the value between the cells on either side', () => {
    const raw = fixture()
    const root = new THREE.Group()
    const mesh = trunk(raw)
    root.add(mesh)
    const track = pipeTracks(root)[0]!
    const cells = cellsOnTrack(track, raw)
    expect(cells.map(c => raw.cell_edge_ids[c.cell]).filter(e => !String(e).startsWith('m_31_seg'))).toEqual([])
    expect(cells).toHaveLength(11)
    // The supply trunk is isothermal in this run; its two edges carry different
    // flows (a tee draws off between them), on a scale spanning the trunk.
    const lpm = liquidQuantityFrame(raw, 0, 'flow')!.cells
    const trunkFlows = cells.map(c => lpm[c.cell]!)
    const scale = networkScale(raw, 0, { ...LIQUID_DISPLAY_DEFAULTS, quantity: 'flow',
      manual: { domain: [Math.min(...trunkFlows), Math.max(...trunkFlows)], colorMap: 'viridis' } })
    const overlay = new MeshColorOverlay()
    expect(paintLiquidNetwork(root, raw, 0, scale, overlay)).toBe(1)
    const colours = mesh.geometry.getAttribute('color') as THREE.BufferAttribute
    expect(colours.count).toBe(mesh.geometry.getAttribute('position').count)
    expect((mesh.material as THREE.MeshStandardMaterial).vertexColors).toBe(true)
    // A ring at a cell's arc length has that cell's colour.
    const arcs = vertexArcLengths(track, mesh.geometry)
    for (const c of [cells[0]!, cells[10]!]) {
      // The rings beyond the end cells keep the end cells' colour.
      const v = c === cells[0] ? 0 : arcs.length - 1
      const expected = new THREE.Color().setHex(liquidValueColor(lpm[c.cell]!, scale.domain!, scale.colorMap))
      expect(colours.getX(v)).toBeCloseTo(expected.r, 6)
      expect(colours.getZ(v)).toBeCloseTo(expected.b, 6)
    }
    // ...and the colour changes along the pipe, through the interpolated rings.
    const distinct = new Set(Array.from({ length: colours.count }, (_, i) => colours.getX(i).toFixed(4)))
    expect(distinct.size).toBeGreaterThan(2)
    overlay.restore()
    expect(mesh.geometry.getAttribute('color')).toBeUndefined()
  })

  it('paint a tee or valve whose id is on the group the shared renderer builds around its meshes', () => {
    const raw = fixture()
    const root = new THREE.Group()
    const fitting = new THREE.Group()
    fitting.userData = { isValve: true, isTeeJunction: true, valveId: 'tee_1778059219789', pipeId: 'm_31' }
    const body = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ color: 0x26c6da }))
    fitting.add(body)
    root.add(fitting)
    const scale = networkScale(raw, 0, { ...LIQUID_DISPLAY_DEFAULTS })
    expect(paintLiquidNetwork(root, raw, 0, scale, new MeshColorOverlay())).toBe(1)
    const tee = liquidQuantityFrame(raw, 0, 'temperature')!.parts.find(p => p.kind === 'tee' && p.equipmentUid === 'tee_1778059219789')!
    expect((body.material as THREE.MeshStandardMaterial).color.getHex()).toBe(liquidValueColor(tee.value, scale.domain!, scale.colorMap))
  })

  it('paint a ring-feed junction model by its node, but not its port helpers', () => {
    const raw = fixture()
    raw.object_bindings.ring_supply_feed_tee = { label: 'Supply_Feed_Tee', kind: 'pipe_junction', node_ids: ['ring_supply_feed_tee_junction'], edge_ids: [] }
    const root = new THREE.Group()
    const body = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ color: 0x9c27b0 }))
    body.userData = { isJunctionBodyMesh: true, junctionId: 'ring_supply_feed_tee' }
    const port = new THREE.Mesh(new THREE.SphereGeometry(), new THREE.MeshStandardMaterial({ color: 0x666666 }))
    port.userData = { isJunctionPort: true, junctionId: 'ring_supply_feed_tee', portId: 'a' }
    root.add(body, port)
    const scale = networkScale(raw, 0, { ...LIQUID_DISPLAY_DEFAULTS, quantity: 'pressure' })
    expect(paintLiquidNetwork(root, raw, 0, scale, new MeshColorOverlay())).toBe(1)
    const p = raw.node_pressure_Pa[raw.node_ids.indexOf('ring_supply_feed_tee_junction')][0] / 1000
    expect((body.material as THREE.MeshStandardMaterial).color.getHex()).toBe(liquidValueColor(p, scale.domain!, scale.colorMap))
    expect((port.material as THREE.MeshStandardMaterial).color.getHex()).toBe(0x666666)
  })

  it('give back the material of a part that has no value in the new quantity', () => {
    const raw = fixture()
    const root = new THREE.Group()
    const tee = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ color: 0x888888 }))
    tee.userData = { valveId: 'tee_1778059219789', pipeId: 'm_31' }
    root.add(tee)
    const original = tee.material
    const overlay = new MeshColorOverlay()
    const temperature = networkScale(raw, 0, { ...LIQUID_DISPLAY_DEFAULTS })
    expect(paintLiquidNetwork(root, raw, 0, temperature, overlay)).toBe(1)
    expect(tee.material).not.toBe(original)
    // Tees have no diameter, so no velocity: the tee is left as drawn.
    const velocity = networkScale(raw, 0, { ...LIQUID_DISPLAY_DEFAULTS, quantity: 'velocity' })
    expect(paintLiquidNetwork(root, raw, 0, velocity, overlay)).toBe(0)
    expect(tee.material).toBe(original)
  })
})

describe('the network colour map', () => {
  it('defaults temperature to a map whose ends are not the drawn supply blue and return red', () => {
    expect(networkScale(fixture(), 0, { ...LIQUID_DISPLAY_DEFAULTS }).colorMap).toBe('jet')
  })

  it('keeps a colour map chosen in the colour bar when the quantity changes and comes back', () => {
    const raw = fixture()
    resetLiquidDisplayOptions()
    const domain = networkScale(raw, 0).domain!
    setLiquidDisplayOptions({ manual: { domain, colorMap: 'viridis' } })
    setLiquidDisplayOptions({ quantity: 'flow' })
    // The typed range belonged to temperature; the flow scale is its own again.
    expect(liquidDisplayOptions().manual).toBeNull()
    expect(networkScale(raw, 0).colorMap).toBe('viridis')
    setLiquidDisplayOptions({ quantity: 'temperature' })
    expect(networkScale(raw, 0).colorMap).toBe('viridis')
    expect(networkScale(raw, 0).source).toBe('global')
    setLiquidDisplayOptions({ quantity: 'flow' })
    setLiquidDisplayOptions({ manual: { domain: networkScale(raw, 0).domain!, colorMap: 'coolwarm' } })
    setLiquidDisplayOptions({ quantity: 'temperature' })
    expect(networkScale(raw, 0).colorMap).toBe('viridis')
    resetLiquidDisplayOptions()
    expect(networkScale(raw, 0).colorMap).toBe('jet')
  })

  it('keeps the arrows colour map across a quantity change', () => {
    const raw = fixture()
    resetLiquidDisplayOptions()
    const velocity = arrowScales(raw, 0).velocity!
    setLiquidDisplayOptions({ arrowManual: { domain: velocity, colorMap: 'jet' } })
    setLiquidDisplayOptions({ quantity: 'pressure' })
    expect(liquidDisplayOptions().arrowManual).toBeNull()
    expect(arrowScales(raw, 0).colorMap).toBe('jet')
    resetLiquidDisplayOptions()
  })
})
