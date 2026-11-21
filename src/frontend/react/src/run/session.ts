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
  installCasePathSync, isCasePathContextCurrent, readCasePath, setRepoRoot,
  syncCasePathFromServer, writeCasePath, type CasePathApplyOptions,
} from './caseDir'
import { buildRunRequest } from './runSubmit'
import { getSolveInit, solveAiInit } from './solveInit'
import { effectiveSolverEngine } from './solverEngine'
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
  /** The GPU air solver: offered only when the server was launched with --gpu; `devices` null means any card. */
  gpuSolver?: { enabled: boolean; devices?: string[] | null }
  /** The repository a relative project case path is resolved against. */
  repoRoot?: string
  /** The signed release-stamp verdict the server started under (tools/release_stamp.sh). */
  release?: { status: 'verified' | 'owner' | 'refused'; stampedAt?: string; contentCommit?: string } | null
}

export type JobKind = 'mesh' | 'solve' | 'checkMesh' | 'scano'

/** What a finished SCANO AI solve reports (server `scanoResult`, worker reply). */
export interface ScanoResult {
  ok: boolean
  runDir: string
  /** The result run directory relative to the case: what post-processing lists. */
  runDirRelative: string
  state: string
  changedPatches: string[]
  nCells: number
  nNodes: number
  timing: Record<string, number>
  peakGpuBytes: number | null
  device: string | null
  /** Where the room leaves the training data (server-side check). */
  warnings?: string[]
  /** The box an ROI solve holds values in; absent for the whole mesh. */
  roi?: { min: [number, number, number]; max: [number, number, number]; cells: number } | null
  /** How the model ran (worker `inference`): the request, the deployment used and why, its TTA counts, memory. */
  inference?: ScanoInferenceRecord | null
}

export interface ScanoInferenceRecord {
  request: string
  mode: 'whole' | 'tta'
  reason?: string
  passes?: number | null
  chunk?: number | null
  forwards?: number | null
  minCount?: number | null
  estimateBytes?: number | null
  freeBytes?: number | null
  peakBytes?: number | null
  nNodes?: number | null
}

/** The extra keys of a SCANO AI solve request (`/api/ai/scano/run`). */
export interface ScanoRequest {
  model: string
  /** auto: reuse the case's first iteration when only server / ACU values changed; exact: always run one. */
  mode: 'auto' | 'exact'
  /** Infer only inside this box (values elsewhere are not kept); null: the whole mesh. */
  roi?: { min: [number, number, number]; max: [number, number, number] } | null
  /** auto | whole | tta (run/scanoInference). */
  inference?: 'auto' | 'whole' | 'tta'
  /** TTA repetitions. */
  passes?: number
}

