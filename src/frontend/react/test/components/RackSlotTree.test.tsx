import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { FeatureTree } from '../../src/components/FeatureTree'
import { mergeSlotsIntoEquipment, setSlotDeleted } from '../../src/geometry/rackSlots'
import { findDoor, setDoorEnabled } from '../../src/geometry/rackDetail'
import type { TreeSelection } from '../../src/interaction/treeNodes'

/**
 * A rack's U-slots are browsable from the feature tree.
 *
 * This is the legacy's own route into a slot: `selectRackSlot(event, uid, face,
 * index)` from a per-U row (STL_generate.html:7984), under a face row carrying
 * the slot count (:7944), with multi-U servers collapsed into one group row
 * whose children are their own U (:7966), and a door row with a visibility
 * checkbox and a remove button (:7995).
 *
 * React's rack branch had `🧰 Equipments` and `🪟 Partitions` and stopped there.
 * The consequence is not only that the rack cannot be read U by U: a click on
 * an occupied slot in the VIEWPORT resolves to the server's face, never to a
 * slot (`rackFaceGesture`, the legacy's table at :17866), so with no tree rows
 * there is no gesture anywhere that selects ONE U of a server — and the
 * single-slot inspector, along with its 移除 button, was unreachable.
 */

let host: HTMLDivElement
let root: Root
let rack: string
let picked: TreeSelection | null

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

function render() {
  flushSync(() =>
    root.render(
      <FeatureTree
        project={store.getProject()}
        selectedUid={rack}
        selectedUids={[rack]}
        onSelect={() => {}}
        onSelectNode={(sel) => {
          picked = sel
        }}
      />,
    ),
  )
}

const row = (key: string) => host.querySelector<HTMLElement>(`[data-node="${key}"]`)

/** The MODEL row is addressed by uid, sub-rows by their node key. */
const modelRow = () => host.querySelector<HTMLElement>(`[data-model-id="${rack}"]`)

/**
 * Fold a branch open or shut by its caret.
 *
 * The FACE rows start folded — 42 rows each, and a 142-rack project would
 * otherwise render 28,682 tree rows — so every case below opens the face first.
 * Everything else starts open.
 */
