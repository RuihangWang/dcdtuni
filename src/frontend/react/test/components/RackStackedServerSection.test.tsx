import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { rackState, stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import {
  RackStackedServerSection,
  autoFitPlacement,
  freeDepthGaps,
  largestFreeDepthGap,
  overlapSegments,
} from '../../src/components/RackStackedServerSection'

/**
 * The add path for a second server at an occupied U range (spec §7.1/§7.3).
 *
 * The rules that render fine and export wrong if they slip:
 *
 * - the confirm button is DISABLED while the candidate's depth interval
 *   intersects a server covering the same slots — the blocking check is a dry
 *   run of the shared predicate, the same walk the normalizer drops by;
 * - "auto-fit into the largest free gap" centres the new box in the widest
 *   depth interval nothing occupies, at min(gap, 0.3 m) deep;
 * - touching faces are a legal stack, not an overlap (the predicate's epsilon).
 */

let host: HTMLDivElement
let root: Root
let rack = ''

/** The rack `createModel` builds is 1.2 m deep. */
const D = 1.2

type Servers = Record<string, Record<string, unknown>>

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

function servers(): Servers {
  return (model().constructions as unknown as { servers: Servers }).servers
}

/** Write server records straight into the rack, then normalise. */
function seedServers(
  seeds: { uid: string; startUnit: number; spanUnits?: number; inletOffset: number; outletOffset: number }[],
) {
  store.mutateLive((project) => {
    const m = store.findConstruction(project, rack)!.model
    const cons = m.constructions as unknown as { servers: Servers }
    const next: Servers = {}
    for (const s of seeds) {
      next[s.uid] = {
        uid: s.uid,
        type: 'fanServer',
        geometry: { slotPosition: s.startUnit, slotOccupation: s.spanUnits ?? 1 },
        cooling: {},
        constructions: { chips: {}, coldPlates: {} },
        meta: { name: s.uid, enabled: true, display: { opacity: 1 } },
        inletOffset: s.inletOffset,
        outletOffset: s.outletOffset,
      }
    }
    cons.servers = next
    rackState().syncRackModelState(m)
  })
}

function render(indices: number[] = [16, 17, 18, 19, 20]) {
  flushSync(() =>
    root.render(<RackStackedServerSection model={model()} indices={indices} />),
  )
}

function el(id: string): HTMLElement {
  const found = document.getElementById(id)
  expect(found, id).not.toBeNull()
  return found!
}

function inputEl(id: string): HTMLInputElement {
  const found = el(id) as HTMLInputElement
  return found
}

const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!

function click(id: string) {
  flushSync(() => el(id).dispatchEvent(new MouseEvent('click', { bubbles: true })))
  render()
}

/** Type metres into one of the form's number boxes and commit (blur). */
function typeOffset(side: 'inlet' | 'outlet', meters: string) {
  const input = inputEl(`rss-${side}`)
  setValue.call(input, meters)
  flushSync(() => input.dispatchEvent(new Event('input', { bubbles: true })))
  flushSync(() => input.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
  render()
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, created)
  rack = created.uid
  // A 5U fan server in the front half of the rack: z 0 -> 0.5.
  seedServers([{ uid: 'deep', startUnit: 16, spanUnits: 5, inletOffset: 0, outletOffset: 0.7 }])
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the pure depth-interval helpers', () => {
  it('complements the occupied intervals within [0, D]', () => {
    expect(freeDepthGaps([{ z0: 0, z1: 0.5 }], D)).toEqual([{ z0: 0.5, z1: D }])
    expect(freeDepthGaps([{ z0: 0.2, z1: 0.5 }], D)).toEqual([
      { z0: 0, z1: 0.2 },
      { z0: 0.5, z1: D },
    ])
    expect(freeDepthGaps([{ z0: 0, z1: D }], D)).toEqual([])
    expect(freeDepthGaps([], D)).toEqual([{ z0: 0, z1: D }])
  })

  it('picks the widest gap', () => {
    const gaps = [
      { z0: 0, z1: 0.1 },
      { z0: 0.6, z1: 0.9 },
    ]
    expect(largestFreeDepthGap([{ z0: 0.1, z1: 0.6 }, { z0: 0.9, z1: D }], D)).toEqual(gaps[1])
  })

  it('auto-fit centres a 0.3 m box in the largest gap, or the gap when narrower', () => {
    // Gap 0.5 -> 1.2 (0.7 wide): depth 0.3, centred at z0 0.7.
    expect(autoFitPlacement([{ z0: 0, z1: 0.5 }], D)).toEqual({
      inletOffset: 0.7,
      outletOffset: 0.2,
    })
    // Gap 0.2 wide: the box shrinks to the gap, centred == flush.
    const fit = autoFitPlacement(
      [
        { z0: 0, z1: 0.5 },
        { z0: 0.7, z1: D },
      ],
      D,
    )
    expect(fit).toEqual({ inletOffset: 0.5, outletOffset: 0.5 })
    // No gap at all.
    expect(autoFitPlacement([{ z0: 0, z1: D }], D)).toBeNull()
  })

  it('overlap segments ignore touching faces — the predicate epsilon', () => {
    const candidate = { z0: 0.5, z1: 0.9 }
    expect(overlapSegments(candidate, [{ z0: 0, z1: 0.5 }])).toEqual([])
    expect(overlapSegments(candidate, [{ z0: 0.4, z1: 0.6 }])).toEqual([{ z0: 0.5, z1: 0.6 }])
  })
})

