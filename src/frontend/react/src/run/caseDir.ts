import { t } from '../i18n'
import * as store from '../projectStore'

/**
 * A project saves its case directory relative to the repository when it lies
 * inside it ("case/Tri_angle"), so a teammate's clone at another path opens the
 * same case. The server reports its repository root; until it has, a relative
 * path stays relative (the server resolves it against the same root).
 */
let repoRoot: string | null = null

export const getRepoRoot = (): string | null => repoRoot

export function setRepoRoot(root: string | null | undefined): void {
  repoRoot = root ? root.replace(/\/+$/, '') : null
}

const isAbsolute = (path: string) => path.startsWith('/') || /^[A-Za-z]:[\\/]/.test(path) || path.startsWith('~')

/** The directory a saved path names on this machine. */
export function resolveCasePath(path: string): string {
  const trimmed = path.trim()
  if (!trimmed || isAbsolute(trimmed) || !repoRoot) return trimmed
  const rel = trimmed.replace(/^\.\/+/, '').replace(/\/+$/, '')
  return rel === '' || rel === '.' ? repoRoot : `${repoRoot}/${rel}`
}

/** The form a project saves: repository-relative inside the repo, else as given. */
export function portableCasePath(path: string): string {
  const trimmed = path.trim().replace(/\/+$/, '')
  if (!repoRoot || !trimmed.startsWith(`${repoRoot}/`)) return trimmed
  return trimmed.slice(repoRoot.length + 1)
}

/** The path exactly as the project stores it. */
export function readSavedCasePath(): string {
  const path = store.getProject()?.meta?.casePath
  return typeof path === 'string' ? path.trim() : ''
}

/** The document's saved path wins over an unrelated backend startup directory. */
export function readCasePath(): string {
  return resolveCasePath(readSavedCasePath())
}

let context = 0
let openedProject = false

export const casePathContext = () => context
export const isCasePathContextCurrent = (token: number) => token === context
export const hasOpenedProject = () => openedProject
export function beginCasePathChange(): number { return ++context }

export interface CasePathApplyOptions {
  automatic?: boolean
  createIfMissing?: boolean
  context?: number
}
type ApplyCasePath = (path: string, options?: CasePathApplyOptions) => Promise<boolean>

/** A deliberate directory selection remains an undoable project edit. */
export function writeCasePath(caseDir: string): void {
  if (readCasePath() === resolveCasePath(caseDir)) return
  const saved = portableCasePath(caseDir)
  store.edit('applyDctwinCaseDir', t('log.case_dir_set', { dir: caseDir }), (draft) => {
    draft.meta = { ...draft.meta, casePath: saved }
  })
}

/** Automatic normalization is not a user edit and must not dirty a loaded file. */
export function syncCasePathFromServer(caseDir: string | undefined | null, accepted = false): void {
  if (!caseDir || readCasePath() === resolveCasePath(caseDir)) return
  if (!accepted && (openedProject || readCasePath())) return
  const saved = portableCasePath(caseDir)
  store.mutateLive((project) => { project.meta = { ...project.meta, casePath: saved } }, { scene: false })
  store.logAction('syncCasePath', t('log.case_dir_set', { dir: caseDir }))
}

/** Clear only a path the server explicitly confirmed missing or not a directory. */
export function clearMissingCasePath(expected: string): void {
  if (readSavedCasePath() !== expected && readCasePath() !== expected) return
  store.mutateLive((project) => { project.meta = { ...project.meta, casePath: '' } }, { scene: false })
  store.logAction('loadProject', t('log.case_path_invalid', { path: expected }))
}

/** Standalone existence probe: connection/permission failures are not "missing". */
export async function probeCasePath(path: string): Promise<boolean> {
  const response = await fetch(`/api/browse-dir?path=${encodeURIComponent(path)}`, { cache: 'no-store' })
  const data = await response.json() as { error?: string; current?: string }
  if (response.status === 400 && /^(Path does not exist|Not a directory):/.test(data.error ?? '')) return false
  if (!response.ok || data.error || !data.current) throw new Error(data.error || response.statusText)
  return true
}

export async function validateCasePathAfterLoad(applyToServer: ApplyCasePath): Promise<void> {
  // Sent as saved: the server resolves a relative path against its repository.
  const saved = readSavedCasePath()
  const token = context
  if (!saved) return
  try {
    // One checked POST validates and applies atomically; it never creates a
    // directory copied from a project made on a different computer.
    await applyToServer(saved, { automatic: true, createIfMissing: false, context: token })
  } catch (error) {
    if (!isCasePathContextCurrent(token)) return
    store.logAction('loadProject', t('log.case_path_restore_failed', {
      path: saved, error: error instanceof Error ? error.message : String(error),
    }))
  }
}

let installed = false
export function installCasePathSync(applyToServer: ApplyCasePath, onLoad?: () => void): void {
  if (installed) return
  installed = true
  store.subscribeProjectLoad(() => {
    beginCasePathChange()
    openedProject = true
    onLoad?.()
    void validateCasePathAfterLoad(applyToServer)
  })
}
