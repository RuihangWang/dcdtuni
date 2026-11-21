/**
 * The ten colours a probe can be, in one place.
 *
 * A probe is identified by colour in three places at once: the ball in the
 * viewport, the curve in the monitor chart, and the dot on its row in 传感器位点.
 * All three index this palette by the probe's POSITION IN THE PROBE LIST, so
 * the colour survives a probe that has not reported yet — colouring by chart
 * order would shift every other curve the moment it appears.
 *
 * It lived twice, as `SENSOR_COLORS` in `sensorSpheres.ts` (the balls) and
 * `SERIES_COLORS` in `solverChart.ts` (the curves), which is the arrangement
 * where a change to one silently stops the colours meaning anything. Both names
 * are kept as re-exports; this is the definition.
 *
 * `window._smTempColors` in the legacy (STL_generate.html:25065).
 */
export const SENSOR_PALETTE = [
  '#e06c75',
  '#e5c07b',
  '#61afef',
  '#c678dd',
  '#56b6c2',
  '#98c379',
  '#d19a66',
  '#ff6b9d',
  '#4ecdc4',
  '#f7dc6f',
]

/** The colour of the probe at `index`, wrapping — and never NaN on a negative. */
export function paletteColor(index: number): string {
  const n = SENSOR_PALETTE.length
  return SENSOR_PALETTE[((index % n) + n) % n]!
}
