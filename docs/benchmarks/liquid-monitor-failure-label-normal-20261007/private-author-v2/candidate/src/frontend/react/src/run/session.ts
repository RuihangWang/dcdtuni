/**
 * Server session: what the backend can do, and running a job on it.
 *
 * A module-level store like `projectStore`, because the mesh panel, the solver
 * panel and the tab bar all read the same two facts (is the backend usable, is
 * a job running) and threading them through the tree is how the copies drift.
 */

import { installAuthGuard } from './auth'
import { readSolveAssessment, solveStatus, type SolveAssessment } from './solveAssessment'
import {
  beginCasePathChange, casePathContext, clearMissingCasePath, hasOpenedProject,
  installCasePathSync, isCasePathContextCurrent, readCasePath,
  syncCasePathFromServer, writeCasePath, type CasePathApplyOptions,
} from './caseDir'
import { buildRunRequest } from './runSubmit'
import { onSolveFinished } from './sensorResults'
import { renderServerBanner } from './serverBanner'
import {
  diagnoseServerBody,
  diagnoseServerFailure,
  type ServerDiagnosis,
} from './serverDiagnosis'
import { setUiFrozen } from './uiFreeze'
import { logAction, logOperation } from '../projectStore'
import { jobLogFinish, jobLogLine, jobLogStart } from './jobLogMirror'
import { t } from '../i18n'

export interface ServerInfo {
  dctwinAvailable: boolean
  dctwinVersion?: string
  pythonExecutable?: string
  caseDir?: string
  caseDirOk: boolean
  /** The single flag the legacy app gates the mesh and solver tabs on. */
  allOk: boolean
  cpuCount?: number
  wsPort?: number
  authRequired?: boolean
  /** Public OV signaling endpoint; needed when this React page uses a local HTTP tunnel. */
  ovSignalingHost?: string | null
  ovSignalingPort?: number
  /** False when this server was launched without --ov (no Kit bridge). */
  ovBridge?: boolean
}

export type JobKind = 'mesh' | 'solve' | 'checkMesh'

export interface JobState {
  id: string
  kind: JobKind
  caseContext: number
  /** Directory captured by this request; never read from another tab's backend selection. */
  caseDir?: string
  executionCaseDir?: string
  cfdrunSha256?: string
  solveAssessment?: SolveAssessment | null
  running: boolean
  /** True only for a confirmed cancelled terminal job, not a stop request. */
  aborted: boolean
  /** A stop request is in flight; separate from backend completion. */
  abortPending?: boolean
  /** The backend accepted a stop request, but may still hold the case lock. */
  abortRequested?: boolean
  returncode: number | null
  checkMeshOk: boolean | null
  failedChecks: number | null
  meshWarnings: unknown[] | null
  lines: string[]
  error: string | null
}

/** What the server finds already on disk for the current case. */
export interface CaseStatus {
  /** Physical mesh presence; checking remains possible before strict acceptance. */
  meshExists?: boolean
  /** Accepted strict quality bound to the current mesh and case input bytes. */
  meshReady: boolean
  solveReady: boolean
  liquidReady: boolean
  postprocessReady: boolean
  logRuns: { name: string; path: string; label?: string; timesteps?: number[] }[]
  liquidRuns: unknown[]
}

interface SessionState {
  info: ServerInfo | null
  infoError: string | null
  /** Why the last `/api/server-info` did not come back usable. Null before the first try. */
  diagnosis: ServerDiagnosis | null
  /** Explicit successful strict quality for this case — gates the solver tab. */
  meshReady: boolean
  status: CaseStatus | null
  job: JobState | null
}

let state: SessionState = {
  info: null,
  infoError: null,
  diagnosis: null,
  meshReady: false,
  status: null,
  job: null,
}

// Installed here, at the module scope of the first thing that talks to the
// server: the guard has to wrap `fetch` before any request is made, and every
// path into the backend goes through a module that loads after this one. A 401
// otherwise fails silently and the panel it belonged to simply stops updating.
installAuthGuard()

// Opening a project re-points the server at THAT project's case directory. Set
// up here, at module scope, because a project can be opened before any run panel
// has ever been mounted — and the case dir is wrong from that moment on.
installCasePathSync(setCaseDir, invalidateCaseReadiness)

let caseWrites: Promise<unknown> = Promise.resolve()
let pendingCaseWrite: { context: number; promise: Promise<boolean> } | null = null
let serverInfoSequence = 0
let caseStatusSequence = 0
// A scan from before a quality job's success cannot suppress that new verdict.
// Results/logRuns stay available; only the mesh-readiness fact gets this stamp.
let meshReadinessGeneration = 0
let statusMeshReadinessGeneration = -1

