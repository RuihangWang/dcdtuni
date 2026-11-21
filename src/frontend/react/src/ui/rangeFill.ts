/** Keeps `--fill` on every range input at its value's share of the track, for the filled-track style. */

function sync(el: HTMLInputElement): void {
  const min = Number(el.min || 0), max = Number(el.max || 100), v = Number(el.value)
  const fill = max > min ? `${Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100))}%` : '0%'
  if (el.style.getPropertyValue('--fill') !== fill) el.style.setProperty('--fill', fill)
}

function syncAll(): void {
  document.querySelectorAll<HTMLInputElement>('input[type="range"]').forEach(sync)
}

export function installRangeFill(): void {
  const onEvent = (e: Event) => {
    const el = e.target
    if (el instanceof HTMLInputElement && el.type === 'range') sync(el)
  }
  document.addEventListener('input', onEvent, true)
  document.addEventListener('change', onEvent, true)
  // Values set from code (undo, a new selection, React re-renders) raise no event.
  setInterval(syncAll, 300)
  syncAll()
}
