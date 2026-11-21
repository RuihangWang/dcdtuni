import { useEffect, useState, useSyncExternalStore } from 'react'
import { ColorbarOverlay } from './ColorbarOverlay'
import * as liquid from '../liquid/liquidSession'
import * as color from '../liquid/liquidTempColor'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import type { TranslationKey } from '../i18n'
import {
  ARROW_DENSITIES, ARROW_SPEED_SCALES, arrowScales, chipScale, liquidDisplayOptions, networkScale,
  setLiquidDisplayOptions, subscribeLiquidDisplay, type LiquidScaleMode,
} from '../liquid/liquidDisplayState'
import { LIQUID_QUANTITIES, liquidQuantityFrame, type LiquidQuantity } from '../liquid/liquidDisplay'
import { liquidFlowSummary, openLiquidFlowDisplay } from '../liquid/liquidFlowDisplay'
import { startLiquidLiveFollow } from '../liquid/liquidLiveFollow'
import { appOvLiquidColouring, ovLiquidWanted, setOvLiquidWanted } from '../liquid/ovLiquidDisplay'
import { ovSession, subscribeOvSession } from '../renderer/ovSession'
import { resultFluid } from '../liquid/liquidOverview'

/**
 * The `🎨 3D Display` section of the liquid results (js/liquid_results_panel.js:129-138),
 * now with what the network is coloured by, its scale and legend, and the flow arrows.
 *
 * The checked states are held in the colouring modules, not here: the scene
 * stays painted across a tab switch, and a checkbox that reset itself on
 * remount would offer "turn on" for something already on.
 *
 * Every colour has a legend (the editor's ColorbarOverlay, beside the
 * viewport): the network in its quantity and unit, the chips, the arrows'
 * velocity. A range typed in a colour bar's dialog is kept until the quantity
 * or the scale mode changes.
 */
const QUANTITY_KEYS: Record<LiquidQuantity, TranslationKey> = {
  temperature: 'lq_3d.q_temperature', flow: 'lq_3d.q_flow', velocity: 'lq_3d.q_velocity', pressure: 'lq_3d.q_pressure',
}
const LEGEND_KEYS: Record<LiquidQuantity, TranslationKey> = {
  temperature: 'lq_3d.legend_temperature', flow: 'lq_3d.legend_flow', velocity: 'lq_3d.legend_velocity', pressure: 'lq_3d.legend_pressure',
}
/** The colour bar's field: 'T' converts to the user's temperature unit, 'Umag' labels m/s. */
const LEGEND_FIELDS: Record<LiquidQuantity, string> = { temperature: 'T', flow: 'liquidFlowLpm', velocity: 'Umag', pressure: 'liquidPressureKpa' }

const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3))

