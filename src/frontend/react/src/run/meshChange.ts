import * as store from '../projectStore'
import * as session from './session'
import { readCasePath } from './caseDir'
import { buildStlUpload } from './runSubmit'

/**
 * What the current geometry asks of the case's mesh (server `/api/mesh-change`):
 *   none      solve or infer on the mesh as it is
 *   patches   the same surface divided into other patches: a patch update keeps the cells
 *   geometry  the surface changed: generate the mesh again
 *   mesh      no mesh yet
 * Checked for the project revision it was asked about; a newer edit makes it stale.
 */

export type MeshChangeKind = 'none' | 'patches' | 'geometry' | 'mesh'

/** Why the solve / AI solve buttons wait: no verdict for this revision yet, or the mesh does not fit. */
export type MeshBlock = 'pending' | 'mismatch' | null

export interface MeshChange {
  kind: MeshChangeKind
  reason?: string
  added?: string[]
  removed?: string[]
  changed?: string[]
  /** Patches whose boundary role changed since the mesh was generated: {patch: [old bcType, new bcType]}. */
  retype?: Record<string, (string | null)[]>
  /** With reason 'settings': the mesh settings that changed since the mesh was generated. */
  settings?: string[]
}

export interface MeshChangeState {
  checking: boolean
  /** The document revision the result describes. */
  revision: number | null
  /** The case directory it was checked against. */
  caseDir?: string | null
  result: MeshChange | null
  error: string | null
}

let state: MeshChangeState = { checking: false, revision: null, result: null, error: null }
const listeners = new Set<() => void>()
let inflight: AbortController | null = null

