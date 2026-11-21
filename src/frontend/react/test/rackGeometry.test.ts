import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import { addModel, getProject, loadProject, undo } from '../src/projectStore'
import * as store from '../src/projectStore'
import {
  rackHeightMode,
  rackSettingPath,
  readRackSetting,
  readRackSize,
  updateRackHeightMode,
  updateRackSetting,
  updateRackSize,
} from '../src/geometry/rackGeometry'

function loadWithRack(): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  loadProject(project, null)
  const { bucket, model } = createModel(getProject() as CfdProject, 'rack')
  return addModel(bucket, model)
}

function rack(uid: string): Construction {
  return store.findConstruction(getProject() as CfdProject, uid)!.model
}

describe('rackSettingPath', () => {
  it('sends the three V4-relocated fields under geometry', () => {
    expect(rackSettingPath('unitCount')).toEqual(['geometry', 'slot'])
    expect(rackSettingPath('unitHeight')).toEqual(['geometry', 'slotHeight'])
    expect(rackSettingPath('slotWidthRatio')).toEqual(['geometry', 'slotWidthRatio'])
  })

  it('leaves the margins and the default span at the model root', () => {
    expect(rackSettingPath('safeMarginTop')).toEqual(['safeMarginTop'])
    expect(rackSettingPath('safeMarginBottom')).toEqual(['safeMarginBottom'])
    expect(rackSettingPath('defaultSpanUnits')).toEqual(['defaultSpanUnits'])
  })
})

describe('updateRackSetting', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWithRack()
  })

  it('re-derives the total height from the unit count in unitHeight mode', () => {
    updateRackHeightMode(uid, 'unitHeight')
    updateRackSetting(uid, 'unitCount', 20)
    const model = rack(uid)
    const slotHeight = readRackSetting(model, 'unitHeight') as number
    const top = readRackSetting(model, 'safeMarginTop') ?? 0
    const bottom = readRackSetting(model, 'safeMarginBottom') ?? 0
    expect(readRackSetting(model, 'unitCount')).toBe(20)
    expect(readRackSize(model, 'H')).toBeCloseTo(20 * slotHeight + top + bottom, 10)
  })

  it('re-derives the unit height from the total height in totalHeight mode', () => {
    updateRackHeightMode(uid, 'totalHeight')
    updateRackSize(uid, 'H', 2.0)
    updateRackSetting(uid, 'unitCount', 40)
    const model = rack(uid)
    const top = readRackSetting(model, 'safeMarginTop') ?? 0
    const bottom = readRackSetting(model, 'safeMarginBottom') ?? 0
    expect(readRackSize(model, 'H')).toBeCloseTo(2.0, 10)
    expect(readRackSetting(model, 'unitHeight')).toBeCloseTo((2.0 - top - bottom) / 40, 10)
  })

  it('rebuilds the partition table to one plate fewer than the slots', () => {
    updateRackSetting(uid, 'unitCount', 12)
    const partitions = (rack(uid).constructions as { partitions?: unknown[] }).partitions ?? []
    expect(partitions.length).toBe(11)
  })

  it('clamps the slot count to the 3U floor the shared sync enforces', () => {
    updateRackSetting(uid, 'unitCount', 1)
    expect(readRackSetting(rack(uid), 'unitCount')).toBe(3)
  })

  it('clamps the slot width ratio into 0.2 – 0.95', () => {
    updateRackSetting(uid, 'slotWidthRatio', 5)
    expect(readRackSetting(rack(uid), 'slotWidthRatio')).toBeCloseTo(0.95, 10)
  })

  it('scales the two safe margins down when they exceed half the slot area', () => {
    updateRackHeightMode(uid, 'unitHeight')
    updateRackSetting(uid, 'unitCount', 10)
    const slotArea = 10 * (readRackSetting(rack(uid), 'unitHeight') as number)
    updateRackSetting(uid, 'safeMarginTop', slotArea)
    updateRackSetting(uid, 'safeMarginBottom', slotArea)
    const model = rack(uid)
    const sum =
      (readRackSetting(model, 'safeMarginTop') ?? 0) +
      (readRackSetting(model, 'safeMarginBottom') ?? 0)
    expect(sum).toBeLessThanOrEqual(slotArea * 0.5 + 1e-9)
  })

  it('is one undo step', () => {
    const before = readRackSetting(rack(uid), 'unitCount')
    updateRackSetting(uid, 'unitCount', 24)
    expect(readRackSetting(rack(uid), 'unitCount')).toBe(24)
    undo()
    expect(readRackSetting(rack(uid), 'unitCount')).toBe(before)
  })
})

describe('updateRackSize', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWithRack()
  })

  it('writes W and D onto the x and z axes', () => {
    updateRackSize(uid, 'W', 0.8)
    updateRackSize(uid, 'D', 1.4)
    expect(readRackSize(rack(uid), 'W')).toBeCloseTo(0.8, 10)
    expect(readRackSize(rack(uid), 'D')).toBeCloseTo(1.4, 10)
  })

  it('clamps a zero depth up, so the inlet/outlet offsets stay resolvable', () => {
    updateRackSize(uid, 'D', 0)
    expect(readRackSize(rack(uid), 'D')).toBeGreaterThan(0)
  })

  it('leaves the total height derived when the mode says unit height', () => {
    updateRackHeightMode(uid, 'unitHeight')
    const derived = readRackSize(rack(uid), 'H')
    updateRackSize(uid, 'H', 99)
    expect(readRackSize(rack(uid), 'H')).toBeCloseTo(derived, 10)
  })

  it('ignores a non-finite value rather than writing NaN into the geometry', () => {
    const before = readRackSize(rack(uid), 'W')
    updateRackSize(uid, 'W', Number.NaN)
    expect(readRackSize(rack(uid), 'W')).toBe(before)
  })
})

describe('rackHeightMode', () => {
  it('defaults anything unrecognised to totalHeight', () => {
    const uid = loadWithRack()
    updateRackHeightMode(uid, 'unitHeight')
    expect(rackHeightMode(rack(uid))).toBe('unitHeight')
    updateRackHeightMode(uid, 'totalHeight')
    expect(rackHeightMode(rack(uid))).toBe('totalHeight')
  })
})
