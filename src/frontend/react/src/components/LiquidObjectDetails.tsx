import { useMemo, useState } from 'react'
import {
  focusEdgeIdsFor,
  liquidHeatSummary,
  liquidNetworkMetrics,
  liquidNetworkTable,
  objectSeries,
  rowInactiveReason,
  type NetworkRow,
  type SeriesTarget,
} from '../liquid/liquidNetwork'
import {
  DASH,
  EDGE_TYPE_KEY,
  fmtCelsius,
  fmtFlow,
  fmtKPa,
  fmtNumber,
  fmtVelocity,
  fmtWatts,
  rowName,
} from '../liquid/liquidDisplayFormat'
import { setFocusedLiquidEdge } from '../liquid/liquidPostprocessFocus'
import type { TreeSelection } from '../interaction/treeNodes'
import { useTranslation } from '../useTranslation'
import { LiquidResultCharts } from './LiquidResultCharts'
import { NetworkDialog, nodeName } from './LiquidNetworkTable'
import './LiquidPostprocess.css'

/**
 * Every value of the selected object at the shown sample, with the way into
 * its time curves.
 *
 * The object is the app's selection (a model, a server in a rack, a valve on a
 * pipe) narrowed by the edge the user picked in the network table, when that
 * edge belongs to it: a pipe-segment model owns many edges, and the row the
 * user clicked is the one they want to read.
 */

export interface LiquidObjectDetailsProps {
  raw: unknown
  sample: number
  transient: boolean
  selection: { uid: string | null; node: TreeSelection | null }
  focusedEdge: string | null
}

export function LiquidObjectDetails({ raw, sample, transient, selection, focusedEdge }: LiquidObjectDetailsProps) {
  const { t } = useTranslation()
  const [chart, setChart] = useState<SeriesTarget | null>(null)
  const table = useMemo(() => liquidNetworkTable(raw, sample), [raw, sample])
  const selectedEdges = useMemo(
    () => (selection.uid ? focusEdgeIdsFor(raw, [selection.uid], selection.node) : []),
    [raw, selection.uid, selection.node],
  )
  if (!table) return null
  const bindings = (raw as { object_bindings?: Record<string, { label?: string; kind?: string }> })?.object_bindings ?? {}
  const node = selection.node
  const subKey = selection.uid && node && (node.type === 'equipment' || node.type === 'serverFace' || node.type === 'serverPatch') && node.equipmentId
    ? `${selection.uid}/${node.equipmentId}`
    : selection.uid && node?.type === 'fitting' && node.fittingId ? `${selection.uid}/${node.fittingId}` : null
  const objectKey = subKey && bindings[subKey] ? subKey : selection.uid
  const binding = objectKey ? bindings[objectKey] : undefined
  const edgeRow = (id: string | null) => (id ? table.rows.find((r) => r.edgeId === id) ?? null : null)
  // The picked edge wins while it belongs to the selection (or nothing is selected).
  const focusRow = focusedEdge && (selectedEdges.length === 0 || selectedEdges.includes(focusedEdge)) ? edgeRow(focusedEdge) : null
  const rows = selectedEdges.map(edgeRow).filter((r): r is NetworkRow => r !== null)

  let body: React.ReactNode
  let title = ''
  let target: SeriesTarget | null = null
  if (focusRow && (rows.length !== 1 || binding?.kind === 'valve' || !binding)) {
    title = rowName(focusRow, t)
    target = { kind: 'edge', edgeId: focusRow.edgeId }
    body = <EdgeValues raw={raw} row={focusRow} sample={table.sample} />
  } else if (binding?.kind === 'server' && objectKey) {
    title = binding.label ?? objectKey
    target = { kind: 'server', key: objectKey }
    body = <ServerValues raw={raw} serverKey={objectKey} sample={table.sample} rows={rows} activeEdge={focusedEdge} />
  } else if (binding?.kind === 'cdu' && objectKey) {
    title = binding.label ?? objectKey
    target = { kind: 'cdu', key: objectKey }
    body = <CduValues raw={raw} cduKey={objectKey} sample={table.sample} rows={rows} activeEdge={focusedEdge} />
  } else if (rows.length === 1) {
    title = rowName(rows[0]!, t)
    target = { kind: 'edge', edgeId: rows[0]!.edgeId }
    body = <EdgeValues raw={raw} row={rows[0]!} sample={table.sample} />
  } else if (rows.length > 1) {
    title = binding?.label ?? selection.uid ?? ''
    target = { kind: 'edge', edgeId: (focusRow ?? rows[0]!).edgeId }
    body = <EdgeList rows={rows} activeEdge={focusedEdge} />
  }

  return (
    <section className="lq-pp-details" id="lq-pp-details" aria-label={t('lq_pp.details_title')}>
      <div className="prop-title" style={{ color: '#00b4dc' }}>{t('lq_pp.details_title')}</div>
      {!selection.uid && !focusRow ? <p className="lq-pp-note">{t('lq_pp.details_empty')}</p>
        : !body ? <p className="lq-pp-note" data-details-missing>{t('lq_pp.details_not_in_result')}</p>
          : <>
            <h4 data-details-title>{title}</h4>
            {body}
            {target && <div className="lq-pp-actions">
              <button type="button" className="btn" data-details-chart onClick={() => setChart(target)}>
                {transient ? t('lq_pp.open_chart') : t('lq_pp.open_values')}
              </button>
            </div>}
          </>}
      {chart && <NetworkDialog id="lq-pp-details-chart" title={title} onClose={() => setChart(null)}>
        <LiquidResultCharts raw={raw} sample={table.sample} transient={transient} target={chart} />
      </NetworkDialog>}
    </section>
  )
}