function set(next: Partial<MeshChangeState>): void {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

export function getMeshChange(): MeshChangeState {
  return state
}

export function subscribeMeshChange(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** What a run waits for: a verdict for the current edit, then a mesh that fits it. A failed check holds nothing,
 * since the runner still refuses a mesh its receipt does not match. */
export function meshBlockFor(change: MeshChange | null, error: string | null): MeshBlock {
  if (!change) return error ? null : 'pending'
  return change.kind === 'patches' || change.kind === 'geometry' ? 'mismatch' : null
}

/** The block at this moment, for a click handler: the button's own disabled state is one render behind an edit. */
export function meshBlockNow(): MeshBlock {
  return session.isMeshPresent() ? meshBlockFor(currentMeshChange(), state.error) : null
}

/** The result, when it describes the current document; else null. */
export function currentMeshChange(): MeshChange | null {
  return state.revision === store.documentInputRevision() && state.caseDir === readCasePath() ? state.result : null
}

export async function checkMeshChange(): Promise<MeshChange | null> {
  inflight?.abort()
  const controller = (inflight = new AbortController())
  const revision = store.documentInputRevision()
  const caseDir = readCasePath()
  if (!store.getProject()) return null
  set({ checking: true, error: null })
  try {
    const { cfdrunJson, stlFiles } = await buildStlUpload(controller.signal)
    const resp = await fetch('/api/mesh-change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ caseDir, cfdrunJson, stlFiles }),
      signal: controller.signal,
    })
    const body = (await resp.json()) as MeshChange & { error?: string }
    if (!resp.ok) throw new Error(body.error ?? String(resp.status))
    if (controller !== inflight) return null
    set({ checking: false, revision, caseDir, result: body, error: null })
    return body
  } catch (err) {
    if (controller !== inflight) return null
    set({ checking: false, error: err instanceof Error ? err.message : String(err) })
    return null
  } finally {
    if (controller === inflight) inflight = null
  }
}

let timer: ReturnType<typeof setTimeout> | null = null
let meshJobWatch: (() => void) | null = null

function meshJobRunning(): boolean {
  const job = session.getJob()
  return job?.kind === 'mesh' && !!job.running
}

/** A mesh job rewrites the mesh a verdict described: from its start there is none until the next check. Installed
 * once and kept, since the panels that watch are unmounted while a mesh job runs. */
function forgetVerdictOnMeshJobs(): void {
  if (meshJobWatch) return
  let running = meshJobRunning()
  meshJobWatch = session.subscribeSession(() => {
    const now = meshJobRunning()
    if (now !== running && (state.result !== null || state.revision !== null)) set({ result: null, revision: null })
    running = now
  })
}

let background: (() => void) | null = null
let backgroundTimer: ReturnType<typeof setTimeout> | null = null
/** The edit and case the background last asked about: a failed check is not retried until either changes. */
let backgroundTried: string | null = null

function backgroundReady(): boolean {
  return !!store.getProject() && !!readCasePath() && session.isValidated() && session.isMeshPresent()
    && !session.getJob()?.running
}

/**
 * Keep the verdict ready before anyone opens the solve tab (owner 2026-11-11): the panels' own watch
 * (`watchMeshChange`) only starts when they mount, and on a large room the check exports the whole scene,
 * which then made the solve button wait for seconds. Checks in the background
 *   - as soon as a mesh job (a full mesh or a patch update) finishes,
 *   - when a project with a mesh is opened, the case changes, or the case turns out to have a mesh,
 *   - after a job that outlived an edit, and after edits once editing has paused for `idleMs`.
 * Never while a job runs or without a mesh, never twice for the same edit and case. Installed once at
 * start-up; returns the undo.
 */
export function startBackgroundMeshChange(idleMs = 3000, soonMs = 300): () => void {
  if (background) return background
  forgetVerdictOnMeshJobs()
  const key = () => `${store.documentInputRevision()}|${readCasePath() ?? ''}`
  const schedule = (delayMs: number, force = false) => {
    if (backgroundTimer) clearTimeout(backgroundTimer)
    backgroundTimer = setTimeout(() => {
      backgroundTimer = null
      if (!backgroundReady()) return
      if (!force && (currentMeshChange() || state.checking || backgroundTried === key())) return
      backgroundTried = key()
      void checkMeshChange()
    }, delayMs)
  }
  // Snapshots, not live objects: the session may update its job in place.
  let editRevision = store.documentInputRevision()
  let meshRunning = meshJobRunning()
  let jobRunning = !!session.getJob()?.running
  let meshPresent = session.isMeshPresent()
  let validated = session.isValidated()
  let caseDir = readCasePath()
  // The store also notifies on repaints and log lines; only a document edit restarts the idle wait.
  const offStore = store.subscribe(() => {
    const now = store.documentInputRevision()
    if (now === editRevision) return
    editRevision = now
    schedule(idleMs)
  })
  const offLoad = store.subscribeProjectLoad(() => schedule(soonMs))
  const offSession = session.subscribeSession(() => {
    const nowMesh = meshJobRunning()
    const running = !!session.getJob()?.running
    const present = session.isMeshPresent()
    const valid = session.isValidated()
    const dir = readCasePath()
    if (meshRunning && !nowMesh) schedule(soonMs, true)
    else if ((jobRunning && !running) || (present && !meshPresent) || (valid && !validated) || dir !== caseDir) {
      schedule(soonMs)
    }
    meshRunning = nowMesh
    jobRunning = running
    meshPresent = present
    validated = valid
    caseDir = dir
  })
  schedule(soonMs)
  background = () => {
    if (backgroundTimer) clearTimeout(backgroundTimer)
    backgroundTimer = null
    offStore()
    offLoad()
    offSession()
    background = null
  }
  return background
}

/** Check now and again after each edit (debounced) and after each finished job; returns the undo. */
export function watchMeshChange(delayMs = 1200): () => void {
  forgetVerdictOnMeshJobs()
  const schedule = (force = false) => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      if (session.getJob()?.running) return
      if (force || !currentMeshChange()) void checkMeshChange()
    }, delayMs)
  }
  // Snapshots, not the job object: the session may update that object in place.
  let meshRunning = meshJobRunning()
  let jobRunning = !!session.getJob()?.running
  const offStore = store.subscribe(() => schedule())
  const offSession = session.subscribeSession(() => {
    const now = meshJobRunning()
    const running = !!session.getJob()?.running
    if (meshRunning && !now) schedule(true)
    // an edit made while a job ran was not checked (the timer stands down during jobs)
    else if (jobRunning && !running) schedule()
    meshRunning = now
    jobRunning = running
  })
  // On mount, only without a verdict for this edit and case: a mesh job voids the verdict whether or not a panel
  // watches (forgetVerdictOnMeshJobs), and on a large room every check exports the whole scene (Alibaba-hongchou:
  // seconds), which opening the CFD and then the AI sub-tab did twice.
  schedule()
  return () => {
    if (timer) clearTimeout(timer)
    timer = null
    offStore()
    offSession()
  }
}