function invalidateMeshReadinessScan(): void {
  ++meshReadinessGeneration
  ++caseStatusSequence
}

type Listener = () => void
const listeners = new Set<Listener>()

function emit() {
  // Driven from here rather than from the two run buttons so it cannot be
  // half-applied: every path that ends a job — the stream's done event, the
  // fallback poll, an error — goes through `emit`, and each one has to thaw.
  setUiFrozen(isUiFrozen())
  // Same reason: the banner is the only thing that says WHY the run tabs are
  // locked, and those tabs are disabled in exactly the state that produces it —
  // so it cannot live inside a panel that state makes unreachable.
  renderServerBanner(state.diagnosis)
  listeners.forEach((fn) => fn())
}

export function subscribeSession(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getServerInfo(): ServerInfo | null {
  return state.info
}

export function getServerInfoError(): string | null {
  return state.infoError
}

/**
 * Why the backend is not answering, in the words the legacy uses.
 *
 * Drawn by `serverBanner.ts` on every emit: without it a down backend, a proxy
 * pointed at the wrong `--api-port` and a case directory that was never set all
 * render as the same greyed-out tab.
 */
export function getServerDiagnosis(): ServerDiagnosis | null {
  return state.diagnosis
}

export function getJob(): JobState | null {
  return state.job
}

/**
 * Is the editor frozen for a running job?
 *
 * A mesh or a solve holds the whole UI, because both run against the geometry
 * that was uploaded when they started and an edit made meanwhile is not in the
 * run. `checkMesh` does not: it only reads the mesh already on disk, so nothing
 * a user changes while it runs can make its verdict wrong.
 */
export function isUiFrozen(): boolean {
  return !!state.job?.running && state.job.kind !== 'checkMesh'
}

export function getCaseStatus(): CaseStatus | null {
  return state.status
}

/**
 * Has this case's mesh passed the strict solve gate?
 *
 * A fresh disk scan restores acceptance across reloads and can revoke stale
 * cached readiness. A current failed/running quality job still overrides an
 * older scan. Physical polyMesh presence alone cannot authorize solving.
 */
export function isMeshReady(): boolean {
  const job = getCurrentCaseJob()
  if (job && (job.kind === 'mesh' || job.kind === 'checkMesh')) {
    if (job.running || job.aborted || job.returncode !== 0 || job.checkMeshOk !== true ||
        (job.failedChecks !== null && job.failedChecks !== 0)) return false
  }
  return state.status && statusMeshReadinessGeneration === meshReadinessGeneration
    ? state.status.meshReady === true : state.meshReady
}

/** Retain old jobs for monitoring, but never present them as this case's result. */
export function getCurrentCaseJob(): JobState | null {
  const job = state.job
  return job && isCasePathContextCurrent(job.caseContext) ? job : null
}

export function isSolveReady(): boolean {
  return !!state.status?.solveReady
}

export function isPostprocessReady(): boolean {
  return !!state.status?.postprocessReady
}

/** Ask the server what the current case directory already contains. */
async function scanForContext(context: number): Promise<CaseStatus | null> {
  const sequence = ++caseStatusSequence
  const meshGeneration = meshReadinessGeneration
  try {
    const caseDir = readCasePath()
    const resp = await fetch(`/api/scan-case-status?caseDir=${encodeURIComponent(caseDir)}`, { cache: 'no-store' })
    if (!resp.ok) throw new Error(`server returned ${resp.status}`)
    const status = (await resp.json()) as CaseStatus
    if (!isCasePathContextCurrent(context) || sequence !== caseStatusSequence ||
        meshGeneration !== meshReadinessGeneration) return null
    statusMeshReadinessGeneration = meshGeneration
    state = { ...state, status }
    emit()
    return status
  } catch {
    // A failed scan is not an error worth surfacing: it just means readiness
    // falls back to what this session did itself.
    return null
  }
}

export async function scanCaseStatus(): Promise<CaseStatus | null> {
  const context = casePathContext()
  if (pendingCaseWrite?.context === context) await pendingCaseWrite.promise
  if (!isCasePathContextCurrent(context) || !state.info?.allOk) return null
  return scanForContext(context)
}

function invalidateCaseReadiness(): void {
  ++serverInfoSequence
  invalidateMeshReadinessScan()
  statusMeshReadinessGeneration = -1
  state = { ...state, info: null, infoError: null, diagnosis: null, meshReady: false, status: null }
  emit()
}

/** True when the backend can actually run something. Gates the mesh/solver tabs. */
export function isValidated(): boolean {
  return !!state.info?.allOk
}

/**
 * Ask the backend what it can do — and SAY SO when it cannot answer.
 *
 * The body is read as TEXT and parsed here rather than through `resp.json()`,
 * because the commonest failure produces a body that parses badly for a reason
 * worth naming: a Vite proxy aimed at the wrong API port answers `/api/*` with
 * index.html, and `resp.json()` turns that into "Unexpected token <" — a
 * message about a character, thrown away, where the legacy says "server.py not
 * running (received HTML response). Please start with start_server.sh".
 */
async function fetchServerInfo(context: number): Promise<ServerInfo | null> {
  const sequence = ++serverInfoSequence
  let probe
  try {
    const resp = await fetch('/api/server-info', { cache: 'no-store' })
    probe = diagnoseServerBody(await resp.text(), resp.status, resp.ok)
  } catch (err) {
    if (!isCasePathContextCurrent(context) || sequence !== serverInfoSequence) return null
    const diagnosis = diagnoseServerFailure(err)
    state = {
      ...state,
      info: null,
      infoError: t(diagnosis.key, diagnosis.vars),
      diagnosis,
    }
    logOperation('refreshServerInfo', t(diagnosis.key, diagnosis.vars))
    emit()
    return null
  }
  if (!isCasePathContextCurrent(context) || sequence !== serverInfoSequence) return null
  const { diagnosis, info } = probe
  if (info) return info
  state = {
    ...state,
    info,
    infoError: info ? null : t(diagnosis.key, diagnosis.vars),
    diagnosis,
  }
  logOperation('refreshServerInfo', t(diagnosis.key, diagnosis.vars))
  emit()
  return null
}

async function publishServerInfo(context: number, info: ServerInfo): Promise<ServerInfo | null> {
  if (!isCasePathContextCurrent(context)) return null
  const saved = readCasePath()
  const matches = !!saved && info.caseDir === saved && info.caseDirOk
  const effective = matches ? info : { ...info, caseDir: undefined, caseDirOk: false, allOk: false }
  const { diagnosis } = diagnoseServerBody(JSON.stringify(effective), 200, true)
  state = { ...state, info: effective, infoError: null, diagnosis }
  logOperation('refreshServerInfo', t(diagnosis.key, diagnosis.vars))
  emit()
  if (effective.allOk) await scanForContext(context)
  return isCasePathContextCurrent(context) ? effective : null
}

export async function refreshServerInfo(): Promise<ServerInfo | null> {
  const context = casePathContext()
  if (pendingCaseWrite?.context === context) {
    await pendingCaseWrite.promise
    return isCasePathContextCurrent(context) ? state.info : null
  }
  const info = await fetchServerInfo(context)
  if (!info || !isCasePathContextCurrent(context)) return null
  const saved = readCasePath()
  if (saved && (info.caseDir !== saved || !info.caseDirOk)) {
    // A restarted backend may have forgotten the case. Restore the project's
    // own directory instead of copying a different server path into the file.
    await setCaseDir(saved, { automatic: true, createIfMissing: false, context })
    return isCasePathContextCurrent(context) ? state.info : null
  }
  if (!saved && !hasOpenedProject() && info.caseDirOk) syncCasePathFromServer(info.caseDir)
  return publishServerInfo(context, info)
}

/**
 * Point the server at a case directory. Process-wide on the server — see CLAUDE.md.
 *
 * The path is written into the PROJECT as well, which is what makes the choice
 * survive a save and be re-applied on the next open.
 */
export function setCaseDir(caseDir: string, options: CasePathApplyOptions = {}): Promise<boolean> {
  const context = options.context ?? beginCasePathChange()
  caseDir = caseDir.trim()
  if (!isCasePathContextCurrent(context)) return Promise.resolve(false)
  invalidateCaseReadiness()
  const apply = async (): Promise<boolean> => {
    if (!isCasePathContextCurrent(context)) return false
    try {
      const resp = await fetch('/api/set-case-dir', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caseDir, createIfMissing: options.createIfMissing !== false }),
      })
      const result = await resp.json() as { ok?: boolean; caseDir?: string; error?: string; code?: string }
      if (!isCasePathContextCurrent(context)) return false
      if (!resp.ok || result.ok !== true || typeof result.caseDir !== 'string') {
        const missing = options.automatic && (
          (resp.status === 404 && result.code === 'case_dir_missing') ||
          (resp.status === 400 && result.code === 'case_dir_not_directory')
        )
        if (missing) clearMissingCasePath(caseDir)
        const diagnosis: ServerDiagnosis = missing
          ? { kind: 'abnormal', key: 'log.case_path_invalid', vars: { path: caseDir } }
          : { kind: 'abnormal', key: 'log.case_path_restore_failed', vars: { path: caseDir, error: result.error ?? resp.statusText } }
        const message = t(diagnosis.key, diagnosis.vars)
        state = { ...state, infoError: message, diagnosis }
        logOperation('setCaseDir', message)
        emit()
        return false
      }
      if (options.automatic) syncCasePathFromServer(result.caseDir, true)
      else writeCasePath(result.caseDir)
      const info = await fetchServerInfo(context)
      if (!info || !isCasePathContextCurrent(context)) return false
      const applied = await publishServerInfo(context, info)
      if (applied?.caseDirOk && options.automatic) {
        logAction('loadProject', t('log.case_path_valid', { path: result.caseDir }))
      }
      return !!applied?.caseDirOk
    } catch (error) {
      if (!isCasePathContextCurrent(context)) return false
      const diagnosis: ServerDiagnosis = {
        kind: 'unreachable', key: 'log.case_path_restore_failed',
        vars: { path: caseDir, error: error instanceof Error ? error.message : String(error) },
      }
      const message = t(diagnosis.key, diagnosis.vars)
      state = { ...state, infoError: message, diagnosis }
      logOperation('setCaseDir', message)
      emit()
      return false
    }
  }
  // Includes authentication waits/replays, so an old POST cannot arrive after
  // a newer project's POST and silently become the backend's final directory.
  const promise = caseWrites.catch(() => {}).then(apply)
  caseWrites = promise
  pendingCaseWrite = { context, promise }
  void promise.finally(() => {
    if (pendingCaseWrite?.promise === promise) pendingCaseWrite = null
  })
  return promise
}

