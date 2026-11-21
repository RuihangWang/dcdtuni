import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import {
  getSensorResults,
  harvestSensorResults,
  onSolveFinished,
  openLatestSensorJson,
  parseSensorFile,
  probeNames,
  resetSensorResults,
  toKelvin,
} from '../src/run/sensorResults'

/**
 * The payload shapes here are the server's own: the POST body
 * `handle_harvest_sensor_results` reads (server.py:1521) and the JSON it writes
 * (:1598), including the `null` it puts where OpenFOAM wrote its
 * "point outside the mesh" sentinel.
 */

function project(probes: Record<string, unknown>): CfdProject {
  return {
    uid: 'p_sensors',
    name: 'T',
    constructions: {},
    instrumentation: { probes },
  } as unknown as CfdProject
}

const PROBE_A = {
  uid: 'sensor_1',
  geometry: { location: { x: 1, y: 2, z: 3 } },
  meta: { name: 'sensor_1', enabled: true },
}
const PROBE_B = {
  uid: 'sensor_2',
  geometry: { location: { x: 4, y: 5, z: 6 } },
  meta: { name: 'inlet_probe', enabled: false },
}

const SENSOR_FILE = {
  version: '1.0',
  caseName: 'case',
  finalStep: 960,
  unitTemperature: 'C' as const,
  sensors: [
    { name: 'sensor_1', location: [2.15, 3.1, 0.89], T_final: 16.199, U_final: [0.1, 0, -0.2], p_final: 1.5 },
    { name: 'inlet_probe', location: [2.28, 1.88, 2.99], T_final: null, U_final: null, p_final: null },
  ],
}

let calls: { url: string; init?: RequestInit }[] = []

function stubFetch(handler: (url: string, init?: RequestInit) => unknown, ok = true) {
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    const body = handler(url, init)
    return Promise.resolve({ ok, status: ok ? 200 : 500, json: () => Promise.resolve(body) })
  })
}

beforeEach(() => {
  calls = []
  resetSensorResults()
  store.loadProject(project({ sensor_1: PROBE_A, sensor_2: PROBE_B }), null)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('toKelvin', () => {
  it('inverts each of the three units the file can be written in', () => {
    expect(toKelvin(293.15, 'K')).toBe(293.15)
    expect(toKelvin(20, 'C')).toBeCloseTo(293.15, 10)
    expect(toKelvin(68, 'F')).toBeCloseTo(293.15, 10)
  })
})

describe('probeNames', () => {
  it('takes every probe in project order, disabled ones included', () => {
    // The server matches names to probe COLUMNS by position, and the case was
    // written with every probe: dropping the disabled one shifts every reading
    // after it onto the wrong sensor.
    expect(probeNames()).toEqual(['sensor_1', 'inlet_probe'])
  })
})

describe('parseSensorFile', () => {
  it('reads the rows and keeps the file unit', () => {
    const { unit, readings } = parseSensorFile(SENSOR_FILE)
    expect(unit).toBe('C')
    expect(readings[0]).toEqual({
      name: 'sensor_1',
      location: [2.15, 3.1, 0.89],
      tFinal: 16.199,
      uFinal: [0.1, 0, -0.2],
      pFinal: 1.5,
    })
  })

  it('keeps a null reading null instead of turning it into zero', () => {
    // null means the probe sat OUTSIDE the mesh. Zero would render as -273.15
    // degrees on a chart and read as a solver fault.
    expect(parseSensorFile(SENSOR_FILE).readings[1]?.tFinal).toBeNull()
  })

  it('defaults to Celsius when the file does not say', () => {
    expect(parseSensorFile({ sensors: [] }).unit).toBe('C')
  })
})

describe('harvestSensorResults', () => {
  it('posts the probe names in order, then reads the file back', async () => {
    stubFetch((url) =>
      url.startsWith('/api/harvest')
        ? { path: '/case/log/run/sensorResults/case_sensor_11-22-33.json' }
        : { ok: true, content: JSON.stringify(SENSOR_FILE) },
    )
    const path = await harvestSensorResults(['sensor_1', 'inlet_probe'], 'C')

    expect(path).toBe('/case/log/run/sensorResults/case_sensor_11-22-33.json')
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({
      caseName: 'case',
      sensorNames: ['sensor_1', 'inlet_probe'],
      unitTemperature: 'C',
    })
    expect(calls[1]?.url).toBe(
      '/api/read-file?path=%2Fcase%2Flog%2Frun%2FsensorResults%2Fcase_sensor_11-22-33.json',
    )
    expect(getSensorResults().readings).toHaveLength(2)
    expect(getSensorResults().busy).toBe(false)
  })

  it('keeps the server error instead of claiming a file was written', async () => {
    stubFetch(() => ({ error: 'No probe data found in run directory' }), false)
    expect(await harvestSensorResults(['sensor_1'], 'C')).toBeNull()
    expect(getSensorResults().error).toBe('No probe data found in run directory')
    expect(getSensorResults().path).toBeNull()
  })

  it('does not call the server when the project has no probes', async () => {
    stubFetch(() => ({}))
    store.loadProject(project({}), null)
    expect(await onSolveFinished()).toBeNull()
    expect(calls).toEqual([])
  })

  it('harvests with the project names when a solve finishes', async () => {
    stubFetch((url) =>
      url.startsWith('/api/harvest')
        ? { path: '/tmp/s.json' }
        : { ok: true, content: JSON.stringify(SENSOR_FILE) },
    )
    await onSolveFinished()
    expect(JSON.parse(String(calls[0]?.init?.body)).sensorNames).toEqual(['sensor_1', 'inlet_probe'])
  })
})

describe('openLatestSensorJson', () => {
  it('names the i18n key to show when no solve has written one', () => {
    expect(openLatestSensorJson()).toBe('sensor.no_saved_json')
  })

  it('opens the harvested path through /api/read-file', async () => {
    stubFetch((url) =>
      url.startsWith('/api/harvest')
        ? { path: '/case/log/r/sensorResults/case_sensor_01-02-03.json' }
        : { ok: true, content: JSON.stringify(SENSOR_FILE) },
    )
    await harvestSensorResults(['sensor_1'], 'C')
    const open = vi.fn()
    vi.stubGlobal('open', open)
    expect(openLatestSensorJson()).toBeNull()
    expect(open).toHaveBeenCalledWith(
      '/api/read-file?path=%2Fcase%2Flog%2Fr%2FsensorResults%2Fcase_sensor_01-02-03.json',
      '_blank',
    )
  })
})
