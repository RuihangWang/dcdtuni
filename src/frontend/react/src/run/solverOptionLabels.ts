import type { TranslationKey } from '../i18n'

/**
 * Human labels for the solver selects whose stored value is an OpenFOAM enum.
 *
 * The enum is what goes in `controlDict`; the label is where the CONSEQUENCE
 * lives. "single" and "double" say nothing about writing 6 versus 10
 * significant digits, and "backward" says nothing about being second order —
 * the legacy option text carries both facts (STL_generate.html:288, :309), and
 * rendering the raw value throws them away along with the translation.
 *
 * A value with no entry falls back to itself. `firstTime` is such a value: the
 * legacy select offers only `latestTime` and `startTime`, so there is no shared
 * key for it and inventing one would add a string to this app's dictionary
 * alone.
 */

export const WRITE_PRECISION_LABELS: Record<string, TranslationKey> = {
  single: 'option.single_precision',
  double: 'option.double_precision',
}

export const START_FROM_LABELS: Record<string, TranslationKey> = {
  latestTime: 'option.start_latest',
  startTime: 'option.start_zero',
}

export const DDT_SCHEME_LABELS: Record<string, TranslationKey> = {
  Euler: 'option.euler',
  backward: 'option.backward',
  CrankNicolson: 'option.crank_nicolson',
}

/** The key for one option, or null when the raw value has to stand in. */
export function optionLabelKey(
  table: Record<string, TranslationKey>,
  value: string,
): TranslationKey | null {
  return table[value] ?? null
}
