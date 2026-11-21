/**
 * Where a .cfdproj comes from and where it goes — server paths and local files.
 *
 * The React port had one direction only: read an absolute path the SERVER can
 * see, write back to that same path. A user sitting at a different machine from
 * the backend could therefore neither open a project nor keep a variant of one.
 * This module is the other three quarters of the legacy editor's file plumbing
 * (STL_generate.html:14751-14990): the path arithmetic, the remembered remote
 * file, the save serialisation, the browser download, and the check that decides
 * whether a file the user picked off their own disk is a project at all.
 *
 * Path handling is deliberately string-only and accepts both separators. The
 * paths belong to the SERVER's filesystem, which may not be the one the browser
 * runs on, so `node:path` semantics would be the wrong ones even if they were
 * available here.
 */

import type { CfdProject } from '../schema/cfdproj'
import { decodeDomainProject, encodeProjectRecord, projectSchemaVersion } from '../domain'
import { stlNaming } from '../legacy/stlNaming'
// Side-effect import: the shared .cfdproj modules attach `migrateCfdprojToV5`
// to `window`, and the validation below calls it. Without this the module
// happens to work only because some OTHER import pulled them in first, and a
// v3 project would then load unmigrated the day that import moves.
import { projectExperimentErrors, projectHybridHeatBudgetErrors, projectGeometryTopologyErrors } from '../legacy/cfdprojModules'
import { transientModel } from '../transient/model'

/**
 * Same localStorage keys as the legacy app.
 *
 * Deliberately shared, not namespaced: the two editors run side by side against
 * one backend during the migration, and "which project file is open" is a fact
 * about the user, not about which port they happened to load. Diverging keys
 * would make a project saved in one app open at a different default in the other.
 */
const STORAGE_KEYS = {
  lastRemoteDir: 'stlgen-last-remote-project-dir',
  currentRemoteFile: 'stlgen-current-remote-project-file',
} as const

function readStored(key: string): string {
  try {
    return (localStorage.getItem(key) || '').trim()
  } catch {
    return ''
  }
}

function writeStored(key: string, value: string) {
  try {
    const next = (value || '').trim()
    if (next) localStorage.setItem(key, next)
    else localStorage.removeItem(key)
  } catch {
    /* private mode: the path just does not persist */
  }
}

// ---------------------------------------------------------------------------
// Path arithmetic — ports of STL_generate.html:14772-14800
// ---------------------------------------------------------------------------

export function getPathBasename(path: string | null | undefined): string {
  const clean = (path || '').replace(/[\\/]+$/, '')
  if (!clean) return ''
  const idx = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'))
  return idx >= 0 ? clean.slice(idx + 1) : clean
}

export function getPathDirname(path: string | null | undefined): string {
  const clean = (path || '').replace(/[\\/]+$/, '')
  if (!clean) return ''
  const idx = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'))
  if (idx < 0) return ''
  // A file at the root has '/' for a parent, not ''. Returning '' there would
  // send the browser to the server's home directory instead of up one level.
  if (idx === 0) return clean.slice(0, 1)
  return clean.slice(0, idx)
}

export function joinFsPath(dir: string | null | undefined, name: string | null | undefined): string {
  const cleanDir = (dir || '').replace(/[\\/]+$/, '')
  const cleanName = (name || '').replace(/^[\\/]+/, '')
  if (!cleanDir) return cleanName
  if (!cleanName) return cleanDir
  const sep = cleanDir.includes('\\') && !cleanDir.includes('/') ? '\\' : '/'
  return cleanDir + sep + cleanName
}

/** Append `.cfdproj` unless it is already there. An empty name stays empty. */
export function ensureProjectFileName(name: string | null | undefined): string {
  const trimmed = (name || '').trim()
  if (!trimmed) return ''
  return trimmed.toLowerCase().endsWith('.cfdproj') ? trimmed : `${trimmed}.cfdproj`
}

// ---------------------------------------------------------------------------
// The remembered remote file
// ---------------------------------------------------------------------------

export function getCurrentRemoteProjectFile(): string {
  return readStored(STORAGE_KEYS.currentRemoteFile)
}

export function clearCurrentRemoteProjectFile() {
  writeStored(STORAGE_KEYS.currentRemoteFile, '')
}

export function getLastRemoteProjectDir(): string {
  return readStored(STORAGE_KEYS.lastRemoteDir)
}

/** Record a server path as the open project, and its directory as where to browse next. */
export function rememberRemoteProjectPath(path: string) {
  const clean = (path || '').trim()
  if (!clean) return
  writeStored(STORAGE_KEYS.currentRemoteFile, clean)
  const dir = getPathDirname(clean)
  if (dir) writeStored(STORAGE_KEYS.lastRemoteDir, dir)
}

