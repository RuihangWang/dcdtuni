import zhCN from '../../shared/i18n/zh-CN.json'
import enUS from '../../shared/i18n/en-US.json'

/**
 * Translations, sharing the legacy editor's dictionaries.
 *
 * `shared/i18n/zh-CN.json` and `en-US.json` are the same 1452-key files the HTML app
 * loads. Forking them would mean two dictionaries to keep in step, and the way
 * that fails is silent: a key exists in one and renders as the raw key string in
 * the other. So this imports them directly, and a test asserts the two locales
 * still have identical key sets.
 *
 * Static import rather than the legacy `fetch` at boot. The HTML app has an
 * `onReady` callback and a window where `t()` returns keys instead of text;
 * bundling the dictionaries removes that state entirely — `t()` works on the
 * first render.
 *
 * `TranslationKey` is the union of real keys, so a typo is a compile error
 * rather than a label that renders as "inspector.projct_name".
 */

// `_meta` is the dictionaries' own header (locale/name/version), not a string —
// exclude it so it is neither a valid key nor a type error on the index signature.
export type TranslationKey = Exclude<keyof typeof zhCN, '_meta'>

export type Locale = 'zh-CN' | 'en-US'

const DICTIONARIES: Record<Locale, Record<string, string>> = {
  'zh-CN': zhCN as unknown as Record<string, string>,
  'en-US': enUS as unknown as Record<string, string>,
}

export const LOCALES: { id: Locale; label: string }[] = [
  { id: 'zh-CN', label: '中文' },
  { id: 'en-US', label: 'English' },
]

/** Same storage key as the legacy app, so the two share the user's choice. */
const STORAGE_KEY = 'stlgen-lang'

const FALLBACK: Locale = 'zh-CN'

function readStoredLocale(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'zh-CN' || saved === 'en-US') return saved
  } catch {
    /* private mode: fall through to the default */
  }
  return FALLBACK
}

let locale: Locale = readStoredLocale()

type Listener = () => void
const listeners = new Set<Listener>()

export function getLocale(): Locale {
  return locale
}

export function setLocale(next: Locale) {
  if (next === locale) return
  locale = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* private mode: the choice just does not persist */
  }
  // Guarded so the module works without a DOM: `t()` is pure logic and is worth
  // being able to test on its own, and the lang attribute is the only thing here
  // that needs a document.
  if (typeof document !== 'undefined') document.documentElement.lang = next
  listeners.forEach((fn) => fn())
}

export function subscribeLocale(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/**
 * Look up `key`, substituting `{name}` and `{{name}}` placeholders.
 *
 * Both brace styles because the dictionaries use both, and the double form is
 * replaced FIRST — otherwise the single-brace pass turns `{{count}}` into a
 * literal `{5}`. That is the legacy `I18n.t` behaviour, and the dictionaries are
 * written against it.
 */
export function t(key: TranslationKey, vars?: Record<string, string | number>): string {
  const dict = DICTIONARIES[locale]
  let text = dict[key] ?? DICTIONARIES[FALLBACK][key] ?? key
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{{${name}}}`, String(value)).replaceAll(`{${name}}`, String(value))
    }
  }
  return text
}

/**
 * The translator, on `window`.
 *
 * The shared `js/` modules call `t(...)` as a BARE global. Most guard it as
 * `window.t ? t(key) : 'fallback'`, but not all: `liquid_mode.js`'s
 * control-point menu calls it unguarded, so without this the right-click menu
 * threw `t is not defined` and no menu appeared — the failure was invisible
 * because the exception died inside the event listener. The legacy page does
 * the same assignment (STL_generate.html:1211) for the same reason.
 */
if (typeof window !== 'undefined') {
  ;(window as unknown as { t?: typeof t }).t = t
}

/** Every translation key in a bundled dictionary, `_meta` excluded. Used by the parity test. */
export function allKeys(which: Locale): string[] {
  return Object.keys(DICTIONARIES[which]).filter((k) => k !== '_meta')
}
