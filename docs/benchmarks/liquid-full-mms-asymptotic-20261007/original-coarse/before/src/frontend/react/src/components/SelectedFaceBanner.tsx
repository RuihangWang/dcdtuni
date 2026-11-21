/**
 * Which FACE the selection sits on, named at the top of the inspector.
 *
 * A click on a wall, a baffle, a rack's front panel or a patch used to open a
 * panel that never said which face was hit: the planar kinds resolved to the
 * whole model, and the rack panels key off a U index. The banner reads the
 * face id (and the rack side) straight off the selection node and the face's
 * own display name when the model keeps one, so every face-level selection
 * says where it is.
 */

import type { Construction } from '../schema/cfdproj'
import type { TreeSelection } from '../interaction/treeNodes'
import { rackFaceLabel, rackFaceRole } from '../geometry/rackFaceLabels'
import { useTranslation } from '../useTranslation'

type Bag = Record<string, unknown>

/** The face the node refers to, if any. */
export function selectedFaceOf(
  node: TreeSelection | null | undefined,
): { faceId: string; side?: 'front' | 'back' } | null {
  if (!node || node.type === 'model' || node.type === 'equipment') return null
  const side = node.side === 'front' || node.side === 'back' ? node.side : undefined
  const faceId = typeof node.faceId === 'string' && node.faceId ? node.faceId : side
  if (!faceId) return null
  return { faceId, ...(side ? { side } : {}) }
}

/**
 * The face's user-facing name: its display name when the face record has one.
 *
 * A RACK's faces are named by their airflow role (`rackFaceLabel`): its front
 * is the inlet and its back the outlet, and a server face or U-slot on it is
 * named the same way. V4 racks keep no per-face record to hold that name —
 * `geometry.faces` is an array of door entries — so without this the banner
 * capitalised the raw key and said "Default_Rack_1 · Back" for the outlet.
 */
export function faceDisplayName(model: Construction, faceId: string): string {
  if (model.kind === 'rack') return rackFaceLabel(faceId)
  const faces = (model.geometry as Bag | undefined)?.faces
  if (faces && !Array.isArray(faces) && typeof faces === 'object') {
    const face = (faces as Record<string, Bag>)[faceId]
    const name = face?.displayName ?? face?.label
    if (typeof name === 'string' && name) return name
  }
  return faceId.charAt(0).toUpperCase() + faceId.slice(1)
}

export function SelectedFaceBanner({
  model,
  node,
}: {
  model: Construction
  node: TreeSelection | null | undefined
}) {
  const { t } = useTranslation()
  const face = selectedFaceOf(node)
  if (!face) return null
  const name = faceDisplayName(model, face.faceId)
  const isRack = model.kind === 'rack'
  return (
    <div
      className="prop-group"
      id="selected-face-banner"
      style={{ borderLeftColor: 'var(--accent3)' }}
      data-face-id={face.faceId}
      data-face-side={face.side ?? ''}
    >
      <div className="prop-title" style={{ color: 'var(--accent3)' }}>
        {t('inspector.selected_face')}
      </div>
      <div className="prop-row">
        <label>{t('label.face_name')}</label>
        <span className="value" id="selected-face-name">
          {model.meta?.name ?? model.uid} · {name}
        </span>
      </div>
      <div className="prop-row">
        <label>{t('label.selected_face_id')}</label>
        <span className="value" id="selected-face-id">
          {/* A rack side shows the ROLE token — `outlet`, the word its exported
              solids carry (`<rack>_outlet_u18_u23`) — while data-face-id above
              keeps the stored key. */}
          {isRack ? rackFaceRole(face.faceId) : face.faceId}
        </span>
      </div>
      {face.side && (
        <div className="prop-row">
          <label>{t('label.selected_face_side')}</label>
          <span className="value" id="selected-face-side">
            {isRack ? rackFaceLabel(face.side) : face.side === 'front' ? 'Front' : 'Back'}
          </span>
        </div>
      )}
    </div>
  )
}
