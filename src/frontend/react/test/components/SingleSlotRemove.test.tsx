import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { RackSlotInspector } from '../../src/components/RackSlotInspector'
import { mergeSlotsIntoEquipment } from '../../src/geometry/rackSlots'
import { equipmentEndUnit, equipmentList } from '../../src/geometry/rackSelection'

/**
 * One selected U can be taken out of the server it belongs to.
 *
 * A 4U server whose bottom U is needed for something else is trimmed by
 * selecting that one slot and pressing 移除. The legacy renders
 * `btn.remove_slot_from_equipment` on the SINGLE-slot inspector whenever
 * `getRackEquipmentSelectionContext` reports `canRemoveFromEquipment`
 * (STL_generate.html:9602), and that context computes full / trimStart /
 * trimEnd / split for a selection of ANY length, one included.
 *
 * React had every piece but the route: `selectionContext` is length-agnostic,
 * `removeSlotsFromEquipment` implements all four modes, both are tested — and
 * `RackSlotInspector` gated the panel that offers the button on
 * `indices.length > 1`. So a one-U trim could only be done by selecting two
 * slots and putting one back.
 */

let host: HTMLDivElement
let root: Root
let rack: string

/** The rack, freshly read — every mutation swaps the project object. */
function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

function servers() {
  return equipmentList(model()).map((e) => ({
    start: e.startUnit,
    end: equipmentEndUnit(e),
    span: e.spanUnits,
  }))
}

function render(index: number) {
  flushSync(() =>
    root.render(
      <RackSlotInspector model={model()} index={index} side="front" indices={[index]} />,
    ),
  )
}

const button = () =>
  document.getElementById('slot-remove-from-equipment') as HTMLButtonElement | null

function press() {
  flushSync(() => button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, created)
  rack = created.uid
  // A 4U server across units 10..13, which is what a real one looks like.
  mergeSlotsIntoEquipment(rack, 'front', [10, 11, 12, 13], 'fanServer')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
})

describe('a single occupied slot', () => {
  it('starts as one 4U server', () => {
    expect(servers()).toEqual([{ start: 10, end: 13, span: 4 }])
  })

  it('offers the remove button', () => {
    render(13)
    expect(button()).not.toBeNull()
  })

  it('trims the END without asking', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(13)
    press()
    expect(servers()).toEqual([{ start: 10, end: 12, span: 3 }])
    // Only a split is destructive enough to ask about.
    expect(confirm).not.toHaveBeenCalled()
  })

  it('trims the START', () => {
    render(10)
    press()
    expect(servers()).toEqual([{ start: 11, end: 13, span: 3 }])
  })

  it('asks before splitting, and splits into two', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(11)
    press()
    expect(confirm).toHaveBeenCalled()
    expect(servers()).toEqual([
      { start: 10, end: 10, span: 1 },
      { start: 12, end: 13, span: 2 },
    ])
  })

  it('leaves the server alone when the split is declined', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(11)
    press()
    expect(servers()).toEqual([{ start: 10, end: 13, span: 4 }])
  })

  it('removes a 1U server outright', () => {
    mergeSlotsIntoEquipment(rack, 'front', [20], 'nonFan')
    expect(servers()).toHaveLength(2)
    render(20)
    press()
    expect(servers()).toEqual([{ start: 10, end: 13, span: 4 }])
  })
})

describe('a slot with nothing in it', () => {
  it('has no remove button — there is nothing to remove it from', () => {
    render(30)
    expect(button()).toBeNull()
  })
})
