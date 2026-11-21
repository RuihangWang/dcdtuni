import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {
  formatSensorLocation,
  fromSensorDisplay,
  sensorNameTaken,
  sensorUnitLabel,
  sensorUnitScale,
  toSensorDisplay,
  validateSensorEdit,
} from '../src/run/sensorEdit'

/**
 * Two failures worth pinning: a probe name OpenFOAM cannot parse, and a
 * coordinate typed in millimetres and stored as metres. The second is silent —
 * the probe simply reads nothing, three kilometres outside the room.
 */

function loadProject(unit: 'mm' | 'cm' | 'm' = 'm'): CfdProject {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  store.loadProject(project, null)
  store.setInputUnit(unit)
  return store.getProject() as CfdProject
}

describe('unit conversion', () => {
  it('scales a typed millimetre value into the metres the project stores', () => {
    expect(fromSensorDisplay(3000, 'mm')).toBeCloseTo(3)
    expect(toSensorDisplay(3, 'mm')).toBeCloseTo(3000)
  })

  it('reads the scale and the label off the project, not off a constant', () => {
    loadProject('mm')
    expect(sensorUnitScale(store.getProject())).toBe(0.001)
    expect(sensorUnitLabel(store.getProject())).toBe('mm')
    loadProject('m')
    expect(sensorUnitScale(store.getProject())).toBe(1)
  })

  it('formats a list row in the project unit with its own precision', () => {
    expect(formatSensorLocation({ x: 3, y: 1.2, z: 0.5 }, 'mm')).toBe(
      'X 3000.0000  Y 1200.0000  Z 500.0000 mm',
    )
    expect(formatSensorLocation({ x: 3, y: 1.2, z: 0.5 }, 'm')).toBe(
      'X 3.0000  Y 1.2000  Z 0.5000 m',
    )
  })
})

describe('validateSensorEdit', () => {
  let id = ''
  beforeEach(() => {
    loadProject('mm')
    id = store.addSensorProbe(1, 2, 3) as string
  })

  it('converts the typed coordinates before they reach the project', () => {
    const result = validateSensorEdit(store.getProject(), id, {
      name: 'inlet_A',
      x: 3000,
      y: 1200,
      z: 500,
    })
    expect(result.ok).toBe(true)
    expect(result.patch?.location.x).toBeCloseTo(3)
    expect(result.patch?.location.y).toBeCloseTo(1.2)
    expect(result.patch?.location.z).toBeCloseTo(0.5)
  })

  it('refuses a name OpenFOAM cannot use as a function-object name', () => {
    expect(validateSensorEdit(store.getProject(), id, { name: 'cold aisle', x: 1, y: 1, z: 1 }))
      .toEqual({ ok: false, error: 'sensor.err_name_chars' })
    expect(validateSensorEdit(store.getProject(), id, { name: '', x: 1, y: 1, z: 1 }).error).toBe(
      'sensor.err_name_chars',
    )
  })

  it('refuses a name another probe already answers to', () => {
    const other = store.addSensorProbe(4, 5, 6) as string
    store.setSensorProbe(other, { name: 'rack_top' })
    expect(validateSensorEdit(store.getProject(), id, { name: 'rack_top', x: 1, y: 1, z: 1 })).toEqual(
      { ok: false, error: 'sensor.err_name_taken' },
    )
  })

  it('accepts a probe keeping its own name', () => {
    store.setSensorProbe(id, { name: 'rack_top' })
    expect(validateSensorEdit(store.getProject(), id, { name: 'rack_top', x: 1, y: 1, z: 1 }).ok).toBe(
      true,
    )
  })

  it('refuses a coordinate that is not a number', () => {
    expect(
      validateSensorEdit(store.getProject(), id, { name: 'ok_1', x: 'abc', y: 1, z: 1 }),
    ).toEqual({ ok: false, error: 'sensor.err_coord_invalid' })
  })

  it('writes the validated patch through the store setter that already accepts it', () => {
    const result = validateSensorEdit(store.getProject(), id, {
      name: 'inlet_A',
      x: 3000,
      y: 1200,
      z: 500,
    })
    store.setSensorProbe(id, result.patch!)
    const probe = store.sensorProbes(store.getProject()).find((p) => p.uid === id)
    expect(probe?.meta.name).toBe('inlet_A')
    expect(probe?.geometry.location.x).toBeCloseTo(3)
  })
})

describe('sensorNameTaken', () => {
  it('ignores the probe being renamed', () => {
    loadProject('m')
    const a = store.addSensorProbe(0, 0, 0) as string
    expect(sensorNameTaken(store.getProject(), a, a)).toBe(false)
    expect(sensorNameTaken(store.getProject(), a, 'sensor_99')).toBe(true)
  })
})
