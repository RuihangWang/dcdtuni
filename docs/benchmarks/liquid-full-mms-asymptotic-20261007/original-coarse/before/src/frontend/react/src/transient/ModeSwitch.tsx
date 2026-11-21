import { useSyncExternalStore } from 'react'
import * as store from '../projectStore'
import { isLiquidModeActive, subscribeLiquidMode } from '../liquid/liquidModeState'
import { useTranslation } from '../useTranslation'
import { domainModesOf, type CoolingDomain, type DomainMode } from './model'
import { setSimulationMode } from './simulationMode'

const MODES: DomainMode[] = ['steady', 'transient']

/** A flat line (steady) or a step over time (transient), 14 px, currentColor. */
function ModeIcon({ mode }: { mode: DomainMode }) {
  return (
    <svg className="mode-switch-icon" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">
      {mode === 'steady'
        ? <path d="M1 7h12" />
        : <path d="M1 10h4V4h4v6h4" />}
    </svg>
  )
}

/**
 * 稳态 / 瞬态 for the cooling domain on screen (风冷 or 液冷), in the top bar
 * beside the renderer switch (E8b: each domain has its own mode, one writer).
 * Two segments, one click; a badge names the other domain when it runs the
 * other way, so a hybrid (air steady, liquid transient) is visible from either view.
 */
export function ModeSwitch() {
  const { t } = useTranslation()
  const project = useSyncExternalStore(store.subscribe, store.getProject)
  const liquid = useSyncExternalStore(subscribeLiquidMode, isLiquidModeActive)
  const domain: CoolingDomain = liquid ? 'liquid' : 'air'
  const other: CoolingDomain = liquid ? 'air' : 'liquid'
  const modes = domainModesOf(project)
  const current = modes[domain]
  const domainLabel = t(domain === 'air' ? 'transient.domain.air' : 'transient.domain.liquid')
  return (
    <div
      id="transient-mode-selector"
      className={`mode-switch mode-${current}`}
      role="radiogroup"
      aria-label={t('transient.mode.aria', { domain: domainLabel })}
      title={t('transient.mode.title')}
      data-domain={domain}
    >
      <span className="mode-switch-domain">{domainLabel}</span>
      {MODES.map((mode) => (
        <button
          key={mode}
          type="button"
          role="radio"
          aria-checked={current === mode}
          className={`mode-switch-seg${current === mode ? ' on' : ''}`}
          data-mode={mode}
          disabled={!project}
          onClick={() => { if (current !== mode) setSimulationMode(mode, domain) }}
        >
          <ModeIcon mode={mode} />
          {t(mode === 'steady' ? 'transient.mode.steady' : 'transient.mode.transient')}
        </button>
      ))}
      {project && modes[other] !== current && (
        <span className="mode-switch-other" data-other={other}>
          {t('transient.mode.other', {
            domain: t(other === 'air' ? 'transient.domain.air' : 'transient.domain.liquid'),
            mode: t(modes[other] === 'steady' ? 'transient.mode.steady' : 'transient.mode.transient'),
          })}
        </span>
      )}
    </div>
  )
}
