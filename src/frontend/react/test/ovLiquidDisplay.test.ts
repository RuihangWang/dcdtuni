import { describe, expect, it } from 'vitest'
import { OvLiquidColouring, type OvLiquidDeps } from '../src/liquid/ovLiquidDisplay'

type Call = [string, ...unknown[]]
function harness(over: Partial<OvLiquidDeps> = {}, ackRun = 'abc') {
  const calls: Call[] = []
  let active = false
  let samples = 3
  const pending: (() => void)[] = []
  let hold = false
  const client = {
    loadLiquidResult: async (grant: unknown) => {
      calls.push(['load', grant])
      return { requestId: 'r', ok: true, liquid: { runId: ackRun, solver: 'differentiable_newton_dae', status: 'completed', samples, times: [0, 20], targets: { pipe: 4 }, projectUid: 'p', projectContentMatches: true } }
    },
    setLiquidFrame: async (sample: number, domains: unknown, display?: unknown) => {
      calls.push(['frame', sample, domains, display])
      if (hold) await new Promise<void>(r => pending.push(r))
      return { requestId: 'r', ok: true, liquidFrame: { sample: Math.max(0, sample), timeSeconds: 20, groups: {}, targets: { pipe: 4 }, painted: 9, unmatched: {} } }
    },
    clearLiquidColours: async () => { calls.push(['clear']); return { requestId: 'r', ok: true, cleared: 9 } },
  }
  const raw: Record<string, unknown> = { run_id: 'abc', object_bindings: { m_31: {} }, cell_edge_ids: ['e'], time_s: [0, 10, 20] }
  const deps: OvLiquidDeps = {
    client: () => client as never,
    results: () => raw,
    sample: () => -1,
    paint: () => ({ domains: { network: [300, 320], chips: [310, 330] }, display: { quantity: 'flow', colormap: 'viridis', arrows: null } }),
    grant: async runId => { calls.push(['grant', runId]); return { payload: { runId }, signature: 'f'.repeat(64) } },
    sceneIdle: () => async () => { calls.push(['idle']) },
    setActive: on => { active = on },
    ...over,
  }
  return {
    calls, deps, raw, colouring: new OvLiquidColouring(deps),
    active: () => active, setSamples: (n: number) => { samples = n },
    hold: (on: boolean) => { hold = on }, release: () => pending.splice(0).forEach(r => r()),
  }
}

describe('Omniverse colours the liquid result itself', () => {
  it('waits for the scene, asks the server for a grant, loads it and paints the shown frame', async () => {
    const h = harness()
    await h.colouring.enable()
    expect(h.calls.map(c => c[0])).toEqual(['idle', 'grant', 'load', 'frame'])
    expect(h.calls[1]).toEqual(['grant', 'abc'])
    expect(h.calls[3]).toEqual(['frame', -1, { network: [300, 320], chips: [310, 330] }, { quantity: 'flow', colormap: 'viridis', arrows: null }])
    expect(h.active()).toBe(true)
    expect(h.colouring.state.status).toBe('on')
    expect(h.colouring.state.frame?.painted).toBe(9)
  })

  it('sends only the latest frame while one is in flight', async () => {
    const h = harness()
    await h.colouring.enable()
    h.hold(true)
    let sample = 0
    h.deps.sample = () => sample
    const first = h.colouring.sync()
    sample = 1; void h.colouring.sync()
    sample = 2; void h.colouring.sync()
    h.hold(false)
    h.release()
    await first
    await new Promise(r => setTimeout(r, 0))
    const frames = h.calls.filter(c => c[0] === 'frame').map(c => c[1])
    expect(frames).toEqual([-1, 0, 2])
  })

  it('re-grants when the shown run gains samples (a running experiment) or changes', async () => {
    const h = harness()
    await h.colouring.enable()
    h.raw.time_s = [0, 10, 20, 30]
    h.setSamples(4)
    await h.colouring.sync()
    expect(h.calls.filter(c => c[0] === 'grant')).toHaveLength(2)
  })

  it('refuses an acknowledgement for another run, and gives the scene back', async () => {
    const h = harness({}, 'other')
    await h.colouring.enable()
    expect(h.colouring.state.status).toBe('error')
    expect(h.active()).toBe(false)
  })

  it('clears Kit\'s colours when switched off', async () => {
    const h = harness()
    await h.colouring.enable()
    await h.colouring.disable()
    expect(h.calls.at(-1)).toEqual(['clear'])
    expect(h.active()).toBe(false)
    expect(h.colouring.state.status).toBe('off')
  })

  it('leaves results without object bindings alone (an older solver)', async () => {
    const h = harness()
    delete h.raw.object_bindings
    await h.colouring.enable()
    expect(h.calls).toEqual([])
    expect(h.colouring.state.status).toBe('unbound')
  })
})

describe('the editor\'s display choice for Kit', () => {
  it('sends the arrows\' options and scales when the arrows are on, and none when off', async () => {
    const { ovArrowOptions } = await import('../src/liquid/ovLiquidDisplay')
    const scales = { mass: [0.004, 0.5] as [number, number], velocity: [0.001, 0.07] as [number, number], colorMap: 'viridis' as const, source: 'global' as const }
    const on = { arrows: true, density: 2, speedScale: 5, animate: true, arrowColor: 'velocity' as const }
    expect(ovArrowOptions(on, scales)).toEqual({ density: 2, speedScale: 5, animate: true, colour: 'velocity', mass: [0.004, 0.5], velocity: [0.001, 0.07], colormap: 'viridis' })
    expect(ovArrowOptions({ ...on, arrows: false }, scales)).toBeNull()
  })
})
