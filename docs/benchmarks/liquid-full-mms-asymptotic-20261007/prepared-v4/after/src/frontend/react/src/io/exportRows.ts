import { stlNaming } from '../legacy/stlNaming'
import * as store from '../projectStore'
import type { Construction } from '../schema/cfdproj'

/**
 * What the export dialog has to say about each model beyond its name.
 *
 * Two facts change what the export produces and neither is visible from a name:
 *
 *  - a rack with through-zones exports EXTRA solids for its leakage groups, so
 *    "include leakage" is the difference between one file and several. Without
 *    the count, unticking that box has no visible consequence anywhere.
 *  - a model hidden in the tree is still exported. `meta.enabled` is a 3D
 *    visibility flag, not a data flag (STL_generate.html:29735 checks it only
 *    to LABEL the row), so a user who hid a rack to see past it needs telling
 *    that it is still going into the mesh.
 */

export interface ExportRowBadges {
  /** Contiguous deleted-door U ranges. 0 for anything that is not a rack. */
  leakGroups: number
  /** `meta.enabled === false` — hidden in the viewport, exported anyway. */
  hidden: boolean
}

export function exportRowBadges(model: Construction): ExportRowBadges {
  const leakGroups =
    model.kind === 'rack' ? (stlNaming().getRackThroughZoneGroups(model)?.length ?? 0) : 0
  return { leakGroups, hidden: model.meta?.enabled === false }
}

/** Does any model carry leakage? The legacy hides the include-leakage box otherwise. */
export function anyLeakage(models: Construction[]): boolean {
  return models.some((m) => exportRowBadges(m).leakGroups > 0)
}

/**
 * Remember the name typed in the dialog.
 *
 * `confirmExportMergedSTL` writes it back to `state.meta.exportFileName`
 * (STL_generate.html:29800) with NO undo checkpoint, and the same applies here:
 * the name is a preference the next export starts from, not a geometry edit,
 * and putting it on the undo stack would make Ctrl+Z after an export undo the
 * filename instead of whatever the user last built.
 */
export function rememberExportFileName(name: string): void {
  const trimmed = name.trim()
  if (!trimmed) return
  const project = store.getProject()
  const meta = project?.meta as { exportFileName?: unknown } | undefined
  if (meta?.exportFileName === trimmed) return
  store.mutateLive((draft) => {
    draft.meta = { ...draft.meta, exportFileName: trimmed }
  }, { scene: false })
  store.notifyLiveEdit()
}
