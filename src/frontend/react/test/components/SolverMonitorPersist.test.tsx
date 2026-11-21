import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getMonitoringConfig, setMonitoringConfig } from '../../src/run/monitoring'
import { SolverMonitorPanel } from '../../src/components/SolverMonitorPanel'

/**
 * Monitoring ticks survive leaving the solver tab.
 *
 * The choices here become OpenFOAM function objects in the .cfdrun, so losing
 * them is not a UI annoyance: the case solves and writes no probe data, and the
 * convergence plot comes back empty with nothing explaining why.
 *
 * React unmounts this panel on every tab switch — the 传感器位点 sub-tab
 * included — and it held both selections in component state seeded from empty
 * Sets. So twenty ticked server patches came back blank; worse, the publish
 * effect fired ON MOUNT while the patch list was still loading and wrote that
 * emptiness over the module's config, destroying the selection rather than just
 * failing to draw it. The legacy keeps the checkboxes in static DOM that is
 * only hidden (STL_generate.html:1052).
 */

const PATCHES = {
  patches: [
    { patch: 'Rack_1_srv1_outlet', shortId: 'r1s1', type: 'server' },
    { patch: 'Rack_2_srv1_outlet', shortId: 'r2s1', type: 'server' },
  ],
  acuReturnPatches: [{ patch: 'ACU_1_Return', shortId: 'a1', type: 'acu' }],
}

let host: HTMLDivElement
let root: Root

/** Let the panel's `/api/server-patches` promise land. */
const settle = async () => {
  for (let i = 0; i < 3; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0))
}

function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  flushSync(() => root.render(<SolverMonitorPanel />))
}

function unmount() {
  flushSync(() => root.unmount())
  host.remove()
}

function boxes(): HTMLInputElement[] {
  return [...host.querySelectorAll<HTMLInputElement>('input[data-patch]')]
}

function tick(patch: string) {
  const box = boxes().find((b) => b.dataset.patch === patch)!
  flushSync(() => box.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

beforeEach(() => {
  setMonitoringConfig({ residuals: true, serverTemperature: [], acuReturnTemperature: [] })
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ json: async () => PATCHES })) as unknown as typeof fetch,
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the monitoring panel', () => {
  it('publishes what was ticked', async () => {
    mount()
    await settle()
    flushSync(() => {})
    tick('Rack_1_srv1_outlet')

    expect(getMonitoringConfig().serverTemperature.map((p) => p.patch)).toEqual([
      'Rack_1_srv1_outlet',
    ])
    unmount()
  })

  it('keeps the selection through an unmount and remount', async () => {
    mount()
    await settle()
    tick('Rack_1_srv1_outlet')
    tick('ACU_1_Return')
    unmount()

    // A tab switch: the panel is gone, and the config must not be.
    expect(getMonitoringConfig().serverTemperature.map((p) => p.patch)).toEqual([
      'Rack_1_srv1_outlet',
    ])
    // The only return, ticked: every return, kept symbolic so a later ACU is monitored too.
    expect(getMonitoringConfig().acuReturnTemperature).toBe('all')

    mount()
    await settle()
    expect(boxes().find((b) => b.dataset.patch === 'Rack_1_srv1_outlet')!.checked).toBe(true)
    expect(boxes().find((b) => b.dataset.patch === 'Rack_2_srv1_outlet')!.checked).toBe(false)
    expect(boxes().find((b) => b.dataset.patch === 'ACU_1_Return')!.checked).toBe(true)
    unmount()
  })

  it('does not wipe the config while the patch list is still loading', async () => {
    setMonitoringConfig({
      residuals: false,
      serverTemperature: [PATCHES.patches[0]!],
      acuReturnTemperature: [],
    })
    mount()
    // Deliberately BEFORE the fetch resolves: the mount-time publish used to
    // fire here with empty lists and destroy the selection.
    expect(getMonitoringConfig().serverTemperature).toHaveLength(1)
    await settle()
    expect(getMonitoringConfig().serverTemperature.map((p) => p.patch)).toEqual([
      'Rack_1_srv1_outlet',
    ])
    unmount()
  })

  it('remembers the residual toggle too', async () => {
    setMonitoringConfig({ residuals: false, serverTemperature: [], acuReturnTemperature: [] })
    mount()
    await settle()
    const residual = host.querySelector<HTMLInputElement>('input[type="checkbox"]:not([data-patch])')
    expect(residual?.checked).toBe(false)
    unmount()
  })

  it('drops a patch that the case no longer has', async () => {
    setMonitoringConfig({
      residuals: true,
      serverTemperature: [{ patch: 'Deleted_Rack_outlet', shortId: 'x', type: 'server' }],
      acuReturnTemperature: [],
    })
    mount()
    await settle()
    // The lists come from the CASE's boundary file. A remembered name that is
    // no longer in it must not travel into the .cfdrun as a probe on a patch
    // OpenFOAM does not have.
    expect(getMonitoringConfig().serverTemperature).toEqual([])
    unmount()
  })
})


