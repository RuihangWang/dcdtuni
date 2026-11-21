/**
 * The shared `shared/js/*.js` modules must not assign to variables they never declare.
 *
 * These modules were lifted verbatim out of `STL_generate.html`, and the lift is
 * name-aware but not perfect: a bare `_foo = ...` inside the module resolves to
 * the page's `let _foo` when the LEGACY app loads it, and to nothing at all when
 * React imports the same file. The legacy app then works, React throws
 * `_foo is not defined` at runtime, and no build step on either side says a word.
 *
 * That is not hypothetical. `_textureRebuildTimer` was exactly this: declared at
 * STL_generate.html:2115, assigned in geometry_scene.js's `loadExternalTexture`.
 * Every external texture that finished loading produced a page error in React,
 * and the debounced rebuild those textures were waiting for never ran. It was
 * found by reading the head browser's error log during an unrelated end-to-end
 * run — nothing else would have surfaced it, because the initial texture apply
 * works and only the re-apply is broken.
 *
 * So the rule is checked statically: every identifier a shared module ASSIGNS to
 * must be declared in that module, be a documented context entry, or be an
 * explicit global. Reads are not checked — those are the injected context, which
 * is the design.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const JS_DIR = join(__dirname, '..', '..', 'shared', 'js')

/**
 * Names a shared module may assign to without declaring.
 *
 * The injected scene registries are reassigned BY the host on clear, and the
 * modules write through the context's setters — but the rewrite left some as
 * plain assignments. They are legitimate: the context defines them.
 */
const CONTEXT_WRITABLES = new Set([
  'renderMeshes',
  'renderRoots',
  'renderHelpers',
  'state',
  'uiState',
  'webglAvailable',
])

/** Deliberate globals: the modules publish these for the host to read. */
const PUBLISHED_GLOBALS = new Set(['module', 'exports', 'window', 'globalThis', 'self'])

/**
 * Every name a `let`/`const`/`var` statement introduces, including the second
 * and later declarators of a list.
 *
 * `let a = 1, b = 2` is one statement declaring TWO names, and matching only
 * `(?:let|const|var)\s+(\w+)` sees `a`. The first version of this guard did
 * exactly that and reported eleven healthy variables as undeclared, which is the
 * failure mode that gets a guard deleted rather than fixed. Walks to the end of
 * the statement tracking bracket depth, and takes the identifier after each
 * depth-0 comma.
 */
function declarationList(src: string, start: number, out: Set<string>): void {
  let i = start
  let depth = 0
  let expectName = true
  while (i < src.length) {
    const c = src[i]!
    if (c === '(' || c === '[' || c === '{') depth++
    else if (c === ')' || c === ']' || c === '}') {
      if (depth === 0) break
      depth--
    } else if (depth === 0 && c === ';') break
    else if (depth === 0 && c === '\n') {
      // A declarator list may WRAP: `var p0 = a, p1 = b,\n    p2 = c, p3 = d;`
      // is one statement declaring four names. Breaking at the newline saw two
      // and called p3 undeclared. Continue when the line ended mid-expression;
      // stop only when it ended at something that can close a statement (ASI).
      const prev = src.slice(0, i).replace(/[ \t\r]+$/, '').slice(-1)
      if (prev === ',' || prev === '=' || prev === '+' || prev === '(' || prev === '&' || prev === '|') {
        i++
        continue
      }
      break
    }
    else if (depth === 0 && c === ',') expectName = true
    else if (expectName && /[A-Za-z_$]/.test(c)) {
      const m = /^[A-Za-z_$][\w$]*/.exec(src.slice(i))
      if (m) {
        out.add(m[0])
        i += m[0].length
        expectName = false
        continue
      }
    }
    i++
  }
}