// ---------------------------------------------------------------------------
// Suggested names and default paths
// ---------------------------------------------------------------------------

function projectMetaString(project: CfdProject | null, key: string): string {
  const meta = project?.meta as Record<string, unknown> | undefined
  const value = meta?.[key]
  return typeof value === 'string' ? value : ''
}

/**
 * The filename a save should offer.
 *
 * The file the project was opened from wins over its display name, because
 * saving is normally saving BACK — offering `CFD_Project.cfdproj` for a file
 * opened as `Base_leakage.cfdproj` invites a duplicate rather than an update.
 */
export function getSuggestedProjectFileName(
  project: CfdProject | null, currentRemoteFile: string | null = getCurrentRemoteProjectFile(),
): string {
  const currentRemoteName = ensureProjectFileName(getPathBasename(currentRemoteFile || ''))
  if (currentRemoteName) return currentRemoteName
  const stem =
    projectMetaString(project, 'projectName') ||
    projectMetaString(project, 'exportFileName') ||
    'CFD_Project'
  return ensureProjectFileName(stlNaming().sanitizeName(stem) || 'CFD_Project')
}

/** Where the dialog opens: the open project's folder, else the last one browsed, else the case dir. */
export function getProjectDialogStartPath(project: CfdProject | null, caseDir?: string | null): string {
  return (
    getPathDirname(getCurrentRemoteProjectFile()) ||
    getLastRemoteProjectDir() ||
    (caseDir || '') ||
    projectMetaString(project, 'casePath')
  )
}

/** An explicit tab path overrides shared browsing memory; null means a new local project. */
export function getDefaultRemoteSavePath(
  project: CfdProject | null, dirHint?: string | null,
  currentRemoteFile: string | null = getCurrentRemoteProjectFile(),
): string {
  if (currentRemoteFile) return currentRemoteFile
  const baseDir = (dirHint || '').trim() || getProjectDialogStartPath(project)
  const fileName = getSuggestedProjectFileName(project, currentRemoteFile)
  return baseDir ? joinFsPath(baseDir, fileName) : fileName
}

/**
 * Turn whatever the user typed into a full file path.
 *
 * A trailing slash means "this directory", so the suggested filename is added
 * rather than the directory being overwritten as a file — which is what
 * `/api/write-file` would otherwise refuse with "Target path is a directory".
 */
export function normalizeRemoteSaveTarget(
  path: string, project: CfdProject | null, currentRemoteFile: string | null = getCurrentRemoteProjectFile(),
): string {
  const trimmed = (path || '').trim()
  if (!trimmed) return ''
  if (/[\\/]$/.test(trimmed)) return joinFsPath(trimmed, getSuggestedProjectFileName(project, currentRemoteFile))
  const dir = getPathDirname(trimmed)
  const fileName = ensureProjectFileName(getPathBasename(trimmed))
  return dir ? joinFsPath(dir, fileName) : fileName
}

// ---------------------------------------------------------------------------
// Serialisation
// ---------------------------------------------------------------------------

/**
 * The canonical document keys (V4 order, STL_generate.html:14884, plus v5's `transient`).
 *
 * Established fields keep canonical order. JSON extension fields are retained;
 * the domain codec excludes only the explicit editor runtime bag.
 */
const SAVE_KEYS = [
  'schemaVersion',
  'uid',
  'name',
  'meta',
  'geometry',
  'geometryTopology',
  'models',
  'constructions',
  'instrumentation',
  'runDefaults',
  'transient',
] as const

/**
 * The document written to disk: cfdproj v5. The live project carries its
 * time-variable fields materialised at the playhead; on disk they exist only
 * in `transient.tracks`, so they are stripped from a copy here.
 */
export function projectForSave(project: CfdProject): Record<string, unknown> {
  const tm = transientModel()
  const src = structuredClone(project) as unknown as Record<string, unknown>
  if (!src.transient) src.transient = tm.defaultTransient(src)
  const errors=[...projectExperimentErrors(project),...projectHybridHeatBudgetErrors(project),...projectGeometryTopologyErrors(project),
    ...tm.validateTransient(src.transient)]
  if(errors.length)throw new Error(errors.map(e=>`${e.path}: ${e.msg}`).join('; '))
  tm.stripTrackedFields(src)
  src.schemaVersion = 5
  return encodeProjectRecord(src, SAVE_KEYS)
}

/** Two-space JSON, the same bytes the legacy editor writes. */
export function serializeProjectState(project: CfdProject): string {
  return JSON.stringify(projectForSave(project), null, 2)
}

// ---------------------------------------------------------------------------
// Validation of a file picked off the user's own disk
// ---------------------------------------------------------------------------

export type ProjectDocumentCheck =
  | { ok: true; project: CfdProject }
  | { ok: false; error: string }

