/**
 * What "nothing is selected" has to undo.
 *
 * Clearing the selection is not only a UI state change. The legacy's
 * `setSelection('project')` — which is what BOTH the blank area of the tree and
 * the tree's root node call (STL_generate.html:151 and :12650) — also turns off
 * every model's move and rotate tool and drops the category highlight
 * (STL_generate.html:6290-6296).
 *
 * Without the tool half, deselecting leaves 32 models still carrying
 * `moveTool.enabled`, so clicking any one of them afterwards puts the drag
 * arrows straight back on a group the user thought they had let go of.
 *
 * Live, with no undo entry, because the legacy does not checkpoint a selection
 * either: the flags are view state that happens to live in the .cfdproj.
 */

import { allModels } from '../schema/cfdproj'
import * as store from '../projectStore'

interface ToolFlags {
  moveTool?: { enabled?: boolean }
  rotateTool?: { enabled?: boolean }
}

/**
 * Turn off every model's move and rotate tool.
 *
 * Returns whether anything was actually on, so a click on empty space does not
 * mark a clean project dirty.
 */
export function clearTransformTools(): boolean {
  const project = store.getProject()
  if (!project) return false
  let changed = false
  store.mutateLive((live) => {
    for (const { model } of allModels(live)) {
      const tools = model as unknown as ToolFlags
      if (tools.moveTool?.enabled) {
        tools.moveTool.enabled = false
        changed = true
      }
      if (tools.rotateTool?.enabled) {
        tools.rotateTool.enabled = false
        changed = true
      }
    }
  }, { scene: false })
  // The legacy logs this as `selectNode` with type `project`
  // (STL_generate.html:12902); reusing the name keeps the two operation logs
  // comparable.
  if (changed) store.notifyLiveEdit('selectNode', 'project')
  return changed
}
