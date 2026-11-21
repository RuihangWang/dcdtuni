import { useEffect, useState } from 'react'
import { getLocale, subscribeLocale, t, type Locale, type TranslationKey } from './i18n'

/**
 * Re-render this component when the locale changes.
 *
 * `t` is a plain module function, so a component that only calls it would keep
 * showing the old language after a switch — React has no reason to re-render.
 * Subscribing here is what makes the toolbar's language selector take effect
 * everywhere at once.
 */
export function useTranslation(): { t: (key: TranslationKey, vars?: Record<string, string | number>) => string; locale: Locale } {
  const [, bump] = useState(0)
  useEffect(() => subscribeLocale(() => bump((n) => n + 1)), [])
  return { t, locale: getLocale() }
}
