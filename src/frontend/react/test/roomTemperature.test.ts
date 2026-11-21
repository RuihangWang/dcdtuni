import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import {
  DEFAULT_ROOM_TEMP_C,
  betaOf,
  currentReadout,
  isPresetTemperature,
  isValidRoomTemperature,
  presetLabel,
  roomTemperatureC,
  selectRoomTemperature,
  setRoomTemperature,
} from '../src/geometry/roomTemperature'

function project(temperature?: number): CfdProject {
  return {
    name: 'T',
    geometry: { height: 3, plane: [], ...(temperature === undefined ? {} : { temperature }) },
    constructions: {},
  }
}

beforeEach(() => store.loadProject(project(22), '/tmp/T.cfdproj'))

describe('reading', () => {
  it('falls back to 22 °C when the project carries none', () => {
    store.loadProject(project(), '/tmp/T.cfdproj')
    expect(roomTemperatureC(store.getProject())).toBe(DEFAULT_ROOM_TEMP_C)
  })

  it('reports K and beta the way the solver derives them', () => {
    // beta = 1/TRef is the Boussinesq coefficient; a wrong TRef is a wrong
    // buoyancy on every plume, so the readout has to be exact.
    expect(betaOf(22)).toBeCloseTo(1 / 295.15, 12)
    expect(currentReadout(22)).toEqual({ tempC: 22, tempK: '295.15', beta: '3.388e-3' })
  })

  it('labels a preset with both scales', () => {
    expect(presetLabel(18)).toBe('18°C (291.15 K)')
  })
})

describe('setRoomTemperature', () => {
  it('writes geometry.temperature', () => {
    setRoomTemperature(26)
    expect(store.getProject()?.geometry?.temperature).toBe(26)
  })

  it('ignores a value outside [-50, 100] instead of clamping it', () => {
    // Clamping would turn a typo of 220 into a deliberate-looking 100 °C.
    setRoomTemperature(220)
    expect(store.getProject()?.geometry?.temperature).toBe(22)
    expect(isValidRoomTemperature(220)).toBe(false)
    expect(isValidRoomTemperature(-50)).toBe(true)
  })

  it('costs no undo step when the value is rejected', () => {
    setRoomTemperature(Number.NaN)
    expect(store.canUndo()).toBe(false)
  })

  it('is undoable when it is accepted', () => {
    setRoomTemperature(30)
    store.undo()
    expect(store.getProject()?.geometry?.temperature).toBe(22)
  })
})

describe('selectRoomTemperature', () => {
  it('applies a preset straight', () => {
    selectRoomTemperature('28')
    expect(store.getProject()?.geometry?.temperature).toBe(28)
  })

  it('nudges a preset off the list when custom is picked', () => {
    // Without the nudge the value stays a preset, the custom box never renders
    // and the select snaps back — the user cannot reach a custom value at all.
    selectRoomTemperature('custom')
    const value = store.getProject()?.geometry?.temperature as number
    expect(value).toBeCloseTo(22.1, 10)
    expect(isPresetTemperature(value)).toBe(false)
  })

  it('leaves an already-custom value alone when custom is picked again', () => {
    store.loadProject(project(23.5), '/tmp/T.cfdproj')
    selectRoomTemperature('custom')
    expect(store.getProject()?.geometry?.temperature).toBe(23.5)
  })
})
