/**
 * What the inspector header calls the thing below it.
 *
 * The panel changes completely between a wall, a U-slot, a CDU and a patch, and
 * the header is the only line that says which one is on screen. Eleven branches
 * in the legacy (`#prop-title`, STL_generate.html:12704 and the STL-preview copy
 * at :12214), collapsed here into a pure function so the mapping can be checked
 * without rendering an app.
 *
 * `serverFace` and `serverPatch` deliberately share ONE literal, 'Server Face',
 * with no key: the legacy has none either, and inventing one would put a string
 * in this app's header that the other app cannot render.
 */

import type { TranslationKey } from '../i18n'
import type { TreeSelection } from '../interaction/treeNodes'

export interface InspectorTitleInput {
  /** The narrower selection, when the tree picked one. */
  node: TreeSelection | null
  /** The selected model's kind, or null when nothing is selected. */
  kind: string | null
  /** How many models are selected, for the multi-select count. */
  count: number
}

export type InspectorTitle =
  | { key: TranslationKey; vars?: Record<string, string | number> }
  | { literal: string }

const KIND_KEYS: Record<string, TranslationKey> = {
  wall: 'inspector.wall_settings',
  baffle: 'inspector.baffle_settings',
  floor: 'inspector.floor_settings',
  acu: 'inspector.acu_settings',
  cdu: 'liquid.cdu_settings',
  pipe: 'pipe.air_title',
}

const PLANAR_FACE_KINDS = new Set(['wall', 'baffle', 'floor', 'partitionWall'])

const NODE_KEYS: Record<string, TranslationKey> = {
  face: 'inspector.face_projection',
  wallRegion: 'inspector.wall_region',
  region: 'inspector.region_info',
  rackSlot: 'inspector.uslot_settings',
  equipment: 'inspector.equipment_settings',
  partition: 'inspector.partition_settings',
  patch: 'inspector.patch_editor',
}

export function inspectorTitle({ node, kind, count }: InspectorTitleInput): InspectorTitle {
  if (!kind && !node) return { key: 'inspector.project_settings' }
  if (node && (node.type === 'serverFace' || node.type === 'serverPatch')) {
    return { literal: 'Server Face' }
  }
  // A planar body's face IS the model's panel (the sheet has one working
  // face), so the header keeps the kind's own name and the face banner below
  // says which face was hit.
  if (node && node.type === 'face' && kind && PLANAR_FACE_KINDS.has(kind)) {
    return { key: KIND_KEYS[kind] ?? 'inspector.model_settings' }
  }
  if (node && node.type !== 'model') {
    const key = NODE_KEYS[node.type]
    if (key) return { key }
  }
  // Several models: the panel below is the multi-select one, and naming a
  // single kind there would claim an edit applies to one model when it applies
  // to all of them.
  if (count >= 2) return { key: 'inspector.multi_select', vars: { count } }
  const key = kind ? KIND_KEYS[kind] : undefined
  return { key: key ?? 'inspector.model_settings' }
}
