import {
  composeFaceScopedExportName,
  faceOf,
  facePrefixSupported,
  isFacePrefixEnabled,
  toggleFacePrefix,
} from '../geometry/facePrefix'
import type { Construction } from '../schema/cfdproj'
import { useTranslation } from '../useTranslation'

/**
 * Section 1 of the legacy face inspector: naming and the export-name prefix.
 *
 * `renderFaceInspector` (STL_generate.html:10416) opens with this group —
 * status badge, the live name preview, and the two buttons that turn the
 * `{face}_` prefix on and off. It sits ABOVE the rest of the face panel here
 * for the same reason it does there: the name is what the boundary condition
 * below it will be attached to.
 *
 * The face-name input the legacy puts in this group is not repeated: the face
 * panel beneath already has one bound to the same field, and two inputs for one
 * value is how they end up disagreeing.
 */
export function FacePrefixSection({
  model,
  faceId,
}: {
  model: Construction
  faceId: string
}) {
  const { t } = useTranslation()
  const face = faceOf(model, faceId)
  if (!face) return null
  const supported = facePrefixSupported(model.kind)
  const enabled = isFacePrefixEnabled(face)
  // The legacy previews the composed name against a placeholder region, so the
  // effect of the toggle is visible on a face that has no named regions yet.
  const preview = composeFaceScopedExportName(faceId, face, 'sample_region')

  return (
    <div className="prop-group" style={{ borderLeftColor: 'rgba(0,255,136,0.4)' }}>
      <div className="prop-title" style={{ color: 'var(--highlight, #00ff88)' }}>
        {t('inspector.face_naming_prefix')}
      </div>
      <div className="prop-row">
        <label>{t('label.prefix_status')}</label>
        <span
          className="badge"
          id="face-prefix-status"
          style={{
            background: enabled ? '#16825d' : '#333',
            borderColor: enabled ? '#00ff88' : '#555',
          }}
        >
          {enabled ? t('label.prefix_enabled') : t('label.prefix_disabled')}
        </span>
      </div>
      <div className="prop-hint">
        {t('label.current_preview')}
        <br />
        <span
          id="face-prefix-preview"
          style={{ color: 'var(--highlight, #00ff88)', fontFamily: 'Share Tech Mono, monospace' }}
        >
          {preview}
        </span>
      </div>
      <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
        <button
          className="btn"
          id="face-prefix-add"
          style={{ margin: 0, flex: 1 }}
          // A wall, floor or baffle exports with no face component at all, so
          // the flag cannot bite there and the legacy's handler silently
          // returns. Disabled rather than silently inert.
          disabled={!supported || enabled}
          title={supported ? undefined : String(model.kind)}
          onClick={() => toggleFacePrefix(model.uid, faceId, true)}
        >
          {t('btn.add_prefix')}
        </button>
        <button
          className="btn"
          id="face-prefix-remove"
          style={{ margin: 0, flex: 1 }}
          disabled={!supported || !enabled}
          title={supported ? undefined : String(model.kind)}
          onClick={() => toggleFacePrefix(model.uid, faceId, false)}
        >
          {t('btn.remove_prefix')}
        </button>
      </div>
    </div>
  )
}