function Row({ id, label, value }: { id: string; label: string; value: string }) {
  return <><dt>{label}</dt><dd data-detail={id}>{value}</dd></>
}

function EdgeValues({ raw, row, sample }: { raw: unknown; row: NetworkRow; sample: number }) {
  const { t } = useTranslation()
  const heat = liquidHeatSummary(raw, sample)
  const opening = heat?.valveOpenings.find((v) => v.edgeId === row.edgeId)?.opening
  const speed = heat?.pumpSpeeds.find((p) => p.edgeId === row.edgeId)?.ratio
  // A cold plate's own result is its chip temperature (cp_details), beside the coolant's.
  const chipK = row.type === 'cold_plate'
    ? (raw as { cp_details?: { edge_id: string; chip_temp_K?: number[] }[] })?.cp_details
      ?.find((c) => c.edge_id === row.edgeId)?.chip_temp_K?.[sample]
    : undefined
  const reason = rowInactiveReason(row)
  return (
    <>
      {reason && <p className="lq-pp-warn" data-detail="inactive">
        {reason === 'stagnant' ? t('lq_pp.stagnant_reason')
          : t('lq_pp.unobservable_reason', { nodes: row.unobservable.map((n) => nodeName(raw, n, t)).join(', ') })}
      </p>}
      <dl>
        <Row id="type" label={t('lq_pp.detail_type')} value={t(EDGE_TYPE_KEY[row.type])} />
        <Row id="actual" label={t('lq_pp.detail_actual')} value={row.upstream === null ? DASH
          : `${nodeName(raw, row.upstream, t)} → ${nodeName(raw, row.downstream, t)}${row.reversed ? ` (${t('lq_pp.reversed')})` : ''}`} />
        <Row id="modelled" label={t('lq_pp.detail_modelled')} value={row.from === null ? DASH : `${nodeName(raw, row.from, t)} → ${nodeName(raw, row.to, t)}`} />
        <Row id="signed-flow" label={t('lq_pp.detail_signed_flow')} value={row.flowKgS === null ? DASH : `${row.flowKgS.toPrecision(6)} kg/s`} />
        <Row id="flow" label={t('lq_pp.detail_flow')} value={fmtFlow(row.volumeFlowLpm, row.massFlowKgS, 3)} />
        <Row id="velocity" label={t('lq_pp.detail_velocity')} value={fmtVelocity(row.velocityMS)} />
        <Row id="diameter" label={t('lq_pp.detail_diameter')} value={row.diameterM === null ? DASH : `${(row.diameterM * 1000).toFixed(1)} mm`} />
        <Row id="p-up" label={t('lq_pp.detail_upstream_p')} value={fmtKPa(row.upstreamPressurePa, 3)} />
        <Row id="p-down" label={t('lq_pp.detail_downstream_p')} value={fmtKPa(row.downstreamPressurePa, 3)} />
        <Row id="drop" label={t('lq_pp.detail_static_drop')} value={fmtKPa(row.staticDropPa, 3)} />
        <Row id="loss" label={t('lq_pp.detail_loss')} value={fmtKPa(row.lossPa, 3)} />
        <Row id="inlet" label={t('lq_pp.detail_inlet_t')} value={fmtCelsius(row.inletK)} />
        <Row id="outlet" label={t('lq_pp.detail_outlet_t')} value={fmtCelsius(row.outletK)} />
        {typeof chipK === 'number' && <Row id="chip" label={t('lq_pp.detail_chip')} value={fmtCelsius(chipK)} />}
        {typeof opening === 'number' && <Row id="opening" label={t('lq_pp.detail_opening')} value={opening.toFixed(3)} />}
        {typeof speed === 'number' && <Row id="speed" label={t('lq_pp.detail_pump_speed')} value={speed.toFixed(3)} />}
        <Row id="edge-id" label={t('lq_pp.detail_edge_id')} value={row.edgeId} />
      </dl>
    </>
  )
}

