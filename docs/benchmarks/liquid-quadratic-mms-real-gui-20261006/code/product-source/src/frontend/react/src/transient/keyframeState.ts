/**
 * Where a time sits on one track (transient design §2, inspector semantics):
 * on a keyframe, strictly inside a ramp toward one, or on a held value. An edit
 * at a keyframe rewrites it; anywhere else it writes a new keyframe there.
 */
import type { Keyframe } from './model'

export type KeyframeState = 'key' | 'ramp' | 'hold'

const EPS = 1e-9

export function keyframeStateAt(keyframes: Keyframe[], t: number): KeyframeState {
  if (keyframes.some((k) => Math.abs(k.t - t) <= EPS * Math.max(1, Math.abs(t)))) return 'key'
  // At the ramp's start the value is still the held one, so it is not "inside".
  if (keyframes.some((k) => (k.rampS ?? 0) > 0 && t > k.t - (k.rampS ?? 0) && t < k.t)) return 'ramp'
  return 'hold'
}
