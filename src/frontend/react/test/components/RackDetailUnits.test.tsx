import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { RackDetailPanel } from '../../src/components/RackDetailPanel'

/**
 * The rack detail editor is in the project's input unit, like every other
 * geometry box.
 *
 * `meta.inputUnit` is a property of the work — a rack authored from a datasheet
 * in millimetres is typed in millimetres — and the legacy's detail panel labels
 * itself `${t('rack.inner_width_label')}(${getLengthUnitLabel()})`, formats
 * through `formatLength()`, steps by `getLengthStep()` and converts back in the
 * setter (`_rdSetInnerWidth`, STL_generate.html:9128-9160, :11847).
 *
 * React's panel hard-coded `(m)`, stepped by 0.005 whatever the unit and passed
 * what was typed straight to setters whose arguments are METRES. On a mm
 * project, typing a 500 mm mounting width asked for a rack 500 m wide — which
 * the ratio clamp then quietly turned into the maximum.
 */

let host: HTMLDivElement
let root: Root
let rack: string

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

function render() {
  flushSync(() =>
    root.render(<RackDetailPanel project={store.getProject()} rack={model()} />),
  )
}

function field(id: string): HTMLInputElement {
  const el = document.getElementById(id) as HTMLInputElement
  expect(el, id).not.toBeNull()
  return el
}

/** Type into a NumberField and commit it — `focusout`, which React sees. */
function commit(id: string, value: string) {
  const el = field(id)
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
  flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
  render()
}

/** The label text of the row a field sits in. */
function labelFor(id: string): string {
  return field(id).closest('.prop-row')?.querySelector('label')?.textContent ?? ''
}

function loadWithUnit(unit: 'm' | 'mm') {
  const project = stateV4().normalizeStateV4(
    stateV4().getInitialStateV4(),
  ) as unknown as CfdProject
  ;(project.meta as unknown as { inputUnit: string }).inputUnit = unit
  store.loadProject(project, null)
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, created)
  rack = created.uid
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

describe('on a millimetre project', () => {
  beforeEach(() => {
    loadWithUnit('mm')
    render()
  })

  it('says mm on the three length rows', () => {
    expect(labelFor('rd-innerwidth-input')).toContain('mm')
    expect(labelFor('rd-inlet-input')).toContain('mm')
    expect(labelFor('rd-outlet-input')).toContain('mm')
  })

  it('shows the mounting width in millimetres', () => {
    const g = model().geometry as { size?: { x?: number }; slotWidthRatio?: number }
    const metres = Number(g.size?.x ?? 0.6) * Number(g.slotWidthRatio ?? 0.8)
    expect(Number(field('rd-innerwidth-input').value)).toBeCloseTo(metres * 1000, 3)
  })

  it('steps in millimetres, not in metre thousandths', () => {
    expect(Number(field('rd-innerwidth-input').step)).toBe(0.1)
  })

  it('stores what was typed as METRES', () => {
    commit('rd-innerwidth-input', '500')
    const g = model().geometry as { size?: { x?: number }; slotWidthRatio?: number }
    // 500 mm of a 600 mm rack, not 500 m of it.
    expect(Number(g.size?.x ?? 0) * Number(g.slotWidthRatio ?? 0)).toBeCloseTo(0.5, 6)
  })

  it('says the same unit in the summary line above the boxes', () => {
    // A panel reading `W: 0.600 m` two rows above `宽度 (mm) 500` is the same
    // editor telling the user two different things.
    const hint = document.querySelector('#rack-detail-panel .prop-hint')?.textContent ?? ''
    expect(hint).toContain('600')
    expect(hint).not.toContain('0.600')
  })

  it('does the same for the door setbacks', () => {
    commit('rd-inlet-input', '80')
    commit('rd-outlet-input', '45')
    const r = model() as Construction & { inletOffset?: number; outletOffset?: number }
    expect(r.inletOffset).toBeCloseTo(0.08, 6)
    expect(r.outletOffset).toBeCloseTo(0.045, 6)
  })
})

describe('on a metre project', () => {
  beforeEach(() => {
    loadWithUnit('m')
    render()
  })

  it('is unchanged: what is typed is what is stored', () => {
    expect(labelFor('rd-inlet-input')).toContain('m')
    commit('rd-inlet-input', '0.08')
    const r = model() as Construction & { inletOffset?: number }
    expect(r.inletOffset).toBeCloseTo(0.08, 6)
  })
})
