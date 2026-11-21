import { useEffect, useMemo, useState } from 'react'
import {
  chartCatalog,
  objectSeries,
  type ObjectSeries,
  type SeriesId,
  type SeriesQuantity,
  type SeriesTarget,
} from '../liquid/liquidNetwork'
import { DASH, fmtKgS } from '../liquid/liquidDisplayFormat'
import type { TranslationKey } from '../i18n'
import { useTranslation } from '../useTranslation'
import { TimeSeriesChart } from './TimeSeriesChart'
import { edgeName } from './LiquidNetworkTable'
import './LiquidPostprocess.css'

/**
 * Temperature / flow / pressure of one server, cold plate, pipe part or CDU —
 * the three tabs of the legacy result window (liquid_solve_monitor.js:940).
 *
 * A transient draws time curves; a steady result has one accepted sample, so
 * it shows value cards instead of a chart with a single point. Pressures are
 * the solver's absolute node pressures, which include the hydrostatic head;
 * the gauge option subtracts the loop's CDU return reference pressure.
 */

const SERIES_LABEL: Record<SeriesId, TranslationKey> = {
  chip: 'liquid.chart_chip_temperature',
  inlet: 'liquid.chart_coolant_inlet',
  outlet: 'liquid.chart_coolant_outlet',
  flow: 'lq_pp.series_flow',
  p_inlet: 'lq_pp.series_p_inlet',
  p_outlet: 'lq_pp.series_p_outlet',
  supply: 'lq_pp.series_supply',
  return: 'lq_pp.series_return',
  primary_return: 'lq_pp.series_primary_return',
  primary_inlet: 'lq_pp.series_primary_inlet',
  pump_flow: 'lq_pp.series_pump_flow',
  p_supply: 'lq_pp.series_p_supply',
  p_return: 'lq_pp.series_p_return',
  p_discharge: 'lq_pp.series_p_discharge',
}

const SERIES_COLOR: Record<SeriesId, string> = {
  chip: '#e06c75', inlet: '#61afef', outlet: '#e5c07b', flow: '#65b7df',
  p_inlet: '#61afef', p_outlet: '#e5c07b', supply: '#61afef', return: '#e06c75',
  primary_return: '#c678dd', primary_inlet: '#98c379', pump_flow: '#65b7df',
  p_supply: '#61afef', p_return: '#e06c75', p_discharge: '#c678dd',
}

type Category = 'server' | 'cold_plate' | 'pipe' | 'cdu'

function categoryOf(target: SeriesTarget | null, raw: unknown): Category {
  if (!target) return 'server'
  if (target.kind === 'server') return 'server'
  if (target.kind === 'cdu') return 'cdu'
  const plates = chartCatalog(raw)?.coldPlates ?? []
  return plates.some((p) => p.edgeId === target.edgeId) ? 'cold_plate' : 'pipe'
}

const targetKey = (target: SeriesTarget | null) =>
  !target ? '' : target.kind === 'edge' ? `edge:${target.edgeId}` : `${target.kind}:${target.key}`

export interface LiquidResultChartsProps {
  raw: unknown
  sample: number
  transient: boolean
  target: SeriesTarget | null
  /** Show the category/object pickers (the charts view); the dialogs pass a fixed target. */
  picker?: boolean
}