type Migrator = (raw: unknown) => unknown

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function windowMigrator(): Migrator {
  const fn = (window as unknown as { migrateCfdprojToV5?: Migrator }).migrateCfdprojToV5
  return typeof fn === 'function' ? fn : (raw) => raw
}

/**
 * Decide whether a parsed document is a project, and bring it up to v5.
 *
 * The marker check is the load-bearing part, and it has to happen BEFORE the
 * migration. `migrateCfdprojToV4` fills in every missing field from defaults, so
 * it turns ANY JSON object — a package.json, a .cfdrun, a screenshot manifest —
 * into a valid, empty, 3.6 m-high room with 19 empty buckets. Validating after
 * the migration therefore accepts everything, and a user who picks the wrong
 * file in the local-file tab silently replaces their project with a blank one.
 * So the raw document must carry one of the format's own markers first:
 * `constructions` (V4), `models` (v1-v3), or `cfdRoom` (v1/v2's room block).
 */
export function checkProjectDocument(
  raw: unknown,
  migrate: Migrator = windowMigrator(),
): ProjectDocumentCheck {
  if (!isPlainObject(raw)) {
    return { ok: false, error: 'Not a .cfdproj document (expected a JSON object)' }
  }
  // Read the version off the RAW document: the migrator passes anything already
  // at v5 or later straight through, so the two agree, and a v6 file has to be
  // refused rather than half-understood.
  try { projectSchemaVersion(raw) }
  catch (err) { return { ok: false, error: err instanceof Error ? err.message : String(err) } }
  // One marker per document generation. `construction` — singular, an array — is
  // v3's, and leaving it out refused EVERY v3 file: all sixteen paper labels and
  // every project saved before b821e27. The shared migrator has always handled
  // them; only this guard did not, and it fails silently — `_applyProjectData`
  // returns false and a script sees its load "succeed" against an unchanged
  // project.
  const hasMarker =
    isPlainObject(raw.constructions) || // V4
    Array.isArray(raw.construction) || // v3
    Array.isArray(raw.models) ||
    isPlainObject(raw.models) ||
    isPlainObject(raw.cfdRoom) // v2
  if (!hasMarker) {
    return { ok: false, error: 'Not a .cfdproj document (no constructions or models)' }
  }
  const migrated = migrate(raw)
  if (!isPlainObject(migrated)) {
    return { ok: false, error: 'Not a .cfdproj document (migration produced no document)' }
  }
  const errors=[...projectExperimentErrors(migrated),...projectHybridHeatBudgetErrors(migrated),...projectGeometryTopologyErrors(migrated)]
  if(errors.length)return {ok:false,error:errors.map(e=>`${e.path}: ${e.msg}`).join('; ')}
  try { return { ok: true, project: decodeDomainProject(migrated).document } }
  catch (err) { return { ok: false, error: err instanceof Error ? err.message : String(err) } }
}

/** Parse the text of a picked file and validate it. Parse errors come back as messages, not throws. */
export function readProjectFileText(text: string, migrate?: Migrator): ProjectDocumentCheck {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) }
  }
  return checkProjectDocument(parsed, migrate)
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

export interface WriteResult {
  ok?: boolean
  exists?: boolean
  path?: string
  error?: string
}

/** POST the project to `/api/write-file`. `overwrite: false` comes back as `{exists: true}`, not an error. */
export async function writeProjectToRemote(
  path: string,
  content: string,
  overwrite: boolean,
): Promise<WriteResult> {
  const resp = await fetch('/api/write-file', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, content, overwrite: !!overwrite }),
  })
  let result: WriteResult
  try {
    result = (await resp.json()) as WriteResult
  } catch {
    result = { ok: false, error: resp.statusText || 'Request failed' }
  }
  if (!resp.ok && !result.error) result.error = resp.statusText || 'Request failed'
  return result
}

/**
 * Hand the project to the browser as a download.
 *
 * The only way out of the app that does not need the server to be able to write
 * where the user wants the file — which is the point of the local tab.
 */
export function downloadProjectToLocal(project: CfdProject, fileName: string): string {
  const finalName = ensureProjectFileName(fileName) || getSuggestedProjectFileName(project)
  const blob = new Blob([serializeProjectState(project)], { type: 'application/json' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = finalName
  link.click()
  setTimeout(() => URL.revokeObjectURL(link.href), 1000)
  return finalName
}

/** POST `/api/create-dir` — the folder browser's New Folder (server.py:2973). */
export async function createRemoteDirectory(path: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const resp = await fetch('/api/create-dir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    const body = (await resp.json()) as { ok?: boolean; error?: string }
    if (body.ok) return { ok: true }
    return { ok: false, error: body.error }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) }
  }
}
