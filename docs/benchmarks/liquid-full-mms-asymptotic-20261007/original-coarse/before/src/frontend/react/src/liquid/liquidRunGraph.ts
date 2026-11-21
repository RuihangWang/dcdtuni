/**
 * Compiling the .liquidrun graph from the solver panel.
 *
 * `buildLiquidGraph` (src/legacy/liquidGraphgen.ts) points `window.state` at the
 * project with a plain assignment, because the shared compiler reads that
 * global. But `installLiquidScene` (src/legacy/liquidScene.ts:139) defines
 * `window.state` as a GETTER with no setter, and an assignment to an accessor
 * with no setter THROWS in a module — which is strict mode. So the first line
 * of "Run Liquid Solve" rejected, `void onRun()` swallowed the rejection, and
 * the button did nothing at all: no request, no error, no stage badge.
 *
 * Presenting the global as a writable data property for the duration of the
 * call is the smallest thing that makes the shared compiler runnable from here.
 * The value installed is the same project the getter would have returned, and
 * the original descriptor goes back in a `finally`, so nothing else that reads
 * `window.state` can observe the swap.
 *
 * The real repair belongs in `liquidGraphgen.ts` — its `withProjectGlobal`
 * should define the property rather than assign to it. This module is the
 * caller-side guard until that lands.
 */

import type { CfdProject } from '../schema/cfdproj'
import { buildLiquidGraph } from '../legacy/liquidGraphgen'

export interface LiquidStateView {
  fluid: unknown
  solver: unknown
  enabled: boolean
}

/** Run `fn` with `window.state` writable, then put the descriptor back. */
export function withWritableStateGlobal<T>(fn: () => T): T {
  const w = window as unknown as Record<string, unknown>
  const before = Object.getOwnPropertyDescriptor(w, 'state')
  // Only an accessor needs replacing. A plain data property (or nothing at all)
  // is already assignable, and redefining it would be a way to lose it.
  if (!before || (!before.get && !before.set)) return fn()
  if (!before.configurable) return fn()
  Object.defineProperty(w, 'state', {
    value: before.get ? before.get.call(w) : undefined,
    writable: true,
    configurable: true,
    enumerable: before.enumerable,
  })
  try {
    return fn()
  } finally {
    Object.defineProperty(w, 'state', before)
  }
}

/** Compile, or null when the compiler rejected the network (it alerts itself). */
export function compileLiquidGraph(
  project: CfdProject,
  liquid: LiquidStateView,
): Record<string, unknown> | null {
  return withWritableStateGlobal(() => buildLiquidGraph(project, liquid))
}
