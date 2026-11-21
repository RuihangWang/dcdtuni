/**
 * Timeline operations: what the strip and the track panel do to the tracks
 * (transient design §2).
 *
 * An op edits a transient block in place. It is first run on a copy and kept
 * only if the result is a valid timeline — `validateTransient` is the one
 * definition of that (order, ramps within the gap, grid, bounds, the t = 0
 * rule) — so an op never has to re-state those rules and can never leave a
 * half-edited block. Times snap to the grid. `applyTimelineOp` commits an
 * accepted op as one undoable edit.
 */
import * as store from '../projectStore'
import { transientModel, transientOf, type Keyframe, type Track, type TransientBlock, type TransientStart } from './model'

/** Refusal reason, or null when the op applied. */
export type TimelineOp = (tr: TransientBlock) => string | null
export type OpResult = { ok: true; block: TransientBlock } | { ok: false; reason: string }

export function trackKeyOf(t: Pick<Track, 'modelUid' | 'param' | 'target'>): string {
  return `${t.modelUid}|${t.param}|${t.target}`
}

const EPS = 1e-9
const snap = (t: number, g: number) => (g > 0 ? Math.round(Math.round(t / g) * g * 1e9) / 1e9 : t)
const same = (a: number, b: number) => Math.abs(a - b) < EPS

function trackOf(tr: TransientBlock, key: string): Track | undefined {
  return tr.tracks.find((t) => trackKeyOf(t) === key)
}

/** Every keyframe time on the timeline (all tracks, t = 0 excluded), ascending. */
export function keyframeTimes(tr: TransientBlock): number[] {
  const ts: number[] = []
  for (const trk of tr.tracks) {
    for (const k of trk.keyframes) {
      if (k.t > EPS && !ts.some((x) => same(x, k.t))) ts.push(k.t)
    }
  }
  return ts.sort((a, b) => a - b)
}

/** Move every keyframe at `from` to `to` (a diamond drag on the strip). */
export function shiftTime(from: number, to: number): TimelineOp {
  return (tr) => {
    if (from <= EPS) return 'the start keyframes cannot move'
    const t = snap(to, tr.gridS)
    if (t <= EPS) return 'a keyframe cannot move onto the start'
    if (t > tr.durationS + EPS) return 'a keyframe cannot move past the end of the timeline'
    for (const trk of tr.tracks) {
      for (const k of trk.keyframes) if (same(k.t, from)) k.t = t
    }
    return null
  }
}

export function moveKeyframe(key: string, index: number, to: number): TimelineOp {
  return (tr) => {
    const trk = trackOf(tr, key)
    if (!trk || !trk.keyframes[index]) return 'no such keyframe'
    if (index === 0) return 'the start keyframe cannot move'
    trk.keyframes[index]!.t = snap(to, tr.gridS)
    return null
  }
}

export function setKeyframe(key: string, index: number, patch: { value?: number; rampS?: number }): TimelineOp {
  return (tr) => {
    const k = trackOf(tr, key)?.keyframes[index]
    if (!k) return 'no such keyframe'
    if (patch.value !== undefined) k.value = patch.value
    if (patch.rampS !== undefined) k.rampS = snap(patch.rampS, tr.gridS)
    return null
  }
}

export function addKeyframe(key: string, at: number, value: number): TimelineOp {
  return (tr) => {
    const trk = trackOf(tr, key)
    if (!trk) return 'no such track'
    const t = snap(at, tr.gridS)
    if (t <= EPS) return 'the start keyframe already exists'
    if (trk.keyframes.some((k) => same(k.t, t))) return 'there is already a keyframe at that time'
    const kf: Keyframe = { t, value, rampS: 0 }
    const i = trk.keyframes.findIndex((k) => k.t > t)
    trk.keyframes.splice(i < 0 ? trk.keyframes.length : i, 0, kf)
    return null
  }
}

export function deleteKeyframe(key: string, index: number): TimelineOp {
  return (tr) => {
    const trk = trackOf(tr, key)
    if (!trk || !trk.keyframes[index]) return 'no such keyframe'
    if (index === 0) return 'the start keyframe cannot be deleted'
    trk.keyframes.splice(index, 1)
    return null
  }
}

/** Refused with reason 'trim' when keyframes lie past the new end and `trim` is off. */
export function setDuration(d: number, opts: { trim: boolean }): TimelineOp {
  return (tr) => {
    const end = snap(d, tr.gridS)
    const beyond = tr.tracks.some((trk) => trk.keyframes.some((k) => k.t > end + EPS))
    if (beyond && !opts.trim) return 'trim'
    if (beyond) for (const trk of tr.tracks) trk.keyframes = trk.keyframes.filter((k) => k.t <= end + EPS)
    tr.durationS = end
    return null
  }
}

/** Change future snapping without moving stored keyframes, ramp starts or duration.
 * The persisted format requires every existing keyframe/ramp start on its grid,
 * so an incompatible new grid is refused instead of silently resampling data.
 */
export function setGrid(gridS: number): TimelineOp {
  return (tr) => {
    if (!Number.isFinite(gridS) || gridS <= 0) return 'grid must be finite and > 0'
    const onGrid = transientModel().onGrid
    if (tr.tracks.some(track => track.keyframes.some(frame =>
      !onGrid(frame.t, gridS) || !onGrid(frame.t - (frame.rampS ?? 0), gridS)))) {
      return 'grid change must preserve existing keyframe and ramp times'
    }
    tr.gridS = gridS
    return null
  }
}

export function setStart(start: TransientStart): TimelineOp {
  return (tr) => {
    tr.start = start
    return null
  }
}

/** Run `op` on a copy; the copy is returned only if it is a valid timeline. */
export function tryTimelineOp(tr: TransientBlock, op: TimelineOp): OpResult {
  const copy: TransientBlock = JSON.parse(JSON.stringify(tr))
  const reason = op(copy)
  if (reason) return { ok: false, reason }
  const errs = transientModel().validateTransient(copy)
  if (errs.length) return { ok: false, reason: errs[0]!.msg }
  return { ok: true, block: copy }
}

let lastRefusal: string | null = null
store.subscribeProjectLoad(() => { lastRefusal = null })
/** Why the last timeline op was refused (for the strip / panel to show), or null. */
export function lastTimelineRefusal(): string | null {
  return lastRefusal
}

/**
 * Commit an op as one undoable edit. A duration change also mirrors
 * `simulationConfig.endTimeSeconds` (the one-writer rule of setSimulationMode).
 */
export function applyTimelineOp(label: string, op: TimelineOp, detail = ''): boolean {
  const project = store.getProject()
  if (!project) return false
  const r = tryTimelineOp(transientOf(project), op)
  if (!r.ok) {
    lastRefusal = r.reason
    store.logAction('timelineRefused', `${label}: ${r.reason}`)
    return false
  }
  lastRefusal = null
  const applied = store.edit(label, detail, (draft) => {
    const doc = draft as { transient?: TransientBlock; runDefaults?: { simulationConfig?: Record<string, unknown> } }
    doc.transient = r.block
    const sim = doc.runDefaults?.simulationConfig
    if (sim && r.block.mode === 'transient') sim.endTimeSeconds = r.block.durationS
  })
  // The cursor has no persisted data or undo entry. A changed grid/duration
  // must normalize it before a later field edit can create a new keyframe.
  if (applied) store.setPlayhead(store.playheadTime())
  return applied
}
