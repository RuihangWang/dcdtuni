import type { Construction } from '../schema/cfdproj'
import {
  commitRackServersOpacity,
  previewRackServersOpacity,
  rackServerRecords,
  rackServersSliderPercent,
} from '../geometry/serverOpacity'
import { useTranslation } from '../useTranslation'
import { OpacitySlider } from './OpacitySlider'

/**
 * The rack panel's SECOND opacity slider: every server at once.
 *
 * Spec 2026-09-17 §5.1. The model slider above it (`ModelDisplayGroup`) fades
 * the rack body — frame, blanking panels, doors — and since the split it no
 * longer touches server meshes. This one is a batch writer over the per-server
 * `meta.display.opacity` fields, never a stored variable of its own: one drag
 * sets every server of the rack to the same value in ONE undo entry.
 *
 * Initial position: the common value when all servers agree; when they do not,
 * the mean plus the "mixed" hint — the same reading the multi-select group
 * slider gives, so a rack half-faded from the equipment inspector shows where
 * its servers actually are instead of lying with the first one's value.
 */
export function RackServerOpacityGroup({ model }: { model: Construction }) {
  const { t } = useTranslation()
  if (model.kind !== 'rack') return null
  const count = Object.keys(rackServerRecords(model)).length
  // No servers, nothing to batch-write: an empty rack shows only the body knob.
  if (!count) return null
  const { percent, mixed } = rackServersSliderPercent(model)

  return (
    <div className="prop-group" id="rack-server-opacity-group">
      <div className="prop-title">{t('label.rack_server_opacity')}</div>
      <div className="prop-row">
        <label htmlFor="rack-server-opacity-slider">{t('label.rack_server_opacity')}</label>
        <OpacitySlider
          id="rack-server-opacity-slider"
          badgeId="rack-server-opacity-badge"
          committed={percent}
          onPreview={(p) => previewRackServersOpacity(model.uid, p)}
          onCommit={(p) => commitRackServersOpacity(model.uid, p)}
        />
      </div>
      {mixed && (
        <div className="prop-hint" id="rack-server-opacity-mixed">
          {t('rack.stacked_mixed')}
        </div>
      )}
      <div className="prop-hint">{t('hint.rack_server_opacity_desc')}</div>
    </div>
  )
}
