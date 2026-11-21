// Inspector time semantics (transient design §2): a tracked field says whether
// the playhead sits on one of its keyframes, inside a ramp, or on a held value;
// refusals read in the user's language.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import * as store from '../src/projectStore'
import { getLocale, setLocale } from '../src/i18n'
import { readProjectFileText } from '../src/io/projectFiles'
import { KeyframeBadge } from '../src/transient/KeyframeBadge'
import { keyframeStateAt } from '../src/transient/keyframeState'
import { refusalText } from '../src/transient/refusalText'
import { transientModel, transientOf } from '../src/transient/model'
import { setSimulationMode } from '../src/transient/simulationMode'
import { addKeyframe, applyTimelineOp, setKeyframe, trackKeyOf } from '../src/transient/trackEdits'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('keyframeStateAt', () => {
  const kfs = [{ t: 0, value: 1 }, { t: 100, value: 2, rampS: 0 }, { t: 300, value: 3, rampS: 60 }]
  it('is key on a keyframe, ramp strictly inside a ramp, hold otherwise', () => {
    expect(keyframeStateAt(kfs, 0)).toBe('key')
    expect(keyframeStateAt(kfs, 100)).toBe('key')
    expect(keyframeStateAt(kfs, 50)).toBe('hold')
    expect(keyframeStateAt(kfs, 240)).toBe('hold')          // the ramp starts from the held value
    expect(keyframeStateAt(kfs, 270)).toBe('ramp')
    expect(keyframeStateAt(kfs, 300)).toBe('key')
    expect(keyframeStateAt(kfs, 500)).toBe('hold')
  })
})

describe('refusalText', () => {
  let prev: ReturnType<typeof getLocale>
  beforeEach(() => { prev = getLocale(); setLocale('zh-CN') })
  afterEach(() => setLocale(prev))

  it('translates every reason the validator can give', () => {
    const m = transientModel()
    const base = { mode: 'transient', durationS: 600, gridS: 10, start: { kind: 'uniform' } }
    const bad = [
      { ...base, durationS: 0, tracks: [] },
      { ...base, tracks: [{ modelUid: 'm', param: 'server.power', target: 'e', keyframes: [{ t: 0, value: -1 }] }] },
      { ...base, tracks: [{ modelUid: 'm', param: 'server.power', target: 'e', keyframes: [{ t: 0, value: 1 }, { t: 15, value: 1, rampS: 0 }] }] },
      { ...base, tracks: [{ modelUid: 'm', param: 'server.power', target: 'e', keyframes: [{ t: 0, value: 1 }, { t: 20, value: 1, rampS: 30 }] }] },
      { ...base, tracks: [{ modelUid: 'm', param: 'server.power', target: 'e', keyframes: [{ t: 0, value: 1 }, { t: 700, value: 1, rampS: 0 }] }] },
      { ...base, tracks: [{ modelUid: 'm', param: 'nope', target: '', keyframes: [{ t: 0, value: 1 }] }] },
    ]
    for (const tr of bad) {
      const errs = m.validateTransient(tr)
      expect(errs.length).toBeGreaterThan(0)
      for (const e of errs) expect(refusalText(e.msg)).not.toMatch(/[a-z]{4,} [a-z]{3,}/)
    }
    expect(refusalText('value outside [0, 1000000] W')).toContain('1000000')
    expect(refusalText('time not on the 10 s grid')).toContain('10')
  })

  it('translates the timeline operations and the store codes; unknown text passes through', () => {
    for (const r of ['the start keyframes cannot move', 'a keyframe cannot move onto the start',
      'a keyframe cannot move past the end of the timeline', 'no such keyframe', 'the start keyframe cannot move',
      'no such track', 'the start keyframe already exists', 'there is already a keyframe at that time',
      'the start keyframe cannot be deleted', 'static', 'structure']) {
      expect(refusalText(r)).not.toBe(r)
    }
    expect(refusalText('something new')).toBe('something new')
  })
})

describe('<KeyframeBadge>', () => {
  let host: HTMLDivElement
  let root: ReturnType<typeof createRoot>
  const power = () => transientOf(store.getProject()).tracks.find(t => t.param === 'server.power' && t.target === 'e_3')!
  beforeEach(() => {
    const r = readProjectFileText(readFileSync(resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj'), 'utf8'))
    if (!r.ok) throw new Error(r.error)
    store.loadProject(r.project, null)
    host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  })
  afterEach(() => { act(() => root.unmount()); host.remove() })
  const render = (target = 'e_3') =>
    act(() => root.render(<KeyframeBadge modelUid="m_1" param="server.power" target={target} />))
  const badge = () => host.querySelector('.kf-badge') as HTMLElement | null

  it('shows nothing in a steady project or without a track', () => {
    render()
    expect(badge()).toBeNull()
    act(() => { setSimulationMode('transient') })
    render('no-such-server')
    expect(badge()).toBeNull()
  })

  it('follows the playhead through key, hold and ramp', () => {
    act(() => { setSimulationMode('transient') })
    render()
    expect(badge()!.dataset.state).toBe('key')            // t = 0
    act(() => { store.setPlayhead(120) })
    expect(badge()!.dataset.state).toBe('hold')
    act(() => { applyTimelineOp('k', addKeyframe(trackKeyOf(power()), 200, 300)) })
    act(() => { applyTimelineOp('r', setKeyframe(trackKeyOf(power()), 1, { rampS: 100 })) })
    act(() => { store.setPlayhead(150) })
    expect(badge()!.dataset.state).toBe('ramp')
    act(() => { store.setPlayhead(200) })
    expect(badge()!.dataset.state).toBe('key')
    expect(badge()!.title).not.toBe('')
  })
})
