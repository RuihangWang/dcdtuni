/** How a steady air solve starts: uniform field, or an AI prediction of the same mesh and boundary conditions. */

import { fetchModelCards } from '../postprocess/scanoModels'

export type SolveInit = 'uniform' | 'ai'

const INIT_KEY = 'dcdtuni.solve.init'
/** The AI sub-tab's model choice (ScanoSolvePanel). */
export const SCANO_MODEL_KEY = 'dcdtuni.scano.model'

const listeners = new Set<() => void>()
let current: SolveInit = read()

function read(): SolveInit {
  try {
    return localStorage.getItem(INIT_KEY) === 'ai' ? 'ai' : 'uniform'
  } catch {
    return 'uniform'
  }
}

export function getSolveInit(): SolveInit {
  return current
}

export function setSolveInit(next: SolveInit): void {
  current = next
  try {
    localStorage.setItem(INIT_KEY, next)
  } catch {
    /* private window: the choice lasts for this page */
  }
  for (const fn of listeners) fn()
}

export function subscribeSolveInit(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** The `aiInit` of a steady solve request, or null (uniform start, a transient solve, or no usable model). */
export async function solveAiInit(steady: boolean): Promise<{ model: string } | null> {
  if (current !== 'ai' || !steady) return null
  const cards = (await fetchModelCards()).filter((c) => !c.error)
  let chosen: string | null = null
  try {
    chosen = localStorage.getItem(SCANO_MODEL_KEY)
  } catch {
    chosen = null
  }
  const model = cards.find((c) => c.id === chosen) ?? cards.find((c) => c.recommended) ?? cards[0]
  if (!model) throw new Error('AI initialisation: no usable model on the server')
  return { model: model.id }
}

/** Test seam. */
export function resetSolveInitForTest(): void {
  current = read()
  listeners.clear()
}
