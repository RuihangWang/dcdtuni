/**
 * A live experiment that finishes becomes the shown liquid result.
 *
 * The experiment runs through `liquidRuntimeClient`, which keeps its own state;
 * the results panel, the result colouring and the playhead read `liquidSession`.
 * Nothing carried one into the other, so a finished run was on disk — the newest
 * one — while the panel kept the run it had loaded before, until 🔄 was pressed.
 *
 * Only a selector left on the newest run ('') or pointing at this very run
 * follows it: a historical run the user picked stays shown. The run is read by
 * its directory (`run-<run_id>`), not as "the newest", because a run prepared
 * afterwards is newer on disk from the moment it exists. A new run_id also sends
 * the shown sample back to the latest (resultPlayback).
 *
 * Keyed on the first sight of a completed run, not on a running → completed
 * change: a steady DAE run is already complete when the server accepts it.
 * Cancelled and failed runs are not followed.
 */
import { selectedLiquidRun } from './liquidPostprocess'
import * as runtime from './liquidRuntimeClient'
import { fetchLiquidResults } from './liquidSession'

let followed: string | null = null

function completedRun(): string | null {
  const session = runtime.runtimeState().session
  return session?.status === 'completed' ? session.run_id : null
}

function onRuntime() {
  const run = completedRun()
  if (!run || run === followed) return
  followed = run
  const dir = `run-${run}`
  const selected = selectedLiquidRun()
  if (selected === '' || selected === dir) void fetchLiquidResults(dir)
}

let stop: (() => void) | null = null

/** Start following finished experiments (idempotent). Returns the unsubscribe. App mount calls it. */
export function startLiquidRunFollow(): () => void {
  if (!stop) {
    // A run that had already finished when following started is not news.
    followed = completedRun()
    const off = runtime.subscribeRuntime(onRuntime)
    stop = () => {
      off()
      stop = null
    }
  }
  return stop
}
