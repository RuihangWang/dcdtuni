/**
 * The POST body a mesh or solve run is submitted with.
 *
 * `/api/run-mesh` accepts the manifest and the geometry in the request itself
 * (`server.py:3101-3104`): `cfdrunJson` is written to `<case>/model/<name>.cfdrun`
 * and every entry of `stlFiles` is base64-decoded into
 * `<case>/Mesh/constant/triSurface/`, after that directory has been WIPED. Send
 * neither and the server falls back to whatever is already on disk — which is
 * how the port used to re-run the last export instead of the geometry on
 * screen.
 *
 * Two rules come straight from the legacy pair `runMeshGeneration`
 * (STL_generate.html:28054) and `runSolveOnly` (:28348):
 *
 * - A SOLVE sends `stlFiles: {}`. It reuses the mesh the last mesh run built,
 *   and the server's wipe-then-write means re-sending the set would delete and
 *   rewrite triSurface/ for a run that never re-meshes. The manifest is still
 *   rebuilt and bound to the cached current grouped STL metadata so source
 *   identity can be checked against the reused mesh. STL bytes are not uploaded.
 * - A MESH refuses on duplicate solid names. Two solids with one name collapse
 *   into a single OpenFOAM patch, so a boundary condition meant for one lands
 *   on both — and the run still meshes, still passes mesh acceptance, and
 *   reports the wrong temperatures.
 *
 * The decisions live here, apart from the scene wiring in `runSubmit.ts`, so
 * they can be tested without a WebGL context.
 */

import type { CfdProject } from '../schema/cfdproj'
import { t } from '../i18n'
import { stlNaming } from '../legacy/stlNaming'
import type { CfdrunV2 } from '../schema/cfdrun'
import { assertGeometryTopologyAirExportReady, bindStlFileHashes } from '../legacy/cfdrunBuild'

export type RunKind = 'mesh' | 'solve'

/** Exactly the keys `handle_run_mesh` reads. */
export interface RunRequestBody {
  cfdrunJson: string
  cfdrunFilename: string
  /** filename -> base64 STL. Empty for a solve. */
  stlFiles: Record<string, string>
  meshOnly: boolean
  solveOnly: boolean
}

export interface DuplicateSolid {
  name: string
  sources: string[]
}

/**
 * Everything a run body needs that this module will not go and fetch itself.
 *
 * Injected rather than imported so the order and the mesh/solve split can be
 * tested: the real implementations need a built Three.js scene.
 */
export interface RunBuildDeps {
  project: CfdProject | null
  /** Path of the loaded .cfdproj, or null for an unsaved one. Names the .cfdrun. */
  projectPath: string | null
  modelUids(): string[]
  duplicateSolids(uids: string[]): DuplicateSolid[]
  buildManifest(): Promise<CfdrunV2>
  /** filename -> ASCII STL text, not yet base64. */
  buildStl(): Record<string, string>
}

/**
 * The name `<case>/model/<name>.cfdrun` gets, mirroring `getCfdrunFilename`
 * (STL_generate.html:14817).
 *
 * The loaded project's own basename wins, so re-running a case overwrites its
 * manifest instead of leaving a second one beside it — the runner picks the
 * first `.cfdrun` it finds when the body carries none, and two files there make
 * that a coin flip.
 */
export function cfdrunFilename(project: CfdProject | null, projectPath: string | null): string {
  const basename = (projectPath ?? '').split(/[\\/]/).pop() ?? ''
  const stem = basename.replace(/\.cfdproj$/i, '')
  if (stem) return `${stem}.cfdrun`
  const meta = (project?.meta ?? {}) as { projectName?: string; exportFileName?: string }
  const fallback =
    stlNaming().sanitizeName(meta.projectName || meta.exportFileName || 'project') || 'project'
  return `${fallback}.cfdrun`
}

/**
 * Base64, through the legacy's exact expression.
 *
 * `btoa` alone throws on any code point above 0xFF, and an exported STL carries
 * whatever the user named a model. `encodeURIComponent` + `unescape` widens
 * those to UTF-8 bytes first, which is what the server's `b64decode` expects.
 */
export function encodeStlFiles(files: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [filename, text] of Object.entries(files)) {
    out[filename] = btoa(unescape(encodeURIComponent(text)))
  }
  return out
}

/** The body, once the manifest and the STL set are in hand. */
export function runRequestBody(
  kind: RunKind,
  cfdrunJson: string,
  filename: string,
  stlFiles: Record<string, string>,
): RunRequestBody {
  return {
    cfdrunJson,
    cfdrunFilename: filename,
    stlFiles: kind === 'mesh' ? stlFiles : {},
    meshOnly: kind === 'mesh',
    solveOnly: kind === 'solve',
  }
}

/** The message the legacy shows for duplicate names, with the offenders named. */
export function duplicateSolidMessage(duplicates: DuplicateSolid[]): string {
  const details = duplicates.map((d) => `"${d.name}" => ${d.sources.join(', ')}`).join('\n')
  return t('error.duplicate_solid_names', { details })
}

/**
 * Assemble the run body for the CURRENT project.
 *
 * Manifest first, then STL: `buildComprehensiveStlMap` tessellates the whole
 * scene to find the porous solids and stashes the result for the upload to
 * reuse (`js/cfdrun_build.js:1142`), so building in the other order pays for
 * 8 MB of ASCII STL twice.
 */
export async function buildRunBody(kind: RunKind, deps: RunBuildDeps): Promise<RunRequestBody> {
  const { project, projectPath } = deps
  // Refusing beats submitting: an empty project would upload an empty STL set,
  // and the server wipes triSurface/ before writing it — so a run started
  // before a project was loaded would delete the case's geometry.
  if (!project) throw new Error(t('error.no_valid_geometry'))
  assertGeometryTopologyAirExportReady(project)
  const uids = deps.modelUids()
  if (uids.length === 0) throw new Error(t('error.no_valid_geometry'))

  if (kind === 'mesh') {
    const duplicates = deps.duplicateSolids(uids)
    if (duplicates.length) throw new Error(duplicateSolidMessage(duplicates))
  }

  const manifest = await deps.buildManifest()
  // buildManifest already tessellated and cached this exact grouped map. A
  // solve needs its original source metadata, without re-uploading STL bytes.
  const files = deps.buildStl()
  bindStlFileHashes(manifest, files, project)
  return runRequestBody(
    kind,
    JSON.stringify(manifest),
    cfdrunFilename(project, projectPath),
    kind === 'mesh' ? encodeStlFiles(files) : {},
  )
}
