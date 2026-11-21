import { t } from '../i18n'
import * as store from '../projectStore'

/** The document's saved path wins over an unrelated backend startup directory. */
export function readCasePath(): string {
  const path = store.getProject()?.meta?.casePath
  return typeof path === 'string' ? path.trim() : ''
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
  if (readCasePath() === caseDir) return
  store.edit('applyDctwinCaseDir', t('log.case_dir_set', { dir: caseDir }), (draft) => {
    draft.meta = { ...draft.meta, casePath: caseDir }
  })
}

/** Automatic normalization is not a user edit and must not dirty a loaded file. */
export function syncCasePathFromServer(caseDir: string | undefined | null, accepted = false): void {
  if (!caseDir || readCasePath() === caseDir) return
  if (!accepted && (openedProject || readCasePath())) return
  store.mutateLive((project) => { project.meta = { ...project.meta, casePath: caseDir } }, { scene: false })
  store.logAction('syncCasePath', t('log.case_dir_set', { dir: caseDir }))
}

/** Clear only a path the server explicitly confirmed missing or not a directory. */
export function clearMissingCasePath(expected: string): void {
  if (readCasePath() !== expected) return
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
  const saved = readCasePath()
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
