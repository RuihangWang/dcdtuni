import type { Construction } from '../schema/cfdproj'
import {
  isPrimaryFaceReversed,
  orientationEligible,
  togglePrimaryFaceSolidNormal,
} from '../geometry/surfaceOrientation'
import { useTranslation } from '../useTranslation'

/**
 * Which side of a wall or solid floor faces the room.
 *
 * The flip itself is reachable through the face sub-node, but only if you know
 * to open it — and nothing there says whether this surface is already flipped.
 * That is the whole value of this section: the badge is the state the exporter
 * will use, in the place the user is already looking.
 *
 * Ported from `buildWallFloorOrientationInspectorHtml`
 * (STL_generate.html:7455), which shows it directly under the size group.
 */

export interface SurfaceOrientationSectionProps {
  model: Construction
}

export function SurfaceOrientationSection({ model }: SurfaceOrientationSectionProps) {
  const { t } = useTranslation()
  if (!orientationEligible(model)) return null
  const reversed = isPrimaryFaceReversed(model)

  return (
    <div className="prop-group" style={{ borderLeftColor: '#36d47a' }}>
      <div className="prop-title" style={{ color: '#36d47a' }}>
        {t('inspector.surface_orientation')}
      </div>
      <div className="prop-hint">{t('hint.surface_orientation_legend')}</div>
      <div className="prop-row">
        <label>{t('label.current_state')}</label>
        <span
          className="badge"
          id="surface-orientation-state"
          style={{ borderColor: 'rgba(54,212,122,0.35)', color: '#36d47a' }}
        >
          {reversed ? t('label.orientation_flipped') : t('label.orientation_default')}
        </span>
      </div>
      <button
        className="btn"
        id="surface-orientation-flip"
        style={{
          background: 'rgba(224,108,117,0.08)',
          borderColor: 'rgba(224,108,117,0.35)',
          color: '#e06c75',
        }}
        onClick={() => togglePrimaryFaceSolidNormal(model.uid)}
      >
        {t('btn.flip_surface_orientation')}
      </button>
      <div className="prop-hint">{t('hint.surface_orientation_desc')}</div>
    </div>
  )
}
