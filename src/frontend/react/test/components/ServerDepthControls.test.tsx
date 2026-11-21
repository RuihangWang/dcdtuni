import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { rackState, stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { EquipmentInspector } from '../../src/components/EquipmentInspector'
import { RackDetailPanel } from '../../src/components/RackDetailPanel'
import { mergeSlotsIntoEquipment } from '../../src/geometry/rackSlots'
import { t } from '../../src/i18n'

/**
 * A server's placement is TWO ABSOLUTE setbacks (spec 2026-09-17 §6): metres
 * from the rack inlet face and from the rack outlet face, stored on the server
 * itself. The panel edits exactly those two fields; the depth is derived and
 * read-only.
 *
 * What the drag protocol has to keep, from the rack-relative era:
 *
 * - ONE honest undo step per slider drag. The frames mutate the live project;
 *   the release restores the start and commits once, so the entry the undo
 *   stack holds is the state BEFORE the gesture.
 * - The trailing native `change` after `pointerup` must not re-arm the drag —
 *   that was the double-entry bug the value-tracker staleness provoked.
 * - Drag anchoring: dragging one slider holds the OTHER offset at its
 *   committed value rather than re-reading it per frame.
 * - A drag that ends where it began leaves no undo step and no residue.
 *
 * And the regression the whole decoupling exists for: a drag of the rack-detail
 * ROW sliders leaves every server field byte-identical.
 */

let host: HTMLDivElement
let root: Root
let rack: string
let server: string

/** The rack `createModel` builds is 1.2 m deep; §3.1 budgets 90% of it. */
const D = 1.2

type Servers = Record<
  string,
  { inletOffset?: number; outletOffset?: number; [key: string]: unknown }
>

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

function servers(): Servers {
  return (model().constructions as unknown as { servers: Servers }).servers
}

function offsets() {
  const s = servers()[server]!
  return { inletOffset: Number(s.inletOffset ?? 0), outletOffset: Number(s.outletOffset ?? 0) }
}

const r3 = (x: number) => Number(x.toFixed(3))

function render() {
  flushSync(() => root.render(<EquipmentInspector model={model()} equipmentId={server} />))
}

function input(id: string): HTMLInputElement {
  const el = document.getElementById(id) as HTMLInputElement | null
  expect(el, id).not.toBeNull()
  return el!
}

const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!

/** One frame of a thumb drag: the browser moves the value and fires `input`. */
function frame(id: string, value: number) {
  const el = input(id)
  setValue.call(el, String(value))
  flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
  // The app re-renders on the live edit's notify; the harness does it by hand.
  render()
}

function pointer(type: string): Event {
  return typeof PointerEvent === 'function'
    ? new PointerEvent(type, { bubbles: true })
    : new Event(type, { bubbles: true })
}

/**
 * The release as Chromium delivers it: pointerup, then the trailing native
 * `change`, then focus leaving the slider. The `change` is made to look like a
 * value change to React (a stale value tracker), which is what the float noise
 * did in the browser.
 */
function release(id: string) {
  let el = input(id)
  flushSync(() => el.dispatchEvent(pointer('pointerup')))
  render()
  el = input(id)
  const tracker = (el as unknown as { _valueTracker?: { setValue(v: string): void } })._valueTracker
  tracker?.setValue('stale')
  flushSync(() => el.dispatchEvent(new Event('change', { bubbles: true })))
  render()
  el = input(id)
  flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
  render()
}

/**
 * The fixture: one 2U fan server at U11-U12 parked in the FRONT half of the
 * rack — 距 inlet 0.1 m, 距 outlet 0.7 m, so its box is z 0.1 -> 0.5 and the
 * outlet slider has room to travel either way.
 */
beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, created)
  rack = created.uid
  mergeSlotsIntoEquipment(rack, 'front', [10, 11], 'fanServer')
  server = Object.keys(servers())[0]!
  store.mutateLive((project) => {
    const m = store.findConstruction(project, rack)!.model
    rackState().setServerDepthAbsolute(m, server, 0.1, 0.7)
    rackState().syncRackModelState(m)
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the absolute editors', () => {
  it('show the two stored setbacks and the derived depth read-only', () => {
    expect(offsets()).toEqual({ inletOffset: 0.1, outletOffset: 0.7 })
    const inlet = input('eq-inlet-offset-slider')
    expect(inlet.type).toBe('range')
    expect(inlet.min).toBe('0')
    // §3.1: inlet + outlet <= 0.9 * D, so the inlet slider tops out at
    // 1.08 - 0.7 = 0.38 with this outlet committed.
    expect(Number(inlet.max)).toBeCloseTo(0.38, 3)
    expect(inlet.step).toBe('0.001')
    expect(Number(inlet.value)).toBeCloseTo(0.1, 3)
    expect(Number(input('eq-outlet-offset-slider').value)).toBeCloseTo(0.7, 3)
    // Depth is DERIVED now: a readout, not an editable box.
    expect(document.getElementById('eq-depth-val')?.textContent).toBe(`${(D - 0.8).toFixed(3)} m`)
    expect(document.getElementById('eq-depth')).toBeNull()
    expect(document.getElementById('eq-depth-hint')).not.toBeNull()
    // Both editors address these by id.
    expect(input('eq-inlet-offset-slider').closest('.prop-group')).toBe(
      input('eq-inlet-offset').closest('.prop-group'),
    )
  })

  it('refuses nothing legal: a mid-range drag of each slider lands exactly', () => {
    frame('eq-inlet-offset-slider', 0.25)
    expect(offsets()).toEqual({ inletOffset: 0.25, outletOffset: 0.7 })
    release('eq-inlet-offset-slider')
    frame('eq-outlet-offset-slider', 0.5)
    expect(offsets()).toEqual({ inletOffset: 0.25, outletOffset: 0.5 })
    release('eq-outlet-offset-slider')
  })
})

