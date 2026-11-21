import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { reviveKinds, stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { getLocale, setLocale } from '../../src/i18n'
import { RackSlotInspector } from '../../src/components/RackSlotInspector'
import { SelectedFaceBanner } from '../../src/components/SelectedFaceBanner'
import { ServerFaceInspector } from '../../src/components/ServerFaceInspector'
import { EquipmentInspector } from '../../src/components/EquipmentInspector'
import { modelChildren } from '../../src/interaction/treeNodes'
import { equipmentAtSlot, mergeSlotsIntoEquipment, setSlotDeleted } from '../../src/geometry/rackSlots'

/**
 * The user's case, on a copy of the user's file: Tri_angle, rack Default_Rack_1 (m_12,
 * yawed 135°), U18. Its U18..U23 hold no server — a perforated blanking panel
 * on each side, exported as `Default_Rack_1_inlet_u18_u23` and
 * `Default_Rack_1_outlet_u18_u23`. Every panel that names the side of that
 * slot said "front"/"back" (measured in the browser: 所在面 back, checkboxes
 * back / front, banner "Default_Rack_1 · Back"); they must say inlet/outlet,
 * the words the exported patches use. The DATA keys must not move.
 */

// A frozen copy of case/Tri_angle/Tri_angle.cfdproj (2026-09-23, `meta.casePath`
// dropped), resolved from this file rather than the working directory: the
// live case is the user's to edit, and a change to m_12 there must not break
// these tests.
const PROJECT = resolve(__dirname, '..', '__fixtures__', 'tri_angle_rack_faces.cfdproj')
const RACK = 'm_12'
const U18 = 17

let host: HTMLDivElement
let root: Root
const locale = getLocale()

function rack(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, RACK)!.model
}

function render(el: ReactElement) {
  flushSync(() => root.render(el))
}

const text = (sel: string) => (document.querySelector(sel)?.textContent ?? '').trim()

beforeEach(() => {
  setLocale('zh-CN')
  store.loadProject(
    reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject),
    '/cases/Tri_angle.cfdproj',
  )
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  setLocale(locale)
})

describe('the U-slot panel', () => {
  it('fixture: U18 of Default_Rack_1 is an empty slot', () => {
    expect(rack().meta?.name).toBe('Default_Rack_1')
    expect(equipmentAtSlot(rack(), U18)).toBeNull()
  })

  it('names the back side 出风面 and its opposite 进风面', () => {
    render(<RackSlotInspector model={rack()} index={U18} side="back" />)
    const panel = document.getElementById('rack-slot-inspector')!
    const side = panel.querySelector('.prop-row .value')!.textContent
    expect(side).toBe('出风面 (Outlet)')
    expect(text('label[for="slot-open-this"]')).toBe('出风面 (Outlet)')
    expect(text('label[for="slot-open-opposite"]')).toBe('进风面 (Inlet)')
  })

  it('says Outlet / Inlet in English', () => {
    setLocale('en-US')
    render(<RackSlotInspector model={rack()} index={U18} side="back" />)
    expect(text('label[for="slot-open-this"]')).toBe('Outlet')
    expect(text('label[for="slot-open-opposite"]')).toBe('Inlet')
  })

  it('shows no bare side key anywhere a side is named', () => {
    for (const side of ['front', 'back'] as const) {
      render(<RackSlotInspector model={rack()} index={U18} side={side} />)
      const named = [
        ...document.querySelectorAll('#rack-slot-inspector label, #rack-slot-inspector .value'),
      ].map((n) => (n.textContent ?? '').trim())
      expect(named).not.toContain('front')
      expect(named).not.toContain('back')
    }
  })

  it('names both faces in the half-open hint and its button', () => {
    // The inlet opened, the outlet still closed: the outlet's panel offers to
    // put the inlet back.
    setSlotDeleted(RACK, 'front', U18, true)
    render(<RackSlotInspector model={rack()} index={U18} side="back" />)
    const hint = [...document.querySelectorAll('#rack-slot-inspector .prop-hint')]
      .map((n) => n.textContent ?? '')
      .find((s) => s.includes('出风面'))
    expect(hint).toContain('出风面 (Outlet)')
    expect(hint).toContain('进风面 (Inlet)')
    expect(text('#slot-restore-opposite')).toContain('进风面 (Inlet)')
  })
})