export function LiquidThreeDDisplay() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const opts = useSyncExternalStore(subscribeLiquidDisplay, liquidDisplayOptions)
  // Re-applied on mount and on every project notification, because a scene
  // rebuild — a project load, a model edit, an air/liquid switch — replaces the
  // meshes and the new ones come back in their default colours.
  useEffect(() => {
    color.reapplyResultColouring()
    return store.subscribe(color.reapplyResultColouring)
  }, [])
  useEffect(() => color.installResultRepaint(), [])
  useEffect(() => liquid.subscribeLiquid(color.reapplyResultColouring), [])
  useEffect(() => openLiquidFlowDisplay(), [])
  useEffect(() => startLiquidLiveFollow(), [])
  useEffect(() => color.subscribeResultSample(() => bump(n => n + 1)), [])

  const raw = liquid.liquidRunState().results as Record<string, unknown> | null
  const sample = color.selectedResultSample()
  const pipesOn = color.isPipeColoringOn()
  const chipsOn = color.isChipColoringOn()
  const bound = !!raw?.object_bindings && Array.isArray(raw?.cell_edge_ids)
  const scale = raw && bound ? networkScale(raw, sample, opts) : null
  const chips = raw && bound ? chipScale(raw, sample, opts) : null
  const arrows = raw && bound ? arrowScales(raw, sample, opts) : null
  const summary = liquidFlowSummary()
  const rho = resultFluid(raw).rho_kg_m3
  const range = (): string => {
    if (!scale?.domain) return ''
    const [lo, hi] = scale.domain
    if (scale.quantity === 'temperature') return t('lq_3d.range_temperature', { lo: (lo - 273.15).toFixed(2), hi: (hi - 273.15).toFixed(2) })
    if (scale.quantity === 'flow') {
      return t('lq_3d.range_flow', { lo: fmt(lo), hi: fmt(hi), kglo: fmt(lo * rho / 60000), kghi: fmt(hi * rho / 60000) })
    }
    if (scale.quantity === 'velocity') return t('lq_3d.range_velocity', { lo: fmt(lo), hi: fmt(hi) })
    return t('lq_3d.range_pressure', { lo: fmt(lo), hi: fmt(hi) })
  }
  const toggle = (fn: (on: boolean) => boolean, on: boolean) => { fn(on); bump(n => n + 1) }

  return (
    <div className="prop-group" id="lc-3d-display" style={{ borderLeftColor: '#00b4dc' }}>
      <div className="prop-title" style={{ color: '#00b4dc' }}>{t('lrd.display_3d')}</div>

      <div className="prop-row">
        <label htmlFor="lc-color-pipes">{t('lq_3d.network_colour')}</label>
        <input type="checkbox" id="lc-color-pipes" checked={pipesOn}
          onChange={e => toggle(color.setPipeTempColoring, e.target.checked)} />
      </div>
      {bound && <>
        <div className="prop-row">
          <label htmlFor="lc-quantity">{t('lq_3d.quantity')}</label>
          <select id="lc-quantity" value={opts.quantity}
            onChange={e => setLiquidDisplayOptions({ quantity: e.target.value as LiquidQuantity })}>
            {LIQUID_QUANTITIES.map(q => <option key={q} value={q}>{t(QUANTITY_KEYS[q])}</option>)}
          </select>
        </div>
        <div className="prop-row">
          <label htmlFor="lc-scale">{t('lq_3d.scale')}</label>
          <select id="lc-scale" value={opts.scale}
            onChange={e => setLiquidDisplayOptions({ scale: e.target.value as LiquidScaleMode })}>
            <option value="global">{t('lq_3d.scale_global')}</option>
            <option value="frame">{t('lq_3d.scale_frame')}</option>
          </select>
        </div>
        {scale?.domain && <div className="prop-hint" id="lc-scale-range" data-quantity={scale.quantity}
          data-lo={scale.domain[0]} data-hi={scale.domain[1]}>{range()}</div>}
        {opts.manual && <div className="prop-hint">
          {t('lq_3d.scale_manual')} · <button type="button" className="lr-link" id="lc-scale-reset"
            onClick={() => setLiquidDisplayOptions({ manual: null })}>{t('lq_3d.scale_reset')}</button>
        </div>}
        <div className="prop-hint">{t('lq_3d.along_hint')}</div>
      </>}

      <div className="prop-row">
        <label htmlFor="lc-color-servers">{t('lrd.chip_temp_color')}</label>
        <input type="checkbox" id="lc-color-servers" checked={chipsOn}
          onChange={e => toggle(color.setChipTempColoring, e.target.checked)} />
      </div>

      {bound && <>
        <div className="prop-row">
          <label htmlFor="lc-arrows">{t('lq_3d.arrows')}</label>
          <input type="checkbox" id="lc-arrows" checked={opts.arrows}
            onChange={e => setLiquidDisplayOptions({ arrows: e.target.checked })} />
        </div>
        {opts.arrows && <>
          <div className="prop-row">
            <label htmlFor="lc-animate">{t('lq_3d.animate')}</label>
            <input type="checkbox" id="lc-animate" checked={opts.animate}
              onChange={e => setLiquidDisplayOptions({ animate: e.target.checked })} />
          </div>
          <div className="prop-row">
            <label htmlFor="lc-density">{t('lq_3d.density')}</label>
            <select id="lc-density" value={opts.density}
              onChange={e => setLiquidDisplayOptions({ density: Number(e.target.value) })}>
              {ARROW_DENSITIES.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div className="prop-row">
            <label htmlFor="lc-speed">{t('lq_3d.speed')}</label>
            <select id="lc-speed" value={opts.speedScale}
              onChange={e => setLiquidDisplayOptions({ speedScale: Number(e.target.value) })}>
              {ARROW_SPEED_SCALES.map(v => <option key={v} value={v}>{v}×</option>)}
            </select>
          </div>
          <div className="prop-row">
            <label htmlFor="lc-arrow-colour">{t('lq_3d.arrow_colour')}</label>
            <select id="lc-arrow-colour" value={opts.arrowColor}
              onChange={e => setLiquidDisplayOptions({ arrowColor: e.target.value as 'velocity' | 'plain' })}>
              <option value="velocity">{t('lq_3d.arrow_colour_velocity')}</option>
              <option value="plain">{t('lq_3d.arrow_colour_plain')}</option>
            </select>
          </div>
          <div className="prop-hint" id="lc-arrows-summary" data-arrows={summary.arrows} data-visible={summary.visible ? '1' : '0'}>
            {t('lq_3d.arrows_summary', { count: summary.arrows })}
            {!!summary.skipped?.hidden && <> · {t('lq_3d.arrows_hidden', { count: summary.skipped.hidden })}</>}
            {!!summary.skipped?.noStub && <> · {t('lq_3d.arrows_no_stub', { count: summary.skipped.noStub })}</>}
          </div>
          <div className="prop-hint">{t('lq_3d.arrows_tab_hint')}</div>
        </>}
        <div className="prop-row">
          <label htmlFor="lc-live" title={t('lq_3d.live_hint')}>{t('lq_3d.live')}</label>
          <input type="checkbox" id="lc-live" checked={opts.live} title={t('lq_3d.live_hint')}
            onChange={e => setLiquidDisplayOptions({ live: e.target.checked })} />
        </div>
      </>}

      <OvLiquidBlock bound={bound} />

      {pipesOn && scale?.domain && raw && <ColorbarOverlay id="lc-network-colorbar" rightOffset={320}
        label={t(LEGEND_KEYS[scale.quantity])} field={LEGEND_FIELDS[scale.quantity]} colorMap={scale.colorMap}
        climMin={scale.domain[0]} climMax={scale.domain[1]}
        onApply={(min, max, colorMap) => setLiquidDisplayOptions({ manual: { domain: [min, max], colorMap } })}
        onAutoScale={() => { const d = liquidQuantityFrame(raw, sample, scale.quantity)?.domain; return d ? { min: d[0], max: d[1] } : null }} />}
      {chipsOn && chips && <ColorbarOverlay id="lc-chip-colorbar" rightOffset={400}
        label={t('lq_3d.legend_chips')} field="T" colorMap="coolwarm" climMin={chips[0]} climMax={chips[1]}
        onApply={() => {}} />}
      {opts.arrows && opts.arrowColor === 'velocity' && summary.visible && arrows?.velocity && <ColorbarOverlay
        id="lc-arrow-colorbar" rightOffset={480} label={t('lq_3d.legend_arrows')} field="Umag"
        colorMap={arrows.colorMap} climMin={arrows.velocity[0]} climMax={arrows.velocity[1]}
        onApply={(min, max, colorMap) => setLiquidDisplayOptions({ arrowManual: { domain: [min, max], colorMap } })} />}
    </div>
  )
}

