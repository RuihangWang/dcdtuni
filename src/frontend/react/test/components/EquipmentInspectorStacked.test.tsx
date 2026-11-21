import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { rackState, stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { EquipmentInspector } from '../../src/components/EquipmentInspector'

/**
 * Reachability of the stacked-servers add path from the equipment panel (WP-R).
 *
 * A viewport click on an occupied slot lands on the SERVER face, so the real
 * user flow opens EquipmentInspector — not the slot inspector. The panel must
 * therefore render `RackStackedServerSection` for the inspected server's own
 * U range, or "add another server at the same U" is unreachable by normal use.
 *
 * Fixture (same shape as RackStackedServerSection.test.tsx): a fresh rack
 * (createModel → 1.2 m deep) holding ONE 5U fan server at U17–U21 occupying
 * depth z 0 → 0.5. The section's indices correctness is asserted indirectly
 * through the auto-fit seed: rows covering z 0..0.5 in a D = 1.2 rack leave
 * the gap 0.5 → 1.2, and the form opens with a 0.3 m box centred at z0 0.7,
 * i.e. `#rss-preview-depth` reads '0.700 / 0.200 m'. A wrong index set (no
 * server found at those U's) would render no section at all.
 */

let host: HTMLDivElement
let root: Root
let rack = ''

type Servers = Record<string, Record<string, unknown>>

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

/** Write one server record straight into the rack, then normalise. */
function seedServer(seed: {
  uid: string
  startUnit: number
  spanUnits?: number
  inletOffset: number
  outletOffset: number
}) {
  store.mutateLive((project) => {
    const m = store.findConstruction(project, rack)!.model
    const cons = m.constructions as unknown as { servers: Servers }
    cons.servers = {
      [seed.uid]: {
        uid: seed.uid,
        type: 'fanServer',
        geometry: { slotPosition: seed.startUnit, slotOccupation: seed.spanUnits ?? 1 },
        cooling: {},
        constructions: { chips: {}, coldPlates: {} },
        meta: { name: seed.uid, enabled: true, display: { opacity: 1 } },
        inletOffset: seed.inletOffset,
        outletOffset: seed.outletOffset,
      },
    }
    rackState().syncRackModelState(m)
  })
}

function render(equipmentId = 'deep') {
  flushSync(() => root.render(<EquipmentInspector model={model()} equipmentId={equipmentId} />))
}

function el(id: string): HTMLElement {
  const found = document.getElementById(id)
  expect(found, id).not.toBeNull()
  return found!
}

function click(id: string) {
  flushSync(() => el(id).dispatchEvent(new MouseEvent('click', { bubbles: true })))
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
  seedServer({ uid: 'deep', startUnit: 16, spanUnits: 5, inletOffset: 0, outletOffset: 0.7 })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('EquipmentInspector hosts the stacked-servers section', () => {
  it('lists the servers at the inspected server\'s own U range', () => {
    render()
    expect(el('rack-stacked-servers')).toBeTruthy()
    expect(el('rss-row-deep')).toBeTruthy()
    expect(el('rss-depth-deep').textContent).toContain('0.000 / 0.700')
  })

  it('opens the add form, seeded by auto-fit into the gap the indices imply', () => {
    render()
    click('rss-toggle')
    expect(el('rss-confirm')).toBeTruthy()
    // Auto-fit seed over rows z 0..0.5 in D = 1.2: gap 0.5 -> 1.2, a 0.3 m
    // box centred at z0 0.7 → inlet 0.700, outlet 0.200. This is the indirect
    // proof that the section received THIS server's [16..20] indices.
    expect(el('rss-preview-depth').textContent).toBe('0.700 / 0.200 m')
  })
})