export function LiquidResultCharts({ raw, sample, transient, target: initial, picker = false }: LiquidResultChartsProps) {
  const { t } = useTranslation()
  const catalog = useMemo(() => chartCatalog(raw), [raw])
  const [target, setTarget] = useState<SeriesTarget | null>(initial)
  const [tab, setTab] = useState<SeriesQuantity>('temperature')
  const [gauge, setGauge] = useState(false)
  // A new focus from outside (a row, the 3D view) replaces the plotted object.
  useEffect(() => { if (initial) setTarget(initial) }, [targetKey(initial)]) // eslint-disable-line react-hooks/exhaustive-deps
  const effective = target ?? (catalog?.servers[0] ? { kind: 'server', key: catalog.servers[0].key } as SeriesTarget : null)
  const set = useMemo(() => (effective ? objectSeries(raw, effective, tab, gauge) : null), [raw, targetKey(effective), tab, gauge]) // eslint-disable-line react-hooks/exhaustive-deps
  const time = ((raw as { time_s?: number[] })?.time_s ?? [])
  const category = categoryOf(effective, raw)

  const options = (c: Category) => !catalog ? [] : c === 'server' ? catalog.servers.map((x) => ({ value: x.key, label: x.label }))
    : c === 'cdu' ? catalog.cdus.map((x) => ({ value: x.key, label: x.label }))
      // Named as the network table names them, with the part translated.
      : (c === 'cold_plate' ? catalog.coldPlates : catalog.pipes).map((x) => ({ value: x.edgeId, label: edgeName(raw, x.edgeId, t) }))
  const targetFor = (c: Category, value: string): SeriesTarget =>
    c === 'server' ? { kind: 'server', key: value } : c === 'cdu' ? { kind: 'cdu', key: value } : { kind: 'edge', edgeId: value }
  const selectedValue = !effective ? '' : effective.kind === 'edge' ? effective.edgeId : effective.key

  return (
    <div className="lq-pp-section" data-result-charts>
      {picker && catalog && <>
        <label className="lq-pp-field"><span>{t('lq_pp.charts_category')}</span>
          <select aria-label={t('lq_pp.charts_category')} value={category} onChange={(e) => {
            const c = e.target.value as Category
            const first = options(c)[0]
            if (first) setTarget(targetFor(c, first.value))
          }}>
            {(['server', 'cold_plate', 'pipe', 'cdu'] as const).filter((c) => options(c).length > 0).map((c) =>
              <option key={c} value={c}>{t(`lq_pp.category_${c}` as TranslationKey)}</option>)}
          </select>
        </label>
        <label className="lq-pp-field"><span>{t('lq_pp.charts_object')}</span>
          <select aria-label={t('lq_pp.charts_object')} value={selectedValue} onChange={(e) => setTarget(targetFor(category, e.target.value))}>
            {options(category).map((o) => <option key={o.value} value={o.value} title={o.value}>{o.label}</option>)}
          </select>
        </label>
      </>}
      {!effective && <p className="lq-pp-note">{t('lq_pp.no_objects')}</p>}
      {effective && <>
        <div className="lq-pp-tabs" role="tablist" aria-label={t('lq_pp.tabs_aria')}>
          {(['temperature', 'flow', 'pressure'] as const).map((q) => (
            <button key={q} type="button" role="tab" aria-selected={tab === q} data-chart-tab={q} onClick={() => setTab(q)}>
              {t(`lq_pp.tab_${q}` as TranslationKey)}
            </button>
          ))}
        </div>
        {tab === 'pressure' && <>
          <label className="lq-pp-check"><input type="checkbox" checked={gauge} onChange={(e) => setGauge(e.target.checked)} />
            <span>{t('lq_pp.pressure_gauge')}</span></label>
          <p className="lq-pp-note" data-pressure-note>
            {set?.gauge && set.referencePa
              ? t('lq_pp.pressure_gauge_note', { value: ((set.referencePa[Math.min(sample, set.referencePa.length - 1)] ?? NaN) / 1000).toFixed(3) })
              : gauge ? t('lq_pp.pressure_gauge_unavailable') : t('lq_pp.pressure_note')}
          </p>
        </>}
        {!set || set.series.length === 0
          ? <p className="lq-pp-note">{t('lq_pp.no_series')}</p>
          : <>
            {transient && time.length > 1 && <TimeSeriesChart id="lq-pp-object-chart" time={time} height={170}
              xLabel={t('liquid.axis_time')} yLabel={set.unit}
              series={set.series.map((s) => ({ label: t(SERIES_LABEL[s.id]), color: SERIES_COLOR[s.id], points: s.points }))} />}
            <ValueCards series={set.series} unit={set.unit} sample={sample} time={time} steady={!transient} />
          </>}
      </>}
    </div>
  )
}

function ValueCards({ series, unit, sample, time, steady }: { series: ObjectSeries[]; unit: string; sample: number; time: number[]; steady: boolean }) {
  const { t } = useTranslation()
  const s = sample < 0 ? time.length - 1 : Math.min(sample, time.length - 1)
  const digits = unit === 'L/min' ? 3 : unit === 'kPa' ? 3 : 2
  return (
    <>
      <div className="lq-pp-note">{steady ? t('lq_pp.steady_values') : t('lq_pp.values_at', { time: (time[s] ?? NaN).toFixed(1) })}</div>
      <dl className="lq-pp-cards" data-series-values>
        {series.map((x) => {
          const v = x.points[s]
          return (
            <div className="lq-pp-card" key={x.id}>
              <dt>{t(SERIES_LABEL[x.id])}</dt>
              <dd data-series-value={x.id}>{typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(digits)} ${unit}` : DASH}
                {x.kgS && <span className="lq-pp-sub">{fmtKgS(x.kgS[s])}</span>}</dd>
            </div>
          )
        })}
      </dl>
    </>
  )
}
