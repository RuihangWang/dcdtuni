/**
 * While a liquid experiment runs, the 3D view and the results panel show its
 * latest sample.
 *
 * The run manager rewrites the run's result.json after every step, so the
 * running result is re-read (`run-<id>`) whenever new frames arrived, at most
 * every LIVE_FOLLOW_MS and one read at a time; the shown sample stays "latest"
 * unless the timeline picked another. The same colouring and arrows then show
 * it — repainting overlays and rebuilding the arrows' own mesh, never the
 * scene.
 *
 * Omniverse: a new result changes the result time in the Omniverse scene key,
 * which would resend the whole scene (~45 MB on Shinaide) every step. So in
 * OV mode the result is followed only while Kit colours liquid results
 * itself (`liquidActive`, where the key is frozen and frames go through
 * setLiquidFrame).
 *
 * A finished run is liquidRunFollow's: it loads the completed result once.
 */

import { ovSession, subscribeOvSession } from '../renderer/ovSession'
import { liquidDisplayOptions, subscribeLiquidDisplay } from './liquidDisplayState'
import { selectedLiquidRun } from './liquidPostprocess'
import * as runtime from './liquidRuntimeClient'
import { fetchLiquidResults } from './liquidSession'

export const LIVE_FOLLOW_MS = 2000

interface RuntimeView { session: { run_id: string; status: string } | null; frames: readonly unknown[] }
export interface LiveFollowContext { live: boolean; selector: string; ovMode: 'three' | 'ov'; ovLiquidActive: boolean }

/** The run to follow now, or null. */
export function liveFollowRun(state: RuntimeView, ctx: LiveFollowContext): string | null {
  const s = state.session
  if (!ctx.live || !s || (s.status !== 'running' && s.status !== 'paused')) return null
  const dir = `run-${s.run_id}`
  if (ctx.selector !== '' && ctx.selector !== dir && !ctx.selector.endsWith(`/${dir}`)) return null
  if (ctx.ovMode === 'ov' && !ctx.ovLiquidActive) return null
  return s.run_id
}

export interface LiveFollowDeps {
  runtime: () => RuntimeView
  subscribe: (fn: () => void) => () => void
  context: () => LiveFollowContext
  fetch: (runDir: string) => Promise<unknown>
  now?: () => number
}

function defaultDeps(): LiveFollowDeps {
  return {
    runtime: () => runtime.runtimeState(),
    subscribe: fn => {
      const offs = [runtime.subscribeRuntime(fn), subscribeLiquidDisplay(fn), subscribeOvSession(fn)]
      return () => offs.forEach(off => off())
    },
    context: () => ({
      live: liquidDisplayOptions().live, selector: selectedLiquidRun(),
      ovMode: ovSession().mode, ovLiquidActive: ovSession().liquidActive,
    }),
    fetch: dir => fetchLiquidResults(dir),
  }
}

/** Start following; returns the stop. */
export function startLiquidLiveFollow(deps: LiveFollowDeps = defaultDeps()): () => void {
  const now = deps.now ?? (() => Date.now())
  let lastRead = -Infinity
  let readRun: string | null = null
  let readFrames = -1
  let inFlight = false
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false

  const due = () => {
    const state = deps.runtime()
    const run = liveFollowRun(state, deps.context())
    return run !== null && (run !== readRun || state.frames.length !== readFrames) ? run : null
  }
  const read = async () => {
    timer = null
    const run = due()
    if (stopped || inFlight || !run) return
    inFlight = true
    lastRead = now()
    readRun = run
    readFrames = deps.runtime().frames.length
    try { await deps.fetch(`run-${run}`) } catch { /* the next frames try again */ } finally {
      inFlight = false
      if (!stopped) schedule()
    }
  }
  const schedule = () => {
    if (stopped || inFlight || timer !== null || !due()) return
    timer = setTimeout(() => { void read() }, Math.max(0, lastRead + LIVE_FOLLOW_MS - now()))
  }
  const off = deps.subscribe(schedule)
  schedule()
  return () => {
    stopped = true
    off()
    if (timer !== null) clearTimeout(timer)
    timer = null
  }
}
