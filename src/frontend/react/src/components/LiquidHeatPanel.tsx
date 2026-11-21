import { useMemo } from 'react'
import { liquidHeatSeries, liquidHeatSummary, type HxFigure } from '../liquid/liquidNetwork'
import { fmtCelsius, fmtFlow, fmtNumber, fmtSci, fmtWatts } from '../liquid/liquidDisplayFormat'
import type { TranslationKey } from '../i18n'
import { useTranslation } from '../useTranslation'
import { edgeName } from './LiquidNetworkTable'
import { TimeSeriesChart } from './TimeSeriesChart'
import './LiquidPostprocess.css'

/**
 * Heat and heat exchangers of a SAVED result, at the selected sample.
 *
 * The runtime monitor on the solver tab shows the same quantities, but only
 * for the run this browser is driving; this reads them from the result
 * document the post-processing tab loaded from disk, so an old run, a run
 * finished in another window or one restored after a restart reads the same.
 */

function hxModelKey(model: string | null): TranslationKey | null {
  if (model === 'counterflow-ntu') return 'lq_pp.hx_model_counterflow'
  if (model === 'mixed-volumes') return 'lq_pp.hx_model_mixed'
  if (model === null) return 'lq_pp.hx_model_unrecorded'
  return null
}

/** Readable names of the law ids the solver records (newton_hydraulic.py) and the pre-record ones. */
const LAW_KEYS: Record<string, TranslationKey> = {
  'affinity_N2H0_minus_aQabsQ/v3': 'lq_pp.law_pump_v3',
  'affinity_N2H0_minus_aQ2/v2': 'lq_pp.law_pump_v2',
  'affinity_N2_times_H0_minus_aQ2/v1': 'lq_pp.law_pump_v1',
  'laminar64_swamee_jain_blend_logcap0p5/v2': 'lq_pp.law_friction_v2',
  'laminar64_swamee_jain_blend_uncapped/v1': 'lq_pp.law_friction_v1',
  'all_edges_rho_g_dh/v2': 'lq_pp.law_hydro_v2',
  'pipes_only_rho_g_dh/v1': 'lq_pp.law_hydro_v1',
}

export function LiquidHeatPanel({ raw, sample, transient }: { raw: unknown; sample: number; transient: boolean }) {
  const { t } = useTranslation()
  const heat = useMemo(() => liquidHeatSummary(raw, sample), [raw, sample])
  const series = useMemo(() => (transient ? liquidHeatSeries(raw) : null), [raw, transient])
  if (!heat) return null
  const d = heat.diagnostics
  const rho = (raw as { fluid?: { rho_kg_m3?: number } })?.fluid?.rho_kg_m3
  const lpm = (kg: number | null) => (kg === null || typeof rho !== 'number' || !(rho > 0) ? null : kg / rho * 60000)
  const runId = (raw as { run_id?: unknown })?.run_id
  const closedValves = heat.valveOpenings.filter((v) => v.opening !== 1)
  const valveName = (edgeId: string) => edgeName(raw, edgeId, t)
  const lawText = (id: string | null, key: string) => {
    if (id === null) return '—'
    const name = LAW_KEYS[id] ? t(LAW_KEYS[id]) : id
    return heat.laws?.inferred.includes(key) ? t('lq_pp.law_inferred', { law: name }) : name
  }
  return (
    <div className="lq-pp-section" id="lq-pp-heat">
      <p className="lq-pp-note" data-heat-source>{t('lq_pp.heat_source', { run: typeof runId === 'string' ? runId.slice(0, 8) : '—', time: heat.timeSeconds.toFixed(1) })}</p>
      <div className="lq-pp-section-title">{t('lq_pp.heat_title')}</div>
      <dl className="lq-pp-cards">
        <Card id="heat-input" label={t('lq_pp.heat_input')} value={fmtWatts(d.heatInputW)} />
        <Card id="heat-rejection" label={t('lq_pp.heat_rejection')} value={fmtWatts(d.primaryRejectionW)} />
        <Card id="storage-rate" label={t('lq_pp.storage_rate')} value={fmtWatts(d.storageRateW)} />
        <Card id="boundary-exchange" label={t('lq_pp.boundary_exchange')} value={fmtWatts(d.boundaryExchangeW)} />
        <Card id="energy-balance" label={t('lq_pp.energy_balance')} value={fmtSci(d.energyBalanceW, 'W')} />
        <Card id="mass-residual" label={t('lq_pp.mass_residual')} value={fmtSci(d.massResidualKgS, 'kg/s')} />
      </dl>
      <p className="lq-pp-note">{t('lq_pp.heat_balance_rule')}</p>
      {series && <TimeSeriesChart id="lq-pp-heat-chart" time={series.time} height={160}
        xLabel={t('liquid.axis_time')} yLabel="kW" series={[
          { label: t('lq_pp.heat_input'), color: '#e7a967', points: series.input.map((w) => w / 1000) },
          { label: t('lq_pp.heat_rejection'), color: '#65b7df', points: series.rejection.map((w) => w / 1000) },
          { label: t('lq_pp.storage_rate'), color: '#8fc8a5', points: series.storage.map((w) => w / 1000) },
        ]} />}
      {heat.hx.map((hx) => <HxCard key={hx.edgeId} raw={raw} hx={hx} lpm={lpm} />)}
      {heat.laws && <>
        <div className="lq-pp-section-title">{t('lq_pp.laws_title')}</div>
        <dl className="lq-pp-cards">
          <Card id="law-pump" wide label={t('lq_pp.pump_law')} value={lawText(heat.laws.pump, 'pump_law')} />
          <Card id="law-friction" wide label={t('lq_pp.friction_law')} value={lawText(heat.laws.friction, 'friction_law')} />
          <Card id="law-hydrostatics" wide label={t('lq_pp.hydrostatics')} value={lawText(heat.laws.hydrostatics, 'hydrostatics')} />
        </dl>
      </>}
      <div className="lq-pp-section-title">{t('lq_pp.controls_title')}</div>
      <dl className="lq-pp-cards">
        <Card id="heat-multiplier" label={t('lq_pp.heat_multiplier')} value={fmtNumber(heat.heatMultiplier, 3)} />
        {heat.pumpSpeeds.map((p) => (
          <Card key={p.edgeId} id={`pump-${p.edgeId}`} label={t('lq_pp.pump_speed', { name: edgeName(raw, p.edgeId, t) })} value={fmtNumber(p.ratio, 3)} />
        ))}
        {heat.valveOpenings.length > 0 && (
          <div className="lq-pp-card wide">
            <dt>{t('lq_pp.valve_openings')}</dt>
            <dd data-heat-value="valves">
              {closedValves.length === 0
                ? t('lq_pp.valves_all_open', { count: heat.valveOpenings.length })
                : <>
                  {t('lq_pp.valves_partly_open', { count: heat.valveOpenings.length - closedValves.length })}
                  {closedValves.map((v) => <span key={v.edgeId} className="lq-pp-sub" data-valve={v.edgeId}>{valveName(v.edgeId)}: {v.opening.toFixed(3)}</span>)}
                </>}
            </dd>
          </div>
        )}
      </dl>
    </div>
  )
}

