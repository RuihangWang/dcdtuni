import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { Inspector } from '../../src/components/Inspector'

/**
 * A FRESH floor or baffle — no patches cut into it — can have the boundary
 * condition of its remaining area set.
 *
 * `test/floorDefaultBc.test.ts` already covers the write and its coupling.
 * What it cannot show is whether anything on screen reaches that write, and for
 * a patch-less sheet nothing did: `RemainingAreaBc` was mounted only in
 * `FaceInspector` and `WallRegionInspector`, and a floor or baffle gets no face
 * row (`sheetChildren`), is not selectable as a face in the viewport
 * (`resolveViewportSelection` returns null for a planar body), and grows
 * wall-region rows only once patches have split it. So the whole-plate case —
 * a perforated raised floor, a perforated containment panel, which is the
 * COMMON case — had no control at all.
 *
 * This test therefore starts at the MODEL selection, the way a user does, and
 * renders the real `Inspector` rather than the section directly.
 */

let host: HTMLDivElement
let root: Root

function addSheet(kind: 'floor' | 'baffle'): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  store.loadProject(project, null)
  const { bucket, model } = createModel(store.getProject() as CfdProject, kind)
  return store.addModel(bucket, model)
}

function render(uid: string) {
  flushSync(() =>
    root.render(<Inspector project={store.getProject()} selectedUid={uid} node={null} />),
  )
}

function sheet(uid: string): Construction & { cooling?: Record<string, unknown> } {
  return store.findConstruction(store.getProject() as CfdProject, uid)!.model
}

function select(id: string): HTMLSelectElement {
  return host.querySelector(`#${id}`) as HTMLSelectElement
}

/** Pick an option the way React's onChange sees it. */
function choose(el: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
  setter.call(el, value)
  flushSync(() => el.dispatchEvent(new Event('change', { bubbles: true })))
}

function patchCount(uid: string): number {
  const faces = (sheet(uid).geometry as Record<string, unknown>).faces as Record<
    string,
    { patches?: unknown[] }
  >
  return Object.values(faces).reduce((n, f) => n + (f.patches?.length ?? 0), 0)
}

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the remaining-area boundary condition of a patch-less sheet', () => {
  it('is on a fresh floor’s model inspector', () => {
    const uid = addSheet('floor')
    render(uid)
    expect(patchCount(uid)).toBe(0)
    expect(host.querySelector('#remaining-area-block')).not.toBeNull()
    expect(select('region-bc-type')).not.toBeNull()
  })

  it('is on a fresh baffle’s model inspector', () => {
    const uid = addSheet('baffle')
    render(uid)
    expect(patchCount(uid)).toBe(0)
    expect(host.querySelector('#remaining-area-block')).not.toBeNull()
    expect(select('region-bc-type')).not.toBeNull()
  })

  it('makes a whole floor perforated, and reveals the hole fields', () => {
    const uid = addSheet('floor')
    render(uid)
    choose(select('region-bc-type'), 'perforated')
    render(uid)

    expect(sheet(uid).cooling?.defaultBoundaryType).toBe('perforated')
    // Seeded, not left undefined: three empty boxes are not a description of a
    // perforated plate, and the derived K would have nothing to compute from.
    expect(sheet(uid).cooling?.defaultPerforation).toBeTruthy()
    expect(host.querySelector('#perf-open-pct')).not.toBeNull()
  })

  it('makes a whole baffle perforated', () => {
    const uid = addSheet('baffle')
    render(uid)
    choose(select('region-bc-type'), 'perforated')
    render(uid)

    expect(sheet(uid).cooling?.type).toBe('perforated')
    expect(host.querySelector('#perf-open-pct')).not.toBeNull()
  })

  it('offers the floor’s three types and the baffle’s two', () => {
    const floorUid = addSheet('floor')
    render(floorUid)
    expect([...select('region-bc-type').options].map((o) => o.value)).toEqual([
      'wall',
      'wallBaffle',
      'perforated',
    ])

    const baffleUid = addSheet('baffle')
    render(baffleUid)
    // A baffle has no `wallBaffle`: it IS one. Offering it would be a
    // no-op the user could not tell apart from the real choice.
    expect([...select('region-bc-type').options].map((o) => o.value)).toEqual([
      'wall',
      'perforated',
    ])
  })

  it('keeps the floor’s room-boundary control, and hides it on a baffle', () => {
    const floorUid = addSheet('floor')
    render(floorUid)
    expect(host.querySelector('#floor-room-boundary')).not.toBeNull()

    const baffleUid = addSheet('baffle')
    render(baffleUid)
    expect(host.querySelector('#floor-room-boundary')).toBeNull()
  })
})
