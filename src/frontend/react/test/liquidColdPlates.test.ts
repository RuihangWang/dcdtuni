import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  addCpCuPair,
  addPair,
  applyCpCuPreset,
  applyPreset,
  COLD_PLATE_PRESETS,
  CP_DEFAULTS,
  normalizePair,
  readPairs,
  readRawPairs,
  removeCpCuPair,
  removePair,
  updateCpCuPair,
  updatePair,
  type RawPair,
} from '../src/liquid/liquidColdPlates'
import * as store from '../src/projectStore'

function project(pairs?: RawPair[]): CfdProject {
  return {
    name: 'T',
    constructions: {
      racks: {
        r1: {
          uid: 'r1',
          meta: { name: 'Rack_1', enabled: true },
          cooling: { type: 'liquid' },
          constructions: {
            servers: {
              e1: {
                uid: 'e1',
                type: 'liquidServer',
                meta: { name: 'srv_1' },
                ...(pairs ? { liquidCooling: { cpCuPairs: pairs } } : {}),
              },
            },
          },
        },
      },
    },
  } as unknown as CfdProject
}

function storedPairs(): RawPair[] {
  const rack = store.getProject()?.constructions?.racks?.r1
  const servers = (rack?.constructions as { servers?: Record<string, Record<string, unknown>> })
    ?.servers
  return readRawPairs(servers?.e1)
}

describe('COLD_PLATE_PRESETS', () => {
  it('carries the legacy numbers, which the compiler turns into edge params', () => {
    // liquid_graphgen.js:509-513 derives UA = 1/R_th and dp_a from
    // dp_rated_kPa / Q_rated_LPM, so a wrong number here is a wrong network,
    // not a wrong label.
    expect(COLD_PLATE_PRESETS.cpu).toEqual({
      Q_W: 350,
      R_th: 0.03,
      dp_rated_kPa: 10,
      Q_rated_LPM: 1.0,
      C_solid: 100,
      V_fluid_mL: 5,
    })
    expect(COLD_PLATE_PRESETS.gpu).toEqual({
      Q_W: 700,
      R_th: 0.01,
      dp_rated_kPa: 20,
      Q_rated_LPM: 2.0,
      C_solid: 150,
      V_fluid_mL: 10,
    })
  })

  it('fills a missing field with the same default the compiler assumes', () => {
    expect(CP_DEFAULTS).toEqual(COLD_PLATE_PRESETS.cpu)
  })
})

describe('normalizePair', () => {
  it('names an unnamed pair by its 1-based position', () => {
    expect(normalizePair({}, 0).name).toBe('CP-CU_1')
    expect(normalizePair({}, 2).name).toBe('CP-CU_3')
  })

  it('migrates the pre-rename heatLoad into Q_W', () => {
    expect(normalizePair({ heatLoad: 500 }, 0).Q_W).toBe(500)
  })

  it('prefers Q_W when a saved project carries both', () => {
    // Real projects have both: the legacy wrote Q_W beside the old heatLoad
    // and never removed it, and the compiler reads Q_W first.
    expect(normalizePair({ heatLoad: 350, Q_W: 900 }, 0).Q_W).toBe(900)
  })

  it('reads an unknown preset as custom', () => {
    expect(normalizePair({ preset: 'tpu' }, 0).preset).toBe('custom')
    expect(normalizePair({}, 0).preset).toBe('custom')
    expect(normalizePair({ preset: 'gpu' }, 0).preset).toBe('gpu')
  })

  it('does not treat a null or a string as a number', () => {
    const p = normalizePair({ R_th: null, C_solid: '120' }, 0)
    expect(p.R_th).toBe(0.03)
    expect(p.C_solid).toBe(100)
  })
})

