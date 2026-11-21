import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { Icon } from './Icon'
import * as store from '../projectStore'
import { PATCH_PHYSICS_DEFAULTS, updatePatchPhysics } from '../geometry/patchPhysics'
import { rackServersOf } from '../geometry/rackServers'
import { getRealtimeState, prepareRealtime, requestRealtime, subscribeRealtimeState } from '../postprocess/scanoRealtime'
import { fetchModelCards, sliderBounds, type TrainingRanges } from '../postprocess/scanoModels'
import {
  addSnapshot, clearSnapshots, getSnapshots, isSnapshotViewOpen, MAX_SNAPSHOTS, setSnapshotViewOpen, subscribeSnapshots,
  type SnapshotInput,
} from '../postprocess/scanoSnapshots'
import { ScanoSnapshotView } from './ScanoSnapshotView'
import { useTranslation } from '../useTranslation'

/** Real-time AI tuning of an open AI result: each released control is one undo step and one inference. */

interface AcuRow { uid: string; name: string; patchId: string; flow: number; temp: number }
interface RackRow { uid: string; name: string; servers: number; power: number }

type Bag = Record<string, unknown>

function acuRows(): AcuRow[] {
  const acus = (store.getProject()?.constructions as { acus?: Record<string, Bag> } | undefined)?.acus ?? {}
  const rows: AcuRow[] = []
  for (const [uid, acu] of Object.entries(acus)) {
    const faces = ((acu.geometry as Bag | undefined)?.faces ?? {}) as Record<string, { patches?: Bag[] }>
    for (const face of Object.values(faces)) {
      const patch = (face.patches ?? []).find((p) => (p.physics as Bag | undefined)?.role === 'supply')
      if (!patch) continue
      const physics = patch.physics as Bag
      rows.push({ uid, name: String((acu.meta as Bag | undefined)?.name ?? uid), patchId: String(patch.id),
        flow: Number(physics.volumeFlowRate ?? 4), temp: Number(physics.setPointTemperature ?? physics.temperature ?? 20) })
      break
    }
  }
  return rows
}

function rackRows(): RackRow[] {
  const racks = (store.getProject()?.constructions as { racks?: Record<string, Bag> } | undefined)?.racks ?? {}
  return Object.entries(racks).map(([uid, rack]) => {
    const servers = Object.values(rackServersOf(rack as never))
    const power = servers.reduce((sum, s) => sum + Number((s.cooling as Bag | undefined)?.power ?? 1000), 0)
    return { uid, name: String((rack.meta as Bag | undefined)?.name ?? uid), servers: servers.length, power }
  }).filter((r) => r.servers > 0)
}

/** Scale every server of a rack to a new rack total, in one edit. */
function setRackPower(uid: string, total: number): void {
  store.edit('scanoRackPower', `${uid} power=${Math.round(total)} W`, (draft) => {
    const rack = store.findConstruction(draft, uid)?.model
    if (!rack) return
    const servers = Object.values(rackServersOf(rack))
    const now = servers.reduce((sum, s) => sum + Number((s.cooling as Bag | undefined)?.power ?? 1000), 0)
    const k = now > 0 ? total / now : 0
    for (const s of servers) {
      const bag = (s.cooling ?? (s.cooling = { volumeFlowRate: 0.5, power: 1000 })) as Bag
      bag.power = Math.round(Number(bag.power ?? 1000) * k * 10) / 10
    }
  })
}

function setAcuSupply(row: AcuRow, field: 'flow' | 'temp', value: number): void {
  if (field === 'flow') {
    updatePatchPhysics(row.uid, row.patchId, 'volumeFlowRate', value, { ...PATCH_PHYSICS_DEFAULTS })
  } else {
    // Supply temperature and set point move together so fixed-supply and capacity ACUs agree.
    updatePatchPhysics(row.uid, row.patchId, 'temperature', value, { ...PATCH_PHYSICS_DEFAULTS })
    updatePatchPhysics(row.uid, row.patchId, 'setPointTemperature', value, { ...PATCH_PHYSICS_DEFAULTS })
  }
}