/** The most recent lines are what a user watches; the rest is scrollback. */
const MAX_LINES = 2000

/**
 * Start a mesh or solve run and stream its log until it finishes.
 *
 * The geometry ON SCREEN is what runs. A freshly built `.cfdrun` manifest goes
 * in the request every time, plus the grouped STL set for a mesh run, the way
 * `runMeshGeneration` (STL_generate.html:28054) and `runSolveOnly` (:28348)
 * submit theirs — see `runPayload.ts` for the mesh/solve split. Sending neither
 * makes the server fall back to whatever is already in `<case>/model/` and
 * `<case>/Mesh/constant/triSurface/`, which is what this used to do: it re-ran
 * the last export, so every edit made in this app was silently left out of the
 * run it appeared to start.
 */
export async function runJob(kind: JobKind): Promise<void> {
  if (state.job?.running) throw new Error('a job is already running')
  if (kind === 'checkMesh') throw new Error('use runCheckMesh for a checkMesh job')

  const context = casePathContext()
  const caseDir = readCasePath()
  const body = await buildRunRequest(kind)
  if (!isCasePathContextCurrent(context)) return
  const resp = await fetch('/api/run-mesh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, caseDir }),
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      detail = ((await resp.json()) as { error?: string }).error ?? detail
    } catch {
      /* body was not JSON; the status text is all there is */
    }
    throw new Error(detail)
  }
  const { jobId, caseDir: executionCaseDir, cfdrunSha256 } = (await resp.json()) as { jobId?: string; caseDir?: string; cfdrunSha256?: string }
  if (!jobId) throw new Error('server did not return a jobId')

  if (kind === 'mesh' && isCasePathContextCurrent(context)) invalidateMeshReadinessScan()
  state = {
    ...state,
    job: {
      id: jobId,
      kind,
      caseContext: context,
      caseDir,
      executionCaseDir, cfdrunSha256, solveAssessment: null,
      running: true,
      aborted: false,
      returncode: null,
      checkMeshOk: null,
      failedChecks: null,
      meshWarnings: null,
      lines: [],
      error: null,
    },
  }
  emit()
  jobLogStart(kind, jobId)
  streamLog(jobId, context)
}

