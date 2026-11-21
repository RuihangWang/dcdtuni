/**
 * Post-processing's line in the bottom Operation Log.
 *
 * Every long action in the editor reports there, and post-processing is the one
 * that most needs to: what a slice request actually returned, how wide the
 * field's range was, how many streamlines came back. The panel's own status
 * text is transient — the next request overwrites it — so without a log line
 * the answer is gone by the time a user asks what happened.
 *
 * ## Why this is not just `store.logOperation`
 *
 * `logOperation` pushes onto the array and stops there. The REPAINT comes from
 * the store's private `emit()`, which the project mutations call after logging.
 * Post-processing mutates no project — it is view-only state by design — so a
 * line written through `logOperation` alone sits in the array unseen until some
 * unrelated edit happens to repaint the panel. `logAction` is the store's
 * log-and-repaint pair; while it is absent the line is still recorded and shows
 * up on the log panel's next render.
 */

import * as store from '../projectStore'
import { formatColorbarTick, isTemperatureField } from './ppUnits'

interface LogAndRepaint {
  logAction?: (action: string, detail?: string) => void
}

/** The legacy prefixes: `PP: …` for the panel, `PP AI: …` for the inference. */
export type PpLogChannel = 'PP' | 'PP AI'

export function ppLog(detail: string, channel: PpLogChannel = 'PP'): void {
  const repaint = (store as unknown as LogAndRepaint).logAction
  if (repaint) repaint(channel, detail)
  else store.logOperation(channel, detail)
}

/**
 * The one-line description of a finished volume render.
 *
 * `128x128x128, T: 21.0~37.0` — the grid it was resampled onto and the range it
 * spans, in the user's display unit. Both halves matter: the resolution is what
 * the seconds were spent on, and the range is what the colours mean. Ported
 * from the summary the legacy builds at STL_generate.html:20344.
 */
export function volumeSummary(
  dimensions: readonly number[],
  field: string,
  min: number,
  max: number,
): string {
  const dims = [dimensions[0] ?? 0, dimensions[1] ?? 0, dimensions[2] ?? 0].join('x')
  const format = (value: number) => isTemperatureField(field)
    ? value.toFixed(1) : formatColorbarTick(value, min, max)
  return `${dims}, ${field}: ${format(min)}~${format(max)}`
}
