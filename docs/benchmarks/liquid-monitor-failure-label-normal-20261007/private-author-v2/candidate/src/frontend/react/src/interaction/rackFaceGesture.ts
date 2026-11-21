import { resolveSlotHit } from '../geometry/rackSlots'
import type { Construction } from '../schema/cfdproj'
import type { TreeSelection } from './treeNodes'

/**
 * What a click on a rack's front or back face selects.
 *
 * The viewport used to answer this with one rule — "the U slot under the
 * cursor" — which is wrong in two of the three cases the legacy separates
 * (`_resolveRackFaceHitToSlot`'s caller, STL_generate.html:17866-17883):
 *
 *  - a slot with a SERVER in it belongs to the server, and its plain click is
 *    that server's inlet or outlet face. The panel it opens is where the flow
 *    and power live, so resolving it to a bare slot hides them.
 *  - a slot inside a LEAKAGE zone belongs to the whole zone. A through zone is
 *    a contiguous run of open U, exported as one cellZone with one σ, so
 *    selecting one U of it reaches the σ field only by landing on whichever U
 *    the panel happens to key off.
 *
 * `shiftKey` promotes a server face to the whole server. The viewport itself
 * no longer passes it (shift there selects the whole RACK, before this table
 * is consulted), but the tree and scripts still can.
 *
 * Pure, and separate from the viewport, because the mapping is a table and the
 * viewport is 900 lines of Three.js around it.
 */

export type RackFaceSide = 'front' | 'back'

/**
 * The selection for a hit that landed ON a U slot.
 *
 * `null` is never returned here — every index resolves to something — but the
 * caller still has to handle a hit that missed every slot, which is the frame
 * case below.
 */
export function rackFaceSelection(
  model: Construction,
  uid: string,
  index: number,
  side: RackFaceSide,
  shiftKey: boolean,
): TreeSelection {
  const hit = resolveSlotHit(model, index)
  if (hit.type === 'equipment' && hit.equipmentId) {
    return shiftKey
      ? { type: 'equipment', uid, equipmentId: hit.equipmentId, side }
      : {
          type: 'serverFace',
          uid,
          equipmentId: hit.equipmentId,
          // The face a rack is entered through: air goes in the front and out
          // the back, so the side that was clicked names the patch.
          faceId: side === 'front' ? 'inlet' : 'outlet',
          side,
        }
  }
  if (hit.type === 'through_zone' && hit.startUnit !== undefined && hit.endUnit !== undefined) {
    const start = Math.min(hit.startUnit, hit.endUnit)
    const end = Math.max(hit.startUnit, hit.endUnit)
    return {
      type: 'rackSlot',
      uid,
      side,
      index: start,
      indices: Array.from({ length: end - start + 1 }, (_, k) => start + k),
    }
  }
  return { type: 'rackSlot', uid, side, index }
}

/**
 * Does this viewport click ADD the model under the pointer to the selection
 * (or take it out)?
 *
 * Ctrl/Cmd always does. Shift does too for the other model kinds — the legacy
 * viewport's rule (STL_generate.html `handleViewportSelection`: "Ctrl+click OR
 * Shift+click: model-level multi-select toggle"), which is what lets two air
 * pipes or baffles be shift-clicked into one selection.
 *
 * Racks, CDUs and boxes use shift as the whole-object gesture (select
 * the rack whatever face, U-slot, server or patch was hit — see the viewport),
 * because a rack's face is where the legacy spends shift on slot ranges and
 * server promotion, and one modifier cannot mean two things there.
 */
function isWholeObjectKind(kind: string | null | undefined): boolean {
  return kind === 'rack' || kind === 'cdu' || kind === 'box'
}

/** Shift promotes any visible cabinet/body face to the whole model. Ctrl/Cmd
 * retain additive toggle; repeating Shift must not deselect an existing body. */
export function isWholeObjectSelectGesture(
  e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean },
  kind: string | null | undefined,
): boolean {
  return e.shiftKey && !e.ctrlKey && !e.metaKey && isWholeObjectKind(kind)
}

export function isAdditiveSelectGesture(
  e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean },
  kind: string | null | undefined,
): boolean {
  return e.ctrlKey || e.metaKey || (e.shiftKey && !isWholeObjectKind(kind))
}

/**
 * The rack's rule: Ctrl/Cmd adds, shift does not.
 *
 * Kept for callers that do not know the kind; the viewport now asks
 * `isAdditiveSelectGesture`. The `onRackFace` flag no longer changes the answer.
 */
export function isMultiSelectGesture(
  e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean },
  _onRackFace: boolean,
): boolean {
  return isAdditiveSelectGesture(e, 'rack')
}
