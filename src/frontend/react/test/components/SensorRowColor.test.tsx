import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { SensorPanel } from '../../src/components/SensorPanel'
import { reviveKinds } from '../../src/legacy/cfdprojModules'
import * as store from '../../src/projectStore'
import { sensorColor } from '../../src/run/sensorSpheres'
import { SERIES_COLORS } from '../../src/run/solverChart'
import type { CfdProject } from '../../src/schema/cfdproj'

/**
 * A probe's row is the colour of its ball and of its curve.
 *
 * The monitor plots one curve per probe and the viewport draws one ball per
 * probe, both coloured from the same ten-entry palette by the probe's position
 * in the list. Without the same dot on the row there is nothing to read the
 * plot against: a rising curve is `sensor_3`, and which of the balls in the
 * room that is has to be worked out by elimination. The legacy prepends an 8 px
 * dot to every row for exactly this (STL_generate.html:23267-23271).
 *
 * So the assertion that matters is not "there is a dot" but "it is the SAME
 * colour the other two use" — which is why this test reaches for both
 * `sensorColor` (the 3D balls) and `SERIES_COLORS` (the chart legend).
 */

const PROJECT = resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj')

let host: HTMLDivElement
let root: Root

function render() {
  flushSync(() => root.render(<SensorPanel project={store.getProject()} />))
}

/** jsdom normalises an inline colour to `rgb(r, g, b)`. */
function rgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`
}

function rows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('#sensor-list .sensor-row'))
}

function dot(row: HTMLElement): HTMLElement | null {
  return row.querySelector<HTMLElement>('[data-sensor-dot]')
}

beforeEach(() => {
  store.loadProject(
    reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject),
    '/cases/Base_leakage.cfdproj',
  )
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the sensor list', () => {
  it('gives every row a dot', () => {
    const list = rows()
    expect(list.length).toBeGreaterThan(0)
    for (const row of list) expect(dot(row)).not.toBeNull()
  })

  it('colours it exactly as the 3D ball is coloured', () => {
    rows().forEach((row, i) => {
      expect(dot(row)!.style.background).toBe(rgb(sensorColor(i)))
    })
  })

  it('and exactly as the chart legend is coloured', () => {
    // Two modules hold this palette. If they ever drift, the dot stops meaning
    // anything and nothing else fails — so the drift is asserted here.
    rows().forEach((row, i) => {
      expect(dot(row)!.style.background).toBe(rgb(SERIES_COLORS[i % SERIES_COLORS.length]!))
    })
  })

  it('wraps round the palette past the tenth probe', () => {
    const before = rows().length
    for (let i = 0; i < SERIES_COLORS.length + 1 - before; i++) store.addSensorProbe(1, 1, 1)
    render()
    const list = rows()
    expect(list.length).toBe(SERIES_COLORS.length + 1)
    expect(dot(list[SERIES_COLORS.length]!)!.style.background).toBe(rgb(sensorColor(0)))
  })

  it('puts it first in the row, before the checkbox', () => {
    // The legacy row is dot, checkbox, name. A dot after the name reads as a
    // status light for the reading rather than an identity for the probe.
    const row = rows()[0]!
    expect(row.firstElementChild).toBe(dot(row))
  })
})
