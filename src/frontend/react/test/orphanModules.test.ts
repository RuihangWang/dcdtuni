/**
 * Every module under `src/` must be reachable from the app.
 *
 * The fourth parity audit's strongest findings all shared one shape: the feature
 * IS written, sometimes IS tested, and NOTHING imports it. `SheetSizeSection`,
 * `SensorEditDialog`, `AuthOverlay`, `duplicatePlacement` and `inspectorTitle`
 * are 460 lines of finished, documented work that the running app never reaches.
 *
 * The first two audits could not see this. They asked "does React have this
 * code?" — it does — and marked the item verified. `AuthOverlay` is the sharp
 * case: audit round 1 signed off the login overlay as VERIFIED, and no import
 * of it exists.
 *
 * A unit test cannot catch it either: a module with a test has a consumer, just
 * not a consumer that ships. So the check is the import graph itself.
 *
 * ALLOWED lists the deliberate exceptions. Adding a name to it is a decision to
 * ship dead code, and should carry a reason.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..', 'src')

/** Reached by the runtime some way other than a static import from `src/`. */
const ALLOWED = new Set([
  // The entry point; index.html loads it.
  'main.tsx',
])

/**
 * The orphans that already exist, as of the fourth parity audit.
 *
 * Asserted as an EXACT set, not tolerated. A new orphan fails this test — that
 * is the point. Wiring one of these up ALSO fails it, which is deliberate: the
 * list has to shrink on purpose, so nobody can quietly ship the fix and leave
 * the debt recorded.
 *
 * It is now EMPTY, and every entry it used to hold is recorded here rather than
 * deleted, because "which modules were unreachable and what happened to each"
 * is the finding — the empty array on its own says nothing.
 *
 *   SheetSizeSection.tsx   wired into Inspector.tsx
 *   inspectorTitle.ts      wired into App.tsx (both panel headers)
 *   duplicatePlacement.ts  wired into projectStore.duplicateInto
 *   SensorEditDialog.tsx   wired into SensorPanel.tsx (double-click the name)
 *   AuthOverlay.tsx        NEVER an orphan — the first scan could not see the
 *                          dynamic import at run/auth.ts:229
 *   legacy/stlWriter.ts    DELETED, not wired: a typed accessor with no
 *                          consumer, while every STL byte this app emits comes
 *                          from shared/js/geometry_scene.js reading
 *                          `window.stlWriter`. Keeping it would have implied
 *                          two STL paths.
 *
 * See docs/archive/react-migration-2026-08/react-migration-gaps-round4.md for what each one does.
 */
const KNOWN_ORPHANS: string[] = []

function walk(dir: string, pattern: RegExp, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, pattern, out)
    else if (pattern.test(name) && !name.endsWith('.d.ts')) out.push(p)
  }
  return out
}

describe('no orphan modules', () => {
  it('has exactly the orphans the audit recorded — no more, no fewer', () => {
    const files = walk(SRC, /\.tsx?$/)
    // Importers also include the .mjs scripts under src/: the headless compiler
    // bundles its entry points by path (headless/headless_compiler.mjs).
    const importers = walk(SRC, /\.(tsx?|mjs)$/)
    const texts = new Map(importers.map((f) => [f, readFileSync(f, 'utf8')]))

    const orphans: string[] = []
    for (const f of files) {
      const rel = relative(SRC, f)
      if (ALLOWED.has(rel)) continue
      const stem = rel.replace(/\.tsx?$/, '').split('/').pop()!
      // Three ways a module gets reached, and the third is the one that bit:
      //   `from '.../<stem>'`      — a normal import
      //   `import '.../<stem>'`    — a side-effect import
      //   `import('.../<stem>')`   — a DYNAMIC import
      // The first version of this scan matched only the first two and reported
      // AuthOverlay as dead. It is not: run/auth.ts reaches it with
      // `await import('../components/AuthOverlay')`, precisely so the auth
      // module stays free of JSX. A guard that cannot see a dynamic import
      // accuses live code, which is worse than missing dead code.
      //
      // A fourth: a quoted path that names the FILE, extension included. That
      // is how a module is reached that nothing imports by specifier --
      // `new Worker(new URL('./liquidTransport.worker.ts', import.meta.url))`
      // and the esbuild entry 'src/headless/projectBuilder.ts' in
      // headless_compiler.mjs. Both were reported as orphans (2026-10-22).
      const pattern = new RegExp(
        `(?:from|import)\\s+['"][^'"]*(?:/|^|\\./)${stem}['"]` +
          `|import\\(\\s*['"][^'"]*(?:/|^|\\./)${stem}['"]\\s*\\)` +
          `|['"][^'"]*(?:/|^)${stem.replace(/\./g, '\\.')}\\.tsx?['"]`,
      )
      const imported = importers.some((g) => g !== f && pattern.test(texts.get(g) ?? ''))
      if (!imported) orphans.push(rel)
    }

    expect(orphans.sort()).toEqual(KNOWN_ORPHANS)
  })
})
