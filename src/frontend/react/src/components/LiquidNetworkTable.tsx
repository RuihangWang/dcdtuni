import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  NETWORK_EDGE_TYPES,
  filterNetworkRows,
  liquidNetworkMetrics,
  liquidNetworkTable,
  resultEdgeName,
  resultNodeName,
  rowInactiveReason,
  sortNetworkRows,
  type NetworkEdgeType,
  type NetworkRow,
  type NetworkSortKey,
} from '../liquid/liquidNetwork'
import {
  DASH,
  EDGE_TYPE_KEY,
  edgePartText,
  fmtCelsius,
  fmtKPa,
  fmtLpm,
  fmtKgS,
  fmtNumber,
  fmtVelocity,
  joinName,
  nodePartText,
  rowName,
} from '../liquid/liquidDisplayFormat'
import { KELVIN_OFFSET } from '../liquid/liquidResults'
import { useTranslation } from '../useTranslation'
import './LiquidPostprocess.css'

/**
 * The network section of the liquid post-processing panel: the key figures
 * on top, then every solved edge — pipe, valve, pump, heat exchanger, cold
 * plate — with its ACTUAL flow direction (upstream node → downstream node by
 * the sign of the solved flow, not the modelled direction), mass and volume
 * flow, velocity, pressure drop and inlet/outlet temperature.
 *
 * Stagnant edges and edges touching a node with no defined temperature stay in
 * the list, greyed, with the reason on the row. A row click selects the object
 * the edge belongs to; rows of the selected object are lit, and an outside
 * selection scrolls its first row into view.
 */

type Translate = ReturnType<typeof useTranslation>['t']

export interface LiquidNetworkTableProps {
  raw: unknown
  sample: number
  focusEdgeIds?: readonly string[]
  activeEdgeId?: string | null
  onPickRow?: (row: NetworkRow) => void
  wide?: boolean
}

export function nodeName(raw: unknown, node: string | null, t: Translate): string {
  if (node === null) return DASH
  const name = resultNodeName(raw, node)
  return joinName(name.ownerLabel, nodePartText(name.part, t), node)
}

export function edgeName(raw: unknown, edgeId: string, t: Translate): string {
  const name = resultEdgeName(raw, edgeId)
  return joinName(name.ownerLabel, name.part ? edgePartText(name.part, t) : '', edgeId)
}

function inactiveTitle(row: NetworkRow, raw: unknown, t: Translate): string | undefined {
  const reason = rowInactiveReason(row)
  if (reason === 'stagnant') return t('lq_pp.stagnant_reason')
  if (reason === 'unobservable') return t('lq_pp.unobservable_reason', { nodes: row.unobservable.map((n) => nodeName(raw, n, t)).join(', ') })
  return undefined
}

const celsius = (k: number | null) => (k === null ? DASH : (k - KELVIN_OFFSET).toFixed(2))

/** Ids like `spoke_ring_return_tee_m_1` may wrap after an underscore, not mid-word. */
function breakable(text: string) {
  const parts = text.split('_')
  return parts.map((part, i) => <span key={i}>{part}{i < parts.length - 1 && <>_<wbr /></>}</span>)
}

