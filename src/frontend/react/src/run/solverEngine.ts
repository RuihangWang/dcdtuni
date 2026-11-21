/** Which solver runs a steady air solve: OpenFOAM (the default) or this project's GPU Newton-Krylov solver. The
 * GPU solver is offered only by a server started with `start_server.sh --gpu` (`gpuSolver.enabled` in
 * /api/server-info); it starts from the uniform field. */

export type SolverEngine = 'openfoam' | 'gpuNewton'

const ENGINE_KEY = 'dcdtuni.solve.engine'

const listeners = new Set<() => void>()
let current: SolverEngine = read()

function read(): SolverEngine {
  try {
    return localStorage.getItem(ENGINE_KEY) === 'gpuNewton' ? 'gpuNewton' : 'openfoam'
  } catch {
    return 'openfoam'
  }
}

export function getSolverEngine(): SolverEngine {
  return current
}

export function setSolverEngine(next: SolverEngine): void {
  current = next
  try {
    localStorage.setItem(ENGINE_KEY, next)
  } catch {
    /* private window: the choice lasts for this page */
  }
  for (const fn of listeners) fn()
}

export function subscribeSolverEngine(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** The engine a solve runs with: the GPU solver when it is chosen, the solve is steady and this server offers it. */
export function effectiveSolverEngine(steady: boolean, offered: boolean): SolverEngine {
  return current === 'gpuNewton' && steady && offered ? 'gpuNewton' : 'openfoam'
}

/** Test seam. */
export function resetSolverEngineForTest(): void {
  current = read()
  listeners.clear()
}