describe('the inlet slider', () => {
  it('holds the outlet offset while it moves — the drag is anchored', () => {
    const start = offsets()
    frame('eq-inlet-offset-slider', 0.3)
    expect(offsets()).toEqual({ inletOffset: 0.3, outletOffset: start.outletOffset })
    expect(document.getElementById('eq-inlet-offset-val')?.textContent).toBe('0.300 m')
    release('eq-inlet-offset-slider')
    expect(offsets()).toEqual({ inletOffset: 0.3, outletOffset: start.outletOffset })
  })

  it('is ONE undo step per drag, and undo puts the start back', () => {
    const start = offsets()
    const before = store.undoDepth()
    for (const v of [0.15, 0.2, 0.28, 0.32]) frame('eq-inlet-offset-slider', v)
    const dragged = offsets()
    release('eq-inlet-offset-slider')
    expect(store.undoDepth() - before).toBe(1)
    expect(store.getLog().filter((e) => e.action === 'rackDetailServerDepth')).toHaveLength(1)
    expect(offsets()).toEqual(dragged)
    store.undo()
    expect(offsets()).toEqual(start)
  })

  it('commits the drag when the panel unmounts mid-drag, still as one step', () => {
    const start = offsets()
    const before = store.undoDepth()
    frame('eq-inlet-offset-slider', 0.05)
    const dragged = offsets()
    flushSync(() => root.render(<div />))
    expect(store.undoDepth() - before).toBe(1)
    expect(offsets()).toEqual(dragged)
    store.undo()
    expect(offsets()).toEqual(start)
  })

  it('a drag that ends where it began leaves no undo step and no residue', () => {
    const start = offsets()
    const before = store.undoDepth()
    frame('eq-inlet-offset-slider', 0.3)
    frame('eq-inlet-offset-slider', 0.1)
    release('eq-inlet-offset-slider')
    expect(store.undoDepth() - before).toBe(0)
    expect(offsets()).toEqual(start)
  })
})

