import { beforeEach, describe, expect, it } from 'vitest'
import {
  acuPerformanceMode,
  acuTable,
  canRemoveTableRow,
  drivingValue,
  modelDrivingValue,
  nextTableRow,
  setAcuDrivingValue,
  setAcuPerformanceMode,
  setAcuTable,
  supplyPatches,
} from '../src/geometry/acuCoolingModel'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'

/** An ACU with two supply openings and one return, the shape the mirror needs. */
function project(): CfdProject {
  return {
    name: 'T',
    constructions: {
      acus: {
        m_1: {
          uid: 'm_1',
          meta: { name: 'ACU_1', enabled: true },
          geometry: {
            location: { x: 0, y: 0, z: 0 },
            size: { x: 1.2, y: 2, z: 0.6 },
            faces: {
              front: {
                patches: [
                  { id: 'p_1', physics: { role: 'supply', temperature: 16, volumeFlowRate: 4 } },
                  { id: 'p_2', physics: { role: 'supply', temperature: 16, volumeFlowRate: 2 } },
                ],
              },
              top: {
                patches: [{ id: 'p_3', physics: { role: 'return', volumeFlowRate: 0 } }],
              },
              back: { patches: [{ id: 'p_4', physics: { role: 'wall' } }] },
            },
          },
        },
      },
    },
  } as unknown as CfdProject
}

function acu(): Construction {
  return store.getProject()!.constructions!.acus!.m_1!
}

function physicsOf(patchId: string): Record<string, unknown> {
  const faces = (acu().geometry as { faces: Record<string, { patches: { id: string; physics: Record<string, unknown> }[] }> }).faces
  for (const face of Object.values(faces)) {
    const hit = face.patches.find((p) => p.id === patchId)
    if (hit) return hit.physics
  }
  throw new Error(`no patch ${patchId}`)
}

beforeEach(() => store.loadProject(project(), '/tmp/T.cfdproj'))

describe('mode', () => {
  it('defaults to fixedTemp and rejects anything unknown', () => {
    expect(acuPerformanceMode(acu())).toBe('fixedTemp')
    setAcuPerformanceMode('m_1', 'nonsense')
    expect(acuPerformanceMode(acu())).toBe('fixedTemp')
  })

  it('stores the two capacity modes', () => {
    setAcuPerformanceMode('m_1', 'linearCapacity')
    expect(acuPerformanceMode(acu())).toBe('linearCapacity')
  })
})

describe('supplyPatches', () => {
  it('finds only the supply openings', () => {
    expect(supplyPatches(acu()).map((p) => p.patchId)).toEqual(['p_1', 'p_2'])
  })
})

describe('drivingValue', () => {
  it('falls back to the SUPPLY temperature for the set point', () => {
    // A unit switched off fixedTemp was already told what to blow; that is the
    // temperature it is trying to hold, not an arbitrary 22.
    expect(modelDrivingValue(acu(), 'setPointTemperature')).toBe(16)
  })

  it('uses 22 when there is no supply temperature either', () => {
    expect(drivingValue({}, 'setPointTemperature')).toBe(22)
  })

  it('uses the legacy defaults for the three rated inputs', () => {
    expect(modelDrivingValue(acu(), 'coolingCapacity')).toBe(35)
    expect(modelDrivingValue(acu(), 'ratedReturnTemp')).toBe(35)
    expect(modelDrivingValue(acu(), 'coolantTemperature')).toBe(14)
  })

  it('prefers a stored value over any fallback', () => {
    setAcuDrivingValue('m_1', 'coolingCapacity', 48)
    expect(modelDrivingValue(acu(), 'coolingCapacity')).toBe(48)
  })
})

describe('setAcuDrivingValue', () => {
  it('writes every supply patch when no patch is named, and no others', () => {
    setAcuDrivingValue('m_1', 'coolantTemperature', 12)
    expect(physicsOf('p_1').coolantTemperature).toBe(12)
    expect(physicsOf('p_2').coolantTemperature).toBe(12)
    expect(physicsOf('p_3').coolantTemperature).toBeUndefined()
    expect(physicsOf('p_4').coolantTemperature).toBeUndefined()
  })

  it('writes one patch when it is named', () => {
    setAcuDrivingValue('m_1', 'setPointTemperature', 21, 'p_2')
    expect(physicsOf('p_1').setPointTemperature).toBeUndefined()
    expect(physicsOf('p_2').setPointTemperature).toBe(21)
  })

  it('mirrors the return flow in the SAME edit', () => {
    // The exporter reads the stored return flow; one undo step behind the
    // supply it is derived from is a wrong boundary condition in the .cfdrun.
    setAcuDrivingValue('m_1', 'coolingCapacity', 50)
    expect(physicsOf('p_3').volumeFlowRate).toBe(6)
  })

  it('is one undo step however many openings it touched', () => {
    setAcuDrivingValue('m_1', 'ratedReturnTemp', 30)
    store.undo()
    expect(physicsOf('p_1').ratedReturnTemp).toBeUndefined()
    expect(physicsOf('p_2').ratedReturnTemp).toBeUndefined()
  })

  it('ignores a value that is not a number', () => {
    setAcuDrivingValue('m_1', 'coolingCapacity', Number.NaN)
    expect(store.canUndo()).toBe(false)
  })
})

describe('performance table', () => {
  it('starts from the three-point default without writing it into the project', () => {
    expect(acuTable(acu())).toEqual([
      { returnTemp: 25, capacity: 20 },
      { returnTemp: 30, capacity: 30 },
      { returnTemp: 35, capacity: 35 },
    ])
    expect((acu() as unknown as { coolingPerformanceTable?: unknown }).coolingPerformanceTable)
      .toBeUndefined()
  })

  it('continues the curve rather than repeating its end', () => {
    // Two points at the same return temperature make the lookup ambiguous.
    expect(nextTableRow(acuTable(acu()))).toEqual({ returnTemp: 40, capacity: 40 })
    expect(nextTableRow([])).toEqual({ returnTemp: 30, capacity: 30 })
  })

  it('refuses to drop below two points', () => {
    expect(canRemoveTableRow(acuTable(acu()))).toBe(true)
    expect(canRemoveTableRow([{ returnTemp: 25, capacity: 20 }, { returnTemp: 30, capacity: 30 }]))
      .toBe(false)
  })

  it('stores an edited table', () => {
    setAcuTable('m_1', [{ returnTemp: 22, capacity: 18 }, { returnTemp: 32, capacity: 33 }])
    expect(acuTable(acu())).toEqual([
      { returnTemp: 22, capacity: 18 },
      { returnTemp: 32, capacity: 33 },
    ])
  })
})