export interface JobState {
  id: string
  kind: JobKind
  caseContext: number
  /** Directory captured by this request; never read from another tab's backend selection. */
  caseDir?: string
  executionCaseDir?: string
  cfdrunSha256?: string
  solveAssessment?: SolveAssessment | null
  /** A solve the GPU solver ended without a result for a reason the page explains (out of GPU memory). */
  solverFailure?: { code: string; error: string } | null
  /** A finished SCANO AI solve (kind 'scano'). */
  scanoResult?: ScanoResult | null
  running: boolean
  /** True only for a confirmed cancelled terminal job, not a stop request. */
  aborted: boolean
  /** A stop request is in flight; separate from backend completion. */
  abortPending?: boolean
  /** The backend accepted a stop request, but may still hold the case lock. */
  abortRequested?: boolean
  /** Click-to-strict deadline expired; physical backend fields remain unchanged. */
  budgetExceeded?: boolean
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

export interface RunPreparation {
  /** Local admission identity; never a backend job id. */
  token: number
  kind: 'mesh' | 'solve'
  caseContext: number
  phase: 'preparing' | 'submitting'
  abortRequested: boolean
}

interface OwnedRunPreparation {
  view: RunPreparation
  controller: AbortController
  meshStarted?: number
  meshDeadline?: number
  deadlineTimer?: ReturnType<typeof setTimeout>
  deadlineExceeded?: boolean
  explicitlyCancelled?: boolean
  jobId?: string
}

/** Development target for the whole mesh pipeline, in seconds: logged, never enforced. */
const MESH_TIME_TARGET_S = 300

let preparationSequence = 0
let activeRunPreparation: OwnedRunPreparation | null = null
const meshJobPreparations = new Map<string, OwnedRunPreparation>()

function meshTimeoutError(): Error {
  const error = new Error('Mesh time budget exhausted before strict completion (preparation and upload are included)')
  error.name = 'MeshTimeoutError'
  return error
}

function clearMeshDeadline(preparation: OwnedRunPreparation): void {
  if (preparation.deadlineTimer !== undefined) clearTimeout(preparation.deadlineTimer)
  preparation.deadlineTimer = undefined
}

function expireMeshDeadline(preparation: OwnedRunPreparation): void {
  if (preparation.meshDeadline === undefined || preparation.explicitlyCancelled ||
      performance.now() < preparation.meshDeadline) return
  preparation.deadlineExceeded = true
  clearMeshDeadline(preparation)
  if (activeRunPreparation === preparation) {
    preparation.view = { ...preparation.view, abortRequested: true }
    state = { ...state, preparation: preparation.view }
    if (preparation.view.phase === 'preparing') {
      preparation.controller.abort()
      releaseRunPreparation(preparation)
    } else emit() // Keep the POST reservation until its real job id arrives.
  } else if (preparation.jobId && state.job?.id === preparation.jobId && state.job.running) {
    state = { ...state, job: { ...state.job, budgetExceeded: true, error: meshTimeoutError().message } }
    emit()
    void abortJob() // Existing owned backend cancellation and terminal cleanup.
  }
}

function checkMeshDeadline(preparation: OwnedRunPreparation): void {
  expireMeshDeadline(preparation)
  if (preparation.deadlineExceeded) throw meshTimeoutError()
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
  preparation: RunPreparation | null
}

let state: SessionState = {
  info: null,
  infoError: null,
  diagnosis: null,
  meshReady: false,
  status: null,
  job: null,
  preparation: null,
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

/** The local request build/POST reservation, before a real backend id exists. */
export function getRunPreparation(): RunPreparation | null {
  return state.preparation
}

function releaseRunPreparation(preparation: OwnedRunPreparation): void {
  // A cancelled old build can settle after a new run acquired its own slot.
  if (activeRunPreparation !== preparation) return
  clearMeshDeadline(preparation)
  activeRunPreparation = null
  state = { ...state, preparation: null }
  emit()
}

function preparationAbortError(): Error {
  const error = new Error('run preparation cancelled')
  error.name = 'AbortError'
  return error
}

/**
 * Is the editor frozen while a run is prepared or its backend job is running?
 *
 * A mesh or a solve holds the whole UI, because both run against the geometry
 * that was uploaded when they started and an edit made meanwhile is not in the
 * run. `checkMesh` does not: it only reads the mesh already on disk, so nothing
 * a user changes while it runs can make its verdict wrong.
 */
export function isUiFrozen(): boolean {
  return !!state.preparation ||
    (!!state.job?.running && state.job.kind !== 'checkMesh')
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
    if (job.running || job.aborted || job.budgetExceeded || job.returncode !== 0 || job.checkMeshOk !== true ||
        (job.failedChecks !== null && job.failedChecks !== 0)) return false
  }
  return state.status && statusMeshReadinessGeneration === meshReadinessGeneration
    ? state.status.meshReady === true : state.meshReady
}

/**
 * Is there a mesh on disk to solve on?
 *
 * Owner rule (2026-10-22): mesh quality is reported, never required. Neither
 * the solver tab nor the solve button waits for a strict checkMesh verdict
 * (`isMeshReady` stays as the report). A running, interrupted or failed mesh
 * job of this case hides the mesh it was rewriting until it is rescanned.
 */
export function isMeshPresent(): boolean {
  const job = getCurrentCaseJob()
  if (job && job.kind === 'mesh') {
    if (job.running || job.aborted || job.budgetExceeded || (job.returncode !== null && job.returncode !== 0)) return false
    if (job.returncode === 0) return true
  }
  return state.status?.meshExists === true
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
  const preparation = activeRunPreparation
  if (preparation?.view.phase === 'preparing') {
    preparation.explicitlyCancelled = true
    preparation.controller.abort()
    releaseRunPreparation(preparation)
  }
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
  if (info) {
    setRepoRoot(info.repoRoot)
    return info
  }
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

let releaseLogged = false
/**
 * The server's release-stamp verdict (tools/release_stamp.sh), once per page.
 * A server that reports none was started past the check — say so plainly.
 */
function logRelease(release: ServerInfo['release']): void {
  if (releaseLogged) return
  releaseLogged = true
  if (release?.status === 'verified') {
    logOperation('releaseStamp', t('release.verified', { at: release.stampedAt ?? '', commit: (release.contentCommit ?? '').slice(0, 12) }))
  } else if (release?.status === 'owner') {
    logOperation('releaseStamp', t('release.owner'))
  } else {
    logOperation('releaseStamp', t('release.unverified'))
  }
}

async function publishServerInfo(context: number, info: ServerInfo): Promise<ServerInfo | null> {
  if (!isCasePathContextCurrent(context)) return null
  logRelease(info.release)
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

/**
 * A project's saved case directory the server confirmed missing on this
 * machine (it was made elsewhere, or the directory moved). The editor asks the
 * user to choose one; null once a directory is set or the prompt dismissed.
 */
let invalidCasePath: string | null = null

export function getInvalidCasePath(): string | null {
  return invalidCasePath
}

export function dismissInvalidCasePath(): void {
  if (invalidCasePath === null) return
  invalidCasePath = null
  emit()
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
      const result = await resp.json() as {
        ok?: boolean; caseDir?: string; portablePath?: string; error?: string; code?: string
      }
      if (!isCasePathContextCurrent(context)) return false
      if (!resp.ok || result.ok !== true || typeof result.caseDir !== 'string') {
        const missing = options.automatic && (
          (resp.status === 404 && result.code === 'case_dir_missing') ||
          (resp.status === 400 && result.code === 'case_dir_not_directory')
        )
        if (missing) {
          clearMissingCasePath(caseDir)
          // the editor offers to choose the directory again (CasePathPrompt)
          invalidCasePath = caseDir
        }
        const diagnosis: ServerDiagnosis = missing
          ? { kind: 'abnormal', key: 'log.case_path_invalid', vars: { path: caseDir } }
          : { kind: 'abnormal', key: 'log.case_path_restore_failed', vars: { path: caseDir, error: result.error ?? resp.statusText } }
        const message = t(diagnosis.key, diagnosis.vars)
        state = { ...state, infoError: message, diagnosis }
        logOperation('setCaseDir', message)
        emit()
        return false
      }
      invalidCasePath = null
      // The server's portable form (repository-relative inside the repo) is what
      // the project saves; its absolute form also tells where the repository is.
      const portable = typeof result.portablePath === 'string' ? result.portablePath : result.caseDir
      if (portable !== result.caseDir && !portable.startsWith('/') && result.caseDir.endsWith(`/${portable}`)) {
        setRepoRoot(result.caseDir.slice(0, -(portable.length + 1)))
      }
      if (options.automatic) syncCasePathFromServer(portable, true)
      else writeCasePath(portable)
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

// Job log lines reach the page state in batches. One state update and React
// render per SSE line froze the editor on Base604's mesh: 104 000 lines over
// nine minutes, each copying the 2000-line window and re-rendering, and the
// page stopped answering input when the mesh finished.
const LINE_FLUSH_MS = 150
let pendingLines: { jobId: string; lines: string[] } | null = null
let lineFlushTimer: ReturnType<typeof setTimeout> | undefined

function flushJobLines(): void {
  if (lineFlushTimer !== undefined) clearTimeout(lineFlushTimer)
  lineFlushTimer = undefined
  const pending = pendingLines
  pendingLines = null
  const job = state.job
  if (!pending || !job || job.id !== pending.jobId || !job.running || !pending.lines.length) return
  const lines = job.lines.concat(pending.lines)
  if (lines.length > MAX_LINES) lines.splice(0, lines.length - MAX_LINES)
  state = { ...state, job: { ...job, lines } }
  emit()
}

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
export interface RunJobOptions {
  /** A mesh job that keeps the mesh and reassigns its boundary faces to the new patches (mesh-change "patches"). */
  patchUpdate?: boolean
}

export async function runJob(kind: JobKind, scano?: ScanoRequest, options: RunJobOptions = {}): Promise<void> {
  if (state.job?.running || activeRunPreparation) throw new Error('a job is already running')
  if (kind === 'checkMesh') throw new Error('use runCheckMesh for a checkMesh job')
  if ((kind === 'scano') !== !!scano) throw new Error('a scano job needs its model and mode, and only it')
  // An AI solve sends the body of a solve (the manifest, no STL) to its own endpoint.
  const requestKind = kind === 'scano' ? 'solve' : kind

  const meshStarted = kind === 'mesh' ? performance.now() : undefined
  const context = casePathContext()
  const caseDir = readCasePath()
  // Reserve synchronously, before export's first await, across all callers.
  const preparation: OwnedRunPreparation = {
    view: { token: ++preparationSequence, kind: requestKind, caseContext: context,
      phase: 'preparing', abortRequested: false },
    controller: new AbortController(),
    meshStarted,
  }
  activeRunPreparation = preparation
  state = { ...state, preparation: preparation.view }

  try {
    // The 5-minute mesh time is a soft development target (owner decision
    // 2026-10-20): no deadline is armed, nothing aborts the job; the elapsed
    // time is logged when the job ends (see finishJob).
    emit()
    checkMeshDeadline(preparation)
    const body = await buildRunRequest(requestKind, undefined, preparation.controller.signal)
    // Both run-time choices of a solve are for a steady one: the GPU solver (offered by a --gpu launch) and the
    // AI initial field. The GPU solver starts from the uniform field, so it takes no AI initial field.
    const gpuOffered = state.info?.gpuSolver?.enabled === true
    const steadyMatters = getSolveInit() === 'ai' || effectiveSolverEngine(true, gpuOffered) === 'gpuNewton'
    const steady = kind === 'solve' && steadyMatters &&
      ((JSON.parse(body.cfdrunJson) as { simulationConfig?: { solverType?: string } })
        .simulationConfig?.solverType ?? 'steady') === 'steady'
    const gpuEngine = kind === 'solve' && effectiveSolverEngine(steady, gpuOffered) === 'gpuNewton'
    const aiInit = kind === 'solve' && !gpuEngine && getSolveInit() === 'ai' ? await solveAiInit(steady) : null
    checkMeshDeadline(preparation)
    if (preparation.controller.signal.aborted || activeRunPreparation !== preparation ||
        !isCasePathContextCurrent(context)) throw preparationAbortError()
    // Serialize the large STL body once. Stamp elapsed duration after that work,
    // without changing the saved project or its exported manifest configuration.
    const serialized = JSON.stringify({ ...body, caseDir, ...(scano ?? {}), ...(aiInit ? { aiInit } : {}),
      ...(gpuEngine ? { solverEngine: 'gpuNewton' } : {}),
      ...(kind === 'mesh' && options.patchUpdate ? { patchUpdate: true } : {}) })
    checkMeshDeadline(preparation)
    const elapsed = meshStarted === undefined ? undefined : (performance.now() - meshStarted) / 1000
    const requestBody = elapsed === undefined ? serialized
      : serialized.slice(0, -1) + ',"meshPreparationElapsedSeconds":' + JSON.stringify(elapsed) + '}'
    // Concatenation and browser transport cannot share the server's monotonic
    // clock; the original local deadline remains armed across POST and the job.
    checkMeshDeadline(preparation)
    preparation.view = { ...preparation.view, phase: 'submitting' }
    state = { ...state, preparation: preparation.view }
    // Do not abort an in-flight POST: its response owns the real backend job.
    const submission = fetch(kind === 'scano' ? '/api/ai/scano/run' : '/api/run-mesh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: requestBody,
    })
    emit()
    const resp = await submission
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
    if (preparation.meshDeadline !== undefined && performance.now() >= preparation.meshDeadline &&
        !preparation.explicitlyCancelled) preparation.deadlineExceeded = true
    preparation.jobId = jobId
    if (meshStarted !== undefined) meshJobPreparations.set(jobId, preparation)
    activeRunPreparation = null
    state = {
      ...state,
      preparation: null,
      job: {
        id: jobId,
        kind,
        caseContext: context,
        caseDir,
        executionCaseDir, cfdrunSha256, solveAssessment: null,
        budgetExceeded: preparation.deadlineExceeded === true,
        running: true,
        aborted: false,
        returncode: null,
        checkMeshOk: null,
        failedChecks: null,
        meshWarnings: null,
        lines: [],
        error: preparation.deadlineExceeded ? meshTimeoutError().message : null,
      },
    }
    emit()
    jobLogStart(kind, jobId)
    streamLog(jobId, context)
    if (preparation.view.abortRequested || preparation.deadlineExceeded) await abortJob()
    if (preparation.deadlineExceeded) throw meshTimeoutError()
  } catch (error) {
    if (preparation.deadlineExceeded) throw meshTimeoutError()
    throw error
  } finally {
    releaseRunPreparation(preparation)
  }
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
  if (state.job?.running || activeRunPreparation) throw new Error('a job is already running')

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
  solverFailure?: { code?: unknown; error?: unknown } | null
  scanoResult?: ScanoResult | null
}

/** One terminal transition for SSE and polling, including current-case refresh. */
function finishJob(jobId: string, context: number, final: JobStatus): void {
  flushJobLines() // the last lines land before the verdict
  const job = state.job
  if (!job || job.id !== jobId || !job.running || final.done !== true) return
  const preparation = meshJobPreparations.get(jobId)
  const budgetExceeded = job.budgetExceeded === true || !!(preparation &&
    preparation.meshDeadline !== undefined && performance.now() >= preparation.meshDeadline &&
    (!preparation.explicitlyCancelled || final.returncode === 0))
  if (preparation) clearMeshDeadline(preparation)
  meshJobPreparations.delete(jobId)
  const currentCase = isCasePathContextCurrent(context)
  const aborted = final.cancelled === true || final.cancelRequested === true ||
    (job.abortRequested === true && final.returncode !== 0)
  const ok = final.returncode === 0 && !aborted && !budgetExceeded
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
      ...job, running: false, aborted, abortPending: false, budgetExceeded,
      returncode: final.returncode ?? null,
      checkMeshOk: final.checkMeshOk ?? null,
      failedChecks: final.failedChecks ?? null,
      meshWarnings: currentCase ? (Array.isArray(final.meshWarnings) ? final.meshWarnings : []) : null,
      solveAssessment: currentCase && job.kind === 'solve' && ok
        ? readSolveAssessment(final.solveAssessment, job.executionCaseDir, job.cfdrunSha256) : null,
      scanoResult: job.kind === 'scano' && ok ? final.scanoResult ?? null : null,
      solverFailure: currentCase && job.kind === 'solve' && !ok && final.solverFailure
        && typeof final.solverFailure.code === 'string'
        ? { code: final.solverFailure.code, error: String(final.solverFailure.error ?? '') } : null,
      error: budgetExceeded ? meshTimeoutError().message : null,
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
  if (job.kind === 'mesh' && preparation?.meshStarted !== undefined) {
    const seconds = Math.round((performance.now() - preparation.meshStarted) / 1000)
    logAction('dctwin', t(seconds > MESH_TIME_TARGET_S ? 'log.mesh_time_over_target' : 'log.mesh_time',
      { seconds: String(seconds), target: String(MESH_TIME_TARGET_S) }))
  }
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
      if (!pendingLines || pendingLines.jobId !== jobId) {
        flushJobLines()
        pendingLines = { jobId, lines: [] }
      }
      pendingLines.lines.push(payload)
      if (pendingLines.lines.length > MAX_LINES) pendingLines.lines.splice(0, pendingLines.lines.length - MAX_LINES)
      if (lineFlushTimer === undefined) lineFlushTimer = setTimeout(flushJobLines, LINE_FLUSH_MS)
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

export async function abortJob(preparationToken?: number): Promise<void> {
  const preparation = activeRunPreparation
  // An overlay belongs to one local build and cannot stop a later run.
  if (preparationToken !== undefined && preparation?.view.token !== preparationToken) return
  if (preparation) {
    if (preparation.view.abortRequested) return
    preparation.explicitlyCancelled = true
    clearMeshDeadline(preparation)
    preparation.view = { ...preparation.view, abortRequested: true }
    state = { ...state, preparation: preparation.view }
    if (preparation.view.phase === 'preparing') {
      preparation.controller.abort()
      releaseRunPreparation(preparation)
    } else {
      // The POST already started. Keep the slot until its real job id arrives.
      emit()
    }
    return
  }
  const job = state.job
  if (!job || !job.running || job.abortPending || job.abortRequested) return
  const meshPreparation = meshJobPreparations.get(job.id)
  if (meshPreparation && !job.budgetExceeded) {
    meshPreparation.explicitlyCancelled = true
    clearMeshDeadline(meshPreparation)
  }
  state = { ...state, job: { ...job, abortPending: true,
    error: job.budgetExceeded ? meshTimeoutError().message : null } }
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
