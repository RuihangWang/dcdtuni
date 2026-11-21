import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { reviveKinds } from '../../src/legacy/cfdprojModules'
import * as store from '../../src/projectStore'
import { SensorPanel } from '../../src/components/SensorPanel'

/**
 * Renaming a probe, from the double-click that opens the dialog.
 *
 * `run/sensorEdit.ts` already has its own tests for the validation and the unit
 * conversion. What those cannot show is whether anything on screen REACHES
 * them: the dialog sat finished and unimported for the whole port, and a unit
 * test of the validator passed the entire time. So this test starts at the
 * gesture — a double-click on the name in the list — and ends at the document.
 */

const PROJECT = resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj')

let host: HTMLDivElement
let root: Root

function render() {
  flushSync(() => root.render(<SensorPanel project={store.getProject()} />))
}

/** A double-click React will act on — `dblclick` alone does not reach onDoubleClick. */
function doubleClick(el: Element) {
  flushSync(() => {
    el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
  })
}

/** Type into a controlled input the way React's onChange sees it. */
function type(id: string, value: string) {
  const el = host.ownerDocument.getElementById(id) as HTMLInputElement
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
}

function click(id: string) {
  const el = host.ownerDocument.getElementById(id) as HTMLElement
  flushSync(() => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

beforeEach(() => {
  store.loadProject(
    reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject),
    '/cases/Base_leakage.cfdproj',
  )
  // Base_leakage ships with probes of its own, so the panel has a row to
  // double-click without one being added here.
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

function probe() {
  return store.sensorProbes(store.getProject())[0]!
}

/**
 * The dialog is PORTALLED to `body` — the right panel's `backdrop-filter`
 * would otherwise make it the containing block for `position: fixed`. So it is
 * never under `host`, and looking for it there would report "no dialog" for a
 * dialog that is on screen.
 */
function dialog(): HTMLElement | null {
  return document.body.querySelector('#sensor-edit-overlay')
}

describe('renaming a probe from the sensor list', () => {
  it('has no dialog until the name is double-clicked', () => {
    expect(dialog()).toBeNull()
    doubleClick(host.querySelector('.sensor-name')!)
    expect(dialog()).not.toBeNull()
  })

  it('writes the new name into the document', () => {
    doubleClick(host.querySelector('.sensor-name')!)
    type('sensor-edit-name', 'cold_aisle_top')
    click('sensor-edit-apply')

    expect(probe().meta.name).toBe('cold_aisle_top')
    // The dialog closes on success — left open, a second apply would run
    // against a name that is now taken by the probe itself.
    expect(dialog()).toBeNull()
    // The list row follows, once the panel is handed the new project — it reads
    // probes from the `project` prop, which App re-supplies on every store
    // change rather than the panel subscribing on its own.
    render()
    expect(host.querySelector('.sensor-name')!.textContent).toBe('cold_aisle_top')
  })

  it('refuses a name OpenFOAM cannot use, and keeps the dialog open', () => {
    const before = probe().meta.name
    doubleClick(host.querySelector('.sensor-name')!)
    type('sensor-edit-name', 'cold aisle')
    click('sensor-edit-apply')

    expect(probe().meta.name).toBe(before)
    expect(dialog()).not.toBeNull()
    expect(dialog()!.querySelector('#sensor-edit-err')!.textContent).not.toBe('')
  })

  it('cancel changes nothing', () => {
    const before = probe().meta.name
    doubleClick(host.querySelector('.sensor-name')!)
    type('sensor-edit-name', 'discarded')
    click('sensor-edit-cancel')

    expect(probe().meta.name).toBe(before)
    expect(dialog()).toBeNull()
  })

  it('typed coordinates land in metres, and undo takes them back', () => {
    // The project is authored in metres, so 4.5 typed is 4.5 stored. The
    // millimetre case — where they differ — is `sensorEdit.test.ts`'s.
    const before = probe().geometry.location.x
    doubleClick(host.querySelector('.sensor-name')!)
    type('sensor-edit-x', '4.5')
    click('sensor-edit-apply')

    expect(probe().geometry.location.x).toBe(4.5)
    store.undo()
    expect(probe().geometry.location.x).toBe(before)
  })
})
