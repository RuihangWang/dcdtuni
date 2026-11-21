import { describe, expect, it } from 'vitest'
import {
  isLeftTabHidden,
  leftTabGate,
  leftTabLabelKey,
  resolveLeftTab,
  visibleLeftTabs,
} from '../src/ui/leftTabs'

const NOTHING_DONE = { validated: false, meshReady: false, solved: false }
const ALL_DONE = { validated: true, meshReady: true, solved: true }

describe('visibleLeftTabs', () => {
  it('offers every tab in air mode', () => {
    expect(visibleLeftTabs('air').map((t) => t.id)).toEqual([
      'geometry',
      'mesh',
      'solver',
      'postprocess',
      'system',
    ])
  })

  it('marks mesh and system hidden in liquid mode, keeping their ids in the DOM', () => {
    expect(isLeftTabHidden('mesh', 'liquid')).toBe(true)
    expect(isLeftTabHidden('system', 'liquid')).toBe(true)
    expect(isLeftTabHidden('solver', 'liquid')).toBe(false)
    expect(isLeftTabHidden('mesh', 'air')).toBe(false)
  })

  it('drops mesh and system in liquid mode', () => {
    expect(visibleLeftTabs('liquid').map((t) => t.id)).toEqual([
      'geometry',
      'solver',
      'postprocess',
    ])
  })
})

describe('leftTabLabelKey', () => {
  it('uses the same solver task name in both cooling modes', () => {
    expect(leftTabLabelKey('solver', 'air')).toBe('nav.solver')
    expect(leftTabLabelKey('solver', 'liquid')).toBe('nav.solver')
    expect(leftTabLabelKey('postprocess', 'liquid')).toBe('nav.postprocess')
  })
})

describe('leftTabGate', () => {
  it('locks the air pipeline until each step has produced something', () => {
    expect(leftTabGate('mesh', 'air', NOTHING_DONE)).toBe('validate')
    expect(leftTabGate('solver', 'air', { ...NOTHING_DONE, validated: true })).toBe('mesh')
    expect(
      leftTabGate('postprocess', 'air', { validated: true, meshReady: true, solved: false }),
    ).toBe('solve')
    expect(leftTabGate('postprocess', 'air', ALL_DONE)).toBeNull()
  })

  it('never locks solver or post-processing in liquid mode', () => {
    expect(leftTabGate('solver', 'liquid', NOTHING_DONE)).toBeNull()
    expect(leftTabGate('postprocess', 'liquid', NOTHING_DONE)).toBeNull()
  })

  it('never gates geometry', () => {
    expect(leftTabGate('geometry', 'air', NOTHING_DONE)).toBeNull()
  })
})

describe('resolveLeftTab', () => {
  it('sends a hidden tab back to geometry', () => {
    expect(resolveLeftTab('mesh', 'liquid')).toBe('geometry')
    expect(resolveLeftTab('system', 'liquid')).toBe('geometry')
  })

  it('leaves a visible tab where it is', () => {
    expect(resolveLeftTab('solver', 'liquid')).toBe('solver')
    expect(resolveLeftTab('mesh', 'air')).toBe('mesh')
  })
})
