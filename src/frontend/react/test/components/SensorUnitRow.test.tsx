import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import * as store from '../../src/projectStore'
import { SensorPanel } from '../../src/components/SensorPanel'
import { formatSensorLocation } from '../../src/run/sensorEdit'
import { LENGTH_UNITS } from '../../src/lengthUnits'

/**
 * The probe entry row is in the project's input unit, like every other box.
 *
 * `meta.inputUnit` is a property of the WORK — a room authored from datasheets
 * in millimetres is typed in millimetres — and every geometry box in both apps
 * follows it. The sensor row was the one place that did not: it hard-coded the
 * label `m`, stepped by 0.0001 whatever the unit, and passed what was typed
 * straight to `addSensorProbe`, whose arguments are METRES. So on a mm project,
 * typing a probe at 3000 mm put it 3 km away, and the list read it back as
 * `3000.00` with no unit to say what that meant.
 *
 * The readout is the legacy's `_formatSensorCoordText` (STL_generate.html:23206)
 * — one helper for the row and for the live drag overlay, so the two cannot
 * disagree mid-drag.
 */

let host: HTMLDivElement
let root: Root

function loadWithUnit(unit: 'm' | 'mm' | 'cm') {
  const project = stateV4().normalizeStateV4(
    stateV4().getInitialStateV4(),
  ) as unknown as CfdProject
  ;(project.meta as unknown as { inputUnit: string }).inputUnit = unit
  store.loadProject(project, null)
}

function render() {
  flushSync(() => root.render(<SensorPanel project={store.getProject()} />))
}

function type(id: string, value: string) {
  const el = document.getElementById(id) as HTMLInputElement
  expect(el, id).not.toBeNull()
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
  // `blur` does not bubble and React 18 delegates at the root.
  flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
}

function add(x: string, y: string, z: string) {
  type('sensor-input-x', x)
  type('sensor-input-y', y)
  type('sensor-input-z', z)
  flushSync(() =>
    document.getElementById('sensor-add')!.dispatchEvent(new MouseEvent('click', { bubbles: true })),
  )
  render()
}

function probes() {
  return store.sensorProbes(store.getProject())
}

function rowText(): string {
  const el = document.querySelector('#sensor-list [data-sensor-coord-id]')
  expect(el).not.toBeNull()
  return el!.textContent ?? ''
}

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('on a millimetre project', () => {
  beforeEach(() => {
    loadWithUnit('mm')
    render()
  })

  it('labels the row mm', () => {
    expect(document.getElementById('sensor-input-unit')!.textContent).toBe('mm')
  })

  it('steps in millimetres, not in metre ten-thousandths', () => {
    const step = (document.getElementById('sensor-input-x') as HTMLInputElement).step
    expect(Number(step)).toBe(LENGTH_UNITS.mm.step)
  })

  it('stores what was typed as METRES', () => {
    add('3000', '1200', '500')
    // 3000 mm is three metres from the origin, not three kilometres.
    expect(probes()[0]!.geometry.location).toEqual({ x: 3, y: 1.2, z: 0.5 })
  })

  it('reads the row back in the same unit, with the unit on it', () => {
    add('3000', '1200', '500')
    expect(rowText()).toBe(
      formatSensorLocation({ x: 3, y: 1.2, z: 0.5 }, 'mm'),
    )
    expect(rowText()).toContain('mm')
  })
})

describe('on a metre project', () => {
  beforeEach(() => {
    loadWithUnit('m')
    render()
  })

  it('is unchanged: what is typed is what is stored', () => {
    add('3', '1.2', '0.5')
    expect(probes()[0]!.geometry.location).toEqual({ x: 3, y: 1.2, z: 0.5 })
    expect(document.getElementById('sensor-input-unit')!.textContent).toBe('m')
    expect(rowText()).toBe(formatSensorLocation({ x: 3, y: 1.2, z: 0.5 }, 'm'))
  })
})