describe('reducers', () => {
  it('adds a CPU-preset pair named after its position', () => {
    const one = addPair([])
    expect(one).toEqual([{ uid: 'CP-CU_1', name: 'CP-CU_1', preset: 'cpu', ...COLD_PLATE_PRESETS.cpu }])
    expect(addPair(one)[1]?.name).toBe('CP-CU_2')
  })

  it('removes by index and leaves the rest in order', () => {
    const three = [{ name: 'a' }, { name: 'b' }, { name: 'c' }]
    expect(removePair(three, 1).map((p) => p.name)).toEqual(['a', 'c'])
    // Out of range is a no-op, not a truncation.
    expect(removePair(three, 9)).toBe(three)
  })

  it('re-types a pair as custom when a NUMBER is edited', () => {
    const pairs = addPair([])
    const edited = updatePair(pairs, 0, 'Q_W', 900)
    expect(edited[0]?.Q_W).toBe(900)
    expect(edited[0]?.preset).toBe('custom')
  })

  it('keeps the preset when only the name is edited', () => {
    const edited = updatePair(addPair([]), 0, 'name', 'GPU0')
    expect(edited[0]?.preset).toBe('cpu')
    expect(edited[0]?.name).toBe('GPU0')
  })

  it('never mutates the array it was given', () => {
    const pairs = addPair([])
    const before = JSON.stringify(pairs)
    updatePair(pairs, 0, 'Q_W', 900)
    applyPreset(pairs, 0, 'gpu')
    removePair(pairs, 0)
    expect(JSON.stringify(pairs)).toBe(before)
  })

  it('stamps all six numbers when a preset is picked', () => {
    const edited = applyPreset(updatePair(addPair([]), 0, 'Q_W', 900), 0, 'gpu')
    expect(edited[0]).toMatchObject({ preset: 'gpu', ...COLD_PLATE_PRESETS.gpu })
  })

  it('leaves the numbers alone when the preset is set to custom', () => {
    const gpu = applyPreset(addPair([]), 0, 'gpu')
    const custom = applyPreset(gpu, 0, 'custom')
    expect(custom[0]?.Q_W).toBe(COLD_PLATE_PRESETS.gpu.Q_W)
    expect(custom[0]?.preset).toBe('custom')
  })

  it('preserves fields it does not know about', () => {
    // A saved pair carries `heatLoad`, and an edge_id may be added later.
    // Dropping either on edit would silently change what the compiler reads.
    const pairs: RawPair[] = [{ name: 'a', heatLoad: 350, Q_W: 350 }]
    expect(updatePair(pairs, 0, 'R_th', 0.02)[0]?.heatLoad).toBe(350)
    expect(applyPreset(pairs, 0, 'gpu')[0]?.heatLoad).toBe(350)
  })
})

describe('store actions', () => {
  beforeEach(() => store.loadProject(project(), '/tmp/T.cfdproj'))

  it('creates the liquidCooling bag on the first add', () => {
    addCpCuPair('r1', 'e1')
    expect(storedPairs()).toHaveLength(1)
    expect(storedPairs()[0]?.preset).toBe('cpu')
  })

  it('is undoable, one step per action', () => {
    addCpCuPair('r1', 'e1')
    addCpCuPair('r1', 'e1')
    updateCpCuPair('r1', 'e1', 1, 'Q_W', 900)
    expect(storedPairs()[1]?.Q_W).toBe(900)
    store.undo()
    expect(storedPairs()[1]?.Q_W).toBe(350)
    store.undo()
    expect(storedPairs()).toHaveLength(1)
  })

  it('removes the addressed pair only', () => {
    store.loadProject(project([{ name: 'a' }, { name: 'b' }, { name: 'c' }]), null)
    removeCpCuPair('r1', 'e1', 1)
    expect(storedPairs().map((p) => p.name)).toEqual(['a', 'c'])
  })

  it('applies a preset through the store', () => {
    addCpCuPair('r1', 'e1')
    applyCpCuPreset('r1', 'e1', 0, 'gpu')
    expect(readPairs(
      ((store.getProject()?.constructions?.racks?.r1?.constructions as {
        servers?: Record<string, Record<string, unknown>>
      })?.servers ?? {}).e1,
    )[0]).toMatchObject({ preset: 'gpu', Q_W: 700 })
  })

  it('does nothing for an unknown server instead of throwing', () => {
    addCpCuPair('r1', 'nope')
    updateCpCuPair('nope', 'e1', 0, 'Q_W', 1)
    expect(storedPairs()).toHaveLength(0)
  })
})