/**
 * Run OpenFOAM `checkMesh` on the mesh already on disk.
 *
 * Two steps, as the legacy `checkMeshReady` does: `/api/check-mesh` first,
 * which is a cheap existence probe, then `/api/run-check-mesh`, which starts a
 * real job. Skipping the probe means a case with no mesh spends a job slot to
 * be told there is no mesh.
 */
export async function runCheckMesh(): Promise<void> {
  if (state.job?.running) throw new Error('a job is already running')

  const context = casePathContext()
  const caseDir = readCasePath()
  const exists = await fetch(`/api/check-mesh?caseDir=${encodeURIComponent(caseDir)}`)
  const existsBody = (await exists.json()) as { meshReady?: boolean; error?: string }
  if (!exists.ok) throw new Error(existsBody.error ?? `server returned ${exists.status}`)
  if (!isCasePathContextCurrent(context)) return
  if (!existsBody.meshReady) {
    // An explicit missing mesh supersedes an older accepted disk scan.
    invalidateMeshReadinessScan()
    state = { ...state, meshReady: false }
    emit()
    throw new Error('mesh not found')
  }

  const resp = await fetch('/api/run-check-mesh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ caseDir }),
  })
  const body = (await resp.json()) as { jobId?: string; error?: string }
  if (body.error) throw new Error(body.error)
  if (!body.jobId) throw new Error('server did not return a jobId')

  if (isCasePathContextCurrent(context)) invalidateMeshReadinessScan()
  state = {
    ...state,
    job: {
      id: body.jobId,
      kind: 'checkMesh',
      caseContext: context,
      caseDir,
      running: true,
      aborted: false,
      returncode: null,
      checkMeshOk: null,
      failedChecks: null,
      meshWarnings: null,
      lines: [],
      error: null,
    },
  }
  emit()
  jobLogStart('checkMesh', body.jobId)
  streamLog(body.jobId, context)
}