function declaredNames(src: string): Set<string> {
  const out = new Set<string>()
  // declarations: let/const/var (whole declarator list), function, class, params
  for (const m of src.matchAll(/\b(?:let|const|var)\s+/g)) {
    declarationList(src, m.index! + m[0].length, out)
  }
  for (const m of src.matchAll(/\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)/g)) out.add(m[1]!)
  for (const m of src.matchAll(/\bclass\s+([A-Za-z_$][\w$]*)/g)) out.add(m[1]!)
  // destructuring and parameter lists, coarsely: any identifier inside ( ) or { }
  // that follows `function name(` or `=>`. Coarse on purpose — a false DECLARE
  // only weakens the check, while a false ASSIGN would fail a healthy file.
  for (const m of src.matchAll(/\(([^()]*)\)\s*(?:=>|\{)/g)) {
    for (const part of m[1]!.split(',')) {
      const name = part.trim().split(/[=:\s]/)[0]?.replace(/^\.\.\./, '')
      if (name && /^[A-Za-z_$][\w$]*$/.test(name)) out.add(name)
    }
  }
  for (const m of src.matchAll(/\bcatch\s*\(\s*([A-Za-z_$][\w$]*)/g)) out.add(m[1]!)
  return out
}

/**
 * Blank out every comment and string body, keeping newlines so line numbers hold.
 *
 * Done over the WHOLE file, not per line. These modules build DOM with multi-line
 * template literals, and a per-line stripper never sees the backtick that opened
 * on an earlier line — so `<input type="number" class="x">` inside one read as
 * four assignments to `type`, `class`, `title` and `placeholder`. Four false
 * accusations in HTML markup is how a guard gets ignored.
 */
function scrub(src: string): string {
  let out = ''
  let i = 0
  const keepNewlines = (text: string) => text.replace(/[^\n]/g, ' ')
  while (i < src.length) {
    const two = src.slice(i, i + 2)
    if (two === '//') {
      const end = src.indexOf('\n', i)
      const stop = end === -1 ? src.length : end
      out += keepNewlines(src.slice(i, stop))
      i = stop
      continue
    }
    if (two === '/*') {
      const end = src.indexOf('*/', i + 2)
      const stop = end === -1 ? src.length : end + 2
      out += keepNewlines(src.slice(i, stop))
      i = stop
      continue
    }
    const q = src[i]!
    if (q === '"' || q === "'" || q === '`') {
      let j = i + 1
      while (j < src.length) {
        if (src[j] === '\\') { j += 2; continue }
        if (src[j] === q) { j++; break }
        j++
      }
      // The quotes themselves survive so the text still parses as a value.
      out += q + keepNewlines(src.slice(i + 1, Math.max(i + 1, j - 1))) + (j <= src.length ? q : '')
      i = j
      continue
    }
    out += q
    i++
  }
  return out
}

/** Bare `name = ...` assignments (not `.name =`, not `==`, `===`, `=>`). */
function assignedNames(src: string): Map<string, number> {
  const out = new Map<string, number>()
  const lines = scrub(src).split('\n')
  lines.forEach((code, i) => {
    for (const m of code.matchAll(/(?<![.\w$])([A-Za-z_$][\w$]*)\s*=(?![=>])/g)) {
      const name = m[1]!
      // `let x =` / `const x =` are declarations, handled above
      const before = code.slice(0, m.index).trimEnd()
      if (/\b(?:let|const|var)$/.test(before)) continue
      if (!out.has(name)) out.set(name, i + 1)
    }
  })
  return out
}

const FILES = readdirSync(JS_DIR).filter((f) => f.endsWith('.js'))

describe('shared web/js modules assign only to names they own', () => {
  it('finds the module directory', () => {
    expect(FILES.length).toBeGreaterThan(0)
  })

  for (const file of FILES) {
    it(`${file} has no undeclared assignment targets`, () => {
      const src = readFileSync(join(JS_DIR, file), 'utf8')
      const declared = declaredNames(src)
      const offenders: string[] = []
      for (const [name, line] of assignedNames(src)) {
        if (declared.has(name)) continue
        if (CONTEXT_WRITABLES.has(name)) continue
        if (PUBLISHED_GLOBALS.has(name)) continue
        offenders.push(`${name} (line ${line})`)
      }
      expect(offenders).toEqual([])
    })
  }
})
