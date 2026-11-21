/**
 * The top bar's lighting group: on/off, intensity, look and shadows.
 *
 * Always on the bar, not inside a tool group: light is what every mode is seen
 * in, geometry, mesh preview and results alike, and the switch used to sit two
 * clicks deep in 视图工具. The on/off is the project's `view.lights`; the rest
 * is the shared lighting state (ui/lightingState), which the post-processing
 * slider, the Three.js viewport and the OV display read too.
 */

import { Icon } from './Icon'
import { useSyncExternalStore } from 'react'
import {
  getLighting,
  LIGHT_SCALE_MAX,
  LIGHT_SCALE_MIN,
  lightingPresets,
  setLighting,
  subscribeLighting,
} from '../ui/lightingState'
import { useTranslation } from '../useTranslation'
import type { TranslationKey } from '../i18n'

export interface LightingControlProps {
  on: boolean
  onToggle(next: boolean): void
}

export function LightingControl({ on, onToggle }: LightingControlProps) {
  const { t } = useTranslation()
  const lit = useSyncExternalStore(subscribeLighting, getLighting, getLighting)
  return (
    <div id="toolbar-lighting" className="toolbar-lighting" role="group" aria-label={t('lighting.group')}>
      <button
        id="light-toggle-btn"
        type="button"
        className={`tool-btn${on ? ' active' : ''}`}
        aria-pressed={on}
        onClick={() => onToggle(!on)}
        title={t('tooltip.toggle_lights')}
      >
        <Icon name="sun" />
        {t('toolbar.toggle_lights')}
      </button>
      <input
        id="light-scale"
        type="range"
        min={LIGHT_SCALE_MIN}
        max={LIGHT_SCALE_MAX}
        step={0.05}
        value={lit.scale}
        disabled={!on}
        aria-label={t('lighting.intensity')}
        title={t('lighting.intensity')}
        onChange={(e) => setLighting({ scale: Number(e.target.value) })}
        onDoubleClick={() => setLighting({ scale: 1 })}
      />
      <span id="light-scale-val" className="mono-pct">{Math.round(lit.scale * 100)}%</span>
      <select
        id="light-preset"
        className="tool-btn"
        value={lit.preset}
        disabled={!on}
        aria-label={t('lighting.preset')}
        title={t('lighting.preset')}
        onChange={(e) => setLighting({ preset: e.target.value })}
      >
        {lightingPresets().map((id) => (
          <option key={id} value={id}>
            {t(`lighting.preset_${id}` as TranslationKey)}
          </option>
        ))}
      </select>
      <label className="tool-btn toolbar-lighting-shadows" title={t('lighting.shadows_tip')}>
        <input
          id="light-shadows"
          type="checkbox"
          checked={lit.shadows}
          disabled={!on}
          onChange={(e) => setLighting({ shadows: e.target.checked })}
        />
        {t('lighting.shadows')}
      </label>
    </div>
  )
}