describe('the outlet slider', () => {
  it('is ONE undo step per drag, and undo puts the start back', () => {
    const start = offsets()
    const before = store.undoDepth()
    for (const v of [0.6, 0.5, 0.42]) frame('eq-outlet-offset-slider', v)
    const dragged = offsets()
    expect(offsets().inletOffset).toBeCloseTo(start.inletOffset, 12)
    release('eq-outlet-offset-slider')
    expect(store.undoDepth() - before).toBe(1)
    expect(offsets()).toEqual(dragged)
    store.undo()
    expect(offsets()).toEqual(start)
  })

  it('carries no float noise in its value or range', () => {
    frame('eq-outlet-offset-slider', 0.553)
    const slider = input('eq-outlet-offset-slider')
    const decimals = (s: string | null) => (s?.split('.')[1] ?? '').length
    expect(decimals(slider.getAttribute('value'))).toBeLessThanOrEqual(3)
    expect(decimals(slider.max)).toBeLessThanOrEqual(3)
    release('eq-outlet-offset-slider')
  })

  it('refuses to intersect a server stacked behind, and says which', () => {
    // A switch at the same U's, recessed: z 0.55 -> 0.8.
    store.mutateLive((project) => {
      const m = store.findConstruction(project, rack)!.model
      const dict = (m.constructions as unknown as { servers: Servers }).servers
      dict['near'] = {
        uid: 'near',
        type: 'nonFan',
        geometry: { slotPosition: 10, slotOccupation: 2 },
        cooling: {},
        constructions: { chips: {}, coldPlates: {} },
        meta: { name: 'near', enabled: true },
        inletOffset: 0.55,
        outletOffset: 0.4,
      }
      rackState().syncRackModelState(m)
    })
    render()
    expect(Object.keys(servers()).sort()).toEqual(['near', server].sort())
    const start = offsets()
    const before = store.undoDepth()
    // Outlet 0.2 would stretch this server to z 0.1 -> 1.0, straight through
    // the switch: the frame is refused, nothing is written.
    frame('eq-outlet-offset-slider', 0.2)
    expect(offsets()).toEqual(start)
    expect(document.getElementById('eq-depth-error')?.textContent).toBe(
      t('error.server_depth_overlap'),
    )
    release('eq-outlet-offset-slider')
    // A refused drag commits nothing: no undo entry, no residue.
    expect(store.undoDepth() - before).toBe(0)
    expect(offsets()).toEqual(start)
  })
})

describe('the number boxes', () => {
  it('stay one edit each and write the absolute field', () => {
    const before = store.undoDepth()
    const el = input('eq-inlet-offset')
    setValue.call(el, '0.3')
    flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
    flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
    render()
    expect(store.undoDepth() - before).toBe(1)
    expect(offsets()).toEqual({ inletOffset: 0.3, outletOffset: 0.7 })
    expect(document.getElementById('eq-depth-val')?.textContent).toBe(
      `${r3(D - 1.0).toFixed(3)} m`,
    )
  })
})

describe('the row sliders do not move servers (the §6 regression)', () => {
  /** Every server record, field for field, in placement order. */
  const snapshot = () => JSON.stringify(servers())

  function renderPanel() {
    flushSync(() =>
      root.render(<RackDetailPanel project={store.getProject()} rack={model()} />),
    )
  }

  function rowFrame(id: string, percent: number) {
    const el = input(id)
    setValue.call(el, String(percent))
    flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
    renderPanel()
    flushSync(() => el.dispatchEvent(pointer('pointerup')))
    renderPanel()
  }

  it('leaves every server field byte-identical across inlet, outlet and both drags', () => {
    // A second server at a different depth, so "unchanged" covers a stack.
    store.mutateLive((project) => {
      const m = store.findConstruction(project, rack)!.model
      const dict = (m.constructions as unknown as { servers: Servers }).servers
      dict['near'] = {
        uid: 'near',
        type: 'nonFan',
        geometry: { slotPosition: 10, slotOccupation: 2 },
        cooling: {},
        constructions: { chips: {}, coldPlates: {} },
        meta: { name: 'near', enabled: true },
        inletOffset: 0.55,
        outletOffset: 0.4,
      }
      rackState().syncRackModelState(m)
    })
    renderPanel()
    const before = snapshot()
    expect(input('rd-slot-depth-decoupled').textContent).toBe(
      t('rack.slot_depth_decoupled_hint'),
    )
    rowFrame('rd-inlet-range', 20)
    rowFrame('rd-outlet-range', 15)
    rowFrame('rd-both-range', 5)
    expect(snapshot()).toBe(before)
    // The row itself DID move — the sliders are not dead, they just own the
    // blank-baffle / U-slot structure only.
    const m = model() as unknown as { inletOffset?: number; outletOffset?: number }
    expect(m.inletOffset).toBeCloseTo(0.06, 6)
    expect(m.outletOffset).toBeCloseTo(0.06, 6)
  })

  it('keeps the per-server editors absolute inside the detail panel too', () => {
    renderPanel()
    const el = input(`rd-server-${server}-inlet`)
    expect(Number(el.value)).toBeCloseTo(0.1, 3)
    const before = store.undoDepth()
    setValue.call(el, '0.2')
    flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
    flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
    renderPanel()
    expect(store.undoDepth() - before).toBe(1)
    expect(offsets()).toEqual({ inletOffset: 0.2, outletOffset: 0.7 })
    store.undo()
    expect(offsets()).toEqual({ inletOffset: 0.1, outletOffset: 0.7 })
  })
})
