/**
 * The time state of one tracked inspector field (transient design §2): ◆ the
 * playhead is on one of its keyframes, ◐ inside a ramp, ◇ on a held value. An
 * edit writes a keyframe at the playhead in every case. Nothing is shown in a
 * steady project, or for a field without a track (its first value is set at
 * the start).
 */
import { useSyncExternalStore } from 'react'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { keyframeStateAt } from './keyframeState'
import { transientOf } from './model'

const GLYPH = { key: '◆', ramp: '◐', hold: '◇' } as const

export function KeyframeBadge({ modelUid, param, target }: { modelUid: string; param: string; target: string }) {
  const { t } = useTranslation()
  const project = useSyncExternalStore(store.subscribe, store.getProject)
  const playhead = useSyncExternalStore(store.subscribePlayhead, store.playheadTime)
  const tr = transientOf(project)
  if (tr.mode !== 'transient') return null
  const trk = tr.tracks.find((x) => x.modelUid === modelUid && x.param === param && x.target === target)
  if (!trk) return null
  const state = keyframeStateAt(trk.keyframes, playhead)
  return (
    <span className={`kf-badge kf-badge--${state}`} data-state={state} title={t(`transient.badge.${state}`)}>
      {GLYPH[state]}
    </span>
  )
}