function EdgeList({ rows, activeEdge }: { rows: NetworkRow[]; activeEdge: string | null }) {
  const { t } = useTranslation()
  return (
    <>
      <div className="lq-pp-note">{t('lq_pp.details_edges', { count: rows.length })}</div>
      <ul data-detail="edges">
        {rows.map((row) => (
          <li key={row.edgeId}>
            <button type="button" data-edge-id={row.edgeId} aria-current={activeEdge === row.edgeId || undefined}
              onClick={() => setFocusedLiquidEdge(row.edgeId)} title={row.edgeId}>
              <span>{rowName(row, t)}</span>
              <span>{fmtNumber(row.volumeFlowLpm, 3)} L/min</span>
            </button>
          </li>
        ))}
      </ul>
    </>
  )
}

function ServerValues({ raw, serverKey, sample, rows, activeEdge }: { raw: unknown; serverKey: string; sample: number; rows: NetworkRow[]; activeEdge: string | null }) {
  const { t } = useTranslation()
  const [rackId, serverId] = [serverKey.slice(0, serverKey.indexOf('/')), serverKey.slice(serverKey.indexOf('/') + 1)]
  const racks = (raw as { rack_summaries?: { rack_id: string; servers: { server_id: string; flow_kg_s: number[]; heat_removed_W: number[] }[] }[] })?.rack_summaries ?? []
  const server = racks.find((r) => r.rack_id === rackId)?.servers.find((s) => s.server_id === serverId)
  const temps = objectSeries(raw, { kind: 'server', key: serverKey }, 'temperature')
  const flows = objectSeries(raw, { kind: 'server', key: serverKey }, 'flow')
  const at = (id: string) => temps?.series.find((x) => x.id === id)?.points[sample]
  const flow = flows?.series[0]
  return (
    <>
      <dl>
        <Row id="flow" label={t('lq_pp.detail_flow')} value={fmtFlow(flow?.points[sample], flow?.kgS?.[sample], 3)} />
        <Row id="chip" label={t('lq_pp.detail_chip')} value={fmtC(at('chip'))} />
        <Row id="inlet" label={t('lq_pp.detail_inlet_t')} value={fmtC(at('inlet'))} />
        <Row id="outlet" label={t('lq_pp.detail_outlet_t')} value={fmtC(at('outlet'))} />
        <Row id="heat" label={t('lq_pp.detail_heat_removed')} value={fmtWatts(server?.heat_removed_W[sample])} />
      </dl>
      <EdgeList rows={rows} activeEdge={activeEdge} />
    </>
  )
}

function CduValues({ raw, cduKey, sample, rows, activeEdge }: { raw: unknown; cduKey: string; sample: number; rows: NetworkRow[]; activeEdge: string | null }) {
  const { t } = useTranslation()
  const metrics = liquidNetworkMetrics(raw, sample)
  const loop = metrics?.loops.find((l) => l.cduUid === cduKey)
  const pumps = metrics?.pumps.filter((p) => rows.some((r) => r.edgeId === p.edgeId)) ?? []
  const pressures = objectSeries(raw, { kind: 'cdu', key: cduKey }, 'pressure')
  const p = (id: string) => pressures?.series.find((x) => x.id === id)?.points[sample]
  return (
    <>
      <dl>
        <Row id="supply" label={t('lq_pp.series_supply')} value={fmtCelsius(loop?.supplyK)} />
        <Row id="return" label={t('lq_pp.series_return')} value={fmtCelsius(loop?.returnK)} />
        {pumps.map((pump) => <Row key={pump.edgeId} id={`pump-${pump.edgeId}`} label={t('lq_pp.series_pump_flow')}
          value={`${fmtFlow(pump.volumeFlowLpm, pump.massFlowKgS, 3)} · ${fmtKPa(pump.headPa)}`} />)}
        <Row id="p-supply" label={t('lq_pp.series_p_supply')} value={fmtKpaValue(p('p_supply'))} />
        <Row id="p-return" label={t('lq_pp.series_p_return')} value={fmtKpaValue(p('p_return'))} />
        <Row id="p-discharge" label={t('lq_pp.series_p_discharge')} value={fmtKpaValue(p('p_discharge'))} />
      </dl>
      <p className="lq-pp-note">{t('lq_pp.pressure_note')}</p>
      <EdgeList rows={rows} activeEdge={activeEdge} />
    </>
  )
}

const fmtC = (c: number | undefined) => (typeof c === 'number' && Number.isFinite(c) ? `${c.toFixed(2)} °C` : DASH)
const fmtKpaValue = (kpa: number | undefined) => (typeof kpa === 'number' && Number.isFinite(kpa) ? `${kpa.toFixed(3)} kPa` : DASH)
