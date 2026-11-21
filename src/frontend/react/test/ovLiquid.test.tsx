import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { OvCommandClient } from '../src/renderer/ovProtocol'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import { liquidFrame, liquidGlobalDomains, mappedPipeTemperatures } from '../src/liquid/liquidTempColor'

const TESTDATA = resolve(__dirname, '../../shared/testdata')
const fixture = () => JSON.parse(readFileSync(resolve(TESTDATA, 'liquid_result_fixture.json'), 'utf8'))
const expected = () => JSON.parse(readFileSync(resolve(TESTDATA, 'liquid_colour_expected.json'), 'utf8'))

describe('liquid colour contract (Python liquid_colour.py <-> React liquidTempColor.ts)', () => {
  it('pipe temperatures match the shared contract at every sample', () => {
    const raw = fixture()
    const frames = expected().frames as Record<string, Record<string, { T: number }>>
    for (const [sample, targets] of Object.entries(frames)) {
      const pipes = mappedPipeTemperatures(raw, Number(sample))
      for (const [key, v] of Object.entries(targets)) {
        const [kind, uid] = key.split('|')
        if (kind !== 'pipe') continue
        expect(pipes.get(uid!)).toBeCloseTo(v.T, 9)
      }
    }
  })
})

describe('liquid colour contract — every part, T and colour', () => {
  it('liquidFrame reproduces the Python contract for all parts and both scales', () => {
    const raw = fixture()
    const frames = expected().frames as Record<string, Record<string, { T: number; rgb: number[]; group: string }> & { __domains__: Record<string, number[]> }>
    for (const [sample, exp] of Object.entries(frames)) {
      const fr = liquidFrame(raw, Number(sample))!
      const { __domains__: domains, ...targets } = exp
      expect(fr.domains).toEqual(domains)
      const got = Object.fromEntries(fr.targets.map(t => [[t.kind, t.modelUid, t.equipmentUid, t.liquidPart].join('|'), t]))
      expect(Object.keys(got).sort()).toEqual(Object.keys(targets).sort())
      for (const [key, v] of Object.entries(targets)) {
        expect(got[key]!.T).toBeCloseTo(v.T, 9)
        expect([(got[key]!.color >> 16) & 255, (got[key]!.color >> 8) & 255, got[key]!.color & 255]).toEqual(v.rgb)
      }
    }
  })
})

describe('liquid OV protocol', () => {
  it('sends exactly the bridge-validated key sets', async () => {
    const sent: { command_name: string; message: string }[] = []
    const client = new OvCommandClient(async payload => { sent.push(payload) })
    const grant = { payload: { kind: 'liquidResult' }, signature: 'f'.repeat(64) }
    void client.loadLiquidResult(grant).catch(() => {})
    void client.setLiquidFrame(3, null).catch(() => {})
    void client.clearLiquidColours().catch(() => {})
    await Promise.resolve(); await Promise.resolve()
    const keys = Object.fromEntries(sent.map(s => [s.command_name, Object.keys(JSON.parse(s.message)).sort()]))
    expect(keys).toEqual({ loadLiquidResult: ['grant', 'requestId'], setLiquidFrame: ['domains', 'requestId', 'sample'], clearLiquidColours: ['requestId'] })
    client.dispose()
  })

  it('names the display quantity and colour map only when asked to', async () => {
    const sent: { command_name: string; message: string }[] = []
    const client = new OvCommandClient(async payload => { sent.push(payload) })
    void client.setLiquidFrame(2, { network: [1, 2] }, { quantity: 'flow', colormap: 'viridis' }).catch(() => {})
    await Promise.resolve(); await Promise.resolve()
    const message = JSON.parse(sent.find(s => s.command_name === 'setLiquidFrame')!.message)
    expect(Object.keys(message).sort()).toEqual(['display', 'domains', 'requestId', 'sample'])
    expect(message.display).toEqual({ quantity: 'flow', colormap: 'viridis' })
    client.dispose()
  })


})

it('snapshots export the un-painted material while Kit colours liquid results', () => {
  const root = new THREE.Group()
  const original = new THREE.MeshStandardMaterial({ color: 0x2196f3 })
  const painted = new THREE.MeshStandardMaterial({ color: 0xff0000 })
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), painted)
  mesh.userData.modelId = 'pipe-1'
  root.add(mesh)
  const colour = (p: ReturnType<typeof buildSceneSnapshot>) => p.primitives[0]!.material.color
  expect(colour(buildSceneSnapshot({ root, project: { uid: 'p' } }))).toEqual([1, 0, 0])
  const orig = buildSceneSnapshot({ root, project: { uid: 'p' }, materialOf: m => (m === mesh ? original : undefined) })
  expect(colour(orig)[2]).toBeGreaterThan(0.8)
})

it('rack manifolds and their stubs export their side as liquidPart, and the schema accepts it', async () => {
  const { validateSceneSnapshot: validateScene } = await import('../src/renderer/validateScene')
  const root = new THREE.Group()
  const mk = (userData: Record<string, unknown>) => { const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial()); m.userData = userData; root.add(m); return m }
  mk({ isLiquidManifold: true, pipeType: 'supply', modelId: 'rack-1' })
  mk({ isLiquidManifold: true, stubType: 'return', modelId: 'rack-1' })
  mk({ modelId: 'rack-1' })
  const packet = buildSceneSnapshot({ root, project: { uid: 'p' } })
  expect(packet.primitives.map(p => p.liquidPart ?? null)).toEqual(['supply-manifold', 'return-manifold', null])
  expect(() => validateScene(packet)).not.toThrow()
  const bad = JSON.parse(JSON.stringify(packet)); bad.primitives[0].liquidPart = 'chiller'
  expect(() => validateScene(bad)).toThrow(/schema/i)
})


it('a fixed colour scale spans every sample of both scales', () => {
  const raw = fixture()
  const g = liquidGlobalDomains(raw)
  const frames = [0, 1, 2].map(s => liquidFrame(raw, s)!.domains)
  expect(g.network).toEqual([Math.min(...frames.map(f => f.network![0])), Math.max(...frames.map(f => f.network![1]))])
  expect(g.chips).toEqual([Math.min(...frames.map(f => f.chips![0])), Math.max(...frames.map(f => f.chips![1]))])
})
