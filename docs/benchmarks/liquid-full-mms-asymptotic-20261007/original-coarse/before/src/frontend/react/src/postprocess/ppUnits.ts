/**
 * Temperature units for the post-processing panel.
 *
 * The solver works in Kelvin and so does every colour limit that reaches a
 * shader. Only what the user READS is converted — the clim boxes and the
 * colorbar ticks — which is why the conversion lives here rather than in the
 * decode: convert on the way in and a 22 °C limit would be handed to the shader
 * as 22 K, colouring the whole room at the top of the ramp.
 *
 * Ported from `_ppTempUnit` / `_ppK2Display` / `_ppDisplay2K`
 * (STL_generate.html:21585), Celsius by default, same as the legacy editor.
 */

export type TempUnit = 'C' | 'K' | 'F'

type Listener = () => void
const listeners = new Set<Listener>()

let unit: TempUnit = 'C'

export function subscribeTempUnit(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function tempUnit(): TempUnit {
  return unit
}

export function setTempUnit(next: TempUnit) {
  unit = next
  listeners.forEach((fn) => fn())
}

/**
 * Whether a field is a temperature.
 *
 * Only `T`. `alphat` and `nut` are diffusivities that happen to sit beside it
 * in the same time directory, and converting one of those would silently
 * subtract 273.15 from a number that was never in Kelvin.
 */
export function isTemperatureField(name: string): boolean {
  return name === 'T'
}

export function k2display(k: number, selectedUnit: TempUnit = unit): number {
  if (selectedUnit === 'C') return k - 273.15
  if (selectedUnit === 'F') return ((k - 273.15) * 9) / 5 + 32
  return k
}

export function display2k(v: number, selectedUnit: TempUnit = unit): number {
  if (selectedUnit === 'C') return v + 273.15
  if (selectedUnit === 'F') return ((v - 32) * 5) / 9 + 273.15
  return v
}

export function tempUnitLabel(selectedUnit: TempUnit = unit): string {
  if (selectedUnit === 'C') return '°C'
  if (selectedUnit === 'F') return '°F'
  return 'K'
}

/** Convert only when the field is a temperature. */
export function toDisplay(field: string, raw: number): number {
  return isTemperatureField(field) ? k2display(raw) : raw
}

export function fromDisplay(field: string, shown: number): number {
  return isTemperatureField(field) ? display2k(shown) : shown
}

/** Show small fields and close limits without rounding both ends to zero. */
export function formatColorbarTick(value: number, min: number, max: number): string {
  if (!Number.isFinite(value)) return '—'
  if (value === 0) return '0'
  const span = Math.abs(max - min)
  const magnitude = Math.max(Math.abs(min), Math.abs(max), Math.abs(value))
  const resolution = span > 0 && Number.isFinite(span) ? span : magnitude
  const decimals = Math.max(1, 2 - Math.floor(Math.log10(resolution)))
  if (decimals <= 8 && magnitude < 1e7) return value.toFixed(decimals)
  const significant = Math.max(3, Math.min(15, Math.ceil(Math.log10(magnitude / resolution)) + 3))
  return value.toExponential(significant - 1)
}

/** The air solver writes kinematic pressure, so p and p_rgh are not Pa. */
export function scalarFieldUnit(field: string): string {
  if (['U', 'Umag', 'Ux', 'Uy', 'Uz'].includes(field)) return 'm/s'
  if (['p', 'p_rgh', 'k'].includes(field)) return 'm²/s²'
  if (['nut', 'alphat'].includes(field)) return 'm²/s'
  if (field === 'epsilon') return 'm²/s³'
  return ''
}

export function formatColorbarRange(field: string, min: number, max: number): [string, string] {
  const lo = toDisplay(field, min)
  const hi = toDisplay(field, max)
  return [formatColorbarTick(lo, lo, hi), formatColorbarTick(hi, lo, hi)]
}
