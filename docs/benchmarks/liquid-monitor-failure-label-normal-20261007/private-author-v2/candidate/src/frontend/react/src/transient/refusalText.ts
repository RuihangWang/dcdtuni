/**
 * Why an edit was refused, in the user's language.
 *
 * The reasons are English sentences built by the shared validator
 * (`cfdproj_transient.js` validateTransient) and the timeline operations
 * (`trackEdits.ts`), plus the store's two codes for edits off t = 0. Each known
 * one maps to a dictionary key; anything else passes through as written.
 */
import { t, type TranslationKey } from '../i18n'

const EXACT: Record<string, TranslationKey> = {
  'static': 'transient.refusal.static',
  'structure': 'transient.refusal.structure',
  'trim': 'transient.refusal.trim',
  'the start keyframes cannot move': 'transient.refusal.start_fixed',
  'the start keyframe cannot move': 'transient.refusal.start_fixed',
  'a keyframe cannot move onto the start': 'transient.refusal.onto_start',
  'a keyframe cannot move past the end of the timeline': 'transient.refusal.after_end',
  'keyframe after the end of the timeline': 'transient.refusal.after_end',
  'no such keyframe': 'transient.refusal.no_keyframe',
  'no such track': 'transient.refusal.no_track',
  'the start keyframe already exists': 'transient.refusal.start_exists',
  'there is already a keyframe at that time': 'transient.refusal.key_exists',
  'the start keyframe cannot be deleted': 'transient.refusal.start_undeletable',
  'missing transient block': 'transient.refusal.invalid_block',
  'mode must be steady or transient': 'transient.refusal.invalid_block',
  'duration must be > 0': 'transient.refusal.duration_positive',
  'grid must be > 0': 'transient.refusal.grid_positive',
  'grid must be finite and > 0': 'transient.refusal.grid_positive',
  'grid change must preserve existing keyframe and ramp times': 'transient.refusal.grid_conflict',
  'start must be steadyConverged or uniform': 'transient.refusal.invalid_start',
  'duplicate track': 'transient.refusal.duplicate_track',
  'a track needs a keyframe at t = 0 without ramp': 'transient.refusal.needs_start',
  'keyframe times must be strictly increasing': 'transient.refusal.order',
  'ramp longer than the gap to the previous keyframe': 'transient.refusal.ramp_gap',
}

const PATTERNS: [RegExp, TranslationKey, (m: RegExpMatchArray) => Record<string, string>][] = [
  [/^value outside \[(.+), (.+)\] (.*)$/, 'transient.refusal.value_range', (m) => ({ min: m[1]!, max: m[2]!, unit: m[3]! })],
  [/^time not on the (.+) s grid$/, 'transient.refusal.off_grid', (m) => ({ grid: m[1]! })],
  [/^unknown parameter (.*)$/, 'transient.refusal.unknown_param', (m) => ({ param: m[1]! })],
]

export function refusalText(reason: string): string {
  const key = EXACT[reason]
  if (key) return t(key)
  for (const [re, k, vars] of PATTERNS) {
    const m = reason.match(re)
    if (m) return t(k, vars(m))
  }
  return reason
}