describe('the selected-face banner', () => {
  it('names a slot on the back by its role, and keeps the data key', () => {
    render(
      <SelectedFaceBanner model={rack()} node={{ type: 'rackSlot', uid: RACK, side: 'back', index: U18 }} />,
    )
    expect(text('#selected-face-name')).toBe('Default_Rack_1 · 出风面 (Outlet)')
    expect(text('#selected-face-id')).toBe('outlet')
    expect(text('#selected-face-side')).toBe('出风面 (Outlet)')
    expect(document.getElementById('selected-face-banner')!.dataset.faceId).toBe('back')
    expect(document.getElementById('selected-face-banner')!.dataset.faceSide).toBe('back')
  })

  it('names the front face 进风面', () => {
    render(<SelectedFaceBanner model={rack()} node={{ type: 'face', uid: RACK, faceId: 'front' }} />)
    expect(text('#selected-face-name')).toBe('Default_Rack_1 · 进风面 (Inlet)')
    expect(text('#selected-face-id')).toBe('inlet')
  })

  it('names a server face the same way', () => {
    render(
      <SelectedFaceBanner
        model={rack()}
        node={{ type: 'serverFace', uid: RACK, equipmentId: 'e_1', faceId: 'outlet' }}
      />,
    )
    expect(text('#selected-face-name')).toBe('Default_Rack_1 · 出风面 (Outlet)')
  })

  it('leaves the faces of a model that is not a rack alone', () => {
    const acu = Object.values(
      (store.getProject() as unknown as { constructions: Record<string, Record<string, Construction>> })
        .constructions.acus ?? {},
    )[0]!
    render(<SelectedFaceBanner model={acu} node={{ type: 'face', uid: acu.uid, faceId: 'front' }} />)
    expect(text('#selected-face-name')).toMatch(/· Front$/)
    expect(text('#selected-face-id')).toBe('front')
  })
})

describe('the server panels', () => {
  it('labels the outlet face colour as the outlet, not "Front"', () => {
    render(<ServerFaceInspector model={rack()} equipmentId="e_1" faceId="outlet" />)
    expect(text('label[for="srv-face-color"]')).toBe('出风面颜色')
    expect(document.getElementById('server-face-inspector')!.textContent).toContain('出风面 (Outlet)')
  })

  it('labels the two colours of a whole server inlet / outlet', () => {
    render(<EquipmentInspector model={rack()} equipmentId="e_1" />)
    expect(text('label[for="eq-front-color"]')).toBe('进风面颜色')
    expect(text('label[for="eq-back-color"]')).toBe('出风面颜色')
    const hints = [...document.querySelectorAll('.prop-hint')].map((n) => n.textContent ?? '')
    expect(hints.some((h) => h.includes('front/back'))).toBe(false)
  })
})

describe('the feature tree', () => {
  it('lists the two rack sides as 进风面 / 出风面', () => {
    const labels = modelChildren(rack()).map((n) => n.label)
    expect(labels).toContain('进风面 (Inlet)')
    expect(labels).toContain('出风面 (Outlet)')
    expect(labels).not.toContain('front')
    expect(labels).not.toContain('back')
  })
})

describe('only the words changed', () => {
  it('rendering every relabelled panel leaves the project byte-identical', () => {
    const before = JSON.stringify(store.getProject())
    const node = { type: 'rackSlot', uid: RACK, side: 'back', index: U18 } as const
    render(<RackSlotInspector model={rack()} index={U18} side="back" />)
    render(<SelectedFaceBanner model={rack()} node={node} />)
    render(<ServerFaceInspector model={rack()} equipmentId="e_1" faceId="inlet" />)
    render(<EquipmentInspector model={rack()} equipmentId="e_1" />)
    modelChildren(rack())
    expect(JSON.stringify(store.getProject())).toBe(before)
    // The side keys the file carries are still the geometric ones.
    const geometry = rack().geometry as unknown as { faces: { uid?: string }[] }
    expect(geometry.faces.map((f) => f.uid).filter((u) => u === 'inlet' || u === 'outlet')).toEqual([])
  })
})

describe('a non-fan server’s face headings', () => {
  it('head its two faces 进风面 / 出风面', () => {
    store.loadProject(
      stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
      null,
    )
    const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
    store.addModel(bucket, created)
    const made = mergeSlotsIntoEquipment(created.uid, 'front', [5], 'nonFan')
    expect(made).toBe('ok')
    const m = store.findConstruction(store.getProject() as CfdProject, created.uid)!.model
    const eq = equipmentAtSlot(m, 5)!
    render(<EquipmentInspector model={m} equipmentId={String(eq.id)} />)
    const body = document.body.textContent ?? ''
    expect(body).toContain('◆ 进风面 (Inlet)')
    expect(body).toContain('◆ 出风面 (Outlet)')
  })
})