function HxCard({ raw, hx, lpm }: { raw: unknown; hx: HxFigure; lpm: (kg: number | null) => number | null }) {
  const { t } = useTranslation()
  const modelKey = hxModelKey(hx.model)
  const modelName = modelKey ? t(modelKey) : String(hx.model)
  return (
    <section className="lq-pp-section" data-hx={hx.edgeId}>
      <div className="lq-pp-section-title">{t('lq_pp.hx_title', { name: hx.ownerLabel ?? edgeName(raw, hx.edgeId, t) })}</div>
      <dl className="lq-pp-cards">
        <Card id={`hx-model-${hx.edgeId}`} wide label={t('lq_pp.hx_model')}
          value={hx.modelInferred && hx.model !== null ? t('lq_pp.law_inferred', { law: modelName }) : modelName} />
        <Card id={`hx-primary-inlet-${hx.edgeId}`} label={t('lq_pp.primary_inlet')} value={fmtCelsius(hx.primaryInletK)} />
        <Card id={`hx-primary-return-${hx.edgeId}`} label={t('lq_pp.primary_return')} value={fmtCelsius(hx.primaryReturnK)} />
        <Card id={`hx-secondary-inlet-${hx.edgeId}`} label={t('lq_pp.secondary_inlet')} value={fmtCelsius(hx.secondaryInletK)} />
        <Card id={`hx-secondary-supply-${hx.edgeId}`} label={t('lq_pp.secondary_supply')} value={fmtCelsius(hx.secondarySupplyK)} />
        <Card id={`hx-primary-flow-${hx.edgeId}`} wide label={t('lq_pp.primary_flow')} value={fmtFlow(hx.primaryFlowLpm, hx.primaryFlowKgS)} />
        <Card id={`hx-secondary-flow-${hx.edgeId}`} wide label={t('lq_pp.secondary_flow')} value={fmtFlow(lpm(hx.secondaryFlowKgS), hx.secondaryFlowKgS)} />
      </dl>
    </section>
  )
}

function Card({ id, label, value, wide }: { id: string; label: string; value: string; wide?: boolean }) {
  return (
    <div className={`lq-pp-card${wide ? ' wide' : ''}`}>
      <dt>{label}</dt>
      <dd data-heat-value={id}>{value}</dd>
    </div>
  )
}
