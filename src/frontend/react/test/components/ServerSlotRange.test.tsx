import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { mergeSlotsIntoEquipment } from '../../src/geometry/rackSlots'
import { addServerPatch } from '../../src/geometry/serverPatches'
import { ServerFaceInspector } from '../../src/components/ServerFaceInspector'
import { PatchInspector } from '../../src/components/PatchInspector'

/**
 * Which U-slots a server covers is stated wherever the server is reached from.
 *
 * The range was on the equipment panel and the server-FACE panel, but a patch
 * on a server's inlet or outlet opened a panel with only the patch editor, and
 * nothing on screen said which server, or where in the rack, the patch was on.
 * Both panels now carry the same identity rows, and the same `#sf-range` id the
 * legacy face panel uses, so one selector reads the range in both editors.
 */

let host: HTMLDivElement
let root: Root
let rack: string
let server: string

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, created)
  rack = created.uid
  // A 2U server in units 32..33: the legacy labels those U33 ~ U34.
  expect(mergeSlotsIntoEquipment(rack, 'front', [32, 33], 'fanServer')).toBe('ok')
  server = Object.keys(
    (model().constructions as { servers?: Record<string, unknown> }).servers ?? {},
  )[0]!
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

const range = () => document.getElementById('sf-range')?.textContent ?? null

describe('the server slot range', () => {
  it('is on the server FACE panel', () => {
    flushSync(() =>
      root.render(<ServerFaceInspector model={model()} equipmentId={server} faceId="inlet" />),
    )
    expect(range()).toBe('U33 ~ U34')
  })

  it('is on the server PATCH panel, above the patch editor', () => {
    const patchId = addServerPatch(rack, server, 'outlet')!
    expect(patchId).toBeTruthy()
    flushSync(() =>
      root.render(
        <PatchInspector
          project={store.getProject() as CfdProject}
          model={model()}
          faceId="outlet"
          patchId={patchId}
        />,
      ),
    )
    expect(document.getElementById('server-patch-inspector')).not.toBeNull()
    expect(range()).toBe('U33 ~ U34')
    const rangeEl = document.getElementById('sf-range')!
    const editor = document.getElementById('server-patch-inspector')!
    // Identity first, then the editor: DOCUMENT_POSITION_FOLLOWING.
    expect(rangeEl.compareDocumentPosition(editor) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // And the server's name, as the face panel shows it.
    expect(host.textContent).toContain(
      String(
        ((model().constructions as { servers: Record<string, { meta?: { name?: string } }> }).servers[server]!
          .meta?.name) ?? server,
      ),
    )
  })
})
