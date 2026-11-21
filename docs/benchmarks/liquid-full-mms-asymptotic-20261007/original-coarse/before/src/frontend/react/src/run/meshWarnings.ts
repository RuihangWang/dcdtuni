/**
 * What checkMesh actually found.
 *
 * The server does not just say pass/fail: `_summarize_check_mesh_result`
 * (server.py:846) turns the checkMesh output into a severity-levelled list —
 * negative-volume cells, skewness, non-orthogonality, disconnected regions —
 * and sends it as `meshWarnings` on the job's final event.
 *
 * That list is the difference between "3 warnings" and knowing whether the mesh
 * is solvable. It is also what the pre-solve gate reads: a level 1 or 2 warning
 * means the run is expected to diverge, and a solve is hours.
 *
 * The session store keeps that final field even if its stream falls back to
 * polling, so this feed can use one source for both mesh and checkMesh jobs.
 */

import * as session from './session'
import { isCasePathContextCurrent } from './caseDir'

export interface MeshWarning {
  /** 1 critical, 2 warning, 3 informational — the server's own scale. */
  level: number
  code?: string
  message?: string
  count?: number
  max_value?: number
  threshold?: number
}

let warnings: MeshWarning[] = []
let warningsContext: number | null = null
let rejectedContext: number | null = null

type Listener = () => void
const listeners = new Set<Listener>()

export function subscribeMeshWarnings(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getMeshWarnings(): readonly MeshWarning[] {
  return warningsContext === null || isCasePathContextCurrent(warningsContext) ? warnings : []
}

/** A real failed checker cannot be overridden by the warning dialog. */
export function hasRejectedMeshQuality(): boolean {
  return rejectedContext !== null && isCasePathContextCurrent(rejectedContext)
}

/**
 * Warnings serious enough to stop a solve.
 *
 * Level <= 2, the legacy threshold (STL_generate.html:28358). Level 3 is
 * informational — a large second mesh region is what a cold-aisle containment
 * looks like, and gating on it would put a dialog in front of every correctly
 * built containment case.
 */
export function blockingMeshWarnings(): MeshWarning[] {
  return getMeshWarnings().filter((w) => w.level <= 2)
}

export function setMeshWarnings(next: MeshWarning[], context: number | null = null): void {
  warnings = next
  warningsContext = context
  listeners.forEach((fn) => fn())
}

function isWarning(value: unknown): value is MeshWarning {
  return !!value && typeof value === 'object' && typeof (value as MeshWarning).level === 'number'
}

/**
 * A writable handle, mirroring the legacy's.
 *
 * The legacy keeps this list in `uiState.session.meshWarnings`, a plain field
 * anyone can assign from the console — which is how its pre-solve dialog is
 * checkable without first building a broken mesh. Without the same handle here,
 * the only way to see this app's dialog is to produce a mesh with
 * negative-volume cells on purpose.
 *
 * Dev only, like `window.__dcdtuni` (main.tsx:15): a production build has no
 * business letting a page script decide what checkMesh found.
 */
export function installMeshWarningBridge(): void {
  if (!import.meta.env.DEV) return
  ;(window as unknown as { _setMeshWarnings?: (w: MeshWarning[]) => void })._setMeshWarnings =
    setMeshWarnings
}

let installed = false
let handledQualityJobId: string | null = null

/**
 * Capture `meshWarnings` from mesh generation and explicit checkMesh runs.
 *
 * A new mesh replaces the old quality verdict. The mesh job now carries its
 * final checkMesh result, so the solver gate reflects the mesh just generated.
 */
export function installMeshWarningFeed(): void {
  if (installed) return
  installed = true
  // The session's primary stream (or fallback poll) carries the final quality
  // payload. Reading it once avoids a second SSE connection and stale results.
  const captureCompletedQuality = () => {
    const job = session.getJob()
    if (!job || job.running || job.id === handledQualityJobId) return
    const solveQualityRefusal = job.kind === 'solve' && job.checkMeshOk === false
    if (job.kind !== 'mesh' && job.kind !== 'checkMesh' && !solveQualityRefusal) return
    if (!isCasePathContextCurrent(job.caseContext)) return
    const rejected = job.checkMeshOk === false || (job.failedChecks ?? 0) > 0
      || (job.returncode !== null && job.returncode !== 0)
    rejectedContext = rejected ? job.caseContext : null
    if (job.meshWarnings === null) return
    handledQualityJobId = job.id
    setMeshWarnings(job.meshWarnings.filter(isWarning), job.caseContext)
  }
  session.subscribeSession(captureCompletedQuality)
  captureCompletedQuality() // the solver panel may mount after a job finished
}
