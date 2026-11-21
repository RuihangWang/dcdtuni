import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { splitBold } from '../src/ui/richText'
import zhCN from '../../shared/i18n/zh-CN.json'
import enUS from '../../shared/i18n/en-US.json'

/**
 * The dictionaries carry a little markup, and React renders text.
 *
 * The legacy page put every hint through `innerHTML`, so `<b>` worked there.
 * In React the same string reached the panel as the literal characters
 * `<b>mm</b>` — visible in the input-unit hint on every model inspector.
 */

describe('splitBold', () => {
  it('leaves a plain string in one piece', () => {
    expect(splitBold('no markup here')).toEqual([{ text: 'no markup here', bold: false }])
  })

  it('splits the emphasised run out of the sentence', () => {
    expect(splitBold('unit is <b>mm</b>.')).toEqual([
      { text: 'unit is ', bold: false },
      { text: 'mm', bold: true },
      { text: '.', bold: false },
    ])
  })

  it('handles several runs, and one that opens the string', () => {
    expect(splitBold('<b>a</b> and <b>b</b>')).toEqual([
      { text: 'a', bold: true },
      { text: ' and ', bold: false },
      { text: 'b', bold: true },
    ])
  })

  it('leaves an unclosed tag as literal text', () => {
    // A dictionary typo should show up as a visible tag somebody fixes, not
    // silently embolden the rest of the sentence.
    expect(splitBold('broken <b>tail')).toEqual([{ text: 'broken <b>tail', bold: false }])
  })

  it('drops nothing — the spans rejoin into the original', () => {
    for (const text of ['<b>x</b>', 'a<b>x</b>b', 'plain', '<b></b>tail']) {
      expect(splitBold(text).map((s) => s.text).join('')).toBe(text.replaceAll(/<\/?b>/g, ''))
    }
  })
})

/**
 * No component may render a `<b>`-bearing key as bare text again.
 *
 * The bug was not that one hint forgot: `ImportedStlOverview` had already
 * noticed and stripped the tags with a local regex, so the repair existed and
 * did not generalise. This finds every key with markup and checks that every
 * component reading one goes through `RichText`.
 */

const SRC = join(__dirname, '..', 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx$/.test(name)) out.push(p)
  }
  return out
}

// `_meta` is an object, so the dictionaries are read as unknown values and the
// string test does the narrowing.
const MARKUP_KEYS = Object.entries({ ...zhCN, ...enUS } as Record<string, unknown>)
  .filter(([, value]) => typeof value === 'string' && /<b>/.test(value))
  .map(([key]) => key)

describe('translations that carry markup', () => {
  it('finds the keys that have any', () => {
    // If this list empties, the dictionaries dropped their markup and
    // `richText.tsx` can go with it.
    expect(MARKUP_KEYS.length).toBeGreaterThan(0)
  })

  it('is rendered through RichText everywhere it is used', () => {
    const offenders: string[] = []
    for (const file of walk(SRC)) {
      if (file.endsWith('richText.tsx')) continue
      const source = readFileSync(file, 'utf8')
      for (const key of new Set(MARKUP_KEYS)) {
        // Every call site of a markup key, and what wraps it.
        const call = new RegExp(`t\\(\\s*['"]${key}['"][^)]*\\)`, 'g')
        for (const hit of source.matchAll(call)) {
          const before = source.slice(Math.max(0, hit.index - 120), hit.index)
          if (!/<RichText\s+text=\{\s*$/.test(before)) {
            offenders.push(`${file.slice(SRC.length + 1)} → ${key}`)
          }
        }
      }
    }
    expect(offenders).toEqual([])
  })
})
