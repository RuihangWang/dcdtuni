import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { NumberField } from '../components/NumberField'
import * as store from '../projectStore'
import { useAppSelection } from '../selectionBus'
import type { TranslationKey } from '../i18n'
import { useTranslation } from '../useTranslation'
import { domainModesOf, transientModel, transientOf, type Track } from './model'
import { subscribeTrackPanel, trackPanelOpen } from './timelineUi'
import { nodeMatches, targetLabel } from './trackLabels'
import {
  addKeyframe, applyTimelineOp, deleteKeyframe, moveKeyframe, setKeyframe, trackKeyOf,
} from './trackEdits'

const DRAG_SLOP = 3
const LANE_W = 1000
const LANE_H = 40

/** Where #toolbar ends (the panel hangs below it) and whether it is collapsed. */
function useToolbarEdge(): { bottom: number; collapsed: boolean } {
  const [edge, setEdge] = useState({ bottom: 0, collapsed: false })
  useEffect(() => {
    const bar = document.getElementById('toolbar')
    if (!bar) return
    const read = () => setEdge({ bottom: Math.round(bar.getBoundingClientRect().bottom), collapsed: bar.classList.contains('collapsed') })
    read()
    const size = new ResizeObserver(read)
    size.observe(bar)
    const cls = new MutationObserver(read)
    cls.observe(bar, { attributes: true, attributeFilter: ['class', 'style'] })
    window.addEventListener('resize', read)
    return () => { size.disconnect(); cls.disconnect(); window.removeEventListener('resize', read) }
  }, [])
  return edge
}

/**
 * The selected object's parameter tracks (transient design §2): one row per
 * track of the current air/liquid mode, with the value over time, keyframes to
 * drag in time, ramp handles to drag out a linear ramp, "+" for a keyframe at
 * the playhead, and a click on a keyframe for exact time / value / ramp and
 * delete. Floats under the top bar; opened from the timeline strip.
 */
export function TrackPanel({ coolingMode }: { coolingMode: 'air' | 'liquid' }) {
  const { t } = useTranslation()
  const project = useSyncExternalStore(store.subscribe, store.getProject)
  const playhead = useSyncExternalStore(store.subscribePlayhead, store.playheadTime)
  const open = useSyncExternalStore(subscribeTrackPanel, trackPanelOpen)
  const sel = useAppSelection()
  const edge = useToolbarEdge()
  const [editing, setEditing] = useState<{ key: string; index: number } | null>(null)
  const tr = transientOf(project)
  if (!open || tr.mode !== 'transient' || edge.collapsed) return null

  const m = transientModel()
  const rows = sel.uid
    ? tr.tracks.filter((trk) => trk.modelUid === sel.uid && m.entryOf(trk.param)?.mode === coolingMode)
    : []
  const edited = editing && tr.tracks.find((trk) => trackKeyOf(trk) === editing.key)
  const editedKey = edited ? edited.keyframes[editing!.index] : undefined

  // E8b: a domain's keyframes drive its run only while that domain is transient.
  const domainSteady = domainModesOf(project)[coolingMode] === 'steady'

  return (
    <div id="track-panel" style={{ top: edge.bottom + 4 }}>
      {domainSteady && (
        <div className="prop-hint" id="track-panel-domain-steady">
          {t(coolingMode === 'air' ? 'transient.tracks.air_steady' : 'transient.tracks.liquid_steady')}
        </div>
      )}
      {!sel.uid && <div className="prop-hint">{t('transient.tracks.pick')}</div>}
      {sel.uid && rows.length === 0 && <div className="prop-hint">{t('transient.tracks.none')}</div>}
      {rows.map((trk) => (
        <TrackRow
          key={trackKeyOf(trk)}
          trk={trk}
          durationS={tr.durationS}
          playhead={playhead}
          label={`${t(`transient.param.${trk.param}` as TranslationKey)}${targetLabel(project, trk) ? ` · ${targetLabel(project, trk)}` : ''}`}
          unit={m.entryOf(trk.param)?.unit ?? ''}
          highlighted={nodeMatches(sel.node, trk)}
          onEdit={(index) => setEditing({ key: trackKeyOf(trk), index })}
        />
      ))}
      {edited && editedKey && (
        <div id="track-key-editor">
          <label className="track-key-time">
            {t('transient.key.time')}
            <NumberField
              value={editedKey.t}
              step={tr.gridS}
              width={64}
              disabled={editing!.index === 0}
              onCommit={(v) => applyTimelineOp('moveKeyframe', moveKeyframe(editing!.key, editing!.index, v))}
            />
          </label>
          <label className="track-key-value">
            {t('transient.key.value')}
            <NumberField
              value={editedKey.value}
              width={72}
              onCommit={(v) => applyTimelineOp('setKeyframeValue', setKeyframe(editing!.key, editing!.index, { value: v }))}
            />
          </label>
          <label className="track-key-ramp">
            {t('transient.key.ramp')}
            <NumberField
              value={editedKey.rampS ?? 0}
              step={tr.gridS}
              min={0}
              width={56}
              disabled={editing!.index === 0}
              onCommit={(v) => applyTimelineOp('setKeyframeRamp', setKeyframe(editing!.key, editing!.index, { rampS: v }))}
            />
          </label>
          <button
            id="track-key-delete"
            className="btn danger"
            disabled={editing!.index === 0}
            onClick={() => { if (applyTimelineOp('deleteKeyframe', deleteKeyframe(editing!.key, editing!.index))) setEditing(null) }}
          >
            {t('transient.key.delete')}
          </button>
          <button className="btn" onClick={() => setEditing(null)}>{t('transient.key.close')}</button>
        </div>
      )}
    </div>
  )
}

