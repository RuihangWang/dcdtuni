/** View-only intent. Missing flags are legacy manual limits, never a numeric sentinel. */
export interface SliceColorRange {
  climMin: number
  climMax: number
  autoScale?: boolean
}
export function isSliceAutoRange(range: SliceColorRange): boolean {
  return range.autoScale === true
}
/** Preserve raw manual literals, including equal endpoints and 0 K. */
export function resolveSliceColorRange(range: SliceColorRange, robust: [number, number]): [number, number] {
  return isSliceAutoRange(range) ? [robust[0], robust[1]] : [range.climMin, range.climMax]
}
