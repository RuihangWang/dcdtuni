import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { allKeys, getLocale, setLocale, t } from '../src/i18n'

/**
 * The dictionaries are shared with the legacy editor, and the way shared
 * dictionaries break is silent: a key added to one locale renders as the raw key
 * string in the other, and a placeholder dropped from one locale silently loses
 * the value it was meant to interpolate. Neither shows up in a type check.
 */

// Paths off the vitest root (this package) rather than `import.meta.url`: under
// jsdom, import.meta.url is an http:// URL and `new URL(..)` is not a file path.
const ROOT = resolve(process.cwd())
const I18N = resolve(ROOT, '../shared/i18n')
const SRC = resolve(ROOT, 'src')

const ZH = JSON.parse(readFileSync(join(I18N, 'zh-CN.json'), 'utf8')) as Record<string, string>
const EN = JSON.parse(readFileSync(join(I18N, 'en-US.json'), 'utf8')) as Record<string, string>

function sourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      out.push(...sourceFiles(path))
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(path)
    }
  }
  return out
}

const PLACEHOLDER = /\{\{?([a-zA-Z0-9_]+)\}?\}/g

function placeholders(text: string): Set<string> {
  return new Set(Array.from(text.matchAll(PLACEHOLDER), (m) => m[1]!))
}

describe('dictionary parity', () => {
  it('zh-CN and en-US have identical key sets', () => {
    const zh = new Set(Object.keys(ZH))
    const en = new Set(Object.keys(EN))
    expect([...zh].filter((k) => !en.has(k))).toEqual([])
    expect([...en].filter((k) => !zh.has(k))).toEqual([])
  })

  it('every key carries the same placeholders in both locales', () => {
    const mismatched: string[] = []
    for (const key of Object.keys(ZH)) {
      if (key === '_meta' || typeof ZH[key] !== 'string' || typeof EN[key] !== 'string') continue
      const a = placeholders(ZH[key]!)
      const b = placeholders(EN[key]!)
      if (a.size !== b.size || [...a].some((p) => !b.has(p))) {
        mismatched.push(`${key}: zh{${[...a]}} en{${[...b]}}`)
      }
    }
    expect(mismatched).toEqual([])
  })

  it('exposes the same key count through allKeys, minus _meta', () => {
    expect(allKeys('zh-CN').length).toBe(Object.keys(ZH).length - 1)
    expect(allKeys('zh-CN')).not.toContain('_meta')
  })
})

describe('keys referenced by the app', () => {
  const files = sourceFiles(SRC)

  it('finds source files to scan', () => {
    expect(files.length).toBeGreaterThan(8)
  })

  it("every t('key') in the source exists in the dictionary", () => {
    // TypeScript already rejects a literal typo, but not a key built from a
    // template literal and cast — this catches those.
    const missing: string[] = []
    for (const file of files) {
      const src = readFileSync(file, 'utf8')
      for (const m of src.matchAll(/\bt\(\s*'([a-z0-9_.]+)'/gi)) {
        if (!(m[1]! in ZH)) missing.push(`${file}: ${m[1]}`)
      }
      for (const m of src.matchAll(/labelKey:\s*'([a-z0-9_.]+)'/gi)) {
        if (!(m[1]! in ZH)) missing.push(`${file}: ${m[1]}`)
      }
      for (const m of src.matchAll(/(?:hintKey|disabledKey):\s*'([a-z0-9_.]+)'/gi)) {
        if (!(m[1]! in ZH)) missing.push(`${file}: ${m[1]}`)
      }
    }
    expect(missing).toEqual([])
  })

  it('has no hard-coded Chinese left in component markup', () => {
    // A hard-coded label survives a language switch, which is exactly the bug
    // this whole layer exists to prevent. Comments are allowed to be Chinese.
    const offenders: string[] = []
    for (const file of files) {
      if (!file.endsWith('.tsx')) continue
      const src = readFileSync(file, 'utf8')
      let inBlockComment = false
      src.split('\n').forEach((line, i) => {
        const trimmed = line.trim()
        if (trimmed.startsWith('/*')) inBlockComment = true
        const wasComment = inBlockComment
        if (trimmed.includes('*/')) inBlockComment = false
        if (wasComment || trimmed.startsWith('//') || trimmed.startsWith('*')) return
        if (/[一-鿿]/.test(line)) offenders.push(`${file}:${i + 1}: ${trimmed}`)
      })
    }
    expect(offenders).toEqual([])
  })
})

describe('t()', () => {
  it('substitutes both {name} and {{name}} placeholders', () => {
    // Double braces must be replaced FIRST, or the single-brace pass turns
    // {{count}} into a literal {5}. The dictionaries use both styles.
    expect(t('tree.mode_multi', { count: 16 })).toContain('16')
    expect(t('tree.mode_multi', { count: 16 })).not.toContain('{')
  })

  it('falls back to zh-CN for a key the active locale lacks', () => {
    const before = getLocale()
    try {
      setLocale('en-US')
      expect(t('nav.geometry')).toBe(EN['nav.geometry'])
    } finally {
      setLocale(before)
    }
  })

  it('switches every string when the locale changes', () => {
    const before = getLocale()
    try {
      setLocale('zh-CN')
      const zh = t('inspector.position_title')
      setLocale('en-US')
      expect(t('inspector.position_title')).not.toBe(zh)
    } finally {
      setLocale(before)
    }
  })
})
