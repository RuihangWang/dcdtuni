/**
 * The dock's rows, capped by LINE.
 *
 * A streamed job log reaches the store as batches of up to 250 lines each, so a
 * cap on entries alone still allows 75 000 rows. At that size every new log
 * entry re-rendered them and the dock's follow-the-tail `scrollHeight` read
 * forced a layout of all of them, which froze the page after a large mesh: the
 * solve the user then started never reached the server.
 */
export const MAX_RENDERED_LINES = 2000

export interface LogRowEntry {
  at: number
  action: string
  detail: string
}

export interface LogRow<E extends LogRowEntry> {
  entry: E
  line: string
}

/** `entries` are newest first, as the store keeps them; `rows` are oldest first. */
export function logRows<E extends LogRowEntry>(
  entries: readonly E[],
  maxLines: number = MAX_RENDERED_LINES,
): { rows: LogRow<E>[]; hiddenOlder: number } {
  const newestFirst: LogRow<E>[] = []
  let shownEntries = 0
  for (const entry of entries) {
    const room = maxLines - newestFirst.length
    if (room <= 0) break
    const lines = entry.detail ? entry.detail.split('\n') : ['']
    for (let i = lines.length - 1; i >= Math.max(0, lines.length - room); i--) {
      newestFirst.push({ entry, line: lines[i] ?? '' })
    }
    if (lines.length > room) break
    shownEntries++
  }
  return { rows: newestFirst.reverse(), hiddenOlder: entries.length - shownEntries }
}