function TrackRow(props: {
  trk: Track; durationS: number; playhead: number; label: string; unit: string; highlighted: boolean
  onEdit: (index: number) => void
}) {
  const { t } = useTranslation()
  const { trk, durationS, playhead } = props
  const laneRef = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<{ kind: 'key' | 'ramp'; index: number; at: number } | null>(null)
  const key = trackKeyOf(trk)
  const m = transientModel()

  const timeAt = (clientX: number) => {
    const r = laneRef.current?.getBoundingClientRect()
    if (!r || r.width <= 0) return 0
    return Math.max(0, Math.min(durationS, ((clientX - r.left) / r.width) * durationS))
  }
  const values = trk.keyframes.map((k) => k.value)
  const lo = Math.min(...values), hi = Math.max(...values)
  const pad = hi > lo ? (hi - lo) * 0.15 : Math.max(1, Math.abs(hi) * 0.1)
  const x = (time: number) => (time / durationS) * LANE_W
  const y = (v: number) => LANE_H - 4 - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * (LANE_H - 8)
  const line = m.cornerTable(trk.keyframes, durationS).map(([tt, v]) => `${x(tt)},${y(v)}`).join(' ')

  const grab = (kind: 'key' | 'ramp', index: number) => (e: React.PointerEvent) => {
    if (e.button !== 0) return
    e.stopPropagation()
    const x0 = e.clientX
    let moved = false
    const move = (ev: PointerEvent) => {
      if (Math.abs(ev.clientX - x0) > DRAG_SLOP) moved = true
      if (moved) setDrag({ kind, index, at: timeAt(ev.clientX) })
    }
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setDrag(null)
      const k = trk.keyframes[index]!
      if (!(moved || Math.abs(ev.clientX - x0) > DRAG_SLOP)) {
        if (kind === 'key') props.onEdit(index)
        return
      }
      const at = timeAt(ev.clientX)
      if (kind === 'key') applyTimelineOp('moveKeyframe', moveKeyframe(key, index, at))
      else applyTimelineOp('setKeyframeRamp', setKeyframe(key, index, { rampS: Math.max(0, k.t - at) }))
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div className={`track-row${props.highlighted ? ' highlighted' : ''}`} data-target={trk.target}>
      <span className="track-label" title={`${props.label} (${props.unit})`}>{props.label} <em>{props.unit}</em></span>
      <svg className="track-lane" ref={laneRef} viewBox={`0 0 ${LANE_W} ${LANE_H}`} preserveAspectRatio="none">
        <polyline className="track-line" points={line} />
        <line className="track-playhead" x1={x(playhead)} x2={x(playhead)} y1={0} y2={LANE_H} />
        {trk.keyframes.map((k, i) => {
          if (i === 0) return null
          const prev = trk.keyframes[i - 1]!
          const rampStart = drag?.kind === 'ramp' && drag.index === i ? drag.at : k.t - (k.rampS ?? 0)
          return (
            <rect
              key={`r${i}`}
              className="track-ramp"
              x={x(rampStart) - 5}
              y={y(prev.value) - 5}
              width={10}
              height={10}
              onPointerDown={grab('ramp', i)}
            >
              <title>{t('transient.key.ramp_handle')}</title>
            </rect>
          )
        })}
        {trk.keyframes.map((k, i) => (
          <circle
            key={`k${i}`}
            className={`track-key${Math.abs(k.t - playhead) < 1e-9 ? ' at-playhead' : ''}`}
            cx={x(drag?.kind === 'key' && drag.index === i ? drag.at : k.t)}
            cy={y(k.value)}
            r={6}
            onPointerDown={grab('key', i)}
          >
            <title>{`${k.t} s: ${k.value} ${props.unit}${k.rampS ? ` (ramp ${k.rampS} s)` : ''}`}</title>
          </circle>
        ))}
      </svg>
      <button
        className="track-add tool-btn"
        title={t('transient.key.add')}
        onClick={() => applyTimelineOp('addKeyframe', addKeyframe(key, playhead, m.valueAt(trk.keyframes, playhead)))}
      >
        +
      </button>
    </div>
  )
}