function Slider({ id, value, min, max, step, onCommit, unit, digits = 2 }: {
  id: string; value: number; min: number; max: number; step: number; unit: string; digits?: number
  onCommit: (v: number) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  const commit = () => { if (draft !== value) onCommit(draft) }
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
      <input id={id} type="range" min={min} max={max} step={step} value={draft} style={{ flex: 1, minWidth: 0 }}
        onChange={(e) => setDraft(Number(e.target.value))} onPointerUp={commit} onKeyUp={commit} onBlur={commit} />
      <span className="value" style={{ minWidth: 72, textAlign: 'right' }}>{draft.toFixed(digits)} {unit}</span>
    </span>
  )
}

export interface ScanoRealtimePanelProps {
  model: string
  capture?: () => SnapshotInput | null
}

export function ScanoRealtimePanel({ model, capture }: ScanoRealtimePanelProps) {
  const { t } = useTranslation()
  const rt = useSyncExternalStore(subscribeRealtimeState, getRealtimeState)
  const revision = useSyncExternalStore(store.subscribe, () => store.getProject())
  const acus = useMemo(acuRows, [revision])
  const racks = useMemo(rackRows, [revision])
  const [showRacks, setShowRacks] = useState(false)
  const snaps = useSyncExternalStore(subscribeSnapshots, getSnapshots)
  const viewOpen = useSyncExternalStore(subscribeSnapshots, isSnapshotViewOpen)

  function takeSnapshot() {
    const data = capture?.()
    if (!data) return
    const units = Object.keys(((store.getProject()?.constructions as { acus?: Record<string, unknown> } | undefined)?.acus) ?? {}).length
    const bc = [t('scano.snap.acus_running', { n: acus.length, total: units }),
      ...acus.map((a) => `${a.name}: ${a.flow.toFixed(2)} m³/s, ${a.temp.toFixed(1)} °C`)]
    const total = racks.reduce((sum, r) => sum + r.power, 0)
    bc.push(t('scano.snap.racks_total', { kw: (total / 1000).toFixed(1), n: racks.length }))
    const snap = addSnapshot(data, bc)
    store.logOperation('scano', t('scano.snap.taken', { n: snap.id }))
  }
  const [ranges, setRanges] = useState<TrainingRanges>({})
  // The first update's manifest ahead of time, once the page is idle: it exports the whole scene (seconds on a large
  // room), which during the post-processing load stacked on its busiest frames.
  useEffect(() => {
    const run = () => void prepareRealtime().catch(() => undefined)
    const w = window as Window & { requestIdleCallback?: (fn: () => void, o?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void }
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(run, { timeout: 5000 })
      return () => w.cancelIdleCallback?.(id)
    }
    const id = setTimeout(run, 2000)
    return () => clearTimeout(id)
  }, [model])
  useEffect(() => {
    let live = true
    void fetchModelCards().then((cards) => {
      if (live) setRanges(cards.find((c) => c.id === model)?.training_ranges ?? {})
    })
    return () => {
      live = false
    }
  }, [model])

  function after(edit: () => void) {
    edit()
    void requestRealtime(model)
  }

  const last = rt.last
  return (
    <div className="prop-group" id="scano-realtime">
      <div className="prop-title"><Icon name="ai" />{t('scano.rt.title')}</div>
      <div className="prop-hint" id="scano-rt-status" data-running={rt.running ? '1' : '0'}
        data-timing={last ? JSON.stringify({ ...last.timing, manifest_ms: Math.round(last.manifestMs),
          round_trip_ms: Math.round(last.roundTripMs) }) : undefined}>
        {rt.running
          ? (rt.queued ? t('scano.rt.queued') : t('scano.rt.running'))
          : last
            ? t('scano.rt.last', { s: (last.roundTripMs / 1000).toFixed(1) })
            : t('scano.rt.idle')}
      </div>
      {rt.error && (
        <div className="error">{rt.code === 'needs_first_iteration' ? t('scano.rt.needs_t1') : rt.error}</div>
      )}
      {last?.warnings && last.warnings.length > 0 && (
        <div className="warn">{t('scano.warnings')} {last.warnings.join('; ')}</div>
      )}

      <div className="prop-row" style={{ gap: 6, flexWrap: 'wrap' }}>
        <button type="button" className="btn" id="scano-snapshot-take" disabled={rt.running || !capture} onClick={takeSnapshot}
          title={t('scano.snap.take_hint', { n: MAX_SNAPSHOTS })}><Icon name="camera" />{t('scano.snap.take')}</button>
        <button type="button" className="btn" id="scano-snapshot-compare" disabled={!snaps.length}
          onClick={() => setSnapshotViewOpen(!viewOpen)}>
          {viewOpen ? t('scano.snap.close') : t('scano.snap.compare', { n: snaps.length })}
        </button>
        <button type="button" className="btn" id="scano-snapshot-clear" disabled={!snaps.length} onClick={clearSnapshots}>
          {t('scano.snap.clear')}
        </button>
      </div>
      <ScanoSnapshotView />

      <div className="prop-title" style={{ marginTop: 6 }}>{t('scano.rt.acus')}</div>
      {acus.map((row) => {
        const [qLo, qHi] = sliderBounds(ranges.acu_supply_airflow_m3s, row.flow, [0.1, Math.max(1, 2 * row.flow)], 0.05)
        const [tLo, tHi] = sliderBounds(ranges.acu_supply_temperature_C, row.temp, [row.temp - 6, row.temp + 6], 0.5)
        return (
        <div key={row.uid} className="scano-rt-acu" data-uid={row.uid}>
          <div className="prop-hint" style={{ marginTop: 4 }}>{row.name}</div>
          <div className="prop-row">
            <label htmlFor={`scano-rt-flow-${row.uid}`}>{t('label.volume_flow_rate')}</label>
            <Slider id={`scano-rt-flow-${row.uid}`} value={row.flow} min={qLo} max={qHi} step={0.05} unit="m³/s"
              onCommit={(v) => after(() => setAcuSupply(row, 'flow', v))} />
          </div>
          <div className="prop-row">
            <label htmlFor={`scano-rt-temp-${row.uid}`}>{t('label.supply_temp')}</label>
            <Slider id={`scano-rt-temp-${row.uid}`} value={row.temp} min={tLo} max={tHi} step={0.5} unit="°C" digits={1}
              onCommit={(v) => after(() => setAcuSupply(row, 'temp', v))} />
          </div>
        </div>
        )
      })}

      <div className="prop-title" style={{ marginTop: 6, cursor: 'pointer' }} id="scano-rt-racks-toggle"
        onClick={() => setShowRacks((v) => !v)}>
        {showRacks ? '▾' : '▸'} {t('scano.rt.racks', { n: racks.length })}
      </div>
      {showRacks && racks.map((row) => {
        const perServer = row.power / row.servers
        const [pLo, pHi] = sliderBounds(ranges.server_power_W, perServer, [0.25 * perServer, 2 * perServer],
          100 / row.servers)
        return (
        <div key={row.uid} className="prop-row scano-rt-rack" data-uid={row.uid}>
          <label htmlFor={`scano-rt-rack-${row.uid}`} title={row.name}
            style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {row.name.replace(/^rack_group_/, 'g')}
          </label>
          <Slider id={`scano-rt-rack-${row.uid}`} value={row.power / 1000} min={(pLo * row.servers) / 1000}
            max={(pHi * row.servers) / 1000} step={0.1} unit="kW" digits={1}
            onCommit={(v) => after(() => setRackPower(row.uid, v * 1000))} />
        </div>
        )
      })}
    </div>
  )
}