interface JobStatus {
  done?: boolean
  returncode?: number | null
  checkMeshOk?: boolean | null
  failedChecks?: number | null
  meshWarnings?: unknown[]
  cancelRequested?: boolean
  cancelled?: boolean
  solveAssessment?: unknown
}

/** One terminal transition for SSE and polling, including current-case refresh. */
function finishJob(jobId: string, context: number, final: JobStatus): void {
  const job = state.job
  if (!job || job.id !== jobId || !job.running || final.done !== true) return
  const currentCase = isCasePathContextCurrent(context)
  const aborted = final.cancelled === true || final.cancelRequested === true ||
    (job.abortRequested === true && final.returncode !== 0)
  const ok = final.returncode === 0 && !aborted
  const qualityOk = ok && final.checkMeshOk === true &&
    (final.failedChecks == null || final.failedChecks === 0)
  if (currentCase && (job.kind === 'mesh' || job.kind === 'checkMesh')) {
    invalidateMeshReadinessScan()
  }
  state = {
    ...state,
    meshReady: !currentCase ? state.meshReady
      : job.kind === 'mesh' || job.kind === 'checkMesh' ? qualityOk
      : state.meshReady,
    job: {
      ...job, running: false, aborted, abortPending: false,
      returncode: final.returncode ?? null,
      checkMeshOk: final.checkMeshOk ?? null,
      failedChecks: final.failedChecks ?? null,
      meshWarnings: currentCase ? (Array.isArray(final.meshWarnings) ? final.meshWarnings : []) : null,
      solveAssessment: currentCase && job.kind === 'solve' && ok
        ? readSolveAssessment(final.solveAssessment, job.executionCaseDir, job.cfdrunSha256) : null,
      error: null,
    },
  }
  jobSources.get(jobId)?.close()
  jobSources.delete(jobId)
  emit()
  // A background job can end after a project switch; its files and logs belong
  // to the old case and cannot establish readiness for the new one.
  if (!currentCase) return
  jobLogFinish(job.kind, t('log.job_finished', {
    kind: job.kind, code: String(final.returncode ?? '?'),
  }))
  if (ok && job.kind === 'mesh') {
    const casePath = job.caseDir ? `${job.caseDir.replace(/\/+$/, '')}/Mesh` : ''
    logAction('dctwin', t('log.mesh_complete', { path: casePath }))
    logAction('dctwin', t('log.paraview_hint', { path: casePath }))
  }
  if (ok && job.kind === 'solve') {
    logAction('dctwin', t(solveStatus(state.job!).key))
    logAction('dctwin', t('log.switch_to_postprocess'))
  }
  void scanCaseStatus()
  // An interrupted solve can still have valid earlier probe rows.
  if (job.kind === 'solve') void onSolveFinished()
}

const jobSources = new Map<string, EventSource>()
const pollingJobs = new Set<string>()

