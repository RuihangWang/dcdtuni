/**
 * The AI adaptive-region inference panel's server calls.
 *
 * A worker is started with a model checkpoint and a case, an axis-aligned box
 * is placed in the room, and the model predicts the field inside it — which
 * comes back as extra fields (`AI_T`, …) the volume renderer then shows.
 *
 * ## The model path is NEVER hardcoded
 *
 * It is fetched from `/api/pp/ai/demo-model-path`, and the legacy editor made
 * the same change for the same reason: a hardcoded absolute path carried the
 * developer's username and a private repository name, and this project is under
 * anonymous review — the association chain is exactly that pair. A default
 * baked in here would ship both to every browser that opens the editor.
 */

import { assertSessionIdentity, revisionHeaders, type PostprocessSession } from './ppRevision'

export interface AiBox {
  min: [number, number, number]
  max: [number, number, number]
}

export interface AiStartResult extends PostprocessSession {
  success?: boolean
  error?: string
  predictionSource: 'independent_h5_graph'
  timeConditioned: false
  status?: { bounds?: AiBox; ready?: boolean }
  result?: { bounds?: AiBox; warmup?: Record<string, unknown> }
}

export interface AiInferResult extends PostprocessSession {
  success?: boolean
  error?: string
  predictionSource: 'independent_h5_graph'
  timeConditioned: false
  fields?: { name: string; type?: string; label?: string }[]
  result?: { ranges?: Record<string, [number, number]> }
}

/** The demo checkpoint the SERVER knows about, or '' when none is configured. */
export async function fetchDemoModelPath(): Promise<string> {
  try {
    const resp = await fetch('/api/pp/ai/demo-model-path')
    const body = (await resp.json()) as { path?: string }
    return body.path ?? ''
  } catch {
    // A missing endpoint is not an error worth surfacing: the field is meant to
    // be typed into, and the button is a convenience.
    return ''
  }
}

export async function startAiWorker(
  modelPath: string, casePath: string, session: PostprocessSession,
): Promise<AiStartResult> {
  const resp = await fetch('/api/pp/ai/start', {
    method: 'POST',
    headers: revisionHeaders(session, true),
    body: JSON.stringify({ modelPath, casePath }),
  })
  const data = (await resp.json()) as AiStartResult
  if (!resp.ok || !data.success) throw new Error(data.error ?? resp.statusText)
  assertSessionIdentity(data, session)
  if (data.predictionSource !== 'independent_h5_graph' || data.timeConditioned !== false) {
    throw new Error('AI response lacks independent H5/time provenance')
  }
  return data
}

export async function runAiInference(box: AiBox, session: PostprocessSession): Promise<AiInferResult> {
  const resp = await fetch('/api/pp/ai/infer', {
    method: 'POST',
    headers: revisionHeaders(session, true),
    body: JSON.stringify({ box }),
  })
  const data = (await resp.json()) as AiInferResult
  if (!resp.ok || !data.success) throw new Error(data.error ?? resp.statusText)
  if (data.runId !== session.runId || data.timeValue !== session.timeValue ||
      !data.revision || data.revision === session.revision) {
    throw new Error('AI inference returned a stale or unrelated postprocess revision')
  }
  if (data.predictionSource !== 'independent_h5_graph' || data.timeConditioned !== false) {
    throw new Error('AI response lacks independent H5/time provenance')
  }
  return data
}

/**
 * Order a box's corners so min really is the minimum.
 *
 * A user dragging a bound past its partner produces an inverted box, which the
 * server reads as an empty region — an inference that returns nothing and looks
 * like a model failure.
 */
export function normaliseBox(box: AiBox): AiBox {
  const min: [number, number, number] = [0, 0, 0]
  const max: [number, number, number] = [0, 0, 0]
  for (let i = 0; i < 3; i++) {
    min[i] = Math.min(box.min[i] as number, box.max[i] as number)
    max[i] = Math.max(box.min[i] as number, box.max[i] as number)
  }
  return { min, max }
}
