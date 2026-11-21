/**
 * Which inspector panel a selection opens.
 *
 * The inspector used to decide this inline, in a chain of ternaries whose
 * ORDER was the behaviour. The transform block and the R / V shortcuts both
 * need to know the same answer — "is the thing on screen the model, a part of
 * its body, or something else inside it?" — so the chain lives here, once, and
 * the inspector dispatches on the result.
 *
 * The order is load-bearing and copied unchanged from the chain it replaces:
 *
 * - An imported-mesh region, or a patch on an imported mesh, is tested FIRST:
 *   a patch on a `custom` model is BOTH a patch node and a custom-mesh node,
 *   and testing the generic patch first would make the custom panel
 *   unreachable.
 * - A node missing the ids its panel needs (a patch with no patch id, a slot
 *   with no index) falls through to the MODEL's panel, as it always has.
 * - A wall, baffle, floor or partition wall's face IS the model: the sheet has
 *   one working face, so its panel is the model's, under a banner naming it.
 */

import type { Construction } from '../schema/cfdproj'
import type { TreeSelection } from './treeNodes'

export type InspectorPanel =
  | 'region'
  | 'customPatch'
  | 'patch'
  | 'equipment'
  | 'rackSlot'
  | 'serverFace'
  | 'partition'
  | 'wallRegion'
  | 'planarFace'
  | 'face'
  | 'pipeSegment'
  | 'airPipe'
  | 'cdu'
  | 'model'

export const PLANAR_FACE_KINDS: ReadonlySet<string> = new Set([
  'wall',
  'baffle',
  'floor',
  'partitionWall',
])

export function inspectorPanelFor(
  model: Construction,
  node?: TreeSelection | null,
): InspectorPanel {
  const kind = String(model.kind ?? '')
  if (node) {
    if (node.type === 'region') return 'region'
    if (node.type === 'patch' && kind === 'custom' && node.patchId && node.faceId) {
      return 'customPatch'
    }
    if ((node.type === 'patch' || node.type === 'serverPatch') && node.patchId && node.faceId) {
      return 'patch'
    }
    if (node.type === 'equipment' && node.equipmentId) return 'equipment'
    if (node.type === 'rackSlot' && typeof node.index === 'number') return 'rackSlot'
    if (node.type === 'serverFace' && node.equipmentId && node.faceId) return 'serverFace'
    if (node.type === 'partition' && typeof node.index === 'number') return 'partition'
    if (node.type === 'wallRegion' && node.faceId && typeof node.index === 'number') {
      return 'wallRegion'
    }
    if (node.type === 'face' && node.faceId) {
      return PLANAR_FACE_KINDS.has(kind) ? 'planarFace' : 'face'
    }
  }
  if (kind === 'pipe_segment') return 'pipeSegment'
  if (kind === 'pipe') return 'airPipe'
  if (kind === 'cdu') return 'cdu'
  return 'model'
}