describe('the section', () => {
  it('lists the existing servers with their absolute offsets', () => {
    render()
    expect(el('rack-stacked-servers')).toBeTruthy()
    expect(el('rss-row-deep')).toBeTruthy()
    expect(el('rss-depth-deep').textContent).toContain('0.000 / 0.700')
  })

  it('renders nothing for a range no server covers', () => {
    render([30, 31])
    expect(document.getElementById('rack-stacked-servers')).toBeNull()
  })

  it('opens seeded into the largest free gap and confirms a stacked server there', () => {
    render()
    click('rss-toggle')
    // Auto-fit seed: gap 0.5 -> 1.2, box 0.3 deep centred at z0 0.7.
    expect(Number(inputEl('rss-inlet').value)).toBeCloseTo(0.7, 3)
    expect(Number(inputEl('rss-outlet').value)).toBeCloseTo(0.2, 3)
    const confirm = inputEl('rss-confirm') as unknown as HTMLButtonElement
    expect(confirm.disabled).toBe(false)
    const before = store.undoDepth()
    click('rss-confirm')
    // ONE edit creates the record, at the shown absolute depth, sharing the U's.
    expect(store.undoDepth() - before).toBe(1)
    const created = Object.values(servers()).find((s) => s.uid !== 'deep')!
    expect(created).toBeTruthy()
    expect((created.geometry as { slotPosition: number }).slotPosition).toBe(16)
    expect((created.geometry as { slotOccupation: number }).slotOccupation).toBe(5)
    expect(created.inletOffset).toBeCloseTo(0.7, 6)
    expect(created.outletOffset).toBeCloseTo(0.2, 6)
    // The normalizer kept the stack: two records at these U's, no conflicts.
    expect(Object.keys(servers())).toHaveLength(2)
    const cons = model().constructions as unknown as { serverConflicts?: unknown[] }
    expect(cons.serverConflicts).toEqual([])
  })

  it('disables the confirm and names the other server while the candidate intersects', () => {
    render()
    click('rss-toggle')
    // Drive 距 inlet into the existing box: z 0.1 -> 0.5 intersects deep's 0 -> 0.5.
    typeOffset('inlet', '0.1')
    typeOffset('outlet', '0.7')
    const confirm = inputEl('rss-confirm') as unknown as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    expect(el('rss-conflict').textContent).toContain('deep')
    // The preview bar tints the intersecting span.
    expect(el('rss-preview').querySelectorAll('[data-overlap="true"]').length).toBeGreaterThan(0)
    // Nothing was written by typing.
    expect(Object.keys(servers())).toHaveLength(1)
  })

  it('accepts a candidate whose face merely touches the existing box', () => {
    render()
    click('rss-toggle')
    // deep ends at z 0.5; a box starting exactly there is a legal stack.
    typeOffset('inlet', '0.5')
    typeOffset('outlet', '0.4')
    const confirm = inputEl('rss-confirm') as unknown as HTMLButtonElement
    expect(confirm.disabled).toBe(false)
    expect(document.getElementById('rss-conflict')).toBeNull()
  })

  it('auto-fit re-seeds out of a conflicting candidate', () => {
    render()
    click('rss-toggle')
    typeOffset('inlet', '0.1')
    typeOffset('outlet', '0.7')
    expect((inputEl('rss-confirm') as unknown as HTMLButtonElement).disabled).toBe(true)
    click('rss-auto-fit')
    // Asserted through the DIRECTLY controlled readouts and % sliders, not the
    // NumberField text — that re-syncs through a passive effect the harness
    // does not flush.
    expect(el('rss-preview-depth').textContent).toBe('0.700 / 0.200 m')
    expect(Number(inputEl('rss-inlet-pct').value)).toBeCloseTo(58.3, 1)
    expect(Number(inputEl('rss-outlet-pct').value)).toBeCloseTo(16.7, 1)
    expect((inputEl('rss-confirm') as unknown as HTMLButtonElement).disabled).toBe(false)
    expect(document.getElementById('rss-conflict')).toBeNull()
  })

  it('refuses a pair that breaks the 90% budget before any conflict check', () => {
    render()
    click('rss-toggle')
    typeOffset('inlet', '0.9')
    typeOffset('outlet', '0.9')
    const confirm = inputEl('rss-confirm') as unknown as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    expect(el('rss-range-error')).toBeTruthy()
  })

  it('the % sliders write metres of the rack depth', () => {
    render()
    click('rss-toggle')
    const pct = inputEl('rss-inlet-pct')
    setValue.call(pct, '25')
    flushSync(() => pct.dispatchEvent(new Event('input', { bubbles: true })))
    render()
    // 25% of the 1.2 m rack is 0.3 m from the inlet face.
    expect(el('rss-preview-depth').textContent).toBe('0.300 / 0.200 m')
    expect(Number(inputEl('rss-inlet-pct').value)).toBeCloseTo(25, 1)
  })
})
