/**
 * The .liquidrun graph compiler, shared with the legacy editor rather than reimplemented.
 *
 * `liquid_graphgen.js` turns the 3D scene into the node/edge graph the Python
 * liquid solver consumes: it walks CDUs, cold plates, pipes, junctions and
 * valves, resolves the flow topology, converts every unit to SI, and validates
 * the result. That is 760 lines of physics-adjacent bookkeeping, and it is the
 * single definition of what a `.liquidrun` IS — `graphcompiling/compiler.py`
 * is written against its output.
 *
 * Rewriting it in TypeScript would create a SECOND definition of that
 * contract, and the two would drift silently: a graph that compiles but wires
 * one branch to the wrong junction still solves, just with the wrong answer.
 * The React rewrite is of the UI. The solver's input format is not the UI.
 *
 * The file is a plain IIFE with exactly two touch points — it reads
 * `window.state` and it assigns `window.buildLiquidrunJson` — so importing it
 * for its side effect is enough, provided `window.state` is pointed at the
 * project first. `withProjectGlobal` does that and puts back whatever was
 * there, so nothing else that reads `window.state` observes the swap.
 */

// Installs geometryPure before the compiler is called. The compiler consumes
// its canonical model-local -> world transform for liquid equipment ports.
import './geometryBuilder'
import '../../../shared/js/liquid_graphgen.js'

import type { CfdProject, Construction } from '../schema/cfdproj'
import { stateV4 } from './cfdprojModules'

interface LiquidStateView {
  fluid: unknown
  solver: unknown
  enabled: boolean
}

type BuildLiquidrunJson = (
  models: Construction[],
  liquidState: LiquidStateView
) => Record<string, unknown> | null

function builder(): BuildLiquidrunJson {
  const fn = (window as unknown as { buildLiquidrunJson?: BuildLiquidrunJson })
    .buildLiquidrunJson
  if (typeof fn !== 'function') {
    throw new Error('liquid_graphgen.js did not define window.buildLiquidrunJson')
  }
  return fn
}

/**
 * Run `fn` with `window.state` pointed at `project`, then restore it.
 *
 * Restoring in a `finally` matters: `buildLiquidrunJson` calls `alert()` and
 * returns null when validation fails, and an early return must not leave the
 * global aliased to a project the rest of the app is not editing.
 */
function withProjectGlobal<T>(project: CfdProject, fn: () => T): T {
  const w = window as unknown as { state?: unknown }
  const previous = w.state
  const had = 'state' in w
  w.state = project
  try {
    return fn()
  } finally {
    if (had) w.state = previous
    else delete w.state
  }
}

/**
 * Compile a project into a `.liquidrun` graph.
 *
 * Returns null when the graph fails validation — the legacy compiler reports
 * those failures through `alert()` and its own console output, which is
 * deliberately left alone here: routing them into the React log would mean
 * parsing prose out of an alert, and a validation failure the user did not see
 * is worse than one shown in a dialog.
 */
export function buildLiquidGraph(
  project: CfdProject,
  liquid: { fluid: unknown; solver: unknown; enabled: boolean }
): Record<string, unknown> | null {
  return withProjectGlobal(project, () => {
    // The shared module's flat list, NOT cfdproj.ts's tree-ordered pairs: the
    // compiler indexes into this list, so a different order rewires the network.
    const models: Construction[] = stateV4().allModels(project)
    return builder()(models, {
      fluid: liquid.fluid,
      solver: liquid.solver,
      enabled: liquid.enabled,
    })
  })
}