it('keeps distinct outlet patch labels and real ACU return identities selectable', async () => {
  const response = {
    patches: [
      { patch: 'Rack_1_srv1_outlet_p0', shortId: 'Rack_1_srv1_p0', type: 'outlet' },
      { patch: 'Rack_1_srv1_outlet_p1', shortId: 'Rack_1_srv1_p1', type: 'outlet' },
    ],
    acuReturnPatches: [1, 2, 3, 4].map(i => ({
      patch: 'ACU_0' + i + '_Back_patch', shortId: 'ACU_0' + i, type: 'return',
    })),
  }
  vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => response })))
  mount()
  await settle()
  const first = boxes().find(b => b.dataset.patch === 'Rack_1_srv1_outlet_p0')!
  const second = boxes().find(b => b.dataset.patch === 'Rack_1_srv1_outlet_p1')!
  expect(first.closest('label')!.textContent).toContain('Rack_1_srv1_p0')
  expect(second.closest('label')!.textContent).toContain('Rack_1_srv1_p1')
  expect(first.closest('label')!.title).toBe('Rack_1_srv1_outlet_p0')
  expect(second.closest('label')!.title).toBe('Rack_1_srv1_outlet_p1')
  expect(host.querySelectorAll('#sm-acu-return-list input[data-patch]')).toHaveLength(4)
  tick('Rack_1_srv1_outlet_p1')
  tick('ACU_04_Back_patch')
  expect(getMonitoringConfig().serverTemperature.map(p => p.patch)).toEqual(['Rack_1_srv1_outlet_p1'])
  expect(getMonitoringConfig().acuReturnTemperature).toEqual([response.acuReturnPatches[3]])
  unmount()
})

it('starts with every ACU return ticked, as the scripted API does', async () => {
  const response = {
    patches: [],
    acuReturnPatches: [1, 2, 3].map(i => ({ patch: 'ACU_0' + i + '_Back_patch', shortId: 'ACU_0' + i, type: 'return' })),
  }
  setMonitoringConfig({ residuals: true, serverTemperature: [], acuReturnTemperature: 'all' })
  vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => response })))
  mount()
  await settle()
  expect(boxes().map(b => b.checked)).toEqual([true, true, true])
  expect(getMonitoringConfig().acuReturnTemperature).toBe('all')
  tick('ACU_02_Back_patch')
  expect(getMonitoringConfig().acuReturnTemperature).toEqual([response.acuReturnPatches[0], response.acuReturnPatches[2]])
  tick('ACU_02_Back_patch')
  expect(getMonitoringConfig().acuReturnTemperature).toBe('all')
  unmount()
})

it('keeps the default when the case has no mesh to list returns from', async () => {
  setMonitoringConfig({ residuals: true, serverTemperature: [], acuReturnTemperature: 'all' })
  vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => ({ patches: [], acuReturnPatches: [] }) })))
  mount()
  await settle()
  expect(getMonitoringConfig().acuReturnTemperature).toBe('all')
  unmount()
})
