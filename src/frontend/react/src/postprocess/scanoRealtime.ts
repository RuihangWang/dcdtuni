/**
 * Real-time AI inference of what is on screen. Edits made while a request is in flight collapse into one follow-up
 * with the latest state.
 */

import * as store from '../projectStore'
import { rebindAirParameters } from '../legacy/cfdrunBuild'
import { buildRunRequest } from '../run/runSubmit'
import { readCasePath } from '../run/caseDir'
import { scanoInferenceRequest } from '../run/scanoInference'
import type { CfdrunV2 } from '../schema/cfdrun'
import type { PostprocessSession } from './ppRevision'
import { rebuildSceneFromProject } from '../renderer/sceneRebuild'

export interface RealtimeResult {
  ok: boolean
  injected: boolean
  ppSession?: PostprocessSession
  runDirRelative?: string
  changedPatches: string[]
  warnings?: string[]
  timing: Record<string, number>
  roundTripMs: number
  manifestMs: number
  manifestMode: 'rebind' | 'full'
}

export interface RealtimeState {
  running: boolean
  queued: boolean
  last: RealtimeResult | null
  error: string | null
  code: string | null
}

type Listener = () => void
type ResultListener = (result: RealtimeResult) => void

let state: RealtimeState = { running: false, queued: false, last: null, error: null, code: null }
const listeners = new Set<Listener>()
const resultListeners = new Set<ResultListener>()
let queuedModel: string | null = null

function emit(next: Partial<RealtimeState>) {
  state = { ...state, ...next }
  for (const fn of listeners) fn()
}

export function getRealtimeState(): RealtimeState {
  return state
}

export function subscribeRealtimeState(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function subscribeScanoRealtime(fn: ResultListener): () => void {
  resultListeners.add(fn)
  return () => resultListeners.delete(fn)
}

/** Boundary-condition values and display state: the rest of the project decides whether the cached manifest holds. */
const PARAMETERS = new Set(['volumeFlowRate', 'power', 'temperature', 'setPointTemperature', 'velocity'])

const DISPLAY = /opacit|visib|colou?r|highlight|selected|expanded|collapsed/i

export function geometryKey(project: unknown): string {
  return JSON.stringify(project, (key, value) => {
    if (PARAMETERS.has(key) || DISPLAY.test(key)) return null
    if (key === 'keyframes' && typeof (value as unknown[])?.length === 'number') return (value as unknown[]).length
    return value
  })
}

let manifestCache: { key: string; manifest: CfdrunV2 } | null = null

let lastMode: 'rebind' | 'full' = 'full'

/** Build the manifest cache ahead of the first edit. */
export async function prepareRealtime(): Promise<void> {
  if (!manifestCache) await realtimeManifest()
}

/** A full export when anything but a boundary value changed; else the cached manifest with its bindings recomputed. */
async function realtimeManifest(): Promise<string> {
  const project = store.getProject()
  const key = geometryKey(project)
  if (project && manifestCache && manifestCache.key === key && rebindAirParameters(manifestCache.manifest, project)) {
    lastMode = 'rebind'
    return JSON.stringify(manifestCache.manifest)
  }
  lastMode = 'full'
  // The export checks the scene against the project; post-processing does not rebuild the scene on edits.
  await rebuildSceneFromProject()
  const body = await buildRunRequest('solve')
  manifestCache = project ? { key, manifest: JSON.parse(body.cfdrunJson) as CfdrunV2 } : null
  return body.cfdrunJson
}

async function runOnce(model: string): Promise<void> {
  const started = performance.now()
  const cfdrunJson = await realtimeManifest()
  const manifestMs = performance.now() - started
  const resp = await fetch('/api/ai/scano/realtime', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cfdrunJson, model, caseDir: readCasePath(), ...scanoInferenceRequest() }),
  })
  const reply = (await resp.json()) as Partial<RealtimeResult> & { error?: string; code?: string }
  if (!resp.ok) {
    emit({ error: reply.error ?? String(resp.status), code: reply.code ?? null })
    return
  }
  const result: RealtimeResult = {
    ok: true,
    injected: !!reply.injected,
    ppSession: reply.ppSession,
    runDirRelative: reply.runDirRelative,
    changedPatches: reply.changedPatches ?? [],
    warnings: reply.warnings,
    timing: reply.timing ?? {},
    roundTripMs: performance.now() - started,
    manifestMs,
    manifestMode: lastMode,
  }
  emit({ last: result, error: null, code: null })
  for (const fn of resultListeners) fn(result)
}

export async function requestRealtime(model: string): Promise<void> {
  if (state.running) {
    queuedModel = model
    emit({ queued: true })
    return
  }
  emit({ running: true, queued: false, error: null })
  try {
    let next: string | null = model
    while (next) {
      queuedModel = null
      await runOnce(next)
      next = queuedModel
      if (next) emit({ queued: false })
    }
  } catch (err) {
    emit({ error: err instanceof Error ? err.message : String(err) })
  } finally {
    emit({ running: false, queued: false })
  }
}

/** Test seam. */
export function resetRealtimeForTest(): void {
  state = { running: false, queued: false, last: null, error: null, code: null }
  queuedModel = null
  manifestCache = null
  listeners.clear()
  resultListeners.clear()
}