function toggleBranch(key: string) {
  const el = key === 'MODEL' ? modelRow() : row(key)
  expect(el, `row ${key}`).not.toBeNull()
  const caret = el!.querySelector<HTMLElement>('.node-expander')
  expect(caret, `caret on ${key}`).not.toBeNull()
  flushSync(() => caret!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  render()
}

function click(key: string) {
  const el = row(key)
  expect(el, `row ${key}`).not.toBeNull()
  flushSync(() => el!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

/** Every slot/equipment row under the front face, in DOM order. */
function frontRows(): string[] {
  return [...host.querySelectorAll<HTMLElement>('[data-node]')]
    .map((el) => el.dataset.node ?? '')
    .filter((k) => k.includes(`:${rack}:front`) || k.includes(`${rack}:front:`))
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, created)
  rack = created.uid
  mergeSlotsIntoEquipment(rack, 'front', [10, 11, 12, 13], 'fanServer')
  // One open slot, so the `void` status has something to report.
  setSlotDeleted(rack, 'front', 5, true)
  // A rack starts with NO door — the face entry's absence is what "no door"
  // means in V4 — so one is installed for the door-row cases, exactly as the
  // rack detail panel installs it.
  setDoorEnabled(rack, 'inlet', true)
  picked = null
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the rack branch', () => {
  it('keeps the U rows folded until the face is opened', () => {
    // 42 rows per face, two faces per rack: on case/Base604 (142 racks) leaving
    // them open put 28,682 rows in the tree and 1.58 s between a click and its
    // paint. Measured, not assumed — see the commit.
    expect(host.querySelectorAll(`[data-node^="rack-slot:${rack}:front:"]`)).toHaveLength(0)
    toggleBranch(`face:${rack}:front`)
    expect(
      host.querySelectorAll(`[data-node^="rack-slot:${rack}:front:"]`).length,
    ).toBeGreaterThan(0)
  })

  it('lists the two faces that have slots, with the slot count on them', () => {
    const front = row(`face:${rack}:front`)
    expect(front).not.toBeNull()
    expect(row(`face:${rack}:back`)).not.toBeNull()
    expect(front!.textContent).toContain('42')
  })

  it('opens into one row per U, top of the rack first', () => {
    toggleBranch(`face:${rack}:front`)
    const keys = frontRows().filter((k) => k.startsWith('rack-slot:'))
    expect(keys.length).toBeGreaterThan(0)
    const indices = keys.map((k) => Number(k.split(':').pop()))
    // Descending, as the legacy walks `slot - 1` down to 0: U42 is the top of
    // the rack and belongs at the top of the list.
    expect(indices).toEqual([...indices].sort((a, b) => b - a))
  })

  it('says what each U is', () => {
    toggleBranch(`face:${rack}:front`)
    // An open slot reads `void`, a closed one `wall` — the two statuses the
    // legacy prints beside the U number.
    expect(row(`rack-slot:${rack}:front:5`)!.textContent).toContain('void')
    expect(row(`rack-slot:${rack}:front:6`)!.textContent).toContain('wall')
  })

  it('collapses a multi-U server into ONE row, badged with its span', () => {
    toggleBranch(`face:${rack}:front`)
    const eq = [...host.querySelectorAll<HTMLElement>('[data-node]')].filter((el) =>
      (el.dataset.node ?? '').startsWith(`rack-eqgroup:${rack}:front:`),
    )
    expect(eq).toHaveLength(1)
    expect(eq[0]!.textContent).toContain('4U')
    // Its four U appear ONCE each, inside that row — not a second time as loose
    // rows beside it, which is what a loop that forgot to skip the span emits.
    for (const i of [10, 11, 12, 13]) {
      const found = host.querySelectorAll(`[data-node="rack-slot:${rack}:front:${i}"]`)
      expect(found, `U${i} rows`).toHaveLength(1)
      expect(found[0]!.closest('li')?.parentElement?.closest('li')).toBe(eq[0]!.closest('li'))
    }
  })

  it('holds its own four U, which fold away with it', () => {
    toggleBranch(`face:${rack}:front`)
    const key = [...host.querySelectorAll<HTMLElement>('[data-node]')]
      .map((el) => el.dataset.node ?? '')
      .find((k) => k.startsWith(`rack-eqgroup:${rack}:front:`))!
    for (const i of [10, 11, 12, 13]) {
      expect(row(`rack-slot:${rack}:front:${i}`), `U${i}`).not.toBeNull()
    }
    // Folded away with the server, not with the face: they are ITS children.
    toggleBranch(key)
    for (const i of [10, 11, 12, 13]) {
      expect(row(`rack-slot:${rack}:front:${i}`), `U${i} folded`).toBeNull()
    }
    expect(row(`rack-slot:${rack}:front:20`)).not.toBeNull()
  })

  it('selects ONE slot when a U row is clicked', () => {
    toggleBranch(`face:${rack}:front`)
    // The gesture the single-slot inspector — and its 移除 button — depends on.
    click(`rack-slot:${rack}:front:20`)
    expect(picked).toMatchObject({ type: 'rackSlot', uid: rack, side: 'front', index: 20 })
  })
})

describe('the door row', () => {
  it('is there, checked, and toggles the door’s visibility', () => {
    toggleBranch(`face:${rack}:front`)
    const door = row(`rack-door:${rack}:front`)
    expect(door).not.toBeNull()
    const box = door!.querySelector<HTMLInputElement>('input[type=checkbox]')
    expect(box).not.toBeNull()
    expect(box!.checked).toBe(true)
    flushSync(() => box!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(findDoor(model(), 'inlet')?.renderDoor).toBe(false)
  })

  it('removes the door from its ✖', () => {
    toggleBranch(`face:${rack}:front`)
    const btn = row(`rack-door:${rack}:front`)!.querySelector<HTMLElement>('.node-action-btn')
    expect(btn).not.toBeNull()
    flushSync(() => btn!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(findDoor(model(), 'inlet')).toBeNull()
  })
})
