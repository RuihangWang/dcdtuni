/**
 * 导出 CFD 工程包 — the manifest and its STL set as ONE archive.
 *
 * Mirrors `exportCFDProject` (STL_generate.html:29161): name the project,
 * point every object's `stlFile` at the merged group file it is actually in,
 * then pack `<project>_cfd/` into `<project>_cfd.zip`.
 *
 * ## Why the remap exists
 *
 * `buildCfdrunJson` already writes the group filenames, so the remap is
 * normally a no-op — which is exactly why it is worth keeping. An object whose
 * `stlFile` still names a per-model file (an older project, a path that missed
 * the group table) points the runner at a file the package does not contain,
 * and the failure surfaces as a missing PATCH during meshing rather than as a
 * missing file during export.
 *
 * ## One archive, not a burst of downloads
 *
 * The parts of a package are useless apart: the `.cfdrun` names STL files that
 * must sit beside it. Firing a dozen `a[download]` clicks also loses files —
 * browsers cancel repeat downloads from one gesture without saying so.
 */

import { buildZip, readZip, type ZipEntry } from './zipWriter'
import type { CfdrunV2 } from '../schema/cfdrun'
import { bindStlFileHashes } from '../legacy/cfdrunBuild'

/**
 * Model kind -> the merged file its geometry is written into.
 *
 * The subset of `STL_EXPORT_GROUPS` (js/geometry_scene.js:209) that the legacy
 * export remaps: racks keep the frame/door split their own entries carry, and
 * the perforated-floor group is an extra surface on top of the floor file
 * rather than a home for a floor object.
 */
export const KIND_GROUP_FILE: Record<string, string> = {
  acu: 'merged_acu.stl',
  box: 'merged_box.stl',
  pipe: 'merged_pipe.stl',
  baffle: 'merged_baffle.stl',
  floor: 'merged_floor_ceiling.stl',
  wall: 'room_wall.stl',
  partitionWall: 'merged_partition_wall.stl',
}

/** meshGeometry bucket -> the kind whose group file its objects belong in. */
const BUCKET_KIND: [string, string][] = [
  ['acus', 'acu'],
  ['wallObjects', 'box'],
  ['baffles', 'baffle'],
  ['floors', 'floor'],
  ['walls', 'wall'],
  ['partitionWalls', 'partitionWall'],
]

type Bucket = Record<string, { stlFile?: string }>

/**
 * Point every object at its group file, in place. Returns what changed.
 *
 * `walls` are the one exception, as in the legacy (STL_generate.html:29202): a
 * wall entry with NO `stlFile` is a room boundary the mesher builds from the
 * block, not a solid in the STL set, and giving it a filename would declare a
 * patch the package cannot supply.
 */
export function remapStlFiles(meshGeometry: Record<string, unknown>): { path: string; from: string | undefined; to: string }[] {
  const changed: { path: string; from: string | undefined; to: string }[] = []
  for (const [bucketName, kind] of BUCKET_KIND) {
    const bucket = meshGeometry[bucketName] as Bucket | undefined
    if (!bucket || typeof bucket !== 'object') continue
    const target = KIND_GROUP_FILE[kind] as string
    for (const [id, entry] of Object.entries(bucket)) {
      if (!entry || typeof entry !== 'object') continue
      if (bucketName === 'walls' && !entry.stlFile) continue
      if (entry.stlFile === target) continue
      changed.push({ path: `${bucketName}.${id}`, from: entry.stlFile, to: target })
      entry.stlFile = target
    }
  }
  return changed
}

export interface CfdPackage {
  /** `<project>_cfd.zip`, the name the download is offered under. */
  filename: string
  /** Every path inside the archive, folder entry first. */
  entries: string[]
  bytes: Uint8Array
}

/**
 * Assemble the package.
 *
 * The layout is the legacy's: a single top-level folder `<project>_cfd/`
 * holding the merged STL files and `<project>.cfdrun`. Unpacking into the
 * current directory therefore leaves one folder rather than scattering a dozen
 * files, and the runner is pointed at the `.cfdrun` inside it.
 */
export function buildCfdPackage(
  projectName: string,
  manifest: CfdrunV2,
  stlFiles: Record<string, string>,
  now: Date = new Date(),
  project?: import('../schema/cfdproj').CfdProject,
): CfdPackage {
  bindStlFileHashes(manifest,stlFiles,project)
  const root = `${projectName}_cfd`
  const entries: ZipEntry[] = [{ name: `${root}/`, text: '' }]
  for (const [filename, stl] of Object.entries(stlFiles)) {
    entries.push({ name: `${root}/${filename}`, text: stl })
  }
  // Last, as JSZip writes it: the folder's files are added before the manifest
  // in `exportCFDProject` (:29207-29208).
  entries.push({ name: `${root}/${projectName}.cfdrun`, text: JSON.stringify(manifest, null, 2) })

  return {
    filename: `${root}.zip`,
    entries: entries.map((e) => e.name),
    bytes: buildZip(entries, now),
  }
}

/**
 * The project name the package is built under.
 *
 * `state.meta.projectName` when the project has one, and otherwise the export
 * filename — which is what the legacy offers as the DEFAULT in its prompt
 * (STL_generate.html:29173), falling back to `CFD_Project`.
 */
export function defaultPackageName(meta: { projectName?: string; exportFileName?: string } | undefined): string {
  return meta?.projectName || meta?.exportFileName || 'CFD_Project'
}

export interface PackageRecord extends CfdPackage {
  manifest: CfdrunV2
  remapped: { path: string; from: string | undefined; to: string }[]
}

let lastBuilt: PackageRecord | null = null

/** What the export dialog produced last, for the dev handle below. */
export function rememberPackage(record: PackageRecord) {
  lastBuilt = record
}

export function lastPackage(): PackageRecord | null {
  return lastBuilt
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx: a
// download cannot be inspected from a headless browser, so without this the
// only way to check what the package CONTAINS is to open the saved file by
// hand. `read` is here too, so the archive can be listed where it was built.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __dcdtuniPackage: unknown }).__dcdtuniPackage = {
    last: lastPackage,
    read: readZip,
  }
}

/** Hand the archive to the browser as a download. */
export function downloadZip(filename: string, bytes: Uint8Array) {
  // A fresh ArrayBuffer copy: `bytes` may be a view into a larger buffer, and
  // Blob would then take the whole of it.
  const url = URL.createObjectURL(new Blob([bytes.slice().buffer], { type: 'application/zip' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
