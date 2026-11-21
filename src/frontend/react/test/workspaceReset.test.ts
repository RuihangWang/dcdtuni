import { describe, expect, it, vi } from 'vitest'
import { liquidRunState, runLiquidSolve, stopLiquidSolve } from '../src/liquid/liquidSession'
import {
  POSTPROCESS_DISPLAY_DEFAULTS,
  resetWorkspace,
  runWorkspaceReset,
  workspaceResetSteps,
  type WorkspaceResetStep,
  type WorkspaceResetWindow,
} from '../src/workspaceReset'

/**
 * The step list is the contract: every subsystem that survives a project load
 * has to be named here, and a broken step must not take the rest of the reset
 * with it. The steps themselves are one-line delegations to modules that carry
 * their own tests, so what is worth pinning is the coverage and the isolation.
 */

/** Every subsystem a load has to empty, by step name. */
const REQUIRED_STEPS = [
  'room2d',
  'postprocessScene',
  'postprocessDisplay',
  'postprocessPatchHighlight',
  'postprocessView',
  'temperatureUnit',
  'meshPreview',
  'blockMeshGrid',
  'meshWarnings',
  'solverMonitor',
  'sensorResults',
  'liquid',
  'liquidRuntime',
  'liquidTempColoring',
  'rackDetail',
  'seedSurface',
  'stlPreview',
]

describe('workspaceResetSteps', () => {
  it('covers every subsystem that outlives a project', () => {
    const names = workspaceResetSteps({}).map((s) => s.name)
    for (const required of REQUIRED_STEPS) expect(names).toContain(required)
  })

  it('exits the 2D editor first, before anything reads the project', () => {
    expect(workspaceResetSteps({}).map((s) => s.name)[0]).toBe('room2d')
  })

  it('empties the scene AFTER every store, or a mounted panel redraws into it', () => {
    const names = workspaceResetSteps({}).map((s) => s.name)
    // `resetPpView` notifies synchronously and a post-processing panel that is
    // still mounted answers by re-applying its layers, so a clear that ran
    // before it left the previous run's surface in the group.
    expect(names.indexOf('postprocessScene')).toBeGreaterThan(names.indexOf('postprocessView'))
    expect(names.indexOf('postprocessScene')).toBe(names.length - 3)
    // The patch overlay is not one of the objects `clear()` tracks, so it has
    // to be removed after the clear rather than before it.
    expect(names.indexOf('postprocessPatchHighlight')).toBeGreaterThan(
      names.indexOf('postprocessScene'),
    )
  })

  it('clears the post-processing scene through the viewport handle', () => {
    const clear = vi.fn()
    const win: WorkspaceResetWindow = { __ppViewport: { clear } }
    const step = workspaceResetSteps(win).find((s) => s.name === 'postprocessScene')!
    step.run()
    expect(clear).toHaveBeenCalledTimes(1)
  })

  it('puts the post-processing display sliders back to their defaults, but not the user\'s lighting', () => {
    const calls: Record<string, unknown> = {}
    const win: WorkspaceResetWindow = {
      __ppViewport: {
        setModelOpacity: (v) => (calls.model = v),
        setFieldOpacity: (v) => (calls.field = v),
        setLightIntensity: (v) => (calls.light = v),
        setAO: (v) => (calls.ao = v),
      },
    }
    workspaceResetSteps(win).find((s) => s.name === 'postprocessDisplay')!.run()
    expect(calls).toEqual({
      model: POSTPROCESS_DISPLAY_DEFAULTS.modelOpacity,
      field: POSTPROCESS_DISPLAY_DEFAULTS.fieldOpacity,
      ao: POSTPROCESS_DISPLAY_DEFAULTS.ao,
    })
    // Lighting is ui/lightingState, a user preference: a new project opens in
    // the light the user last chose.
    expect(calls.light).toBeUndefined()
  })

  it('detaches the mesh preview AND lifts the fade it left on the geometry', () => {
    const attached: unknown[] = []
    const opacity: number[] = []
    const win: WorkspaceResetWindow = {
      __attachMeshPreview: (g) => attached.push(g),
      __setGeometryOpacity: (v) => opacity.push(v),
    }
    workspaceResetSteps(win).find((s) => s.name === 'meshPreview')!.run()
    expect(attached).toEqual([null])
    expect(opacity).toEqual([1])
  })

  it('survives a viewport that has not been published yet', () => {
    const step = workspaceResetSteps({}).find((s) => s.name === 'postprocessScene')!
    expect(() => step.run()).not.toThrow()
  })

  it('leaves a running liquid solve alone, so its stop button still works', () => {
    const step = workspaceResetSteps({}).find((s) => s.name === 'liquid')!
    void runLiquidSolve({ device: 'cpu' }).catch(() => undefined)
    expect(liquidRunState().running).toBe(true)
    step.run()
    expect(liquidRunState().running).toBe(true)
    stopLiquidSolve()
    step.run()
    expect(liquidRunState()).toEqual({
      running: false,
      aborted: false,
      stage: null,
      progress: [],
      results: null,
      error: null,
    })
  })
})