export function LiquidNetworkTable({ raw, sample, focusEdgeIds, activeEdgeId, onPickRow, wide = false }: LiquidNetworkTableProps) {
  const { t } = useTranslation()
  const [type, setType] = useState<NetworkEdgeType | 'all'>('all')
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<{ key: NetworkSortKey; dir: 'asc' | 'desc' } | null>(null)
  const [expanded, setExpanded] = useState(false)
  const table = useMemo(() => liquidNetworkTable(raw, sample), [raw, sample])
  const nameOf = (row: NetworkRow) => rowName(row, t)
  const rows = useMemo(() => {
    if (!table) return []
    const filtered = filterNetworkRows(table.rows, type === 'all' ? null : new Set([type]), query, nameOf,
      (node) => nodeName(raw, node, t))
    return sort ? sortNetworkRows(filtered, sort.key, sort.dir, nameOf) : filtered
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, type, query, sort, raw, t])
  const focus = useMemo(() => new Set(focusEdgeIds ?? []), [focusEdgeIds])
  const bodyRef = useRef<HTMLTableSectionElement>(null)
  const picked = useRef<string | null>(null)
  const focusKey = [...focus].join('|')

  // An outside selection (the 3D view, the right column) scrolls its first row
  // into view; a selection this table made itself does not move the list.
  useEffect(() => {
    if (!focus.size) return
    const self = picked.current
    picked.current = null
    if (self && focus.has(self)) return
    const first = [...(bodyRef.current?.querySelectorAll<HTMLElement>('tr[data-edge-id]') ?? [])]
      .find((tr) => focus.has(tr.dataset.edgeId ?? ''))
    first?.scrollIntoView?.({ block: 'nearest' })
  }, [focusKey]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!table) return null
  const types = NETWORK_EDGE_TYPES.filter((ty) => table.rows.some((r) => r.type === ty))
  const header = (key: NetworkSortKey, label: string, title?: string) => (
    <button type="button" title={title ?? t('lq_pp.sort_hint')}
      onClick={() => setSort((s) => (s?.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'name' || key === 'type' ? 'asc' : 'desc' }))}>
      {label}{sort?.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
    </button>
  )
  const pick = (row: NetworkRow) => {
    picked.current = row.edgeId
    onPickRow?.(row)
  }
  const direction = (row: NetworkRow) => row.upstream === null
    ? DASH : `${nodeName(raw, row.upstream, t)} → ${nodeName(raw, row.downstream, t)}`
  const badges = (row: NetworkRow) => <>
    {row.reversed && <span className="lq-pp-badge reversed" title={t('lq_pp.reversed')}>⇄</span>}
    {row.stagnant && <span className="lq-pp-badge stagnant">{t('lq_pp.stagnant')}</span>}
    {!row.stagnant && row.unobservable.length > 0 && <span className="lq-pp-badge unobservable">{t('lq_pp.unobservable')}</span>}
  </>

  return (
    <div className="lq-pp-section" data-network-table={wide ? 'wide' : 'narrow'}>
      <div className="lq-pp-toolbar">
        <select aria-label={t('lq_pp.filter_type')} value={type} onChange={(e) => setType(e.target.value as NetworkEdgeType | 'all')}>
          <option value="all">{t('lq_pp.type_all')}</option>
          {types.map((ty) => <option key={ty} value={ty}>{t(EDGE_TYPE_KEY[ty])}</option>)}
        </select>
        <input type="search" aria-label={t('lq_pp.search')} placeholder={t('lq_pp.search')} value={query} onChange={(e) => setQuery(e.target.value)} />
        {!wide && <button type="button" className="btn" onClick={() => setExpanded(true)}>{t('lq_pp.expand')}</button>}
      </div>
      <div className="lq-pp-count" data-network-count>{t('lq_pp.rows_shown', { shown: rows.length, total: table.rows.length })}</div>
      {!table.hasTopology && <p className="lq-pp-warn" data-network-no-topology>{t('lq_pp.no_topology')}</p>}
      <div className="lq-pp-table-wrap">
        <table className={`lq-pp-table ${wide ? 'wide' : 'narrow'}`}>
          {wide ? (
            <colgroup><col className="c-name" /><col className="c-type" /><col className="c-dir" /><col /><col /><col /><col /><col /><col /><col /></colgroup>
          ) : (
            <colgroup><col className="c-name" /><col className="c-flow" /><col className="c-drop" /><col className="c-temp" /></colgroup>
          )}
          <thead>
            {wide ? (
              <tr>
                <th>{header('name', t('lq_pp.col_name'))}</th>
                <th>{header('type', t('lq_pp.col_type'))}</th>
                <th style={{ textAlign: 'left' }}>{t('lq_pp.col_direction')}</th>
                <th>{header('flow', t('lq_pp.col_flow_kgs'))}</th>
                <th>{header('flow', t('lq_pp.col_flow_lpm'))}</th>
                <th>{header('velocity', t('lq_pp.col_velocity'))}</th>
                <th>{header('drop', t('lq_pp.col_drop'), t('lq_pp.drop_hint'))}</th>
                <th>{header('loss', t('lq_pp.col_loss'), t('lq_pp.drop_hint'))}</th>
                <th>{header('inlet', t('lq_pp.col_inlet'))}</th>
                <th>{header('outlet', t('lq_pp.col_outlet'))}</th>
              </tr>
            ) : (
              <tr>
                <th>{header('name', `${t('lq_pp.col_name')} / ${t('lq_pp.col_direction')}`)}</th>
                <th>{header('flow', t('lq_pp.col_flow_lpm'))}<span className="lq-pp-sub">kg/s</span></th>
                <th>{header('loss', t('lq_pp.col_loss'), t('lq_pp.drop_hint'))}{header('velocity', t('lq_pp.col_velocity'))}</th>
                <th>{header('inlet', t('lq_pp.col_temps'))}</th>
              </tr>
            )}
          </thead>
          <tbody ref={bodyRef}>
            {rows.length === 0 && <tr><td colSpan={wide ? 10 : 4} className="lq-pp-note">{t('lq_pp.no_rows')}</td></tr>}
            {rows.map((row) => {
              const inactive = rowInactiveReason(row)
              const cls = [focus.has(row.edgeId) && 'lq-pp-row-focus', activeEdgeId === row.edgeId && 'lq-pp-row-active', inactive && 'lq-pp-row-inactive']
                .filter(Boolean).join(' ') || undefined
              const name = nameOf(row)
              const title = inactiveTitle(row, raw, t)
              return wide ? (
                <tr key={row.edgeId} data-edge-id={row.edgeId} data-inactive={inactive ?? undefined} className={cls} title={title} onClick={() => pick(row)}>
                  <td className="lq-pp-name"><span className="lq-pp-main" title={`${name}\n${row.edgeId}`}>{name}{badges(row)}</span></td>
                  <td>{t(EDGE_TYPE_KEY[row.type])}</td>
                  <td className="lq-pp-dir">{direction(row)}</td>
                  <td data-col="kgs">{fmtNumber(row.massFlowKgS, 5)}</td>
                  <td data-col="lpm">{fmtNumber(row.volumeFlowLpm, 3)}</td>
                  <td data-col="velocity">{fmtNumber(row.velocityMS, 4)}</td>
                  <td data-col="drop">{row.staticDropPa === null ? DASH : (row.staticDropPa / 1000).toFixed(3)}</td>
                  <td data-col="loss">{row.lossPa === null ? DASH : (row.lossPa / 1000).toFixed(3)}</td>
                  <td data-col="inlet">{celsius(row.inletK)}</td>
                  <td data-col="outlet">{celsius(row.outletK)}</td>
                </tr>
              ) : (
                <tr key={row.edgeId} data-edge-id={row.edgeId} data-inactive={inactive ?? undefined} className={cls} title={title} onClick={() => pick(row)}>
                  <td className="lq-pp-name">
                    <span className="lq-pp-main" title={`${name}\n${row.edgeId}`}>{breakable(name)}{badges(row)}</span>
                    <span className="lq-pp-sub" title={direction(row)}>{t(EDGE_TYPE_KEY[row.type])} · {direction(row)}</span>
                  </td>
                  <td data-col="lpm">{fmtNumber(row.volumeFlowLpm, 3)}<span className="lq-pp-sub" data-col="kgs">{fmtNumber(row.massFlowKgS, 5)}</span></td>
                  <td data-col="loss" title={row.staticDropPa === null ? undefined : `${t('lq_pp.col_drop')}: ${(row.staticDropPa / 1000).toFixed(3)}`}>
                    {row.lossPa === null ? DASH : (row.lossPa / 1000).toFixed(2)}<span className="lq-pp-sub" data-col="velocity">{fmtNumber(row.velocityMS, 3)}</span></td>
                  <td data-col="temps">{celsius(row.inletK)}<span className="lq-pp-sub" data-col="outlet">{celsius(row.outletK)}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="lq-pp-note">{t('lq_pp.drop_hint')}</p>
      {expanded && <NetworkDialog title={t('lq_pp.network_title', { count: table.rows.length })} onClose={() => setExpanded(false)}>
        <LiquidNetworkTable raw={raw} sample={sample} focusEdgeIds={focusEdgeIds} activeEdgeId={activeEdgeId} onPickRow={onPickRow} wide />
      </NetworkDialog>}
    </div>
  )
}

export function NetworkDialog({ title, onClose, children, id }: { title: string; onClose: () => void; children: React.ReactNode; id?: string }) {
  const { t } = useTranslation()
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    const invoker = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialog?.showModal?.()
    return () => {
      if (dialog?.open) dialog.close?.()
      if (invoker?.isConnected) invoker.focus({ preventScroll: true })
    }
  }, [])
  return createPortal(
    <dialog ref={ref} id={id} className="lq-pp-dialog" aria-label={title}
      onCancel={(e) => { e.preventDefault(); onClose() }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="lq-pp-dialog-header"><span>{title}</span>
        <button type="button" className="btn" onClick={onClose}>{t('lq_pp.close')}</button>
      </div>
      {children}
    </dialog>,
    document.body,
  )
}

/** The key figures on top of the network section. */
export function LiquidNetworkMetricCards({ raw, sample, onPickEdge }: { raw: unknown; sample: number; onPickEdge?: (edgeId: string) => void }) {
  const { t } = useTranslation()
  const m = useMemo(() => liquidNetworkMetrics(raw, sample), [raw, sample])
  if (!m) return null
  const serverName = (rackId: string, serverId: string) => {
    const label = (raw as { object_bindings?: Record<string, { label?: string }> })?.object_bindings?.[`${rackId}/${serverId}`]?.label
    return label ?? `${rackId} / ${serverId}`
  }
  return (
    <dl className="lq-pp-cards" id="lq-pp-metrics" aria-label={t('lq_pp.metrics_title')}>
      <div className="lq-pp-card" title={t('lq_pp.metric_total_flow_hint')}>
        <dt>{t('lq_pp.metric_total_flow')}</dt>
        <dd data-metric="total-flow">{fmtLpm(m.totalFlowLpm)}<span className="lq-pp-sub">{fmtKgS(m.totalFlowKgS)}</span></dd>
      </div>
      <div className="lq-pp-card">
        <dt>{t('lq_pp.metric_max_chip')}</dt>
        <dd data-metric="max-chip">{fmtCelsius(m.maxChip?.valueK)}
          {m.maxChip && <span className="lq-pp-sub">{serverName(m.maxChip.rackId, m.maxChip.serverId)}</span>}</dd>
      </div>
      {m.pumps.map((pump) => (
        <div className="lq-pp-card wide" key={pump.edgeId} title={t('lq_pp.metric_pump_hint')}>
          <dt>{t('lq_pp.metric_pump', { name: edgeName(raw, pump.edgeId, t) })}</dt>
          <dd data-metric="pump" data-edge-id={pump.edgeId}>
            {fmtLpm(pump.volumeFlowLpm)} · {fmtKgS(pump.massFlowKgS)}
            <span className="lq-pp-sub">
              {t('lq_pp.metric_pump_head', { head: fmtKPa(pump.headPa), metres: fmtNumber(pump.headM, 2) })}
              {pump.speedRatio !== null && ` · ${t('lq_pp.metric_pump_speed', { value: pump.speedRatio })}`}
            </span>
          </dd>
        </div>
      ))}
      {m.loops.map((loop) => (
        <div className="lq-pp-card wide" key={loop.cduUid}>
          <dt>{t('lq_pp.metric_supply_return', { name: loop.label })}</dt>
          <dd data-metric="supply-return">{fmtCelsius(loop.supplyK)} / {fmtCelsius(loop.returnK)}</dd>
        </div>
      ))}
      <div className="lq-pp-card">
        <dt>{t('lq_pp.metric_max_velocity')}</dt>
        <dd data-metric="max-velocity">
          {m.maxVelocity ? <button type="button" className="lr-link" onClick={() => onPickEdge?.(m.maxVelocity!.edgeId)}>{fmtVelocity(m.maxVelocity.valueMS)}</button> : DASH}
          {m.maxVelocity && <span className="lq-pp-sub">{edgeName(raw, m.maxVelocity.edgeId, t)}</span>}
        </dd>
      </div>
      <div className="lq-pp-card">
        <dt>{t('lq_pp.metric_min_branch')}</dt>
        <dd data-metric="min-branch">{fmtLpm(m.minFlowBranch?.volumeFlowLpm, 3)}
          {m.minFlowBranch && <span className="lq-pp-sub">{fmtKgS(m.minFlowBranch.massFlowKgS)} · {serverName(m.minFlowBranch.rackId, m.minFlowBranch.serverId)}</span>}</dd>
      </div>
    </dl>
  )
}

