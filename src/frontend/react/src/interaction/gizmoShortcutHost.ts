/**
 * The R / V half of the viewport's `EditorKeyHost`.
 *
 * Split out of `Viewport.tsx` so the wiring is tested, not just the rule: WHERE
 * the keys are off is read from the modules that own each mode (first person,
 * the 2D room editor, STL preview, rack detail), and WHAT they act on is the
 * selection at the moment of the key press. A host that passed a constant tab,
 * dropped a flag, or captured the selection once at install would still pass
 * `gizmoShortcutBlocked`'s own test.
 *
 * The viewport supplies only what only it holds: the open tab, the selection
 * refs, and whether one of its drags is live.
 */

import * as store from '../projectStore'
import { isRackDetailMode } from '../geometry/rackDetail'
import { isPreviewActive } from '../io/stlPreview'
import { firstPerson } from '../legacy/firstPerson'
import { isRoom2dPanelOpen } from '../room2d/room2dPanelSlot'
import type { EditorKeyHost } from './editorKeys'
import { gizmoShortcutBlocked, toggleSelectionGizmo } from './transformToolToggle'
import type { TreeSelection } from './treeNodes'

export interface GizmoShortcutSources {
  /** The open left tab. */
  activeTab(): string
  /** Every selected model, active one first. */
  selectedUids(): readonly string[]
  /** The narrower selection under the active model, if any. */
  node(): TreeSelection | null
  /** True while any viewport drag is live. */
  dragging(): boolean
}

export function createGizmoShortcutHost(
  src: GizmoShortcutSources,
): Required<Pick<EditorKeyHost, 'gizmoShortcutsBlocked' | 'toggleGizmo'>> {
  return {
    // First-person mode passes R and V straight through (its own table is
    // WASD/C/F/Space/Shift), so it has to be asked here. Two flags: the
    // module's own `isActive()`, and the window flag it sets while locked.
    gizmoShortcutsBlocked: () =>
      gizmoShortcutBlocked({
        firstPerson:
          firstPerson()?.isActive() === true ||
          (window as unknown as { _fpsActive?: boolean })._fpsActive === true,
        room2d: isRoom2dPanelOpen(),
        preview: isPreviewActive(),
        rackDetail: isRackDetailMode(),
        dragging: src.dragging(),
        tab: src.activeTab(),
      }),
    // The same selection the inspector shows: the models, and the narrower
    // node under the active one — a patch or a server has no gizmo, so the key
    // does nothing there, exactly as the panel offers nothing.
    toggleGizmo: (kind) => {
      toggleSelectionGizmo(kind, {
        project: store.getProject(),
        uids: src.selectedUids(),
        node: src.node(),
      })
    },
  }
}