describe('liquid run session and 3D colouring', () => {
  it('drops the previous project\'s runtime session, so the solver tab does not show it', async () => {
    const runtime = await import('../src/liquid/liquidRuntimeClient')
    const session = { run_id: 'run-old', status: 'completed', config_hash: 'h', metadata: { node_ids: [], edge_ids: [], cp_edge_ids: [],
      total_sample_count: 1, dt_s: 1, object_bindings: {} }, frame: { kind: 'frame', run_id: 'run-old', seq: 0, time_s: 0, node_pressure_Pa: [],
      node_temperature_K: [], edge_flow_kg_s: [], chip_temp_K: [], edge_outlet_temp_K: [], controls: {}, diagnostics: {} } }
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(session), { status: 200 }))
    await runtime.createRuntimeSession({}, [], 1)
    expect(runtime.runtimeState().session?.run_id).toBe('run-old')
    workspaceResetSteps({}).find((s) => s.name === 'liquidRuntime')!.run()
    expect(runtime.runtimeState().session).toBeNull()
    fetch.mockRestore()
  })

  it('switches the 3D temperature colouring off', async () => {
    const colour = await import('../src/liquid/liquidTempColor')
    const reset = vi.spyOn(colour, 'resetTempColoring')
    workspaceResetSteps({}).find((s) => s.name === 'liquidTempColoring')!.run()
    expect(reset).toHaveBeenCalled()
    reset.mockRestore()
  })
})

describe('resetWorkspace', () => {
  it('publishes the report, so a browser can tell a reset ran at all', () => {
    const win: WorkspaceResetWindow & { __lastWorkspaceReset?: unknown } = {}
    resetWorkspace(win)
    expect((win.__lastWorkspaceReset as { ran: string[] }).ran).toContain('postprocessView')
  })
})

describe('runWorkspaceReset', () => {
  it('runs every step and reports them', () => {
    const order: string[] = []
    const steps: WorkspaceResetStep[] = [
      { name: 'a', run: () => order.push('a') },
      { name: 'b', run: () => order.push('b') },
    ]
    expect(runWorkspaceReset(steps)).toEqual({ ran: ['a', 'b'], failed: [] })
    expect(order).toEqual(['a', 'b'])
  })

  it('keeps going after a step throws, and names the one that failed', () => {
    const steps: WorkspaceResetStep[] = [
      {
        name: 'boom',
        run: () => {
          throw new Error('no WebGL context')
        },
      },
      { name: 'after', run: () => undefined },
    ]
    const report = runWorkspaceReset(steps)
    expect(report.ran).toEqual(['after'])
    expect(report.failed).toEqual([{ name: 'boom', error: 'no WebGL context' }])
  })
})