function streamLog(jobId: string, context: number) {
  const source = new EventSource(`/api/log-stream?job_id=${encodeURIComponent(jobId)}`)
  jobSources.set(jobId, source)

  source.onmessage = (event) => {
    if (!state.job || state.job.id !== jobId || !state.job.running) return
    const jobKind = state.job.kind
    let payload: unknown
    try {
      payload = JSON.parse(event.data)
    } catch {
      payload = event.data
    }
    if (typeof payload === 'string') {
      const lines = [...state.job.lines, payload]
      if (lines.length > MAX_LINES) lines.splice(0, lines.length - MAX_LINES)
      state = { ...state, job: { ...state.job, lines } }
      emit()
      jobLogLine(jobKind, payload)
      return
    }
    if (payload && typeof payload === 'object' && (payload as JobStatus).done === true) {
      finishJob(jobId, context, payload as JobStatus)
    }
  }

  source.onerror = () => {
    // Polling may already have closed this stream on terminal completion.
    if (jobSources.get(jobId) === source) {
      source.close()
      jobSources.delete(jobId)
    }
    if (!state.job || state.job.id !== jobId || !state.job.running) return
    void pollUntilDone(jobId, context)
  }
}

/** HTTP failure cannot prove the process ended; retain the lock and retry. */
function jobRequestError(jobId: string, error: unknown): void {
  const job = state.job
  if (!job || job.id !== jobId || !job.running) return
  state = { ...state, job: {
    ...job, error: error instanceof Error ? error.message : String(error),
  } }
  emit()
}

async function responseBody(resp: Response): Promise<Record<string, unknown>> {
  const body: unknown = await resp.json()
  if (!resp.ok) {
    throw new Error(body && typeof body === 'object' && 'error' in body
      ? String(body.error) : `server returned ${resp.status}`)
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new Error('server returned an invalid job response')
  }
  return body as Record<string, unknown>
}

async function pollUntilDone(jobId: string, context: number, immediate = false): Promise<void> {
  // SSE error callbacks and the stop reply can race; one monitor owns each job.
  if (pollingJobs.has(jobId)) return
  pollingJobs.add(jobId)
  try {
    let wait = !immediate
    while (state.job?.id === jobId && state.job.running) {
      if (wait) await new Promise((resolve) => setTimeout(resolve, 5000))
      if (!state.job || state.job.id !== jobId || !state.job.running) return
      wait = true
      try {
        const resp = await fetch(`/api/job-status?job_id=${encodeURIComponent(jobId)}`, { cache: 'no-store' })
        const body = await responseBody(resp)
        if (typeof body.done !== 'boolean') throw new Error('server returned an invalid job status')
        if (!state.job || state.job.id !== jobId || !state.job.running) return
        if (body.done === true) {
          finishJob(jobId, context, body as JobStatus)
          return
        }
        if (state.job.error) {
          state = { ...state, job: { ...state.job, error: null } }
          emit()
        }
      } catch (error) {
        jobRequestError(jobId, error)
      }
    }
  } finally {
    pollingJobs.delete(jobId)
  }
}

export async function abortJob(): Promise<void> {
  const job = state.job
  if (!job || !job.running || job.abortPending || job.abortRequested) return
  state = { ...state, job: { ...job, abortPending: true, error: null } }
  emit()
  try {
    const resp = await fetch('/api/abort-job', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobId: job.id }),
    })
    const body = await responseBody(resp)
    if (!['aborted', 'aborting', 'already_done'].includes(String(body.status))) {
      throw new Error('server did not acknowledge the stop request')
    }
    const current = state.job
    if (!current || current.id !== job.id) return
    const accepted = body.status === 'aborted' || body.status === 'aborting'
    // A final SSE can arrive first. Never restore the snapshot taken before the
    // request, and never relabel an already successful completion as cancelled.
    state = { ...state, job: {
      ...current, abortPending: false,
      abortRequested: current.abortRequested || accepted,
      aborted: current.aborted || (accepted && !current.running && current.returncode !== 0),
    } }
    emit()
    // Even a healthy EventSource can miss its terminal message. The abort ACK
    // itself is not evidence that stdout drained or the case lock was released.
    if (current.running) void pollUntilDone(job.id, job.caseContext, true)
  } catch (error) {
    jobRequestError(job.id, error)
    const current = state.job
    if (current?.id === job.id) {
      state = { ...state, job: { ...current, abortPending: false } }
      emit()
    }
  }
}
