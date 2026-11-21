/** Max / min markers of the drawn fields: the surface over its visible patches, each slice over its own points. */

export interface Extreme {
  value: number
  position: [number, number, number]
}

export interface ExtremePair {
  min: Extreme
  max: Extreme
}

export function extremePoints(positions: Float32Array, values: Float32Array,
  indices?: Uint32Array | null): ExtremePair | null {
  let lo = -1
  let hi = -1
  let vlo = Infinity
  let vhi = -Infinity
  const n = indices ? indices.length : values.length
  for (let k = 0; k < n; k++) {
    const i = indices ? (indices[k] as number) : k
    const v = values[i] as number
    if (!Number.isFinite(v)) continue
    if (v < vlo) { vlo = v; lo = i }
    if (v > vhi) { vhi = v; hi = i }
  }
  if (lo < 0) return null
  const at = (i: number): [number, number, number] =>
    [positions[3 * i] as number, positions[3 * i + 1] as number, positions[3 * i + 2] as number]
  return { min: { value: vlo, position: at(lo) }, max: { value: vhi, position: at(hi) } }
}

/** Extremes of a voxel grid over its valid nodes (point nodes at origin + ijk * spacing, i fastest). */
export function volumeExtremes(v: { dimensions: [number, number, number]; origin: [number, number, number];
  spacing: [number, number, number]; scalars: Float32Array; sentinel: number; validity?: Uint8Array }): ExtremePair | null {
  const { scalars, validity, sentinel } = v
  let lo = -1
  let hi = -1
  let vlo = Infinity
  let vhi = -Infinity
  for (let i = 0; i < scalars.length; i++) {
    if (validity ? !validity[i] : scalars[i] === sentinel) continue
    const x = scalars[i] as number
    if (!Number.isFinite(x)) continue
    if (x < vlo) { vlo = x; lo = i }
    if (x > vhi) { vhi = x; hi = i }
  }
  if (lo < 0) return null
  const [nx, ny] = v.dimensions
  const at = (n: number): [number, number, number] => {
    const i = n % nx, j = Math.floor(n / nx) % ny, k = Math.floor(n / (nx * ny))
    return [v.origin[0] + i * v.spacing[0], v.origin[1] + j * v.spacing[1], v.origin[2] + k * v.spacing[2]]
  }
  return { min: { value: vlo, position: at(lo) }, max: { value: vhi, position: at(hi) } }
}

export interface ExtremeMarker {
  /** 'surface' or 'slice-<id>'. */
  id: string
  pair: ExtremePair
  minLabel: string
  maxLabel: string
}

type Listener = () => void
let tags: ExtremeMarker[] = []
const listeners = new Set<Listener>()

/** The extremes post-processing draws now; the viewport overlay (ExtremeTags) shows one tag per max and min. */
export function setExtremeTags(next: ExtremeMarker[]): void {
  const same = next.length === tags.length && next.every((m, k) => {
    const o = tags[k]!
    return o.id === m.id && o.maxLabel === m.maxLabel && o.minLabel === m.minLabel
      && o.pair.max.position.join() === m.pair.max.position.join() && o.pair.min.position.join() === m.pair.min.position.join()
  })
  if (same) return
  tags = next
  for (const fn of listeners) fn()
}

export function getExtremeTags(): ExtremeMarker[] {
  return tags
}

export function subscribeExtremeTags(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
