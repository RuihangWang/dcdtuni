import { useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { FeatureTree } from '../../src/components/FeatureTree'
import { mergeSlotsIntoEquipment } from '../../src/geometry/rackSlots'
import { equipmentList } from '../../src/geometry/rackSelection'
import type { TreeSelection } from '../../src/interaction/treeNodes'

/**
 * The tree lights the sub-row the viewport or the tree narrowed the selection
 * to — a server, a server face, a face, a patch, a U-slot — and moves the light
 * with the next pick on the SAME model.
 *
 * Reported on the HTML editor as "the second face click on an object clears
 * the item and its type"; React had the other half of the bug: every sub-row
 * rendered `className="node-label"` whatever was selected, so the server and
 * its face were never lit at all, not even on the first click.
 */

let host: HTMLDivElement
let root: Root
let rack: string
let box: string
let serverA: string
let serverB: string

function rackModel(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

function render(uid: string | null, node: TreeSelection | null, uids?: string[]) {
  flushSync(() =>
    root.render(
      <FeatureTree
        project={store.getProject()}
        selectedUid={uid}
        selectedUids={uids ?? (uid ? [uid] : [])}
        selectedNode={node}
        onSelect={() => {}}
        onSelectNode={() => {}}
      />,
    ),
  )
}

const row = (key: string) => host.querySelector<HTMLElement>(`[data-node="${key}"]`)
function lit(key: string): boolean {
  const el = row(key)
  expect(el, `row ${key}`).not.toBeNull()
  return el!.classList.contains('selected')
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const createdRack = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(createdRack.bucket, createdRack.model)
  rack = createdRack.model.uid
  mergeSlotsIntoEquipment(rack, 'front', [10, 11], 'fanServer')
  mergeSlotsIntoEquipment(rack, 'front', [20, 21], 'fanServer')
  const ids = equipmentList(rackModel()).map((e) => e.id)
  expect(ids.length).toBe(2)
  ;[serverA, serverB] = ids as [string, string]

  const createdBox = createModel(store.getProject() as CfdProject, 'box')
  store.addModel(createdBox.bucket, createdBox.model)
  box = createdBox.model.uid
  store.edit('addPatch', 'test', (draft) => {
    const m = store.findConstruction(draft, box)!.model
    const faces = (m.geometry as { faces: Record<string, { patches?: unknown[] }> }).faces
    const top = faces.top!
    ;(top.patches ?? (top.patches = [])).push({
      id: 'p_top1',
      name: 'vent',
      type: 'rect',
      cx: 0.5,
      cy: 0.5,
      w: 0.2,
      h: 0.2,
    })
  })

  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('sub-row highlight', () => {
  it('a server face lights its server row and its face row, and the light moves to the next server', () => {
    render(rack, { type: 'serverFace', uid: rack, equipmentId: serverA, faceId: 'outlet' })
    expect(lit(`srv-eq:${rack}:${serverA}`)).toBe(true)
    expect(lit(`srv-face:${rack}:${serverA}:outlet`)).toBe(true)
    expect(lit(`srv-face:${rack}:${serverA}:inlet`)).toBe(false)
    expect(lit(`srv-eq:${rack}:${serverB}`)).toBe(false)
    expect(lit(`srv-face:${rack}:${serverB}:outlet`)).toBe(false)

    // The second pick on the SAME rack — the reported sequence.
    render(rack, { type: 'serverFace', uid: rack, equipmentId: serverB, faceId: 'top' })
    expect(lit(`srv-eq:${rack}:${serverA}`)).toBe(false)
    expect(lit(`srv-face:${rack}:${serverA}:outlet`)).toBe(false)
    expect(lit(`srv-eq:${rack}:${serverB}`)).toBe(true)
    expect(lit(`srv-face:${rack}:${serverB}:top`)).toBe(true)
    // The model row and its category stay lit throughout.
    expect(host.querySelector(`[data-model-id="${rack}"]`)!.classList.contains('selected')).toBe(true)
    expect(host.querySelector('[data-category="racks"]')!.classList.contains('selected')).toBe(true)
  })

  it('follows row clicks through the app’s select-then-narrow flow', () => {
    // App.select() clears the node and onSelectNode sets the new one in the same
    // batch; this harness does the same with its own state.
    function Harness() {
      const [uid, setUid] = useState<string | null>(null)
      const [node, setNode] = useState<TreeSelection | null>(null)
      return (
        <FeatureTree
          project={store.getProject()}
          selectedUid={uid}
          selectedUids={uid ? [uid] : []}
          selectedNode={node}
          onSelect={(u) => {
            setUid(u)
            setNode(null)
          }}
          onSelectNode={setNode}
        />
      )
    }
    flushSync(() => root.render(<Harness />))
    const click = (key: string) =>
      flushSync(() => row(key)!.dispatchEvent(new MouseEvent('click', { bubbles: true })))

    click(`srv-face:${rack}:${serverA}:outlet`)
    expect(lit(`srv-eq:${rack}:${serverA}`)).toBe(true)
    expect(lit(`srv-face:${rack}:${serverA}:outlet`)).toBe(true)

    click(`srv-face:${rack}:${serverB}:inlet`)
    expect(lit(`srv-eq:${rack}:${serverA}`)).toBe(false)
    expect(lit(`srv-face:${rack}:${serverA}:outlet`)).toBe(false)
    expect(lit(`srv-eq:${rack}:${serverB}`)).toBe(true)
    expect(lit(`srv-face:${rack}:${serverB}:inlet`)).toBe(true)

    click(`face:${box}:top`)
    expect(lit(`face:${box}:top`)).toBe(true)
    click(`face:${box}:right`)
    expect(lit(`face:${box}:top`)).toBe(false)
    expect(lit(`face:${box}:right`)).toBe(true)
  })

  it('a box face lights its row; a patch lights its own row and its face row', () => {
    render(box, { type: 'face', uid: box, faceId: 'top' })
    expect(lit(`face:${box}:top`)).toBe(true)
    expect(lit(`face:${box}:right`)).toBe(false)
    expect(lit(`patch:${box}:top:p_top1`)).toBe(false)

    render(box, { type: 'patch', uid: box, faceId: 'top', patchId: 'p_top1' })
    expect(lit(`face:${box}:top`)).toBe(true)
    expect(lit(`patch:${box}:top:p_top1`)).toBe(true)

    render(box, { type: 'face', uid: box, faceId: 'right' })
    expect(lit(`face:${box}:top`)).toBe(false)
    expect(lit(`patch:${box}:top:p_top1`)).toBe(false)
    expect(lit(`face:${box}:right`)).toBe(true)
  })

  it('a U-slot lights its rack face row, and once opened its server group and slot rows', () => {
    const slot: TreeSelection = { type: 'rackSlot', uid: rack, side: 'front', index: 11, indices: [11] }
    render(rack, slot)
    expect(lit(`face:${rack}:front`)).toBe(true)
    expect(lit(`face:${rack}:back`)).toBe(false)
    // The face rows start folded; open the front one by its caret.
    const caret = row(`face:${rack}:front`)!.querySelector<HTMLElement>('.node-expander')!
    flushSync(() => caret.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    render(rack, slot)
    expect(lit(`rack-eqgroup:${rack}:front:${serverA}`)).toBe(true)
    expect(lit(`rack-eqgroup:${rack}:front:${serverB}`)).toBe(false)
    expect(lit(`rack-slot:${rack}:front:11`)).toBe(true)
    expect(lit(`rack-slot:${rack}:front:10`)).toBe(false)
  })

  it('lights nothing under a multi-selection, or for a node of a model that is not the active one', () => {
    const node: TreeSelection = { type: 'serverFace', uid: rack, equipmentId: serverA, faceId: 'outlet' }
    render(rack, node, [rack, box])
    expect(lit(`srv-eq:${rack}:${serverA}`)).toBe(false)
    expect(lit(`srv-face:${rack}:${serverA}:outlet`)).toBe(false)

    render(box, node)
    expect(lit(`srv-eq:${rack}:${serverA}`)).toBe(false)
    expect(host.querySelectorAll('[data-node].selected')).toHaveLength(0)
  })
})
