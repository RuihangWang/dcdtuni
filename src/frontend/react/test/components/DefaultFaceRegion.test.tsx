import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { FaceInspector } from '../../src/components/PatchInspector'
import { wallRegionName, wallRegionSolidName } from '../../src/geometry/wallRegion'

/**
 * A face can be named before anything is cut into it.
 *
 * The part of a face no patch covers is a region of its own, and it is what the
 * exporter writes as an STL solid — so it has a name, and that name is the
 * boundary the runner attaches a condition to. The legacy's face panel ends
 * with a `Default Face Region` group holding that name and the resulting solid
 * name, unconditionally, patches or not (STL_generate.html:10548).
 *
 * React had the whole write path — `wallRegionName`, `setWallRegionName`,
 * `wallRegionSolidName` — reachable only from a wallRegion selection, and BOTH
 * routes to one require patches: the tree gates its region rows on
 * `patches.length > 0` and viewport picking on `facePatchCount(faceId) > 0`. So
 * on a face with nothing cut into it, the region that will be exported was the
 * one thing that could not be named.
 */

let host: HTMLDivElement
let root: Root
let uid: string
const FACE = 'front'

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, uid)!.model
}

function render() {
  flushSync(() => root.render(<FaceInspector model={model()} faceId={FACE} />))
}

const nameBox = () => document.getElementById('face-region-name') as HTMLInputElement | null
const solidBox = () => document.getElementById('face-region-final-name')

function type(value: string) {
  const el = nameBox()!
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
  // React 18 delegates focus at the root, so `blur` alone reaches no handler.
  flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
  render()
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'wall')
  store.addModel(bucket, created)
  uid = created.uid
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('a face with no patches cut into it', () => {
  it('still offers the default region’s name', () => {
    expect(nameBox()).not.toBeNull()
    expect(nameBox()!.value).toBe(wallRegionName(model(), FACE, 0))
  })

  it('writes what is typed into the project', () => {
    type('containment_panel')
    expect(wallRegionName(model(), FACE, 0)).toBe('containment_panel')
  })

  it('shows the solid name the exporter will actually write', () => {
    expect(solidBox()).not.toBeNull()
    expect(solidBox()!.textContent).toBe(wallRegionSolidName(model(), FACE, 0))
  })

  it('is a LIVE readout, not a string frozen at first render', () => {
    // Renaming the model changes what the exporter writes, and the readout has
    // to follow or it is telling the user the name of a solid that no longer
    // exists.
    const before = solidBox()!.textContent
    store.renameModel(uid, 'Containment_Panel')
    render()
    expect(solidBox()!.textContent).not.toBe(before)
    expect(solidBox()!.textContent).toBe(wallRegionSolidName(model(), FACE, 0))
  })

  it('does not pretend the region name reaches the solid name on its own', () => {
    // With ONE region the exporter names the solid after the MODEL — the
    // region name is a label for the panel, and `wallRegionSolidName` reports
    // what is actually written rather than the legacy's composed guess (see its
    // doc comment). Pinned so a future "fix" that concatenates the two has to
    // argue with the exporter first.
    const before = solidBox()!.textContent
    type('containment_panel')
    expect(solidBox()!.textContent).toBe(before)
  })

  it('does not write on a render, only on an edit', () => {
    // `wallRegionName` defaults on READ for exactly this reason: materialising
    // `wall_1` into the file just because a panel was opened is how an
    // untouched project comes back from a save with fields it never had.
    const face = (
      model().geometry as { faces?: Record<string, { wallNames?: string[] }> }
    ).faces?.[FACE]
    // The array itself is the factory's; what must stay empty is the SLOT.
    expect(face?.wallNames?.[0]).toBeUndefined()
  })
})
