/**
 * Results on the timeline (transient design §2 "同一播放头可擦洗结果", plan T5).
 *
 * In a transient project with a transient result loaded, moving the playhead
 * shows the result sample nearest its time; the result's own time control moves
 * the playhead too. Results follow the playhead when it moves. A fresh run opens at its latest
 * sample and moves the project clock to that sample. The liquid sample lives in
 * `liquidTempColor` (Three.js and Omniverse colour follow it); the air frame is
 * chosen by the post-processing panel from `nearestIndex`.
 */
import * as liquid from '../liquid/liquidSession'
import * as color from '../liquid/liquidTempColor'
import { isTransient, parseLiquidResults } from '../liquid/liquidResults'
import * as store from '../projectStore'
import { transientOf } from './model'

/** Index of the time nearest `t`; the earlier one on a tie. -1 for no times. Times ascend. */
export function nearestIndex(times: readonly number[], t: number): number {
  let best = -1
  let gap = Infinity
  times.forEach((time, i) => {
    const d = Math.abs(time - t)
    if (d < gap - 1e-9 * Math.max(1, Math.abs(t))) {
      best = i
      gap = d
    }
  })
  return best
}

/** The playhead drives results only in a transient project (a steady one has one time). */
export function projectIsTransient(): boolean {
  const project = store.getProject()
  return !!project && transientOf(project).mode === 'transient'
}

type LiquidDoc = { run_id?: string; time_s?: unknown } | null

function liquidTimes(): number[] | null {
  const raw = liquid.liquidRunState().results
  const times = (raw as LiquidDoc)?.time_s
  if (!Array.isArray(times) || times.length === 0
    || !times.every((time) => typeof time === 'number' && Number.isFinite(time))) return null
  if (times.length > 1) return times as number[]
  // A cancelled or just initialized transient has only its first accepted
  // sample. Its clock must not retain a preceding run's terminal time.
  const parsed = parseLiquidResults(raw)
  return parsed && isTransient(parsed) ? times as number[] : null
}

let picking = false

/** The result's own time control: show sample `i` and move the playhead to its time. */
export function pickLiquidSample(i: number): void {
  color.setResultSample(i)
  const times = liquidTimes()
  if (!times || !projectIsTransient() || times[i] === undefined) return
  picking = true
  try {
    store.setPlayhead(times[i]!)
  } finally {
    picking = false
  }
}

function onPlayhead() {
  if (picking || !projectIsTransient()) return
  const times = liquidTimes()
  if (!times) return
  const i = nearestIndex(times, store.playheadTime())
  const shown = color.selectedResultSample()
  if (i !== (shown < 0 ? times.length - 1 : shown)) color.setResultSample(i)
}

let lastRun: string | undefined

function onLiquid() {
  // A new run opens at its latest sample; live frames of the same run keep the pick.
  const run = (liquid.liquidRunState().results as LiquidDoc)?.run_id
  if (run !== lastRun) {
    lastRun = run
    if (color.selectedResultSample() !== -1) color.setResultSample(-1)
    const times = liquidTimes()
    if (times && projectIsTransient()) {
      // A newly opened result is already shown at its latest physical sample.
      // Move the project clock with it; don't let that update turn -1 into a
      // separate manual sample pick.
      picking = true
      try {store.setPlayhead(times[times.length - 1]!)}
      finally {picking = false}
    }
  }
}

let stop: (() => void) | null = null

/** Start following the playhead (idempotent). Returns the unsubscribe. App mount calls it. */
export function startResultPlayback(): () => void {
  if (!stop) {
    lastRun = (liquid.liquidRunState().results as LiquidDoc)?.run_id
    const offPlayhead = store.subscribePlayhead(onPlayhead)
    const offLiquid = liquid.subscribeLiquid(onLiquid)
    stop = () => {
      offPlayhead()
      offLiquid()
      stop = null
    }
  }
  return stop
}
