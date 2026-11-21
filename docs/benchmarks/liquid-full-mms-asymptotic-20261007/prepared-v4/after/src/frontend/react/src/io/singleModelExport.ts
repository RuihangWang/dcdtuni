/**
 * Export ONE model to its own .stl file.
 *
 * `exportSingleModel` (STL_generate.html:29226). The whole-project export
 * writes merged per-kind files — `merged_rack.stl` carries every rack in the
 * room — so there is otherwise no way to look at one object's solids in an
 * external viewer, which is what you do when a single rack is the thing
 * snappyHexMesh is choking on.
 *
 * The bytes come from the BUILT scene, like every other export here: a solid's
 * OpenFOAM name lives in its mesh's `userData.exportName` and nowhere else.
 * Two consequences follow, and both are the legacy's:
 *
 * - `_withUnmergedModels` runs first. A merged proxy mesh carries no
 *   `exportName`, so exporting over one writes a file with no solid names.
 * - A DISABLED model has no meshes, so its file comes out empty. The caller
 *   reports that rather than handing the user a valid-looking 0-facet STL.
 */

import { findConstruction, getProject, logOperation } from '../projectStore'
import { stlNaming } from '../legacy/stlNaming'
import { downloadText } from './stlExport'
import type { Construction } from '../schema/cfdproj'

interface SingleExportApi {
  exportModelToStl(model: Construction, isMirrored: boolean, meshList?: unknown): string
  buildThroughZoneStl(model: Construction): { stl?: string } | null
  applyModelSolidOrientationOverrides(model: Construction, stlText: string): string
  _withUnmergedModels(cb: () => void): void
}

interface BuilderLike {
  api: SingleExportApi
  registries: { renderRoots: { parent?: { parent?: unknown } | null }[] }
}

function liveBuilder(): BuilderLike | null {
  return (window as unknown as { __sceneBuilder?: BuilderLike }).__sceneBuilder ?? null
}

/** The wrapper group whose matrix tells the exporter whether the scene is mirrored. */
function wrapperGroup(): { matrixWorld: { determinant(): number }; updateMatrixWorld(f?: boolean): void } | null {
  const three = (window as unknown as {
    __three?: { globalGroup?: { parent?: unknown } }
  }).__three
  return (three?.globalGroup?.parent as never) ?? null
}

export interface SingleExportResult {
  ok: boolean
  filename: string
  facets: number
  /** Set when nothing was written, so the caller can say why. */
  error?: string
}

/** The name the file will be given — shown in the inspector beside the button. */
export function exportFileNameFor(model: Construction): string {
  return `${stlNaming().sanitizeName(model.meta?.name || 'model')}.stl`
}

export function buildSingleModelStl(model: Construction): string {
  const builder = liveBuilder()
  if (!builder) throw new Error('the viewport has not built a scene yet')
  const wrapper = wrapperGroup()
  wrapper?.updateMatrixWorld(true)
  // A mirrored scene transform inverts every facet's winding, and the exporter
  // compensates by swapping two vertices. Asking the wrong object gives a file
  // whose normals all point into the solid.
  const isMirrored = (wrapper?.matrixWorld.determinant() ?? 1) < 0

  let stl = ''
  builder.api._withUnmergedModels(() => {
    stl = builder.api.exportModelToStl(model, isMirrored)
    // Leakage through-zones are exported down their own path, so a rack's file
    // would otherwise be missing the very solids the leakage BC binds to.
    if (model.kind === 'rack') {
      const leak = builder.api.buildThroughZoneStl(model)
      if (leak?.stl) stl += leak.stl
    }
    stl = builder.api.applyModelSolidOrientationOverrides(model, stl)
  })
  return stl
}

export function exportSingleModel(uid: string): SingleExportResult {
  const project = getProject()
  const model = project ? findConstruction(project, uid)?.model : null
  const filename = model ? exportFileNameFor(model) : ''
  if (!model) return { ok: false, filename, facets: 0, error: 'no such model' }

  let stl: string
  try {
    stl = buildSingleModelStl(model)
  } catch (e) {
    return { ok: false, filename, facets: 0, error: e instanceof Error ? e.message : String(e) }
  }
  const facets = (stl.match(/facet normal/g) ?? []).length
  if (!facets) {
    // An empty STL still opens in a viewer and still meshes — as nothing. Say
    // so instead of writing it.
    return { ok: false, filename, facets: 0, error: 'no geometry' }
  }
  downloadText(filename, stl)
  logOperation('exportSingleModel', `${model.meta?.name ?? uid} -> ${filename} (${facets} facets)`)
  return { ok: true, filename, facets }
}

if (typeof window !== 'undefined') {
  // Exposed so the parity harness can compare the two apps' bytes for one
  // model without driving a download dialog.
  ;(window as unknown as { __singleExport?: unknown }).__singleExport = {
    build: (uid: string) => {
      const project = getProject()
      const model = project ? findConstruction(project, uid)?.model : null
      return model ? buildSingleModelStl(model) : null
    },
    filename: (uid: string) => {
      const project = getProject()
      const model = project ? findConstruction(project, uid)?.model : null
      return model ? exportFileNameFor(model) : null
    },
    run: exportSingleModel,
  }
}
