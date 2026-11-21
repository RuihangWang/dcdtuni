/**
 * How an AI solve runs its model (server backend.aircooling.scano.memory): auto (the whole-room forward when its
 * estimated peak fits the GPU's free memory, else TTA), whole (one forward over the room, the paper's tables), or
 * tta (the paper's test-time aggregation: chunks the size of a training draw, `passes` repetitions). The AI solve
 * and the real-time updates both send it; the choice is remembered per viewer.
 */

export type ScanoInferenceMode = 'auto' | 'whole' | 'tta'

export interface ScanoInference {
  inference: ScanoInferenceMode
  passes: number
}

export const MAX_PASSES = 16
const KEY = 'dcdtuni.scano.inference'
const MODES: ScanoInferenceMode[] = ['auto', 'whole', 'tta']

function load(): ScanoInference {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Partial<ScanoInference> | null
    if (v && MODES.includes(v.inference as ScanoInferenceMode)) {
      return { inference: v.inference as ScanoInferenceMode, passes: clampPasses(Number(v.passes) || 1) }
    }
  } catch {
    /* private window or a stale value */
  }
  return { inference: 'auto', passes: 1 }
}

function clampPasses(n: number): number {
  return Math.min(MAX_PASSES, Math.max(1, Math.round(n)))
}

let state: ScanoInference = load()
const listeners = new Set<() => void>()

export function getScanoInference(): ScanoInference {
  return state
}

export function subscribeScanoInference(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function setScanoInference(next: Partial<ScanoInference>): void {
  state = {
    inference: next.inference ?? state.inference,
    passes: clampPasses(next.passes ?? state.passes),
  }
  try {
    localStorage.setItem(KEY, JSON.stringify(state))
  } catch {
    /* private window */
  }
  listeners.forEach((fn) => fn())
}

/** The request body keys of an AI solve or a real-time update. */
export function scanoInferenceRequest(): ScanoInference {
  return { ...state }
}
