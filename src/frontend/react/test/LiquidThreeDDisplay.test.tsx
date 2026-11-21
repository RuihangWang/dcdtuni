import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import * as THREE from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LiquidResultsPanel } from '../src/components/LiquidResultsPanel'
import { getLocale, setLocale } from '../src/i18n'
import { fetchLiquidResults, resetLiquidRun } from '../src/liquid/liquidSession'
import * as color from '../src/liquid/liquidTempColor'
import { resetLiquidDisplayOptions, liquidDisplayOptions } from '../src/liquid/liquidDisplayState'
import { setLiquidFlowTab } from '../src/liquid/liquidFlowDisplay'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'

vi.mock('../src/components/TimeSeriesChart', () => ({ TimeSeriesChart: () => <div /> }))

const TESTDATA = resolve(__dirname, '../../shared/testdata')
type Bag = Record<string, any>
const fixture = (): Bag => JSON.parse(readFileSync(resolve(TESTDATA, 'liquid_flow_fixture.json'), 'utf8'))
const node = (raw: Bag, id: string) => raw.node_positions_m[raw.node_ids.indexOf(id)] as number[]

let host: HTMLDivElement, root: Root
const originalLocale = getLocale()
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

/** The scene the panel paints: the trunk tube m_31, as the shared renderer builds it. */
function scene(raw: Bag): THREE.Mesh {
  const route = ['ring_supply_feed_tee_junction', 'm_31_tee_tee_1778059219789', 'm_31_tee_tee_1778059216039']
    .map(id => new THREE.Vector3(...node(raw, id)))
  const path = new THREE.CurvePath<THREE.Vector3>()
  for (let i = 1; i < route.length; i++) path.add(new THREE.LineCurve3(route[i - 1]!, route[i]!))
  const mesh = new THREE.Mesh(new THREE.TubeGeometry(path, 24, 0.0125, 8, false), new THREE.MeshStandardMaterial({ color: 0x2196f3 }))
  mesh.userData = { isPipe: true, pipeModelId: 'm_31', _pipeDisplaySource: { context: { path, segments: 24 }, radius: 0.0125 } }
  const group = new THREE.Group()
  group.add(mesh)
  ;(window as unknown as Bag).globalGroup = group
  return mesh
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  ;(globalThis as unknown as Bag).THREE = THREE
  act(() => { setLocale('zh-CN'); resetLiquidRun(); color.resetTempColoring(); resetLiquidDisplayOptions() })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => { root.unmount(); setLocale(originalLocale); color.resetTempColoring(); resetLiquidRun(); resetLiquidDisplayOptions(); setLiquidModeActive(false); setLiquidFlowTab('geometry') })
  host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
  delete (window as unknown as Bag).globalGroup
  document.querySelectorAll('.pp-colorbar').forEach(e => e.remove())
})
async function show(body: unknown) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(body))
  await act(async () => { await fetchLiquidResults(); root.render(<LiquidResultsPanel />) })
}
const select = (id: string, value: string) => act(() => {
  const el = host.querySelector<HTMLSelectElement>(`#${id}`)!
  el.value = value
  el.dispatchEvent(new Event('change', { bubbles: true }))
})

describe('the liquid results 3D display', () => {
  it('colours the network in the chosen quantity, with a legend naming it and its unit', async () => {
    const raw = fixture()
    const mesh = scene(raw)
    await show(raw)
    expect(host.querySelector('#lc-3d-display')).not.toBeNull()
    act(() => host.querySelector<HTMLInputElement>('#lc-color-pipes')!.click())
    expect(color.isPipeColoringOn()).toBe(true)
    expect((mesh.material as THREE.MeshStandardMaterial).vertexColors).toBe(true)
    const bar = () => document.querySelector('#lc-network-colorbar .pp-colorbar-field')?.textContent
    expect(bar()).toContain('管网温度')
    expect(host.querySelector('#lc-scale-range')!.textContent).toContain('°C')
    select('lc-quantity', 'flow')
    expect(liquidDisplayOptions().quantity).toBe('flow')
    expect(bar()).toBe('管网流量 (L/min)')
    // Flow in L/min with kg/s beside it.
    expect(host.querySelector('#lc-scale-range')!.textContent).toMatch(/L\/min（.* kg\/s）/)
    select('lc-quantity', 'velocity')
    expect(bar()).toBe('管网流速 (m/s)')
    select('lc-quantity', 'pressure')
    expect(host.querySelector('#lc-scale-range')!.textContent).toContain('绝对压力')
  })

  it('keeps the section under every view, not only the overview', async () => {
    await show(fixture())
    act(() => host.querySelector<HTMLButtonElement>('[data-lq-view="network"]')!.click())
    expect(host.querySelector('#lc-3d-display')).not.toBeNull()
  })

  it('draws flow arrows only on the post-processing tab in liquid mode', async () => {
    const raw = fixture()
    scene(raw)
    act(() => { setLiquidModeActive(true); setLiquidFlowTab('postprocess') })
    await show(raw)
    const summary = () => host.querySelector<HTMLElement>('#lc-arrows-summary')!
    act(() => select('lc-density', '1'))
    expect(Number(summary().dataset.arrows)).toBeGreaterThan(0)
    const anim = (window as unknown as { liquidFlowAnim: { isVisible(): boolean; count(): number } }).liquidFlowAnim
    expect(anim.isVisible()).toBe(true)
    expect(anim.count()).toBe(Number(summary().dataset.arrows))
    act(() => setLiquidFlowTab('geometry'))
    expect(anim.isVisible()).toBe(false)
    act(() => setLiquidFlowTab('postprocess'))
    act(() => host.querySelector<HTMLInputElement>('#lc-arrows')!.click())
    expect(anim.isVisible()).toBe(false)
  })
})
