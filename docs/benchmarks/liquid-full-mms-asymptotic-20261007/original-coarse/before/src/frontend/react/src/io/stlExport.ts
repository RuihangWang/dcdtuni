/**
 * Exporting STL from React, through the shared exporter.
 *
 * `buildGroupedStlMap` reads the meshes the scene BUILT — `renderMeshes` and
 * their `userData.exportName` tags — so this is only correct because the
 * viewport now runs the same geometry builder as the legacy editor. Before
 * that, exporting from here would have produced a different set of OpenFOAM
 * patches from the same project.
 *
 * `parity/stl_oracle.py` compares the two apps' output byte for byte, which is
 * the only check that means anything here: a flipped normal or a renamed solid
 * still yields a valid STL, a mesh that looks right, and a boundary condition
 * on the wrong surface.
 */

import type { CfdProject } from '../schema/cfdproj'
import { stateV4 } from '../legacy/cfdprojModules'
import { stlNaming } from '../legacy/stlNaming'
import type { SceneBuilder } from '../interaction/sceneBuilder'

export interface StlExportResult {
  /** filename -> ASCII STL text, one entry per merged export group. */
  files: Record<string, string>
  totalFacets: number
}

/**
 * Build the grouped STL map for a project.
 *
 * `includeLeakage` and the "every model, enabled or not" model list mirror what
 * `exportCFDProject` passes: a disabled model still has to reach the mesh, or
 * its patch vanishes from the case while the project still declares it.
 */
export function buildStlFiles(
  builder: SceneBuilder,
  project: CfdProject,
  includeLeakage = true,
): StlExportResult {
  const uids = (stateV4().allModels(project) as { uid: string }[]).map((m) => m.uid)
  const files = builder.api.buildGroupedStlMap(uids, includeLeakage)
  let totalFacets = 0
  for (const text of Object.values(files)) {
    totalFacets += (text.match(/facet normal/g) ?? []).length
  }
  return { files, totalFacets }
}

/** POST the built STL set to the server, the way the legacy exporter does. */
export async function uploadStlFiles(files: Record<string, string>): Promise<boolean> {
  const resp = await fetch('/api/browser/export-stl', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ files }),
  })
  return resp.ok
}


/**
 * Solid names claimed by more than one model.
 *
 * A duplicate is not a warning: two solids with the same name collapse into one
 * OpenFOAM patch, so a boundary condition meant for one lands on both and the
 * case solves a different problem. The legacy exporter refuses the export on
 * this, and so does React.
 *
 * Names come from the BUILT meshes (`userData.exportName`) plus the rack
 * leakage zones, which are exported down a separate path and would otherwise
 * be invisible to the check.
 */
export function duplicateSolidNames(
  builder: SceneBuilder,
  project: CfdProject,
  modelIds: string[],
): { name: string; sources: string[] }[] {
  const wanted = new Set(modelIds)
  const byName = new Map<string, Map<string, string>>()
  const add = (solid: string, uid: string, modelName: string) => {
    let entry = byName.get(solid)
    if (!entry) byName.set(solid, (entry = new Map()))
    // Keyed by model uid: one model contributing a name twice is fine, the
    // exporter merges those. Two DIFFERENT models are the problem.
    if (!entry.has(uid)) entry.set(uid, modelName)
  }

  const models = stateV4().allModels(project) as {
    uid: string
    kind?: string
    meta?: { name?: string }
  }[]
  const nameOf = new Map(models.map((m) => [m.uid, m.meta?.name ?? m.uid]))

  for (const mesh of builder.registries.renderMeshes) {
    const data = mesh.userData as { exportName?: string; modelId?: string }
    if (!data.exportName || !data.modelId || !wanted.has(data.modelId)) continue
    add(data.exportName, data.modelId, nameOf.get(data.modelId) ?? data.modelId)
  }

  const naming = stlNaming()
  for (const model of models) {
    if (!wanted.has(model.uid) || model.kind !== 'rack') continue
    const groups = naming.getRackThroughZoneGroups(model as never) as {
      startUnit: number
      endUnit: number
    }[]
    if (!groups?.length) continue
    const rackName = naming.sanitizeName(model.meta?.name ?? model.uid)
    for (const g of groups) {
      add(
        `${rackName}_u${g.startUnit + 1}_u${g.endUnit + 1}_through_zone`,
        model.uid,
        model.meta?.name ?? model.uid,
      )
    }
  }

  const out: { name: string; sources: string[] }[] = []
  for (const [name, entry] of byName) {
    if (entry.size > 1) out.push({ name, sources: [...entry.values()] })
  }
  return out
}

/** Trigger a browser download of one text file. */
export function downloadText(filename: string, text: string, type = 'application/octet-stream') {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  // Revoked on the next tick, not immediately: Chrome reads the blob
  // asynchronously after the click, and revoking in the same turn cancels the
  // download on some versions.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