/** Kit colours the result itself while Omniverse owns the viewport. */
function OvLiquidBlock({ bound }: { bound: boolean }) {
  const { t } = useTranslation()
  const ov = useSyncExternalStore(subscribeOvSession, ovSession)
  const colouring = appOvLiquidColouring()
  const state = useSyncExternalStore(fn => colouring.subscribe(fn), () => colouring.state)
  const [wanted, setWanted] = useState(ovLiquidWanted)
  // The OV session's display distributor colours Kit (startOvLiquidChannel);
  // this block only shows its state and the switch.
  const live = ov.mode === 'ov' && ov.ready && !ov.degraded && !!ov.client
  if (ov.mode !== 'ov') return null
  const info = state.info, frame = state.frame
  return (
    <fieldset className="lc-ov-liquid" id="lc-ov-liquid" data-status={state.status} disabled={!live}>
      <legend>{t('lq_3d.ov_title')}</legend>
      {!bound && <p role="note">{t('ovpp.liquid_unbound')}</p>}
      <label className="liquid-experiment-check">
        <input type="checkbox" id="lc-ov-toggle" checked={wanted} disabled={!bound}
          onChange={e => {
            const next = e.target.checked
            setOvLiquidWanted(next)
            setWanted(next)
            void (next ? colouring.enable() : colouring.disable())
          }} />
        {t('lq_3d.ov_toggle')}
      </label>
      {state.status === 'loading' && <p role="status">{t('lq_3d.ov_loading')}</p>}
      {info && <p role="status" data-ov-liquid-run={info.runId}>
        {t('ovpp.liquid_loaded', { run: info.runId.slice(0, 8), status: info.status, samples: info.samples })}</p>}
      {info && !info.projectContentMatches && <p role="note">{t('ovpp.liquid_project_changed')}</p>}
      {frame && <p role="status" data-ov-liquid-painted={frame.painted}>{t('ovpp.liquid_frame', {
        time: frame.timeSeconds, painted: frame.painted,
        parts: Object.entries(frame.targets).map(([k, n]) => `${k} ${n}`).join(' · '),
      })}</p>}
      {frame && Object.keys(frame.unmatched).length > 0 && <p role="note">{t('ovpp.liquid_unmatched', {
        list: Object.entries(frame.unmatched).map(([k, n]) => `${k} ${n}`).join(' · '),
      })}</p>}
      {state.note && <p role="status">{state.note}</p>}
      {frame?.arrows
        ? <p className="liquid-experiment-note" data-ov-liquid-arrows={frame.arrows}>
          {t('lq_3d.ov_arrows_kit', { count: frame.arrows, branches: frame.arrowBranchesNotDrawn ?? 0 })}</p>
        : <p className="liquid-experiment-note">{t('lq_3d.ov_arrows_note')}</p>}
    </fieldset>
  )
}
