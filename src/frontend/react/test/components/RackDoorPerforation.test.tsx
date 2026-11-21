import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { perforatedK } from '../../src/geometry/patchPhysics'
import { enterRackDetail, findDoor, setDoorEnabled } from '../../src/geometry/rackDetail'
import { RackDetailPanel } from '../../src/components/RackDetailPanel'

/**
 * A rack door's hole SHAPE, and the K it implies.
 *
 * The rack-detail panel hand-copied two of `PerforatedEditor`'s four fields —
 * open area and hole diameter — and left out the shape select and the K
 * readout. `shape` is not cosmetic: it is exported into the .cfdrun and changes
 * the door's pressure-loss coefficient, so every rack door in this app was a
 * circle-hole door whatever the real cabinet had, and nothing on screen said
 * so. The legacy calls the shared inspector here (STL_generate.html:9214).
 *
 * Both doors are on screen at once, so the ids have to differ — a duplicate id
 * makes a label point at the wrong control.
 */

let host: HTMLDivElement
let root: Root
let uid: string

function rack(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, uid)!.model
}

/** A door is a `role: 'door'` entry in the rack's FACES array — see `findDoor`. */
function perforation(side: 'inlet' | 'outlet'): Record<string, unknown> {
  const door = findDoor(rack(), side) as unknown as { perforation?: Record<string, unknown> } | null
  return door?.perforation ?? {}
}

function render() {
  flushSync(() => root.render(<RackDetailPanel project={store.getProject()} rack={rack()} />))
}

// `CSS.escape` is not in jsdom, and these ids need no escaping anyway.
function el<T extends HTMLElement>(id: string): T {
  return host.querySelector(`#${id}`) as T
}

function choose(select: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
  setter.call(select, value)
  flushSync(() => select.dispatchEvent(new Event('change', { bubbles: true })))
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const { bucket, model } = createModel(store.getProject() as CfdProject, 'rack')
  uid = store.addModel(bucket, model)
  // The panel only draws for the rack the app has entered, and the perforation
  // block only for a door that exists.
  enterRackDetail(uid)
  setDoorEnabled(uid, 'inlet', true)
  setDoorEnabled(uid, 'outlet', true)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('a rack door’s perforation', () => {
  it('offers the hole shape on both doors, with distinct ids', () => {
    for (const side of ['inlet', 'outlet'] as const) {
      const select = el<HTMLSelectElement>(`rd-door-${side}-shape`)
      expect(select).not.toBeNull()
      expect([...select.options].map((o) => o.value)).toEqual(['circle', 'rect', 'diamond', 'hexagon'])
    }
  })

  it('writes the shape into the door, where the exporter reads it', () => {
    expect(perforation('inlet').shape ?? 'circle').toBe('circle')
    choose(el<HTMLSelectElement>('rd-door-inlet-shape'), 'diamond')
    expect(perforation('inlet').shape).toBe('diamond')
  })

  it('leaves the OTHER door alone', () => {
    choose(el<HTMLSelectElement>('rd-door-inlet-shape'), 'rect')
    render()
    expect(perforation('outlet').shape ?? 'circle').toBe('circle')
  })

  it('shows the K the shape implies, and moves it when the shape changes', () => {
    const before = el(`rd-door-inlet-k`).textContent
    expect(before).toBeTruthy()

    choose(el<HTMLSelectElement>('rd-door-inlet-shape'), 'diamond')
    render()
    const after = el(`rd-door-inlet-k`).textContent

    // The whole point of the shape control: a diamond and a circle of the same
    // open area are not the same resistance.
    expect(after).not.toBe(before)
    const perf = perforation('inlet')
    expect(after).toBe(
      perforatedK({
        shape: 'diamond',
        holeDiameterMm: Number(perf.holeDiameterMm ?? 3),
        openAreaPct: Number(perf.openAreaPct ?? 64),
      }).toFixed(2),
    )
  })

  it('keeps the two fields it already had', () => {
    expect(el('rd-door-inlet-diameter')).not.toBeNull()
    expect(el('rd-door-inlet-open-pct')).not.toBeNull()
  })
})
