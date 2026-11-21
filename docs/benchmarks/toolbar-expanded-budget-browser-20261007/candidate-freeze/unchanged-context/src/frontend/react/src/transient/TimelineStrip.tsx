import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { refitToolbar } from '../components/PanelResizers'
import { NumberField } from '../components/NumberField'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { refusalText } from './refusalText'
import { transientOf, type TransientStart } from './model'
import { setTrackPanelOpen, subscribeTrackPanel, trackPanelOpen } from './timelineUi'
import {
  applyTimelineOp, keyframeTimes, lastTimelineRefusal, setDuration, setGrid, setStart, shiftTime, tryTimelineOp,
} from './trackEdits'

/** Pixels a pointer may wander before a press on a diamond counts as a drag. */
const DRAG_SLOP = 3

/**
 * The timeline under the top bar (transient design §2): the start condition on
 * the left, the union of all keyframe times as diamonds, the playhead, the
 * duration on the right, and the toggle for the track panel. Transient
 * projects only; it is the last row of #toolbar, so it collapses with it.
 */
export function TimelineStrip() {
  const { t } = useTranslation()
  const project = useSyncExternalStore(store.subscribe, store.getProject)
  // Refused timeline operations leave the project object unchanged.
  useSyncExternalStore(store.subscribe, store.revision)
  const playhead = useSyncExternalStore(store.subscribePlayhead, store.playheadTime)
  const panelOpen = useSyncExternalStore(subscribeTrackPanel, trackPanelOpen)
  const barRef = useRef<HTMLDivElement>(null)
  const stripRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState<{ from: number; at: number } | null>(null)
  const [exact, setExact] = useState<{ from: number; text: string } | null>(null)
  const settingsRef = useRef<HTMLDetailsElement>(null)
  const settingsPanelRef = useRef<HTMLDivElement>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsPosition, setSettingsPosition] = useState({ left: 8, top: 8 })
  const tr = transientOf(project)
  const refusal = lastTimelineRefusal()
  // The toolbar is capped, scrollable and filtered. A body portal keeps this
  // small editor clear of its clipping/containing block, like the shortcut UI.
  useLayoutEffect(() => {
    if (!settingsOpen || tr.mode !== 'transient') return
    const position = () => {
      const anchor = settingsRef.current?.getBoundingClientRect()
      const panel = settingsPanelRef.current
      if (!anchor || !panel) return
      const left = Math.max(8, Math.min(anchor.left, window.innerWidth - panel.offsetWidth - 8))
      const below = anchor.bottom + 6
      const top = below + panel.offsetHeight <= window.innerHeight - 8 ? below : Math.max(8, anchor.top - panel.offsetHeight - 6)
      setSettingsPosition({ left, top })
    }
    position()
    window.addEventListener('resize', position)
    window.addEventListener('scroll', position, true)
    return () => {
      window.removeEventListener('resize', position)
      window.removeEventListener('scroll', position, true)
    }
  }, [settingsOpen, tr.mode, refusal])
  useEffect(() => {
    if (!settingsOpen) return
    settingsPanelRef.current?.querySelector<HTMLInputElement>('input')?.focus()
    const outside = (e: PointerEvent) => {
      if (e.target instanceof Node && !settingsRef.current?.contains(e.target) && !settingsPanelRef.current?.contains(e.target)) setSettingsOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [settingsOpen])
  useEffect(() => { setSettingsOpen(false) }, [tr.mode])
  // The strip wraps onto more lines in a narrow toolbar, and the uniform
  // temperatures come and go: the toolbar fit has to hear about a height it
  // cannot observe through its pinned box.
  useEffect(() => {
    const el = stripRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    let h = el.offsetHeight
    const ro = new ResizeObserver(() => {
      if (el.offsetHeight !== h) { h = el.offsetHeight; refitToolbar() }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [tr.mode])
  if (tr.mode !== 'transient') return null

  const dur = tr.durationS
  const seekStep = tr.gridS > 0 ? tr.gridS : 1
  const times = keyframeTimes(tr)
  const pct = (time: number) => `${Math.max(0, Math.min(100, (time / dur) * 100))}%`
  const timeAt = (clientX: number) => {
    const r = barRef.current?.getBoundingClientRect()
    if (!r || r.width <= 0) return 0
    return Math.max(0, Math.min(dur, ((clientX - r.left) / r.width) * dur))
  }

  // Press on the bar: seek there and keep following the pointer until release.
  const seekFrom = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    barRef.current?.focus()
    store.setPlayhead(timeAt(e.clientX))
    const move = (ev: PointerEvent) => store.setPlayhead(timeAt(ev.clientX))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const seekByKey = (e: React.KeyboardEvent) => {
    let at: number
    if (e.key === 'Home') at = 0
    else if (e.key === 'End') at = dur
    else if (e.key === 'ArrowLeft') at = store.playheadTime() - seekStep
    else if (e.key === 'ArrowRight') at = store.playheadTime() + seekStep
    else return
    e.preventDefault()
    e.stopPropagation()
    store.setPlayhead(at)
  }

  // Press on a diamond: a drag shifts every keyframe at that time, a click seeks to it.
  const grabDiamond = (from: number) => (e: React.PointerEvent) => {
    if (e.button !== 0) return
    e.stopPropagation()
    const x0 = e.clientX
    let moved = false
    const move = (ev: PointerEvent) => {
      if (Math.abs(ev.clientX - x0) > DRAG_SLOP) moved = true
      if (moved) setDragging({ from, at: timeAt(ev.clientX) })
    }
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setDragging(null)
      if (moved || Math.abs(ev.clientX - x0) > DRAG_SLOP) applyTimelineOp('shiftKeyframeTime', shiftTime(from, timeAt(ev.clientX)), `${from} s`)
      else store.setPlayhead(from)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const commitDuration = (d: number) => {
    const r = tryTimelineOp(tr, setDuration(d, { trim: false }))
    if (!r.ok && r.reason === 'trim') {
      const n = tr.tracks.reduce((s, trk) => s + trk.keyframes.filter((k) => k.t > d).length, 0)
      if (!window.confirm(t('transient.trim_confirm', { d, n }))) return false
      return applyTimelineOp('setDuration', setDuration(d, { trim: true }), `${d} s`)
    }
    return applyTimelineOp('setDuration', setDuration(d, { trim: false }), `${d} s`)
  }

  const start = tr.start
  const uniform = start.uniform ?? {}
  const setUniform = (key: 'airT_C' | 'waterT_C' | 'solidT_C', v: number) =>
    applyTimelineOp('setStart', setStart({ kind: 'uniform', uniform: { ...uniform, [key]: v } } as TransientStart))

  return (
    <div id="timeline-strip" ref={stripRef}>
      <select
        id="timeline-start"
        title={t('transient.start.title')}
        value={start.kind}
        onChange={(e) => applyTimelineOp('setStart', setStart(
          e.target.value === 'uniform' ? { kind: 'uniform', uniform } : { kind: 'steadyConverged', uniform },
        ))}
      >
        <option value="steadyConverged">{t('transient.start.steadyConverged')}</option>
        <option value="uniform">{t('transient.start.uniform')}</option>
      </select>
      {start.kind === 'uniform' && (
        <span className="timeline-uniform">
          {(['airT_C', 'waterT_C', 'solidT_C'] as const).map((k) => (
            <label key={k} title={t(`transient.start.${k}`)}>
              {t(`transient.start.${k}`)}
              <NumberField value={uniform[k] ?? 0} step={0.5} width={48} onCommit={(v) => { setUniform(k, v) }} />
            </label>
          ))}
        </span>
      )}
      <div id="timeline-bar" ref={barRef} onPointerDown={seekFrom} onKeyDown={seekByKey}
        role="slider" tabIndex={0} aria-label={t('transient.playhead')} aria-orientation="horizontal"
        aria-valuemin={0} aria-valuemax={dur} aria-valuenow={playhead}
        aria-valuetext={t('transient.time', { t: playhead })} title={t('transient.bar.title')}>
        {times.map((time) => (
          <span
            key={time}
            className={`timeline-diamond${Math.abs(time - playhead) < 1e-9 ? ' at-playhead' : ''}`}
            style={{ left: pct(dragging?.from === time ? dragging.at : time) }}
            title={`${time} s`}
            onPointerDown={grabDiamond(time)}
            onDoubleClick={() => setExact({ from: time, text: String(time) })}
          />
        ))}
        <span className="timeline-playhead" style={{ left: pct(playhead) }} />
      </div>
      {exact && (
        <input
          id="timeline-exact"
          type="number"
          autoFocus
          value={exact.text}
          title={t('transient.exact_time')}
          onChange={(e) => setExact({ ...exact, text: e.target.value })}
          onBlur={() => setExact(null)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const v = Number(exact.text)
              if (Number.isFinite(v)) applyTimelineOp('shiftKeyframeTime', shiftTime(exact.from, v), `${exact.from} s`)
              setExact(null)
            }
            if (e.key === 'Escape') setExact(null)
          }}
        />
      )}
      <label id="timeline-time" className="timeline-duration" htmlFor="timeline-playhead-time"
        title={t('transient.playhead')}>
        t
        <NumberField id="timeline-playhead-time" ariaLabel={t('transient.playhead')} value={playhead}
          min={0} max={dur} step={seekStep} width={64} onCommit={(v) => { store.setPlayhead(v) }} />
        s
      </label>
      <details id="timeline-settings" ref={settingsRef} open={settingsOpen} className="timeline-settings" onKeyDown={(e) => {
        if (e.key === 'Escape') {
          setSettingsOpen(false)
          e.currentTarget.querySelector('summary')?.focus()
          e.stopPropagation()
        }
      }}>
        <summary aria-controls="timeline-settings-panel" aria-expanded={settingsOpen} onClick={(e) => {
          e.preventDefault()
          setSettingsOpen(!settingsOpen)
        }}>{t('transient.settings')}</summary>
        {createPortal(<div id="timeline-settings-panel" ref={settingsPanelRef} className="timeline-settings-panel"
          hidden={!settingsOpen} style={settingsPosition}>
          <div className="timeline-settings-fields">
            <label htmlFor="timeline-duration">
              {t('transient.duration')}
              <NumberField id="timeline-duration" ariaLabel={t('transient.duration')} value={dur}
                step={tr.gridS} min={tr.gridS} width="100%" onCommit={(v) => commitDuration(v)} />
            </label>
            <label htmlFor="timeline-grid">
              {t('transient.grid')}
              <NumberField id="timeline-grid" ariaLabel={t('transient.grid')} ariaDescribedBy="timeline-grid-help"
                value={tr.gridS} step={0.01} min={0} width="100%"
                onCommit={(v) => applyTimelineOp('setGrid', setGrid(v), `${v} s`)} />
            </label>
          </div>
          <p id="timeline-grid-help">{t('transient.grid.help')}</p>
          {refusal && <p role="status" className="timeline-settings-error">
            {t('transient.refused', { reason: refusalText(refusal) })}
          </p>}
        </div>, document.body)}
      </details>
      <button
        id="timeline-tracks-toggle"
        className={`tool-btn${panelOpen ? ' active' : ''}`}
        title={t('transient.tracks_toggle_title')}
        onClick={() => setTrackPanelOpen(!panelOpen)}
      >
        {t('transient.tracks_toggle')}
      </button>
      {refusal && <span className="timeline-refusal">{t('transient.refused', { reason: refusalText(refusal) })}</span>}
    </div>
  )
}
